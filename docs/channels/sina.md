# Sina Source 接口与 Channel 契约

> Source ID: `sina`  
> Role: `auxiliary_live / auxiliary_info`  
> Status: v12 target contract.

## 1. 定位

新浪是独立行情/资讯 Provider。它可以提供与 TDX 部分重叠的行情，也提供 TDX 不具备或不适合承载的联想、板块、新闻、资金流等特色数据。它不是 TDX 的自动备用源。

## 2. Channel

### `quote`

```python
md.sina.quotes(symbols, market="cn_a")
md.sina.hk_quotes(symbols)
```

### `history_kline`

```python
md.sina.history_kline(symbol, period="day", count=320)
```

TDX K 线失败不能自动调用该接口。

### `suggest`

```python
md.sina.suggest("茅台")
```

### `industry_board`

```python
md.sina.industry_board(...)
```

### `board_list`

```python
md.sina.board_list(...)
```

### `board_member`

```python
md.sina.board_members(board)
```

### `fund_flow`

```python
md.sina.fund_flow(symbol)
```

### `news`

```python
md.sina.news(symbol)
```

## 3. Unified API

仅共同语义能力接入统一入口：

```python
md.quotes(symbols, source="sina")
md.bars(symbol, source="sina")
```

`suggest/boards/fund_flow/news` 默认使用 Direct API。

## 4. Referer / Rate Policy

新浪部分接口要求 Referer。该事实属于 Source/Channel metadata，必须由 session/runtime 统一处理，不能散落在 endpoint 函数。

## 5. Freshness

行情 Channel 必须通过 freshness gate；新闻/板块等信息 Channel 按各自更新时间语义声明 freshness profile，不能用实时行情的秒级标准机械套用。

## 6. Errors

```text
SourceUnavailable(source=sina)
ChannelUnavailable
CapabilityUnsupported
FreshnessViolation
DataIntegrityError
RateLimited/Forbidden
```

失败不得转腾讯、东财、TDX。

## 7. Provider-specific 数据

新闻、资金流、板块结构使用明确的 Sina-specific model，不通过 `Quote.extra` 兜底承载。

## 8. Conformance

验证：Referer、单位、市场、时间、字符集/解析、限流、无跨源执行、session reuse。
