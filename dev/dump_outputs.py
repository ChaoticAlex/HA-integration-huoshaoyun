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


import json                                                 # noqa: E402
from homeassistant.config_entries import ConfigEntry        # noqa: E402
from homeassistant.core import HomeAssistant                # noqa: E402
from custom_components.huoshaoyun import sensor as S        # noqa: E402
from custom_components.huoshaoyun.coordinator import HuoshaoyunCoordinator  # noqa: E402


async def main():
    a = cli("导出某地点当前对外输出的完整清单(实体 + 全部属性)")
    async with aiohttp.ClientSession(trust_env=False) as sess:
        hass = HomeAssistant(); hass._session = sess
        entry = ConfigEntry(entry_id="dump", data={"name": a.name, "latitude": a.lat,
                                                   "longitude": a.lon})
        c = HuoshaoyunCoordinator(hass, entry)
        await c.async_config_entry_first_refresh()
        ents = [S.EventQualitySensor(c, ev) for ev in H.EVENTS] + [S.AodSensor(c), S.CloudLayerSensor(c)]
        out = {"update_interval_min": int(c.update_interval.total_seconds() // 60),
               "entities": [{"entity": f"{a.name} {e._attr_name}", "state": e.native_value,
                             "attributes": e.extra_state_attributes} for e in ents]}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
