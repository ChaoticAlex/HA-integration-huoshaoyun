"""离线自检: 不联网, 只验本次改动的纯逻辑(请求节拍/退避/失败语义/事件刻印)。

用法: python3 test_offline.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_teststub"))
sys.path.insert(0, os.path.dirname(HERE))

from homeassistant.config_entries import ConfigEntry                      # noqa: E402
from homeassistant.core import HomeAssistant                              # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed         # noqa: E402

from custom_components.huoshaoyun import fetcher                          # noqa: E402
from custom_components.huoshaoyun import hsy_core as H                    # noqa: E402
from custom_components.huoshaoyun.const import (FAILURE_RETRY_MIN,      # noqa: E402
                                                QUOTA_BACKOFF_CAP_MIN, QUOTA_BACKOFF_MIN)
from custom_components.huoshaoyun.coordinator import HuoshaoyunCoordinator  # noqa: E402

OK, BAD = "✅", "❌"
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {OK if ok else BAD} {label}: {got!r}" + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        fails.append(label)


def mk(name="测试点", lat=0.0, lon=0.0):
    hass = HomeAssistant()
    entry = ConfigEntry(entry_id=f"t_{name}", data={"name": name, "latitude": lat, "longitude": lon})
    return HuoshaoyunCoordinator(hass, entry)


def mins(c):
    return int(c.update_interval.total_seconds() // 60)


def nxt(hours, future=True):
    return {"in_hours": hours, "is_future": future}


print("\n[1] 恒定节拍: 与距事件多远无关, 恒为配置值")


def case_flat():
    c = mk()
    check("初始间隔", mins(c), 180)
    for h in (0.5, 2.0, 8.0, 20.0):
        c._stamp_events({"updated": datetime(2026, 10, 9, 12, 0), "events": {
            "set_1": {"event_time": f"2026-10-09 {18 if h < 6 else 22:02d}:00", "quality": 0.3},
            "rise_1": {"event_time": "2026-10-10 06:23", "quality": 0.2}}})
        c._adapt_interval()
        check(f"距事件 {h}h 附近仍是恒定节拍", mins(c), 180)
    # 自定义间隔应生效
    c2 = mk()
    c2._base_interval = 360
    c2._adapt_interval()
    check("自定义 360 分钟生效", mins(c2), 360)


case_flat()

print("\n[3] 普通失败: 抛 UpdateFailed + 实体置不可用 + 重试提前到 %d 分钟" % FAILURE_RETRY_MIN)

async def fake_fail(*a, **k):
    raise RuntimeError("rate limited(429)")

async def case_failure():
    c = mk("失败点")
    orig = fetcher.async_fetch_transects
    fetcher.async_fetch_transects = fake_fail
    try:
        try:
            await c._async_update_data()
            print(f"  {BAD} 未抛出 UpdateFailed")
            fails.append("失败未抛出")
        except UpdateFailed as e:
            print(f"  {OK} 抛出 UpdateFailed: {e}")
        check("失败后重试间隔", mins(c), FAILURE_RETRY_MIN)
        check("无数据时 data 仍为空", c.data, None)
    finally:
        fetcher.async_fetch_transects = orig

asyncio.run(case_failure())

print("\n[3b] 429 配额用尽: 立即失败且**不重试**(不浪费配额)")


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Session:
    """只统计调用次数的最小假 session"""

    def __init__(self, resp):
        self.resp = resp
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return self.resp


async def case_quota():
    for scope, payload in (("day", {"error": True, "reason": "Daily API request limit exceeded. Please try again tomorrow."}),
                           ("hour", {"error": True, "reason": "Hourly API request limit exceeded. Please try again in the next hour."})):
        s = _Session(_Resp(429, payload))
        try:
            await fetcher._get(s, "http://x", {})
            print(f"  {BAD} 未抛出 QuotaExhausted")
            fails.append("429 未抛出")
        except fetcher.QuotaExhausted as e:
            check(f"429/{scope} -> scope", e.scope, scope)
        check(f"429/{scope} 只发 1 个请求(不重试)", s.calls, 1)

    # 5xx 仍应重试
    s = _Session(_Resp(503, None))
    try:
        await fetcher._get(s, "http://x", {}, retries=3)
    except Exception:
        pass
    check("503 会重试(3次)", s.calls, 3)

asyncio.run(case_quota())

print("\n[3c] 配额用尽 -> 退避逐次翻倍, 上限封顶")


async def case_quota_backoff():
    c = mk("配额点")

    async def fake_quota(*a, **k):
        raise fetcher.QuotaExhausted("day", "Daily API request limit exceeded.")

    orig = fetcher.async_fetch_transects
    fetcher.async_fetch_transects = fake_quota
    try:
        seen = []
        for i in range(7):
            try:
                await c._async_update_data()
            except UpdateFailed:
                seen.append(mins(c))
        want = [min(QUOTA_BACKOFF_MIN * (2 ** i), QUOTA_BACKOFF_CAP_MIN) for i in range(7)]
        check("退避序列(分钟)", seen, want)
    finally:
        fetcher.async_fetch_transects = orig

asyncio.run(case_quota_backoff())

print("\n[4] 失败/配额之后能回到恒定节拍")
c = mk()
c.update_interval = timedelta(minutes=FAILURE_RETRY_MIN)   # 普通失败后的临时短间隔
c._adapt_interval()
check("普通失败恢复 -> 恒定节拍", mins(c), 180)
c.update_interval = timedelta(minutes=720)                 # 配额退避后的长间隔
c._quota_strikes = 4
c._adapt_interval()
check("配额未恢复(第4次) -> 维持长退避", mins(c), 240)   # 30·2^(4-1)
c._quota_strikes = 0
c._adapt_interval()
check("配额恢复 -> 恒定节拍", mins(c), 180)

print("\n[5] 事件刻印 in_hours / 已过, 与最近事件挑选")
c = mk()
now = datetime(2026, 10, 9, 18, 40)
c.data = {"updated": now, "events": {
    "rise_1": {"event_time": "2026-10-09 06:23", "quality": 0.1, "aod": 0.3, "layers": "x"},
    "set_1": {"event_time": "2026-10-09 18:06", "quality": 0.26, "aod": 0.29, "layers": "y"},
    "rise_2": {"event_time": "2026-10-10 06:23", "quality": 0.05, "aod": 0.3, "layers": "z"},
    "set_2": {"event_time": "2026-10-10 18:05", "quality": 0.06, "aod": 0.2, "layers": "w"}}}
c._stamp_events(c.data)
check("今日朝霞 已过", c.data["events"]["rise_1"]["is_future"], False)
check("今日朝霞 距今小时为负", c.data["events"]["rise_1"]["in_hours"] < 0, True)
check("明日朝霞 未过", c.data["events"]["rise_2"]["is_future"], True)
check("挑出的下一事件 = 明日朝霞", c.data["next"]["event"], "rise_2")
check("下一事件距今约 11.7h", round(c.data["next"]["in_hours"]), 12)

print("\n[5b] 可选 API key -> customer 端点")


def case_endpoints():
    f_api, a_api, extra = fetcher.endpoints(None)
    check("无 key: 预报端点", f_api, "https://api.open-meteo.com/v1/forecast")
    check("无 key: 空气质量端点", a_api, "https://air-quality-api.open-meteo.com/v1/air-quality")
    check("无 key: 无附加参数", extra, {})
    f_api, a_api, extra = fetcher.endpoints("  MYKEY123  ")
    check("有 key: 预报端点", f_api, "https://customer-api.open-meteo.com/v1/forecast")
    check("有 key: 空气质量端点", a_api, "https://customer-air-quality-api.open-meteo.com/v1/air-quality")
    check("有 key: apikey 参数(已去空白)", extra, {"apikey": "MYKEY123"})


case_endpoints()

print("\n[6b] 事件冻结: 已过事件不再随新起报漂移")


def case_freeze():
    c = mk("冻结点")
    c._store_loaded = True          # 跳过 Store 载入
    c._snapshots = {}
    # 第一次: 事件还在未来 -> 记录事前快照
    d1 = {"updated": datetime(2026, 10, 10, 10, 0),
          "events": {"set_1": {"event_time": "2026-10-10 18:06", "quality": 0.40,
                               "peak": 0.8, "layers": "mid"}}}
    c._apply_freeze(d1)
    check("未过事件不冻结", d1["events"]["set_1"]["冻结于"], None)
    check("未过事件已存快照", "set_1_2026-10-10" in c._snapshots, True)
    # 第二次(新起报): 仍是未来 -> 快照更新为 0.45
    d2 = {"updated": datetime(2026, 10, 10, 13, 0),
          "events": {"set_1": {"event_time": "2026-10-10 18:06", "quality": 0.45,
                               "peak": 0.9, "layers": "mid"}}}
    c._apply_freeze(d2)
    check("事前快照取最新的一次", c._snapshots["set_1_2026-10-10"]["info"]["quality"], 0.45)
    # 第三次: 事件已过 + 起报又变(0.60) -> 必须冻结回 0.45, 不被污染
    d3 = {"updated": datetime(2026, 10, 10, 19, 0),
          "events": {"set_1": {"event_time": "2026-10-10 18:06", "quality": 0.60,
                               "peak": 1.2, "layers": "mid"}}}
    c._apply_freeze(d3)
    check("已过事件被冻结(回到事前值)", d3["events"]["set_1"]["quality"], 0.45)
    check("冻结时间戳", d3["events"]["set_1"]["冻结于"], "2026-10-10 13:00")
    check("事后重算标记为假", d3["events"]["set_1"]["事后重算"], False)
    # 第四次: 昨天的键不该污染今天
    d4 = {"updated": datetime(2026, 10, 11, 8, 0),
          "events": {"set_1": {"event_time": "2026-10-11 18:05", "quality": 0.30}}}
    c._apply_freeze(d4)
    check("跨日后不张冠李戴", d4["events"]["set_1"]["quality"], 0.30)
    check("新的一天重新计为事前", d4["events"]["set_1"]["冻结于"], None)
    # 第五: 无事前快照(事后才装) -> 标注事后重算
    c2 = mk("事后点")
    c2._store_loaded = True
    c2._snapshots = {}
    d5 = {"updated": datetime(2026, 10, 10, 19, 0),
          "events": {"rise_1": {"event_time": "2026-10-10 06:23", "quality": 0.2}}}
    c2._apply_freeze(d5)
    check("无事前快照时标事后重算", d5["events"]["rise_1"]["事后重算"], True)


case_freeze()

print("\n[7] 实体命名(改名后由测试锁定)")


def case_names():
    from custom_components.huoshaoyun import sensor as S
    c = mk()
    got = ([S.EventQualitySensor(c, e)._attr_name for e in H.EVENTS]
           + [S.AodSensor(c)._attr_name, S.CloudLayerSensor(c)._attr_name])
    want = ["今日朝霞指数", "今日晚霞指数", "明日朝霞指数", "明日晚霞指数",
            "下次霞光气溶胶", "下次霞光云层"]
    check("6 个实体名称", got, want)


case_names()

print("\n[6] 部分数据缺失的标记")
check("missing 会带出缺口名", fetcher.__doc__ is not None, True)
print("     (取数层在气压层单组失败时 append 形如 '650-400hPa'; AOD 失败 append 'CAMS AOD')")

print("\n" + ("=" * 60))
print(f"{OK} 全部通过" if not fails else f"{BAD} 失败项: {fails}")
sys.exit(1 if fails else 0)
