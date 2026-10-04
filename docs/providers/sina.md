# Sina Provider 接口与 Channel 契约

> Provider ID: `sina`  
> API compatibility selector: `source="sina"`  
> Role: `auxiliary_live / auxiliary_info`

## 1. 定位

新浪是独立行情/资讯 Provider。它与 TDX 部分行情能力重叠，同时提供联想、板块、新闻、资金流等特色数据。它不是 TDX 自动备用 Provider。

## 2. Channel

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

Direct API：

```python
md.sina.quotes(symbols, market="cn_a")
md.sina.hk_quotes(symbols)
md.sina.history_kline(symbol, period="day", count=320)
md.sina.suggest("茅台")
md.sina.industry_board(...)
md.sina.board_list(...)
md.sina.board_members(board)
md.sina.fund_flow(symbol)
md.sina.news(symbol)
```

## 3. Unified API

共同语义能力：

```python
md.quotes(symbols, provider="sina")
md.bars(symbol, provider="sina")
```

`suggest/boards/fund_flow/news` 优先 Direct API。

新浪 `quotes()` 的 `hq_str` 快照不包含 PE/PB/市值字段；`all_market(node="hs_a")`
使用另一个行情中心端点，返回 `per`、`pb`、`mktcap`、`nmc`、`turnoverratio`，其中
`mktcap`/`nmc` 已换算为元。它适合一次获取全市场当日摘要，不是个股历史估值接口。
需要历史 PE/PB/总市值/流通市值/股数时使用 `valuation_history`（Eastmoney）或
百度 `baidu_valuation_history`（总市值与 PE/PB 等，但无流通股数序列）。

## 4. Referer / Rate

新浪部分接口要求 Referer。该事实属于 Provider/Channel metadata，由 Provider runtime 统一装配。

## 5. Freshness

quote/history/news/fund_flow 使用各自 freshness profile；新闻和板块更新频率不能机械套用秒级行情 freshness。

## 6. Errors

错误面只有 `docs/providers/README.md` §12 那一棵树：能力/`currentness` 口径与新浪 channel 不匹配时是规划期的 `ValidationError`(E1010)（`history_kline`/`news`/`fund_flow` 等非 live channel 上 `currentness='live'` 当场拒绝），上游失败保持 `WebSourceError`(E7xxx) 家族原异常（429 → `WebRateLimited`，反爬 → `AntiSpiderBlocked`，接口下线或返回空 → `SourceDeprecated`，读超时 → `ReadTimeout` E2030），context 带 `provider=sina`/`channel`/`capability`。

## 7. Provider-specific Model

新闻、资金流、板块结构使用 Sina-specific model，不塞入 Quote.extra。

## 8. Conformance

验证 Referer、单位、Market、时间/字符集、限流、session reuse、无跨 Provider attempt。
