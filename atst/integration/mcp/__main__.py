# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``python -m atst.integration.mcp``：在 stdio 上起 v13 MCP JSON-RPC 服务。

MCP 客户端的配置只会拼模块名（``python -m atst.integration.mcp``），不会去猜包内私有
模块，所以这条入口是 MCP 面唯一的拉起方式。纯标准库，无需 extra。
"""

from __future__ import annotations

if __name__ == "__main__":  # pragma: no cover
    from ._server import create_mcp_server

    create_mcp_server().serve()
