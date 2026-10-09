class _Config:
    def __init__(self):
        self.time_zone="Asia/Shanghai"; self.latitude=0.0; self.longitude=0.0
class _States:
    def async_entity_ids(self, domain=None): return []
    def get(self, entity_id): return None
class HomeAssistant:
    def __init__(self):
        self.config=_Config(); self.data={}; self.states=_States(); self._session=None
    async def async_add_executor_job(self, func, *args):
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)
