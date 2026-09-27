# atst Provider Isolation Architecture V1

## 目标

将所有数据源 Provider 设计为独立、隔离、可审计的数据接入单元。

核心原则：

> Provider 之间不相互依赖、不相互调用、不隐式兜底。

每个 Provider 都代表一个独立数据事实来源。

---

# 一、总体架构

```text
                 Client
                   |
                   v
              QuerySpec
                   |
                   v
              QueryPlanner
                   |
                   v
          Provider Resolver
                   |
      +------------+------------+
      |            |            |
      v            v            v

     TDX      EastMoney     Tencent
  Provider    Provider     Provider

      |            |            |
      v            v            v

   TCP协议      HTTP API    HTTP API
```

Provider 是边界，不感知其它 Provider。

---

# 二、禁止行为

## 禁止 Provider 内部调用其它 Provider

错误：

```text
TDX Provider
    |
    +--> EastMoney
```

原因：

- 数据来源不可追踪
- provenance 失真
- 数据语义可能变化
- 回测和研究结果不可复现

---

## 禁止 Runtime 隐式切换 Provider

错误：

```text
request provider=tdx

TDX失败

自动切换

EastMoney
```

正确：

```text
request provider=tdx

只执行 TDX
```

失败必须返回真实错误。

---

# 三、Provider Contract

每个 Provider 必须声明：

```text
Provider
 |
 +-- identity
 |
 +-- capabilities
 |
 +-- channels
 |
 +-- schema
 |
 +-- health
 |
 +-- provenance
```

示例：

```python
Provider(
    name="tdx",
    capabilities=[
        "bars",
        "quotes",
        "trades",
    ],
)
```

---

# 四、Runtime 规则

Runtime 默认：

```text
一个 Query

=

一个 Provider

=

一个 Channel
```

执行链：

```text
QuerySpec
   |
   v
QueryPlanner
   |
   v
QueryPlan(provider=tdx)
   |
   v
DirectProviderExecutor
   |
   v
TDX Provider
```

---

# 五、多 Provider 场景

多源比较、数据校验、容灾等需求由独立 Orchestrator 完成。

```text
Application
      |
      v
ProviderOrchestrator
      |
 +----+----+
 |         |
TDX    EastMoney
```

例如：

```python
FallbackPolicy.build(
    "tdx",
    "eastmoney",
)
```

该策略属于应用层，不属于 Provider。

---

# 六、缓存隔离

缓存 Key 必须包含 Provider。

错误：

```text
bars(sh600519)
```

正确：

```text
bars
+
symbol
+
period
+
provider
+
channel
+
params
```

TDX 和 EastMoney 数据不可互相污染。

---

# 七、Provenance 要求

所有结果必须携带：

```text
provider
channel
capability
fingerprint
observed_at
cache_tier
```

缓存命中不得改变数据来源。

---

# 八、DataSourceRouter 调整

旧概念：

```text
DataSourceRouter
```

容易误解为自动降级。

调整为：

```text
ProviderResolver
```

职责：

- 根据请求找到指定 Provider
- 校验 capability
- 返回执行目标

不负责：

- fallback
- 数据融合
- 自动切源

---

# 九、README 与 API 调整

需要同步修改：

1. 删除默认五级自动降级描述
2. 删除 Provider 自动互相兜底描述
3. 强调 Provider Isolation
4. 将 fallback 定义为可选 Orchestration 能力

---

# 十、验收标准

## Provider 隔离

- Provider 无交叉 import
- Provider 无隐式调用
- Provider 独立测试

## Runtime

- 单请求单 Provider
- provenance 永远准确
- cache 按 Provider 隔离

## Orchestration

- fallback 显式声明
- fallback 有完整审计记录

最终形成：

> 一个请求，一个 Provider，一个真实数据来源。
