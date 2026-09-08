# Eastmoney Source 接口与 Channel 契约

> Source ID: `eastmoney`  
> Role: `auxiliary_live / auxiliary_info`  
> Status: v12 target contract.

## 1. 定位

东方财富是重要辅助数据 Provider，既有行情，也有大量 TDX 不具备的特色量化决策数据。它必须被建模成独立 Source，而不是 TDX 失败时的透明兜底。

## 2. Channel

### `quote`

```python
md.eastmoney.quotes(symbols)
```

### `kline`

```python
md.eastmoney.kline(symbol, period="day", count=320)
```

### `trends`

当日趋势/分时：

```python
md.eastmoney.trends(symbol)
```

### `rank`

```python
md.eastmoney.rank(...)
```

### `fund_flow`

```python
md.eastmoney.fund_flow(symbol)
md.eastmoney.board_fund_flow(...)
```

### `limit_pool`

```python
md.eastmoney.limit_pool(kind="up")
```

### `stock_changes`

```python
md.eastmoney.stock_changes(...)
```

### `northbound`

```python
md.eastmoney.northbound(...)
```

### `hot_rank`

```python
md.eastmoney.hot_rank(...)
```

### `corporate`

公司基本面和事件类数据：

```python
md.eastmoney.profile(symbol)
md.eastmoney.notices(symbol)
md.eastmoney.research(symbol)
md.eastmoney.shareholders(symbol)
md.eastmoney.block_trades(symbol)
md.eastmoney.unlocks(symbol)
md.eastmoney.performance(symbol)
```

### `longhu`

```python
md.eastmoney.longhu(...)
```

### `margin`

```python
md.eastmoney.margin(symbol)
```

### `index_constituents`

```python
md.eastmoney.index_constituents(index)
```

### `fund`

```python
md.eastmoney.fund.nav(...)
md.eastmoney.fund.estimate(...)
md.eastmoney.fund.list(...)
```

## 3. Unified API

仅 quote/kline 等共同语义能力进入：

```python
md.quotes(symbols, source="eastmoney")
md.bars(symbol, source="eastmoney")
```

资金流、涨停池、异动、人气榜、两融、龙虎榜、公司资料等保持 Direct API。

## 4. Provider-specific Model

必须提供明确类型，例如：

```text
EastmoneyFundFlow
EastmoneyLimitPoolItem
EastmoneyStockChange
EastmoneyHotRank
EastmoneyCorporateEvent
EastmoneyMarginRecord
```

避免将大量特色字段压进 `dict/extra` 而失去 schema。

## 5. 单位与字段归一

东财部分行情字段存在 ×100/手等原始口径。Canonical Adapter 必须归一到：

```text
price=元
volume=股
amount=元
```

Provider-specific raw model 可保留原始字段。

## 6. Freshness

不同 Channel 要单独定义：

- quote/trends/stock_changes/limit_pool：实时/盘中 freshness；
- fund_flow/hot_rank：provider update freshness；
- corporate/longhu/margin/index constituents：业务日期 freshness；
- fund NAV：净值日期 freshness。

不能用一个全局 TTL 解释全部数据。

## 7. Endpoint / Rate Policy

同 Channel 内可在明确等价的东财 endpoint 中容错；高频接口必须有独立 rate limiter/circuit。失败不得转 Sina/Tencent/TDX。

## 8. Errors

```text
SourceUnavailable(source=eastmoney)
ChannelUnavailable
CapabilityUnsupported
FreshnessViolation
DataIntegrityError
RateLimited
ProviderRejected
```

## 9. Conformance

每个 Channel 独立验证：schema、单位、日期、分页、限流、freshness、partial page、无跨源执行、session reuse。
