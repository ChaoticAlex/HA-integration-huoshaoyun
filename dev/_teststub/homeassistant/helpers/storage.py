"""离线自检用的最小 Store 桩(不落盘)"""


class Store:
    def __init__(self, hass, version, key, **kw):
        self.hass = hass
        self.version = version
        self.key = key
        self._data = None

    async def async_load(self):
        return self._data

    def async_delay_save(self, data_func, delay=0):
        self._data = data_func()

    async def async_save(self, data=None):
        self._data = data if data is not None else self._data

    async def async_remove(self):
        self._data = None
