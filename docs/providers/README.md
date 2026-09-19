# tstdx Provider 接口目录

> Status: v12 target contract  
> Terminology: `docs/adr/ADR-013-provider-source-terminology.md`  
> Registry target: `ProviderRegistry + ChannelRegistry + CapabilityRegistry`

## 1. 核心术语

`tstdx` 只把“谁提供数据”建模成 **Provider**。

- **Provider**：`tdx / tencent / sina / eastmoney / baidu / jsl / boc / iwencai`。
- **source**：API/兼容语义中的 Provider selector，值就是 Provider ID，不是第二层对象。
- **Channel**：Provider 内的数据通道/接口族。
- **Capability**：用户要获取的业务数据能力。
- **Market**：`cn_a / hk / us / future / fund / fx ...`。
- **Endpoint/Host**：Provider + Channel 内部物理连接目标。

推荐调用：

```python
md.quotes(["sh600519"], provider="tdx")
```

兼容调用：

```python
md.quotes(["sh600519"], source="tdx")
```

两者最终都只解析成：

```text
provider_id = tdx
```

## 2. Provider 总览

| Provider | 角色 | 主要 Channel | 典型特有数据 | 自动跨 Provider 替代 |
|---|---|---|---|---|
| `tdx` | primary live | quotation / extended / goods / f10 / mac / vipdoc | TDX 二进制行情、扩展市场、商品、F10、本地历史 | 禁止 |
| `tencent` | auxiliary live | quote / kline / minute_kline / minute / ticks / global / market_stat / board_rank | 港美行情、逐笔、全球行情、大盘统计 | 禁止 |
| `sina` | auxiliary live/info | quote / history_kline / suggest / boards / fund_flow / news | 联想、板块、新闻、资金流 | 禁止 |
| `eastmoney` | auxiliary live/info | quote / kline / trends / rank / fund_flow / limit_pool / stock_changes / northbound / corporate / longhu / hot_rank / margin / index_constituents / fund | 资金流、涨跌停池、异动、人气、两融、龙虎榜、公司资料 | 禁止 |
| `baidu` | auxiliary live | quote / kline / minute / ticks | 百度财经行情 | 禁止 |
| `jsl` | auxiliary info | bond | 已验证的可转债数据；ETF 尚未注册为生产能力 | 禁止 |
| `boc` | auxiliary info | fx | 外汇牌价 | 禁止 |
| `iwencai` | auxiliary info | screening | 自然语言选股 | 禁止 |

`vipdoc` 不再作为独立 Provider，而是：

```text
provider=tdx
channel=vipdoc
mode=local_historical
```

Golden Replay / Synthetic 只属于测试运行时。

JSL 的 ETF 能力只有在存在独立真实 endpoint、adapter、schema 与真实样本门禁后才可重新注册；不得复用可转债 endpoint 冒充 ETF。

## 3. Provider → Channel → Capability

示例：

```text
Provider: tdx
  Channel: quotation
    Capabilities: quotes / bars / minute / trades / finance / capital_changes

Provider: tencent
  Channel: quote
    Capability: quotes
  Channel: kline
    Capability: bars

Provider: eastmoney
  Channel: fund_flow
    Capabilities: fund_flow / board_fund_flow
```

Provider 决定 provenance；Channel 决定具体接入族；Capability 决定业务语义。

## 4. Public API

### Unified API

只覆盖跨 Provider 真正同语义的能力：

```python
md.quotes(symbols, provider="tdx")
md.quotes(symbols, provider="tencent")
md.bars(symbol, provider="tdx")
md.bars(symbol, provider="eastmoney")
```

统一 API 不能因为某 Provider 失败而换另一个 Provider。

### Direct Provider API

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

Provider-specific 数据优先保留 Direct API 和独立 schema。

## 5. TDX 的特殊规则

TDX 内部允许：

```text
host failover
endpoint failover
registry-driven channel resolution
```

TDX 内部禁止被称为“跨源 fallback”。

例如：

```text
tdx/quotation host A -> host B -> host C   # 合法
```

但：

```text
tdx/bars -> sina/bars                      # 禁止
```

## 6. Market 不属于 Provider

以下旧式概念必须拆开：

```text
hk -> market=hk
us -> market=us
```

例如：

```text
provider=tencent, channel=quote, market=hk
provider=sina,    channel=quote, market=hk
provider=tencent, channel=quote, market=us
```

## 7. kline/minute/ticks 不属于 Provider

以下都是 Channel/Capability，不是 Provider：

```text
kline
minute
minute_kline
ticks
fund_flow
news
```

因此旧 Web registry 最终需要从“混合 source registry”迁移为 Provider + Channel + Capability 三张事实表。

## 8. Provider 文档模板

每份 `docs/providers/<provider>.md` 必须包含：

1. Provider ID（人类可读的名称与定位写在正文里——代码面的 `ProviderSpec` 只持有执行面
   真会读到的字段，`display_name` / `role` 曾在那里写着却无人读取，已随 F-52 删除）；
2. 支持 Market；
3. Channel 列表；
4. Capability 列表；
5. Unified API；
6. Direct API；
7. Provider-specific model；
8. canonical schema 映射；
9. 单位归一；
10. freshness evidence/profile；
11. auth/referer/cookie；
12. rate/batch limit；
13. endpoint/host failover 规则；
14. error contract；
15. production/experimental/degraded 状态；
16. tests/fixtures；
17. benchmark cases；
18. ProviderRegistry/ChannelRegistry/CapabilityRegistry entry。

## 9. Registry 目标

### ProviderRegistry

只保存 Provider 级事实：

```text
identity
role
markets
auth/rate policy
channels
production status
```

### ChannelRegistry

保存 Provider 内通道事实：

```text
provider
channel
protocol/endpoint group
capabilities
batch/rate/freshness constraints
```

### CapabilityRegistry

保存业务能力事实：

```text
capability
supported providers
default provider
canonical result schema
freshness profile
```

## 10. Health 与 Registry 分离

```text
ProviderRegistry       = 静态事实
ProviderHealthRegistry = 动态健康状态
```

TDX 某个 host 不健康只更新 Endpoint/Host health；不能把 `provider=tdx` 静态能力删掉。

## 11. Freshness 与历史窗口

Freshness 必须和查询语义绑定，不能只检查“字段非空”。

### 当前行情 / current series

统一实时查询遵守：

```text
direct selected Provider fetch
+ real provenance
+ parseable Provider tail timestamp（时间序列能力）
+ gross-stale sanity guard
+ integrity/schema check
```

`gross-stale sanity guard` 只是防止明显陈旧数据，不宣称自己是交易所日历。当前实现不会用估算交易日历把周末、春节、国庆等休市期的最新真实数据误判为 stale。

### historical closed window

显式历史窗口必须标记为：

```text
mode=historical_closed
verified=true
currentness_verified=false
```

历史数据可以是真实、可审计的数据，但不能满足要求“当前/最新”的 live contract，也不能把 `verified` 等同于 `verified_fresh`。

### no result cache

v17 运行期不做结果缓存：每一次公开查询都编译为一个 `QueryPlan` 并直接请求它绑定的 Provider，`provenance.cache_tier` 恒为 `null`。因此 Provider 侧的契约是：

- 不得自带"命中即跳过上游"的语义——那是运行期缓存的职责，而运行期缓存已被物理删除；
- 返回的 provenance 必须与当前 `QueryPlan` 的 Provider/Channel/Capability 完全一致，否则结果被拒（`ResultMeta.from_plan` 抛 `ValidationError`）；
- replay/synthetic 或跨 Provider 的 payload 不能冒充当前 Provider 的真实数据；
- freshness mode 由 Provider 如实声明：历史窗口返回后仍是 `historical_closed`，不得因为"数据还新"被重标为 `current_series`；
- 调用方要控制的是新鲜度**口径**（`currentness`）与执行**预算**（`deadline_ms`），不是过期容忍度
  或部分放行——`options` 袋里只有 `tstdx.query.EXECUTED_OPTIONS` 的键会被执行面读取，
  其余键（含 `tstdx.query.REJECTED_OPTIONS` 的策略键）在直连执行面上恒被当场拒绝。

## 12. Error

现有 `SourceUnavailable` 保持 E7050，不创建第二棵错误树。

规范语义：

```text
SourceUnavailable == selected Provider unavailable
```

context 应统一使用：

```json
{
  "provider": "tdx",
  "channel": "quotation",
  "capability": "bars"
}
```

Provider-specific Direct API 已有 `TdxError` 必须原样保留并补齐 Provider/Channel context；adapter 意外抛出的原生异常统一进入 `InternalError(E9000)`，不得伪装成跨 Provider fallback 条件。

## 13. CI 文档门禁

```text
ProviderRegistry -> docs/providers/<provider>.md exists
provider doc -> ProviderRegistry entry exists
ChannelRegistry -> documented channel exists
CapabilityRegistry -> provider/channel mapping exists
Direct API -> documented + callable
provider/source alias -> same ProviderId
no hk/us/kline/minute/ticks as Provider IDs
vipdoc -> tdx/vipdoc only
no result cache -> provenance.cache_tier is always null
result provenance -> same Provider/Channel/Capability as the QueryPlan
historical closed -> currentness_verified=false
```

## 14. 最终术语

> **Provider 是正式实体；source 是用户语言/API 兼容名，指向同一个 Provider ID。Provider 下有 Channel，Channel 暴露 Capability，Endpoint/Host 只在 Provider + Channel 内部。**
