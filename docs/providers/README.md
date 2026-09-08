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
| `jsl` | auxiliary info | bond / etf | 可转债、ETF 等特色数据 | 禁止 |
| `boc` | auxiliary info | fx | 外汇牌价 | 禁止 |
| `iwencai` | auxiliary info | screening | 自然语言选股 | 禁止 |

`vipdoc` 不再作为独立 Provider，而是：

```text
provider=tdx
channel=vipdoc
mode=local_historical
```

Golden Replay / Synthetic 只属于测试运行时。

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

1. Provider ID / display name / role；
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
ProviderRegistry      = 静态事实
ProviderHealthRegistry = 动态健康状态
```

TDX 某个 host 不健康只更新 Endpoint/Host health；不能把 `provider=tdx` 静态能力删掉。

## 11. Error

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

## 12. CI 文档门禁

```text
ProviderRegistry -> docs/providers/<provider>.md exists
provider doc -> ProviderRegistry entry exists
ChannelRegistry -> documented channel exists
CapabilityRegistry -> provider/channel mapping exists
Direct API -> documented + callable
provider/source alias -> same ProviderId
no hk/us/kline/minute/ticks as Provider IDs
vipdoc -> tdx/vipdoc only
```

## 13. 最终术语

> **Provider 是正式实体；source 是用户语言/API 兼容名，指向同一个 Provider ID。Provider 下有 Channel，Channel 暴露 Capability，Endpoint/Host 只在 Provider + Channel 内部。**
