# tstdx 行业对标架构重构与执行优化方案 v12

> Branch: `refactor/industry-benchmark-v12`  
> Source baseline: `main@f927e7faf49e77352b531addc49ecc6e44abcf81` (`v1.4.0`)  
> Previous plan: `refactor/industry-benchmark-v11@6efcccb0527c648651120e3a07305c48c3b75749`  
> Updated: 2026-09-08  
> Status: **Current execution SSOT / 当前唯一执行基线**

本方案继承 v11 已确认的源码事实与重构边界，并重新对标 CCXT、OpenBB、AKShare、NautilusTrader、Qlib、pytdx/mootdx 类项目的最新工程模式，重点补齐 v11 在**执行计划、Provider 生命周期、错误传播、批量语义、缓存一致性、并发预算、实时调度、结果质量元数据和接口生成治理**方面仍不够具体的问题。

> 本轮原则：**不推倒底层协议；不重新设计已经稳定的 E1-E9 错误树；不靠增加更多 facade if/else 解决问题；先建立唯一执行内核，再做性能优化。**

---

# 0. 结论先行

`tstdx` 当前底层已经具备较成熟的协议、解析、连接池、Web 数据源、本地 vipdoc、缓存、Streaming、REST/WS/MCP 和多种输出能力。真正制约下一阶段质量与效率的，不是“功能少”，而是**同一能力存在多套执行规则和生命周期**。

当前最需要解决的不是继续增加数据源，而是把系统收敛为：

```text
Python / Async / CLI / REST / WS / MCP
                    |
                    v
          UnifiedMarketDataService
                    |
       Request Contract / QuerySpec
                    |
                    v
          QueryPlanner / QueryPlan
                    |
       Cache / Batch / Failure Policy
                    |
                    v
              SourceManager
        /          |          |          \
      TDX         Web       Vipdoc      Replay
        \          |          |          /
                    v
        Canonical Record / ResultMeta
                    |
          Serializer / Sink / Stream
```

横切能力统一由一个 `RuntimeContext` 管理：

```text
RuntimeContext
  +-- ConfigSnapshot
  +-- CapabilityRegistry
  +-- SourceManager
  +-- SourceHealthRegistry
  +-- CacheManager
  +-- ExecutionBudget / Executor
  +-- Metrics / Logger / Clock
  +-- TaskManager
```

最终必须做到：

1. **一个能力事实源**：`CapabilityRegistry`；
2. **一个请求契约**：`QuerySpec`；
3. **一个编译后的执行计划**：`QueryPlan`；
4. **一个高层执行入口**：`UnifiedMarketDataService`；
5. **一个 Source 生命周期所有者**：`SourceManager`；
6. **一个错误事实源**：现有 `tstdx.errors` E1-E9；
7. **一个应用层失败决策器**：`FailureDisposition/FailurePolicy`；
8. **一个结果元数据契约**：`ResultMeta / Attempt / ErrorEnvelope`；
9. **一个缓存语义**：Cache 只能优化执行，不能改变业务结果；
10. **一个实时调度模型**：请求与订阅共享 Source 和数据契约；
11. **一个机器可读 API/Capability Registry**：接口、文档、服务面、测试共同消费；
12. **所有入口一致**：HTTP/WS/MCP/CLI 不再自行决定 source/fallback。

---

# 1. v12 相比 v11 的新增结论

v11 已经确定了 `CapabilityRegistry + QuerySpec + Canonical Router + SourceManager + Cache V2 + MarketDataService` 主线。v12 不推翻这些结论，而是把缺失的执行细节补齐。

## 1.1 v12 新增核心抽象

| 抽象 | 解决的问题 |
|---|---|
| `RuntimeContext` | Config、Source、Cache、Health、Executor、Metrics 多生命周期并存 |
| `QueryPlan` | 每个方法重复 route/cache/retry 判断，没有可审计的实际执行计划 |
| `ExecutionBudget` | Transport、Source、Facade 多层 retry 导致超时放大 |
| `QueryFingerprint` | Cache key、SingleFlight key、trace key 各自定义 |
| `FailureDisposition` | `RetryAdvice` 偏底层，不能独立表达高层“是否切 Source” |
| `BatchPlanner` | 批量请求未按 source/market/batch-limit 分组与自动 chunk |
| `SingleFlight` | 并发相同请求重复访问上游，缓存 miss 时形成击穿 |
| `ResultMeta` | source、fallback、stale、approximate、parse tier 等元数据传播不统一 |
| `BatchResult` | 批量请求出现部分失败时没有统一 partial-success 契约 |
| `SchemaRegistry` | Quote/Bar/dict/HTTP JSON/MCP schema 演进无法机器校验 |
| `ProviderConformance` | 新 Source 是否满足 canonical contract 依靠人工记忆 |

## 1.2 v12 新增工程原则

### 原则 A：Capability 与 Health 必须分离

```text
Capability = 静态事实：这个 Source 理论上支持什么
Health     = 动态事实：这个 Source 现在是否健康
```

禁止把“暂时失败”写回 capability，也禁止把“理论支持”当成“当前可用”。

### 原则 B：Error 与 Failure Decision 必须分离

异常描述**发生了什么**；Planner 决定**接下来怎么做**。

```text
TdxError / RetryAdvice
          |
          v
FailurePolicy + RoutePolicy + Budget + Capability
          |
          v
FailureDisposition
```

这避免继续把 fallback 逻辑塞进错误类、Transport 或 Facade。

### 原则 C：内部只保留 canonical data，序列化只发生在边界

禁止高频路径反复：

```text
Quote -> dict -> Quote -> dict -> JSON
Bar   -> dict -> Bar   -> DataFrame
```

内部保持 canonical record；HTTP/CLI/DataFrame/Sink 最后一步再转换。

### 原则 D：所有重试共享一个总预算

```text
request deadline
  ├─ source A / host 1 retry
  ├─ source A / host 2 retry
  └─ source B retry
```

每层只消费**剩余预算**，不能每层重新获得完整 timeout。

---

# 2. 最新源码复核：仍需优先解决的问题

以下问题均基于当前 `main@f927e7f` 源码重新确认。

## 2.1 P0：数据正确性与显式契约

| ID | 当前实现 | 风险 | v12 处置 |
|---|---|---|---|
| P0-01 | `_reader_kline()` 仅 `M1 -> MinBarReader`，M5 落到 DayBarReader | `.lc5` 可能按日线格式解析 | M1/M5 都走 MinBarReader，并做真实 fixture 对拍 |
| P0-02 | Facade local bars 明确只允许 day | 已存在 lc1/lc5 reader 但高层主链不可达 | local 支持 day/1m/5m，以 Capability 声明驱动 |
| P0-03 | KlineCache key 只有 `(symbol, period, datetime)` | raw/qfq/hfq、source provenance 可互相污染 | Cache V2 + canonical raw + source_scope/schema_version |
| P0-04 | KlineCache 命中发生在 route/source 判断之前 | `route="tdx"` 可能返回非 tdx 数据 | explicit source 下 Cache 必须 source-scoped 或 bypass |
| P0-05 | KlineCache.get 只接受 count，不接受 start | `start>0` 命中返回错误窗口 | canonical series 读出后统一 slice |
| P0-06 | Router web 路径忽略 `start` | direct Router 调用可静默改变窗口 | Capability/ContractGuard 在 Planner 前 fail-closed |
| P0-07 | `security_list(route="tdx")` CommandOffline 后仍转 Web | explicit route 语义被破坏 | explicit source 永不跨源 |
| P0-08 | 存在 `errors.SourceUnavailable(E7050)` 与 `sources.SourceUnavailable(E3400)` | catch / code / HTTP 映射不一致 | 保留 E7050，sources 旧路径 re-export alias |
| P0-09 | `_get_router()` 不透传 timeout/web_sources | Unified API 构造参数不完全生效 | RuntimeContext/SourceManager 统一持有有效配置 |
| P0-10 | `_get_router()` 直接构造 DataSourceRouter | `build_router()` 的全局 Web/cache 配置可能旁路 | 禁止 Facade 自行 new Router |
| P0-11 | REST `/snapshot` 把代码列表传给单 symbol snapshot | 运行时签名错位 | REST 绑定 Service typed method；批量用专用 batch API |
| P0-12 | REST `/auction_snapshot` 同类错位 | 运行时断链 | 同上 |
| P0-13 | REST minute_history 日期是 str，底层契约是 int YYYYMMDD | 输入语义不统一 | Request model 统一规范化 |
| P0-14 | TaskStore cancel 只改状态，线程继续运行且不再计 active | 可绕过 max_tasks | Bounded TaskManager + cancel_requested 状态 |
| P0-15 | running task 可 delete 后后台继续 | orphan execution | running/cancel_requested 禁止 purge |
| P0-16 | `adjusted_bars(events=[])` 空列表被视为未提供 | 显式空输入触发额外联网 | 使用 `events is not None` 语义 |

## 2.2 P1：架构一致性与生命周期

| ID | 当前实现 | 影响 |
|---|---|---|
| P1-01 | Router quotes/kline 每次 `with TdxClient(...)` | ConnectionPool 无法跨 query 复用 |
| P1-02 | Router 每次创建/close WebQuoteClient | HTTP keep-alive 无法跨 query 复用 |
| P1-03 | Facade 另有持久 `_tdx/_web` | 同一进程存在双套客户端生命周期 |
| P1-04 | Router `last_errors/last_source` 是实例可变状态 | 请求间污染、并发竞态 |
| P1-05 | `_safe_run` 可把 generic Exception 转 SourceUnavailable | TypeError/AttributeError 等程序 bug 被 fallback 掩盖 |
| P1-06 | `build_router()` 对配置读取异常一律 warning + 默认配置 | 非法配置可能静默变成另一套运行语义 |
| P1-07 | Facade RouteSelector 与 Sources order 各有一套 auto 规则 | 同一“auto”在不同入口含义不同 |
| P1-08 | HTTP 默认 `_LazyClient -> TdxClient` | 高层 fallback、Web、Goods/Ex/Mac/F10 能力旁路 |
| P1-09 | HTTP 搜索/热点等端点直接调用 `WebQuoteSession` | 配置、生命周期、错误映射再次旁路 |
| P1-10 | WS/MCP 同样混用 direct client/facade/web | 服务面行为漂移 |
| P1-11 | Async facade 基于共享 sync facade + to_thread | first-use / circuit / close 并发语义不够清晰 |

## 2.3 P1：Streaming 与批量执行

当前 `QuoteStream` 已经有 `ReconnectPolicy / DeltaMerger / BackpressureQueue`，这部分应保留，但调度方式仍需重构：

- 所有订阅 symbol 简单拼接，没有统一去重；
- 每轮所有 subscription 一起 fetch；
- 最终 sleep 取最小 interval，低频订阅会被高频订阅带着重复抓取；
- callback 仍在 polling 主路径 drain 后同步执行；
- `PushChannel` 与 QuoteStream 仍是两套实时路径；
- 未形成 source-aware 的 subscription reference counting；
- 没有统一 watermark/sequence/gap-recovery 状态。

## 2.4 P2：语义孤儿与治理

- `SecurityConfig.credential_backend` 与已删除 CredentialStore 方向不一致；
- CompatibilityConfig 的部分字段需要证明真实消费者；
- BinaryClient / BridgeClient / HqClient 等兼容 facade 与正式入口并列；
- Golden replay、Performance cache、Synthetic 仍容易被“source”同一概念混淆；
- README/服务端点/能力文档依赖手工数字，长期必漂移；
- AST reachability=0 不代表 semantic orphan=0。

---

# 3. 行业主流项目对标结论

## 3.1 CCXT：Capability / Feature / Error / Rate Limit

CCXT 的关键价值不是“交易接口多”，而是：

- Provider/Exchange 有统一 `describe()`；
- `has/features` 把“支持/不支持/模拟实现”作为机器可读能力；
- `rateLimit` 与 provider metadata 同层声明；
- 异常层级稳定，上层可以按类型处理 Network/RateLimit/NotSupported；
- 用户高层 API 不需要知道每个 provider 的 URL/transport 细节。

### tstdx 应吸收

```text
CapabilityRegistry
  +-- supported
  +-- exact / approximate / replay
  +-- periods
  +-- adjustments
  +-- batch_limit
  +-- streaming
  +-- freshness_class
```

同时保留 `tstdx.errors` 稳定错误码，不复制 CCXT 的交易领域错误树。

---

## 3.2 OpenBB：标准 Query / Provider Fetcher / Canonical Data

OpenBB 的 Provider Fetcher 模型明确拆成：

```text
transform_query
     |
extract_data
     |
transform_data
```

这比“一个 execute() 内同时解析参数、联网、转换”更适合 tstdx 多源场景。

### tstdx 应吸收

Source Adapter 拆成三段：

```python
class SourceAdapter(Protocol):
    def prepare(self, query: QuerySpec, ctx: RequestContext) -> PreparedRequest: ...
    def fetch(self, req: PreparedRequest, ctx: RequestContext) -> RawResult: ...
    def normalize(self, raw: RawResult, ctx: RequestContext) -> CanonicalResult: ...
```

收益：

- 参数转换可独立测试；
- 网络/协议调用可独立 benchmark；
- normalize 可做跨源 parity；
- 缓存可选择存 canonical 或 raw，而不是和 source 代码绑定；
- 同步/异步可以共享 prepare/normalize，只替换 fetch seam。

---

## 3.3 AKShare：构建期 Registry + 文档双向棘轮

AKShare 当前构建脚本已经把导出接口、docs 元信息和 schema version 汇总成机器可读 registry，并通过 baseline 检测：

- 新接口没有文档；
- 文档存在但代码接口已消失；
- baseline 中已修复问题未清理。

### tstdx 应吸收

生成：

```text
artifacts/capabilities.json
artifacts/api_registry.json
artifacts/errors.json
```

由同一 registry 驱动：

- docs capability matrix；
- CLI help 部分元数据；
- REST/OpenAPI route contract 检查；
- MCP ToolSpec 校验；
- service parity test；
- deprecated API 清单。

---

## 3.4 NautilusTrader：Adapter 与 Data Engine 分离

NautilusTrader 明确把 HTTP/WS adapter、DataClient 与核心 DataEngine 分离；调用方使用统一 Engine API，而不是直接操作 transport。

### tstdx 应吸收

```text
Transport Client != Source Adapter != Query Engine
```

- `TdxClient`：低层协议 SDK；
- `TdxSource`：把 TDX 能力转成统一 Source contract；
- `UnifiedMarketDataService`：高层业务执行入口；
- Streaming 和历史请求共享 SourceManager 生命周期。

不需要引入完整 event bus / portfolio / execution engine。

---

## 3.5 Qlib：Provider + 参数指纹 + Cache 锁

Qlib 的 Provider/Cache 体系强调：

- provider 与 storage backend 分离；
- 对参数做确定性 hash/fingerprint；
- cache 写路径有锁，避免并发生成同一缓存；
- calendar/instrument 等稳定数据单独缓存。

### tstdx 应吸收

```text
QueryFingerprint
SingleFlight
cache write lock
stable metadata cache
```

但不把 Redis 等外部组件变成 tstdx 必需依赖。

---

## 3.6 pytdx / mootdx 类项目：保持低层 API 简洁

这些项目最值得保留的是：协议研究和本地 reader 可以直接使用，不要求所有调用都进入“大平台”。

因此 tstdx 最终保留两层产品：

```text
Low-level Protocol SDK
Unified Market Data Runtime
```

高层统一，不牺牲低层专业用户。

---

# 4. 目标架构：RuntimeContext + Application Core

建议新增应用层，不直接大改现有 protocol/client：

```text
tstdx/
  application/
    capabilities.py
    query.py
    result.py
    planner.py
    policy.py
    schema.py
  runtime/
    context.py
    health.py
    budget.py
  sources/
    base.py
    manager.py
    tdx.py
    web.py
    vipdoc.py
    replay.py
  service/
    market_data.py
```

> 目录只是目标形态，不要求一次移动全部源码。应先新增，再逐步把旧 Facade/Router 委托进去。

## 4.1 RuntimeContext

```python
@dataclass
class RuntimeContext:
    config: ConfigSnapshot
    capabilities: CapabilityRegistry
    sources: SourceManager
    health: SourceHealthRegistry
    caches: CacheManager
    executor: ExecutorPool
    metrics: Metrics
    clock: Clock
```

生命周期：

```text
CREATED -> RUNNING -> DRAINING -> CLOSED
```

硬约束：

- first-use lazy 初始化必须线程安全；
- `close()` 进入 DRAINING 后拒绝新请求；
- 等待 in-flight 在 deadline 内完成；
- Source/Cache/Executor 按 ownership 统一关闭；
- 不允许 Router/Fascade/HTTP 各自拥有另一套 client。

---

# 5. CapabilityRegistry：静态能力事实源

```python
CapabilitySpec(
    name="bars",
    schema_version=1,
    sources={
        "tdx": SourceCapability(
            exact=True,
            periods=("day", "1min", "5min", ...),
            adjustments=("raw",),
            supports_start=True,
            batch_limit=1,
        ),
        "web": SourceCapability(
            exact=True,
            periods=(...),
            adjustments=("raw", "qfq", "hfq"),
            supports_start=False,
            batch_limit=1,
        ),
        "local": SourceCapability(
            exact=True,
            periods=("day", "1min", "5min"),
            adjustments=("raw",),
            supports_start=True,
        ),
    },
)
```

## 5.1 必须描述的能力维度

```text
supported
exact / approximate / replay
periods
adjustments
supports_start
supports_batch
batch_limit
supports_streaming
supports_partial
freshness_class
required_optional_dependency
known command status
canonical schema version
```

## 5.2 Capability 与 Runtime Health 分离

动态健康状态：

```python
SourceHealth(
    state="healthy|degraded|open|half_open",
    latency_ewma_ms=...,
    error_rate=...,
    consecutive_failures=...,
    last_success_at=...,
    retry_after=...,
)
```

Planner 组合静态 capability + 当前 health 生成 QueryPlan。

---

# 6. QuerySpec / QueryPlan / QueryFingerprint

## 6.1 QuerySpec：稳定外部请求契约

建议：

```python
QuerySpec(
    capability="bars",
    symbols=("sh600519",),
    period="day",
    count=320,
    start=0,
    adjustment="raw",
    route_policy="fresh",
    source=None,
    allow_approximate=False,
    allow_partial=False,
    max_age=None,
    allow_stale=False,
    deadline_ms=5000,
    schema_version=1,
)
```

不建议把 output format 混入 source route 语义；输出格式属于边界 serializer。

## 6.2 QueryPlan：请求执行前先“编译”

```python
QueryPlan(
    query=...,
    normalized_symbols=...,
    candidates=(...),
    cache_policy=...,
    batch_groups=(...),
    budget=...,
    quality_requirement=...,
)
```

执行链：

```text
normalize once
    |
validate contract
    |
compute fingerprint
    |
cache probe
    |
resolve capability + health
    |
compile route candidates
    |
batch/chunk plan
    |
execute within budget
    |
normalize canonical records
    |
quality/provenance guard
    |
cache write
    |
serialize at boundary
```

## 6.3 QueryFingerprint

必须是确定性的 canonical key：

```text
capability
normalized symbols
period/start/count
adjustment
source scope / route semantics
schema version
freshness-affecting parameters
```

用于：

- Cache key；
- SingleFlight key；
- request-local memo；
- trace correlation；
- benchmark case ID。

---

# 7. ExecutionBudget：阻止 retry amplification

当前不同层都可能拥有 timeout/retry。v12 明确总预算：

```python
ExecutionBudget(
    deadline_monotonic=...,
    max_attempts=4,
    max_source_switches=2,
    max_host_switches=3,
)
```

每一次网络调用计算：

```text
remaining = deadline - monotonic_now
```

下层 timeout 不能超过 remaining。

硬规则：

- explicit source 失败后不得消耗 source-switch budget；
- retryable=False 立即停止当前 source retry；
- `CommandOffline` 不做网络 retry；
- 429 优先尊重 Retry-After / advice；
- fallback 前再次检查剩余预算；
- deadline 到期统一返回明确 Timeout/ErrorEnvelope，不再继续后台工作。

---

# 8. FailurePolicy：错误与路由决策解耦

保留现有 `RetryAdvice`，新增应用层结果：

```python
FailureDisposition(
    retry_same_source=False,
    switch_host=False,
    switch_source=True,
    retry_after=0.0,
    terminal=False,
    reason="command_offline",
)
```

输入：

```text
TdxError + RetryAdvice
Capability
RoutePolicy
SourceHealth
ExecutionBudget
Query quality requirement
```

这样：

- Transport 只处理 host 级恢复；
- Planner 处理 source 级恢复；
- Error class 不再硬编码具体路由顺序；
- explicit source 始终能 fail-closed。

---

# 9. 统一错误信息：保留 E1-E9，完善传播模型

## 9.1 不重写错误树

当前 E1-E9、`code`、`http_status`、`RetryAdvice`、`context`、`cause`、`to_dict()` 是稳定资产。

v12 禁止：

- 整体重新编号；
- 为新架构复制一套 AppError；
- 在 REST/WS/MCP 再造字符串错误体系。

## 9.2 合并重复 SourceUnavailable

```python
# legacy path only
from tstdx.errors import SourceUnavailable
```

`errors.SourceUnavailable(E7050)` 为唯一类。

Contract test：

```python
assert tstdx.sources.SourceUnavailable is tstdx.errors.SourceUnavailable
```

## 9.3 ErrorEnvelope

统一对外安全序列化：

```json
{
  "code": "E7050",
  "type": "SourceUnavailable",
  "message": "security_list 数据源当前不可用",
  "phase": "route",
  "capability": "security_list",
  "source": "tdx",
  "request_id": "...",
  "query_id": "...",
  "retryable": false,
  "retry_after": null,
  "partial": false,
  "alternatives": ["web"],
  "context": {}
}
```

新增 `phase` 建议枚举：

```text
validation
planning
cache_read
connect
send
receive
parse
normalize
route
cache_write
serialize
stream_dispatch
```

## 9.4 Public context 与 Diagnostic context 分开

严禁对外泄漏：

- token/cookie；
- 完整本地绝对路径；
- 原始请求头；
- private payload；
- Python traceback。

内部日志保留 `cause`/trace；对外只发 safe context + request_id。

## 9.5 Batch 错误语义

```python
BatchResult(
    items=(...),
    errors={"sh600001": ErrorEnvelope(...)},
    partial=True,
)
```

默认：

- 单个 symbol 失败不应让整个 batch 丢失已成功结果；
- `allow_partial=False` 时由 ContractGuard 把 partial 结果转 terminal error；
- 输入顺序必须可恢复；
- duplicate symbols 只请求一次，但 fan-out 回原输入位置。

## 9.6 错误映射统一

```text
Python exception / QueryResult
REST ErrorEnvelope
WS JSON-RPC error.data
MCP structured error
CLI human / --json
```

全部共享一个 serializer。

---

# 10. API 设计优化

## 10.1 明确两层公共 API

### Low-level

```text
TdxClient
AsyncTdxClient
Reader
Protocol / Codec
```

允许直接访问真实协议，不经过多源 fallback。

### High-level

```python
service.quotes(...)
service.bars(...)
service.minute(...)
service.trades(...)
service.security_list(...)
service.query(QuerySpec(...))
service.query_many([...])
```

所有高层方法只负责构造 QuerySpec。

## 10.2 route/source 参数收敛

建议新 API：

```text
policy="fresh|prefer_local|offline|legacy_auto"
source="tdx|web|local|replay|None"
```

兼容期继续接受：

```text
route="auto|tdx|web|local"
```

映射规则固定并发 deprecation warning；minor 版本不静默改变 legacy auto 顺序。

## 10.3 返回值统一

当前高层有 `Quote/Bar`，同时大量方法返回 dict。长期目标：

```text
Canonical Record -> Result[T]
```

至少做到：

- 同 capability 不因入口不同改变字段类型；
- missing 与真实 0 分开；
- amount 不可用用 `None + quality flag`，而不是伪装为 0；
- `as_format`/DataFrame/JSON 放到 serializer 边界；
- schema version 可机器读取。

## 10.4 Pagination / start / count 统一语义

所有时间序列定义同一窗口语义：

```text
start = 从最新向前跳过多少根
count = 返回多少根
```

不支持该语义的 Source 必须：

- Capability 标记 false；
- explicit source 立即 Validation/Capability error；
- auto policy 直接从候选中剔除；
- 禁止“忽略 start 后继续返回数据”。

---

# 11. Canonical Data + Provenance + Data Quality

建议所有内部结果携带：

```python
ResultMeta(
    source="tdx",
    exact=True,
    approximate=False,
    replay=False,
    stale=False,
    cache_status="miss",
    observed_at=...,
    source_timestamp=...,
    parse_tier="L1",
    confidence=1.0,
    attempts=(...),
)
```

## 11.1 为什么需要 parse tier/confidence

协议层已经有 L1/L2/L3 的概念，高层不能把“精确解析”和“generic/raw fallback”全部压成同一个无元数据结果。

建议：

- L1 默认 exact；
- L2 标记 degraded/confidence；
- L3 raw 不应伪装成 canonical model；
- Service 可以按 `quality_requirement` 决定接受还是报错。

## 11.2 Provenance

至少记录：

```text
source
provider/adapter
host（仅内部诊断）
cache origin
replay sample id
adjustment
schema version
```

这样跨 TDX/Web/Local fallback 后，调用方可以知道数据从哪里来，而不是只拿一份“看起来一样”的 dict。

---

# 12. SourceManager：生命周期与资源复用

```text
SourceManager
  +-- TdxQuotationSource
  +-- TdxExtendedSource
  +-- TdxGoodsSource
  +-- TdxMacSource
  +-- TdxF10Source
  +-- WebSource
  +-- VipdocSource
  +-- GoldenReplaySource
```

## 12.1 持久资源

- TDX ConnectionPool 跨 query 复用；
- Web HTTP session/connection pool 跨 query 复用；
- source health/circuit 跨 query 复用；
- parser registry/period mapping 复用；
- family client 不由 endpoint 临时创建。

## 12.2 Source circuit 与 Host circuit 分层

```text
Host circuit   -> 某个 TDX IP:port
Source circuit -> TDX/Web/Vipdoc 某能力整体
```

Planner 不复制 ConnectionPool 的 host ranking；Transport 不决定“下一数据源是什么”。

---

# 13. Cache V2：一致性优先，再谈命中率

## 13.1 分层

```text
L0 Request-local memo
L1 Process memory TTL cache
L2 SQLite/disk canonical cache
GoldenReplaySource（不是 performance cache）
```

不要求 Redis。

## 13.2 Key

```text
QueryFingerprint
+ source_scope（explicit source）
+ canonical schema version
```

## 13.3 Kline canonical raw

推荐：

```text
Raw Bars Cache
  + Corporate Action Cache
        |
        v
Adjustment Engine
```

避免 raw/qfq/hfq 互相污染。

## 13.4 Freshness

实时行情必须明确：

```text
max_age
allow_stale=False
stale_reason
```

如果 Source 不可达，不能静默把无限陈旧 Quote 当实时结果。

## 13.5 SingleFlight 防击穿

相同 QueryFingerprint 同时到达：

```text
100 requests
    |
SingleFlight
    |
1 upstream request
    |
fan-out 100 waiters
```

只合并**语义完全相同**的请求。

## 13.6 Cache invariant

```text
cache off == cache miss == cache hit
```

业务数据、排序、窗口、复权必须一致；只能 `ResultMeta.cache_status` 不同。

---

# 14. BatchPlanner：批量请求是性能主战场

当前批量功能需要从“循环调用单个接口”升级为执行计划。

## 14.1 Batch 过程

```text
normalize input once
    |
deduplicate symbols
    |
resolve source per capability
    |
group by source / market / request type
    |
chunk by provider batch limit
    |
run bounded concurrency
    |
normalize
    |
fan-out to original order
```

## 14.2 Provider-specific batch limit

Capability 声明：

```text
max_symbols_per_request
max_rows_per_request
supports_multi_symbol
supports_parallel_requests
```

不要把 `100`、`800` 等 magic number 分散在 HTTP、Client、Facade。

## 14.3 Partial failure

例如 100 symbols 中 3 个失败：

- 已成功 97 个不能丢；
- 错误按 symbol 返回；
- source/attempt 元数据保留；
- `allow_partial=False` 由高层决定是否整体失败。

---

# 15. 执行效率优化

## 15.1 第一优先级：减少重复 IO

收益最高的项目：

1. persistent TDX pool；
2. persistent Web session；
3. BatchPlanner；
4. symbol dedup；
5. SingleFlight；
6. Cache V2；
7. source health 快速跳过已 open source。

## 15.2 第二优先级：减少 Python 对象往返

当前 Router/Facade 边界存在 dict/model 转换。

目标：

```text
parser -> canonical record -> service result
                           -> serializer only at boundary
```

避免：

- `dict(row)` 多次复制；
- Quote -> dict -> Quote；
- Bar -> dict -> Bar；
- batch 中重复 normalize_symbol；
- DataFrame 提前创建。

## 15.3 第三优先级：Parser/Codec profile-guided 优化

只有 benchmark 证明 CPU 是瓶颈后再考虑：

- `memoryview` / `struct.unpack_from`；
- 减少 payload slice copy；
- 预编译/缓存 period-to-command mapping；
- 批量 decode 的临时对象复用。

不允许为了“零拷贝”绕过完整性校验。

## 15.4 CPU / IO 隔离

- IO concurrency 由 per-source semaphore/worker budget 控制；
- 大量 DataFrame/serialization 不占用 network dispatch 线程；
- TaskManager 和 Async sync-adapter 共享有上限 executor，而不是无限 thread-per-request。

---

# 16. Async 架构

短期：

```text
AsyncMarketDataService
  -> shared QueryPlanner
  -> async-capable SourceAdapter
  -> sync-only adapter through bounded executor
```

需要：

- first-use lock；
- lifecycle state；
- close 与 in-flight 协调；
- cancellation token；
- deadline propagation；
- bounded executor；
- circuit/health 状态并发安全。

长期无需强制所有 Source 原生 async；低频本地 reader 用 bounded thread adapter 即可。

---

# 17. Streaming v3：Scheduler + Shared Subscription

## 17.1 Subscription state

```python
SubscriptionState(
    key=...,
    symbols=...,
    interval=...,
    next_due=...,
    diff_only=...,
    max_queue=...,
    consumer=...,
)
```

Scheduler 用 monotonic clock，建议 min-heap/时间轮而不是每轮全部扫 + min interval sleep。

## 17.2 Shared upstream fetch

```text
subscriptions due now
       |
union + dedup symbols
       |
one/batched upstream fetch
       |
DeltaMerger
       |
fan-out by subscriber
```

相同 symbol 多订阅只抓一次。

## 17.3 Poll / Push backend

```text
StreamBackend
  +-- TdxPollingBackend(0x0530)
  +-- TdxPushBackend(0x0547 experimental)
  +-- WebPollingBackend
```

`PushChannel` 不再作为平行正式系统存在。

## 17.4 Reconnect / Gap / Watermark

需要统一状态：

```text
last_seen
sequence/watermark
missing_rounds
reconnect_count
resubscribe state
```

Push 重连后必须重新订阅；检测到 gap 时根据 capability 决定是否能补历史。

## 17.5 真正背压

```text
fetch scheduler
   |
bounded event queue
   |
consumer dispatcher
```

慢 callback 不阻塞 fetch scheduler。

可配置策略：

```text
drop_oldest
latest_only
block_with_timeout
fail_subscription
```

并有对应 metrics。

---

# 18. HTTP / WS / MCP / CLI 全部变成“协议适配器”

目标：

```text
HTTP --\
WS -----+--> UnifiedMarketDataService
MCP ----+
CLI ----/
```

Integration 层只允许：

- 输入解析；
- auth/security；
- request model；
- 调用 Service；
- serializer/error mapper；
- protocol-specific streaming response。

禁止：

- `new TdxClient()`；
- `WebQuoteSession.xxx()`；
- 自己判断 fallback；
- 自己 catch 一套错误；
- MCP 用 `use_facade` 决定数据链。

## 18.1 REST

优先修：

- snapshot/auction signature；
- minute_history date；
- binary download 用 `application/octet-stream`/stream response，不把 bytes 塞 JSON；
- Goods/Ex/Mac/F10 通过 Service capability；
- `/query` 使用 Capability/API registry，不允许任意方法名成为半公开 RPC。

## 18.2 MCP

ToolSpec 从 registry 校验/部分生成：

```text
tool name
capability
input schema
output schema
availability
```

不再维护 `use_facade=True/False` 这种执行链开关。

## 18.3 WebSocket

历史请求与 subscription 都共享同一个 RuntimeContext/SourceManager。

---

# 19. Config：有效配置快照，而不是运行时多处重新解释

## 19.1 ConfigSnapshot

Service 创建时解析一次 effective config：

```text
file
+ env
+ constructor overrides
+ defaults
= ConfigSnapshot
```

SourceManager 使用同一 snapshot。

## 19.2 非法配置 fail-fast

当前 `build_router()` 遇到配置异常会 warning 后回默认 Router。v12 改为：

- “配置文件不存在/可选配置未提供”允许默认；
- “用户提供了非法配置”必须 ValidationError；
- 禁止静默换成另一套 source order/enable 状态。

## 19.3 清理重复开关

统一：

- Web total gate；
- Sources enabled；
- route policy；
- cache enable；
- compatibility flags。

每个配置字段必须有消费者测试。

---

# 20. Observability：从“有 metrics”升级到“执行链可定位”

## 20.1 Metrics

建议：

```text
query_total
query_latency_seconds
query_partial_total
source_attempt_total
source_success_total
source_failure_total
source_fallback_total
source_circuit_state
cache_hit_total
cache_miss_total
cache_stale_total
singleflight_join_total
batch_size
stream_fetch_total
stream_drop_total
stream_gap_total
task_active
```

## 20.2 Cardinality guard

Prometheus label 禁止直接放：

```text
symbol
query_id
request_id
raw URL
error message
```

允许低基数 label：

```text
capability
source
error_code
period class
cache status
```

symbol/request_id 放结构化日志或 trace。

## 20.3 Structured log

```text
request_id
query_id
capability
source
policy
attempt
host（内部）
cache_status
latency_ms
error_code
```

---

# 21. TaskManager v2

替换 thread-per-task：

```text
TaskManager
  +-- bounded ThreadPoolExecutor
  +-- bounded pending queue
  +-- Future
  +-- CancellationToken
  +-- TaskRecord
```

状态：

```text
pending
running
cancel_requested
cancelled
done
failed
expired
```

规则：

- cancel_requested 在 worker 真正退出前仍计 active；
- running/cancel_requested 不允许 delete；
- finished 才允许 purge；
- deadline 到期 token 置取消；
- source 支持 cooperative cancellation 时透传；
- shutdown 时 DRAINING，停止接收新任务。

---

# 22. Registry / Schema / Docs 治理

## 22.1 机器可读 registry

建议生成：

```text
docs/generated/capabilities.json
docs/generated/api_registry.json
docs/generated/errors.json
```

不要手工维护接口数量。

## 22.2 双向 ratchet

CI 校验：

```text
public high-level API - registry == empty
registry - public high-level API == empty
registry documented=false == baseline only
fixed baseline entries must be removed
```

## 22.3 Schema version

每个 capability 公开稳定 schema version。

Breaking schema change：

- 必须 bump schema version；
- 有 migration note；
- 服务面 parity fixture 同步。

---

# 23. Provider Conformance Tests

新增每个 Source 都必须通过的统一测试：

```text
prepare contract
normalize contract
symbol order contract
empty response contract
missing field contract
error mapping contract
deadline contract
close/lifecycle contract
batch limit contract
```

Source adapter 新增时，先过 conformance，再接入 Registry。

---

# 24. Architecture Contract Tests

## 24.1 Explicit source invariant

```text
source=tdx    -> 只能 TdxSource
source=web    -> 只能 WebSource
source=local  -> 只能 VipdocSource
source=replay -> 只能 ReplaySource
```

## 24.2 Cache equivalence

```text
cache off == miss == hit
```

覆盖：

```text
raw/qfq/hfq
start 0/>0
day/1m/5m
explicit source/policy
```

## 24.3 Service parity

同一 fake source：

```text
Python
Async
REST
WS
MCP
```

数据 schema 与 error code 一致。

## 24.4 Architecture import guard

`integration/http*`, `integration/ws*`, `integration/mcp*` 禁止直接导入具体：

```text
TdxClient
WebQuoteSession
GoodsClient
ExMarketClient
MacClient
F10Client
```

只允许高层 Service/Serializer。

## 24.5 Semantic orphan audit

验证：

```text
config field -> runtime consumer
capability -> source adapter
source adapter -> registry
registry -> service exposure
service exposure -> docs/test
```

---

# 25. Benchmark Gates

重构前先保存基线，不先定拍脑袋阈值。

## 25.1 Cases

```text
quote single
quote 100 symbols
quote 1000 symbols
bars 320
bars 2000 pagination
cache hit
cache miss
source failover
cold start
warm persistent connection
REST throughput
WS subscription 100 symbols
WS subscription 1000 symbols
MCP tool latency
```

## 25.2 指标

```text
p50 / p95 / p99 latency
requests/sec
upstream request count
connection reuse ratio
allocations / peak memory
cache hit ratio
singleflight join ratio
error/fallback count
```

## 25.3 性能优化判定

优先看：

```text
减少上游请求数
减少建连数
减少重复转换
减少重复 symbol normalize
减少无效 fallback/retry
```

而不是先做微观 parser 手工优化。

---

# 26. 实施路线

## Phase 0 — Correctness Freeze

目标：先消除确定性错误，不大改架构。

1. 修 M5 Reader；
2. local lc1/lc5 接通；
3. KlineCache 对 start/adjust/explicit source 先安全 bypass；
4. duplicate SourceUnavailable alias E7050；
5. security_list explicit source fail-closed；
6. `_get_router` config/timeout/web_sources 旁路先修；
7. build_router 非法配置 fail-fast；
8. REST snapshot/auction/date/binary contract；
9. TaskStore cancel/delete 资源漏洞；
10. `adjusted_bars(events=[])`；
11. broad exception fallback 收窄；
12. 给 legacy auto 固化 compatibility fixtures。

出口：原测试 + P0 regression 全绿。

---

## Phase 1 — Application Contracts

新增：

```text
RuntimeContext skeleton
CapabilityRegistry
SchemaRegistry
QuerySpec
QueryPlan
QueryFingerprint
QueryResult / BatchResult / ResultMeta
ErrorEnvelope
ExecutionBudget
FailurePolicy
```

首批迁移：

```text
quotes
bars
snapshot
minute
trades
security_list
```

---

## Phase 2 — SourceManager + Provider Adapter

完成：

- prepare/fetch/normalize contract；
- persistent TDX/Web；
- SourceHealthRegistry；
- Planner 唯一路由；
- family client lifecycle；
- Router `last_errors/last_source` 删除；
- legacy Facade 变 thin adapter。

出口：可观测到连接复用，且同 QuerySpec 的 attempts 可完整审计。

---

## Phase 3 — Cache V2 + BatchPlanner + SingleFlight

完成：

- QueryFingerprint CacheKey；
- canonical raw bars；
- freshness/stale；
- GoldenReplaySource 分离；
- batch grouping/chunk；
- per-source concurrency；
- SingleFlight；
- partial-error result。

出口：Cache/Batch contract 全绿，上游请求数显著下降且可测。

---

## Phase 4 — Service Surface Unification

完成：

```text
HTTP -> Service
WS   -> Service
MCP  -> Service
CLI high-level -> Service
```

- ErrorEnvelope 单一 serializer；
- MCP ToolSpec/REST contract 由 registry 校验；
- binary response 修正；
- Goods/Ex/Mac/F10 统一能力暴露。

---

## Phase 5 — Async / Streaming / TaskManager

完成：

- Async lifecycle；
- cancellation/deadline；
- bounded executor；
- StreamScheduler；
- subscription dedup/refcount；
- poll/push backend；
- true backpressure；
- TaskManager v2。

---

## Phase 6 — Compatibility / Orphan / Docs

完成：

- deprecated facade 迁 compat；
- orphan config 删除；
- Synthetic 隔离 testing/experimental；
- PushChannel 状态收口；
- generated registry/docs；
- README/DESIGN/Feature Map 同步；
- 历史计划标记 superseded/archive。

---

# 27. 并行轨道

## Track R — Protocol Reality

继续真实主站/Golden 定标：

- 已知 offline/degraded 命令；
- 0x0547 / 7727 / 特殊记录布局；
- 指数 K 线等悬案。

架构层不得用“猜测代码修复”替代真机证据。

## Track H — Host Health

- hosts audit；
- RankingStore；
- family-specific candidate；
- scheduled CI probe；
- 新主站先证据化再入默认池。

## Track Q — Quality/Release

- Windows 支持矩阵；
- scripts lint/format；
- coverage 不降低并逐步向 80% 提升；
- registry/docs check；
- benchmark gate；
- install smoke；
- native deprecation timeline。

---

# 28. 推荐提交拆分

```text
1. fix: close local reader and cache semantic gaps
2. fix: enforce explicit source and canonical source errors
3. fix: align HTTP contracts and bounded task lifecycle
4. refactor: add runtime query result and capability contracts
5. refactor: introduce execution budget and failure policy
6. refactor: introduce source manager and provider adapters
7. refactor: compile core queries through canonical planner
8. refactor: add semantic cache batch planner and singleflight
9. refactor: unify HTTP WS MCP execution surface
10. refactor: harden async stream scheduler and task cancellation
11. chore: generate registry error catalog docs and architecture gates
12. perf: establish and enforce runtime benchmark baselines
```

每个提交必须：

- 可独立测试；
- 不降低门禁；
- 可回滚；
- 不顺手修改 protocol ledger 事实。

---

# 29. Definition of Done

## 数据正确性

- [ ] `.day/.lc1/.lc5` Reader/Source/Service 对拍一致；
- [ ] start/count 不因 cache/source 改变；
- [ ] raw/qfq/hfq 不污染；
- [ ] explicit source 不跨源；
- [ ] approximate/replay/stale 不会静默伪装 exact/live；
- [ ] missing != 0；
- [ ] parse tier/confidence 可传播；
- [ ] partial batch 不丢成功结果和错误。

## 架构

- [ ] 单一 RuntimeContext；
- [ ] 单一 CapabilityRegistry；
- [ ] 单一 QuerySpec/QueryPlan；
- [ ] 单一 QueryFingerprint；
- [ ] 单一 SourceManager；
- [ ] 单一 Planner；
- [ ] E1-E9 唯一错误树；
- [ ] 单一 FailurePolicy；
- [ ] 单一 ResultMeta/ErrorEnvelope；
- [ ] integration 不再路由 source。

## 生命周期

- [ ] TDX pool 跨 query 复用；
- [ ] Web session 跨 query 复用；
- [ ] SourceHealth 跨 query 复用；
- [ ] close/in-flight 安全；
- [ ] invalid config 不静默回默认；
- [ ] router 无请求级共享 mutable state。

## 效率

- [ ] BatchPlanner 自动分组/chunk；
- [ ] duplicate symbols 只请求一次；
- [ ] SingleFlight 合并等价并发请求；
- [ ] retry 使用总 deadline；
- [ ] internal canonical record 不反复 dict/model 转换；
- [ ] DataFrame/JSON lazy serialization；
- [ ] benchmark 有 cold/warm/cache/failover 基线。

## 服务面

- [ ] REST/WS/MCP/CLI 高层只依赖 Service；
- [ ] Python/Async/REST/WS/MCP schema 一致；
- [ ] error code/error envelope 一致；
- [ ] Goods/Ex/Mac/F10 capability 可审计；
- [ ] binary response 契约正确；
- [ ] raw/debug 入口明确隔离。

## Streaming/Task

- [ ] 每订阅 interval 独立；
- [ ] due symbol union+dedup；
- [ ] shared upstream subscription/fetch；
- [ ] push/poll 共用 backend contract；
- [ ] callback 不阻塞 fetch；
- [ ] gap/watermark/reconnect 有状态；
- [ ] cancel 不绕过 task limit；
- [ ] 无 orphan execution。

## 治理

- [ ] public API ↔ registry ↔ docs 双向一致；
- [ ] provider conformance 全绿；
- [ ] semantic orphan audit 全绿；
- [ ] architecture import gate 全绿；
- [ ] Windows 矩阵满足支持范围；
- [ ] benchmark gate 存在；
- [ ] 真机协议悬案单独跟踪；
- [ ] v12 是唯一 Current 方案。

---

# 30. 明确不做

1. 不把 tstdx 扩成实盘交易系统；
2. 不重写已验证 protocol/codec/golden；
3. 不重新设计整棵 E1-E9 错误树；
4. 不引入重型 DI framework；
5. 不要求 Redis/Kafka；
6. 不为了统一删除低层 TdxClient；
7. 不为了性能跳过完整性校验；
8. 不让 Synthetic 进入生产默认 fallback；
9. 不让 approximate/replay/stale 静默替代 exact/live；
10. 不在 minor 版本静默改变 legacy auto 顺序；
11. 不先做 speculative micro-optimization，再找 benchmark 证明；
12. 不把 Source health 和 Capability 混成同一个可变表。

---

# 31. 最终产品定位

重构后，tstdx 应明确分成两层：

## Low-level Protocol SDK

```text
TdxClient / AsyncTdxClient
Reader
Protocol / Codec / CommandLedger
```

面向协议研究、低层调用和调试。

## Unified Market Data Runtime

```text
RuntimeContext
UnifiedMarketDataService
CapabilityRegistry / SchemaRegistry
QuerySpec / QueryPlan / QueryResult
ExecutionBudget / FailurePolicy
SourceManager / SourceHealthRegistry
CacheManager / BatchPlanner / SingleFlight
StreamingScheduler
```

面向业务应用、CLI、REST、WS、MCP、多数据源统一访问。

新增一个新 Source 时正常流程只应：

1. 实现 Source Adapter；
2. 注册 Capability；
3. 注册生命周期 factory；
4. 实现 canonical normalize；
5. 声明 batch/rate/freshness 约束；
6. 通过 Provider Conformance；
7. 增加 contract fixture/benchmark；
8. 运行 registry/doc check。

**不应该再修改：**

```text
Facade route if/else
HTTP fallback if/else
WS direct client dispatch
MCP use_facade flag
多个独立 retry loop
多个 cache source order
每入口单独 error string
```

最终验收标准：

> **底层协议保持稳定；高层执行彻底收敛。相同 Query 在任何入口都得到相同数据契约、相同错误语义和可解释的数据来源；性能来自资源复用、批量规划、缓存一致性和并发合并，而不是通过隐藏错误或改变数据口径换取。**

---

# Appendix A — 对标来源

本方案重点参考以下公开项目的架构模式（只吸收与 tstdx 有关的工程思想）：

- CCXT — https://github.com/ccxt/ccxt
- OpenBB — https://github.com/OpenBB-finance/OpenBB
- AKShare — https://github.com/akfamily/akshare
- NautilusTrader — https://github.com/nautechsystems/nautilus_trader
- Qlib — https://github.com/microsoft/qlib
- pytdx / mootdx 类 TDX 协议项目

对标原则：**借鉴能力声明、Provider/Engine 分层、Registry、Cache/Query 指纹、统一错误与生命周期，不复制交易系统或重型基础设施。**
