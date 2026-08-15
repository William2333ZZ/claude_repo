"""DataFoundry:自研数据精炼平台(v0.2 拆包:域即目录,包边界=部署边界)。

- kernel/    精炼内核:schema/算子引擎/漏斗编译器/执行器/配方/证据包——**零第三方依赖**,
             `pip install datafoundry` 即得,可嵌入/端侧/私有化直用(docs/21 §7,#27 载体①)
- service/   服务壳:RBAC HTTP API/存储双后端/计费/限流——`pip install datafoundry[server]`(+pg/billing-*)
- interface/ 薄客户端:CLI 与 MCP harness,经 HTTP 窄腰访问服务
"""

__version__ = "0.2.0"
