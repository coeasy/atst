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

live Channel 的 `currentness` 声明的是源侧口径；运行期没有新鲜度判据——`FreshnessViolation`(E4060) 只在读本地文件的 channel 上触发（判据见 `tstdx/runtime/freshness.py`），baidu 全走 HTTP，因此严格模式不会因新鲜度报错。

错误面只有 `docs/providers/README.md` §12 那一棵树：能力或 `currentness` 口径不匹配是规划期的 `ValidationError`(E1010)（`kline`/`catalog` 非 live channel，`currentness='live'` 当场拒绝），上游失败保持 `WebSourceError`(E7xxx) 家族原异常（429 → `WebRateLimited`，反爬 → `AntiSpiderBlocked`，接口下线或返回空 → `SourceDeprecated`），context 始终带 `provider=baidu`/`channel`/`capability`。

Conformance 验证 A 股 Market scope、字段/单位、时间戳、限流、session reuse、无跨 Provider attempt。
