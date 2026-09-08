# tstdx Industry Benchmark Refactor Plan v11

> Branch: `refactor/industry-benchmark-v11`  
> Baseline: tstdx `v1.4.0` current architecture  
> Updated: 2026-09-08  
> Status: **source-audited refactor execution baseline**

## 0. Executive summary

`tstdx` 已经不再是单一的 TDX 协议客户端，而是由 **TDX 二进制协议、连接池、多数据源路由、本地 vipdoc reader、Web 数据源、统一 Facade、缓存、Streaming、REST/WS/MCP、输出与可观测性** 组成的市场数据基础设施。

本轮源码审计确认：

- 底层 `codec / protocol / transport / client` 主体能力较完整，不应大规模推倒重写；
- 当前主要风险集中在 `facade / sources / cache / integration / streaming` 之间存在多套规则；
- 多个功能模块虽然存在，但没有真正接入统一主链；
- 部分配置、路由、缓存和服务接口存在确定性行为偏差；
- 当前还不能宣称“主体流程全部联通、核心链路不存在断链、没有孤儿逻辑”。

本次 v11 重构的总目标不是继续增加接口，而是建立：

1. **单一 Query 执行模型**；
2. **单一 Capability Registry**；
3. **单一 Canonical Router**；
4. **单一 Source 生命周期管理器**；
5. **单一错误体系**；
6. **缓存不改变业务语义**；
7. **Python / Async / CLI / REST / WS / MCP 行为一致**；
8. **无孤儿能力、无隐式跨源、无重复路由、无请求间状态污染**。

---

## 1. 当前架构基线

当前主体链路可以抽象为：

```text
External API
  |
  +-- Python API
  +-- Async API
  +-- CLI
  +-- REST
  +-- WebSocket
  +-- MCP
  |
  v
UnifiedQuoteAPI / direct clients / integration-specific routing
  |
  v
DataSourceRouter / RouteSelector / manual fallback
  |
  +-- TDX
  +-- Web
  +-- vipdoc
  +-- cache
  +-- golden/synthetic
  |
  v
TdxClient / Web adapters / Reader
  |
  v
ConnectionPool / Protocol / Codec
```

底层已经具有：

- TDX 7709/7727 等协议族；
- L1/L2/L3 parser 分层；
- Command Ledger；
- ConnectionPool / host ranking / circuit breaker；
- sync/async clients；
- `.day/.lc1/.lc5/.dat/gpcw` reader；
- 多 Web 数据源；
- Quote/Kline cache；
- Streaming；
- REST/WS/MCP；
- metrics/exporters；
- 输出到 CSV/Parquet/DuckDB/DataFrame。

这些应视为 **稳定资产**，重构重点是把它们接入同一执行内核。

---

## 2. 重构边界与不可破坏约束

### 2.1 不推倒重写的模块

本轮默认不重写：

- `tstdx.codec`
- `tstdx.protocol` 的已验证命令事实
- `tstdx.transport.ConnectionPool`
- 已稳定 `TdxClient` 底层协议调用
- L1/L2/L3 parser 机制
- Command Ledger 中真实 `online/offline/degraded` 事实

### 2.2 不允许的“修复方式”

禁止：

- 把已标记 offline 的命令强行改成 online；
- 通过降低测试门禁掩盖问题；
- 通过 `except Exception: pass` 掩盖内部 bug；
- 用 Web 近似数据偷偷替代精确 TDX 数据；
- 为了兼容继续增加第三、第四套路由逻辑；
- 在 HTTP/WS/MCP 中重新实现数据源选择；
- 使用 synthetic 数据作为真实行情生产兜底。

---

## 3. 本轮源码审计确认的问题清单

### 3.1 P0：数据正确性 / 确定性断链

| ID | 问题 | 影响 |
|---|---|---|
| P0-01 | 本地 `M5` reader 分派错误 | `.lc5` 可能被 `DayBarReader` 解析 |
| P0-02 | Facade local bars 仅允许 day | `.lc1/.lc5` 已实现但统一 API 不通 |
| P0-03 | KlineCache 未编码 `start` 语义 | 可能返回错误窗口 |
| P0-04 | KlineCache 未编码 `adjust` 语义 | raw/qfq/hfq 可能污染 |
| P0-05 | cache 在 route/source 判断前命中 | 显式 route 可能拿到别的 source 数据 |
| P0-06 | `_get_router()` 不透传 `timeout/web_sources` | UnifiedQuoteAPI 用户配置失效 |
| P0-07 | Facade 未统一使用 configured router factory | 全局 source/cache 配置存在旁路 |
| P0-08 | Facade 与 DataSourceRouter 存在两套 auto 顺序 | 同一调用语义不一致 |
| P0-09 | `security_list()` 违反 explicit route contract | `route="tdx"` 失败后仍可能降级 Web |
| P0-10 | 存在两个不同 `SourceUnavailable` | catch/error code/HTTP 映射不统一 |
| P0-11 | REST `/snapshot` 参数与 client 签名错位 | list 传入单 symbol 方法 |
| P0-12 | REST `/auction_snapshot` 同类签名错位 | 请求可直接断链 |
| P0-13 | HTTP 默认只构造 `TdxClient` | Goods/Ex/Mac/F10 已注册端点默认不可用 |
| P0-14 | HTTP 默认主链绕过 Unified facade/router | 已实现 fallback 无法生效 |
| P0-15 | TaskStore cancel 不终止线程 | 可绕过 `max_tasks` 持续产生后台执行 |
| P0-16 | 删除 running task 不终止执行 | 任务成为不可观测 orphan execution |
| P0-17 | `adjusted_bars(events=[])` 语义错误 | 显式空事件被当成未提供，触发额外联网 |

### 3.2 P1：架构一致性 / 生命周期 / 并发问题

| ID | 问题 | 影响 |
|---|---|---|
| P1-01 | Router 每请求创建/关闭 TdxClient | ConnectionPool 无法跨请求复用 |
| P1-02 | Router 每请求创建 Web client | HTTP keep-alive 无法复用 |
| P1-03 | Facade 自有 client 与 Router client 并存 | 双生命周期、双状态 |
| P1-04 | Router `last_errors/last_source` 是实例可变状态 | 请求间污染、并发竞态 |
| P1-05 | Facade/Router 广泛 catch `Exception` 后 fallback | TypeError/AttributeError 等程序 bug 被掩盖 |
| P1-06 | Async facade 通过共享 sync facade + `to_thread()` | lazy init / circuit / close 存在竞态 |
| P1-07 | RouteSelector 的 circuit dict 无统一并发保护 | 并发状态不确定 |
| P1-08 | Web-only 方法直接 new Session | timeout/web_sources/config 再次旁路 |
| P1-09 | `f10()` 与 `f10_catalog()` 错误转换不统一 | 相同能力不同错误语义 |
| P1-10 | HTTP / WS / MCP 各自决定 client/facade | 服务面行为漂移 |
| P1-11 | MCP 部分工具直接走 offline TDX 命令 | 已有 facade fallback 未接入 |
| P1-12 | WS `security_list` 等仍直连 TdxClient | 服务层主链不一致 |

### 3.3 P1：Streaming / task 调度

| ID | 问题 | 影响 |
|---|---|---|
| P1-13 | Subscription interval 使用全局最小值 | 低频订阅被高频轮询 |
| P1-14 | 多订阅 symbol 不去重 | 重复请求、额外网络负载 |
| P1-15 | callback 与 polling 主循环耦合 | 慢消费者拖慢整个流 |
| P1-16 | `PushChannel` 与 `QuoteStream` 两套实时路径 | 主 Streaming backend 不统一 |
| P1-17 | backpressure 更接近批内限流 | 尚未形成真正 producer/consumer 隔离 |

### 3.4 P2：孤儿逻辑 / 兼容面 / 文档漂移

| ID | 问题 | 影响 |
|---|---|---|
| P2-01 | `SecurityConfig.credential_backend` 仍存在 | CredentialStore 已删除，配置成为 orphan |
| P2-02 | CompatibilityConfig 部分字段缺少消费者证明 | 配置面膨胀 |
| P2-03 | BinaryClient / BridgeClient / HqClient 等平行 Facade | 公共入口过多、职责重复 |
| P2-04 | deprecated 文案版本体系与实际 1.x 不一致 | 迁移契约不清晰 |
| P2-05 | README / REST 端点数等文档有漂移 | 文档不能作为唯一事实源 |
| P2-06 | Golden cache 与 performance cache 同处 source 概念 | cache/source 语义混杂 |
| P2-07 | Synthetic 作为 source 排序项容易被误用 | 生产数据语义风险 |

---

## 4. 第一原则：建立单一执行主干

重构后的唯一主体链路：

```text
┌───────────────────────────────────────────────────────┐
│ External Interfaces                                   │
│ Python / Async / CLI / REST / WS / MCP               │
└──────────────────────────┬────────────────────────────┘
                           │
                           v
┌───────────────────────────────────────────────────────┐
│ UnifiedMarketDataService                              │
│ QuerySpec / CapabilityRegistry / ContractGuard       │
└──────────────────────────┬────────────────────────────┘
                           │
                           v
┌───────────────────────────────────────────────────────┐
│ QueryPlanner / Canonical Router                       │
│ RoutePolicy / Circuit / Retry / Error Aggregation    │
└──────────────┬──────────────┬──────────────┬──────────┘
               │              │              │
               v              v              v
          TdxSource       WebSource      VipdocSource
               │              │              │
               v              v              v
         SourceManager    HTTP Session    Reader
               │
               v
        ConnectionPool
               │
               v
      Protocol / L1-L3 / Codec
```

横切层：

```text
CachePolicy
CircuitPolicy
RetryPolicy
ContractGuard
Observability
Provenance
StreamingScheduler
```

---

## 5. Capability Registry：能力事实 SSOT

所有公共能力都必须有统一声明：

```python
CapabilitySpec(
    name="bars",
    sources={
        "tdx": SourceCapability(
            periods=("day", "1min", "5min", ...),
            adjustments=("raw",),
            supports_start=True,
            equivalence="exact",
        ),
        "web": SourceCapability(
            periods=(...),
            adjustments=("raw", "qfq", "hfq"),
            supports_start=False,
            equivalence="exact",
        ),
        "local": SourceCapability(
            periods=("day", "1min", "5min"),
            adjustments=("raw",),
            supports_start=True,
            equivalence="exact",
        ),
    },
)
```

Capability Registry 必须解决：

- 哪个 source 支持哪种 capability；
- period 范围；
- raw/qfq/hfq；
- `start/count`；
- exact / approximate；
- source 当前状态；
- command offline/degraded 事实；
- 是否允许 fallback。

以后禁止在每个 facade method 中重复写：

```python
if route == ...
try:
    ...
except CommandOffline:
    ...
```

---

## 6. QuerySpec：统一请求模型

所有外部入口统一转换成 `QuerySpec`：

```python
QuerySpec(
    capability="bars",
    symbols=("sh600519",),
    period="day",
    count=320,
    start=0,
    adjustment="raw",
    route_policy="auto",
    allow_approximate=False,
    timeout=3.0,
)
```

建议字段：

```text
query_id
capability
symbols
market
period
count
start
adjustment
route_policy
source_scope
allow_approximate
timeout
metadata
```

Python API：

```python
service.bars(...)
```

内部只负责构造：

```python
service.query(QuerySpec(...))
```

HTTP / WS / MCP 也必须进入同一个 query engine。

---

## 7. QueryResult：替代请求级 mutable router state

禁止继续依赖：

```text
router.last_source
router.last_errors
```

新内部结果：

```python
QueryResult(
    data=...,
    source="tdx",
    attempts=(...),
    warnings=(...),
    cache_status="hit|miss|bypass",
    provenance={...},
)
```

外层兼容 API 可以默认返回 `data`，高级调用允许返回 metadata。

收益：

- 无请求间污染；
- 并发安全；
- provenance 可观察；
- fallback 链可审计；
- HTTP/WS/MCP 可统一暴露诊断信息。

---

## 8. Canonical Router：只保留一套路由

### 8.1 路由策略

建议正式定义：

#### `auto`

适合在线新鲜数据：

```text
tdx -> web -> local -> replay
```

#### `prefer_local`

适合本地历史数据优先：

```text
local -> tdx -> web -> replay
```

#### `offline`

```text
local -> replay
```

#### explicit source

```text
route="tdx"
route="web"
route="local"
route="replay"
```

硬约束：

> 显式 route 只能使用指定 Source；失败直接返回错误，禁止隐藏 fallback。

### 8.2 exact / approximate

Capability 必须标记：

```text
exact
approximate
```

默认：

```python
allow_approximate=False
```

例如 TDX 板块行情 offline 时，Web 板块排名不能默认作为同义替代。

---

## 9. SourceManager：统一客户端生命周期

Router 以后**只选择 Source，不创建客户端**。

新结构：

```text
SourceManager
 |
 +-- TdxSource
 |     +-- persistent TdxClient
 |     +-- ConnectionPool
 |
 +-- GoodsSource
 +-- ExtendedSource
 +-- MacSource
 +-- F10Source
 |
 +-- WebSource
 |     +-- persistent HTTP session
 |
 +-- VipdocSource
 +-- GoldenReplaySource
```

生命周期：

```text
UnifiedMarketDataService.__init__
       |
       v
lazy Source creation
       |
       v
reuse across queries
       |
       v
service.close()
       |
       v
close all owned sources
```

必须修复当前：

```text
Router -> new TdxClient -> query -> close
Router -> new WebClient -> query -> close
```

否则 ConnectionPool、keep-alive、host health、source circuit 都不能真正跨请求复用。

---

## 10. Source abstraction

统一接口示例：

```python
class MarketDataSource(Protocol):
    @property
    def name(self) -> str: ...

    def capabilities(self) -> frozenset[str]: ...

    def execute(self, query: QuerySpec) -> QueryResult: ...

    def close(self) -> None: ...
```

TDX 内部可继续按协议族拆 client，但不暴露到业务路由层。

---

## 11. Cache V2：缓存绝不能改变业务语义

### 11.1 区分两种“缓存”

当前需要彻底拆开：

#### Performance Cache

```text
QuoteCache
KlineCache
```

属于执行优化层。

#### Golden Replay

```text
payload.bin / meta.json
```

本质是一个离线真实数据 Source。

应重命名为：

```text
GoldenReplaySource
```

### 11.2 CacheKey

建议：

```python
CacheKey(
    capability="bars",
    symbol="sh600519",
    period="day",
    adjustment="raw",
    source_scope="tdx",
)
```

显式 route 下必须限定 `source_scope`。

### 11.3 `start/count` 语义

对于时间序列，优先缓存 canonical series：

```text
raw canonical series
      |
      v
start/count slicing
```

而不是在 cache hit 时忽略 `start`。

### 11.4 复权建议

长期推荐：

```text
Raw Bar Cache
      +
Corporate Action Cache
      |
      v
Adjustment Engine
```

避免 raw/qfq/hfq 在同一存储模型中互相污染。

### 11.5 Cache invariant

必须满足：

```text
cache disabled
==
cache miss
==
cache hit
```

业务结果完全一致，只允许 provenance/cache metadata 不同。

---

## 12. Error System v2

当前两个 `SourceUnavailable` 必须合并成同一个类。

建议错误树：

```text
TdxError
 |
 +-- ConfigError
 +-- ValidationError
 +-- TransportError
 +-- ProtocolError
 +-- DataError
 +-- SourceError
 |    +-- SourceUnavailable
 |    +-- SourceTimeout
 |    +-- SourceRateLimited
 |    +-- SourceCapabilityMissing
 |    +-- SourceDegraded
 +-- CacheError
 +-- ServiceError
```

每个 SourceError 建议包含：

```json
{
  "code": "SRC_TIMEOUT",
  "source": "tdx",
  "retryable": true,
  "fallback_allowed": true,
  "context": {}
}
```

### 12.1 fallback 白名单

仅以下可恢复异常允许自动 fallback：

```text
TransportError
SourceTimeout
SourceRateLimited
CommandOffline
SourceUnavailable
DataFileNotFound
```

禁止把以下程序错误转换成“数据源不可用”：

```text
TypeError
AttributeError
AssertionError
KeyError caused by internal bug
unexpected InternalError
```

目标：

> 数据源故障可以降级；程序 bug 必须立即暴露。

---

## 13. Local Reader 主链修复

Phase 0 必须先修：

```text
DAY -> DayBarReader
M1  -> MinBarReader(1)
M5  -> MinBarReader(5)
```

并确保：

```text
Reader direct API
   ==
DataSourceRouter local
   ==
UnifiedMarketDataService local
```

至少用真实 fixture 验证：

```text
.day
.lc1
.lc5
```

---

## 14. HTTP / WS / MCP 统一服务面

目标：

```text
HTTP --\
WS -----+--> MarketDataService --> Query Engine --> Router --> Sources
MCP ----/
```

禁止：

```text
HTTP -> direct TdxClient
WS   -> direct WebQuoteSession
MCP  -> direct client based on use_facade flag
```

除非专门提供明确命名的调试入口：

```text
/raw/tdx/*
```

### 14.1 HTTP 默认服务实例

当前默认只创建 TdxClient，导致 Goods/Ex/Mac/F10 注册了但默认不可用。

重构后 HTTP 只依赖：

```text
MarketDataService
```

由 Service 内部 SourceManager 管理标准 TDX、Goods、Extended、MAC、F10、Web、Vipdoc。

### 14.2 REST 参数签名

必须补齐 contract test，优先覆盖：

```text
snapshot
auction_snapshot
security_list
goods
extended
f10
```

---

## 15. TaskManager v2

替换：

```text
thread-per-task
```

为：

```text
TaskManager
 +-- ThreadPoolExecutor(max_workers=N)
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
```

关键规则：

- `cancel_requested` 在真实执行结束前仍占 active slot；
- running task 禁止直接从 registry 删除；
- delete 只能 purge 已终止任务；
- max tasks 同时约束 running + cancel_requested + pending；
- 后续如果 capability 支持 cooperative cancellation，再向 source 传 token。

---

## 16. Async architecture

### 16.1 短期安全化

当前 `AsyncUnifiedQuoteAPI -> asyncio.to_thread(shared sync facade)` 需要先补：

- lazy initialization lock；
- route circuit lock；
- lifecycle state；
- close 与 in-flight request 协调；
- dedicated bounded executor；
- 拒绝 close 后的新请求。

### 16.2 中期目标

```text
AsyncMarketDataService
 |
 +-- AsyncTdxSource -> AsyncTdxClient
 +-- AsyncWebSource
 +-- sync-only source -> bounded thread adapter
```

同步与异步必须共享：

```text
QuerySpec
CapabilityRegistry
RoutePolicy
CachePolicy
ErrorPolicy
```

只允许 IO adapter 不同。

---

## 17. Streaming v2

### 17.1 现有问题

当前：

```text
all subscriptions
   |
union symbols without dedup
   |
min(subscription.interval)
   |
poll all every min interval
```

导致低频订阅被高频拉取。

### 17.2 新 Scheduler

```text
SubscriptionState
 +-- interval
 +-- next_due
 +-- symbols
 +-- subscriber
      |
      v
StreamScheduler
      |
      +-- select due subscriptions
      +-- deduplicate symbols
      +-- batch fetch
      +-- DeltaMerger
      +-- fan-out
```

### 17.3 Backend abstraction

统一：

```text
StreamBackend
 +-- TdxPollingBackend (0x0530)
 +-- TdxPushBackend (0x0547, experimental)
 +-- WebPollingBackend
```

`PushChannel` 要么接入该 backend，要么移动到 `experimental/`，不能继续作为平行正式实时体系。

### 17.4 真正背压

应形成：

```text
producer
   |
bounded queue
   |
consumer worker / async consumer
```

慢 callback 不允许阻塞整个 fetch scheduler。

---

## 18. Observability

统一指标：

```text
request_total
request_latency_seconds
source_request_total
source_success_total
source_failure_total
source_fallback_total
source_circuit_state
cache_hit_total
cache_miss_total
cache_bypass_total
stream_fetch_total
stream_drop_total
task_active
```

日志统一字段：

```text
request_id
query_id
capability
source
route_policy
symbol
period
adjustment
cache_status
fallback_count
error_code
```

QueryResult 的 attempts/provenance 应可转成 trace/event。

---

## 19. Compatibility layer 收敛

正式入口长期保留：

```text
TdxClient
AsyncTdxClient
UnifiedQuoteAPI / future UnifiedMarketDataService
AsyncUnifiedQuoteAPI / future AsyncMarketDataService
```

以下兼容 Facade：

```text
BinaryClient
BridgeClient
HqClient
ExHqClient
OptionClient
```

迁移到：

```text
tstdx.compat
```

流程：

1. 明确真实 package version deprecation schedule；
2. 产生真正 `DeprecationWarning`；
3. 文档迁移示例；
4. major release 再删除。

禁止继续和正式 Facade 平级扩展。

---

## 20. Orphan config / orphan logic 清理

必须逐项证明消费者：

```text
SecurityConfig.credential_backend
CompatibilityConfig.web_facade
CompatibilityConfig.market_facade
其他 deprecated/experimental flags
```

如果没有运行时消费者：

- 删除；或
- 明确迁移到 compat/experimental namespace。

`CredentialStore` 已删除后，不应继续保留仿佛仍有完整凭据后端的正式配置。

---

## 21. 性能优化方向

性能优化只能在语义统一后进行。

### 21.1 TDX

- persistent ConnectionPool；
- 批量请求合并；
- symbols 去重；
- host ranking / circuit 状态持续复用；
- 避免每 query 建连；
- 降低不必要 payload copy。

### 21.2 Web

- persistent HTTP client；
- HTTP keep-alive；
- per-source concurrency limit；
- source health score；
- adaptive retry；
- 避免 Web-only method 自行 new Session。

### 21.3 数据处理

- parser registry 热路径 profiling 后再优化；
- DataFrame 延迟转换；
- batch API 优先；
- canonical domain model 减少 dict/object 往返；
- 避免重复 symbol normalize。

---

## 22. Phase 0：P0 止血

目标：**不大改架构，先消除确定性错误和显式断链。**

必须完成：

1. 修 `M5 -> MinBarReader`；
2. Unified local bars 接通 `.lc1/.lc5`；
3. cache 对 `start/adjust/explicit route` 先保证安全；
4. 请求开始时隔离/清空错误聚合状态；
5. `SourceUnavailable` 合一；
6. `security_list` explicit route 语义修复；
7. `_get_router` 透传 timeout/web_sources/config；
8. Facade 使用统一 configured router factory；
9. 修 REST snapshot 参数签名；
10. 修 REST auction snapshot 参数签名；
11. HTTP 默认服务接通 Goods/Ex/Mac/F10；
12. TaskStore 阻止 cancel 绕过 worker limit；
13. running task 禁止删除为 orphan；
14. 修 `adjusted_bars(events=[])`；
15. broad exception fallback 至少先限制为可恢复错误。

验收：

- 所有已有测试绿色；
- 新 P0 regression tests 绿色；
- 不修改协议 ledger 的已验证事实。

---

## 23. Phase 1：Capability + Query SSOT

新增建议目录：

```text
tstdx/application/
  capabilities.py
  query.py
  result.py
  policy.py
```

完成：

```text
CapabilityRegistry
QuerySpec
QueryResult
RoutePolicy
```

首批迁移能力：

```text
quotes
bars
snapshot
minute
trades
security_list
```

验收：

- 这些能力不再在 facade 中手写 source/fallback；
- 所有 public capability 都必须有 capability declaration。

---

## 24. Phase 2：Canonical Router + SourceManager

建议目录：

```text
tstdx/sources/
  base.py
  manager.py
  router.py
  tdx.py
  web.py
  vipdoc.py
  replay.py
```

完成：

- 唯一 Router；
- 唯一 Source capability 判断；
- 唯一 fallback policy；
- persistent TDX client / pool；
- persistent Web session；
- 删除/废弃 Facade RouteSelector 的重复路由职责。

注意：

```text
Source circuit != Transport host circuit
```

Transport host circuit 继续保留，二者分层：

- Source circuit：某类数据源整体可用性；
- Host circuit：TDX 某具体主站可用性。

---

## 25. Phase 3：Cache V2

完成：

- CacheKey；
- CachePolicy；
- provenance；
- source_scope；
- canonical raw series；
- adjustment isolation；
- GoldenReplaySource 拆出。

验收：

```text
cache off == miss == hit
```

覆盖：

```text
raw/qfq/hfq
start=0/start>0
day/1min/5min
tdx/web/local
explicit route/auto
```

---

## 26. Phase 4：Service layer 全面统一

完成：

```text
HTTP
WS
MCP
CLI
  |
  v
MarketDataService
  |
  v
Query Engine
```

删除 integration 层自行选择：

```text
TdxClient
WebQuoteSession
GoodsClient
ExMarketClient
MacClient
F10Client
```

integration 只做：

- 参数解析；
- auth/security；
- protocol serialization；
- service 调用；
- error mapping。

---

## 27. Phase 5：Async + Streaming + TaskManager

完成：

- Async lifecycle safety；
- dedicated executor / native async source；
- StreamScheduler；
- symbols dedup；
- polling/push backend；
- producer/consumer backpressure；
- bounded TaskManager；
- cancellation state machine。

---

## 28. Phase 6：兼容层与孤儿逻辑清理

处理：

- duplicate facade；
- orphan config；
- stale docs；
- outdated version wording；
- experimental push path；
- 无消费者 helper/config；
- synthetic 生产入口隔离。

最后重新生成：

- Architecture 文档；
- Feature Map；
- API compatibility map；
- Source capability matrix；
- REST/WS/MCP capability table。

---

## 29. Architecture Contract Tests

### 29.1 Capability completeness

所有公开能力必须有注册：

```text
public capabilities - capability registry == empty set
```

### 29.2 Explicit route invariant

```text
route=tdx   -> only TdxSource
route=web   -> only WebSource
route=local -> only VipdocSource
route=replay-> only ReplaySource
```

失败也禁止跨 source。

### 29.3 Cache equivalence

相同 QuerySpec：

```text
cache disabled == cache miss == cache hit
```

### 29.4 Local reader matrix

真实 fixture：

```text
.day -> DayBarReader
.lc1 -> MinBarReader(1)
.lc5 -> MinBarReader(5)
```

从三层入口对拍：

```text
Reader
Source/Router
Unified Service
```

### 29.5 Service parity

同一 fake source 输入：

```text
Python API
REST
WS
MCP
```

结果必须一致。

### 29.6 Concurrency

至少：

```text
100 concurrent quotes
100 concurrent bars
concurrent first-use lazy init
concurrent circuit state update
concurrent cache access
concurrent close
TaskManager cancel storm
```

### 29.7 Architecture import guard

建议 AST/import gate：

```text
integration/http*
integration/ws*
integration/mcp*
```

禁止直接依赖具体 source client，只允许依赖：

```text
MarketDataService
```

### 29.8 Error contract

- 同一错误在 Python/REST/WS/MCP code 一致；
- 只有可恢复 source error 可 fallback；
- 程序内部 TypeError/AttributeError 不得被自动降级掩盖。

---

## 30. Benchmark gates

重构期间必须同时建立性能基线，避免统一架构后性能回退。

建议基准：

```text
quote single latency
quote batch latency
bars 320 latency
bars cache hit latency
TDX connection reuse ratio
Web connection reuse ratio
stream 100 symbols
stream 1000 symbols
REST throughput
WS throughput
MCP tool latency
```

每个 benchmark 至少区分：

```text
cold
warm
cache hit
cache miss
source failover
```

禁止用降低数据正确性换性能。

---

## 31. Migration strategy

### Stage A

保持现有 public API，内部导向新 Service。

### Stage B

给旧 Facade 增加 deprecation notice，但不立即删除。

### Stage C

文档全部改用新主入口。

### Stage D

major release 删除明确 deprecated compat API。

迁移期必须保持：

- `TdxClient` 作为低层协议 API；
- Unified API 作为高层数据 API；
- 不迫使低层协议研究用户必须经过统一 Router。

---

## 32. Definition of Done

本轮重构只有满足以下全部条件才算完成：

### 数据正确性

- [ ] `.day/.lc1/.lc5` 全链路正确；
- [ ] cache 不改变 start/count；
- [ ] cache 不混 raw/qfq/hfq；
- [ ] explicit route 不跨 source；
- [ ] approximate fallback 默认关闭。

### 架构

- [ ] 单一 Capability Registry；
- [ ] 单一 QuerySpec；
- [ ] 单一 Canonical Router；
- [ ] 单一 SourceManager；
- [ ] 单一 Source error hierarchy；
- [ ] 不再有 facade/router 双路由。

### 生命周期

- [ ] TDX ConnectionPool 跨 query 复用；
- [ ] Web HTTP session 跨 query 复用；
- [ ] close 与 in-flight request 安全；
- [ ] router 无请求级共享 mutable state。

### 服务面

- [ ] REST 只走 MarketDataService；
- [ ] WS 只走 MarketDataService；
- [ ] MCP 只走 MarketDataService；
- [ ] Goods/Ex/Mac/F10 默认服务可用；
- [ ] Python/REST/WS/MCP capability parity gate 绿色。

### Async / Streaming / Tasks

- [ ] Async lifecycle race 关闭；
- [ ] subscription interval 独立生效；
- [ ] symbols 去重；
- [ ] push/poll backend 统一；
- [ ] backpressure 与 callback 解耦；
- [ ] cancel 不可绕过 task limit；
- [ ] 无 orphan execution。

### 清理

- [ ] 重复 `SourceUnavailable` 删除；
- [ ] orphan config 清理；
- [ ] deprecated facade 移到 compat；
- [ ] stale docs 更新；
- [ ] synthetic 与生产 source 明确隔离。

### 门禁

- [ ] unit tests green；
- [ ] integration tests green；
- [ ] architecture contract tests green；
- [ ] concurrency tests green；
- [ ] benchmark 无不可接受回退；
- [ ] docs 与实际 capability registry 一致。

---

## 33. 推荐执行优先级

严格按以下顺序推进：

```text
P0-A 数据正确性
  M5 reader
  local minute bars
  cache start/adjust/source

        |
        v
P0-B 路由正确性
  explicit route
  SourceUnavailable
  config propagation
  router factory

        |
        v
P0-C 服务主链
  REST signatures
  HTTP default service
  Goods/Ex/Mac/F10
  task safety

        |
        v
P1-A 架构统一
  CapabilityRegistry
  QuerySpec
  QueryResult
  Canonical Router

        |
        v
P1-B 生命周期统一
  SourceManager
  persistent TDX/Web
  Cache V2

        |
        v
P1-C 服务统一
  HTTP / WS / MCP -> MarketDataService

        |
        v
P1-D 并发/实时
  Async
  Streaming Scheduler
  TaskManager

        |
        v
P2 清理
  compat
  orphan config
  stale docs
  experimental paths
```

---

## 34. 最终目标

重构结束后，tstdx 应具备一个非常清晰的产品定位：

> **以 TDX 协议为核心、融合 Web 与本地 vipdoc 的统一市场数据执行内核。底层协议客户端保持专业和可直接使用；高层所有数据访问经过同一 Capability / Query / Router / Source / Cache 体系，并通过 Python、Async、CLI、REST、WS、MCP 一致暴露。**

新增一个新数据源时，只应：

1. 实现 Source adapter；
2. 注册 Capability；
3. 注册生命周期；
4. 增加 contract fixture。

不应该再修改：

```text
Facade route if/else
HTTP route logic
WS route logic
MCP use_facade flag
多个 fallback helper
多套 cache source order
```

这就是 v11 的核心验收标准：

> **一条主干、一个事实源、多个数据源、所有入口一致；无断链、无隐式口径变化、无孤儿逻辑。**
