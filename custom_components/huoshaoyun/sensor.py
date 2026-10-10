"""传感器实体 —— 只输出纯数值, 不做任何评级/判断。

每个地点 4 个实体: 今日朝霞 / 今日晚霞 / 明日朝霞 / 明日晚霞
state = 鲜艳度纯数值(0~2.5); 原始明细(时间/云层/AOD/各模式数值)放属性里。
"什么算值得拍"由 HA 自动化自己用阈值判断。
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import hsy_core as H
from .const import DOMAIN, SRC_LABEL
from .coordinator import HuoshaoyunCoordinator

EVENT_ICON = {"rise_1": "mdi:weather-sunset-up", "rise_2": "mdi:weather-sunset-up",
              "set_1": "mdi:weather-sunset-down", "set_2": "mdi:weather-sunset-down"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HuoshaoyunCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [EventQualitySensor(coordinator, e) for e in H.EVENTS]
    entities += [AodSensor(coordinator), CloudLayerSensor(coordinator)]
    async_add_entities(entities)


class EventQualitySensor(CoordinatorEntity[HuoshaoyunCoordinator], SensorEntity):
    """单个事件(朝霞/晚霞)的鲜艳度 —— 纯数值"""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HuoshaoyunCoordinator, event: str) -> None:
        super().__init__(coordinator)
        self._event = event
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{event}"
        self._attr_name = f"{H.EVENT_CN[event]}指数"   # 明确是复合指数, 不是物理量测量
        self._attr_icon = EVENT_ICON.get(event, "mdi:weather-sunset")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.site_name,
            manufacturer="huoshaoyun (复刻自 sunsetbot.top 公开因子清单)",
            model="火烧云/朝霞晚霞预报",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _info(self) -> dict | None:
        return (self.coordinator.data or {}).get("events", {}).get(self._event)

    @property
    def available(self) -> bool:
        return super().available and self._info is not None

    @property
    def native_value(self) -> float | None:
        info = self._info
        return None if info is None else info["quality"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """原始数值 + 口径自述, 不含任何评级/推荐字样。

        键名与顺序稳定, 便于写模板; 缺失值一律 null(不造假值)。
        """
        info = self._info
        if not info:
            return {}
        c = self.coordinator.data or {}
        return {
            # --- 时间(自动化最常用) ---
            "纬度": round(self.coordinator.lat, 4),
            "经度": round(self.coordinator.lon, 4),
            "事件时间": info["event_time"],
            "距今小时": info.get("in_hours"),        # 负值=已过
            "已过": not info.get("is_future", True),
            "太阳方位角": info["azimuth"],            # 朝霞/晚霞该往哪个方向看
            # --- 结果 ---
            "峰值": info["peak"],
            "峰值时刻": info["peak_time"],
            "有效时段": info["window"],
            "持续分钟": info["duration_min"],
            # --- 物理因子 ---
            "气溶胶光学厚度": info["aod"],            # 缺失=null(该次按中性0.2计算)
            "云层": info["layers"],
            "云量来源": SRC_LABEL.get(info["amount_src"], info["amount_src"]),
            "各模式数值": info["per_model"],
            "时间序列": ", ".join(f"{t}:{s:.2f}" for t, s, _ in info["series"] if s > 0.005) or "无",
            # --- 数据状态(判断"数字为什么没变") ---
            "数据指纹": c.get("fingerprint"),
            "数据变更": c.get("data_changed"),
            "数据完整": not c.get("partial", False),   # false=有模型层/AOD 没取到, 数值置信度较低
            "缺失数据": c.get("missing") or None,
            "数据更新时刻": c.get("updated").strftime("%Y-%m-%d %H:%M") if c.get("updated") else None,
            # --- 事件冻结(复盘用) ---
            "冻结于": info.get("冻结于"),        # 非空=该事件已过, 数值已冻结在"最后一次事前预报"
            "事后重算": info.get("事后重算", False),  # true=事件已过但没有事前快照, 此值是事后回算的
            "口径": ("鲜艳度 0~2.5, 越高越鲜艳; 双模式取均值; "
                    "AOD 缺失时按中性 0.2 计算(此时「气溶胶光学厚度」为 null); "
                    "太阳高度角>0 不计分; 云量口径见「云量来源」"),
        }


class _NextBasedSensor(CoordinatorEntity[HuoshaoyunCoordinator], SensorEntity):
    """以"下一次霞光"(内部用于自适应刷新)为参照的诊断实体。

    state 仍是纯数值/纯文本, 不含任何评断; "参照的是哪一次"写在 `对应事件` 属性里。
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: HuoshaoyunCoordinator, suffix: str, name: str,
                 icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{suffix}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.site_name,
            manufacturer="huoshaoyun (复刻自 sunsetbot.top 公开因子清单)",
            model="火烧云/朝霞晚霞预报",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _nxt(self) -> dict | None:
        return (self.coordinator.data or {}).get("next")

    @property
    def available(self) -> bool:
        return super().available and self._nxt is not None

    def _base_attrs(self) -> dict[str, Any]:
        n = self._nxt or {}
        return {"纬度": round(self.coordinator.lat, 4),
                "经度": round(self.coordinator.lon, 4),
                "对应事件": n.get("name"),
                "事件时间": n.get("time")}


class AodSensor(_NextBasedSensor):
    """**下一次霞光**光路上的 CAMS 气溶胶光学厚度预报(非实时观测; 取不到为未知)"""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HuoshaoyunCoordinator) -> None:
        super().__init__(coordinator, "aod", "下次霞光气溶胶", "mdi:blur")

    @property
    def native_value(self) -> float | None:
        n = self._nxt
        return None if not n else n.get("aod")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        events = (self.coordinator.data or {}).get("events", {})
        return {**self._base_attrs(),
                "各事件AOD": {H.EVENT_CN[k]: v["aod"] for k, v in events.items()},
                "来源": "CAMS (ECMWF 大气组分模式)",
                "含义": "数值越大天空越浑浊, 霞的饱和度与亮度越低"}


class CloudLayerSensor(_NextBasedSensor):
    """**下一次霞光**时刻的云层结构预报 —— 不是实时观测。

    state = 云层类别标记: none / low / mid / high / 组合如 low+mid (云量>=0.10 者)
    各类别的云量/云底/云顶/温度/相态拆成扁平属性, 直接可写模板。
    """

    def __init__(self, coordinator: HuoshaoyunCoordinator) -> None:
        super().__init__(coordinator, "clouds", "下次霞光云层", "mdi:cloud-outline")

    @property
    def _cloud(self) -> dict | None:
        n = self._nxt
        return (n or {}).get("cloud")

    @property
    def native_value(self) -> str | None:
        c = self._cloud
        if not c:
            return None
        return "+".join(c["present"]) if c["present"] else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self._cloud
        if not c:
            return {**self._base_attrs()}
        events = (self.coordinator.data or {}).get("events", {})
        attrs: dict[str, Any] = {
            **self._base_attrs(),
            "主导云层": c["dominant"],               # low/mid/high, 贡献最大者
            "主导云底_m": c["dominant_base_m"],       # 无湿层则为 null
            "各事件标记": {H.EVENT_CN[k]: ("+".join(v["cloud"]["present"])
                                       if v.get("cloud", {}).get("present") else "none")
                       for k, v in events.items()},
        }
        for cls, cn in (("low", "低云"), ("mid", "中云"), ("high", "高云")):
            d = c["classes"][cls]
            attrs[f"{cn}_云量"] = d["云量"]            # 0~1
            attrs[f"{cn}_云底_m"] = d["云底_m"]         # null=该层无 RH>=78% 湿层
            attrs[f"{cn}_云顶_m"] = d["云顶_m"]
            attrs[f"{cn}_温度_C"] = d["温度_C"]
            attrs[f"{cn}_相态"] = d["相态"]             # ice / water / null
        attrs["文字描述"] = c["text"]
        attrs["标记口径"] = "类别 = 云量>=0.10 者, low+mid 这样连接; 无则 none"
        attrs["云量口径"] = "0~1; 高度/温度来自观察者上空 RH>=78% 湿层; 相态按 0℃ 线"
        return attrs
