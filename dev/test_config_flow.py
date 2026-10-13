"""配置流离线走查 —— 覆盖 4 条添加入口 + 选项流。

这是之前的测试盲区: config_flow.py 从未被执行过, 导致一个阻塞性 bug
(NumberSelector 的 step 用了 1e-6, 违反 HA 的 step >= 1e-3 约束) 一路发到线上。

用法: python3 test_config_flow.py
"""
import asyncio
import inspect
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_teststub"))
sys.path.insert(0, os.path.dirname(HERE))

from homeassistant.config_entries import ConfigEntry                      # noqa: E402
from homeassistant.core import HomeAssistant                              # noqa: E402

from custom_components.huoshaoyun import config_flow as CF                # noqa: E402
from custom_components.huoshaoyun.config_flow import (                    # noqa: E402
    HuoshaoyunConfigFlow, HuoshaoyunOptionsFlow)
from custom_components.huoshaoyun.const import (                          # noqa: E402
    CONF_AMOUNT_SRC, CONF_LATITUDE, CONF_LONGITUDE, CONF_MODELS, CONF_NAME,
    CONF_SLOT_MARGIN)

OK, BAD = "✅", "❌"
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {OK if ok else BAD} {label}: {got!r}" + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        fails.append(label)


async def call(label, fn, *a, **kw):
    """调用一步; 断言不抛异常(这正是线上 'Unknown error occurred' 的成因)"""
    try:
        r = fn(*a, **kw)
        if inspect.isawaitable(r):
            r = await r
    except Exception as err:                      # noqa: BLE001
        print(f"  {BAD} {label}: 抛异常 {type(err).__name__}: {err}")
        fails.append(label)
        return None
    print(f"  {OK} {label}")
    return r


def mk_hass(with_zone=True):
    hass = HomeAssistant()
    hass.config.latitude = 39.9042
    hass.config.longitude = 116.4074
    hass.config.time_zone = "Asia/Shanghai"
    if with_zone:
        hass.states.add("zone.home", "家", {"latitude": 39.9042, "longitude": 116.4074})
        hass.states.add("zone.office", "公司", {"latitude": 31.2304, "longitude": 121.4737})
        hass.states.add("zone.noloc", "无坐标", {})          # 缺经纬度, 应被跳过
    return hass


def new_flow(with_zone=True):
    f = HuoshaoyunConfigFlow()
    f.hass = mk_hass(with_zone)
    return f


async def main():
    print("\n[1] 入口菜单")
    f = new_flow()
    await call("显示入口表单", f.async_step_user, None)
    check("入口 step_id", (f.form or {}).get("step_id"), "user")

    print("\n[2] 路径 A: 使用 HA 家庭位置")
    f = new_flow()
    await call("选择 home -> 显示命名表单", f.async_step_user, {"source": "home"})
    check("home step_id", (f.form or {}).get("step_id"), "home")
    r = await call("提交命名", f.async_step_home, {CONF_NAME: "家"})
    check("建成条目", r and r["type"], "create_entry")
    cd = (f.created or {}).get("data", {})
    check("坐标取自 HA 配置", (cd.get(CONF_LATITUDE), cd.get(CONF_LONGITUDE)), (39.9042, 116.4074))
    check("unique_id", f._unique_id, "39.9042,116.4074")

    print("\n[3] 路径 B: 手动输入经纬度  ← 线上报 Unknown error occurred 的位置")
    f = new_flow()
    await call("选择 manual -> 显示坐标表单", f.async_step_user, {"source": "manual"})
    check("manual step_id", (f.form or {}).get("step_id"), "manual")
    r = await call("提交手动坐标(含 5 位小数)", f.async_step_manual,
                   {CONF_NAME: "测试点", CONF_LATITUDE: 23.12912, CONF_LONGITUDE: 113.26439})
    check("建成条目", r and r["type"], "create_entry")
    cd = (f.created or {}).get("data", {})
    check("坐标写入", (cd.get(CONF_LATITUDE), cd.get(CONF_LONGITUDE)), (23.12912, 113.26439))
    check("unique_id 取 4 位", f._unique_id, "23.1291,113.2644")

    print("\n[4] 路径 C: 从 zone 选择")
    f = new_flow()
    await call("选择 zone -> 显示区域表单", f.async_step_user, {"source": "zone"})
    check("zone step_id", (f.form or {}).get("step_id"), "zone")
    await call("选中 zone.office", f.async_step_zone, {"zone": "zone.office"})
    check("进入命名步骤", (f.form or {}).get("step_id"), "zone_name")
    await call("提交命名", f.async_step_zone_name, {CONF_NAME: "公司"})
    cd = (f.created or {}).get("data", {})
    check("坐标取自 zone", (cd.get(CONF_LATITUDE), cd.get(CONF_LONGITUDE)), (31.2304, 121.4737))

    print("\n[5] 路径 C 边界: 没有任何可用 zone")
    f = new_flow(with_zone=False)
    await call("无 zone 时 -> 中止而非报错", f.async_step_zone, None)
    check("中止原因", f.aborted, "no_zone")

    print("\n[6] 选项流")
    of = HuoshaoyunOptionsFlow()
    of.hass = mk_hass()
    of.config_entry = ConfigEntry(entry_id="e1", data={CONF_NAME: "旧名",
                                                      CONF_LATITUDE: 23.1291,
                                                      CONF_LONGITUDE: 113.2644})
    await call("显示选项表单", of.async_step_init, None)
    check("选项 step_id", (of.form or {}).get("step_id"), "init")
    submitted = {CONF_NAME: "新名", CONF_LATITUDE: 23.5, CONF_LONGITUDE: 113.5,
                 CONF_MODELS: "both", CONF_AMOUNT_SRC: "mean", CONF_SLOT_MARGIN: 15}
    r = await call("提交选项", of.async_step_init, submitted)
    check("选项保存", r and r["type"], "create_entry")

    print("\n[7] 静态检查: 所有 step 参数 >= 0.001 (或 'any')")
    src = inspect.getsource(CF)
    bad = [m for m in re.findall(r"step=([0-9.eE+-]+)", src) if float(m) < 1e-3]
    check("无 < 0.001 的 step", bad, [])

    print("\n" + "=" * 62)
    print(f"{OK} 全部通过" if not fails else f"{BAD} 失败项: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
