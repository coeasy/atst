# BOC Provider 接口与 Channel 契约

> Provider ID: `boc`  
> API compatibility selector: `source="boc"`  
> Role: `auxiliary_info`

中国银行外汇牌价是独立 FX 数据 Provider，不属于股票行情替代链。

Channel：`fx`。

Direct API 目标：

```python
md.boc.fx_rates(...)
md.boc.fx_rate(pair=...)
```

返回模型使用明确的 `FxRate`/BOC-specific schema，字段区分币种、现汇/现钞、买入/卖出、中间价、发布日期和 Provider 更新时间。

Freshness 按牌价发布日期/更新时间验证，不使用实时股票 quote 的秒级 profile。

错误沿用 `docs/providers/README.md` §12 那一棵树：`fx` 是非 live channel，`currentness='live'` 在规划期就被 `ValidationError`(E1010) 拒绝；牌价抓取失败保持 `WebSourceError`(E7xxx) 家族原异常，context 始终带 `provider=boc`/`channel`/`capability`。

BOC 不参与其它 Provider 的自动替代，也不会被其它 Provider 自动替代。
