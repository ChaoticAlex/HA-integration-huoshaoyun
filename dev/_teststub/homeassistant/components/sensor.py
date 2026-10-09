class SensorStateClass:
    MEASUREMENT="measurement"
class SensorEntity:
    _attr_has_entity_name=False
    @property
    def available(self): return True
