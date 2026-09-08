# Baidu Source 接口与 Channel 契约

> Source ID: `baidu`  
> Role: `auxiliary_live`  
> Status: v12 target contract.

## 定位

百度财经是独立辅助行情 Provider。当前目标 Channel 为 A 股 quote/kline/minute/ticks 等能力；不作为 TDX/Tencent/Sina/Eastmoney 的自动 fallback。

## Channel

```python
md.baidu.quotes(symbols)
md.baidu.kline(symbol, period="day")
md.baidu.minute(symbol)
md.baidu.ticks(symbol)
```

## Unified API

若 capability schema 完全对齐，可使用：

```python
md.quotes(symbols, source="baidu")
md.bars(symbol, source="baidu")
```

## Freshness

live Channel 必须独立声明 source timestamp/update time 等 freshness evidence。无法证明 fresh 时严格模式报错。

## Errors

```text
SourceUnavailable(source=baidu)
ChannelUnavailable
CapabilityUnsupported
FreshnessViolation
DataIntegrityError
RateLimited/ProviderRejected
```

## Conformance

验证 A 股 market scope、字段/单位、时间戳、限流、session reuse、无跨源执行。
