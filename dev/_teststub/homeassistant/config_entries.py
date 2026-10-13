"""config_entries 最小桩(支持配置流走查)。"""


class ConfigEntry:
    def __init__(self, entry_id="test", data=None, options=None, title=""):
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
        self.title = title


class FlowHandler:
    def __init__(self):
        self.hass = None
        self._unique_id = None
        self.created = None
        self.form = None
        self.aborted = None

    async def async_set_unique_id(self, uid, *, raise_on_progress=True):
        self._unique_id = uid

    def _abort_if_unique_id_configured(self, updates=None, reload_on_update=True):
        return None

    def async_create_entry(self, *, title=None, data=None, options=None):
        self.created = {"title": title, "data": data, "options": options}
        return {"type": "create_entry", **self.created}

    def async_show_form(self, *, step_id, data_schema=None, errors=None,
                        description_placeholders=None, last_step=None):
        self.form = {"step_id": step_id, "schema": data_schema}
        return {"type": "form", "step_id": step_id, "data_schema": data_schema,
                "errors": errors}

    def async_abort(self, *, reason, description_placeholders=None):
        self.aborted = reason
        return {"type": "abort", "reason": reason}


class ConfigFlow(FlowHandler):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()      # 吸收 domain=... 这类类关键字参数

    def __init__(self):
        super().__init__()


class OptionsFlow(FlowHandler):
    def __init__(self):
        super().__init__()
        self.config_entry = None
