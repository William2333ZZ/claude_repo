"""旧路径兼容 shim(v0.2 拆包,docs/21 §7):实体在 datafoundry.interface.credentials。"""
import sys

from datafoundry.interface import credentials as _m

sys.modules[__name__] = _m
