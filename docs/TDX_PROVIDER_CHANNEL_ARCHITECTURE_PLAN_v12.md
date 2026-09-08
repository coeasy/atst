# tstdx：TDX 主 Provider + 多 Provider 独立数据通道架构与优化方案 v12

> Branch: `refactor/industry-benchmark-v12`  
> Main baseline: `main@f927e7faf49e77352b531addc49ecc6e44abcf81` (`v1.4.0`)  
> Terminology ADR: `docs/adr/ADR-013-provider-source-terminology.md`  
> Provider docs: `docs/providers/`  
> Updated: 2026-09-08  
> Status: **Current execution SSOT / 当前唯一执行基线**

---

# 0. 产品定位

`tstdx` 面向量化研究与交易决策，核心是：

> **以 TDX 为默认主 Provider，同时完整暴露腾讯、新浪、东财、百度、集思录、中行、i问财等独立 Provider 的真实数据能力。每个 Provider 都有自己的 Channel、特色数据和 Direct API。用户可以明确选择 Provider；选定 Provider 不可用时直接报错，不允许跨 Provider 静默替代。实时/决策数据必须最新、真实、可追溯。**

核心硬约束：

1. TDX 是默认主 Provider；
2. TDX 内部允许 host/endpoint failover；
3. TDX K 线失败不得自动调用新浪/腾讯/东财 K 线；
4. 腾讯、新浪、东财等都是独立 Provider，不是 TDX fallback；
5. Provider-specific 数据必须有明确 Direct API 和文档；
6. Unified API 只统一真正同语义的 Capability；
7. 实时结果必须通过 freshness + integrity guard；
8. stale/replay/synthetic 不得冒充实时真实数据；
9. 跨 Provider 比较允许，跨 Provider 自动替代禁止；
10. `source` 不是第二层对象，只是 Provider selector 的兼容术语。

---

# 1. 唯一术语模型

正式模型只有：

```text
Provider
  |
  +-- Channel
        |
        +-- Capability
              |
              +-- Endpoint / Host

Query dimensions:
  Market / AssetClass / Freshness / Schema / TimeWindow
```

## 1.1 Provider：谁提供数据

规范 Provider ID：

```text
tdx
tencent
sina
eastmoney
baidu
jsl
boc
iwencai
```

Provider 决定：

- provenance；
- 会话生命周期；
- auth/cookie/referer；
- rate/batch policy；
- health/circuit；
- 不允许跨站替代的边界。

### Provider 与 source

可以在自然语言里称 TDX/新浪/腾讯为“行情源”，但内部只有 `Provider` 实体。

```text
source="tdx" -> ProviderId("tdx")
source="sina" -> ProviderId("sina")
```

目标内部结构不允许同时存在：

```text
Source(name="tdx")
Provider(name="tdx")
```

只保留 Provider。

## 1.2 Channel：Provider 内部哪条数据通道

示例：

```text
tdx/quotation
tdx/extended
tdx/goods
tdx/f10
tdx/mac
tdx/vipdoc

tencent/quote
tencent/kline
tencent/minute
tencent/ticks

eastmoney/fund_flow
eastmoney/limit_pool
eastmoney/corporate
```

Channel 是实现/业务接口族，不等于 Provider。

## 1.3 Capability：用户要什么

示例：

```text
quotes
bars
minute
trades
finance
capital_changes
fund_flow
hot_rank
news
screening
fx_rates
```

Capability 是统一服务和 Registry 的业务语义单位。

## 1.4 Market / AssetClass：数据属于哪里

```text
cn_a
hk
us
future
option
commodity
fund
bond
fx
```

`hk/us` 不是 Provider。

## 1.5 Endpoint / Host：实际连接到哪里

例如：

```text
provider=tdx
channel=quotation
endpoint=hostA:7709
endpoint=hostB:7709
```

Endpoint/Host failover 不改变 Provider。

---

# 2. Provider 与 source 的 API 决策

## 2.1 新 API 推荐 provider=

```python
md.quotes(["sh600519"], provider="tdx")
md.quotes(["sh600519"], provider="tencent")
md.bars("sh600519", provider="sina")
```

## 2.2 兼容 source=

```python
md.quotes(["sh600519"], source="tdx")
```

归一化：

```text
provider only -> accept
source only -> map to provider
provider == source -> compatibility accept
provider != source -> ValidationError
neither -> CapabilityRegistry.default_provider
```

内部 `QuerySpec` 只有：

```python
QuerySpec(
    provider="tdx",
    capability="bars",
    ...,
)
```

不存第二个 source 状态。

---

# 3. Provider Registry / Channel Registry / Capability Registry

## 3.1 ProviderRegistry

静态 Provider 事实：

```python
ProviderSpec(
    id="tdx",
    display_name="TDX",
    role="primary_live",
    markets=(...),
    channels=(...),
    auth_policy=...,
    rate_policy=...,
    production=True,
)
```

## 3.2 ChannelRegistry

```python
ChannelSpec(
    provider="tdx",
    id="quotation",
    protocol="7709",
    endpoint_group="quotation_hosts",
    capabilities=("quotes", "bars", "minute", "trades"),
    freshness_profile=...,
    batch_limit=...,
)
```

## 3.3 CapabilityRegistry

```python
CapabilitySpec(
    id="quotes",
    providers=("tdx", "tencent", "sina", "eastmoney", "baidu"),
    default_provider="tdx",
    result_schema="Quote@v1",
    freshness_profile="live_quote",
)
```

三张表各自只表达一种事实，禁止互相复制动态状态。

## 3.4 ProviderHealthRegistry

动态健康状态与静态 Registry 分离：

```text
ProviderRegistry       = 理论能力
ProviderHealthRegistry = 当前健康
EndpointHealth         = 具体主站/endpoint 健康
```

TDX 某个 host 失败不修改 TDX capability。

---

# 4. TDX Provider 模型

TDX Provider 下至少有：

```text
quotation   7709
extended    7727
goods       GOODS
f10         F10
mac         MAC
vipdoc      local historical
```

`vipdoc` 不再与新浪/腾讯并列为 Provider。

## 4.1 quotation

典型 Capability：

```text
quotes
bars
minute
trades
security_count
security_list
finance
capital_changes
snapshot
```

只有 Command Ledger 已验证 online 的能力才能进入 production capability。

## 4.2 host failover

合法：

```text
tdx/quotation host A -> host B -> host C
```

这是 Provider 内部 host failover。

## 4.3 channel resolution

不同 TDX Channel 默认不可互换。

只有 Registry 明确声明相同 Capability 语义等价时，Provider 内 resolver 才能选择另一个 Channel。

## 4.4 跨 Provider switch 禁止

```text
tdx/bars failed -> tencent/bars   # forbidden
tdx/bars failed -> sina/bars      # forbidden
```

全部 TDX host 失败：

```text
AllHostsUnreachable / SourceUnavailable(provider=tdx)
```

到此结束。

---

# 5. 其它 Provider 不是 fallback

## Tencent

主要 Channel：

```text
quote
kline
minute_kline
minute
ticks
global
market_stat
board_rank
```

## Sina

```text
quote
history_kline
suggest
industry_board
board_list
board_member
fund_flow
news
```

## Eastmoney

```text
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

## Baidu

```text
quote
kline
minute
ticks
```

## JSL

```text
bond
etf
```

## BOC

```text
fx
```

## iWencai

```text
screening
```

完整 Direct API 与语义见 `docs/providers/`。

---

# 6. Unified API 与 Direct Provider API

## 6.1 Unified API

只统一真正具有共同业务语义的 Capability。

```python
md.quotes(symbols, provider="tdx")
md.quotes(symbols, provider="tencent")
md.bars(symbol, provider="tdx")
md.bars(symbol, provider="eastmoney")
```

Unified API 做：

```text
normalize input
validate Provider capability
resolve Provider/Channel once
execute
normalize canonical model
freshness/integrity guard
return QueryResult
```

Unified API 不做：

```text
cross-provider fallback
provider-specific field destruction
stale cache substitution
synthetic/replay substitution
```

## 6.2 Direct Provider API

```python
md.tdx.quotation.quotes(...)
md.tdx.goods.bars(...)
md.tencent.ticks(...)
md.sina.news(...)
md.eastmoney.fund_flow(...)
md.baidu.minute(...)
md.jsl.bonds(...)
md.boc.fx_rates(...)
md.iwencai.screen(...)
```

Provider-specific capability 应有独立 schema，而不是全部压成 `dict/extra`。

---

# 7. QuerySpec / QueryPlan

```python
QuerySpec(
    provider="tdx",
    capability="bars",
    symbols=("sh600519",),
    period="day",
    start=0,
    count=320,
    adjustment="raw",
    freshness="strict",
)
```

Planner 编译成单 Provider 计划：

```python
QueryPlan(
    provider="tdx",
    channel="quotation",
    capability="bars",
    endpoint_group="quotation_hosts",
    budget=...,
    freshness_profile=...,
)
```

硬约束：

```python
len(plan.providers) == 1
```

生产计划不包含 Provider fallback candidates。

---

# 8. Freshness：量化数据必须最新

不同 Capability 使用不同 FreshnessProfile：

```text
live_quote
current_minute
current_ticks
current_bar_tail
provider_intraday_rank
business_day_event
daily_nav
historical_closed_bar
```

结果至少记录：

```text
provider
channel
source_timestamp
received_at
observed_at
age
trading_day
freshness
real
```

实时数据无法证明 fresh 时：

```text
FreshnessViolation
```

不能使用其它 Provider 或旧 cache 掩盖。

---

# 9. Cache：只优化，不改变 Provider 或时效语义

## 9.1 实时数据

默认不允许持久缓存短路 Provider：

```text
quotes
snapshot
current minute
current ticks
streaming
current unfinished bar
```

允许：

- request-local memo；
- very-short coalescing；
- SingleFlight；
- 在 freshness contract 内明确允许的极短 TTL memory cache。

但 metadata 必须保留原 Provider 和 source timestamp。

## 9.2 历史数据

已闭合历史 K 线允许 canonical cache。

Cache key 至少：

```text
provider
capability
symbol
period
adjustment
schema version
window semantics
```

不能把 TDX K 线缓存拿去满足 `provider=sina` 请求。

## 9.3 Replay/Synthetic

Golden Replay / Synthetic 不属于 ProviderRegistry，只能在 test/replay runtime 显式启用。

---

# 10. Error 契约

不重写 E1-E9。

现有：

```text
SourceUnavailable(E7050)
```

继续保留，规范解释为：

> selected Provider cannot satisfy the query.

context：

```json
{
  "provider":"tdx",
  "channel":"quotation",
  "capability":"bars",
  "provider_switched":false
}
```

如果需要新术语，只允许 alias：

```python
ProviderUnavailable = SourceUnavailable
```

必须是同一 class object、同一错误码，不能再生第二套错误树。

Provider 内部错误可细分：

```text
AllHostsUnreachable
EndpointUnavailable
ChannelUnavailable
CapabilityUnsupported
RateLimited
AuthenticationRequired
ProviderRejected
FreshnessViolation
DataIntegrityError
CommandOffline
```

---

# 11. ResultMeta / Provenance

```python
ResultMeta(
    provider="tdx",
    channel="quotation",
    capability="quotes",
    market="cn_a",
    source_timestamp=...,
    received_at=...,
    observed_at=...,
    freshness="fresh",
    real=True,
    cache_status="miss",
    attempts=(...),
)
```

内部只存 `provider`。

兼容 JSON 可临时输出：

```json
{"provider":"tdx","source":"tdx"}
```

但 source 只是 alias。

---

# 12. Multi-Provider 比较

禁止自动替代，不等于禁止主动多源比较。

```python
md.compare_quotes(
    ["sh600519"],
    providers=["tdx", "tencent", "sina"],
)
```

语义：

```text
launch independent tdx query
launch independent tencent query
launch independent sina query
collect each success/error/provenance
never substitute one for another
```

用途：

- 行情对账；
- 延迟比较；
- 异常价格发现；
- Provider quality scoring；
- 量化特征融合。

---

# 13. 性能设计

性能来自同 Provider 内部资源复用，而不是跨站 fallback。

优先级：

1. persistent TDX pools；
2. persistent per-Provider HTTP sessions；
3. BatchPlanner；
4. symbol normalize/dedup once；
5. SingleFlight；
6. Provider/Endpoint health 快速失败；
7. total ExecutionBudget；
8. lazy serialization；
9. benchmark 后 parser micro-optimization。

BatchPlanner 绝不能把一次 `provider=tdx` 请求拆成多 Provider。

---

# 14. Streaming

Streaming 一旦选择 Provider，全生命周期绑定该 Provider：

```python
md.stream.quotes(symbols, provider="tdx", interval=1.0)
```

TDX streaming 允许：

```text
TDX host reconnect
TDX endpoint failover
TDX re-subscribe
```

禁止：

```text
TDX stream failure -> Tencent polling
```

每 Provider 使用统一 StreamBackend contract，但不跨 Provider 接管订阅。

---

# 15. Integration

HTTP / WS / MCP / CLI 都只做协议适配：

```text
parse provider/source alias
build QuerySpec(provider=...)
call UnifiedMarketDataService
serialize QueryResult/ErrorEnvelope
```

Integration 不允许自行：

```text
new TdxClient/WebClient
choose fallback Provider
invoke another Provider after failure
invent error strings
```

---

# 16. 配置模型

目标：

```text
providers.tdx.enabled
providers.tencent.enabled
providers.sina.enabled
providers.eastmoney.enabled
...

providers.tdx.channels.quotation.hosts
providers.tencent.channels.quote.rate_limit
...
```

废弃含义模糊的：

```text
sources.order
DEFAULT_FALLBACK_ORDER
auto source fallback
continue_on_error meaning switch provider
```

兼容字段只做解析映射，不成为第二个运行时事实源。

---

# 17. 文档模型

Provider 文档统一：

```text
docs/providers/README.md
docs/providers/tdx.md
docs/providers/tencent.md
docs/providers/sina.md
docs/providers/eastmoney.md
docs/providers/baidu.md
docs/providers/jsl.md
docs/providers/boc.md
docs/providers/iwencai.md
```

每份必须列：

```text
Provider identity
Markets
Channels
Capabilities
Unified API
Direct API
Provider-specific schema
units
freshness
auth/rate
endpoint failover
errors
status
tests
benchmarks
registry entry
```

---

# 18. 当前代码中需要迁移的概念

现有 Web registry 把以下不同维度混在 source ID 中：

```text
sina/tencent/eastmoney       # Provider
hk/us                         # Market
a kline/minute/ticks          # Channel/Capability
fund_flow/news/rank           # Channel/Capability
```

v12 需要拆为：

```text
ProviderRegistry
ChannelRegistry
CapabilityRegistry
Market metadata
```

例如：

```text
旧: source=hk
新: provider=tencent, channel=quote, market=hk

旧: source=minute_kline
新: provider=tencent, channel=minute_kline, capability=bars
```

---

# 19. P0 正确性改造

在大架构前优先修：

1. 删除跨 Provider fallback；
2. `security_list(provider=tdx)` offline 后不得转东财；
3. Router generic Exception 不得转换成“换 Provider”理由；
4. realtime cache 不得跨 Provider/时效短路；
5. M5 vipdoc reader 修正；
6. local lc1/lc5 高层链路接通为 `tdx/vipdoc`；
7. duplicate SourceUnavailable 合并到 errors.py E7050；
8. REST/WS/MCP source/provider 参数统一；
9. `adjusted_bars(events=[])` 语义修复；
10. Task cancel/delete 资源漏洞修复。

---

# 20. 实施阶段

## Phase 0 — Terminology + No Cross-Provider

- ADR-013；
- Provider docs；
- compatibility fixture；
- no-fallback tests；
- P0 correctness fixes。

## Phase 1 — Registries + Query Contract

新增：

```text
ProviderId
ProviderSpec
ProviderRegistry
ChannelSpec
ChannelRegistry
CapabilitySpec
CapabilityRegistry
QuerySpec(provider=...)
ResultMeta(provider=...)
ErrorEnvelope
FreshnessProfile
```

## Phase 2 — ProviderManager

替代概念上重叠的 SourceManager：

```text
ProviderManager
  +-- TdxProviderRuntime
  +-- TencentProviderRuntime
  +-- SinaProviderRuntime
  +-- EastmoneyProviderRuntime
  +-- ...
```

每个 Runtime 持有自己的 Channel client/session/pool。

## Phase 3 — Single-Provider QueryPlanner

`QueryPlan` 只允许一个 Provider。

实现：

- Channel resolver；
- Endpoint resolver；
- ExecutionBudget；
- FreshnessGuard；
- IntegrityGuard；
- FailurePolicy（只允许同 Provider 恢复）。

## Phase 4 — Cache / Batch / SingleFlight

所有 key 包含 Provider；Batch 不跨 Provider；SingleFlight 只合并完全相同 Provider Query。

## Phase 5 — Direct Provider API + Unified API

完成每个 Provider 独立 namespace、特色 schema、文档和 conformance。

## Phase 6 — HTTP / WS / MCP / CLI

统一 provider/source alias 解析，全部调用 Service。

## Phase 7 — Async / Streaming / Tasks

绑定单 Provider 生命周期，完善 scheduler/backpressure/cancellation。

## Phase 8 — Remove Legacy Mixed Source Model

删除/迁移：

```text
SourceRegistry
SourceManager
DEFAULT_FALLBACK_ORDER
sources.order
hk/us source IDs
kline/minute/ticks source IDs
vipdoc standalone source
```

保留必要 compatibility aliases，但不保留第二份状态。

---

# 21. CI Architecture Gates

必须增加：

```text
ProviderRegistry IDs == docs/providers IDs
no production SourceRegistry class
no production SourceManager class
QuerySpec stores provider only
ResultMeta stores provider only
source alias maps to ProviderId
Provider QueryPlan contains exactly one Provider
no cross-provider attempts after failure
hk/us not Provider IDs
kline/minute/ticks not Provider IDs
vipdoc only tdx/vipdoc
cache key includes provider
SingleFlight key includes provider
integration cannot direct-new provider clients
```

---

# 22. Definition of Done

## 术语

- [ ] Provider/source 不再是两个领域实体；
- [ ] `provider=` 为正式新 API；
- [ ] `source=` 仅兼容 alias；
- [ ] Provider → Channel → Capability → Endpoint 层级清晰；
- [ ] Market 独立维度。

## 数据正确性

- [ ] TDX K 线失败不去新浪/腾讯/东财；
- [ ] 每 Provider 失败独立报错；
- [ ] 实时数据通过 freshness；
- [ ] stale/replay/synthetic 不冒充实时；
- [ ] cache 不跨 Provider；
- [ ] Provider-specific schema 不丢字段。

## TDX

- [ ] quotation/extended/goods/F10/MAC/vipdoc 清晰；
- [ ] 同 Channel host failover；
- [ ] Provider identity 始终 tdx；
- [ ] Command Ledger 事实不被架构改写。

## Provider 文档

- [ ] TDX；
- [ ] Tencent；
- [ ] Sina；
- [ ] Eastmoney；
- [ ] Baidu；
- [ ] JSL；
- [ ] BOC；
- [ ] iWencai；
- [ ] 所有 Channel/Capability/Direct API 可审计。

## 效率

- [ ] persistent pools/sessions；
- [ ] BatchPlanner；
- [ ] SingleFlight；
- [ ] deadline budget；
- [ ] normalize once；
- [ ] benchmark baseline。

---

# 23. 最终架构一句话

> **TDX、新浪、腾讯、东财等都是 Provider；source 只是“选择哪个 Provider”的兼容参数/自然语言，不是另一层对象。Provider 下面划分 Channel，Channel 暴露 Capability，Endpoint/Host 只在 Provider+Channel 内部容错。TDX 默认主 Provider，但任何 Provider 失败都不能自动跨站替代；数据必须保持最新、真实、可追溯。**
