class _Cfg:
    def __init__(self, **kw): self.__dict__.update(kw)
def NumberSelectorConfig(**kw): return _Cfg(**kw)
class _Sel:
    def __init__(self, cfg=None, **kw): self.cfg=cfg
def NumberSelector(cfg=None, **kw): return _Sel(cfg)
def TextSelector(cfg=None, **kw): return _Sel(cfg)
def SelectSelector(cfg=None, **kw): return _Sel(cfg)
def SelectOptionDict(**kw): return kw
def EntitySelector(cfg=None, **kw): return _Sel(cfg)
class NumberSelectorMode: BOX="box"; SLIDER="slider"
class SelectSelectorMode: LIST="list"; DROPDOWN="dropdown"
