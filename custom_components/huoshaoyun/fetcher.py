"""数据抓取层 —— NCEP-GFS / ECMWF-IFS 云况廓线 + CAMS 气溶胶, 走 Open-Meteo。

与 sunsetbot.top 同源: 云况=GFS/IFS, 气溶胶=CAMS(ECMWF 大气组分模式)。
Open-Meteo 仅作取数通道(免 key), 数值本身即 GFS/IFS/CAMS。

请求优化: 18 个断面点 × 2 模型合并进同一次请求(字段带模型后缀),
每站点每次刷新仅 5 个请求(地表1 + 气压层3 + AOD1)。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime

import aiohttp

from .hsy_core import PLEV, TRANSECT_KM, dest_point, sun_events

_LOGGER = logging.getLogger(__name__)

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"
SURFACE_VARS = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")
FORECAST_DAYS = 2
_TIMEOUT = aiohttp.ClientTimeout(total=45)

# 多地点时 HA 会同时触发所有条目 -> 瞬间 N×5 个请求会撞上限额。
# 用一把全局锁把各站点的取数串行化, 再配合分块之间的短间隔, 天然摊开请求。
_FETCH_LOCK = asyncio.Lock()


class QuotaExhausted(Exception):
    """Open-Meteo 配额用尽(429)。

    这种情况**重试毫无意义且有害**: 每次重试都照样计入配额, 而"失败即重试"
    会与协调器的重试间隔构成自激循环, 使配额永远无法恢复。
    因此这里直接抛出, 由协调器做长退避(分钟~小时级)。
    """

    def __init__(self, scope: str, reason: str) -> None:
        super().__init__(reason)
        self.scope = scope          # "day" / "hour" / "other"
        self.reason = reason


async def _get(session: aiohttp.ClientSession, url: str, params: dict, retries: int = 3):
    """GET。

    - **429(配额)**: 立即抛出 QuotaExhausted, **不重试** —— 重试同样计费且会自激
    - 网络错误 / 5xx: 退避重试
    """
    last = None
    for attempt in range(retries):
        try:
            async with session.get(url, params=params, timeout=_TIMEOUT) as resp:
                if resp.status == 429:
                    reason = ""
                    try:
                        body = await resp.json(content_type=None)
                        reason = body.get("reason", "") if isinstance(body, dict) else ""
                    except Exception:                 # noqa: BLE001
                        reason = "429 Too Many Requests"
                    low = reason.lower()
                    scope = ("day" if ("daily" in low or "day" in low) else
                             "hour" if ("hourly" in low or "hour" in low) else "other")
                    raise QuotaExhausted(scope, reason or "429 Too Many Requests")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except QuotaExhausted:
            raise                                     # 不重试
        except Exception as err:                      # noqa: BLE001
            last = err
            if attempt < retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise last


def _plev_chunks() -> list[list[int]]:
    """气压层分块: 每块 ≤8 层 (8×3=24 个变量/请求, 实测可用) -> 整个廓线只需 1 个请求"""
    return [PLEV[i:i + 8] for i in range(0, len(PLEV), 8)]


def _normalize(entry: dict, models: tuple[str, ...]) -> dict[str, dict]:
    """把带模型后缀的 hourly 拆成 {model: {var: [...]}}"""
    hourly = entry.get("hourly") or {}
    out: dict[str, dict] = {m: {"time": hourly.get("time", [])} for m in models}
    for key, val in hourly.items():
        if key == "time":
            continue
        matched = False
        for m in models:
            suffix = "_" + m
            if key.endswith(suffix):
                out[m][key[: -len(suffix)]] = val
                matched = True
                break
        if not matched and len(models) == 1:
            out[models[0]][key] = val
    return out


async def async_fetch_transects(session: aiohttp.ClientSession, lat: float, lon: float,
                                tz: str, tz_hours: float, base_date: datetime,
                                models: tuple[str, ...]) -> tuple[dict, dict]:
    """返回 ({model: {"rise": {...}, "set": {...}}}, meta)

    meta = {"fingerprint": 数据指纹, "partial": 是否缺数据, "missing": [缺了什么]}

    pts[i] 是可直接喂给 hsy_core 的 hourly 字典(变量名为裸名, 整点等间隔)。
    指纹 = 所有原始返回体的哈希: 模型起报一换就变, 同一份起报内恒定
    (响应里的 generationtime_ms 之类易变元数据不参与哈希)。
    """
    async with _FETCH_LOCK:          # 各站点串行取数, 避免同时打爆配额
        return await _fetch_locked(session, lat, lon, tz, tz_hours, base_date, models)


async def _fetch_locked(session: aiohttp.ClientSession, lat: float, lon: float,
                        tz: str, tz_hours: float, base_date: datetime,
                        models: tuple[str, ...]) -> tuple[dict, dict]:
    ev = sun_events(lat, lon, base_date, tz_hours)
    az_rise = ev.get("rise_1", {}).get("azimuth", 90.0)
    az_set = ev.get("set_1", {}).get("azimuth", 270.0)

    pts_rise = [dest_point(lat, lon, az_rise, km * 1000) for km in TRANSECT_KM]
    pts_set = [dest_point(lat, lon, az_set, km * 1000) for km in TRANSECT_KM]
    pts_rise[0] = pts_set[0] = (lat, lon)
    all_pts = pts_rise + pts_set
    lats = ",".join(f"{p[0]:.4f}" for p in all_pts)
    lons = ",".join(f"{p[1]:.4f}" for p in all_pts)
    common = {"latitude": lats, "longitude": lons, "timezone": tz,
              "forecast_days": FORECAST_DAYS}

    # 1) 地表分层云量 (18点 × 2模型, 1 次请求)
    r_surf = await _get(session, FORECAST_API,
                        {**common, "hourly": ",".join(SURFACE_VARS),
                         "models": ",".join(models)})
    # 2) 气压层廓线 (18点 × 2模型, 分块) —— 单块失败不致命, 用已有层继续
    plev_results, missing = [], []
    for chunk in _plev_chunks():
        vs = [f"{v}_{L}hPa" for L in chunk
              for v in ("temperature", "relative_humidity", "geopotential_height")]
        try:
            plev_results.append(await _get(session, FORECAST_API,
                                           {**common, "hourly": ",".join(vs),
                                            "models": ",".join(models)}))
        except Exception as err:                       # noqa: BLE001
            _LOGGER.warning("气压层 %s 获取失败, 跳过该层组: %s", chunk, err)
            plev_results.append(None)
            missing.append(f"{chunk[0]}-{chunk[-1]}hPa")
        await asyncio.sleep(0.2)
    # 3) CAMS 气溶胶光学厚度 (18点, 与模式无关) —— 缺失则回落中性值, 不致命
    try:
        r_aod = await _get(session, AIR_API,
                           {"latitude": lats, "longitude": lons, "timezone": tz,
                            "hourly": "aerosol_optical_depth", "forecast_days": FORECAST_DAYS})
    except Exception as err:                           # noqa: BLE001
        _LOGGER.warning("CAMS AOD 获取失败, 本次按中性浑浊度计算: %s", err)
        r_aod = None
        missing.append("CAMS AOD")

    def as_list(r):
        if isinstance(r, list):
            return r
        if isinstance(r, dict) and "reason" in r:
            raise RuntimeError(f"open-meteo: {r['reason']}")
        return [r]

    surf = as_list(r_surf)                      # 分层云量是必需项, 缺了就没意义 -> 致命
    if len(surf) < len(all_pts):
        raise RuntimeError(f"open-meteo 返回点数不足: {len(surf)}/{len(all_pts)}")
    plevs = [as_list(r) if r is not None else [] for r in plev_results]
    aods = as_list(r_aod) if r_aod is not None else [{} for _ in all_pts]

    def build(model: str) -> dict:
        trans = {"rise": {"pts": [], "aod": []}, "set": {"pts": [], "aod": []}}
        for idx in range(len(all_pts)):
            merged: dict = {}
            for src in [surf[idx]] + [pv[idx] for pv in plevs if len(pv) > idx]:
                part = _normalize(src, models)[model]
                for k, v in part.items():
                    if k == "time":
                        merged.setdefault("time", v)
                    else:
                        merged[k] = v
            aod_pt = {}
            for k, v in (aods[idx].get("hourly") or {}).items():
                if k == "time":
                    aod_pt.setdefault("time", v)
                else:
                    aod_pt[k] = v
            if idx < len(TRANSECT_KM):
                trans["rise"]["pts"].append(merged)
                trans["rise"]["aod"].append(aod_pt)
            else:
                trans["set"]["pts"].append(merged)
                trans["set"]["aod"].append(aod_pt)
        return trans

    # 指纹只取 hourly 数据体 —— 响应里的 generationtime_ms 等元数据每次请求都变, 不能参与哈希
    fp = hashlib.md5()
    parts = [[e.get("hourly") for e in surf]]
    parts += [[e.get("hourly") for e in pv] for pv in plevs if pv]
    parts += [[e.get("hourly") for e in aods]] if r_aod is not None else []
    for part in parts:
        fp.update(json.dumps(part, sort_keys=True, separators=(",", ":")).encode())
    return {m: build(m) for m in models}, {
        "fingerprint": fp.hexdigest()[:12],
        "partial": bool(missing),
        "missing": missing,
    }
