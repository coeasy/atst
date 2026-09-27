# atst Public API Convergence Plan V1

## 目标

将 atst 当前多入口 API 收敛为稳定、清晰、可长期维护的公共接口体系。

目标用户入口：

```python
from atst import Client, AsyncClient
```

Client 作为普通用户、量化应用、Agent、数据服务的统一入口。

---

## 当前接口问题

当前存在多个公开入口：

- Client / AsyncClient
- TdxClient / AsyncTdxClient
- UnifiedQuoteAPI
- RuntimeGateway
- Runtime

这些接口能力存在重叠：

- 查询能力重复
- Provider 选择逻辑分散
- 文档入口不一致
- 新用户学习成本高

---

## 目标 API 分层

```text
应用层
 |
 v
Client / AsyncClient
 |
 v
QuerySpec / Typed Query
 |
 v
Unified Runtime
 |
 v
Provider Contract
 |
 v
Protocol / Transport
```

---

# Level 1 普通用户 API

## 同步

```python
from atst import Client

with Client() as client:
    result = client.bars("sh600519", count=100)
```

支持：

- quotes
- bars
- snapshot
- minute
- trades
- security_list
- security_count
- finance

---

## 异步

```python
from atst import AsyncClient

async with AsyncClient() as client:
    result = await client.quotes([
        "sh600519",
        "sz000001",
    ])
```

---

# Level 2 高级查询 API

用于量化平台和框架集成。

```python
QuerySpec
    |
    v
client.execute(spec)
```

示例：

```python
spec = QuerySpec.build(
    "bars",
    symbols="sh600519",
    provider="tdx",
    count=100,
)

result = client.execute(spec)
```

---

# Level 3 Runtime API

Runtime 只服务于框架开发者。

职责：

- Query 编排
- Batch 执行
- Cache
- Stream 生命周期
- Provider 调度

不作为普通用户入口。

---

# 接口迁移策略

## 保留

### Client

状态：核心入口

### AsyncClient

状态：核心入口

### QuerySpec

状态：稳定协议层

### UnifiedRuntime

状态：内部执行内核

---

## 逐步降级

### TdxClient

迁移：

```
TdxClient
  -> Client
```

保留兼容周期，但停止新增能力。

---

### UnifiedQuoteAPI

迁移：

```
UnifiedQuoteAPI
  -> Client capability
```

Facade 不再拥有独立业务逻辑。

---

### RuntimeGateway

定位调整：

```
HTTP
WS
CLI
MCP
    |
    v
RuntimeGateway
    |
    v
Client contract
```

只作为服务适配层。

---

# 返回值统一

所有公开 API 统一返回：

```
QueryResult
 |
 +-- data
 |
 +-- meta
      |
      +-- provider
      +-- channel
      +-- fingerprint
      +-- provenance
```

禁止不同入口返回不同数据结构。

---

# Provider 规则

默认：单 Provider 执行。

禁止：

```
TDX失败
  -> 偷偷切 Web
```

需要降级：

```python
FallbackPolicy.build(
    "tdx",
    "eastmoney",
    "tencent",
)
```

所有 fallback 必须记录 provenance。

---

# 实施阶段

## Phase 1

- 更新 README
- 更新 quickstart
- 更新 API 文档
- 增加 API parity tests

## Phase 2

- Client 成为唯一业务入口
- Facade 逻辑迁移
- TdxClient 标记 deprecated

## Phase 3

- RuntimeGateway 服务化
- CLI/HTTP/WS/MCP 全部复用 Runtime

## Phase 4

- 删除重复逻辑
- 完成 API freeze

---

# 验收标准

- 新用户只需要学习 Client
- 所有入口结果结构一致
- Provider provenance 完整
- Runtime 无业务重复逻辑
- 文档和代码接口一致
