# tstdx v15 重构方案：三代架构统一（三审修订版）

> **文档状态**：待执行（三遍审查修订）
> **创建日期**：2026-09-14
> **修订日期**：2026-09-14（Round 1-3 完整审查）
> **目标**：消除 v12/v13/v14 三代架构并存问题，以 v14 为目标架构，采用 Bridge Pattern 保留 v13 执行引擎，解决模块遮蔽、循环导入、服务面/CLI/facade 多代重复

---

## 0. 三遍审查关键发现

### Round 1 发现：v14 Runtime 不完整

- `Runtime` 类缺失 `UnifiedRuntime` 的全部 7 个业务方法（bars/quotes/snapshot/minute/trades/security_count/security_list）
- 缺失 L2 持久缓存提升、负缓存、SingleFlight 三个关键机制
- `RuntimeGateway` 缺失 `Client` 的 `call()`/`execute_with_policy()`/`__getattr__`/`AsyncClient`
- `batch.py` 的 `SingleFlight` 是简化版（32 行 vs 174 行），缺失 deadline 集成和 observability

### Round 2 发现：当前代码库已处于大规模断裂状态

- **execution/ 包遮蔽 execution.py 文件**：`from tstdx.execution import SingleFlight` 已经 `ImportError`，影响 6 个文件
- **streaming/ 循环导入**：`import tstdx.runtime` 完全失败（streaming/__init__.py → stateful.py → `from . import AsyncQuoteStream` → 包未初始化完成）
- `service.py` 的 `manager.tdx` 接口无法简单委托 v14 Runtime
- `cache.py` 的 `KlineCache` 是原始数据层缓存，不可被 `SemanticResultCache` 替代
- 25 个测试文件 + 13 个非测试文件的导入链已断裂

### Round 3 发现：最优方案是 Bridge Pattern

- v13 的 L2/negative/singleflight 可重构为**装饰器/包装器**注入 v14 的 `SemanticExecutionAdapter`
- v14 `Runtime` 保持纯 DAG 编排器，不添加业务方法
- `RuntimeGateway` 添加缺失方法，内部委托 v13 `UnifiedRuntime` 作为执行引擎
- `facade/strict.py` 的 `manager.tdx` 可通过 `LegacyServiceAdapter` 兼容
- **最小修复仅 8 个文件**（重命名 execution.py + 修复 stateful.py）

---

## 1. 方案选型对比

| 方案 | 描述 | 优点 | 缺点 | 选用 |
|---|---|---|---|---|
| **原方案：v14 直接替代** | 删除 v13 全部，v14 Runtime 补齐业务方法 | 架构最干净 | v14 严重不完整，工作量巨大，25+ 测试断裂 | ❌ |
| **Bridge Pattern（推荐）** | v14 保持 DAG 编排，v13 作为执行引擎被桥接 | 最小改动，不丢功能，渐进迁移 | v13 代码暂不删除 | ✅ |
| **v13+v14 融合** | 将 v13 代码合并进 v14 类中 | 统一类型 | 双 cache key 空间不兼容，实操极复杂 | ❌ |

### 选定方案：Bridge Pattern + 渐进清理

**核心理念**：
1. v14 `Runtime` 保持纯 DAG 编排器（不加业务方法）
2. v14 `RuntimeGateway` 扩展为完整业务表面（补齐 `call()`/`AsyncClient` 等）
3. v13 `UnifiedRuntime` 作为内部执行引擎被 `RuntimeGateway` 持有
4. v13 的 L2/negative/singleflight 通过包装器注入 v14 `SemanticExecutionAdapter`
5. v13 散落文件重命名（不删除），避免遮蔽包目录
6. facade/ 保留为兼容层，通过 `LegacyServiceAdapter` 桥接

---

## 2. 问题诊断（精确版）

### 2.1 致命问题：导入链已断裂

| # | 问题 | 根因 | 影响范围 |
|---|---|---|---|
| P0-1 | `import tstdx.runtime` 完全失败 | `runtime/__init__.py` → `stream.py` → `streaming.state` → `streaming/__init__.py` → `stateful.py` → `from . import AsyncQuoteStream`（已删除的类） | v14 Runtime/RuntimeGateway/StreamHandle 全部不可导入 |
| P0-2 | `from tstdx.execution import SingleFlight` 失败 | `execution/` 包遮蔽 `execution.py` 文件，包 `__init__.py` 不导出 `SingleFlight` | `planned_service.py`、`failure.py`、4 个测试文件断裂 |

### 2.2 严重问题：v14 功能不完整

| # | 缺失能力 | v13 等价物 | 影响 |
|---|---|---|---|
| P1-1 | Runtime 缺 7 个业务方法 | UnifiedRuntime.bars/quotes/snapshot/minute/trades/security_count/security_list | RuntimeGateway 无法替代 Client |
| P1-2 | Runtime 缺 L2 持久缓存提升 | UnifiedRuntime._promote_l2() | 缓存仅 L1，无持久化 |
| P1-3 | Runtime 缺负缓存 | UnifiedRuntime.negative_cache | 确定性失败重复请求 |
| P1-4 | Runtime 缺 SingleFlight | UnifiedRuntime.singleflight.do() | 并发相同请求不合并 |
| P1-5 | RuntimeGateway 缺 call()/__getattr__() | Client.call()/__getattr__() | 40+ 迁移能力不可用 |
| P1-6 | RuntimeGateway 缺 AsyncClient | AsyncClient | 异步客户端不可用 |
| P1-7 | RuntimeGateway 缺 execute_with_policy() | Client.execute_with_policy() | fallback 策略不可用 |

### 2.3 多代重复实现

| 领域 | 代次 | 文件 | 问题 |
|---|---|---|---|
| HTTP 服务面 | v12 legacy / v12 planned / v12 adapter / v13 canonical | 4 个 | http_server + http_app + http_runtime + runtime_http |
| WS 服务面 | v12 legacy / v12 planned / v13 canonical / v13 transport | 4 个 | ws_server + ws_app + runtime_ws + runtime_ws_server |
| CLI | v12 / v13 | 7 个 | parser.py（11 命令）+ cmds_market/cmds_web/cmds_hosts |
| Facade | v12 / v13 / v14 adapter | 12 个 | UnifiedQuoteAPI 被 19 个文件引用 |
| 根级散落文件 | v12/v13/v14 | 30 个 | 15 个 v13 散落文件与 v14 包重叠 |

### 2.4 不可删除的文件（原方案遗漏）

| 文件 | 原方案 | 实际情况 | 修正 |
|---|---|---|---|
| `service.py` | 删除 | `facade/strict.py:28` 直接依赖 `UnifiedMarketDataService` + `.manager.tdx` | **保留**，改为委托 RuntimeGateway |
| `cache.py` | 删除 | `sources/__init__.py:572` 依赖 `KlineCache`（原始数据层缓存） | **保留**，与 SemanticResultCache 不同层 |
| `client_api.py` | 删除 | `cli/runtime_commands.py:13` 直接导入 `Client` | **保留**，改为委托 RuntimeGateway |
| `planned_service.py` | 删除 | 15 个测试文件 + 5 个非测试文件依赖 | **保留**，改为委托 v14 Runtime |
| `freshness.py` / `health.py` / `failure.py` | 合并到 runtime/ | 7+ 测试文件直接导入 | **保留**，不迁移 |
| `orchestration.py` | 删除 | `client_api.py` + `integration/serialization.py` 依赖 | **保留** |

---

## 3. 目标架构（Bridge Pattern）

### 3.1 架构图

```
                    ┌─────────────────────────────────┐
                    │         公开 API 层              │
                    │  TdxClient · AsyncTdxClient      │
                    │  RuntimeGateway · Client (兼容)   │
                    │  UnifiedQuoteAPI (facade 兼容)    │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │      v14 Runtime (DAG 编排器)     │
                    │  Runtime.execute(QueryRequest)   │
                    │  Runtime.execute_batch()         │
                    │  Runtime.subscribe()             │
                    │  Runtime.execute_typed()         │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │   SemanticExecutionAdapter       │
                    │   (扩展: L2/negative/singleflight)│
                    │   ← ExecutionWrapper (新增)       │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │    ExecutionPlanner (DAG)         │
                    │    ProviderRouter (v14 适配器)     │
                    │    TdxProvider / WebProvider /    │
                    │    LocalProvider                  │
                    └─────────────────────────────────┘

Bridge: RuntimeGateway 持有 UnifiedRuntime 实例
        作为"执行引擎"处理 L2/negative/singleflight
```

### 3.2 目标包结构

```
tstdx/
├── __init__.py              # 包入口（惰性导入）
├── __main__.py              # CLI 入口
├── errors.py                # 错误分类树（保留）
├── error_envelope.py        # ErrorEnvelope（保留）
├── deprecation.py           # 弃用工具（保留）
├── native.py                # 兼容入口（保留，v1.5.0 删除）
│
├── codec/                   # 编解码（不变）
├── protocol/                # 协议核心（不变）
├── transport/               # 传输层（不变）
├── client/                  # 客户端层（不变）
├── reader/                  # 本地 vipdoc 解析（不变）
├── domain/                  # 领域模型（不变）
├── charset/                 # 字符集（不变）
├── profile/                 # 数据规格（不变）
├── config/                  # 配置（不变）
├── web/                     # HTTP Web 源（不变）
├── output/                  # 输出 Sink（不变）
├── sink/                    # LocalDaySink（不变）
├── feedback/                # 反馈/遥测（不变）
├── security/                # 安全（不变）
├── observability/           # 可观测性（不变）
├── tools/                   # 工具链（不变）
├── trade/                   # 交易模拟器（不变）
│
├── providers/               # 静态注册表（保留，共享基础）
├── provider/                # v14 动态适配器（保留）
├── execution/               # v14 DAG 编排 + 执行原语（扩展）
│   ├── __init__.py          # 导出 DAG 类 + 执行原语
│   ├── graph.py             # ExecutionGraph
│   ├── node.py              # ExecutionNode
│   ├── plan.py              # ExecutionPlan
│   ├── planner.py           # ExecutionPlanner
│   ├── semantic.py          # SemanticExecutionAdapter（扩展 L2/negative/singleflight）
│   └── primitives.py       # 【新增】SingleFlight/ExecutionBudget/BatchPlan/BatchPlanner
│
├── runtime/                 # v14 Runtime 编排内核（唯一 runtime 包）
│   ├── __init__.py          # 导出 Runtime/RuntimeGateway/...
│   ├── bootstrap.py         # create_runtime()
│   ├── context.py           # ExecutionContext
│   ├── gateway.py           # RuntimeGateway（扩展 call()/AsyncClient）
│   ├── request.py           # QueryRequest
│   ├── response.py          # QueryResponse
│   ├── runtime.py           # Runtime（纯 DAG 编排器）
│   ├── stream.py            # StreamHandle
│   ├── typed.py             # request_from_typed
│   └── legacy_bridge.py    # 【新增】桥接 UnifiedRuntime 作为执行引擎
│
├── streaming/               # 流式订阅（修复循环导入）
│   ├── __init__.py          # 修复幽灵导入
│   ├── state.py             # StreamState/StreamLifecycle
│   ├── stateful.py          # StatefulQuoteStream（修复导入链）
│   ├── planned.py           # PlannedQuoteStream
│   └── push.py              # PushChannel
│
├── query.py                 # QuerySpec/QueryPlan/QueryFingerprint（保留）
├── result.py                # QueryResult/Provenance（保留）
├── batch.py                 # BatchSpec/BatchItem/BatchResult（保留）
├── cache_semantic.py        # SemanticResultCache L1（保留）
├── cache_persistent.py      # PersistentSemanticCache L2（保留）
├── stream_contract.py       # StreamPlanner/StreamSpec（保留）
├── typed_query.py           # Typed Query 契约（保留）
│
│   === 以下为保留的 v13 兼容文件（重命名避免遮蔽） ===
│
├── runtime_v13.py           # 【重命名自 runtime.py】UnifiedRuntime（兼容执行引擎）
├── execution_primitives.py  # 【重命名自 execution.py】v13 执行原语（被 execution/primitives.py 引用）
├── client_api.py            # 【保留+修改】Client → 委托 RuntimeGateway
├── planned_service.py       # 【保留+修改】UnifiedMarketDataService → 委托 Runtime
├── orchestration.py         # 【保留】FallbackPolicy（RuntimeGateway 使用）
├── service.py               # 【保留】旧版 service（facade/strict.py 依赖）
├── cache.py                 # 【保留】KlineCache（sources/ 依赖）
├── freshness.py             # 【保留】散落原语（不迁移，避免测试断裂）
├── health.py                # 【保留】散落原语
├── failure.py               # 【保留】散落原语（改为从 execution_primitives 导入）
├── direct_provider.py       # 【保留】v13 绑定（runtime_v13.py 依赖）
├── capability_catalog.py    # 【保留】能力目录（client_api.py 依赖）
├── async_service.py         # 【保留】异步服务
├── client_core.py           # 【保留】客户端核心
├── provider_api.py          # 【保留】Provider API 类型
├── cache_v2.py              # 【保留】v12 缓存
├── semantic_cache.py        # 【保留】v12 语义缓存
│
├── facade/                  # 兼容层（保留，标记 deprecated）
│   ├── __init__.py
│   ├── api.py               # UnifiedQuoteAPI
│   ├── runtime_adapter.py   # 桥接到 v14 Runtime
│   ├── legacy_service_adapter.py  # 【新增】manager.tdx 兼容接口
│   └── ...
│
├── cli/                     # CLI（统一到 v13）
│   ├── __init__.py          # main() 入口
│   ├── parser.py            # 统一参数解析器（扩展命令）
│   ├── runtime_commands.py  # 全部命令处理器（扩展）
│   └── _common.py           # 公共工具
│
└── integration/             # 服务面（统一到 v13 canonical）
    ├── __init__.py
    ├── runtime_http.py      # HTTP REST（唯一标准）
    ├── runtime_ws.py        # WS JSON-RPC 处理器（唯一）
    ├── runtime_ws_server.py # WS 传输层
    ├── serialization.py     # 结果序列化
    └── mcp/                 # MCP 工具服务
```

### 3.3 重命名清单（避免遮蔽）

| 原文件名 | 新文件名 | 理由 |
|---|---|---|
| `tstdx/runtime.py` | `tstdx/runtime_v13.py` | 避免与 `runtime/` 包遮蔽 |
| `tstdx/execution.py` | `tstdx/execution_primitives.py` | 避免与 `execution/` 包遮蔽 |

### 3.4 新增文件清单

| 文件 | 用途 |
|---|---|
| `tstdx/execution/primitives.py` | 从 `execution_primitives.py` 导入并 re-export v13 执行原语 |
| `tstdx/runtime/legacy_bridge.py` | 桥接 `UnifiedRuntime` 作为 v14 执行引擎 |
| `tstdx/facade/legacy_service_adapter.py` | 提供 `manager.tdx` 兼容接口 |

### 3.5 修改清单（不删除任何文件）

| 文件 | 修改内容 |
|---|---|
| `tstdx/execution/__init__.py` | 添加 `from .primitives import ExecutionBudget, SingleFlight, BatchPlan, BatchPlanner` |
| `tstdx/streaming/__init__.py` | 修复幽灵导入（移除 `from .stateful import` 或改为延迟导入） |
| `tstdx/streaming/stateful.py:14` | 修复 `from . import AsyncQuoteStream` → 从正确模块导入或定义 stub |
| `tstdx/planned_service.py:34` | `from .execution import` → `from .execution_primitives import` |
| `tstdx/failure.py:24` | `from .execution import` → `from .execution_primitives import` |
| `tstdx/client_api.py:25` | `from .runtime import UnifiedRuntime` → `from .runtime_v13 import UnifiedRuntime` |
| `tstdx/orchestration.py:16` | `from .runtime import UnifiedRuntime` → `from .runtime_v13 import UnifiedRuntime` |
| `tstdx/runtime.py` (重命名后) | 无修改，仅文件名变更 |
| `tstdx/execution.py` (重命名后) | 无修改，仅文件名变更 |
| 测试文件（6 个） | 更新导入路径：`from tstdx.execution import` → `from tstdx.execution_primitives import` |
| 测试文件（3 个） | 更新导入路径：`from tstdx.runtime import UnifiedRuntime` → `from tstdx.runtime_v13 import UnifiedRuntime` |

---

## 4. 执行计划

### Phase 0：修复导入断裂（P0 紧急）

**目标**：修复循环导入和模块遮蔽，使 `import tstdx.runtime` 和 `from tstdx.execution import SingleFlight` 恢复工作

**步骤**：

1. **修复 streaming/ 循环导入**
   - `streaming/stateful.py:14`：`from . import AsyncQuoteStream, QuoteStream, Subscription, on_error_t, on_quote_t` → 改为从 `streaming/planned.py` 或其他模块导入等价类，或使用 `TYPE_CHECKING` + 延迟导入
   - `streaming/__init__.py:28`：如果 `stateful.py` 依赖的基类已删除，需在 `__init__.py` 中提供 stub 或从 `planned.py` 别名导出

2. **重命名 execution.py 消除遮蔽**
   - `tstdx/execution.py` → `tstdx/execution_primitives.py`
   - 新建 `tstdx/execution/primitives.py`：`from ..execution_primitives import ExecutionBudget, SingleFlight, BatchPlan, BatchPlanner`
   - `tstdx/execution/__init__.py`：添加 `from .primitives import ExecutionBudget, SingleFlight, BatchPlan, BatchPlanner`
   - 更新 `planned_service.py:34` 和 `failure.py:24` 的导入

3. **重命名 runtime.py 消除遮蔽**
   - `tstdx/runtime.py` → `tstdx/runtime_v13.py`
   - 更新 `client_api.py:25` 和 `orchestration.py:16` 的导入

4. **更新测试文件导入路径**
   - 6 个测试文件：`from tstdx.execution import SingleFlight` → `from tstdx.execution_primitives import SingleFlight`
   - 3 个测试文件：`from tstdx.runtime import UnifiedRuntime` → `from tstdx.runtime_v13 import UnifiedRuntime`

5. **验证**
   ```bash
   python -c "import tstdx; print('OK')"
   python -c "from tstdx.runtime import Runtime, RuntimeGateway; print('OK')"
   python -c "from tstdx.execution import ExecutionPlanner, SingleFlight; print('OK')"
   python -c "from tstdx.runtime_v13 import UnifiedRuntime; print('OK')"
   python -c "from tstdx.execution_primitives import SingleFlight, ExecutionBudget; print('OK')"
   ```

**预计变更**：~15 个文件（2 重命名 + 5 修改 + 2 新建 + 6 测试修改）

### Phase 1：扩展 RuntimeGateway（Bridge Pattern）

**目标**：RuntimeGateway 成为完整业务表面，补齐 Client 的缺失方法

**步骤**：

1. **新建 `runtime/legacy_bridge.py`**
   - 创建 `LegacyRuntimeBridge` 类，持有 `UnifiedRuntime` 实例
   - 提供 `execute_spec(QuerySpec)` 方法，走 v13 的 L2/negative/singleflight 路径
   - 提供 7 个业务方法（bars/quotes/snapshot/minute/trades/security_count/security_list）

2. **扩展 `runtime/gateway.py`**
   - 添加 `call(capability, *args, **kwargs)` 方法
   - 添加 `__getattr__` 动态方法（支持 40+ 迁移能力）
   - 添加 `execute_with_policy(spec, *, policy, use_cache)` 方法
   - 添加 `AsyncClient` 类（`asyncio.to_thread` 桥接）
   - 内部持有 `LegacyRuntimeBridge` 实例

3. **扩展 `execution/semantic.py`**
   - 在 `SemanticExecutionAdapter` 中注入 L2/negative/singleflight 包装器
   - 创建 `ExecutionWrapper` 类，包装 `Runtime.execute()` 的返回值

4. **修改 `client_api.py`**
   - `Client.__init__` 改为委托 `RuntimeGateway`
   - 保留 `call()`/`__getattr__`/`AsyncClient` 的 API 表面

5. **验证**
   ```bash
   python -c "from tstdx.runtime import RuntimeGateway; gw = RuntimeGateway.create(); print(gw.call('bars', 'sh600519', count=30))"
   python -c "from tstdx.client_api import Client; c = Client(); print(c.bars('sh600519', count=30))"
   ```

**预计变更**：~5 个文件（2 新建 + 3 修改）

### Phase 2：统一服务面

**目标**：删除 v12 HTTP/WS 服务器，保留 v13 canonical

**步骤**：

1. **迁移 HTTP 路由到 runtime_http.py**
   - 将 `http_server.py` 的 42 端点路由定义迁移到 `runtime_http.py`
   - 内部实现从 `ProviderHttpClient` 切换到 `RuntimeGateway`

2. **迁移 WS 处理器到 runtime_ws.py**
   - 将 `ws_server.py` 的 JSON-RPC 方法表迁移到 `runtime_ws.py`

3. **删除旧代文件**
   - `integration/http_server.py`、`http_app.py`、`http_runtime.py`
   - `integration/ws_server.py`、`ws_app.py`

4. **更新 integration/__init__.py**
   - 导出从 `runtime_http.py` / `runtime_ws.py` / `runtime_ws_server.py`

**预计变更**：~10 个文件（5 删除 + 5 修改）

### Phase 3：统一 CLI

**目标**：将 v12 CLI 命令迁移到 v13 runtime_commands.py

**步骤**：

1. **确认 v12 CLI 是否被使用**
   - `cli/__init__.py` 的 `main()` 只调用 `parser.py` 的 `build_parser()`
   - 如果 `cmds_market/cmds_web/cmds_hosts` 不被 `parser.py` 引用，直接删除

2. **如需迁移命令，添加到 runtime_commands.py**
   - `cmds_hosts.py` 的 `hosts audit`/`server-test` → `runtime_commands.py`
   - `cmds_market.py` 的 `serve`/`probe` → `runtime_commands.py`
   - 扩展 `parser.py` 添加新子命令

3. **删除旧文件**
   - `cli/cmds_market.py`、`cmds_web.py`、`cmds_hosts.py`

**预计变更**：~5 个文件（3 删除 + 2 修改）

### Phase 4：Facade 兼容层标注

**目标**：保留 facade/ 但标记 deprecated，添加 LegacyServiceAdapter

**步骤**：

1. **新建 facade/legacy_service_adapter.py**
   - 创建 `LegacyServiceAdapter` 类
   - 提供 `.manager.tdx` 接口，内部委托 v14 `ProviderRouter`

2. **添加 deprecation 警告**
   - `facade/api.py` 的 `UnifiedQuoteAPI.__init__` 添加 `DeprecationWarning`

3. **验证 facade/strict.py 依赖链**
   - 确认 `LegacyServiceAdapter` 正确提供 `.manager.tdx`

**预计变更**：~3 个文件（1 新建 + 2 修改）

### Phase 5：导入链验证与测试

**目标**：确保所有导入链正确，测试通过

**步骤**：

1. **全量导入验证**
   ```bash
   python -c "import tstdx; print('OK')"
   python -c "from tstdx import TdxClient; print('OK')"
   python -c "from tstdx.runtime import Runtime, RuntimeGateway; print('OK')"
   python -c "from tstdx.execution import ExecutionPlanner, SingleFlight; print('OK')"
   python -c "from tstdx.runtime_v13 import UnifiedRuntime; print('OK')"
   python -c "from tstdx.client_api import Client; print('OK')"
   python -c "from tstdx.facade import UnifiedQuoteAPI; print('OK')"
   ```

2. **Ruff + mypy 清洁**
   ```bash
   ruff check tstdx/
   python -m mypy tstdx/ --ignore-missing-imports
   ```

3. **测试运行**
   ```bash
   python -m pytest tests/ -x -q
   ```

4. **覆盖率门禁**
   ```bash
   python -m pytest --cov=tstdx --cov-fail-under=77
   ```

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| streaming/ 修复幽灵导入可能改变流式行为 | 对比修复前后 QuoteStream/StatefulQuoteStream 行为 |
| execution/ 包导出执行原语可能与 batch.py 的 SingleFlight 冲突 | 统一使用 execution/ 版本（174 行完整版），batch.py 版本改为别名 |
| RuntimeGateway 扩展可能引入循环导入 | legacy_bridge.py 使用延迟导入 UnifiedRuntime |
| facade LegacyServiceAdapter 可能不完整 | 保留 service.py 作为 fallback，LegacyServiceAdapter 仅为优化 |
| 测试文件导入路径修改遗漏 | 使用 grep 全量搜索每个待修改导入 |

---

## 6. 执行顺序与提交策略

| Phase | 内容 | 预计文件变更 | 提交信息 |
|---|---|---|---|
| P0 | 修复导入断裂 | 2 重命名 + 2 新建 + 11 修改 = ~15 | `fix(v15): resolve module shadowing and streaming circular import` |
| P1 | Bridge Pattern | 2 新建 + 3 修改 = ~5 | `feat(v15): bridge v13 UnifiedRuntime into v14 RuntimeGateway` |
| P2 | 统一服务面 | 5 删除 + 5 修改 = ~10 | `refactor(v15): unify integration surfaces to v13 canonical` |
| P3 | 统一 CLI | 3 删除 + 2 修改 = ~5 | `refactor(v15): unify CLI to parser.py + runtime_commands.py` |
| P4 | Facade 兼容层 | 1 新建 + 2 修改 = ~3 | `refactor(v15): mark facade as deprecated compat layer` |
| P5 | 验证与测试 | 修复测试导入 | `test(v15): fix import paths and verify all gates` |

**总预计**：~43 个文件变更（2 重命名 + 5 新建 + 8 删除 + 28 修改）

---

## 7. 验收标准

- [ ] `python -c "import tstdx"` 无错误
- [ ] `python -c "from tstdx.runtime import Runtime, RuntimeGateway"` 无错误
- [ ] `python -c "from tstdx.execution import ExecutionPlanner, SingleFlight"` 无错误
- [ ] `python -c "from tstdx.runtime_v13 import UnifiedRuntime"` 无错误
- [ ] `python -c "from tstdx.client_api import Client"` 无错误
- [ ] `python -c "from tstdx.facade import UnifiedQuoteAPI"` 无错误
- [ ] 无模块遮蔽冲突（`runtime.py` / `execution.py` 已重命名）
- [ ] 无循环导入（`streaming/` 幽灵导入已修复）
- [ ] `ruff check tstdx/` 0 violations
- [ ] `mypy tstdx/` 0 errors
- [ ] `pytest tests/ -x -q` 全部通过
- [ ] `pytest --cov=tstdx --cov-fail-under=77` 通过
- [ ] `integration/` 文件数从 14 降到 ~6
- [ ] `cli/` 文件数从 7 降到 3-4
- [ ] v14 RuntimeGateway 支持 `call()` / `execute_with_policy()` / `AsyncClient`
- [ ] facade/strict.py 的 `manager.tdx` 访问正常工作

---

## 8. 与原方案的关键差异

| 维度 | 原方案 | 修订方案 |
|---|---|---|
| 架构策略 | v14 直接替代 v13 | Bridge Pattern，v13 作为执行引擎 |
| 文件处理 | 删除 25 个 v13 文件 | 重命名 2 个 + 保留全部，不删除根级文件 |
| 最小修复 | 未识别 | Phase 0 优先修复 2 个断裂（8 文件） |
| runtime.py | 删除 | 重命名为 runtime_v13.py |
| execution.py | 合并到 batch.py | 重命名为 execution_primitives.py + execution/primitives.py |
| service.py | 删除 | 保留（facade/strict.py 依赖） |
| cache.py | 删除 | 保留（sources/ 依赖 KlineCache） |
| client_api.py | 删除 | 保留+修改（委托 RuntimeGateway） |
| L2/negative/singleflight | 合并进 Runtime | 通过 SemanticExecutionAdapter 包装器注入 |
| 总文件变更 | ~40 文件 | ~43 文件（但改动更浅，风险更低） |
