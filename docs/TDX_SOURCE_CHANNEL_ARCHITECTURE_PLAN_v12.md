# tstdx：TDX 主源、多渠道独立取数的量化数据架构方案 v12

> Branch: `refactor/industry-benchmark-v12`  
> Main baseline: `main@f927e7faf49e77352b531addc49ecc6e44abcf81` (`v1.4.0`)  
> Supersedes: `TDX_PRIMARY_MULTI_SOURCE_ARCHITECTURE_PLAN_v12.md`  
> Updated: 2026-09-08  
> Status: **Current execution SSOT / 当前唯一执行基线**

---

# 0. 产品定位

`tstdx` 的核心目标是：

> **以 TDX 为主行情源，保留并明确暴露多个真实数据渠道；用户可以选择具体数据源获取对应信息，也可以直接调用某个数据源的专属接口。系统必须返回最新、真实、可追溯的数据；选定渠道不可用时明确报错，不允许跨站静默替代。**

该定位决定以下硬约束：

1. **TDX 是默认主行情源。**
2. **TDX 内部允许主站/端点容错。**
3. **TDX 不能因为失败而跨站到新浪、腾讯、东财获取同名 K 线。**
4. **腾讯、新浪、东财、百度等不是 TDX 的 fallback，而是独立数据源。**
5. **每个数据源都有自己的特有 Channel 和独立接口。**
6. **统一 API 只负责选择数据源、校验能力和标准化结果，不隐式切换 Provider。**
7. **实时/决策数据必须最新且真实；过期、回放、合成、不可验证的新鲜度均不得冒充实时数据。**
8. **跨源比较允许，跨源替代禁止。**

禁止的执行链：

```text
TDX bars failed
  -> Tencent bars
  -> Sina bars
  -> Eastmoney bars
  -> cache/replay
```

正确执行链：

```text
Query(source=tdx, capability=bars)
        |
        v
TDX channel resolver
        |
        v
TDX quotation endpoint group
  host A -> host B -> host C   # 同源内部容错
        |
        +-- success -> validate freshness -> result(source=tdx)
        |
        +-- all failed -> SourceUnavailable(source=tdx)
                         # 到此结束，不跨 Provider
```

---

# 1. 重新定义四个核心维度

当前代码中“source”概念混入了 Provider、Channel、Market 等多种含义。v12 必须拆开。

## 1.1 Source / Provider

Source 是**数据所有者或独立数据提供方**，决定数据 provenance。

规范 Source ID：

```text
tdx
tencent
sina
eastmoney
baidu
jsl
boc
iwencai
vipdoc
```

说明：

- `tdx`：TDX 网络协议数据；
- `vipdoc`：本地通达信文件，可视为 TDX 生态的 local historical source，但数据来源、时效和执行模型与在线 TDX 不同，因此在 Query provenance 中保持独立；
- `tencent/sina/eastmoney/...`：外部网站 Provider；
- Golden Replay/Synthetic 不注册为生产 Source，只属于 test runtime。

## 1.2 Channel

Channel 是某 Source 内的**具体数据通路/业务数据族**。

例如：

```text
tdx/quotation
tdx/extended
tdx/goods
tdx/f10
tdx/mac

tencent/quote
tencent/kline
tencent/minute
tencent/ticks
tencent/global

eastmoney/quote
eastmoney/kline
eastmoney/fund_flow
eastmoney/limit_pool
eastmoney/northbound
eastmoney/corporate
```

Channel 不等于 Source。

## 1.3 Market

Market 表示证券/资产市场范围：

```text
cn_a
cn_bse
hk
us
fund
bond
fx
future
option
commodity
```

因此旧概念中的 `hk` / `us` 不应再作为 Source ID。

正确表示：

```text
source=tencent, channel=quote, market=hk
source=sina,    channel=quote, market=hk
source=tencent, channel=quote, market=us
```

## 1.4 Endpoint / Host

Endpoint 是某 Source/Channel 内的实际网络端点。

例如 TDX：

```text
source=tdx
channel=quotation
endpoint=IP_A:7709
endpoint=IP_B:7709
endpoint=IP_C:7709
```

Endpoint 失败可以切换同一个 EndpointGroup 内的其它 endpoint。

**Endpoint failover 不等于 Source fallback。**

---

# 2. TDX 内部允许“换源”的精确定义

用户要求“TDX 内部可以换源”，v12 将其定义为 **TDX Provider 内部容错与 Channel 解析**，而不是跨 Provider fallback。

## 2.1 TDX Provider 的内部 Channel

TDX 当前协议事实可按以下逻辑域管理：

```text
tdx
  +-- quotation    # 7709：A 股行情、K线、分时、逐笔等
  +-- extended     # 7727：扩展市场
  +-- goods        # GOODS 协议族
  +-- f10          # F10 文件/资料
  +-- mac          # MAC 协议族
  +-- trade        # 协议研究/边界，非默认量化行情生产能力
```

这些 Channel 均属于 `source=tdx`，但能力和协议语义不同。

## 2.2 同 Channel Endpoint failover

允许：

```text
tdx/quotation host A failed -> host B -> host C
```

前提：

- 同一 Provider；
- 同一 Channel；
- 同一 capability；
- 返回语义相同；
- 数据仍满足 freshness contract。

## 2.3 TDX 内不同 Channel 的切换

只有当 `ChannelRegistry` 明确声明两个 Channel 对某 capability **语义等价**时，才允许内部 resolver 自动选择。

默认规则：

```text
same-source cross-channel = deny unless explicitly equivalent
```

原因：quotation / extended / goods / F10 并非天然可互换。

## 2.4 跨 Provider 永远禁止自动切换

例如：

```text
tdx/quotation bars
```

失败后禁止：

```text
tencent/kline
sina/history_kline
eastmoney/kline
```

即便三者都能返回 K 线，也只能由用户显式选择。

---

# 3. 目标执行架构

```text
Python / Async / CLI / REST / WS / MCP
                    |
                    v
          UnifiedMarketDataService
                    |
                    v
                QuerySpec
 source / channel / market / capability
 freshness / count / start / adjustment
                    |
                    v
             SourceSelector
      # 一次 Query 只选一个 Source
                    |
                    v
             ChannelResolver
     # 只在已选 Source 内选 Channel
                    |
                    v
                QueryPlan
                    |
        +-----------+-----------+
        |                       |
  Batch / SingleFlight     ExecutionBudget
        |                       |
        +-----------+-----------+
                    |
                    v
              SourceManager
                    |
         Source-specific Runtime
                    |
                    v
          EndpointGroup / Session
                    |
                    v
       Canonical Record + ResultMeta
                    |
                    v
             FreshnessGuard
                    |
                    v
          Serializer / Consumer
```

横切运行时：

```text
RuntimeContext
  +-- ConfigSnapshot
  +-- SourceRegistry
  +-- ChannelRegistry
  +-- CapabilityRegistry
  +-- SourceManager
  +-- SourceHealthRegistry
  +-- EndpointHealthRegistry
  +-- ExecutionBudget
  +-- CacheManager
  +-- BatchPlanner
  +-- SingleFlight
  +-- Metrics / Logging
```

---

# 4. Registry 体系：三个事实源，不再混用

## 4.1 SourceRegistry

记录 Provider 级事实：

```python
SourceSpec(
    id="tencent",
    role="auxiliary_live",
    real_data=True,
    markets=("cn_a", "hk", "us", "global"),
    auth="none",
    rate_policy="tencent_default",
    docs="docs/channels/tencent.md",
)
```

SourceRegistry 不记录动态健康状态。

## 4.2 ChannelRegistry

记录 Source 内具体 Channel：

```python
ChannelSpec(
    source="tencent",
    id="kline",
    capabilities=("bars",),
    markets=("cn_a",),
    freshness_class="market_data",
    batch_limit=...,
    endpoint_group="tencent_kline",
    direct_api="md.tencent.kline",
)
```

## 4.3 CapabilityRegistry

记录某业务 capability 可以由哪些 Source/Channel 提供：

```python
CapabilitySpec(
    id="bars",
    default_source="tdx",
    providers={
        "tdx": ("quotation",),
        "tencent": ("kline",),
        "sina": ("history_kline",),
        "eastmoney": ("kline",),
        "baidu": ("kline",),
        "vipdoc": ("day", "minute"),
    },
)
```

注意：`providers` 是“可选来源列表”，不是 fallback 顺序。

---

# 5. SourceSelector：确定性选择，不做健康度跨源选路

## 5.1 显式 Source

```python
md.bars("sh600519", source="tdx")
```

只能执行 TDX。

```python
md.bars("sh600519", source="tencent")
```

只能执行腾讯。

## 5.2 source=None

`source=None` 不是 auto fallback。

它表示：

```text
按 CapabilityRegistry/default_source 确定一个 Source
```

例如：

```text
bars      -> tdx
quotes    -> tdx
minute    -> tdx
trades    -> tdx
hot_rank  -> eastmoney
wencai    -> iwencai
fx_rates  -> boc
```

选定后：

```text
success or error
```

不能继续换 Provider。

## 5.3 用户级默认 Source

允许用户配置确定性 override：

```toml
[defaults]
quotes = "tdx"
bars = "tdx"
hot_rank = "eastmoney"
```

如果用户设置：

```toml
quotes = "tencent"
```

则高层 `md.quotes()` 固定腾讯，腾讯不可用直接报错。

健康状态不能偷偷改写该选择。

---

# 6. API 设计：统一入口 + Source 专属入口同时保留

必须同时满足两类用户。

## 6.1 Unified API

适合策略代码和统一调用：

```python
md.quotes(symbols, source="tdx")
md.bars(symbol, period="day", source="tdx")
md.minute(symbol, source="tdx")
md.trades(symbol, source="tdx")

md.quotes(symbols, source="tencent")
md.bars(symbol, period="day", source="eastmoney")
```

统一入口只暴露跨 Source **语义真的一致**的 capability。

## 6.2 Source Direct API

每个 Source 都必须有明确 namespace：

```python
md.tdx
md.tencent
md.sina
md.eastmoney
md.baidu
md.jsl
md.boc
md.iwencai
md.vipdoc
```

### TDX

```python
md.tdx.quotation.quotes(...)
md.tdx.quotation.bars(...)
md.tdx.quotation.minute(...)
md.tdx.quotation.trades(...)
md.tdx.extended.bars(...)
md.tdx.goods.quote(...)
md.tdx.goods.bars(...)
md.tdx.f10.catalog(...)
md.tdx.f10.download(...)
md.tdx.mac.quote(...)
```

### Tencent

```python
md.tencent.quotes(...)
md.tencent.kline(...)
md.tencent.minute_kline(...)
md.tencent.minute(...)
md.tencent.ticks(...)
md.tencent.global_quotes(...)
md.tencent.market_stat(...)
md.tencent.board_rank(...)
```

### Sina

```python
md.sina.quotes(...)
md.sina.hk_quotes(...)
md.sina.history_kline(...)
md.sina.suggest(...)
md.sina.board_list(...)
md.sina.board_members(...)
md.sina.fund_flow(...)
md.sina.news(...)
```

### Eastmoney

```python
md.eastmoney.quotes(...)
md.eastmoney.kline(...)
md.eastmoney.trends(...)
md.eastmoney.rank(...)
md.eastmoney.fund_flow(...)
md.eastmoney.limit_pool(...)
md.eastmoney.stock_changes(...)
md.eastmoney.northbound(...)
md.eastmoney.hot_rank(...)
md.eastmoney.corporate(...)
md.eastmoney.longhu(...)
md.eastmoney.margin(...)
md.eastmoney.index_constituents(...)
md.eastmoney.fund(...)
```

Source Direct API 的原则：

- 不跨源；
- 不隐藏该 Provider 的特色字段；
- 仍经过统一 symbol/market normalizer、error envelope、freshness/provenance；
- 可以提供 `raw=True` 或 provider-specific model，但必须明确与 canonical model 的区别。

---

# 7. “特有数据”不能被统一模型抹平

多源项目最容易出现的问题是为了统一接口，把 Provider 特有信息全部丢掉。

v12 定义双层数据模型。

## 7.1 Canonical Model

用于跨源一致能力：

```text
Quote
Bar
MinutePoint
Tick
Security
CorporateAction
```

字段只保留有稳定共同语义的内容。

## 7.2 Provider-specific Model

保留渠道特色：

```text
EastmoneyFundFlow
EastmoneyLimitPoolItem
EastmoneyStockChange
EastmoneyHotRank
SinaNewsItem
SinaFundFlow
TencentMarketStat
TencentGlobalQuote
TdxF10CatalogItem
TdxGoodsQuote
JslBondSnapshot
BocFxRate
```

统一 API 不强行把这些塞进 `Quote.extra` 形成无类型大杂烩。

## 7.3 ResultMeta 保留 provenance

每次结果至少包含：

```python
ResultMeta(
    source="tdx",
    channel="quotation",
    market="cn_a",
    capability="bars",
    endpoint_group="tdx_quotation",
    source_timestamp=...,
    received_at=...,
    observed_at=...,
    freshness="fresh",
    real=True,
    replay=False,
    synthetic=False,
    schema_version="bars.v1",
)
```

公网 endpoint/host 的具体 IP 默认只进入诊断日志，不要求公开到业务响应。

---

# 8. Freshness：量化决策数据的第一门禁

用户明确要求“必须获取最新真实数据”。因此 freshness 不是 metadata 装饰，而是硬 gate。

## 8.1 FreshnessProfile

每个 Channel 声明自己的验证方法：

```python
FreshnessProfile(
    class_="realtime_quote",
    evidence=("source_timestamp", "trading_day", "sequence"),
    max_age=...,
    strict=True,
)
```

阈值必须由 capability/channel 配置和真实 benchmark 决定，不在公共代码中到处写 magic seconds。

## 8.2 Freshness Evidence

可使用：

- Source 原生 timestamp；
- 交易日；
- sequence / watermark；
- 数据 bar time；
- HTTP server/update timestamp；
- provider contract + request received time（仅 Registry 明确允许时）。

## 8.3 Freshness 状态

```text
fresh
stale
unverified
not_applicable
```

生产实时 Query 默认只接受 `fresh`。

## 8.4 不允许 stale fallback

```text
live source unavailable + stale cache exists
```

结果：

```text
SourceUnavailable
```

而不是返回 stale cache。

历史已闭合数据可从历史缓存读取，但 ResultMeta 必须明确 `data_class=historical_closed`。

---

# 9. Cache 重新定位

Cache 是性能组件，不是数据源。

## 9.1 实时数据

对于：

```text
quotes
snapshot
current minute
current tick/trades
streaming
```

默认规则：

- 不允许持久 cache 在网络 Source 之前返回结果；
- 允许 request-local memo；
- 允许 SingleFlight 合并同一时刻重复请求；
- 允许非常短生命周期的 response coalescing，但必须仍满足 freshness profile；
- Source 不可用时不使用旧 cache 替代。

## 9.2 历史数据

对已闭合历史数据允许：

```text
canonical historical cache
```

但 key 必须包含：

```text
source
channel
symbol
period
adjustment
schema version
```

不能把 TDX 历史 K 线缓存返回给 `source="sina"` 请求。

## 9.3 Replay / Synthetic

```text
GoldenReplay = test fixture/replay runtime
Synthetic    = test/benchmark only
```

生产 SourceRegistry 中不可见。

---

# 10. Error 设计：错误必须告诉用户“哪个源、哪个渠道、为什么不可用”

保留现有 E1-E9，不推倒重建。

## 10.1 SourceUnavailable

```json
{
  "code": "E7050",
  "type": "SourceUnavailable",
  "message": "TDX 行情源当前不可用",
  "source": "tdx",
  "channel": "quotation",
  "capability": "bars",
  "retryable": true,
  "cross_source_switched": false,
  "request_id": "..."
}
```

## 10.2 ChannelUnavailable

场景：Source 可用，但指定 Channel 不可用。

例如：

```text
source=tdx channel=goods capability=quote
```

Goods endpoint group 全部不可用。

## 10.3 CapabilityUnsupported

场景：用户要求某 Source 不支持该 capability。

例如：

```python
md.sina.hot_rank(...)
```

如果 Sina 没有该能力，应立即报：

```text
CapabilityUnsupported(source=sina, capability=hot_rank)
```

不能换到 Eastmoney。

## 10.4 FreshnessViolation

Source 返回了数据，但不满足实时要求：

```text
FreshnessViolation(source=tencent, channel=quote, age=...)
```

## 10.5 DataIntegrityError

真实响应解析后违反 canonical contract：

```text
invalid timestamp
impossible price
truncated payload
schema mismatch
```

不能当成 SourceUnavailable 后跨源继续。

## 10.6 ErrorEnvelope 必须统一跨入口

Python / REST / WS / MCP / CLI JSON 都共享：

```text
code
type
message
source
channel
capability
market
phase
retryable
request_id
context
```

---

# 11. FailurePolicy：只允许同源恢复

应用层不再存在“跨源 fallback disposition”。

```python
FailureDisposition(
    retry_same_endpoint=False,
    switch_endpoint=True,
    switch_channel=False,
    switch_source=False,
    terminal=False,
)
```

决策优先级：

```text
1. Query source/channel contract
2. Capability support
3. Freshness requirement
4. ExecutionBudget
5. Endpoint health
6. RetryAdvice
7. same-source endpoint recovery
8. terminal source/channel error
```

`switch_source` 在生产 Query 中永远为 False。

---

# 12. SourceHealth 与 EndpointHealth 分层

## 12.1 EndpointHealth

例如：

```text
tdx/quotation/host-A
```

记录：

```text
latency
failure count
cooldown
last success
last error
```

可驱动同 Channel 内 endpoint 选择。

## 12.2 ChannelHealth

当所有 endpoint 失败：

```text
channel unhealthy
```

## 12.3 SourceHealth

Source 层根据 Channel 聚合，只用于：

- health endpoint；
- observability；
- 用户诊断；
- 运维告警。

**不能因为 SourceHealth 差就自动选择另一个 Provider。**

---

# 13. Current Web Registry 的结构性重构

当前 Web 注册表将以下内容并列为 Source：

```text
sina
tencent
eastmoney
hk
us
kline
minute_kline
minute
ticks
trends
rank
fund_flow
limit_pool
northbound
corporate
news
...
```

这会造成 API 和文档混乱。

v12 迁移目标：

## 13.1 Provider 归并

### Tencent

```text
source=tencent
channels:
  quote
  kline
  minute_kline
  minute
  ticks
  global
  market_stat
  board_rank
markets:
  cn_a
  hk
  us
  global
```

### Sina

```text
source=sina
channels:
  quote
  hk_quote
  history_kline
  suggest
  industry_board
  board_list
  board_member
  fund_flow
  news
markets:
  cn_a
  hk
```

### Eastmoney

```text
source=eastmoney
channels:
  quote
  kline
  trends
  rank
  fund_flow
  limit_pool
  stock_changes
  northbound
  hot_rank
  corporate
  longhu
  margin
  index_constituents
  fund
```

### Baidu

```text
source=baidu
channels:
  quote
  kline
  minute
  ticks
```

### Others

```text
source=jsl      channels=bond, etf
source=boc      channels=fx
source=iwencai  channels=screening
source=vipdoc   channels=day, minute
```

## 13.2 旧 Source ID 迁移

旧的：

```text
kline
minute_kline
minute
ticks
hk
us
```

不得继续作为顶层 source 的长期公共概念。

兼容层可暂时接受，并映射到明确 `(source, channel, market)`，同时发 deprecation warning。

---

# 14. 每个数据渠道必须有独立文档

新增文档体系：

```text
docs/channels/
  README.md
  tdx.md
  tencent.md
  sina.md
  eastmoney.md
  baidu.md
  jsl.md
  boc.md
  iwencai.md
  vipdoc.md
```

每份 Source 文档必须包含：

1. Source 定位；
2. 支持市场；
3. Channel 列表；
4. 每个 Channel 的 capability；
5. 统一 API 用法；
6. Direct API 用法；
7. 输入参数；
8. 返回模型；
9. Provider-specific 字段；
10. freshness 验证方式；
11. 限流/认证/Referer/Cookie；
12. 批量限制；
13. 已知异常；
14. 错误码；
15. 当前 production/experimental/degraded 状态；
16. 示例；
17. 对应 tests/fixtures；
18. 对应 registry 条目。

文档不能只写“支持 K 线”，必须明确：

```text
source=tencent
channel=kline
markets=cn_a
periods=...
adjustments=...
window semantics=...
freshness=...
return schema=...
```

---

# 15. Channel Documentation 必须机器可校验

建议生成：

```text
docs/generated/sources.json
docs/generated/channels.json
docs/generated/capabilities.json
docs/generated/errors.json
```

CI 校验：

```text
registered source -> docs/channels/<source>.md exists
registered channel -> documented
public direct API -> registry entry exists
registry direct API -> callable exists
capability provider mapping -> conformance test exists
source/channel status -> docs status matches
```

禁止继续手工维护一个易漂移的“支持 43 个接口”数字。

---

# 16. Direct API 与 Unified API 的关系

## 16.1 Unified API 是 canonical capability 层

例如：

```python
md.bars(symbol, source="tdx")
md.bars(symbol, source="tencent")
```

要求：

- 相同输入语义；
- 相同 canonical Bar；
- source-specific metadata 保留；
- 不跨源。

## 16.2 Direct API 保留 Source 特色

例如东财资金流：

```python
md.eastmoney.fund_flow(symbol)
```

不需要强行伪装成通用 `Quote`。

## 16.3 Provider raw API

必要时：

```python
md.eastmoney.raw(...)
```

必须明确标记 advanced/debug，不进入默认策略 API，也不保证 canonical schema。

---

# 17. 跨源比较是独立功能，不是 fallback

为量化决策提供显式 compare API：

```python
md.compare.quotes(
    ["sh600519"],
    sources=["tdx", "tencent", "sina", "eastmoney"],
)
```

执行：

```text
tdx        -> independent result/error
tencent    -> independent result/error
sina       -> independent result/error
eastmoney  -> independent result/error
```

返回：

```python
MultiSourceResult(
    results={...},
    errors={...},
    comparison={
        "price_spread": ...,
        "timestamp_skew": ...,
        "fresh_sources": ...,
    },
)
```

禁止：

```text
sina failed -> fill its value with tencent
```

## 17.1 数据质量监控

允许后台统计：

```text
source freshness
latency
availability
schema error rate
cross-source price divergence
```

这些指标用于决策信息和运维，不自动改变 Query 的 source。

---

# 18. BatchPlanner：批量也必须 source-bound

```text
QuerySpec(source=tdx, symbols=1000)
       |
normalize + dedup
       |
resolve tdx channel
       |
chunk by tdx channel batch limit
       |
bounded parallelism
       |
TDX endpoint pool
       |
fan-out original order
```

不能把一部分 symbol 因 TDX 失败自动转到腾讯。

批量 partial failure：

```python
BatchResult(
    source="tdx",
    data={...},
    errors={...},
    partial=True,
)
```

错误仍然全部属于选定 Source。

---

# 19. SingleFlight：减少真实上游请求，不改变来源

Key 必须至少包含：

```text
source
channel
capability
market
symbols
period
start/count
adjustment
freshness profile
schema version
```

因此：

```text
tdx bars != tencent bars
```

绝不共享 SingleFlight，也绝不共享 source-scoped realtime cache。

---

# 20. Streaming：每条订阅绑定 Source

```python
md.stream.quotes(
    ["sh600519"],
    source="tdx",
    interval=1.0,
)
```

生命周期：

```text
Source=tdx
  -> Channel=quotation
  -> endpoint failover inside tdx
  -> reconnect
  -> continue
```

如果 TDX 整体不可用：

```text
stream error SourceUnavailable(source=tdx)
```

不能自动切腾讯。

多个来源需要用户明确建多个订阅：

```python
tdx_stream = md.stream.quotes(..., source="tdx")
tencent_stream = md.stream.quotes(..., source="tencent")
```

可以在上层 `MultiSourceStream` 进行比较，但不替代。

---

# 21. HTTP / WS / MCP / CLI 必须暴露 source/channel

## REST

```http
GET /v1/quotes?symbols=...&source=tdx
GET /v1/bars/{symbol}?source=tdx&period=day
```

Source 专属：

```http
GET /v1/sources/tdx/quotation/quotes
GET /v1/sources/eastmoney/fund-flow/{symbol}
GET /v1/sources/sina/news/{symbol}
```

## WS

订阅请求：

```json
{
  "method": "subscribe_quotes",
  "source": "tdx",
  "symbols": ["sh600519"]
}
```

## MCP

Tool metadata 必须声明：

```text
source
channel
capability
freshness class
```

## CLI

```bash
tstdx quote sh600519 --source tdx
tstdx bars sh600519 --source tencent

tstdx source tdx quotation quotes sh600519
tstdx source eastmoney hot-rank
```

---

# 22. 执行效率优化

不跨 Source fallback 后，执行链可以更简单、更快。

优先级：

1. TDX persistent connection pool；
2. 每 Provider persistent HTTP session；
3. 每 Channel endpoint pool；
4. endpoint health 快速选择；
5. BatchPlanner；
6. SingleFlight；
7. symbol normalize once；
8. canonical record 一次转换；
9. lazy serialization；
10. 真实 benchmark 后再做 parser micro-optimization。

避免：

```text
TDX timeout 5s
+ Tencent timeout 5s
+ Sina timeout 5s
+ Eastmoney timeout 5s
```

新的请求最大延迟只受**选定 Source 内部预算**控制。

---

# 23. ExecutionBudget

```python
ExecutionBudget(
    deadline=...,
    max_endpoint_attempts=...,
    max_channel_attempts=...,
)
```

无 `max_source_switches`，因为 production Query 不切 Source。

TDX：

```text
budget consumed by host A/B/C
```

腾讯：

```text
budget consumed only by Tencent channel endpoints
```

---

# 24. SourceManager 结构

建议：

```text
tstdx/runtime/
  context.py
  service.py
  query.py
  result.py
  selection.py
  budget.py

 tstdx/sources/
  registry.py
  channel_registry.py
  capability_registry.py
  manager.py
  health.py
  base.py
  tdx/
    provider.py
    quotation.py
    extended.py
    goods.py
    f10.py
    mac.py
  web/
    tencent.py
    sina.py
    eastmoney.py
    baidu.py
    jsl.py
    boc.py
    iwencai.py
  vipdoc.py
```

现有代码可以逐步迁移，不要求一次物理搬目录。

---

# 25. Provider Adapter Contract

每个 Channel 实现：

```python
class ChannelAdapter:
    def prepare(self, query): ...
    def fetch(self, prepared, budget): ...
    def normalize(self, raw): ...
    def freshness(self, result): ...
```

必须同时声明：

```text
source_id
channel_id
capabilities
markets
batch_limits
freshness_profile
rate_policy
schema versions
endpoint group
```

Provider Conformance 测试统一执行。

---

# 26. Provider Conformance Test

每个 Channel 必须验证：

```text
source identity preserved
no cross-source imports/execution
input normalization
market validation
batch limits
empty response semantics
missing field semantics
unit normalization
freshness evidence
error mapping
endpoint failover only inside provider
close/lifecycle
deadline/cancellation
schema version
```

关键断言：

```python
assert result.meta.source == requested_source
assert all(a.source == requested_source for a in result.meta.attempts)
```

---

# 27. Architecture Gate：从代码层禁止跨站 fallback

CI 增加静态/动态门禁。

## 27.1 Static import guard

例如 TDX Provider 模块禁止导入：

```text
tstdx.web.sina
tstdx.web.tencent
tstdx.web.eastmoney
```

## 27.2 Runtime attempt invariant

任何 Query：

```text
len(unique(attempt.source)) == 1
```

除非 capability 明确是 `multi_source_compare`。

## 27.3 Explicit source invariant

```text
source=tdx       -> attempts only tdx
source=tencent   -> attempts only tencent
source=sina      -> attempts only sina
source=eastmoney -> attempts only eastmoney
```

---

# 28. 文档与接口治理

Registry 是 API/Docs 的 SSOT。

推荐生成：

```text
Source Overview
Source -> Channel Matrix
Capability -> Available Sources Matrix
Direct API Reference
Freshness Matrix
Rate/Auth Matrix
Error Matrix
```

README 只做入口，详细能力放 `docs/channels/`。

每次新增 Channel 的 DoD：

1. Registry；
2. Adapter；
3. Direct API；
4. Unified capability mapping（如适用）；
5. Error/freshness；
6. Conformance tests；
7. Channel docs；
8. benchmark；
9. API/docs registry gate。

---

# 29. 当前代码需要优先移除/修复的跨源逻辑

Phase 0 必须先处理：

1. `DataSourceRouter` 中 `tdx -> web -> reader -> cache -> synthetic` 生产降级；
2. `UnifiedQuoteAPI` 的 auto route 依次尝试多个 Provider；
3. `security_list()` TDX CommandOffline 后自动转东财；
4. minute/trades/block 等 TDX 失败后隐式 Web；
5. WebQuoteClient 内多个 Provider 的默认 fallback order；
6. generic `KLINE/MINUTE/TICKS` source ID；
7. quote/kline cache 在 source selection 前短路；
8. Golden/Synthetic 被视为生产 source；
9. REST/WS/MCP 自行选择 direct client/Web fallback；
10. broad Exception 被包装成 SourceUnavailable 后继续尝试其它 Provider。

这些不是“可优化项”，而是新的产品契约下必须删除的旧行为。

---

# 30. 实施路线

## Phase 0 — Source Boundary Freeze

目标：先确保绝不跨站。

- 固化 Source ID；
- source explicit fail-closed；
- source=None 固定 default source；
- 删除生产跨 Provider fallback；
- 保留 TDX host failover；
- cache source-scoped；
- replay/synthetic 移出 production registry；
- SourceUnavailable 统一 E7050；
- cross-source attempt invariant tests。

## Phase 1 — Source / Channel / Market Registry

- SourceRegistry；
- ChannelRegistry；
- CapabilityRegistry；
- Market scope；
- 旧 Web SourceSpec 拆维度；
- generic channel IDs deprecate；
- generated docs skeleton。

## Phase 2 — TDX Provider Runtime

优先把核心主源做好：

- quotation persistent pool；
- extended/goods/F10/MAC lifecycle；
- endpoint health；
- family/channel registry；
- ExecutionBudget；
- ResultMeta；
- strict freshness；
- async lifecycle。

## Phase 3 — Auxiliary Provider Runtime

逐个 Provider 收敛：

1. Tencent；
2. Sina；
3. Eastmoney；
4. Baidu；
5. JSL/BOC/iWencai；
6. Vipdoc historical。

每个 Provider 独立通过 conformance。

## Phase 4 — Direct API + Docs

- `md.tdx.*`；
- `md.tencent.*`；
- `md.sina.*`；
- `md.eastmoney.*`；
- 其余 Provider；
- `docs/channels/*`；
- generated matrices；
- examples。

## Phase 5 — Unified Service

只把真正 canonical 的能力接入：

```text
quotes
bars
minute
trades
security metadata
...
```

Source-specific 数据保持 direct API。

## Phase 6 — Performance

- persistent pools；
- BatchPlanner；
- SingleFlight；
- source-scoped historical cache；
- streaming scheduler；
- lazy serialization；
- benchmark gate。

## Phase 7 — Integrations

REST / WS / MCP / CLI 全面携带 source/channel，不再自行 fallback。

## Phase 8 — Compatibility Cleanup

- `route=auto` deprecate；
- generic web source IDs deprecate；
- old facade fallback code remove；
- orphan config remove；
- docs/README 同步。

---

# 31. 推荐提交拆分

```text
1. fix: enforce source-bound execution and remove cross-provider fallback
2. fix: keep tdx host failover while blocking web substitution
3. refactor: split source channel market and endpoint identities
4. refactor: introduce source channel and capability registries
5. refactor: build persistent tdx provider runtime
6. refactor: normalize tencent provider and channels
7. refactor: normalize sina provider and channels
8. refactor: normalize eastmoney provider and channels
9. refactor: add provider direct api namespaces
10. refactor: add strict freshness and source-scoped result metadata
11. perf: add source-bound batch singleflight and historical cache
12. refactor: bind streaming rest ws mcp cli to explicit sources
13. docs: generate channel capability freshness and error catalogs
14. chore: remove legacy auto fallback and generic channel source ids
```

---

# 32. Benchmark 重新定义

必须分别测各 Source，不混合成“自动路由性能”。

TDX：

```text
quote single/batch
bars 320/2000
host failover latency
connection reuse
stream 100/1000 symbols
```

Tencent/Sina/Eastmoney：

```text
channel latency
batch limit
rate limiter behavior
session reuse
freshness lag
```

Cross-source comparison 作为单独 workload。

指标：

```text
p50/p95/p99
upstream requests
connection reuse
endpoint switches
freshness lag
error rate
schema failures
allocations
```

---

# 33. Definition of Done

## Source Boundary

- [ ] TDX 失败永不自动请求 Sina/Tencent/Eastmoney；
- [ ] TDX 内 host pool 可正常 failover；
- [ ] 每个 Query attempts 只包含一个 Source；
- [ ] compare API 除外且明确 multi-source；
- [ ] cache 不跨 Source；
- [ ] replay/synthetic 不进入生产 SourceRegistry。

## Interface

- [ ] 每个 Source 有稳定 ID；
- [ ] 每个 Source 有 Direct API；
- [ ] 每个 Channel 有明确 ID；
- [ ] hk/us 等归 Market，不再冒充 Source；
- [ ] kline/minute/ticks 等归 Channel，不再冒充 Source；
- [ ] Unified API 接受 source；
- [ ] Direct API 保留 source-specific data。

## Freshness / Truth

- [ ] live Query 必须通过 FreshnessGuard；
- [ ] stale/unverified 不冒充 fresh；
- [ ] historical/realtime 明确区分；
- [ ] ResultMeta 有 source/channel/timestamp；
- [ ] 数据源不可用直接报错。

## Docs

- [ ] `docs/channels/README.md` 完整索引；
- [ ] 每个生产 Source 有独立文档；
- [ ] 每个 Channel 有 capability 表；
- [ ] Direct API 有示例；
- [ ] freshness/rate/auth/error 有说明；
- [ ] Registry ↔ API ↔ docs CI 双向一致。

## Performance

- [ ] TDX pool 跨 query 复用；
- [ ] Web provider session 跨 query 复用；
- [ ] endpoint health 复用；
- [ ] BatchPlanner source-bound；
- [ ] SingleFlight source-bound；
- [ ] 无多 Source timeout 叠加；
- [ ] benchmark 分 Source 建立基线。

---

# 34. 最终对用户的能力表达

`tstdx` 不再告诉用户：

> “我们会自动帮你找一个能返回数据的来源。”

而是明确告诉用户：

> “TDX 是默认主行情源；你也可以显式选择腾讯、新浪、东财等真实数据渠道。每个渠道有独立能力和特有数据。选中的渠道不可用时我们会明确报告不可用，不会偷偷更换来源。所有实时结果都带来源、渠道和时间信息，只有通过最新性与真实性检查的数据才进入量化决策链。”

这是 v12 后续实现的最高级架构约束。
