# BOC Source 接口与 Channel 契约

> Source ID: `boc`  
> Role: `auxiliary_info`  
> Status: v12 target contract.

## 定位

中国银行外汇牌价是独立 FX 数据 Provider，不属于股票行情 fallback 链。

## Channel

```text
fx
```

目标 Direct API：

```python
md.boc.fx_rates(...)
md.boc.fx_rate(pair=...)
```

## 数据模型

使用明确的 `FxRate`/Provider-specific model，字段至少区分币种、现汇/现钞、买入/卖出、中间价、发布日期和来源时间。

## Freshness

按牌价发布日期/更新时间验证，不使用实时股票 quote 的秒级 freshness profile。

## Errors

```text
SourceUnavailable(source=boc)
FreshnessViolation
DataIntegrityError
CapabilityUnsupported
```

BOC 不参与其它 Source 的自动替代，也不会被其它 Source 自动替代。
