# atst 优化重构方案 V1

## 1. 背景与目标

`atst` 当前已经具备 TDX 协议解析、Provider 管理、Runtime 编排、缓存、批量执行、流式订阅和多服务入口能力。下一阶段重点不是继续堆叠功能，而是进行架构收敛：

- 统一用户 API 入口
- 收敛 Runtime 分层职责
- 消除旧接口与新架构混杂
- 强化 Provider / Capability / Contract 一致性
- 提升扩展性、可维护性和生态集成能力

目标架构：

```
Application / Agent / Quant Platform
                |
                v
        Client / AsyncClient
                |
                v
       QuerySpec / Typed Query
                |
                v
       Unified Runtime Kernel
                |
                v
 DirectProviderExecutor + ProviderContract
                |
        TDX / Web / Local / Custom
```

---

# 2. 当前问题分析

## 2.1 多入口并存

当前存在：

- Client
- TdxClient
- UnifiedQuoteAPI
- RuntimeGateway
- Runtime

导致用户不知道推荐入口。

优化目标：

最终公开 API：

```
atst.Client
 atst.AsyncClient
```

其它接口按照层级隐藏：

- QuerySpec：高级用户
- Runtime：框架开发
- Provider：插件开发

---

# 3. API 收敛方案

## 3.1 普通用户接口

统一：

```python
from atst import Client

with Client() as client:
    result = client.bars("sh600519", count=100)
```

核心能力：

- quotes
- bars
- snapshot
- minute
- trades
- security_list
- finance

统一返回：

```
QueryResult
 ├── data
 └── meta
      ├── provider
      ├── channel
      ├── fingerprint
      └── provenance
```

---

# 4. Runtime 重构方案

## 4.1 v13 Runtime

定位：

> 单 Provider 语义执行内核

职责：

- QueryPlan 编译
- Cache
- SingleFlight
- DirectProviderExecutor
- Result Provenance

禁止：

- 多 Provider 自动 fallback
- 服务协议转换

---

## 4.2 v14 Runtime

定位：

> 编排网关层

职责：

- Batch
- DAG
- Typed Query
- Stream lifecycle
- Gateway adapter

禁止重新实现：

- Provider 选择
- Cache
- Result identity

---

# 5. Provider 架构优化

目标：

```
ProviderRegistry
        |
        v
Capability Contract
        |
        v
DirectProviderExecutor
```

新增门禁：

- Provider 注册必须有 capability
- Capability 必须有 executor
- Executor 必须有 contract test
- 文档自动生成能力矩阵

---

# 6. Fallback 重构

禁止：

```
TDX失败
 -> 自动切 Web
 -> 自动切本地
```

改为显式：

```python
FallbackPolicy.build(
    "tdx",
    "eastmoney",
    "tencent"
)
```

所有 fallback 必须记录：

- requested_provider
- actual_provider
- attempts
- reason

---

# 7. Cache 优化

## 当前问题

需要区分：

- 数据来源
- 缓存来源

优化：

```
Direct Data
    |
    v
Semantic Cache
    |
    v
Replay
```

缓存必须保留 provenance。

Negative Cache：

只缓存确定性失败：

允许：

- invalid symbol
- unsupported capability

禁止：

- timeout
- network error
- HTTP 5xx
- temporary unavailable

---

# 8. Batch 执行统一

当前：

- Client batch
- Runtime batch

存在重复逻辑。

统一：

```
BatchSpec
   |
BatchPlanner
   |
ExecutionGraph
   |
BatchResult
```

支持：

- cache dedup
- concurrency
- partial failure
- ordered result

---

# 9. Typed Capability 升级

长期目标：

减少：

```python
args=dict()
kwargs=dict()
```

增加：

```python
FinanceQuery
BarsQuery
FundQuery
NewsQuery
```

每个 capability 必须包含：

- schema
- validator
- fingerprint
- executor
- tests

---

# 10. Streaming 优化

统一链路：

```
StreamSpec
   |
StreamPlanner
   |
StreamLifecycle
   |
Provider Stream
```

强化：

- reconnect
- backpressure
- gap fill
- state recovery

---

# 11. 服务层统一

所有入口：

```
CLI
HTTP
WebSocket
MCP
Agent
```

统一进入：

```
RuntimeGateway
        |
        v
Client Contract
```

禁止服务层复制业务逻辑。

---

# 12. 文档治理

需要同步更新：

- README.md
- docs/quickstart.md
- docs/api/interfaces.md
- docs/api/v14-runtime.md

明确：

推荐入口：

```
Client
AsyncClient
```

历史兼容：

```
TdxClient
UnifiedQuoteAPI
```

---

# 13. CI 新增门禁

新增：

## provider-contract

检查：

Registry == Executor == Test

## runtime-parity

检查：

Client / Runtime / HTTP / WS / MCP

结果一致。

## capability-completeness

检查新增 capability 是否完整。

## docs-alignment

检查公开 API 文档一致。

---

# 14. 实施阶段

## Phase 1 API 收敛

- Client 作为唯一入口
- 更新文档
- 标记旧接口

## Phase 2 Runtime 收敛

- v13 kernel
- v14 gateway
- 删除重复逻辑

## Phase 3 Provider Contract

- capability matrix
- contract tests

## Phase 4 Batch/Cache

- unified executor
- cache policy

## Phase 5 Typed Query

- capability schema
- domain records

## Phase 6 Service Ecosystem

- CLI
- HTTP
- WS
- MCP

---

# 15. 最终目标

`atst` 最终成为：

> 高性能、可扩展、Provider-first 的金融市场数据基础设施。

核心特点：

- 一个用户入口
- 一个执行内核
- 一个结果模型
- 一个 Provider Contract
- 多种部署方式
- 可被量化系统、Agent、数据平台直接复用
