# tstdx：TDX 主行情源 + 多源辅助的量化行情架构与优化执行方案 v12

> Branch: `refactor/industry-benchmark-v12`  
> Main baseline: `main@f927e7faf49e77352b531addc49ecc6e44abcf81` (`v1.4.0`)  
> Previous plan baseline: `refactor/industry-benchmark-v12@9d28b3156198151be5a6adc6cc7c629ad50e1fbd`  
> Updated: 2026-09-08  
> Status: **Current execution SSOT / 当前唯一执行基线**

---

# 0. 产品定位重新收敛

`tstdx` 的核心不是“任意数据源自动兜底”，而是：

> **以 TDX 为主行情源，多个真实行情/资讯源作为辅助渠道；面向量化研究与交易决策提供可选择、可追溯、最新且真实的数据。**

必须遵守四条产品级硬约束：

1. **TDX 是默认主行情源。**
2. **不同数据源是并列数据渠道，不是自动 fallback 链。**
3. **用户可以显式指定数据源，也可以直接调用某个数据源对应接口。**
4. **选中的数据源不可用、数据过期或数据真实性无法确认时，直接返回明确错误；禁止静默换源、禁止返回过期缓存、禁止 Replay/Synthetic 冒充实时数据。**

最终产品不是：

```text
TDX -> Web -> Local -> Cache -> Synthetic
```

而是：

```text
                        +--> TDX           (primary)
                        |
Python / REST / WS -----+--> Tencent       (auxiliary)
MCP / CLI / Async       +--> Sina          (auxiliary)
                        +--> Eastmoney     (auxiliary)
                        +--> Baidu/...     (auxiliary)
                        +--> Vipdoc        (local historical)
                        +--> other source  (capability-specific)

                SourceSelection
                      |
              exactly one source
                      |
               fetch + validate
                      |
             freshness + quality
                      |
                 QueryResult
```

**一次 Query 的生产执行只允许有一个 Source。**

跨源可以做：

- 用户主动比较；
- 数据质量校验；
- 多源因子融合；
- 研究分析；

但不能做：

- A 源失败后偷偷切 B 源；
- A 源数据过期后偷偷拿 B 源补；
- TDX 不可用后把历史缓存当作“实时行情”；
- 将 Golden/Synthetic 当成生产行情。

---

# 1. 数据源角色模型

## 1.1 Source Role

每个数据源必须注册角色：

```text
primary_live        主实时行情
auxiliary_live      辅助实时行情
auxiliary_info      资讯/排行/资金/特色能力
local_historical    本地历史数据
replay_test         回放/测试
synthetic_test      合成/测试
```

推荐当前角色：

```text
tdx             = primary_live
tencent         = auxiliary_live
sina            = auxiliary_live
eastmoney       = auxiliary_live + auxiliary_info
baidu/...       = auxiliary_info
vipdoc          = local_historical
golden replay   = replay_test
synthetic       = synthetic_test
```

生产环境默认只允许前三类真实数据角色。

## 1.2 TDX 是默认源，不是唯一源

对于 TDX 具备真实能力的 capability：

```text
quotes
bars
minute
trades
finance
capital_changes
...
```

默认源：

```text
source=None -> tdx
```

但用户可以显式指定其它**支持同一 capability 且满足同等数据语义**的真实源：

```python
service.quotes(["sh600519"], source="tdx")
service.quotes(["sh600519"], source="tencent")
service.quotes(["sh600519"], source="sina")
```

## 1.3 TDX 不具备的能力

例如某些：

```text
热度排行
资金流
问财
特色榜单
Web 专属资讯
```

CapabilityRegistry 为该能力配置**确定的 default_source**：

```text
capability=hot_rank
supported_sources=[eastmoney]
default_source=eastmoney
```

这仍然不是 fallback。

执行过程是：

```text
select eastmoney once -> execute -> success/error
```

不是：

```text
eastmoney fail -> sina -> baidu -> ...
```

---

# 2. Source 与 Host 必须严格区分

“不允许跨源兜底”不等于“不允许同源连接容错”。

## 2.1 Source

```text
tdx
tencent
sina
eastmoney
baidu
vipdoc
...
```

Source 是业务数据来源，直接影响 provenance、数据语义和可信度。

## 2.2 Host

TDX Source 内可以包含多个主站：

```text
tdx
 +-- host A :7709
 +-- host B :7709
 +-- host C :7709
```

一个 TDX host 失败：

```text
允许 TDX ConnectionPool 在 TDX host pool 内切换
```

全部 TDX host 失败：

```text
返回 AllHostsUnreachable / SourceUnavailable(source="tdx")
```

**禁止切 Tencent/Sina/Eastmoney。**

同理，如果未来某一 Web Provider 自己有多个 endpoint/CDN，也只允许在该 Provider 内部切换。

---

# 3. 目标总体架构

```text
External API
Python / Async / CLI / REST / WS / MCP
                    |
                    v
          UnifiedMarketDataService
                    |
            QuerySpec / BatchSpec
                    |
                    v
             SourceSelector
          (one source, deterministic)
                    |
                    v
              QueryPlanner
                    |
        Freshness / Quality Contract
                    |
                    v
              SourceManager
    +---------------+----------------+
    |               |                |
 TdxSource      TencentSource   EastmoneySource ...
    |               |                |
 host pool       http pool         http pool
    +---------------+----------------+
                    |
                    v
           Canonical Data Model
                    |
      FreshnessGuard / QualityGuard
                    |
                    v
       QueryResult + ResultMeta
```

横切生命周期：

```text
RuntimeContext
  +-- ConfigSnapshot
  +-- CapabilityRegistry
  +-- SourceRegistry
  +-- SourceManager
  +-- SourceHealthRegistry
  +-- CacheManager
  +-- ExecutionBudget
  +-- Metrics / Logger / Clock
  +-- TaskManager
```

核心变化：

> `QueryPlanner` 不再编译“候选源降级链”，而是编译“**一个已确定 Source 的执行计划**”。

---

# 4. Source Selection：选择，不兜底

## 4.1 QuerySpec

建议：

```python
QuerySpec(
    capability="quotes",
    symbols=("sh600519", "sz000001"),
    source=None,              # None -> capability.default_source
    freshness="live",
    max_age=None,
    allow_stale=False,
    require_real=True,
    allow_replay=False,
    timeout=3.0,
)
```

K 线：

```python
QuerySpec(
    capability="bars",
    symbols=("sh600519",),
    source="tdx",
    period="day",
    start=0,
    count=320,
    adjustment="raw",
    freshness="latest_available",
)
```

## 4.2 SourceSelector

```text
if query.source is not None:
    validate source exists
    validate source supports capability
    select exactly query.source
else:
    select capability.default_source
```

结束。

没有：

```text
candidate list
fallback sequence
switch_source
continue_on_error across sources
```

## 4.3 Legacy route

旧：

```text
route="auto"
route="tdx"
route="web"
route="local"
```

迁移：

```text
route="tdx"   -> source="tdx"
route="local" -> source="vipdoc"
route="web"   -> 必须进一步映射具体 provider；长期弃用
route="auto"  -> source=None -> capability.default_source，仅选择一个源
```

`auto` 不再代表自动跨源 fallback。

---

# 5. 用户接口：统一接口 + 指定源 + 直接 Source API

必须同时支持三种使用方式。

## 5.1 默认 TDX

```python
from tstdx import MarketDataService

with MarketDataService() as md:
    quotes = md.quotes(["sh600519", "sz000001"])
    bars = md.bars("sh600519", period="day", count=320)
```

TDX 能力默认使用 TDX。

## 5.2 显式指定数据源

```python
md.quotes(["sh600519"], source="tdx")
md.quotes(["sh600519"], source="tencent")
md.quotes(["sh600519"], source="sina")
md.bars("sh600519", source="eastmoney", period="day", count=320)
```

显式 source 后：

> **100% fail-closed：不可用就是错误。**

## 5.3 直接调用对应数据源

推荐高层 SourceHandle：

```python
md.source("tdx").quotes(["sh600519"])
md.source("tencent").quotes(["sh600519"])
md.source("eastmoney").hot_rank()
md.source("eastmoney").fund_flow("sh600519")
```

该接口仍经过：

```text
input normalization
freshness guard
error normalization
provenance
metrics
lifecycle
```

但**绝不会跨 source**。

## 5.4 保留 Low-level API

协议研究或完全控制时：

```python
from tstdx.client import TdxClient
```

Web Provider 也允许提供专业低层 API。

分层：

```text
Low-level Provider API
  -> 精确控制 provider/transport

High-level SourceHandle
  -> 单 provider + 统一数据/错误/新鲜度契约

Unified MarketDataService
  -> 默认 source 选择 + 统一能力入口
```

---

# 6. CapabilityRegistry：不仅记录支持能力，还要记录默认源

建议：

```python
CapabilitySpec(
    name="quotes",
    default_source="tdx",
    supported_sources=("tdx", "tencent", "sina", "eastmoney"),
    source_semantics={
        "tdx": "exact",
        "tencent": "exact",
        "sina": "exact",
        "eastmoney": "exact",
    },
    freshness_class="live_quote",
    supports_batch=True,
    output_schema="quote.v1",
)
```

专有能力：

```python
CapabilitySpec(
    name="hot_rank",
    default_source="eastmoney",
    supported_sources=("eastmoney",),
    freshness_class="live_info",
)
```

Registry 是以下内容的 SSOT：

```text
capability
source support
default_source
exact/approximate
periods
adjustment
batch limit
freshness class
streaming support
schema version
```

**不再保存 fallback order。**

---

# 7. “最新真实数据”契约

“获取成功”不等于“数据可用”。

必须同时通过：

```text
transport success
parse success
real-source check
freshness check
quality check
schema check
```

## 7.1 时间字段统一

ResultMeta 至少：

```python
ResultMeta(
    source="tdx",
    source_role="primary_live",
    source_timestamp=...,
    received_at=...,
    observed_at=...,
    age_ms=...,
    freshness="fresh",
    real=True,
    stale=False,
    replay=False,
    synthetic=False,
    cache_status="bypass",
    host="...",       # internal diagnostic only
)
```

语义：

```text
source_timestamp = 上游行情自身时间（若可用）
received_at      = tstdx 收到数据时间
observed_at      = 本次结果形成时间
age              = observed_at - source_timestamp
```

上游无 source timestamp 时必须在 metadata 明确：

```text
source_timestamp=None
timestamp_confidence="receive_time_only"
```

不能伪造交易时间。

## 7.2 FreshnessPolicy

按 capability 分类配置，不用一个 TTL 覆盖所有数据。

示例：

```text
live_quote       很短 SLA
snapshot         很短 SLA
minute_today     当前交易日 / 最新分钟
trade_today      当前交易日 / 最新可用逐笔
live_rank        短 SLA
historical_bar   已收盘区间可长期缓存；当前未收盘 tail 必须刷新
fundamental      按数据发布日期/version 判断
```

具体阈值应由 benchmark/真实源特性校准，不拍脑袋写死在代码多个位置。

## 7.3 FreshnessGuard

```text
response received
   |
source timestamp / trading calendar / session state
   |
FreshnessPolicy
   |
fresh -> return
stale -> FreshnessViolation
```

过期数据：

- 不返回；
- 不跨源；
- 不自动读 stale cache；
- 明确告知 source/age/threshold。

---

# 8. 生产环境禁止假数据与隐式历史替代

## 8.1 Synthetic

```text
production: forbidden
test: allowed explicitly
benchmark: allowed explicitly
```

必须移到：

```text
tstdx.testing.synthetic
```

或显式 `environment="test"` 才能注册。

## 8.2 Golden Replay

Golden 是：

```text
protocol regression
parser regression
offline deterministic test
```

不是实时 Source。

生产默认不注册到 MarketDataService。

## 8.3 Vipdoc

Vipdoc 是真实历史本地数据，但不是“实时替代”。

允许：

```python
md.bars(..., source="vipdoc")
```

但 ResultMeta 必须标：

```text
source=vipdoc
source_role=local_historical
```

若用户要求 `freshness="live"`，Vipdoc capability 应直接拒绝。

---

# 9. Cache：实时数据默认不使用持久缓存代替上游

## 9.1 Real-time Query

对于：

```text
quotes
snapshot
minute current
trades current
streaming
```

默认：

```text
persistent cache read = disabled
stale cache fallback = forbidden
```

允许的优化：

### SingleFlight

同一时刻相同 Query 合并为一次**真实上游请求**：

```text
100 concurrent calls
      |
   SingleFlight
      |
1 selected-source fetch
      |
fan-out
```

这不改变 freshness，也不是历史 cache。

### Request-local memo

同一高层组合查询内部重复依赖可以复用本次刚取得的真实结果。

## 9.2 Historical Query

历史已闭合区间可以缓存，但 key 必须包含：

```text
source
capability
symbol
period
adjustment/raw model
schema version
coverage range
```

当前交易日/未闭合 K 线：

```text
cached historical prefix
+
selected source refresh tail
```

仍然只访问同一个 source。

## 9.3 Cache invariant

```text
cache hit result == selected source canonical historical result
```

并且 metadata 明确 cache provenance。

---

# 10. 错误体系：不可用就明确报不可用

保留当前 E1-E9 错误树，不重建。

## 10.1 SourceUnavailable 唯一化

当前重复：

```text
tstdx.errors.SourceUnavailable E7050
tstdx.sources.SourceUnavailable E3400
```

必须统一到：

```text
tstdx.errors.SourceUnavailable E7050
```

旧路径只 re-export。

## 10.2 新鲜度错误

建议在现有 DataError 分支新增最小必要类型：

```text
FreshnessViolation   E4060
DataConsistencyError E4070
```

不重排已有 code。

### FreshnessViolation

场景：

```text
source 返回成功，但行情时间超过 freshness policy
当前交易日只返回陈旧数据
实时请求得到历史缓存
```

### DataConsistencyError

场景：

```text
字段完整性/跨字段约束不满足
主动多源对账差异超过阈值（研究/校验模式）
```

## 10.3 SourceCapabilityMissing

不需要复制一大批 SourceError 类。

当用户指定某 Source 不支持 capability：

```text
ValidationError / SourceUnavailable
context:
  source
  capability
  supported_sources
```

建议最终选择一个稳定 code 并在 registry 中机器生成文档。

## 10.4 ErrorEnvelope

统一：

```json
{
  "code": "E7050",
  "type": "SourceUnavailable",
  "message": "TDX 行情源当前不可用",
  "source": "tdx",
  "capability": "quotes",
  "phase": "connect",
  "retryable": true,
  "switch_source": false,
  "request_id": "...",
  "query_id": "...",
  "context": {
    "hosts_attempted": 4
  }
}
```

重点：

```text
switch_source = false
```

生产主链永远不通过 ErrorEnvelope 建议跨源切换。

## 10.5 用户可读错误

TDX 全部 host 不可达：

```text
[E2040] TDX 主站当前不可达；已尝试 4 个 TDX 主站，未切换到其它行情源。
```

用户指定腾讯：

```text
[E7050] tencent 行情源当前不可用；本次请求未切换其它行情源。
```

数据过期：

```text
[E4060] tdx 返回行情已超过实时新鲜度要求；age=...，数据未返回。
```

必须避免：

```text
“自动切换到备用源成功”
“使用缓存继续返回”
```

---

# 11. FailurePolicy：只允许同源恢复

v12 原 `switch_source` 思路废弃。

新的 FailureDisposition：

```python
FailureDisposition(
    retry_same_source=True,
    switch_host=True,       # TDX 内部 host
    switch_endpoint=True,   # 同 provider 内部 endpoint
    switch_source=False,
    terminal=False,
    retry_after=...,
)
```

规则：

```text
connection timeout  -> retry same source / switch same-source host
host unavailable    -> switch same-source host
rate limited        -> obey retry-after or fail
command offline     -> terminal for this source/capability
source unavailable  -> terminal after same-source recovery exhausted
freshness violation -> optional same-source immediate refresh; then terminal
parse integrity     -> terminal, do not fallback
programming error   -> InternalError, do not retry
```

---

# 12. SourceManager：按 Source 长生命周期复用

```text
SourceManager
  +-- TdxSource
  |     +-- quotation pool
  |     +-- extended pool
  |     +-- goods pool
  |     +-- mac pool
  |     +-- f10 pool
  |
  +-- TencentSource
  +-- SinaSource
  +-- EastmoneySource
  +-- BaiduSource
  +-- VipdocSource
```

## 12.1 不再每 Query new/close

当前 Router 每次：

```text
with TdxClient(...)
WebQuoteClient() -> close()
```

会丢失：

```text
TCP reuse
HTTP keep-alive
host ranking
provider health
connection warm state
```

改成 SourceManager 持久拥有资源。

## 12.2 健康状态只影响报错与监控，不触发跨源路由

```text
healthy
suspect
unhealthy
cooldown
```

如果 `tdx` unhealthy：

```text
source=None + quotes -> default=tdx -> SourceUnavailable
```

不是：

```text
source=None + quotes -> tdx unhealthy -> tencent
```

---

# 13. Source API 与 Provider Adapter

每个 Source 统一三段式：

```text
prepare
  |
fetch
  |
normalize
```

例如：

```python
class TdxQuoteSource:
    def prepare(self, query): ...
    def fetch(self, request, budget): ...
    def normalize(self, raw): ...
```

辅助源同样遵守。

优势：

- 输入语义统一；
- 每个 source 差异局限在 adapter；
- 统一 freshness/provenance；
- 统一 benchmark/conformance；
- HTTP/WS/MCP 不再知道 provider transport。

---

# 14. 多源辅助的正确使用方式：比较/融合，不是兜底

量化决策确实需要多源。

应该提供显式多源 API：

```python
md.compare_quotes(
    ["sh600519"],
    sources=["tdx", "tencent", "sina"],
)
```

或：

```python
md.query_many([
    QuerySpec(capability="quotes", symbols=(...), source="tdx"),
    QuerySpec(capability="quotes", symbols=(...), source="tencent"),
])
```

返回：

```text
每个 source 独立成功/失败
每个 source 自己 timestamp/provenance
不互相替代
```

## 14.1 CrossSourceResult

```python
CrossSourceResult(
    results={
        "tdx": QueryResult(...),
        "tencent": QueryResult(...),
    },
    errors={
        "sina": ErrorEnvelope(...),
    },
    comparison=...,
)
```

这非常适合：

- 行情对账；
- 价格差异监控；
- 数据源质量评分；
- 量化特征输入；
- 异常数据识别。

**但是 compare 结果不能自动选“看起来最正常”的 source 冒充默认行情。**

---

# 15. BatchPlanner：只优化同一选中 Source

批量流程：

```text
normalize once
  |
deduplicate symbols
  |
select exactly one source
  |
group by market/request type
  |
chunk by selected source batch limit
  |
bounded parallel requests
  |
normalize
  |
freshness guard
  |
fan-out original order
```

禁止 BatchPlanner 因某 symbol 失败改 source。

Partial failure：

```python
BatchResult(
    items=...,
    errors={symbol: ErrorEnvelope(...)},
    source="tdx",
    partial=True,
)
```

所有成功和失败都属于同一 source。

---

# 16. Streaming：实时流必须绑定 Source

## 16.1 API

```python
md.stream.quotes(
    ["sh600519"],
    source="tdx",
    interval=1.0,
)
```

默认：

```text
source=tdx
```

如果 TDX stream 失败：

- 同 TDX host 恢复；
- 重连；
- resubscribe；
- 明确 gap；
- 最终 SourceUnavailable；

**不切 Web polling source。**

## 16.2 不同 Source 的流分别存在

用户要腾讯流：

```python
md.stream.quotes(..., source="tencent")
```

这是另一条显式订阅。

## 16.3 Scheduler

保留 v12 的：

```text
per-sub next_due
union+dedup due symbols
batch fetch
true bounded queue
consumer dispatcher
```

但只在同 Source 内共享 fetch。

## 16.4 Push/ Poll

如果 TDX 同一 Source 内同时支持：

```text
TdxPushBackend
TdxPollingBackend
```

这是**同源传输 backend**。

允许：

```text
push unavailable -> polling
```

前提是二者数据语义相同并且仍然 `source=tdx`。

ResultMeta 记录 backend。

---

# 17. HTTP / WS / MCP 接口必须暴露 source

## 17.1 REST

建议：

```http
GET /quotes?symbols=600519&source=tdx
GET /quotes?symbols=600519&source=tencent
GET /bars/600519?source=tdx&period=day
```

返回：

```json
{
  "data": [...],
  "meta": {
    "source": "tdx",
    "freshness": "fresh",
    "source_timestamp": "...",
    "received_at": "..."
  }
}
```

不指定 source：

```text
使用 capability.default_source
```

REST 不允许自行 fallback。

## 17.2 Source-specific REST

可选提供：

```http
GET /sources/tdx/quotes
GET /sources/tencent/quotes
GET /sources/eastmoney/hot_rank
```

内部仍调用 SourceHandle，不直接 new client。

## 17.3 WS

订阅消息：

```json
{
  "op": "subscribe",
  "capability": "quotes",
  "source": "tdx",
  "symbols": ["sh600519"]
}
```

source 一旦建立订阅不能静默变更。

## 17.4 MCP

Tool input schema 应包含 `source`；或者生成 source-specific tool。

MCP 不得保留 `use_facade` 这种内部链路开关。

---

# 18. 历史行情与实时行情必须分开建模

量化系统最危险的错误之一是：

> “接口返回了数据，但不是用户以为的当前真实行情。”

必须区分：

```text
LiveQuote
LiveSnapshot
IntradayMinute
TradeTick
HistoricalBar
FundamentalSnapshot
InfoEvent
```

不能全部用 `list[dict]` 模糊语义。

对于 HistoricalBar：

- 已收盘 bar 可安全缓存；
- 当前形成中的 bar 要 refresh；
- source-specific provenance 必须保留；
- 不同 provider 的 adjustment/volume/amount 语义必须 contract test。

---

# 19. 数据质量与量化决策元数据

量化系统应能判断数据能否进入策略。

建议 `ResultMeta` 额外提供：

```text
real
fresh
complete
exact
source
source_role
source_timestamp
age_ms
market_session
parse_tier
confidence
partial
```

策略层可以：

```python
if not result.meta.fresh:
    skip_signal()
if result.meta.partial:
    reject_batch()
if result.meta.source != "tdx":
    record_alternative_source_feature()
```

这比“拿到 dict 就当真”更适合量化决策。

---

# 20. 当前源码需要按新定位优先修复的事项

## P0

1. **删除 Router 跨源自动降级语义。**
2. `route="auto"` 兼容映射为 deterministic default source。
3. `security_list(route="tdx")` 禁止 CommandOffline 后切东财。
4. `_safe_run` 不再 generic Exception -> source fallback。
5. duplicate `SourceUnavailable` 统一 E7050。
6. M5 -> MinBarReader。
7. Facade local 接通 lc1/lc5，但只有用户显式 `source="vipdoc"` 时使用。
8. 实时 QuoteCache 不得在生产请求前短路真实 source fetch。
9. KlineCache 必须 source-scoped；实时/current tail 强制刷新。
10. Golden/Synthetic 从生产 source registry 移除。
11. REST snapshot/auction/date/binary 签名修复。
12. TaskStore thread-per-task/cancel 绕过修复。
13. `adjusted_bars(events=[])` 显式空值语义修复。

## P1

1. SourceManager 持久 TDX/Web clients。
2. RuntimeContext 唯一 config snapshot。
3. CapabilityRegistry 增加 `default_source`。
4. SourceSelector 替代 fallback Router。
5. QueryPlan 只包含一个 source。
6. FreshnessPolicy/FreshnessGuard。
7. ResultMeta/provenance。
8. SourceHandle API。
9. HTTP/WS/MCP 全部走 Service。
10. BatchPlanner/SingleFlight。

---

# 21. 执行效率：在“不换源、不返回旧数据”的前提下优化

优先级：

## 第一层：减少建连与重复上游请求

```text
persistent TDX pool
persistent HTTP pool
BatchPlanner
symbol dedup
SingleFlight
request-local memo
```

## 第二层：减少对象转换

```text
parser -> canonical record -> QueryResult
```

边界再：

```text
JSON
DataFrame
CSV
model
```

避免：

```text
Quote -> dict -> Quote
Bar -> dict -> Bar
```

## 第三层：减少无效 retry

因为没有跨源 fallback：

- 执行路径更短；
- timeout 更可预测；
- source outage 不会造成多源连续等待；
- 错误更快暴露给策略层。

## 第四层：Parser 优化

只有 benchmark 显示 parser CPU 是瓶颈时再：

```text
memoryview
unpack_from
减少 slice copy
批量 decode
```

完整性检查不能取消。

---

# 22. ExecutionBudget 简化

新的预算只作用于：

```text
selected source
selected source endpoints/hosts
```

例如：

```python
ExecutionBudget(
    total_deadline=3.0,
    max_same_source_attempts=3,
    max_host_switches=2,
)
```

删除：

```text
max_source_switches
fallback budget
```

超时后：

```text
terminal error
```

不会再启动其它 Source 请求。

---

# 23. Config 重新定义

推荐：

```toml
[market_data]
default_primary_source = "tdx"
require_real = true
allow_stale = false

[sources.tdx]
enabled = true

[sources.tencent]
enabled = true

[sources.sina]
enabled = true

[sources.eastmoney]
enabled = true

[test_sources]
replay = false
synthetic = false
```

Capability 自己决定默认源，不用一个全局 `sources.order`。

必须逐步废弃：

```text
sources.order = [tdx, web, reader, cache, synthetic]
continue_on_error = true  # 跨源语义
```

保留同源内部 retry 配置。

---

# 24. Observability

指标必须围绕“哪个 Source 真正被使用”：

```text
query_total{capability,source}
query_success_total{capability,source}
query_failure_total{capability,source,error_code}
query_latency_seconds{capability,source}
source_unavailable_total{source}
source_freshness_violation_total{source,capability}
source_data_age_seconds{source,capability}
host_switch_total{source=tdx}
connection_reuse_total{source}
batch_size{source,capability}
singleflight_join_total{source,capability}
stream_gap_total{source}
```

删除/不再强调：

```text
source_fallback_total
```

因为生产架构不应存在跨源 fallback。

---

# 25. Source Conformance Test

每个真实 Source 必须通过：

```text
capability contract
real-data contract
freshness metadata contract
symbol normalization contract
schema contract
missing-field contract
error mapping contract
deadline contract
no-cross-source contract
lifecycle/close contract
batch limit contract
```

最关键新增：

```python
assert query(source="tdx") never calls tencent/sina/eastmoney
assert query(source="tencent") never calls any other provider
```

---

# 26. Architecture Contract Gates

## 26.1 One Source Per Query

测试 QueryPlan：

```text
len(plan.sources) == 1
```

生产 Query 不允许 candidate list。

## 26.2 Default Source Determinism

```text
capability + config snapshot -> exactly one default source
```

相同配置不得因 source health 不同改变默认 source。

## 26.3 No Silent Fallback

注入：

```text
tdx -> SourceUnavailable
```

断言：

```text
tencent.calls == 0
sina.calls == 0
eastmoney.calls == 0
```

## 26.4 Freshness Gate

旧数据必须：

```text
raise FreshnessViolation
```

而不是返回 stale=True 给默认生产调用。

如果未来提供研究用途 `allow_stale=True`，必须显式 opt-in，且不能用于默认实时 API。

## 26.5 Real Data Gate

生产 RuntimeContext：

```text
ReplaySource not registered
SyntheticSource not registered
```

## 26.6 Direct Source Gate

```text
service.source("tdx")
service.source("tencent")
```

必须保留 source identity 到最终 ResultMeta。

---

# 27. Benchmark Gates

必须重新加入 source 维度：

```text
TDX quote 1 / 100 / 1000 symbols
Tencent quote 1 / 100 / provider max
Eastmoney capability-specific query
TDX bars cold/warm
historical cache prefix + live tail
TDX all-host unavailable failure latency
freshness validation overhead
SingleFlight concurrency
REST source=tdx/source=tencent
WS source=tdx 100/1000 symbols
```

关注：

```text
p50/p95/p99
upstream request count
connection reuse
host switch count
time-to-error when source unavailable
source data age
memory allocation
```

特别要求：

> 数据源不可用时要**快速明确失败**，不能因为逐个尝试其它 Source 把错误延迟拉长。

---

# 28. 实施路线重新排序

## Phase 0 — No-Fallback Correctness Freeze

1. 固化“TDX 默认主源”的 contract test；
2. 禁止跨源 fallback；
3. legacy auto -> deterministic default source；
4. security_list 显式 TDX 不再转 Web；
5. 统一 SourceUnavailable；
6. 实时 cache 禁止绕过真实 source；
7. Golden/Synthetic 从生产 registry 脱离；
8. M5/local reader 正确性；
9. REST 参数/二进制/TaskStore/adjusted_bars 修复。

出口：

```text
selected source failure -> selected source error
other source call count == 0
```

## Phase 1 — Source Selection Contract

实现：

```text
CapabilityRegistry.default_source
QuerySpec.source
SourceSelector
QueryPlan(single source)
SourceHandle
ResultMeta
FreshnessPolicy
FreshnessGuard
```

首批：

```text
quotes
bars
minute
trades
snapshot
security_list
```

## Phase 2 — SourceManager

实现：

```text
persistent TDX pool
persistent provider HTTP pool
source lifecycle
source health
same-source host/endpoint recovery
```

删除 Router 请求级共享状态。

## Phase 3 — Performance / Historical Cache

实现：

```text
BatchPlanner
SingleFlight
historical source-scoped cache
current-tail refresh
canonical data path
```

## Phase 4 — Multi-source Quant API

实现显式：

```text
query_many
compare_quotes
CrossSourceResult
source quality metrics
```

多源用于分析，不用于隐藏故障。

## Phase 5 — Service Surfaces

```text
REST
WS
MCP
CLI
```

全部暴露 source 并只调用 MarketDataService。

## Phase 6 — Async / Streaming / Task

完成：

```text
same-source async execution
source-bound streaming
scheduler/dedup
bounded task manager
cancellation
```

## Phase 7 — Orphan / Docs / Release

清理：

```text
sources.order
cross-source continue_on_error
legacy fallback docs
Synthetic production hooks
MCP use_facade
integration direct clients
orphan config
```

生成 registry/docs/error catalog。

---

# 29. 推荐提交拆分

```text
1. test: lock tdx-primary and no-cross-source contracts
2. fix: remove cross-source fallback from facade and router
3. fix: unify source unavailable and freshness failures
4. fix: isolate replay synthetic and real-time cache behavior
5. fix: close local reader rest and task correctness gaps
6. refactor: add capability default source and source selector
7. refactor: add single-source query plan result metadata freshness guard
8. refactor: introduce persistent source manager and source handles
9. perf: add batch planner singleflight and source-scoped history cache
10. feat: add explicit multi-source comparison APIs
11. refactor: route HTTP WS MCP CLI through market data service
12. refactor: bind async streaming and tasks to selected source
13. chore: remove fallback config and generate source capability docs
14. perf: establish source-aware benchmark gates
```

---

# 30. Definition of Done

## 产品语义

- [ ] TDX 是 TDX 支持能力的默认主源；
- [ ] 其它 Source 是辅助渠道，不是自动备用链；
- [ ] source=None 确定性选择一个 default source；
- [ ] 用户显式 source 100% fail-closed；
- [ ] 用户可通过 `service.source(name)` 直接调用对应 Source；
- [ ] 多源 API 是显式 comparison/query-many，不是隐式替换。

## 最新真实数据

- [ ] 实时请求不会被持久 cache 短路；
- [ ] stale 数据默认拒绝；
- [ ] Source timestamp/received_at/age 可追溯；
- [ ] Replay/Synthetic 不进入 production runtime；
- [ ] Vipdoc 不伪装实时；
- [ ] 当前未闭合历史 tail 会向同一 selected source 刷新。

## 错误

- [ ] SourceUnavailable 唯一 E7050；
- [ ] TDX 全主站失败明确报 TDX 不可用；
- [ ] 不出现“已自动切换其它行情源”；
- [ ] stale 返回 FreshnessViolation；
- [ ] unexpected exception 不被 SourceUnavailable 掩盖；
- [ ] Python/REST/WS/MCP/CLI ErrorEnvelope 一致。

## 架构

- [ ] QueryPlan 生产模式只有一个 Source；
- [ ] CapabilityRegistry 有 default_source；
- [ ] SourceSelector 无 fallback chain；
- [ ] SourceManager 统一生命周期；
- [ ] Host switch 与 Source switch 概念完全分离；
- [ ] Integration 层不直接 new Provider client。

## 效率

- [ ] TDX pool 跨 query 复用；
- [ ] Web provider HTTP pool 跨 query 复用；
- [ ] BatchPlanner 自动 chunk；
- [ ] symbols 去重；
- [ ] SingleFlight 合并同源等价实时请求；
- [ ] 不因多源 fallback 放大 timeout；
- [ ] canonical record 减少对象转换。

## Streaming

- [ ] subscription 绑定 source；
- [ ] TDX 失败不切 Web；
- [ ] 同 Source 内 host/backend 可恢复；
- [ ] interval 独立；
- [ ] due symbol 合并去重；
- [ ] callback 不阻塞 fetch；
- [ ] gap 明确暴露。

## 治理

- [ ] source/capability registry ↔ API ↔ docs 一致；
- [ ] no-cross-source architecture test 绿色；
- [ ] freshness gate 绿色；
- [ ] real-data gate 绿色；
- [ ] provider conformance 全绿；
- [ ] source-aware benchmark baseline 存在。

---

# 31. 明确删除的旧设计方向

以下不再作为目标架构：

```text
TDX -> Web -> Reader -> Cache -> Synthetic fallback chain
fresh/prefer_local 作为自动候选源链
FailureDisposition.switch_source=True
source fallback budget
source_fallback_total 作为正常运行指标
stale cache fallback
Golden replay production fallback
Synthetic production smoke fallback
```

这些能力如果历史 API 仍存在，只能进入 deprecation/compatibility 层，不能继续成为新核心设计。

---

# 32. 最终产品架构定位

重构完成后：

## TDX Core

```text
TdxClient / AsyncTdxClient
ConnectionPool / HostRanking
Protocol / Codec / CommandLedger
```

TDX 是核心、默认主行情通路。

## Multi-source Market Data Runtime

```text
MarketDataService
CapabilityRegistry
SourceSelector
QuerySpec / QueryPlan
SourceManager
FreshnessGuard
QualityGuard
QueryResult / ResultMeta
BatchPlanner / SingleFlight
StreamingScheduler
```

作用是：

> **让用户稳定地选择并使用不同真实行情源，而不是替用户偷偷更换行情源。**

## Quant Data Decision Layer

```text
query_many / compare
cross-source validation
quality metrics
data age / provenance
```

作用是：

> **把不同渠道数据作为量化决策信息输入，同时保留每个渠道自己的身份、时间和失败状态。**

最终核心原则：

> **TDX 为主，多源为辅；源选择显式、失败透明、时间可验证、数据必须真实。一个源失败就告诉用户这个源失败，而不是拿另一个源的数据替换它。**
