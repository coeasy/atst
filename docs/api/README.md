# API 参考

当前 `1.0.0` Draft 开发线；最新已发布稳定版是 `v1.0.0`（2026-09-09 发布）· [发布说明](../releases/v1.0.0.md)

> 本页对应 v1.0.0 稳定发布版 + **v13/v17 单一执行内核**。完整 docstring 驱动文档
> 由 `pdoc`/`mkdocstrings` 生成；此处提供稳定入口和模块索引。
>
> **Provider 契约**：每次请求绑定恰好一个 Provider/channel，禁止跨 Provider silent fallback。
> 跨 Provider 容错只能经显式 `FallbackPolicy` + `ProviderOrchestrator`。
> 数据请求**零缓存**。
> 已删除且不再是事实的层（以目录形式记名，均已物理移除、无兼容别名，也都没有作为
> legacy compatibility router 保留）：v12 门面 `tstdx/facade/`、
> 降级路由 `tstdx/sources/`、全部缓存层、v14 信封运行时（`Runtime`/`RuntimeGateway`/
> `QueryRequest`/`QueryResponse` 及 `tstdx/execution/`、`tstdx/provider/`）。

## 统一内核层

| 模块 | 说明 |
|------|------|
| `tstdx.Client` / `tstdx.AsyncClient` | **唯一业务入口**（`tstdx.client.api`）：bars/quotes/snapshot/minute/trades/security_*/quotes_batch/stream + `execute`/`call`/`typed`/`execute_with_policy` |
| `tstdx.runtime.UnifiedRuntime` | 唯一执行内核：`QuerySpec → QueryPlan → 绑定执行 → QueryResult`，零缓存 |
| `tstdx.runtime.KernelExecutor` | 执行面 Protocol（测试注入假执行体的唯一接缝）|
| `tstdx.runtime.executor.DirectProviderExecutor` | 按 `DIRECT_BINDINGS` 精确直调 Provider 实现 |
| `tstdx.runtime.orchestration.ProviderOrchestrator` | 显式 `FallbackPolicy` 跨源编排（返回 `OrchestratedResult`）|
| `tstdx.runtime.audit.audit_runtime` | 启动三方对账（registry / catalog / bindings）|
| `tstdx.runtime.identity` / `tstdx.runtime.provenance` | 执行身份派生 + 结果溯源守卫（换源即抛）|
| `tstdx.query.QuerySpec` / `QueryPlan` / `QueryPlanner` | 请求规格、单 Provider/单 Channel 计划、规划期校验 |
| `tstdx.result.QueryResult` / `ResultMeta` / `Provenance` | 结果载荷 + 溯源元信息 |
| `tstdx.batch.BatchResult` / `BatchItem` | 批量三态（ok/missing/failed）保序契约 |
| `tstdx.typed_query.CapabilityQuery` | 类型化查询契约（60+ 契约，10 领域基类）+ `TypedQueryResult` |
| `tstdx.domain.records` | Domain Record 族（9 类：Financial/Fund/Bond/News/Research/Option/MarketData/Search/Macro）|
| `tstdx.stream_contract.StreamSpec` / `StreamPlanner` | 流式请求契约与规划 |
| `tstdx.catalog.capability` | capability 目录 + 规划期真实签名校验（`validate_call`）|
| `tstdx.catalog.provider_bindings` | Provider `channel → adapter` 绑定表 |
| `tstdx.catalog.provider_contract` / `provider_guard` / `*_audit` | Provider 隔离契约、运行时守卫与一致性审计 |
| `tstdx.providers.PROVIDERS` | 11 Provider × 172 capability × channel 唯一事实源 |

## 核心入口

| 模块 | 说明 |
|---|---|
| `tstdx.client.TdxClient` | 同步协议客户端主入口（可脱离内核使用）|
| `tstdx.client.AsyncTdxClient` | 异步镜像客户端 |
| `tstdx.client.get_client(kind)` | 工厂：std/goods/ex/mac/f10 |
| `tstdx.client.core` | 同步/异步共享的纯协议构造与校验 SSOT |
| `tstdx.web.session.WebQuoteSession` | Web 源原生命名会话（异动/人气榜/问财/IPO…），精确 Provider 适配器，非聚合路由 |

## 协议层

| 模块 | 说明 |
|---|---|
| `tstdx.protocol.commands` | 85 命令账本（5 协议族）|
| `tstdx.protocol.registry` | BaseParser + dispatch（L1/L2/L3）|
| `tstdx.protocol.generic` | L2 启发式 + ProtocolSniffer |
| `tstdx.protocol.prober` | 未知命令探测（限速 + 非交易时段）|
| `tstdx.protocol.parsers.*` | std7709/std7727/mac/f10/goods 解析器 |

## 传输层

| 模块 | 说明 |
|---|---|
| `tstdx.transport.pool` | ConnectionPool 连接池 |
| `tstdx.transport.async_` | AsyncConnectionPool 异步连接池 |
| `tstdx.transport.ratelimit` | 令牌桶限流 |
| `tstdx.transport.speedtest` | 主站测速（持久化 TTL）|
| `tstdx.transport.sniff` | 被动嗅探 + spec 草稿导出 |

连接池在 v1.0.0 中提供 generation/lease 生命周期保护、half-open 单探测门禁，
并将后台测速 RTT 与真实请求 health RTT 分开维护；热更新主站时，仍在执行的旧代请求
不会回写新代主站状态。

## 数据与落地

| 模块 | 说明 |
|---|---|
| `tstdx.domain.models` | Bar/Quote/Level + to_dataframe |
| `tstdx.domain.adjust` | 复权计算 |
| `tstdx.domain.calendar` | A 股交易日历 2024-2026 |
| `tstdx.output` | write() → DataFrame/Parquet/DuckDB |
| `tstdx.reader.formats` | vipdoc .day/.lc1/.lc5/.dat/.gpcw |
| `tstdx.profile.detect` | 6 步数据规格探测 |
| `tstdx.profile.presets` | 9 市场预设 |

## Web 源与流

| 模块 | 说明 |
|---|---|
| `tstdx.providers` | Provider 注册表（11 Provider × channel），内核唯一可调用实现体 |
| `tstdx.web.adapters` | HTTP Web 源（新浪/腾讯/东财/集思录/港股/中行）|
| `tstdx.web.adapters_ext` | 扩展 Web 源（分时/逐笔/联想/全球）|
| `tstdx.web.fundflow` | 资金流 + 涨停池 + **盘中异动**（20 类异动枚举）+ 沪深港通 |
| `tstdx.web.hot_rank` | **股吧个股人气榜**（emappdata POST JSON）|
| `tstdx.web.wencai` | **i问财自然语言选股**（cookie 调用方持有）|
| `tstdx.web.boards` | 个股所属板块 / 板块行情 |
| `tstdx.web.corporate` | F10/业绩/IPO 申购日历（datacenter 报表族）|
| `tstdx.web.adapters_margin` | **融资融券个股明细**（datacenter RPTA_WEB_RZRQ_GGMX；`margin` capability / `tstdx margin <symbol>`）|
| `tstdx.web.normalize` | volume/amount 集中归一化 |
| `tstdx.streaming.engine` | StreamEngine（重连/补数/背压）|
| `tstdx.streaming.push` | PushChannel 0x0547 原始推送 |

## 基础设施

| 模块 | 说明 |
|---|---|
| `tstdx.errors` | 40+ 异常树 + RetryAdvice |
| `tstdx.config.loader` | 6 源合并配置 |
| `tstdx.charset.encoding` | UTF-8/GBK/GB18030/Big5 自动探测 |
| `tstdx.feedback` | 反馈上报（opt-in + 7 步脱敏）|
| `tstdx.observability` | Prometheus/Statsd/OTLP 导出 |
| `tstdx.deprecation` | DeprecationPolicy + @deprecated |

## 集成服务

| 模块 | 说明 |
|---|---|
| `tstdx.integration.runtime_http` | FastAPI 网关工厂（10 路由：`/v13/quotes` `/v13/bars/{symbol}` `/v13/snapshot/{symbol}` `/v13/minute/{symbol}` `/v13/trades/{symbol}` `/v13/security/count` `/v13/security/list` `/v13/query/{capability}` `/v13/capabilities` `/v13/runtime/health`）|
| `tstdx.integration.runtime_ws` | WebSocket JSON-RPC（10 方法：quotes/bars/snapshot/minute/trades/security.count/security.list/query/runtime.capabilities/runtime.health）|
| `tstdx.integration.runtime_ws_server` | WS 服务宿主（`serve_runtime_ws`）|
| `tstdx.integration.mcp` | MCP stdio 工具（9 项：`query_capability` + get_bars/get_quote(s)/get_snapshot/get_minute_today/get_trades/get_security_count/get_security_list）|
| `tstdx.integration.runtime_tasks` | 后台任务存储（有界结果保留 + 安全信封）|
| `tstdx.integration.serialization` | `QueryResult → JSON-safe` 统一序列化 |
| `tstdx.cli` | CLI 子命令（31 项；数据命令全部经 `Client`，6 个传输/诊断命令除外，见 `runtime_commands.py`）|

## 迁移指南

从 mootdx / easy_tdx / eltdx / easyquotation 迁移见 [docs/migration/](../migration/README.md)。
（v1.0 时代的 `tstdx/compat/` 与 `tstdx/web/easyquotation.py` 兼容垫片已随 v1.2.0 清理移除，
v1.2 时代的门面 `tstdx/facade/` 亦已随 v16 Phase 2 物理删除；
迁移请使用 `tstdx.Client`（唯一业务入口）、协议层 `TdxClient`、或 `tstdx.web` 适配器。）
