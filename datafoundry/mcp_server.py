"""给 Claude Code 用的 MCP harness(stdio,JSON-RPC 2.0,newline-delimited)。

设计原则:MCP 层是 HTTP API 的薄客户端——所有权限检查都在服务端统一执行,
Agent 拿到的能力严格等于其 API Key 对应用户的角色。

接入(Claude Code):
  claude mcp add datafoundry \
    --env DATAFOUNDRY_URL=http://127.0.0.1:8321 \
    --env DATAFOUNDRY_API_KEY=dfk_xxx \
    -- datafoundry mcp
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from datafoundry import __version__

PROTOCOL_VERSION = "2025-06-18"


class ApiClient:
    """HTTP API 薄客户端(标准库 urllib,零额外依赖)。

    地址与凭据的解析顺序(对齐 feishu-cli):显式参数 > 环境变量 > ~/.datafoundry/credentials.json > 默认值。
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        from datafoundry.credentials import load_credentials

        creds = load_credentials() or {}
        self.base_url = (
            base_url or os.environ.get("DATAFOUNDRY_URL") or creds.get("url") or "http://127.0.0.1:8321"
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("DATAFOUNDRY_API_KEY") or creds.get("api_key", "")

    def call(self, method: str, path: str, body: dict | str | None = None, content_type: str = "application/json"):
        data = None
        if body is not None:
            data = (json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": content_type, "X-API-Key": self.api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"无法连接 {self.base_url}: {exc.reason}。请先启动服务(datafoundry serve)并检查 DATAFOUNDRY_URL"
            ) from None


STEPS_SCHEMA = {
    "type": "array",
    "description": '流水线步骤,如 [{"op":"length_filter","params":{"min_len":30}},{"op":"exact_dedup"}]',
    "items": {
        "type": "object",
        "properties": {
            "op": {"type": "string"},
            "params": {"type": "object"},
            "pin": {"type": "boolean", "description": "true 则漏斗编译时保持原位"},
        },
        "required": ["op"],
    },
}

TOOLS: list[dict] = [
    {
        "name": "df_status",
        "description": "平台健康状态、当前身份与角色、可用执行引擎(native/datajuicer)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "df_list_operators",
        "description": "列出全部算子:名称、类别(filter/mapper/score/dedup/verify)、成本档(heuristic/model/llm)、参数与默认值",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "df_list_datasets",
        "description": "列出已登记的数据集(id、名称、样本数)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "df_upload_dataset",
        "description": "上传 JSONL 文本为新数据集(每行一个 JSON 对象,文本字段名 text)。需要 engineer 角色",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "jsonl_text": {"type": "string", "description": "JSONL 内容"},
            },
            "required": ["name", "jsonl_text"],
        },
    },
    {
        "name": "df_inspect_dataset",
        "description": "查看数据集前 n 条样本(含 stats/trace)",
        "inputSchema": {
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}, "n": {"type": "integer", "default": 5}},
            "required": ["dataset_id"],
        },
    },
    {
        "name": "df_list_recipes",
        "description": "列出命名配方(简单算子的组合+实验证据出处);传 name 看完整定义。配方可直接作为 df_run_pipeline 的 recipe_name",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "配方名;不传则列出全部"}},
        },
    },
    {
        "name": "df_recipe_history",
        "description": "配方效果档案:该配方跨数据集的历史 run(计数/成本/评测分/hash 演化)——同配方在哪些数据上跑出过什么效果",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "配方名(见 df_list_recipes)"}},
            "required": ["name"],
        },
    },
    {
        "name": "df_estimate_pipeline",
        "description": "流水线成本预估:漏斗重排后的顺序、朴素 vs 漏斗成本对比、预期留存。先估后跑",
        "inputSchema": {
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}, "steps": STEPS_SCHEMA},
            "required": ["dataset_id", "steps"],
        },
    },
    {
        "name": "df_run_pipeline",
        "description": "执行流水线(默认 native 引擎+漏斗编排;steps 手写或 recipe_name 用命名配方;engine=datajuicer 时传 recipe 跑 DJ 配方)。需要 engineer 角色。默认等待完成并返回 manifest",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "steps": STEPS_SCHEMA,
                "recipe_name": {"type": "string", "description": "命名配方(见 df_list_recipes),与 steps 二选一"},
                "name": {"type": "string", "default": "run"},
                "engine": {"type": "string", "enum": ["native", "datajuicer"], "default": "native"},
                "recipe": {"type": "object", "description": "engine=datajuicer 时的 DJ 配方 {process:[...]}"},
                "wait_seconds": {"type": "integer", "default": 120},
            },
            "required": ["dataset_id"],
        },
    },
    {
        "name": "df_get_run",
        "description": "查看运行状态与 manifest(每算子进出/留存/耗时/成本),附 rejects 死因抽样",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "rejects_n": {"type": "integer", "default": 5}},
            "required": ["run_id"],
        },
    },
]


def dispatch_tool(client: ApiClient, name: str, args: dict):
    if name == "df_status":
        health = client.call("GET", "/health")
        me = client.call("GET", "/auth/me")
        return {"health": health, "me": me}
    if name == "df_list_operators":
        return client.call("GET", "/ops")
    if name == "df_list_datasets":
        return client.call("GET", "/datasets")
    if name == "df_upload_dataset":
        q = urllib.parse.quote(args["name"])
        return client.call("POST", f"/datasets/upload?name={q}", body=args["jsonl_text"], content_type="text/plain")
    if name == "df_inspect_dataset":
        return client.call("GET", f"/datasets/{args['dataset_id']}/head?n={int(args.get('n', 5))}")
    if name == "df_list_recipes":
        if args.get("name"):
            return client.call("GET", f"/recipes/{urllib.parse.quote(args['name'])}")
        return client.call("GET", "/recipes")
    if name == "df_recipe_history":
        return client.call("GET", f"/recipes/{urllib.parse.quote(args['name'])}/history")
    if name == "df_estimate_pipeline":
        return client.call("POST", "/pipelines/estimate", body={"dataset_id": args["dataset_id"], "steps": args["steps"]})
    if name == "df_run_pipeline":
        body = {
            "name": args.get("name", "run"),
            "dataset_id": args["dataset_id"],
            "engine": args.get("engine", "native"),
            "steps": args.get("steps", []),
            "recipe": args.get("recipe"),
            "recipe_name": args.get("recipe_name"),
        }
        run = client.call("POST", "/runs", body=body)
        deadline = time.time() + int(args.get("wait_seconds", 120))
        while time.time() < deadline:
            info = client.call("GET", f"/runs/{run['id']}")
            if info["status"] in ("succeeded", "failed"):
                return info
            time.sleep(1)
        return {"id": run["id"], "status": "running", "note": "仍在运行,用 df_get_run 查询"}
    if name == "df_get_run":
        info = client.call("GET", f"/runs/{args['run_id']}")
        try:
            rejects = client.call("GET", f"/runs/{args['run_id']}/rejects?n={int(args.get('rejects_n', 5))}")
            info["rejects_preview"] = rejects["rejects"]
        except RuntimeError:
            pass
        return info
    raise ValueError(f"未知工具: {name}")


def handle_request(client: ApiClient, req: dict) -> dict | None:
    """返回响应 dict;通知类消息返回 None。"""
    method, req_id = req.get("method"), req.get("id")
    if method == "initialize":
        client_version = (req.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "datafoundry", "version": __version__},
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        try:
            result = dispatch_tool(client, params.get("name", ""), params.get("arguments") or {})
            content = json.dumps(result, ensure_ascii=False, indent=1)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": content}], "isError": False},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"错误: {exc}"}], "isError": True},
            }
    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def serve_stdio() -> None:
    client = ApiClient()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(client, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
