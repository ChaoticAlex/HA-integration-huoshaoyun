"""离线内存体检(不联网)：量集成的常驻占用、单轮峰值、以及多轮刷新是否累积。

用法: python3 measure_memory.py [轮数]
"""
import asyncio
import gc
import os
import sys
import tracemalloc
from collections import Counter
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_teststub"))
sys.path.insert(0, os.path.dirname(HERE))

from homeassistant.config_entries import ConfigEntry                      # noqa: E402
from homeassistant.core import HomeAssistant                              # noqa: E402

from custom_components.huoshaoyun import fetcher                          # noqa: E402
from custom_components.huoshaoyun import hsy_core as H                    # noqa: E402
from custom_components.huoshaoyun import sensor as S                      # noqa: E402
from custom_components.huoshaoyun.coordinator import HuoshaoyunCoordinator  # noqa: E402


def deep_size(obj, seen=None):
    """递归求对象图的真实内存占用(bytes)"""
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    size = sys.getsizeof(obj, 0)
    if isinstance(obj, dict):
        size += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(deep_size(x, seen) for x in obj)
    elif hasattr(obj, "__dict__"):
        size += deep_size(obj.__dict__, seen)
    return size


def rss_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def fake_fetch_factory(tag=[0]):
    """合成断面数据, 每次调用返回不同指纹(模拟"每次都有新起报")"""
    times = [(datetime(2026, 10, 10) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(48)]
    n = len(times)
    levels = {900: 1000, 850: 1500, 700: 3100, 600: 4400, 500: 5900, 400: 7600, 300: 9700, 200: 12500}
    pt = {"time": times}
    for L, h in levels.items():
        mid = L <= 600
        pt[f"geopotential_height_{L}hPa"] = [h] * n
        pt[f"relative_humidity_{L}hPa"] = [85 if mid else 20] * n
        pt[f"temperature_{L}hPa"] = [-8 if mid else -45] * n
    pt["cloud_cover_low"] = [0] * n
    pt["cloud_cover_mid"] = [60] * n
    pt["cloud_cover_high"] = [0] * n
    aod = {"time": times, "aerosol_optical_depth": [0.3] * n}

    async def _fetch(*a, **k):
        tag[0] += 1
        return ({"gfs_global": {
            "rise": {"pts": [dict(pt) for _ in range(9)], "aod": [dict(aod) for _ in range(9)]},
            "set": {"pts": [dict(pt) for _ in range(9)], "aod": [dict(aod) for _ in range(9)]}}},
            {"fingerprint": f"FP{tag[0]:06d}", "partial": False, "missing": []})
    return _fetch


async def main():
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    hass = HomeAssistant()
    c = HuoshaoyunCoordinator(hass, ConfigEntry(
        entry_id="m", data={"name": "内存体检", "latitude": 0.0, "longitude": 0.0}))
    c._now = lambda: datetime(2026, 10, 10, 13, 46)
    c.models = ("gfs_global",)
    orig = fetcher.async_fetch_transects
    fetcher.async_fetch_transects = fake_fetch_factory()

    print("=" * 66)
    print(f"轮数: {cycles}（每轮 = 取数 + 计算 + 装配 + 冻结 + 排程）")
    print("=" * 66)

    tracemalloc.start()
    base_objs = Counter(type(o).__name__ for o in gc.get_objects())
    rss0 = rss_kb()

    # 单轮峰值
    tracemalloc.reset_peak()
    await c._async_update_data()
    c.data = c.data
    peak_one, _ = tracemalloc.get_traced_memory()
    print(f"\n[1] 单轮刷新的瞬时峰值分配: {peak_one/1024/1024:.2f} MB")

    # 多轮(每 25 轮采样一次 RSS, 判断增长是线性(泄漏)还是平台化(预热))
    marks = []
    for i in range(1, cycles + 1):
        await c._async_update_data()
        if i % 25 == 0 or i == 1:
            gc.collect()
            marks.append((i, rss_kb()))
    print(f"\n[2a] RSS 曲线(每 25 轮)")
    for i, r in marks:
        print(f"     {i:>4} 轮: {r/1024:>6.1f} MB")
    gc.collect()
    rss1 = rss_kb()
    objs1 = Counter(type(o).__name__ for o in gc.get_objects())

    print(f"\n[2] {cycles} 轮刷新后进程 RSS")
    print(f"    前 {rss0/1024:.1f} MB -> 后 {rss1/1024:.1f} MB   "
          f"增量 {(rss1-rss0)/1024:.2f} MB  （{(rss1-rss0)/cycles:.1f} KB/轮）")

    print("\n[3] 常驻对象深尺寸")
    print(f"    coordinator.data        : {deep_size(c.data)/1024:.1f} KB")
    print(f"    _snapshots(事前快照)    : {deep_size(c._snapshots)/1024:.1f} KB  "
          f"({len(c._snapshots)} 条)")
    ents = [S.EventQualitySensor(c, e) for e in H.EVENTS] + [S.AodSensor(c), S.CloudLayerSensor(c)]
    attr_total = 0
    for e in ents:
        a = e.extra_state_attributes
        sz = deep_size(a)
        attr_total += sz
    print(f"    6 个实体的属性字典合计  : {attr_total/1024:.1f} KB")
    print(f"    ==> 集成常驻合计约      : "
          f"{(deep_size(c.data)+deep_size(c._snapshots))/1024:.1f} KB"
          f"（+ 属性约 {attr_total/1024:.1f} KB，每次读取时新建、由 HA 持有最新一份）")

    # 增长最多的对象类型
    print("\n[4] 对象数量增长最多的类型(前 8)")
    diff = [(k, objs1[k] - base_objs.get(k, 0)) for k in objs1]
    for name, d in sorted(diff, key=lambda x: -x[1])[:8]:
        if d > 0:
            print(f"    {name:<20} +{d}")

    fetcher.async_fetch_transects = orig
    tracemalloc.stop()
    print("\n结论: RSS 增量 / 轮数 ≈ 上面 [2] 的每轮值；若接近 0 则不存在累积泄漏。")

if __name__ == "__main__":
    asyncio.run(main())
