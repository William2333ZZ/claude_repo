"""MCP harness 协议层测试:不起 HTTP 服务,把 ApiClient.call 打桩。"""
import json

from datafoundry.mcp_server import TOOLS, ApiClient, handle_request


class FakeClient(ApiClient):
    def __init__(self):
        super().__init__(base_url="http://fake", api_key="dfk_test")
        self.calls = []

    def call(self, method, path, body=None, content_type="application/json"):
        self.calls.append((method, path))
        if path == "/health":
            return {"ok": True}
        if path == "/auth/me":
            return {"username": "root", "role": "admin"}
        if path == "/ops":
            return [{"name": "length_filter"}]
        raise RuntimeError(f"unexpected {path}")


def test_initialize_and_tools_list():
    c = FakeClient()
    resp = handle_request(c, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert resp["result"]["serverInfo"]["name"] == "datafoundry"
    assert resp["result"]["protocolVersion"] == "2025-06-18"

    assert handle_request(c, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    resp = handle_request(c, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {"df_status", "df_list_operators", "df_run_pipeline", "df_get_run"} <= names
    for tool in TOOLS:
        assert tool["inputSchema"]["type"] == "object"


def test_tools_call_dispatch_and_error():
    c = FakeClient()
    resp = handle_request(
        c, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "df_status", "arguments": {}}}
    )
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["me"]["username"] == "root"

    resp = handle_request(
        c, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "df_nope", "arguments": {}}}
    )
    assert resp["result"]["isError"] is True

    resp = handle_request(c, {"jsonrpc": "2.0", "id": 5, "method": "unknown/method"})
    assert resp["error"]["code"] == -32601
