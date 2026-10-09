"""配置流 —— 地点全部由 HA 指定: 家庭位置 / 手动经纬度 / 从 zone 选。

每个地点 = 一个 config entry, 可添加多个(多设备)。
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_AMOUNT_SRC, CONF_INTERVAL, CONF_LATITUDE, CONF_LONGITUDE,
                    CONF_API_KEY, CONF_MODELS, CONF_NAME, CONF_NEAR_INTERVAL,
                    CONF_TIMEZONE, DEFAULT_INTERVAL, DEFAULT_NEAR_INTERVAL,
                    DOMAIN, MODEL_CHOICES, SRC_CHOICES)

_HOME = "home"
_MANUAL = "manual"
_ZONE = "zone"


def _coord_schema(hass, name_default: str = "", include_name: bool = True) -> vol.Schema:
    fields = {
        vol.Required(CONF_LATITUDE, default=hass.config.latitude):
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=-90, max=90, step=0.000001, mode=selector.NumberSelectorMode.BOX)),
        vol.Required(CONF_LONGITUDE, default=hass.config.longitude):
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=-180, max=180, step=0.000001, mode=selector.NumberSelectorMode.BOX)),
    }
    if include_name:
        fields = {vol.Required(CONF_NAME, default=name_default): selector.TextSelector(), **fields}
    return vol.Schema(fields)


class HuoshaoyunConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._pending: dict = {}

    # 入口: 选择指定方式
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return await getattr(self, f"async_step_{user_input['source']}")()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("source", default=_HOME): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value=_HOME, label="使用 HA 家庭位置"),
                    selector.SelectOptionDict(value=_ZONE, label="从 HA 区域(zone)选择"),
                    selector.SelectOptionDict(value=_MANUAL, label="手动输入经纬度"),
                ], mode=selector.SelectSelectorMode.LIST))}),
        )

    async def async_step_home(self, user_input=None):
        if user_input is not None:
            return await self._finish(user_input[CONF_NAME], self.hass.config.latitude,
                                      self.hass.config.longitude)
        return self.async_show_form(step_id="home", data_schema=vol.Schema({
            vol.Required(CONF_NAME, default="家"): selector.TextSelector()}))

    async def async_step_manual(self, user_input=None):
        if user_input is not None:
            return await self._finish(user_input[CONF_NAME], user_input[CONF_LATITUDE],
                                      user_input[CONF_LONGITUDE])
        return self.async_show_form(step_id="manual", data_schema=_coord_schema(self.hass))

    async def async_step_zone(self, user_input=None):
        zones = []
        for entity_id in self.hass.states.async_entity_ids("zone"):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if "latitude" not in state.attributes or "longitude" not in state.attributes:
                continue
            label = state.name or entity_id
            zones.append(selector.SelectOptionDict(value=entity_id, label=label))
        if not zones:
            return self.async_abort(reason="no_zone")
        if user_input is not None:
            state = self.hass.states.get(user_input["zone"])
            if state is None:
                return self.async_abort(reason="no_zone")
            self._pending = {"name": state.name or "地点",
                             "lat": state.attributes["latitude"],
                             "lon": state.attributes["longitude"]}
            return await self.async_step_zone_name()
        return self.async_show_form(step_id="zone", data_schema=vol.Schema({
            vol.Required("zone"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="zone"))}))

    async def async_step_zone_name(self, user_input=None):
        if user_input is not None:
            return await self._finish(user_input[CONF_NAME], self._pending["lat"],
                                      self._pending["lon"])
        return self.async_show_form(step_id="zone_name", data_schema=vol.Schema({
            vol.Required(CONF_NAME, default=self._pending.get("name", "")): selector.TextSelector()}))

    async def _finish(self, name: str, lat: float, lon: float):
        uid = f"{float(lat):.4f},{float(lon):.4f}"
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"{name}", data={
            CONF_NAME: name, CONF_LATITUDE: float(lat), CONF_LONGITUDE: float(lon)})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HuoshaoyunOptionsFlow()


class HuoshaoyunOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opt = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_NAME, default=opt.get(CONF_NAME, "")): selector.TextSelector(),
            vol.Required(CONF_LATITUDE, default=float(opt.get(CONF_LATITUDE, self.hass.config.latitude))):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=-90, max=90, step=0.000001, mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_LONGITUDE, default=float(opt.get(CONF_LONGITUDE, self.hass.config.longitude))):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=-180, max=180, step=0.000001, mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_MODELS, default=opt.get(CONF_MODELS, "both")):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=k, label=v) for k, v in MODEL_CHOICES.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Required(CONF_AMOUNT_SRC, default=opt.get(CONF_AMOUNT_SRC, "mean")):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=k, label=v) for k, v in SRC_CHOICES.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Required(CONF_INTERVAL, default=int(opt.get(CONF_INTERVAL, DEFAULT_INTERVAL))):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=15, max=360, step=5, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min")),
            vol.Required(CONF_NEAR_INTERVAL, default=int(opt.get(CONF_NEAR_INTERVAL, DEFAULT_NEAR_INTERVAL))):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=5, max=120, step=5, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min")),
            vol.Optional(CONF_TIMEZONE, default=opt.get(CONF_TIMEZONE, self.hass.config.time_zone)):
                selector.TextSelector(),
            vol.Optional(CONF_API_KEY, default=opt.get(CONF_API_KEY, "")):
                selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
