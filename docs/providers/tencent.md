# Tencent Provider 接口与 Channel 契约

> Provider ID: `tencent`  
> API compatibility selector: `source="tencent"`  
> Role: `auxiliary_live`

## 1. 定位

腾讯是独立辅助 Provider，不是 TDX fallback。用户选择腾讯时，仅允许腾讯内部 endpoint/session 容错；腾讯失败直接返回 Provider 不可用语义，不得切新浪、东财或 TDX。

## 2. Channel

### quote

Markets：`cn_a / hk / us`（以 adapter conformance 为准）。

```python
md.tencent.quotes(symbols, market="cn_a")
md.tencent.quotes(symbols, market="hk")
md.tencent.quotes(symbols, market="us")
```

### kline

```python
md.tencent.kline(symbol, period="day", count=320)
```

### minute_kline

```python
md.tencent.minute_kline(symbol, period="1m|5m|15m|30m|60m")
```

不支持的 Market 当场失败（symbol 归一化抛 `SymbolError` E4040，或规划期 `ValidationError` E1010），不得改去其它 Provider。

### minute

```python
md.tencent.minute(symbol)
```

### ticks

```python
md.tencent.ticks(symbol)
```

### global

```python
md.tencent.global_quotes(...)
```

### market_stat

```python
md.tencent.market_stat(...)
```

### board_rank

```python
md.tencent.board_rank(...)
```

## 3. Unified API

```python
md.quotes(symbols, provider="tencent")
md.bars(symbol, provider="tencent")
```

兼容：`source="tencent"`。

## 4. 单位契约

Adapter 必须在 canonical model 前完成：

```text
price  -> 元
volume -> 股
amount -> 元
```

Provider-specific raw model 可保留腾讯原始手/万元等字段，但必须显式标 raw。

## 5. Freshness

每个 live Channel 必须独立定义 freshness evidence。无法证明 fresh 时返回 FreshnessViolation/FreshnessUnverified，不得返回旧缓存顶替。

## 6. Endpoint 容错

只允许腾讯同一 Channel 内的等价 endpoint/CDN failover。

## 7. Errors

错误面只有 `docs/providers/README.md` §12 那一棵树：能力/period/`currentness` 口径与腾讯 channel 不匹配时是规划期的 `ValidationError`(E1010)，上游失败保持 `WebSourceError`(E7xxx) 家族原异常（429 → `WebRateLimited`，反爬 → `AntiSpiderBlocked`，接口下线或返回空 → `SourceDeprecated`，读超时 → `ReadTimeout` E2030），context 带：

```json
{"provider":"tencent","channel":"quote","capability":"quotes"}
```

## 8. Provider-specific 数据

`global / market_stat / board_rank` 等保留 Tencent-specific schema，不强制塞入 Quote.extra。

A 股 `quote` 的 `Quote.extra` 还包含当日源快照指标：`pe`、`pb`、`turnover_rate`、
`float_market_cap`、`total_market_cap`、`volume_ratio` 与涨跌停价。市值由源值“亿元”
换成“元”；`pe` 的具体静态/滚动口径受上游字段定义影响，不能当作历史 TTM 序列。
这些数据是当前快照，不含历史市值、流通股数或股息率；取历史估值走
`valuation_history`（Eastmoney）或 `baidu_valuation_history`（百度，源端单位）。

## 9. Conformance

验证 Provider identity、Market scope、单位、freshness、rate/batch limit、session reuse、no cross-provider attempts。
