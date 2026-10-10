"""数据协调器 —— 恒定低频节拍, 只求"够看"。

模型数据只在起报更新时变化(GFS 4 次/天, IFS 2~4 次/天, CAMS 2 次/天),
同一份起报内分数是常数, 因此:

- **恒定节拍**: 默认 180 分钟一拉, 不做"临近事件提速"(不需要), 行为可预测、请求量最小
- **请求最小化**: 每轮 3 个请求, 18 个断面点 × 2 模型合并进单次请求
- **数据指纹**: 未变则直接复用上轮计算结果, 省掉 ~0.3s 评分计算(不影响调度)
- **配额保护**: 429 不重试; 配额用尽则逐次翻倍长退避(30→720 分钟), 期间实体不可用
- **自动化友好**: 每个事件都带 `距今小时`/`已过`; 数据层暴露 `数据指纹`/`数据变更`

输出只有纯数值, 不做评级、不做"值得拍"判断 —— 交给 HA 自动化。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import fetcher
from . import hsy_core as H
from .const import (CONF_API_KEY, DEFAULT_INTERVAL, DOMAIN, FAILURE_RETRY_MIN, MODELS_MAP,
                    QUOTA_BACKOFF_CAP_MIN, QUOTA_BACKOFF_MIN)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SNAPSHOT_KEEP_DAYS = 2          # 快照只保留最近两天(每天 4 个事件)
SNAPSHOT_FIELDS_SKIP = ("in_hours", "is_future", "冻结于", "事后重算")


def _merge_cloud(per_model: dict) -> dict | None:
    """把各模式的云层结构诊断按均值合并(与主值=各模式均值同口径)"""
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
        self._last_fp: str | None = None
        self._quota_strikes = 0      # 连续配额用尽次数, 用于逐次翻倍退避
        # 事件过去后必须"冻结"在最后一次事前预报上, 否则拿到的不是"当时预报说了多少"
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._snapshots: dict[str, dict] = {}
        self._store_loaded = False
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
            data = dict(self.data)
            data["updated"] = now
            data["data_changed"] = False
            data["partial"] = meta["partial"]
            data["missing"] = meta["missing"]
            await self._load_snapshots()
            self._stamp_events(data)
            self._apply_freeze(data)
            self._stamp_events(data)
            self._adapt_interval()
            _LOGGER.debug("[%s] 起报未变, 复用上轮结果", self.site_name)
            return data

        self._last_fp = fingerprint
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
        await self._load_snapshots()
        self._stamp_events(data)
        self._apply_freeze(data)
        self._stamp_events(data)
        self._adapt_interval()
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
                "hhmm": t.strftime("%H:%M"), "in_hours": info.get("in_hours"),
                "is_future": info.get("is_future"), "quality": info.get("quality"),
                "aod": info.get("aod"), "layers": info.get("layers"),
                "cloud": info.get("cloud")}

    # ------------------------------------------------------------ 事件冻结
    async def _load_snapshots(self) -> None:
        if self._store_loaded:
            return
        self._store_loaded = True
        try:
            self._snapshots = await self._store.async_load() or {}
            _LOGGER.debug("[%s] 载入 %d 条事前快照", self.site_name, len(self._snapshots))
        except Exception as err:                       # noqa: BLE001
            _LOGGER.warning("[%s] 事前快照载入失败, 从空开始: %s", self.site_name, err)
            self._snapshots = {}

    def _apply_freeze(self, data: dict) -> None:
        """事件未过: 记为"事前预报"快照; 事件已过: 用快照覆盖, 不再随新起报漂移。

        快照键 = 事件 + 事件日期 —— 跨过 00:00 后"今日朝霞"会指向新的一天,
        若只按事件名做键, 昨天的冻结值会被张冠李戴到今天。
        """
        now = data["updated"]
        changed = False
        for event, info in data["events"].items():
            try:
                t = datetime.strptime(info["event_time"], "%Y-%m-%d %H:%M")
            except (ValueError, KeyError):
                continue
            key = f"{event}_{t:%Y-%m-%d}"
            if now < t:                                # 事件未到: 更新快照(总是保留最新的那次事前预报)
                self._snapshots[key] = {
                    "saved_at": now.strftime("%Y-%m-%d %H:%M"),
                    "info": {k: v for k, v in info.items() if k not in SNAPSHOT_FIELDS_SKIP},
                }
                info["冻结于"] = None
                info["事后重算"] = False
                changed = True
            else:                                      # 事件已过: 冻结
                snap = self._snapshots.get(key)
                if snap:
                    for k, v in snap["info"].items():
                        info[k] = v
                    info["冻结于"] = snap["saved_at"]
                    info["事后重算"] = False
                else:                                  # 没有事前快照(例如集成是事后才装的)
                    info["冻结于"] = None
                    info["事后重算"] = True
        if changed:
            self._prune_snapshots()
            self._store.async_delay_save(lambda: self._snapshots, 60)

    def _prune_snapshots(self) -> None:
        """只保留最近 SNAPSHOT_KEEP_DAYS 天的快照"""
        cutoff = (self._now() - timedelta(days=SNAPSHOT_KEEP_DAYS)).strftime("%Y-%m-%d")
        for key in [k for k in self._snapshots
                    if k.rsplit("_", 1)[-1] < cutoff]:
            self._snapshots.pop(key, None)

    # ------------------------------------------------------------ 刷新节拍
    def _adapt_interval(self) -> None:
        """恒定节拍: 只有配额用尽才临时拉长, 恢复后回到配置的固定间隔。"""
        if self._quota_strikes:
            mins = min(QUOTA_BACKOFF_MIN * (2 ** (self._quota_strikes - 1)),
                       QUOTA_BACKOFF_CAP_MIN)
        else:
            mins = self._base_interval
        interval = timedelta(minutes=max(5, mins))
        if self.update_interval != interval:
            _LOGGER.debug("[%s] 刷新间隔 -> %s 分钟", self.site_name, interval)
            self.update_interval = interval
