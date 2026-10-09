"""火烧云/朝霞-晚霞鲜艳度算法核心 —— 纯计算, 不依赖 HA / 网络。

算法依据: sunsetbot.top 公开因子清单 + 作者 sdc《火烧云预报教程》1~4 章
因子: 持续时间、云量(分层云量/相态/含水量)、占据天空面积、云底高度、
      层次间照明与遮挡、气溶胶光学厚度 AOD

几何(可自洽推导, 与教程 1.2.2 "火烧云三角"及表1.3 一致):
  太阳高度角 -s (s>0, 弧度) 时, 晨昏线距观察者 d = R*s
  高度 z 的云被照亮:  z > (x-d)^2/(2R)      被看见: z > x^2/(2R)
  => 霞光深入距离 sqrt(2Rz); 教程表1.3 云底2km->319.23km 即 2*sqrt(2Rz)=319.3km
  朝霞与晚霞几何完全对称: 只需把观察窗口换成日出前、方位角换成日出方位。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

R = 6371000.0
PLEV = [950, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200]
TRANSECT_KM = [0, 30, 60, 100, 150, 210, 270, 330, 400]
GFS, ECMWF = "gfs_ifs_gfs", "ecmwf_ifs025"          # 占位, 实际由 fetcher 传入
MODEL_LABEL = {"gfs_global": "NCEP-GFS", "ecmwf_ifs025": "ECMWF-IFS"}

# 评分权重(复刻假设, 非官方)
W = {
    "aod_k": 0.9,        # AOD 消光 -> 通透度衰减
    "cal": 1.45,         # 标定: 使"理想大烧"≈1.0
    "lam": 0.15,         # 太阳下沉 -> 照明强度衰减 /°
    "block_k": 0.85,     # 阴影中低/中云对本地方向天空的遮挡
    "dur_ref": 25.0,     # 参考持续分钟
    "aod_fallback": 0.2, # AOD 数据缺失时的中性浑浊度(仅参与计算, 对外报 None)
    "half_window": 45,   # 事件前后观察窗(分钟)
    "step": 5,           # 时间步长(分钟)
}
# 站点官方分级(站内原文) —— 仅作参考与文档用途, HA 集成不对外输出评级
# (判断"值不值得拍"由用户自己的 HA 自动化按阈值决定)
GRADES = [(0.001, "不烧"), (0.05, "微微烧"), (0.2, "小烧"), (0.4, "小烧~中等烧"),
          (0.6, "中等烧"), (0.8, "中等烧~大烧")]
GRADE_TOP = "大烧"


def grade(v: float) -> str:
    for thr, name in GRADES:
        if v < thr:
            return name
    return GRADE_TOP


# ---------------------------------------------------------------- 天文
def solar_pos(dt_utc: datetime, lat: float, lon: float) -> tuple[float, float]:
    """NOAA 简化算法 -> (太阳高度角°, 方位角°, 含近地平折射校正)"""
    d = (dt_utc - datetime(2000, 1, 1, 12)).total_seconds() / 86400.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    L = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    e = math.radians(23.439 - 0.00000036 * d)
    dec = math.asin(math.sin(e) * math.sin(L))
    ra = math.atan2(math.cos(e) * math.sin(L), math.cos(L))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    lst = math.radians((gmst * 15 + lon) % 360)
    ha = (lst - ra + math.pi) % (2 * math.pi) - math.pi
    la = math.radians(lat)
    el = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(dec) * math.cos(la))
    az = (math.degrees(az) + 180) % 360
    el_deg = math.degrees(el)
    if -2 < el_deg < 5:
        el_deg += (1.02 / math.tan(math.radians(el_deg + 10.3 / (el_deg + 5.11)))) / 60.0
    return el_deg, az


def dest_point(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """球面大圆终点"""
    b, dd = math.radians(bearing_deg), dist_m / R
    la1, lo1 = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la1) * math.cos(dd) + math.cos(la1) * math.sin(dd) * math.cos(b))
    lo2 = lo1 + math.atan2(math.sin(b) * math.sin(dd) * math.cos(la1),
                           math.cos(dd) - math.sin(la1) * math.sin(la2))
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


EVENTS = ("rise_1", "set_1", "rise_2", "set_2")
EVENT_CN = {"rise_1": "今日朝霞", "set_1": "今日晚霞", "rise_2": "明日朝霞", "set_2": "明日晚霞"}
EVENT_KIND = {"rise_1": "rise", "rise_2": "rise", "set_1": "set", "set_2": "set"}


def sun_events(lat: float, lon: float, base_date: datetime, tz_hours: float) -> dict:
    """扫描 base_date 当天 00:00 起 48h, 求 4 个事件的本地时刻与方位角"""
    out = {}
    rises, sets = [], []
    t = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    prev_el, _ = solar_pos(t - timedelta(hours=tz_hours), lat, lon)
    steps = 48 * 60
    for k in range(1, steps + 1):
        tk = t + timedelta(minutes=k)
        el, az = solar_pos(tk - timedelta(hours=tz_hours), lat, lon)
        if prev_el <= 0 < el:
            rises.append((tk, az))
        elif prev_el > 0 >= el:
            sets.append((tk, az))
        prev_el = el
    for key, seq in (("rise_1", rises[:1]), ("set_1", sets[:1]),
                     ("rise_2", rises[1:2]), ("set_2", sets[1:2])):
        if seq:
            out[key] = {"time": seq[0][0], "azimuth": seq[0][1]}
    return out


# ---------------------------------------------------------------- 廓线/云
def _nearest_hour_idx(times: list, t: datetime) -> int | None:
    key = t.strftime("%Y-%m-%dT%H:00")
    if key in times:
        return times.index(key)
    return None


def tv(hourly: dict, var: str, t: datetime, default=None):
    """按整点做线性时间插值(模型为逐小时输出)"""
    times = hourly.get("time") or []
    i = _nearest_hour_idx(times, t)
    if i is None:
        return default
    vals = hourly.get(var)
    if not vals:
        return default
    j = min(i + 1, len(times) - 1)
    a, b = vals[i], vals[j]
    if a is None or b is None:
        return a if a is not None else (b if b is not None else default)
    if i == j:
        return a
    frac = t.minute / 60.0
    return a + (b - a) * frac


def _lv(plev_of_level: dict, kind: str, L: int) -> float | None:
    return plev_of_level.get(f"{kind}_{L}hPa")


RH_LAYER_MIN = 78.0      # 湿层(RH 阈值)检测下限


def moist_layers(hourly: dict, t: datetime) -> list[tuple]:
    """RH>=78% 的联通湿层 -> [(底_m, 顶_m, RH_max, 层中温度_C)]"""
    hs, rh, tt = [], [], []
    for L in PLEV:
        h = tv(hourly, f"geopotential_height_{L}hPa", t)
        r = tv(hourly, f"relative_humidity_{L}hPa", t)
        x = tv(hourly, f"temperature_{L}hPa", t)
        if None in (h, r, x):
            continue
        hs.append(h); rh.append(r); tt.append(x)
    o = sorted(range(len(hs)), key=lambda i: hs[i])
    hs = [hs[i] for i in o]; rh = [rh[i] for i in o]; tt = [tt[i] for i in o]
    layers, cur = [], None
    for i in range(len(hs)):
        if rh[i] >= RH_LAYER_MIN:
            if cur is None:
                cur = [hs[i], hs[i], rh[i], tt[i]]
            else:
                cur[1] = hs[i]; cur[2] = max(cur[2], rh[i]); cur[3] = tt[i]
        elif cur:
            layers.append(tuple(cur)); cur = None
    if cur:
        layers.append(tuple(cur))
    return layers


def cls_of(h: float) -> str:
    return "low" if h < 2500 else ("mid" if h < 6000 else "high")


def rh_amount(hourly: dict, t: datetime) -> dict:
    """湿度廓线反演分层云量。

    与湿层检测阈值连续: RH=78%(检出下限)→0, RH=100%→满云。
    (旧式 (RH-55)/35 与 78% 阈值不连续, 任何被检出的层至少得 0.66, 阈值附近跳变。)
    按"湿层"归并后以**云底高度**定类别 —— 与 moist_layers/_class_setup 同口径,
    否则同一层饱和气团会被同时算作中云和高云(重复计数)。
    """
    out = {"low": 0.0, "mid": 0.0, "high": 0.0}
    for base_h, _top, rh, _tc in moist_layers(hourly, t):
        c = max(0.0, min(1.0, (rh - RH_LAYER_MIN) / (100.0 - RH_LAYER_MIN)))
        k = cls_of(base_h)
        out[k] = max(out[k], c)
    return out


def phase_weight(base: float, top: float, tc: float | None) -> float:
    """云相态/云型权重(教程 2.1 各类云能否烧的定性结论)"""
    if tc is None:
        return 0.75
    thick = top - base
    if tc < -35:
        return 0.55          # 高空薄卷云: 亮但淡
    if tc < -5:
        return 1.00          # 冰/混合相态中高云: 最佳
    if thick > 4000:
        return 0.35          # 深厚对流云: 自遮挡
    if thick < 300:
        return 0.55
    return 0.80              # 液态水云


def annulus_weights(z: float, xs: list[float]) -> list[float]:
    """高度 z 云盘各环带占观察者天空的立体角份额(按云盘真实视面积)"""
    edges = [0.0] + [(xs[i] + xs[i + 1]) / 2.0 for i in range(len(xs) - 1)] + [800000.0]

    def S(X):
        return 0.0 if X <= 0 else 1.0 - z / math.sqrt(X * X + z * z)

    w = [max(0.0, S(edges[k + 1]) - S(edges[k])) for k in range(len(xs))]
    tot = sum(w)
    return [v / tot for v in w] if tot > 0 else [1.0 / len(xs)] * len(xs)


# ---------------------------------------------------------------- 评分
def score_once(lat: float, lon: float, t_local: datetime, tz_hours: float,
               blk: dict, xs: list[float], class_h: dict) -> tuple[float, dict]:
    el, _ = solar_pos(t_local - timedelta(hours=tz_hours), lat, lon)
    if el > 0.0:
        return 0.0, {"t": t_local.strftime("%H:%M"), "el": round(el, 2), "S": 0.0,
                     "note": "太阳在地平线上"}
    d = R * math.radians(-el)
    P_tot, dark, det = 0.0, 0.0, []
    for cls in ("low", "mid", "high"):
        z = class_h[cls]
        w = annulus_weights(z, xs)
        Pc = 0.0
        for i, x in enumerate(xs):
            c = blk[cls][i]
            if c <= 0:
                continue
            if z <= x * x / (2 * R):            # 低于观察者地平切线 -> 看不见
                continue
            if z > (x - d) ** 2 / (2 * R):      # 高于地球阴影面 -> 被照亮
                Pc += w[i] * c
            else:                               # 阴影中的云 -> 剪影挡光
                dark += w[i] * c * (1.0 if cls == "low" else 0.5)
        P_tot += Pc * blk["pw"][cls]
        if Pc > 0.01:
            det.append(f"{cls}[z={z/1000:.1f}km 亮面{Pc*100:.0f}%]")
    C_eff = 1.0 - math.exp(-P_tot)              # 天空面积有限, 软上限
    dark = min(dark, 1.0)
    S = (C_eff * (1 - W["block_k"] * dark)
         * math.exp(-W["aod_k"] * blk["aod"])
         * math.exp(-W["lam"] * abs(el)) * W["cal"])
    return S, {"t": t_local.strftime("%H:%M"), "el": round(el, 2), "S": round(S, 3),
               "Ceff": round(C_eff, 2), "dark": round(dark, 2),
               "aod": round(blk["aod"], 2), "shadow_km": round(d / 1000),
               "layers": "; ".join(det) or "-"}


CLASS_CN = {"low": "低云", "mid": "中云", "high": "高云"}


def _class_setup(pts0_hourly: dict, t: datetime) -> tuple[dict, dict, dict]:
    """由事件时刻观察者上空的廓线定出三类云的等效高度与相态权重。

    返回 (class_h, pw, layer_of); layer_of[cls] 为该类选中的湿层
    (底_m, 顶_m, RH_max, 层中温度_C) 或 None(该层无 RH>=78% 湿层)。
    无湿层时 class_h 用占位高度, 仅服务于几何计算, 不对外声明为"云底"。
    """
    lay = moist_layers(pts0_hourly, t)
    class_h, pw, layer_of = {}, {}, {}
    for cls, dflt in (("low", 1500.0), ("mid", 4500.0), ("high", 8500.0)):
        cand = [l for l in lay if cls_of(l[0]) == cls]
        if cand:
            l = max(cand, key=lambda v: v[2])
            class_h[cls] = max(400.0, (l[0] + l[1]) / 2)
            pw[cls] = phase_weight(l[0], l[1], l[3])
            layer_of[cls] = l
        else:
            class_h[cls] = dflt
            pw[cls] = 0.75
            layer_of[cls] = None
    return class_h, pw, layer_of


def cloud_text(layer_of: dict) -> str:
    """人读文本: 低云 无湿层; 中云 5.9-6.7km RH88% T-9℃; 高云 无湿层"""
    out = []
    for cls in ("low", "mid", "high"):
        l = layer_of.get(cls)
        if l:
            out.append(f"{CLASS_CN[cls]} {l[0]/1000:.1f}-{l[1]/1000:.1f}km "
                       f"RH{l[2]:.0f}% T{l[3]:.0f}℃")
        else:
            out.append(f"{CLASS_CN[cls]} 无湿层")
    return "; ".join(out)


def analyse_event(lat: float, lon: float, event: str, event_time: datetime, azimuth: float,
                  tz_hours: float, pts: list[dict], aod_pts: list[dict],
                  amount_src: str = "rh") -> dict:
    """对单个事件(朝霞/晚霞)算鲜艳度时间序列并汇总"""
    xs = [k * 1000 for k in TRANSECT_KM]
    class_h, pw, layer_of = _class_setup(pts[0], event_time)
    series, half, step = [], W["half_window"], W["step"]
    aod_found = False
    for j in range(-half // step, half // step + 1):
        t = event_time + timedelta(minutes=j * step)
        blk = {"low": [], "mid": [], "high": [], "pw": pw}
        for i, pt in enumerate(pts):
            if amount_src == "diag":
                vals = {c: (tv(pt, f"cloud_cover_{c}", t, 0) or 0) / 100.0
                        for c in ("low", "mid", "high")}
            else:
                vals = rh_amount(pt, t)
            for c in ("low", "mid", "high"):
                blk[c].append(vals[c])
        num = den = 0.0
        for i, x in enumerate(xs):
            if i >= len(aod_pts):
                break
            a = tv(aod_pts[i], "aerosol_optical_depth", t)
            if a is None:
                continue
            aod_found = True
            wgt = math.exp(-x / 250000.0)
            num += a * wgt; den += wgt
        blk["aod"] = max(0.0, min(3.0, (num / den) if den else W["aod_fallback"]))
        S, dg = score_once(lat, lon, t, tz_hours, blk, xs, class_h)
        series.append((t, S, dg))
    peak = max((s for _, s, _ in series), default=0.0)
    thr = max(0.05, 0.15 * peak)
    dur = step * sum(1 for _, s, _ in series if s > thr)
    total = peak * (0.4 + 0.6 * min(1.0, dur / W["dur_ref"]))
    lit = [(t, s) for t, s, _ in series if s > thr]
    peak_t = max(series, key=lambda v: v[1])[0]
    # ---- 结构化云况(供 HA 自动化直接取用) ----
    if amount_src == "diag":
        cover = {c: (tv(pts[0], f"cloud_cover_{c}", event_time, 0) or 0) / 100.0
                 for c in ("low", "mid", "high")}
    else:
        cover = rh_amount(pts[0], event_time)
    classes = {}
    for cls in ("low", "mid", "high"):
        l = layer_of.get(cls)
        classes[cls] = {
            "云量": round(cover[cls], 3),
            "云底_m": int(round(l[0])) if l else None,
            "云顶_m": int(round(l[1])) if l else None,
            "温度_C": round(l[3], 1) if l else None,
            "相态": (("ice" if l[3] < 0 else "water") if l else None),
        }
    present = [c for c in ("low", "mid", "high") if cover[c] >= 0.10]
    dom = max(("low", "mid", "high"), key=lambda c: cover[c] * pw[c]) if present else None
    cloud = {"classes": classes, "present": present,
             "dominant": dom,
             "dominant_base_m": classes[dom]["云底_m"] if dom else None,
             "text": cloud_text(layer_of)}
    # AOD 取"有效评分时刻"的代表值(中点可能落在太阳已升起/已落下的空档)。
    # AOD 数据缺失时用中性值参与计算, 但对外返回 None, 避免静默偏差。
    aods = [dg["aod"] for _, _, dg in series if dg.get("aod") is not None]
    aod_rep = round(sum(aods) / len(aods), 2) if (aods and aod_found) else None
    return {
        "event": event, "event_cn": EVENT_CN[event], "kind": EVENT_KIND[event],
        "time": event_time.strftime("%Y-%m-%d %H:%M"),
        "time_hhmm": event_time.strftime("%H:%M"),
        "azimuth": round(azimuth, 1),
        "quality": round(total, 3), "grade": grade(total),
        "peak": round(peak, 3), "peak_time": peak_t.strftime("%H:%M"),
        "duration_min": dur,
        "window": (f"{lit[0][0]:%H:%M}-{lit[-1][0]:%H:%M}" if lit else "-"),
        "aod": aod_rep,
        "layers": cloud["text"], "cloud": cloud,
        "series": [(t.strftime("%H:%M"), round(s, 3), dg) for t, s, dg in series],
    }


def analyse_all(lat: float, lon: float, base_date: datetime, tz_hours: float,
                transects: dict, amount_srcs=("diag", "rh", "mean")) -> dict:
    """transects: {"rise": {"pts":[...], "aod":[...]}, "set": {...}}
    返回 {event: {src: 结果, ...}, "_sun": {...}}"""
    ev = sun_events(lat, lon, base_date, tz_hours)
    out = {"_sun": {k: {"time": v["time"].strftime("%Y-%m-%d %H:%M"),
                        "azimuth": round(v["azimuth"], 1)} for k, v in ev.items()},
           "_events": {}}
    for event, info in ev.items():
        kind = EVENT_KIND[event]
        tr = transects[kind]
        res = {}
        for src in amount_srcs:
            if src == "mean":
                continue
            res[src] = analyse_event(lat, lon, event, info["time"], info["azimuth"],
                                     tz_hours, tr["pts"], tr["aod"], amount_src=src)
        if "diag" in res and "rh" in res:
            m = dict(res["rh"])
            q = (res["diag"]["quality"] + res["rh"]["quality"]) / 2.0
            m["quality"] = round(q, 3); m["grade"] = grade(q)
            m["variant"] = {"diag": res["diag"]["quality"], "rh": res["rh"]["quality"]}
            cl = {k: dict(v) for k, v in res["rh"]["cloud"]["classes"].items()}
            for cls in cl:                       # 云量取两口径均值, 高度/相态同源
                cl[cls]["云量"] = round((res["diag"]["cloud"]["classes"][cls]["云量"]
                                         + res["rh"]["cloud"]["classes"][cls]["云量"]) / 2, 3)
            present = [c for c in ("low", "mid", "high") if cl[c]["云量"] >= 0.10]
            m["cloud"] = {**res["rh"]["cloud"], "classes": cl, "present": present}
            res["mean"] = m
        out["_events"][event] = res
    return out
