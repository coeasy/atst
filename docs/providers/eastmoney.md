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
md.valuation_history("600519", count=120)
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

个股历史估值由 capability `valuation_history` 提供（日频、按日期倒序，单次最多 500 行）：
返回 Eastmoney Datacenter 原始字段，不对列名或缺列做静默填充。常见字段包括
`TRADE_DATE`、`TOTAL_MARKET_CAP`、`NOTLIMITED_MARKETCAP_A`、`TOTAL_SHARES`、
`FREE_SHARES_A`、`PE_TTM`、`PE_LAR`、`PB_MRQ`、`PS_TTM`、`PCF_OCF_TTM`；
以实际返回行为准。该接口有历史市值、流通市值/股数和估值比率，不能保证股息率列；
分红事件请另外读取 `dividend_history`，若计算历史股息率，应明确每股分红、除权日、
价格和 as-of 规则。

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

错误面只有 `docs/providers/README.md` §12 那一棵树：能力/period/`currentness` 口径与东财 channel 不匹配时是规划期的 `ValidationError`(E1010)（`kline`/`news`/`fund` 一类非 live channel 上 `currentness='live'` 当场拒绝），上游失败保持 `WebSourceError`(E7xxx) 家族原异常（429 → `WebRateLimited`，反爬 → `AntiSpiderBlocked`，接口下线或返回空 → `SourceDeprecated`，读超时 → `ReadTimeout` E2030），context 带 `provider=eastmoney`/`channel`/`capability`；容错只在同 Channel 内换 endpoint，跨 Provider 不会以错误码的形式偷偷发生。

## 9. Conformance

每个 Channel 独立验证 schema、单位、日期、分页、限流、freshness、partial page、session reuse、无跨 Provider attempt。
