<!-- 本文件由 scripts/gen_docs.py 生成，请勿手改；改代码后运行 `make docs-gen`。 -->

# 四张服务面

- CLI 叶子命令：39 条
- HTTP 路由：12 条
- WS / JSON-RPC 方法：13 个
- MCP 工具：10 个

## WS / JSON-RPC 方法

- `bars`
- `list`
- `minute`
- `query`
- `quotes`
- `runtime.capabilities`
- `runtime.health`
- `security.count`
- `security.list`
- `snapshot`
- `subscribe`
- `trades`
- `unsubscribe`

## HTTP 路由

- `GET /v13/bars/{symbol}`
- `GET /v13/capabilities`
- `GET /v13/minute/{symbol}`
- `GET /v13/quotes`
- `GET /v13/runtime/health`
- `GET /v13/security/count`
- `GET /v13/security/list`
- `GET /v13/snapshot/{symbol}`
- `GET /v13/trades/{symbol}`
- `GET /v13/universe`
- `GET /v13/universe/{kind}`
- `POST /v13/query/{capability}`

## MCP 工具

- `capabilities`
- `query_capability`
- `get_bars`
- `get_quote`
- `get_quotes`
- `get_snapshot`
- `get_minute_today`
- `get_trades`
- `get_security_count`
- `get_security_list`
