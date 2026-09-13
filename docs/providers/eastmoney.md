# Eastmoney Provider 接口与 Channel 契约

> Provider ID: `eastmoney`  
> API compatibility selector: `source="eastmoney"`  
> Role: `auxiliary_live / auxiliary_info`

## 1. 定位

东方财富是重要辅助 Provider，既有行情，也有大量 TDX 不具备的量化决策数据。它不是 TDX 失败时的透明替代。

## 2. Channel

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

典型 Direct API：

```python
md.eastmoney.quotes(symbols)
md.eastmoney.kline(symbol, period="day", count=320)
md.eastmoney.trends(symbol)
md.eastmoney.rank(...)
md.eastmoney.fund_flow(symbol)
md.eastmoney.board_fund_flow(...)
md.eastmoney.limit_pool(kind="up")
md.eastmoney.stock_changes(...)
md.eastmoney.northbound(...)
md.eastmoney.hot_rank(...)
md.eastmoney.profile(symbol)
md.eastmoney.notices(symbol)
md.eastmoney.research(symbol)
md.eastmoney.shareholders(symbol)
md.eastmoney.block_trades(symbol)
md.eastmoney.unlocks(symbol)
md.eastmoney.performance(symbol)
md.eastmoney.longhu(...)
md.eastmoney.margin(symbol)
md.eastmoney.index_constituents(index)
md.eastmoney.fund.nav(...)
md.eastmoney.fund.estimate(...)
md.eastmoney.fund.list(...)
```

## 3. Unified API

只给共同语义能力统一入口：

```python
md.quotes(symbols, provider="eastmoney")
md.bars(symbol, provider="eastmoney")
```

资金流、涨停池、异动、人气、两融、龙虎榜、公司资料等保持 Provider Direct API。

## 4. Provider-specific Model

至少规划：

```text
EastmoneyFundFlow
EastmoneyLimitPoolItem
EastmoneyStockChange
EastmoneyHotRank
EastmoneyCorporateEvent
EastmoneyMarginRecord
EastmoneyLonghuRecord
```

特色字段不应被压进无 schema 的 dict/extra。

## 5. 单位归一

行情 canonical contract：

```text
price=元
volume=股
amount=元
```

Provider-specific raw model 可以保留东财原始字段/缩放值。

## 6. Freshness

必须按 Channel 分开：

- quote/trends/stock_changes/limit_pool：盘中实时 freshness；
- fund_flow/hot_rank：Provider 更新 freshness；
- corporate/longhu/margin/index constituents：业务日期 freshness；
- fund NAV：净值日期 freshness。

## 7. Endpoint / Rate

只允许东财同 Channel 内明确等价 endpoint 容错；高频 Channel 使用独立 rate limiter/circuit。不得切 Sina/Tencent/TDX。

## 8. Errors

`SourceUnavailable(E7050)` context 使用 `provider=eastmoney`；其它包括 CapabilityUnsupported、FreshnessViolation、DataIntegrityError、RateLimited、ProviderRejected。

## 9. Conformance

每个 Channel 独立验证 schema、单位、日期、分页、限流、freshness、partial page、session reuse、无跨 Provider attempt。
