# Tencent Source 接口与 Channel 契约

> Source ID: `tencent`  
> Role: `auxiliary_live`  
> Status: v12 target contract; actual endpoint support must be verified by adapter tests.

## 1. 定位

腾讯是独立辅助行情源，不是 TDX fallback。用户显式选择腾讯时，仅使用腾讯 Channel；腾讯失败直接报错。

## 2. Channel

### `quote`

市场：`cn_a / hk / us`（以实际 adapter 支持为准）。

```python
md.tencent.quotes(symbols, market="cn_a")
md.tencent.quotes(symbols, market="hk")
md.tencent.quotes(symbols, market="us")
```

### `kline`

```python
md.tencent.kline(symbol, period="day", count=320)
```

### `minute_kline`

当前已知主要面向 A 股分钟 K 线：

```python
md.tencent.minute_kline(symbol, period="5m")
```

市场不支持时返回 `CapabilityUnsupported`，不得改去东财。

### `minute`

```python
md.tencent.minute(symbol)
```

### `ticks`

```python
md.tencent.ticks(symbol)
```

### `global`

外盘/全球行情：

```python
md.tencent.global_quotes(...)
```

### `market_stat`

```python
md.tencent.market_stat(...)
```

### `board_rank`

```python
md.tencent.board_rank(...)
```

## 3. Unified API

跨源公共语义能力可使用：

```python
md.quotes(symbols, source="tencent")
md.bars(symbol, source="tencent")
```

统一接口不得隐藏腾讯原始单位差异；normalize 后必须符合 canonical unit contract。

## 4. 单位契约

当前腾讯行情存在“手/万元”等原始单位差异。Adapter 必须在 canonical model 前完成归一：

```text
price  -> 元
volume -> 股
amount -> 元
```

Provider-specific raw model 可以保留原始字段，但必须显式标注 raw。

## 5. Freshness

每个 live Channel 必须定义 freshness evidence。严格模式无法确认实时性时返回 `FreshnessViolation`/`FreshnessUnverified`，不得返回旧缓存替代。

## 6. Endpoint 容错

若腾讯同一 Channel 有多个等价 endpoint/CDN，可在 `source=tencent` 内部切换；不得切新浪、东财或 TDX。

## 7. Errors

```text
SourceUnavailable(source=tencent)
ChannelUnavailable(source=tencent, channel=...)
CapabilityUnsupported(source=tencent, ...)
FreshnessViolation
DataIntegrityError
RateLimited
```

## 8. 特有数据保留

`global / market_stat / board_rank` 等不需要强行映射成通用 Quote API，可保留 Tencent-specific result model。

## 9. Conformance

必须验证：

```text
source identity=tencent
market scope
unit normalization
freshness
rate limit
batch limit
no cross-source attempts
session reuse
```
