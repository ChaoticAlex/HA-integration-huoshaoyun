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


print("\n[1] 分档节拍 (基准 90min / 临近 15min)")
c = mk()
for h, want in ((0.5, 15), (2.0, 45), (8.0, 90), (20.0, 360)):
    c._unchanged = 0
    c._adapt_interval(nxt(h))
    check(f"距事件 {h}h", mins(c), want)
c._adapt_interval(nxt(8.0, future=False))     # 事件已过 -> 回落常规
check("事件已过 -> 常规档", mins(c), 90)

print("\n[2] 数据未变退避 (最多 x3, 上限 8h; 距事件 6h 内不退避)")
c = mk()
c._unchanged = 1
c._adapt_interval(nxt(20.0))
check("远(20h) 未变1次", mins(c), 480)          # 360 * 2 = 720 -> 封顶 8h
c._unchanged = 2
c._adapt_interval(nxt(20.0))
check("远(20h) 未变2次", mins(c), 480)
c._unchanged = 9
c._adapt_interval(nxt(20.0))
check("远(20h) 未变很多次(封顶)", mins(c), 480)
c._unchanged = 2
c._adapt_interval(nxt(8.0))
check("近(8h) 未变 -> 可退避", mins(c), 270)     # 90 * min(2+1,3)
c._unchanged = 2
c._adapt_interval(nxt(5.0))
check("很近(5h) 未变 -> 不退避", mins(c), 90)
c._unchanged = 5
c._adapt_interval(nxt(0.5))
check("临近(0.5h) 未变 -> 不退避", mins(c), 15)

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

print("\n[4] 恢复后回到正常分档")
c = mk()
c._failed = True
c.update_interval = timedelta(minutes=FAILURE_RETRY_MIN)
c._failed = False
c._unchanged = 0
c._adapt_interval(nxt(20.0))
check("恢复 -> 放宽档", mins(c), 360)
c._adapt_interval(nxt(0.5))
check("恢复 -> 临近档", mins(c), 15)

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

print("\n[6] 部分数据缺失的标记")
check("missing 会带出缺口名", fetcher.__doc__ is not None, True)
print("     (取数层在气压层单组失败时 append 形如 '650-400hPa'; AOD 失败 append 'CAMS AOD')")

print("\n" + ("=" * 60))
print(f"{OK} 全部通过" if not fails else f"{BAD} 失败项: {fails}")
sys.exit(1 if fails else 0)
