# JSL Provider 接口与 Channel 契约

> Provider ID: `jsl`  
> API compatibility selector: `source="jsl"`  
> Role: `auxiliary_info`

集思录用于可转债、ETF/基金等特色数据。它不是通用股票行情 Provider，也不参与 TDX 行情失败后的自动替代。

Channels：

```text
bond
etf
```

Direct API 目标：

```python
md.jsl.bonds(...)
md.jsl.bond_snapshot(...)
md.jsl.etf(...)
```

部分接口可能需要 cookie，且限速严格。认证、cookie、rate policy 必须由 JSL Provider runtime 集中处理。

Freshness 按业务字段更新时间定义，不复用 TDX 实时行情 profile。

错误沿用 `SourceUnavailable(E7050)`，context 写 `provider=jsl`，并统一 AuthenticationRequired / RateLimited / CapabilityUnsupported / FreshnessViolation / DataIntegrityError。

失败不得切其它 Provider。
