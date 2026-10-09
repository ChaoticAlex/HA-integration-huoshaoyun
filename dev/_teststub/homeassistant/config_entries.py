class ConfigEntry:
    def __init__(self, entry_id="test", data=None, options=None):
        self.entry_id=entry_id; self.data=data or {}; self.options=options or {}
