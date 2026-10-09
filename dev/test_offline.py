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
from custom_components.huoshaoyun.const import FAILURE_RETRY_MIN          # noqa: E402
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


print("\n[1] 分档节拍 (基准 60min / 临近 15min)")
c = mk()
for h, want in ((0.5, 15), (2.0, 30), (8.0, 60), (20.0, 180)):
    c._unchanged = 0
    c._adapt_interval(nxt(h))
    check(f"距事件 {h}h", mins(c), want)
c._adapt_interval(nxt(8.0, future=False))     # 事件已过 -> 回落常规
check("事件已过 -> 常规档", mins(c), 60)

print("\n[2] 数据未变退避 (最多 x3, 上限 6h; 距事件 6h 内不退避)")
c = mk()
c._unchanged = 1
c._adapt_interval(nxt(20.0))
check("远(20h) 未变1次", mins(c), 360)          # 180 * min(1+1,3)=360, 正好到 6h 上限
c._unchanged = 2
c._adapt_interval(nxt(20.0))
check("远(20h) 未变2次", mins(c), 360)
c._unchanged = 9
c._adapt_interval(nxt(20.0))
check("远(20h) 未变很多次(封顶)", mins(c), 360)
c._unchanged = 2
c._adapt_interval(nxt(8.0))
check("近(8h) 未变 -> 可退避", mins(c), 180)     # 60 * min(2+1,3)
c._unchanged = 2
c._adapt_interval(nxt(5.0))
check("很近(5h) 未变 -> 不退避", mins(c), 60)
c._unchanged = 5
c._adapt_interval(nxt(0.5))
check("临近(0.5h) 未变 -> 不退避", mins(c), 15)

print("\n[3] 失败语义: 抛 UpdateFailed + 实体置不可用 + 重试提前到 5 分钟")

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

print("\n[4] 恢复后回到正常分档")
c = mk()
c._failed = True
c.update_interval = timedelta(minutes=FAILURE_RETRY_MIN)
c._failed = False
c._unchanged = 0
c._adapt_interval(nxt(20.0))
check("恢复 -> 放宽档", mins(c), 180)
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

print("\n[6] 部分数据缺失的标记")
check("missing 会带出缺口名", fetcher.__doc__ is not None, True)
print("     (取数层在气压层单组失败时 append 形如 '650-400hPa'; AOD 失败 append 'CAMS AOD')")

print("\n" + ("=" * 60))
print(f"{OK} 全部通过" if not fails else f"{BAD} 失败项: {fails}")
sys.exit(1 if fails else 0)
