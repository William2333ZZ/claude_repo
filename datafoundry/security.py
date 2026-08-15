"""旧路径兼容 shim(v0.2 拆包,docs/21 §7):实体在 datafoundry.service.security。"""
import sys

from datafoundry.service import security as _m

sys.modules[__name__] = _m
