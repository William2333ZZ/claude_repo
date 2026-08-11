"""执行引擎层:native(自研)之外,把开源项目当引擎接入。

依赖红线(docs/04):闭源组件零依赖;开源项目(Apache-2.0 等)可以依赖,
但必须走薄适配——引擎不可用时平台照常工作,并明确告知如何安装。
"""
from __future__ import annotations

from datafoundry.engines.datajuicer import DataJuicerEngine

ENGINES = {
    "datajuicer": DataJuicerEngine(),
}


def engine_status() -> dict:
    status = {"native": {"available": True, "note": "自研算子引擎(内置)"}}
    for name, eng in ENGINES.items():
        status[name] = eng.status()
    return status
