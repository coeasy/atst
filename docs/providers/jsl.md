# JSL Provider 接口与 Channel 契约

> Provider ID: `jsl`  
> API compatibility selector: `source="jsl"`  
> Role: `auxiliary_info`

集思录当前生产 Registry **只注册已经有真实 adapter 的可转债 Channel**。它不是通用股票行情 Provider，也不参与 TDX 行情失败后的自动替代。

## 当前生产 Channel

```text
bond
```

当前 `bond` adapter 使用集思录真实 `cbnew` 可转债接口，并按可转债字段解析。

Direct API：

```python
with market_data() as md:
    bonds = md.jsl.bonds()
```

## ETF 状态

`etf` **当前不属于生产 Provider Registry**。

历史代码曾把 `etf` 与 `bond` 都映射到同一个 `JslSource`，但该 adapter 实际请求的是可转债 endpoint，并解析 `bond_id` / `bond_nm` 等可转债字段。继续把它声明为 ETF 会产生错误业务数据，因此 v12 按 fail-closed 原则移除了生产 ETF capability。

在真实 ETF endpoint、请求参数、解析 schema 和回归样本被验证并实现独立 adapter 之前：

```text
jsl/etf -> unavailable / ValidationError
```

绝不能：

```text
jsl/etf -> 调用可转债 endpoint -> 把债券数据冒充 ETF
```

后续恢复 ETF capability 的最低条件：

1. 确认真实 JSL ETF endpoint 与认证/cookie 要求；
2. 建立独立 ETF schema；
3. 实现独立 adapter；
4. 增加真实样本/契约测试；
5. 再把 `etf` 加回 Provider Registry 与 Direct API。

## 认证、限速与 freshness

部分 JSL 接口可能需要 cookie，且限速严格。认证、cookie、rate policy 必须由 JSL Provider runtime 集中处理。

Freshness 按业务字段更新时间定义，不复用 TDX 实时行情 profile。

错误沿用稳定错误树，并始终保留：

```text
provider=jsl
fallback=false
provider_switch_allowed=false
```

失败不得切换其它 Provider。
