# v14 Runtime API 参考

> v14 Runtime 是 tstdx 的编排内核层，提供统一的查询执行、批量处理、流式订阅
> 与语义缓存去重能力。所有网关（Python API / CLI / REST / WS / MCP）均可委托
> Runtime 执行，禁止实现独立的 Provider 选择、回退、缓存或 provenance 逻辑。

## 架构概览

```text
Python API / CLI / REST / WS / MCP
              |
              v
        Runtime boundary
        QueryRequest
              |
              v
      ExecutionPlanner
      (DAG/orchestration)
              |
              v
   canonical QueryPlanner
  QuerySpec -> QueryPlan
  (single Provider identity)
              |
       +------+------+
       |             |
       v             v
SemanticResultCache  dynamic Provider adapter
(QueryFingerprint)   tdx / local_vipdoc /
                     eastmoney / tencent / ...
       |             |
       +------+------+
              v
     canonical QueryResult
        + Provenance
              |
              v
       Runtime Response
```

## 核心类

### Runtime

v14 编排内核入口点。管理 Provider 路由、执行计划编译、语义缓存与流式订阅。

```python
from tstdx.runtime import Runtime, create_runtime

# 空 Runtime（需手动注册 handler）
runtime = Runtime()

# 带语义缓存的 Runtime
runtime = create_runtime(
    semantic_cache=SemanticResultCache(),
    default_cache_ttl=60.0,
)

# 自定义 Provider 执行顺序
runtime = create_runtime(
    provider_order=("eastmoney", "tencent", "tdx"),
)
```

| 方法 | 说明 |
|------|------|
| `register(operation, handler)` | 注册操作处理器 |
| `register_provider(provider)` | 注册 Provider 适配器 |
| `execute(request) -> QueryResponse` | 执行单个 QueryRequest |
| `execute_typed(query, metadata=None) -> QueryResponse` | 执行 Typed CapabilityQuery |
| `execute_batch(requests, max_concurrent=8) -> list[QueryResponse]` | 批量执行（语义缓存去重 + 并发） |
| `subscribe(symbols, provider="tdx", interval=1.0, ...) -> StreamHandle` | 注册流式订阅 |
| `unsubscribe(subscription_id) -> StreamHandle \| None` | 停止订阅 |
| `get_subscription(subscription_id) -> StreamHandle \| None` | 获取订阅句柄 |
| `subscriptions() -> Mapping[str, StreamHandle]` | 活跃订阅快照 |
| `semantic_cache_stats() -> dict` | 语义缓存诊断（tier/enabled/size） |

### RuntimeGateway

CLI / HTTP / WS 的统一适配层。通过 RuntimeFacadeAdapter 桥接到 Runtime 执行，
禁止实现独立 Provider 选择/回退/缓存/provenance 逻辑。

```python
from tstdx.runtime import RuntimeGateway, create_runtime

gateway = RuntimeGateway()

# K 线
resp = gateway.bars("sh600519", count=30)
if resp.success:
    print(resp.data)

# 实时行情
resp = gateway.quotes(("sh600000", "sh600519"))

# 指定 Provider 路由
resp = gateway.bars("sh600519", route="tdx")
resp = gateway.bars("sh600519", providers=("eastmoney", "tencent"))

# 批量执行
from tstdx.runtime import QueryRequest
reqs = [
    QueryRequest(operation="bars", args=("sh600000",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh600519",), params={"count": 30}),
]
results = gateway.execute_batch(reqs, max_concurrent=4)

# 诊断
print(gateway.providers)              # 注册 Provider 列表
print(gateway.semantic_cache_stats()) # 缓存诊断
print(gateway.subscriptions())        # 活跃订阅
```

| 方法 | 说明 |
|------|------|
| `bars(symbol, period, count, start, ...)` | 获取 K 线 |
| `quotes(symbols, as_format, ...)` | 实时行情快照 |
| `security_count(market, ...)` | 证券数量 |
| `finance_info(symbol, ...)` | 财务信息 |
| `minute_today(symbol, ...)` | 当日分时 |
| `security_list(market, start, ...)` | 证券列表 |
| `execute_batch(requests, max_concurrent)` | 批量执行 |
| `execute(request)` | 执行 QueryRequest |
| `execute_typed(query)` | 执行 CapabilityQuery |
| `subscribe(symbols, provider, interval, ...)` | 流式订阅 |
| `providers` | Provider 名称列表 |
| `semantic_cache_stats()` | 缓存诊断 |
| `subscriptions()` | 活跃订阅 |

### QueryRequest

Runtime 边界调用信封。携带操作名、位置参数、关键字参数与运行时元数据。

```python
from tstdx.runtime import QueryRequest

# 基本用法
req = QueryRequest(
    operation="bars",
    args=("sh600519",),
    params={"count": 80, "period": "day"},
    metadata={"cache_ttl": 60.0, "provider": "tdx"},
)

# 从 Typed Query 转换
from tstdx.runtime import request_from_typed
req = request_from_typed(capability_query, metadata={"timeout": 5.0})
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `operation` | str | 操作名（capability 标识） |
| `args` | tuple | 位置参数 |
| `params` | dict | 关键字参数 |
| `metadata` | dict | 运行时元数据（provider/providers/cache_ttl/timeout/channel） |
| `request_key` | str | 诊断用标识（非语义缓存键） |

### QueryResponse

Runtime 执行结果信封。

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 是否成功 |
| `data` | Any | 成功时的数据载荷 |
| `error` | str \| None | 失败时的错误信息 |
| `code` | str \| None | 错误码 |
| `metadata` | dict | 运行时元数据（provider/channel/query_fingerprint/provenance/execution） |

元数据中的 `execution` 字段标识执行路径：

| 值 | 含义 |
|----|------|
| `"compat-handler"` | 通过注册 handler 执行 |
| `"execution-plan"` | 通过 ExecutionPlanner 编译 + 执行 |
| `"semantic-cache"` | 批量执行中命中语义缓存 |

### StreamHandle

流式订阅句柄。持有编译后的 StreamPlan 与生命周期状态机。

```python
from tstdx.runtime import create_runtime

runtime = create_runtime()
handle = runtime.subscribe(
    "quotes",
    ("sh600000", "sh600519"),
    provider="tdx",
    interval=1.0,
    diff_only=True,
    max_queue=1024,
    subscription_id="main-watchlist",
)

# 生命周期操作
handle.snapshot()         # StreamLifecycleSnapshot
handle.stream_state()     # StreamState
handle.begin_start()      # 幂等启动：CREATED -> RUNNING
handle.id                 # 订阅标识

# 停止订阅
runtime.unsubscribe("main-watchlist")
```

| 方法 | 说明 |
|------|------|
| `snapshot()` | 生命周期快照 |
| `stream_state()` | 当前状态（别名 state） |
| `begin_start()` | 幂等启动 |
| `begin_stop()` | 开始停止（幂等） |
| `close()` | 关闭（保留 FAILED 语义） |
| `id` | 订阅标识 |
| `plan` | 编译后的 StreamPlan |
| `lifecycle` | StreamLifecycle 状态机 |

### create_runtime

Runtime 工厂函数。

```python
from tstdx.runtime import create_runtime
from tstdx.cache_semantic import SemanticResultCache

runtime = create_runtime(
    router=provider_router,          # 可选：自定义 Provider 路由器
    planner=execution_planner,       # 可选：自定义 ExecutionPlanner
    provider_order=("eastmoney",),   # Provider 执行顺序
    semantic_cache=SemanticResultCache(),  # 语义缓存
    default_cache_ttl=60.0,          # 默认缓存 TTL（秒）
)
```

## 批量执行

`execute_batch()` 是 Phase 7 的批量执行入口，支持语义缓存去重与并发执行。

### 工作原理

```text
Pass 1: 语义缓存查询
  - 对每个 QueryRequest 编译 QuerySpec
  - 命中缓存 → 直接返回 QueryResponse(ok, execution="semantic-cache")
  - 未命中 → 加入待执行队列

Pass 2: 并发执行
  - 待执行请求通过 ThreadPoolExecutor 并发执行
  - max_concurrent 控制并发度
  - 结果保序返回
```

### 示例

```python
from tstdx.runtime import Runtime, QueryRequest, create_runtime
from tstdx.cache_semantic import SemanticResultCache

runtime = create_runtime(semantic_cache=SemanticResultCache())

requests = [
    QueryRequest(operation="bars", args=("sh600000",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh600519",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh000001",), params={"count": 30}),
]

# 批量执行（max_concurrent=1 串行）
results = runtime.execute_batch(requests, max_concurrent=1)

# 批量执行（max_concurrent=8 并发，默认）
results = runtime.execute_batch(requests)

# 检查缓存诊断
stats = runtime.semantic_cache_stats()
print(f"缓存层: {stats['tier']}, 启用: {stats['enabled']}, 大小: {stats.get('size')}")
```

## Typed Query 契约

v14 提供 60+ 类型化查询契约，覆盖 9 个领域。每个契约通过
`request_from_typed()` 转换为 QueryRequest 后由 Runtime 执行。

```python
from tstdx.runtime import create_runtime, request_from_typed
from tstdx.typed_query import CapabilityQuery

runtime = create_runtime()

# 从 CapabilityQuery 执行
resp = runtime.execute_typed(query, metadata={"cache_ttl": 60.0})
```

### 领域覆盖

| 领域 | 契约数 | 示例 |
|------|--------|------|
| Financial | 10 | FinancialQuery, IncomeStatementQuery, CashFlowQuery |
| Fund | 7+2 | FundHoldingsQuery, FundRankQuery |
| Bond | 7+1 | BondKlineQuery, BondYieldQuery |
| Futures | 3+1 | FuturesKlineQuery, FuturesQuoteQuery |
| Options | 2+1 | OptionSnapshotQuery |
| News | 1+1 | NewsQuery |
| Research | 1+1 | ResearchReportQuery |
| F10 | 1+1 | F10Query |
| Macro | 1 | MacroEconomicQuery |
| Search | 1 | SearchQuery |

## Domain Record 模型

v14 提供 9 个类型化 Domain Record 族，替代 list[dict] 的原始输出。

```python
from tstdx.domain.records import (
    Bar,
    Quote,
    Level,
    CapitalChange,
    FinanceInfo,
    StockInfo,
    FundInfo,
    BondInfo,
    NewsItem,
)
```

| Record 族 | 说明 |
|-----------|------|
| `Bar` | K 线数据 |
| `Quote` | 实时行情 |
| `Level` | 分价分笔 |
| `CapitalChange` | 资本变迁 |
| `FinanceInfo` | 财务信息 |
| `StockInfo` | 证券基本信息 |
| `FundInfo` | 基金信息 |
| `BondInfo` | 债券信息 |
| `NewsItem` | 新闻/公告 |

## 语义缓存

语义缓存基于 `QueryFingerprint` 实现，命中时保留原始 Provider provenance，
仅添加 `cache_tier` 元数据。

```python
from tstdx.cache_semantic import SemanticResultCache

cache = SemanticResultCache()

# L1（内存）缓存
cache.put(plan, result, ttl=60.0)
hit = cache.get(plan)

# L2（持久化）缓存
from tstdx.cache_persistent import PersistentSemanticCache
cache = PersistentSemanticCache(directory="./.cache")
```

| 层 | 类型 | 说明 |
|----|------|------|
| L1 | 内存 | 进程内快速缓存 |
| L2 | 持久化 | 磁盘持久缓存（Pydantic + JSON） |

## 模块索引

| 模块 | 说明 |
|------|------|
| `tstdx.runtime` | Runtime 包入口（Runtime/RuntimeGateway/QueryRequest/QueryResponse/create_runtime） |
| `tstdx.runtime.runtime` | Runtime 编排内核 |
| `tstdx.runtime.gateway` | RuntimeGateway 网关适配器 |
| `tstdx.runtime.request` | QueryRequest 边界信封 |
| `tstdx.runtime.response` | QueryResponse 结果信封 |
| `tstdx.runtime.context` | ExecutionContext 执行上下文 |
| `tstdx.runtime.bootstrap` | create_runtime 工厂 |
| `tstdx.runtime.stream` | StreamHandle 流式订阅管理 |
| `tstdx.runtime.typed` | request_from_typed 类型化查询转换 |
| `tstdx.execution` | ExecutionPlanner / ExecutionGraph / ExecutionNode |
| `tstdx.execution.semantic` | SemanticExecutionAdapter（语义执行桥接） |
| `tstdx.execution.planner` | ExecutionPlanner DAG 编译器 |
| `tstdx.execution.graph` | ExecutionGraph（DAG + 拓扑排序 + 执行） |
| `tstdx.execution.node` | ExecutionNode（节点 + 依赖 + handler） |
| `tstdx.execution.plan` | ExecutionPlan（编译结果） |
| `tstdx.cache_semantic` | SemanticResultCache（语义缓存 L1/L2） |
| `tstdx.cache_persistent` | PersistentSemanticCache（持久化缓存） |
| `tstdx.stream_contract` | StreamPlanner（流计划编译） |
| `tstdx.streaming.state` | StreamLifecycle（流生命周期状态机） |
| `tstdx.typed_query` | CapabilityQuery + 60 契约 + QueryResult[T] |
| `tstdx.domain.records` | 9 Domain Record 族 |
| `tstdx.facade.runtime_adapter` | RuntimeFacadeAdapter（门面桥接） |
