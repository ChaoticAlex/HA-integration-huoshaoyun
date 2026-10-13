"""selector 最小桩。**故意镜像 HA 的真实约束**，让离线测试能抓住非法参数。

镜像来源: homeassistant/helpers/selector.py → NumberSelector.CONFIG_SCHEMA
    Optional("step", default=1): Any(
        "any",
        All(Coerce(float), Range(min=1e-3)),     # ← step 必须 "any" 或 >= 0.001
    )
该校验在 2025.7.0 ~ 2026.10.0 各版本一致(只是校验库由 voluptuous 换成 probatio)。
构造 NumberSelector 时即触发, 非法 step 会直接抛异常 —— 表现为前端"Unknown error occurred"。
"""


class Config:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        for k, v in kw.items():
            setattr(self, k, v)


def NumberSelectorConfig(**kw):
    step = kw.get("step")
    if step is not None and step != "any":
        # 与 HA 一致: 必须 >= 1e-3
        if float(step) < 1e-3:
            raise ValueError(
                f"NumberSelector step must be 'any' or >= 0.001, got {step!r} "
                "(mirrors homeassistant.helpers.selector NumberSelector.CONFIG_SCHEMA)")
    return Config(**kw)


class NumberSelectorMode:
    BOX = "box"
    SLIDER = "slider"


class SelectSelectorMode:
    LIST = "list"
    DROPDOWN = "dropdown"


class TextSelectorType:
    TEXT = "text"
    PASSWORD = "password"


class SelectOptionDict(dict):
    def __init__(self, value, label):
        super().__init__(value=value, label=label)
        self.value = value
        self.label = label


class _Sel:
    def __init__(self, config=None, **kw):
        self.config = config


def NumberSelector(config=None):
    return _Sel(config)


def TextSelector(config=None):
    return _Sel(config)


def SelectSelector(config=None):
    return _Sel(config)


def EntitySelector(config=None):
    return _Sel(config)


def TextSelectorConfig(**kw):
    return Config(**kw)


def SelectSelectorConfig(**kw):
    if "options" in kw:
        for o in kw["options"]:
            if not isinstance(o, SelectOptionDict):
                raise ValueError("SelectSelectorConfig options must be SelectOptionDict")
    return Config(**kw)


def EntitySelectorConfig(**kw):
    return Config(**kw)
