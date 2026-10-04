# Baidu Provider 接口与 Channel 契约

> Provider ID: `baidu`  
> API compatibility selector: `source="baidu"`  
> Role: `auxiliary_live`

百度财经是独立辅助行情 Provider。目标 Channel 为 A 股 `quote / kline / minute / ticks / catalog`，不参与其它 Provider 的自动替代。

Direct API：

```python
md.baidu.quotes(symbols)
md.baidu.kline(symbol, period="day")
md.baidu.minute(symbol)
md.baidu.ticks(symbol)
md.baidu_valuation_history(
    "600519",
    indicators=("总市值", "市盈率(TTM)", "市净率"),
    period="近五年",
)
```

估值方法返回 `dict[指标, list[date/value]]`，指标可选 `总市值`、`市盈率(TTM)`、
`市盈率(静)`、`市净率`、`市现率`，周期可选近一年/三年/五年/十年/全部。
`value` 保留百度图表的源端单位；接口不提供股息率、流通股本或流通市值历史。
接口结构属非官方服务，百度可随时调整或关闭。

CLI 通用入口也可使用该 capability：

```console
atst query baidu_valuation_history --provider baidu --args '["600519"]' --kwargs '{"indicators":["总市值","市盈率(TTM)"],"period":"近五年"}'
```

也可用统一执行器按 capability 调用：

```python
from atst import Client, QuerySpec

with Client() as client:
    result = client.execute(QuerySpec.build(
        "baidu_valuation_history",
        provider="baidu",
        options={"args": ["600519"], "kwargs": {
            "indicators": ["总市值", "市盈率(TTM)"], "period": "近五年"
        }},
    ))
    history = result.data
```

共同语义能力可进入统一 API：

```python
md.quotes(symbols, provider="baidu")
md.bars(symbol, provider="baidu")
```

live Channel 的 `currentness` 声明的是源侧口径；运行期没有新鲜度判据——`FreshnessViolation`(E4060) 只在读本地文件的 channel 上触发（判据见 `atst/runtime/freshness.py`），baidu 全走 HTTP，因此严格模式不会因新鲜度报错。

错误面只有 `docs/providers/README.md` §12 那一棵树：能力或 `currentness` 口径不匹配是规划期的 `ValidationError`(E1010)（`kline`/`catalog` 非 live channel，`currentness='live'` 当场拒绝），上游失败保持 `WebSourceError`(E7xxx) 家族原异常（429 → `WebRateLimited`，反爬 → `AntiSpiderBlocked`，接口下线或返回空 → `SourceDeprecated`），context 始终带 `provider=baidu`/`channel`/`capability`。

Conformance 验证 A 股 Market scope、字段/单位、时间戳、限流、session reuse、无跨 Provider attempt。
