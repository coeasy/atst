# JSL Source 接口与 Channel 契约

> Source ID: `jsl`  
> Role: `auxiliary_info`  
> Status: v12 target contract.

## 定位

集思录用于可转债、ETF/基金等特色数据。它不是通用股票行情源，也不参与 TDX 行情失败后的自动替代。

## Channel

```text
bond
etf
```

目标 Direct API：

```python
md.jsl.bonds(...)
md.jsl.bond_snapshot(...)
md.jsl.etf(...)
```

具体接口以当前 adapter 能力和后续 registry 审计为准。

## Auth / Rate

部分接口可能需要 cookie，且限速严格。认证、cookie、rate policy 必须由 JSL Source runtime 集中处理。

## Freshness

按业务字段更新时间定义，不使用 TDX 实时行情的 freshness profile。

## Errors

```text
SourceUnavailable(source=jsl)
AuthenticationRequired
RateLimited
CapabilityUnsupported
FreshnessViolation
DataIntegrityError
```

失败不得切其它 Source。
