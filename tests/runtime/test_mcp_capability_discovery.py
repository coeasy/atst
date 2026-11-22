# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""MCP 必须有与 CLI/HTTP/WS 同源的能力发现口（2026-11-22 四面前后端贯通）。

此前 MCP 只能通过 ``tools/list`` 看到 9 个固定工具名，而 ``query_capability`` 声称
能执行"任意已迁移能力"——模型侧无法枚举那 217 个名字，四张服务面里唯一一张缺
能力目录。本测试钉住新增的 ``capabilities`` 工具确实返回与 Client 相同的数据。
"""

from __future__ import annotations

import json

import pytest

from atst.client.api import Client
from atst.server.mcp._server import MCPServer
from atst.server.mcp._tools_spec import TOOLS

pytestmark = pytest.mark.unit


def test_tools_roster_exposes_capabilities_discovery() -> None:
    assert "capabilities" in {tool.name for tool in TOOLS}


def test_capabilities_tool_returns_same_names_as_client() -> None:
    client = Client()
    server = MCPServer(client)
    reply = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "capabilities", "arguments": {}},
        }
    )
    assert reply is not None and "error" not in reply, reply
    text = reply["result"]["content"][0]["text"]
    data = json.loads(text)
    assert data["capabilities"] == list(client.capabilities())
    assert data["providers"], "能力出处不能为空"


def test_capabilities_tool_supports_name_filter() -> None:
    server = MCPServer(Client())
    reply = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "capabilities", "arguments": {"query": "bars"}},
        }
    )
    assert reply is not None and "error" not in reply, reply
    data = json.loads(reply["result"]["content"][0]["text"])
    assert data["capabilities"]
    assert all("bars" in name for name in data["capabilities"])
