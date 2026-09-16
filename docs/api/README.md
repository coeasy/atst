# API 参考

当前 `1.0.0` Draft 开发线；最新已发布稳定版是 `v1.0.0`。

> 本页对应 v1.0.0 稳定发布版 + v14 Runtime 编排内核。完整 docstring 驱动文档
> 由 `pdoc`/`mkdocstrings` 生成；此处提供稳定入口和模块索引。
>
> **Provider 契约**：v13 clean break 后禁止跨 Provider silent fallback。每次请求绑定
> 恰好一个 Provider/channel；跨 Provider 容错只能经显式 `FallbackPolicy` +
> `ProviderOrchestrator`。`tstdx.facade.UnifiedQuoteAPI` 是官方导出，
> `tstdx.facade.api.UnifiedQuoteAPI` 仅作为 legacy compatibility router 保留。

## v14 Runtime（编排内核）

| 模块 | 说明 |
|------|------|
| `tstdx.runtime.Runtime` | 编排内核入口（execute/execute_batch/subscribe/semantic_cache_stats） |
| `tstdx.runtime.RuntimeGateway` | CLI/HTTP/WS 统一网关适配器（bars/quotes/security_count/...） |
| `tstdx.runtime.QueryRequest` | 边界调用信封（operation/args/params/metadata） |
| `tstdx.runtime.QueryResponse` | 结果信封（success/data/error/code/metadata） |
| `tstdx.runtime.create_runtime()` | 工厂（router/planner/provider_order/semantic_cache/default_cache_ttl） |
| `tstdx.runtime.StreamHandle` | 流式订阅句柄（plan/lifecycle/snapshot/begin_start/close） |
| `tstdx.execution.ExecutionPlanner` | DAG 编排编译器（fallback/多 Provider） |
| `tstdx.execution.SemanticExecutionAdapter` | 语义执行桥接（QuerySpec→QueryPlan→QueryResult） |
| `tstdx.execution.ExecutionGraph` | DAG 图（拓扑排序 + 串行执行） |
| `tstdx.cache_semantic.SemanticResultCache` | 语义缓存（L1 内存 / L2 持久化，QueryFingerprint 键） |
| `tstdx.typed_query.CapabilityQuery` | 类型化查询契约（60+ 契约，9 领域） |
| `tstdx.domain.records` | Domain Record 族（Bar/Quote/Level/CapitalChange/FinanceInfo/...） |

详见 [v14 Runtime API 完整参考](v14-runtime.md) 与 [项目接口文档](interfaces.md)。

## 核心入口

| 模块 | 说明 |
|---|---|
| `tstdx.client.TdxClient` | 同步客户端主入口 |
| `tstdx.client.AsyncTdxClient` | 异步镜像客户端 |
| `tstdx.client.get_client(kind)` | 工厂：std/goods/ex/mac/f10 |
| `tstdx.facade.UnifiedQuoteAPI` | 官方门面导出（`tstdx.facade.planned`，走 QueryPlan/Provider 绑定）|
| `tstdx.facade.api.UnifiedQuoteAPI` | legacy compatibility router + 历史实现（保留供迁移）|
| `tstdx.facade.async_api.AsyncUnifiedQuoteAPI` | 异步门面（紧凑设计：核心 10 方法桥接 + `arun()` 泛化任意方法；有意不逐方法镜像）|
| `tstdx.facade.response.ApiResponse` | 统一响应形态（ok/err/wrap + 惰性 .df）|
| `tstdx.web.facade.WebQuoteSession` | Web 源原生命名会话（异动/人气榜/问财/IPO…）|

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

## 源与流

| 模块 | 说明 |
|---|---|
| `tstdx.sources.router` | DataSourceRouter 单 Provider 选择器（禁止跨 Provider silent fallback）|
| `tstdx.web.adapters` | HTTP Web 源（新浪/腾讯/东财/集思录/港股/中行）|
| `tstdx.web.adapters_ext` | 扩展 Web 源（分时/逐笔/联想/全球）|
| `tstdx.web.fundflow` | 资金流 + 涨停池 + **盘中异动**（16 类实时池）+ 沪深港通 |
| `tstdx.web.hot_rank` | **股吧个股人气榜**（emappdata POST JSON）|
| `tstdx.web.wencai` | **i问财自然语言选股**（cookie 调用方持有）|
| `tstdx.web.boards` | 个股所属板块 / 板块行情 |
| `tstdx.web.corporate` | F10/业绩/IPO 申购日历（datacenter 报表族）|
| `tstdx.web.adapters_margin` | **融资融券个股明细**（datacenter RPTA_WEB_RZRQ_GGMX；`api.margin()` / `tstdx margin`）|
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
| `tstdx.integration.http_server` | FastAPI 网关（~40 接口，含 /stock_changes /hot_rank /wencai /ipo /search /query）|
| `tstdx.integration.ws_server` | WebSocket JSON-RPC（bars/quotes/minute/trades/finance/security_count/stock_changes + subscribe）|
| `tstdx.integration.mcp_server` | MCP stdio 工具（canonical Client runtime 能力 + `query_capability` 通用入口）|
| `tstdx.cli` | CLI 子命令（bars/quotes/…/changes/hot）|

## 迁移指南

从 mootdx / easy_tdx / eltdx / easyquotation 迁移见 [docs/migration/](../migration/README.md)。
（v1.0 时代的 `tstdx.compat.*` / `tstdx.web.easyquotation` 兼容垫片已随 v1.2.0 清理移除，
迁移请使用原生 `TdxClient` / `tstdx.web` / 门面 API。）
