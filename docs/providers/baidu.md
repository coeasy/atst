# Baidu Provider 接口与 Channel 契约

> Provider ID: `baidu`  
> API compatibility selector: `source="baidu"`  
> Role: `auxiliary_live`

百度财经是独立辅助行情 Provider。当前目标 Channel 为 A 股 `quote / kline / minute / ticks`，不参与其它 Provider 的自动替代。

Direct API：

```python
md.baidu.quotes(symbols)
md.baidu.kline(symbol, period="day")
md.baidu.minute(symbol)
md.baidu.ticks(symbol)
```

共同语义能力可进入统一 API：

```python
md.quotes(symbols, provider="baidu")
md.bars(symbol, provider="baidu")
```

live Channel 必须声明 freshness evidence；无法证明 fresh 时严格模式报错。

错误使用现有 `SourceUnavailable(E7050)`，context 写 `provider=baidu`，并统一 CapabilityUnsupported / FreshnessViolation / DataIntegrityError / RateLimited / ProviderRejected。

Conformance 验证 A 股 Market scope、字段/单位、时间戳、限流、session reuse、无跨 Provider attempt。
