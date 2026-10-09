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


from homeassistant.config_entries import ConfigEntry        # noqa: E402
from homeassistant.core import HomeAssistant                # noqa: E402
from custom_components.huoshaoyun import sensor as S        # noqa: E402
from custom_components.huoshaoyun.coordinator import HuoshaoyunCoordinator  # noqa: E402


async def main():
    a = cli("coordinator + 实体装配自检(含指纹缓存验证)")
    async with aiohttp.ClientSession(trust_env=False) as sess:
        hass = HomeAssistant(); hass._session = sess
        entry = ConfigEntry(entry_id="dev", data={"name": a.name, "latitude": a.lat,
                                                  "longitude": a.lon})
        c = HuoshaoyunCoordinator(hass, entry)
        await c.async_config_entry_first_refresh()
        d = c.data
        q1, fp1 = d["events"].get("set_1", {}).get("quality"), d["fingerprint"]
        print(f"指纹 {fp1} | 数据变更 {d['data_changed']} | 数据完整 {not d['partial']} "
              f"| 缺失 {d['missing']} | 刷新间隔 {c.update_interval}")
        await c.async_request_refresh()
        d2 = c.data
        print(f"第二次: 数据变更 {d2['data_changed']} | 指纹 {d2['fingerprint']} "
              f"| 间隔 {c.update_interval} | set_1 {q1} -> "
              f"{d2['events'].get('set_1', {}).get('quality')}")
        print("\n实体:")
        for e in [S.EventQualitySensor(c, ev) for ev in H.EVENTS] + \
                 [S.AodSensor(c), S.CloudLayerSensor(c)]:
            print(f"  {e._attr_name:<14} available={e.available} value={e.native_value!r} "
                  f"属性 {len(e.extra_state_attributes)} 项")


if __name__ == "__main__":
    asyncio.run(main())
