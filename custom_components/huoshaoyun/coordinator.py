"""数据协调器 —— 性能/请求/可读性/自动化友好 四项优化集中在这里。

优化要点:
1) 请求优化: 按"距下一次霞光多远"分 4 档节拍(1h/3h/12h/更远), 而不是固定间隔;
   模型数据只在起报更新时变化, 远离事件时无需密拉。
2) 请求优化: 数据指纹(模型输出哈希)连续未变时自动退避, 最多拉到 3 档(≤6h),
   但绝不作用在"距事件 6h 以内"的窗口 —— 保证事件前一定用最新起报。
3) 性能优化: 指纹未变则直接复用上轮计算结果, 跳过 ~0.3s 的评分计算。
4) 自动化友好: 每个事件都带 `距今小时`/`已过`; 数据层暴露 `数据指纹`/`数据变更`。

输出只有纯数值, 不做评级、不做"值得拍"判断 —— 交给 HA 自动化。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import fetcher
from . import hsy_core as H
from .const import (CONF_API_KEY, DEFAULT_INTERVAL, DEFAULT_NEAR_INTERVAL, DOMAIN,
                    FAILURE_RETRY_MIN, MODELS_MAP, NEAR_WINDOW_HOURS, QUOTA_BACKOFF_CAP_MIN,
                    QUOTA_BACKOFF_MIN)

_LOGGER = logging.getLogger(__name__)


TIER_NEAR_H = 1.0      # <=1h  : 用"临近间隔"(默认15min)
TIER_MID_H = 3.0       # <=3h  : 用常规间隔的一半(>=15min)
TIER_FAR_H = 12.0      # <=12h : 用常规间隔
FAR_INTERVAL_H = 6.0   # >12h  : 至少 6h 一拉
BACKOFF_MAX = 3        # 数据连续未变时最多放大到 3 档
BACKOFF_CAP_H = 8.0    # 未变退避上限(h)
NO_BACKOFF_WITHIN_H = 6.0   # 距事件 6h 以内禁止退避


def _merge_cloud(per_model: dict) -> dict | None:
    """把各模式的云况诊断按均值合并(与主值=各模式均值同口径)"""
    clouds = [v["cloud"] for v in per_model.values() if v.get("cloud")]
    if not clouds:
        return None
    if len(clouds) == 1:
        return clouds[0]
    classes = {}
    for cls in ("low", "mid", "high"):
        items = [c["classes"][cls] for c in clouds]

        def avg(key):
            vals = [i[key] for i in items if i.get(key) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        cover = round(sum(i["云量"] for i in items) / len(items), 3)
        temp = avg("温度_C")
        base_m = avg("云底_m")
        top_m = avg("云顶_m")
        classes[cls] = {
            "云量": cover,
            "云底_m": int(base_m) if base_m is not None else None,
            "云顶_m": int(top_m) if top_m is not None else None,
            "温度_C": temp,
            "相态": (("ice" if temp < 0 else "water") if temp is not None else None),
        }
    present = [c for c in ("low", "mid", "high") if classes[c]["云量"] >= 0.10]
    dom = max(present, key=lambda c: classes[c]["云量"]) if present else None
    text = " | ".join(f"{m}: {v['cloud']['text']}" for m, v in per_model.items()
                      if v.get("cloud"))
    return {"classes": classes, "present": present, "dominant": dom,
            "dominant_base_m": classes[dom]["云底_m"] if dom else None, "text": text}


class HuoshaoyunCoordinator(DataUpdateCoordinator):
    """按固定地点定期计算 4 个事件(今明 × 朝霞/晚霞)的鲜艳度"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.lat = float(entry.data["latitude"])
        self.lon = float(entry.data["longitude"])
        self.site_name = str(entry.data["name"])
        opt = {**entry.data, **entry.options}
        self.models_key = opt.get("models", "both")
        self.models = MODELS_MAP.get(self.models_key, MODELS_MAP["both"])
        self.amount_src = opt.get("amount_src", "mean")
        self.api_key = str(opt.get(CONF_API_KEY) or "").strip() or None
        self.tz = ZoneInfo(opt.get("timezone") or hass.config.time_zone)
        self._base_interval = int(opt.get("update_interval", DEFAULT_INTERVAL))
        self._near_interval = int(opt.get("near_interval", DEFAULT_NEAR_INTERVAL))
        self._last_fp: str | None = None
        self._unchanged = 0
        self._quota_strikes = 0      # 连续配额用尽次数, 用于逐次翻倍退避
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {self.site_name}",
                         update_interval=timedelta(minutes=self._base_interval))

    # ------------------------------------------------------------ 工具
    def _now(self) -> datetime:
        return datetime.now(self.tz).replace(tzinfo=None)

    @property
    def tz_hours(self) -> float:
        off = self._now().replace(tzinfo=self.tz).utcoffset() or timedelta(0)
        return off.total_seconds() / 3600.0

    # ------------------------------------------------------------ 主循环
    async def _async_update_data(self) -> dict:
        now = self._now()
        base_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        session = async_get_clientsession(self.hass)
        try:
            transects, meta = await fetcher.async_fetch_transects(
                session, self.lat, self.lon, str(self.tz), self.tz_hours,
                base_date, self.models, self.api_key)
            fingerprint = meta["fingerprint"]
        except fetcher.QuotaExhausted as err:
            # 配额用尽: 必须长退避。绝不能"失败就立刻重试"—— 每个请求都照样计入配额,
            # 而重试间隔远小于配额重置窗口时会形成自激循环, 让配额永远无法恢复。
            self._quota_strikes += 1
            mins = min(QUOTA_BACKOFF_MIN * (2 ** (self._quota_strikes - 1)),
                       QUOTA_BACKOFF_CAP_MIN)
            self.update_interval = timedelta(minutes=mins)
            scope_cn = {"day": "每日", "hour": "每小时", "other": ""}.get(err.scope, "")
            _LOGGER.warning(
                "[%s] Open-Meteo %s配额已用尽(%s) -> 退避 %d 分钟(第%d次连续), 期间实体不可用",
                self.site_name, scope_cn, err.reason or "429", mins, self._quota_strikes)
            raise UpdateFailed(f"Open-Meteo {scope_cn}配额用尽: {err.reason}") from err
        except Exception as err:                       # noqa: BLE001
            # 普通失败: 走 HA 标准语义 —— 实体置为"不可用"(UI/自动化/日志都看得见),
            # 而不是拿旧数值冒充新鲜数据。仅本地点受影响, 其它条目各自独立。
            self.update_interval = timedelta(minutes=FAILURE_RETRY_MIN)
            _LOGGER.warning("[%s] 取数失败, 实体置为不可用, %d 分钟后重试: %s",
                            self.site_name, FAILURE_RETRY_MIN, err)
            raise UpdateFailed(f"取数失败: {err}") from err

        if fingerprint == self._last_fp and self.data:
            # 起报未变: 复用上轮结果, 省掉 ~0.3s 评分计算
            self._unchanged += 1
            data = dict(self.data)
            data["updated"] = now
            data["data_changed"] = False
            data["partial"] = meta["partial"]
            data["missing"] = meta["missing"]
            self._stamp_events(data)
            self._adapt_interval(data["next"])
            _LOGGER.debug("[%s] 起报未变(第%d次), 复用上轮结果", self.site_name,
                          self._unchanged)
            return data

        self._last_fp = fingerprint
        self._unchanged = 0
        if self._quota_strikes:
            _LOGGER.info("[%s] 取数恢复, 重置配额退避", self.site_name)
            self._quota_strikes = 0
        raw = {}
        for model in self.models:
            try:
                # 纯 CPU 计算放线程池, 不阻塞 HA 事件循环
                raw[model] = await self.hass.async_add_executor_job(
                    H.analyse_all, self.lat, self.lon, base_date,
                    self.tz_hours, transects[model])
            except Exception as err:                   # noqa: BLE001
                _LOGGER.warning("[%s] %s 计算失败: %s", self.site_name, model, err)
        if not raw:
            raise UpdateFailed("所有模式均计算失败")

        data = {"events": {}, "sun": {}, "updated": now,
                "fingerprint": fingerprint, "data_changed": True,
                "partial": meta["partial"], "missing": meta["missing"]}
        data["sun"] = next(iter(raw.values()))["_sun"]
        for event in H.EVENTS:
            per_model, per_model_src = {}, {}
            for model, res in raw.items():
                variants = res["_events"].get(event)
                if not variants:
                    continue
                src = self.amount_src if self.amount_src in variants else "mean"
                chosen = variants[src]
                per_model[model] = chosen
                per_model_src[H.MODEL_LABEL.get(model, model)] = {
                    "本模式": chosen["quality"],
                    "诊断云量": variants["diag"]["quality"],
                    "廓线反演": variants["rh"]["quality"],
                }
            if not per_model:
                continue
            q = sum(v["quality"] for v in per_model.values()) / len(per_model)
            ref = next(iter(per_model.values()))
            data["events"][event] = {
                "quality": round(q, 3),
                "peak": round(sum(v["peak"] for v in per_model.values()) / len(per_model), 3),
                "peak_time": ref["peak_time"],
                "window": ref["window"],
                "duration_min": ref["duration_min"],
                "aod": ref["aod"],
                "layers": ref["layers"],
                "cloud": _merge_cloud(per_model),
                "event_time": ref["time"],
                "event_hhmm": ref["time_hhmm"],
                "azimuth": ref["azimuth"],
                "kind": ref["kind"],
                "per_model": per_model_src,
                "series": ref["series"],
                "amount_src": self.amount_src,
            }
        self._stamp_events(data)
        self._adapt_interval(data["next"])
        return data

    # ------------------------------------------------------------ 事件时刻刻印
    def _stamp_events(self, data: dict) -> None:
        """每个事件都带上"距现在多少小时/是否已过"; 并挑出下一次霞光(内部用)"""
        now = data["updated"]
        for event, info in data["events"].items():
            try:
                t = datetime.strptime(info["event_time"], "%Y-%m-%d %H:%M")
            except (ValueError, KeyError):
                continue
            hours = (t - now).total_seconds() / 3600.0
            info["in_hours"] = round(hours, 2)
            info["is_future"] = hours >= 0
        data["next"] = self._pick_next(data)

    def _pick_next(self, data: dict) -> dict | None:
        now = data["updated"]
        cands = []
        for event, info in data["events"].items():
            if "in_hours" not in info:
                continue
            try:
                t = datetime.strptime(info["event_time"], "%Y-%m-%d %H:%M")
            except (ValueError, KeyError):
                continue
            cands.append((t, event, info))
        if not cands:
            return None
        future = [c for c in cands if c[0] >= now]
        t, event, info = min(future) if future else max(cands)
        return {"event": event, "name": H.EVENT_CN[event], "time": info["event_time"],
                "hhmm": t.strftime("%H:%M"), "in_hours": info["in_hours"],
                "is_future": info["is_future"], "quality": info["quality"],
                "aod": info["aod"], "layers": info["layers"], "cloud": info.get("cloud")}

    # ------------------------------------------------------------ 请求节拍
    def _adapt_interval(self, nxt: dict | None) -> None:
        """分档节拍 + 数据未变时退避(距事件 6h 内不退避)"""
        base, near = self._base_interval, self._near_interval
        if not nxt or not nxt.get("is_future"):
            want = base
            hours = 99.0
        else:
            hours = nxt["in_hours"]
            if hours <= TIER_NEAR_H:
                want = min(near, base)
            elif hours <= TIER_MID_H:
                want = max(near, base // 2)
            elif hours <= TIER_FAR_H:
                want = base
            else:
                want = max(base, int(FAR_INTERVAL_H * 60))
        if self._unchanged and hours > NO_BACKOFF_WITHIN_H:
            want = min(want * min(self._unchanged + 1, BACKOFF_MAX),
                       int(BACKOFF_CAP_H * 60))
        interval = timedelta(minutes=max(5, want))
        if self.update_interval != interval:
            _LOGGER.debug("[%s] 刷新间隔 -> %s 分钟", self.site_name, interval)
            self.update_interval = interval
