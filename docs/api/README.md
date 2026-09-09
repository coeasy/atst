# API 参考

> 本页描述当前 `1.4.0` Draft 开发线。最新已发布稳定版是 `v1.0.0`；稳定版历史接口与
> 验证结果见 [v1.0.0 发布说明](../releases/v1.0.0.md)。

当前 canonical runtime 是 **Provider-first + fail-closed**。兼容门面仍保留，但不得把
legacy router 的历史降级行为理解为新 Query runtime 的默认语义。

## Provider-first 核心入口

| 模块 | 说明 |
|---|---|
| `tstdx.query` | `QuerySpec` / `QueryPlan` / fingerprint 与查询语义 |
| `tstdx.service` | Provider-first service 执行入口 |
| `tstdx.async_service` | 异步 service 生命周期与 cancellation 边界 |
| `tstdx.providers` | canonical Provider / Channel / Capability registry |
| `tstdx.provider_api` | Direct Provider API；registry 驱动映射 |
| `tstdx.planned_service` | QueryPlan 到 exactly-one Provider/Channel 的执行层 |
| `tstdx.freshness` | current/historical freshness 契约 |
| `tstdx.semantic_cache` / `tstdx.cache_v2` | Provider/Channel/fingerprint 绑定缓存 |
| `tstdx.failure` | fail-closed failure policy |
| `tstdx.error_envelope` | 外部边界 canonical ErrorEnvelope |

核心约束：

```text
QuerySpec -> QueryPlan -> Provider -> Channel -> Capability -> Endpoint/Host
```

- 一个 QueryPlan 只执行一个 Provider。
- TDX 是默认 Provider；其它 Provider 必须显式选择。
- 禁止跨 Provider silent fallback。
- TDX host failover 只允许发生在 TDX Provider 内部。
- cache/replay/synthetic provenance 不得冒充 direct current data。

## TDX 兼容客户端

| 模块 | 说明 |
|---|---|
| `tstdx.client.TdxClient` | 同步 TDX 协议兼容入口 |
| `tstdx.client.AsyncTdxClient` | 异步 TDX 协议镜像入口 |
| `tstdx.client.get_client(kind)` | TDX family 工厂：std/goods/ex/mac/f10 |

这些入口仍支持既有协议 API；新跨 Provider 编排优先使用 Query/Direct Provider API。

## 兼容门面

| 模块 | 说明 |
|---|---|
| `tstdx.facade.api.UnifiedQuoteAPI` | 历史统一门面；Provider-first 新路径不依赖跨源自动降级 |
| `tstdx.facade.async_api.AsyncUnifiedQuoteAPI` | 历史异步门面 |
| `tstdx.facade.response.ApiResponse` | 兼容响应形态 |
| `tstdx.sources.router.DataSourceRouter` | legacy compatibility router；不是新 Query runtime 的 Provider fallback 机制 |
| `tstdx.web.facade.WebQuoteSession` | Web Provider/adapter 的兼容会话入口 |

## 协议层

| 模块 | 说明 |
|---|---|
| `tstdx.protocol.commands` | 5 个 TDX 协议族命令账本 |
| `tstdx.protocol.registry` | BaseParser + canonical dispatch |
| `tstdx.protocol.generic` | 启发式解析 + ProtocolSniffer |
| `tstdx.protocol.prober` | 未知命令受控探测 |
| `tstdx.protocol.parsers.*` | std7709/std7727/mac/f10/goods 解析器 |

## 传输层

| 模块 | 说明 |
|---|---|
| `tstdx.transport.hosts` | family-safe selector + STANDARD ranking provenance |
| `tstdx.transport.pool` | ConnectionPool generation/lease/circuit/host failover |
| `tstdx.transport.async_` | AsyncConnectionPool，同步 lifecycle/circuit 语义镜像 |
| `tstdx.transport.ratelimit` | 本地限流 |
| `tstdx.transport.speedtest` | probe RTT；与 live request health 分离 |
| `tstdx.transport.sniff` | 被动嗅探 + spec 草稿导出 |

连接池继承 v1.0.0 已发布的 generation/lease 生命周期修复，并在当前开发线继续强化：

- 在飞请求持有旧 generation lease，热更新不能切断它。
- 旧 generation 的迟到 success/failure 不能改写新 generation。
- HALF_OPEN 同一主站同时只允许一个 probe。
- `live_rtt_ms` 优先于 background `rtt_ms` 做当前进程排序。
- ranking 文件不能恢复 live-health 或 half-open token。
- F10/GOODS 可以复用物理 endpoint，但不能继承其它 family 的 verified provenance。

## 数据与落地

| 模块 | 说明 |
|---|---|
| `tstdx.domain.models` | Bar/Quote/Level 等 domain model |
| `tstdx.domain.symbol` | canonical symbol/market identity |
| `tstdx.domain.period` | canonical period normalization |
| `tstdx.domain.adjust` | 复权计算 |
| `tstdx.domain.calendar` | 交易日历 |
| `tstdx.output` | DataFrame/Parquet/DuckDB 输出 |
| `tstdx.reader.formats` | vipdoc .day/.lc1/.lc5/.dat/.gpcw |

## Web Provider / adapter

Web 能力通过 Provider Registry / Direct API 明确归属来源；adapter 原生入口仍可用于
Provider-specific 数据，不会被自动转换成另一 Provider 的响应。

当前 registry 包括 TDX 以及 Tencent/Sina/Eastmoney/Baidu/JSL/BOC/iWencai 等已接线 Provider。
具体能力见 [Provider 文档](../providers/README.md)。

## Streaming

| 模块 | 说明 |
|---|---|
| `tstdx.streaming.planned` | Provider-first planned stream + `StreamState` |
| `tstdx.streaming.engine` | legacy/底层 streaming engine |
| `tstdx.streaming.push` | TDX PushChannel 原始推送 |

`PlannedQuoteStream` 生命周期为：

```text
CREATED -> RUNNING -> STOPPING -> CLOSED
                    \-> FAILED
```

terminal 状态不能重新 start/subscribe；部分 worker 启动失败和意外 worker 退出均 fail closed。

## 错误与外部集成

| 模块 | 说明 |
|---|---|
| `tstdx.errors` | domain error tree + RetryAdvice |
| `tstdx.error_envelope` | canonical safe envelope |
| `tstdx.integration.http_app` | 当前 REST Provider-first 入口 |
| `tstdx.integration.ws_app` | 当前 WebSocket JSON-RPC 入口 |
| `tstdx.integration.mcp_app` | 当前 MCP JSON-RPC/stdio 入口 |
| `tstdx.integration.tasks` | background task safe result/error lifecycle |
| `tstdx.integration.http_server` | legacy compatibility HTTP surface |
| `tstdx.integration.mcp_server` | legacy compatibility MCP facade |

REST / WS / MCP / background task 的 native/internal exception 对外折叠为安全 ErrorEnvelope；
`KeyboardInterrupt` / `SystemExit` / async cancellation 保持控制流语义。

## 打包与类型

- wheel 是 canonical `py3-none-any`。
- 包声明 `Typing :: Typed`，并实际包含 `tstdx/py.typed`。
- `scripts/build_package.py` 校验 source/project/artifact/METADATA identity、wheel/sdist 内容与 Twine。
- GitHub Release 使用同一个 canonical artifact 做 12-cell 安装冒烟、PyPI、Release asset、Docker。

## 迁移

从 mootdx / easy_tdx / eltdx / easyquotation 等历史入口迁移见
[迁移文档](../migration/README.md)。新代码建议直接迁到 Provider-first Query/Direct API；
兼容入口只承担迁移期行为，不扩展新的跨 Provider fallback 语义。
