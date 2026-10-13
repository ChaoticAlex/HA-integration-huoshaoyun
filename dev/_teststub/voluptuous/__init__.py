"""voluptuous 最小桩(仅覆盖本集成配置流用到的 API)。"""


class Invalid(Exception):
    pass


class MultipleInvalid(Invalid):
    pass


class _Mark:
    def __init__(self, key, default=None):
        self.key = key
        self.default = default
        self.default_set = default is not None


class Required(_Mark):
    pass


class Optional(_Mark):
    pass


class Schema:
    def __init__(self, schema, **kw):
        self.schema = schema

    def __call__(self, data):
        if not isinstance(data, dict):
            raise MultipleInvalid("expected a mapping")
        out = {}
        missing = []
        for key, validator in self.schema.items():
            name = key.key if isinstance(key, _Mark) else key
            if name in data:
                out[name] = validator(data[name]) if callable(validator) else data[name]
            elif isinstance(key, Required) and not key.default_set:
                missing.append(name)
            elif isinstance(key, _Mark) and key.default_set:
                out[name] = key.default
        if missing:
            raise MultipleInvalid(f"missing: {missing}")
        return out


class All:
    def __init__(self, *vs):
        self.vs = vs

    def __call__(self, v):
        for x in self.vs:
            v = x(v)
        return v


def Coerce(t):
    return lambda v: t(v)


def Range(min=None, max=None):
    def _r(v):
        if min is not None and v < min:
            raise Invalid("too small")
        if max is not None and v > max:
            raise Invalid("too large")
        return v
    return _r
