from typing import Generic, TypeVar
T = TypeVar("T")
class UpdateFailed(Exception):
    pass
class DataUpdateCoordinator(Generic[T]):
    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass=hass; self.logger=logger; self.name=name
        self.update_interval=update_interval; self.data=None
    async def async_config_entry_first_refresh(self):
        self.data=await self._async_update_data()
    async def async_request_refresh(self):
        self.data=await self._async_update_data()
class CoordinatorEntity(Generic[T]):
    def __init__(self, coordinator):
        self.coordinator=coordinator
    @property
    def available(self): return True
