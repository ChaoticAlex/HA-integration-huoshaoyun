import argparse, asyncio, os, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))          # dev/
ROOT = os.path.dirname(HERE)                                # 仓库根
sys.path.insert(0, os.path.join(HERE, "_teststub"))         # 离线桩(HA 环境下会被真包覆盖)
sys.path.insert(0, ROOT)

import aiohttp                                              # noqa: E402
from custom_components.huoshaoyun import hsy_core as H      # noqa: E402


def cli(desc):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--lat", type=float, required=True, help="纬度(必填, 仓库内不含任何地点)")
    ap.add_argument("--lon", type=float, required=True, help="经度(必填)")
    ap.add_argument("--name", default="测试点", help="地点名称")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="基准日期")
    ap.add_argument("--tz", default="Asia/Shanghai")
    ap.add_argument("--tz-hours", type=float, default=8.0)
    ap.add_argument("--models", default="gfs_global,ecmwf_ifs025")
    return ap.parse_args()


async def main():
    a = cli("算法 + 取数链路自检")
    from custom_components.huoshaoyun import fetcher
    base = datetime.strptime(a.date, "%Y-%m-%d")
    models = tuple(a.models.split(","))
    async with aiohttp.ClientSession(trust_env=False) as s:
        trans, meta = await fetcher.async_fetch_transects(
            s, a.lat, a.lon, a.tz, a.tz_hours, base, models)
    print(f"数据指纹 {meta['fingerprint']} | 数据完整 {not meta['partial']} | 缺失 {meta['missing']}")
    for m in models:
        res = H.analyse_all(a.lat, a.lon, base, a.tz_hours, trans[m])
        print(f"\n── {H.MODEL_LABEL.get(m, m)}")
        for k, v in res["_sun"].items():
            print(f"   {H.EVENT_CN[k]} {v['time']} 方位 {v['azimuth']}°")
        for ev in H.EVENTS:
            variants = res["_events"].get(ev)
            if not variants:
                continue
            d = variants["mean"]
            print(f"   {H.EVENT_CN[ev]}: 主值 {d['quality']:.3f} "
                  f"(诊断 {variants['diag']['quality']:.3f} / 廓线 {variants['rh']['quality']:.3f}) "
                  f"峰值 {d['peak']:.3f}@{d['peak_time']} 窗口 {d['window']} AOD {d['aod']}")
            print(f"      {d['layers']}")


if __name__ == "__main__":
    asyncio.run(main())
