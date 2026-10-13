"""homeassistant.core 最小桩。"""
from types import SimpleNamespace


class _Config:
    def __init__(self):
        self.time_zone = "Asia/Shanghai"
        self.latitude = 0.0
        self.longitude = 0.0


class _States:
    def __init__(self):
        self._entities = {}

    def add(self, entity_id, name, attributes):
        self._entities[entity_id] = SimpleNamespace(name=name, attributes=attributes)

    def async_entity_ids(self, domain=None):
        return [e for e in self._entities
                if domain is None or e.startswith(domain + ".")]

    def get(self, entity_id):
        return self._entities.get(entity_id)


class HomeAssistant:
    def __init__(self):
        self.config = _Config()
        self.data = {}
        self.states = _States()
        self._session = None

    async def async_add_executor_job(self, func, *args):
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)


def callback(func):
    """HA 的 @callback 只表示"这是线程安全的回调", 桩里直接返回原函数。"""
    return func
