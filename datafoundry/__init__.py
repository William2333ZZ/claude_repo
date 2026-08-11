"""DataFoundry:自研数据精炼平台。

框架层零依赖(不依赖 Data-Juicer / DataFlow / NeMo),只依赖底座(FastAPI/uvicorn)。
核心组件:
- schema:统一样本模式与逐样本血缘(op_trace)
- registry/ops:自研算子引擎(启发式/模型/LLM 三档成本标签)
- pipeline:漏斗编译器(按成本重排)与成本预估
- runner:执行器,产出 manifest/rejects/血缘
- server:带 RBAC 的 HTTP API(用户/API Key/审计)
- mcp_server:给 Claude Code 用的 MCP harness(HTTP API 的薄客户端)
"""

__version__ = "0.1.0"
