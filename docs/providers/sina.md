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

## 4. Referer / Rate

新浪部分接口要求 Referer。该事实属于 Provider/Channel metadata，由 Provider runtime 统一装配。

## 5. Freshness

quote/history/news/fund_flow 使用各自 freshness profile；新闻和板块更新频率不能机械套用秒级行情 freshness。

## 6. Errors

`SourceUnavailable(E7050)` context 使用 `provider=sina`；其它包括 CapabilityUnsupported、FreshnessViolation、DataIntegrityError、RateLimited、ProviderRejected。

## 7. Provider-specific Model

新闻、资金流、板块结构使用 Sina-specific model，不塞入 Quote.extra。

## 8. Conformance

验证 Referer、单位、Market、时间/字符集、限流、session reuse、无跨 Provider attempt。
