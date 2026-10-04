# API 参考

当前 `1.4.2`；GitHub Release [`v1.4.2`](https://github.com/coeasy/atst/releases/tag/v1.4.2) 已发布，但本包**不在 PyPI 上** · [v1.0.0 发布说明](../releases/v1.0.0.md) · [v1.1.0 发布说明](../releases/v1.1.0.md) · [v1.2.0 发布说明](../releases/v1.2.0.md) · [v1.2.1 发布说明](../releases/v1.2.1.md) · [v1.2.2 发布说明](../releases/v1.2.2.md) · [v1.3.0 发布说明](../releases/v1.3.0.md) · [v1.4.0 发布说明](../releases/v1.4.0.md) · [v1.4.1 发布说明](../releases/v1.4.1.md) · [v1.4.2 发布说明](../releases/v1.4.2.md)

> 本页对应 v1.0.0 + **v13/v17 单一执行内核**。完整 docstring 驱动文档
> 由 `pdoc`/`mkdocstrings` 生成；此处提供稳定入口和模块索引。
>
> **Provider 契约**：每次请求绑定恰好一个 Provider/channel，禁止跨 Provider silent fallback。
> 跨 Provider 容错只能经显式 `FallbackPolicy` + `ProviderOrchestrator`。
> 数据请求**零缓存**。
> 已删除且不再是事实的层（以目录形式记名，均已物理移除、无兼容别名，也都没有作为
> legacy compatibility router 保留）：v12 门面 `atst/facade/`、
> 降级路由 `atst/sources/`、全部缓存层、v14 信封运行时（`Runtime`/`RuntimeGateway`/
> `QueryRequest`/`QueryResponse` 及 `atst/execution/`、`atst/provider/`）。

## 统一内核层

| 模块 | 说明 |
|------|------|
| `atst.Client` / `atst.AsyncClient` | **唯一业务入口**（`atst.client.api`）：bars/quotes/snapshot/minute/trades/security_*/quotes_batch/stream + `execute`/`call`/`typed`/`execute_with_policy` |
| `atst.runtime.UnifiedRuntime` | 唯一执行内核：`QuerySpec → QueryPlan → 绑定执行 → QueryResult`，零缓存 |
| `atst.runtime.KernelExecutor` | 执行面 Protocol（测试注入假执行体的唯一接缝）|
| `atst.runtime.executor.DirectProviderExecutor` | 按 `DIRECT_BINDINGS` 精确直调 Provider 实现 |
| `atst.runtime.orchestration.ProviderOrchestrator` | 显式 `FallbackPolicy` 跨源编排（返回 `OrchestratedResult`）|
| `atst.runtime.audit.audit_runtime` | 启动三方对账（registry / catalog / bindings）|
| `atst.runtime.identity` / `atst.runtime.provenance` | 执行身份派生 + 结果溯源守卫（换源即抛）|
| `atst.query.QuerySpec` / `QueryPlan` / `QueryPlanner` | 请求规格、单 Provider/单 Channel 计划、规划期校验 |
| `atst.result.QueryResult` / `ResultMeta` / `Provenance` | 结果载荷 + 溯源元信息 |
| `atst.batch.BatchResult` / `BatchItem` | 批量三态（ok/missing/failed）保序契约 |
| `atst.typed_query.CapabilityQuery` | 类型化查询契约（60+ 契约，10 领域基类）+ `TypedQueryResult` |
| `atst.domain.records` | Domain Record 族（9 类：Financial/Fund/Bond/News/Research/Option/MarketData/Search/Macro）|
| `atst.stream_contract.StreamSpec` / `StreamPlanner` | 流式请求契约与规划 |
| `atst.catalog.capability` | capability 目录 + 规划期真实签名校验（`validate_call`）|
| `atst.catalog.provider_bindings` | Provider `channel → adapter` 绑定表 |
| `atst.catalog.provider_contract` / `provider_guard` / `*_audit` | Provider 隔离契约、运行时守卫与一致性审计 |
| `atst.providers.PROVIDERS` | 14 Provider × 59 channel × 194 capability 唯一事实源；能力发现面只声明名字、不声明可用性，见 [interfaces.md](interfaces.md)「能力发现面」 |

## 核心入口

| 模块 | 说明 |
|---|---|
| `atst.client.TdxClient` | 同步协议客户端主入口（可脱离内核使用）|
| `atst.client.AsyncTdxClient` | 异步镜像客户端 |
| `atst.client.get_client(kind)` | 工厂：std/goods/ex/mac/f10 |
| `atst.client.core` | 同步/异步共享的纯协议构造与校验 SSOT |
| `atst.web.session.WebQuoteSession` | Web 源原生命名会话（异动/人气榜/问财/IPO…），精确 Provider 适配器，非聚合路由 |

## 协议层

| 模块 | 说明 |
|---|---|
| `atst.protocol.commands` | 85 命令账本（5 协议族）；它的 5 个查询函数逐个写了口径，见 `docs/api/interfaces.md`「命令账本查询面」|
| `atst.protocol.registry` | BaseParser + dispatch（L1/L2/L3）|
| `atst.protocol.generic` | L2 启发式 + ProtocolSniffer |
| `atst.protocol.prober` | 未知命令探测（限速 + 非交易时段）|
| `atst.protocol.parsers.*` | std7709/std7727/mac/f10/goods 解析器 |

## 传输层

| 模块 | 说明 |
|---|---|
| `atst.transport.pool` | ConnectionPool 连接池 |
| `atst.transport.async_` | AsyncConnectionPool 异步连接池 |
| `atst.transport.ratelimit` | 令牌桶限流 |
| `atst.transport.speedtest` | 主站测速（持久化 TTL）|
| `atst.transport.sniff` | 被动嗅探 + spec 草稿导出 |

连接池在 v1.0.0 中提供 generation/lease 生命周期保护、half-open 单探测门禁，
并将后台测速 RTT 与真实请求 health RTT 分开维护；热更新主站时，仍在执行的旧代请求
不会回写新代主站状态。异步侧的 `AsyncTcpConnection.close()` 有停机上界：发出关闭后
最多等 1 秒对端确认，超时或报错都直接丢弃该传输，调用方的 `await pool.close()`
因此不会因为一条半断的连接而挂住。

## 数据与落地

| 模块 | 说明 |
|---|---|
| `atst.domain.models` | Bar/Quote/Level + to_dataframe |
| `atst.domain.adjust` | 复权计算 |
| `atst.domain.calendar` | A 股交易日历 2024-2026 |
| `atst.output` | write() → DataFrame/Parquet/DuckDB |
| `atst.reader.formats` | vipdoc .day/.lc1/.lc5/.dat/.gpcw |
| `atst.profile.detect` | 6 步数据规格探测 |
| `atst.profile.presets` | 9 市场预设 |

## Web 源与流

| 模块 | 说明 |
|---|---|
| `atst.providers` | Provider 注册表（14 Provider × channel），内核唯一可调用实现体 |
| `atst.web.tencent.adapters` | 腾讯系 HTTP 源（实时行情 / K 线 / 分钟线 / 分时 / 港股 / 美股）|
| `atst.web.sina.adapters` | 新浪系 HTTP 源（实时行情 / 港股 / 历史 K 线 / 代码联想）|
| `atst.web.eastmoney.adapters` | 东财系 HTTP 源（实时行情 / push2his 历史 K 线 / 融资融券 / 指数成分）|
| `atst.web.baidu.adapters` | 百度财经 HTTP 源（日/周/月 K 线 / 分时 / 逐笔 / 五档）|
| `atst.web.jsl.adapters` | 集思录 HTTP 源（可转债）|
| `atst.web.boc.adapters` | 中行 HTTP 源（外汇牌价）|
| `atst.web.fundflow` | 资金流 + 涨停池 + **盘中异动**（20 类异动枚举）+ 沪深港通 |
| `atst.web.hot_rank` | **股吧个股人气榜**（emappdata POST JSON）|
| `atst.web.wencai` | **i问财自然语言选股**（cookie 调用方持有）|
| `atst.web.boards` | 个股所属板块 / 板块行情 |
| `atst.web.corporate` | F10/业绩/IPO 申购日历（datacenter 报表族）|
| `atst.web.adapters_fund` | 基金净值 / 估值 / 列表；**融资融券个股明细**见 `atst.web.eastmoney.adapters`（`margin` capability / `atst margin <symbol>`）|
| `atst.web.normalize` | volume/amount 集中归一化 |
| `atst.streaming.engine` | StreamEngine（重连/补数/背压）|
| `atst.streaming.push` | PushChannel 0x0547 原始推送 |

## 基础设施

| 模块 | 说明 |
|---|---|
| `atst.errors` | 40+ 异常树 + RetryAdvice |
| `atst.config.loader` | 6 源合并配置 |
| `atst.charset.encoding` | UTF-8/GBK/GB18030/Big5 自动探测 |
| `atst.feedback` | 反馈上报（opt-in + 7 步脱敏）+ `TelemetryCollector` / `UserStats`，口径见 `docs/api/interfaces.md`「反馈上报」|
| `atst.observability` | 内置指标门面 + Prometheus/Statsd/OTLP 三个导出器 + `start_exporter`；只有库面，逐格"谁在写"见 `docs/api/interfaces.md` §8 |

## 集成服务

| 模块 | 说明 |
|---|---|
| `atst.integration.runtime_http` | FastAPI 网关工厂（12 路由：`/v13/quotes` `/v13/bars/{symbol}` `/v13/snapshot/{symbol}` `/v13/minute/{symbol}` `/v13/trades/{symbol}` `/v13/security/count` `/v13/security/list` `/v13/query/{capability}` `/v13/capabilities` `/v13/universe` `/v13/universe/{kind}` `/v13/runtime/health`）；复权 K 线走通用入口 POST `/v13/query/{capability}`（capability=adjusted_bars）|
| `atst.integration.runtime_ws` | WebSocket JSON-RPC（13 方法：quotes/bars/snapshot/minute/trades/security.count/security.list/query/runtime.capabilities/runtime.health/subscribe/unsubscribe/list）；其中 `subscribe`/`unsubscribe`/`list` 为实时订阅控制面（配合服务端 `push` 推送帧），复权走 `query`（`capability=adjusted_bars`），见 `docs/api/interfaces.md`「WebSocket 实时订阅」|
| `atst.integration.runtime_ws_server` | WS 服务宿主（`serve_runtime_ws`；一键拉起 `python -m atst.integration.runtime_ws_server`）|
| `atst.integration.mcp` | MCP stdio 工具（9 项：`query_capability`/`get_bars`/`get_quote`/`get_quotes`/`get_snapshot`/`get_minute_today`/`get_trades`/`get_security_count`/`get_security_list`；一键拉起 `python -m atst.integration.mcp`，不需要 extra。复权 K 线走 query_capability 通用入口）|
| `atst.integration.serialization` | `QueryResult → JSON-safe` 统一序列化 |
| `atst.cli` | CLI 子命令（32 项；数据命令全部经 `Client`，6 个传输/诊断命令除外，见 `runtime_commands.py`）|

## 迁移指南

从 mootdx / easy_tdx / eltdx / easyquotation 迁移见 [docs/migration/](../migration/README.md)。
（v1.0 时代的 `atst/compat/` 与 `atst/web/easyquotation.py` 兼容垫片已随 v1.2.0 清理移除，
v1.2 时代的门面 `atst/facade/` 亦已随 v16 Phase 2 物理删除；
迁移请使用 `atst.Client`（唯一业务入口）、协议层 `TdxClient`、或 `atst.web` 适配器。）
