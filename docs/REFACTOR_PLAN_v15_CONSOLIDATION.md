# tstdx v15 重构方案：三代架构统一

> **文档状态**：待执行
> **创建日期**：2026-09-14
> **目标**：消除 v12/v13/v14 三代架构并存问题，以 v14 为唯一目标架构，解决模块遮蔽、导入链断裂、服务面/CLI/facade 多代重复

---

## 1. 问题诊断

### 1.1 严重问题：模块遮蔽导致导入链断裂

| # | 冲突 | 文件 | 包 | 影响 |
|---|---|---|---|---|
| P1 | `runtime.py` vs `runtime/` | 导出 `UnifiedRuntime`（v13） | 导出 `Runtime`（v14） | `client_api.py`、`orchestration.py` 的 `from .runtime import UnifiedRuntime` 与 `facade/runtime_adapter.py` 的 `from ..runtime import Runtime` 互相矛盾，Python 导入行为不确定 |
| P2 | `execution.py` vs `execution/` | 导出 `SingleFlight`/`BatchPlanner`（v13） | 导出 `ExecutionPlanner`（v14） | `planned_service.py` 的 `from .execution import BatchPlan` 与 `runtime/runtime.py` 的 `from ..execution.planner import ExecutionPlanner` 互相矛盾 |

### 1.2 多代重复实现

| 领域 | 代次 | 文件数 | 问题 |
|---|---|---|---|
| HTTP 服务面 | v12 legacy / v12 planned / v12 adapter / v13 canonical | 4 | `http_server.py` + `http_app.py` + `http_runtime.py` + `runtime_http.py` 四代共存 |
| WS 服务面 | v12 legacy / v12 planned / v13 canonical / v13 transport | 4 | `ws_server.py` + `ws_app.py` + `runtime_ws.py` + `runtime_ws_server.py` 四代共存 |
| CLI | v12 / v13 | 7 | `parser.py`（11 命令）+ `cmds_market/cmds_web/cmds_hosts`（v12 遗留）+ `_common.py` |
| Facade | v12 / v13 / v14 adapter | 12 | `facade/api.py`（UnifiedQuoteAPI）被 19 个文件引用；`client_api.py`（v13 Client）声明为"唯一业务表面" |
| Provider | v12 / v13 / v14 | 4 实体 | `provider/`（v14 适配器）+ `providers/`（静态注册表）+ `provider_api.py`（v12 API 类型）+ `direct_provider.py`（v13 绑定） |

### 1.3 v13 根级散落文件

`tstdx/` 根目录有 30 个 .py 文件，其中大量 v13 散落文件与 v14 包结构重叠：

| v13 根级文件 | v14 包等价物 | 状态 |
|---|---|---|
| `runtime.py`（UnifiedRuntime） | `runtime/`（Runtime） | **遮蔽冲突** |
| `execution.py`（SingleFlight/BatchPlanner） | `execution/`（ExecutionPlanner） | **遮蔽冲突** |
| `client_api.py`（Client） | `runtime/gateway.py`（RuntimeGateway） | 功能重叠 |
| `planned_service.py`（UnifiedMarketDataService） | `runtime/runtime.py`（Runtime） | 功能重叠 |
| `orchestration.py`（FallbackPolicy） | `runtime/` 内部编排 | 功能重叠 |
| `service.py`（旧版 service） | `planned_service.py` / `runtime/` | 历史遗留 |
| `direct_provider.py`（v13 绑定） | `provider/`（v14 适配器） | 功能重叠 |
| `provider_api.py`（v12 API 类型） | `providers/`（静态注册表） | 功能重叠 |
| `capability_catalog.py` | `typed_query.py` / `runtime/typed.py` | 功能重叠 |
| `cache_v2.py` / `semantic_cache.py` | `cache_semantic.py` / `cache_persistent.py` | 重复缓存 |
| `freshness.py` / `health.py` / `failure.py` | v14 内部 | 散落原语 |
| `async_service.py` | `runtime/` 内部 | 历史遗留 |
| `client_core.py` | `client/` | 历史遗留 |
| `cache.py` / `native.py` | 兼容层 | 保留 |

---

## 2. 目标架构（v14 唯一标准）

### 2.1 架构决策（已确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| 目标架构 | **v14 为唯一架构** | Phase 1-8 全部完成，DAG 编排 + Typed Query + Semantic Cache 最完整 |
| Facade 处理 | **保留为兼容层，逐步迁移** | 19 个文件引用 facade/，立即删除风险过大；facade/runtime_adapter.py 已桥接到 v14 |
| 服务面统一 | **v13 canonical 为唯一标准** | runtime_http.py + runtime_ws.py + runtime_ws_server.py 保留，删除旧代 |
| CLI 统一 | **统一到 v13 parser.py + runtime_commands.py** | 11 命令已基于 Client，将 v12 命令迁移过来 |

### 2.2 目标包结构

```
tstdx/
├── __init__.py              # 包入口（惰性导入，不变）
├── __main__.py              # CLI 入口
├── errors.py                # 错误分类树 E1-E8（保留）
├── error_envelope.py        # ErrorEnvelope（保留）
├── deprecation.py           # 弃用工具（保留）
├── native.py                # 兼容入口（保留，v1.5.0 删除）
│
├── codec/                   # 编解码（不变）
├── protocol/                # 协议核心（不变）
│   └── parsers/
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
├── execution/               # v14 DAG 编排（保留，吸收 v13 散落原语）
├── runtime/                 # v14 Runtime 编排内核（保留，唯一 runtime）
├── streaming/               # 流式订阅（保留）
├── query.py                 # QuerySpec/QueryPlan/QueryFingerprint（保留）
├── result.py                # QueryResult/Provenance（保留）
├── batch.py                 # BatchSpec/BatchItem/BatchResult（保留）
├── cache_semantic.py        # SemanticResultCache L1（保留）
├── cache_persistent.py       # PersistentSemanticCache L2（保留）
├── stream_contract.py       # StreamPlanner/StreamSpec（保留）
├── typed_query.py           # Typed Query 契约 + records_from_response（保留）
│
├── facade/                  # 兼容层（保留，标记 deprecated）
│   ├── __init__.py          # 导出 UnifiedQuoteAPI 等
│   ├── api.py               # UnifiedQuoteAPI（46 方法）
│   ├── runtime_adapter.py  # 桥接到 v14 Runtime（关键）
│   ├── response.py          # ApiResponse
│   └── ...                  # 其他兼容模块
│
├── cli/                     # CLI（统一到 v13）
│   ├── __init__.py          # main() 入口
│   ├── parser.py            # 统一参数解析器
│   ├── runtime_commands.py  # 全部命令处理器
│   └── _common.py           # 公共工具（从 v12 迁移）
│
└── integration/             # 服务面（统一到 v13 canonical）
    ├── __init__.py
    ├── runtime_http.py      # HTTP REST（唯一）
    ├── runtime_ws.py        # WS JSON-RPC 处理器（唯一）
    ├── runtime_ws_server.py # WS 传输层（唯一）
    ├── serialization.py     # 结果序列化
    └── mcp/                 # MCP 工具服务
```

### 2.3 删除清单

| 文件 | 代次 | 删除理由 |
|---|---|---|
| `tstdx/runtime.py` | v13 | 与 `runtime/` 包遮蔽，UnifiedRuntime 被 v14 Runtime 替代 |
| `tstdx/execution.py` | v13 | 与 `execution/` 包遮蔽，SingleFlight/BatchPlanner 合并到 `execution/` 或 `batch.py` |
| `tstdx/client_api.py` | v13 | Client 被 RuntimeGateway 替代 |
| `tstdx/planned_service.py` | v13 | UnifiedMarketDataService 被 Runtime 替代 |
| `tstdx/orchestration.py` | v13 | FallbackPolicy 被 v14 编排替代 |
| `tstdx/service.py` | v12 | 旧版 service |
| `tstdx/async_service.py` | v13 | 异步服务被 v14 Runtime 内部处理 |
| `tstdx/client_core.py` | v13 | 客户端核心被 client/ 替代 |
| `tstdx/direct_provider.py` | v13 | 被 provider/ v14 适配器替代 |
| `tstdx/provider_api.py` | v12 | 被 providers/ 静态注册表替代 |
| `tstdx/capability_catalog.py` | v13 | 被 typed_query.py / runtime/typed.py 替代 |
| `tstdx/cache_v2.py` | v12 | 被 cache_semantic.py + cache_persistent.py 替代 |
| `tstdx/semantic_cache.py` | v12 | 被 cache_semantic.py 替代 |
| `tstdx/freshness.py` | v13 | 散落原语，合并到 runtime/ 内部 |
| `tstdx/health.py` | v13 | 散落原语，合并到 runtime/ 内部 |
| `tstdx/failure.py` | v13 | 散落原语，合并到 runtime/ 内部 |
| `tstdx/cache.py` | v12 | 兼容缓存，合并到 cache_semantic.py |
| `tstdx/integration/http_server.py` | v12 | 被 runtime_http.py 替代 |
| `tstdx/integration/http_app.py` | v12 | 过渡层，删除 |
| `tstdx/integration/http_runtime.py` | v12 | 适配器层，删除 |
| `tstdx/integration/ws_server.py` | v12 | 被 runtime_ws.py 替代 |
| `tstdx/integration/ws_app.py` | v12 | 过渡层，删除 |
| `tstdx/cli/cmds_market.py` | v12 | 命令迁移到 runtime_commands.py |
| `tstdx/cli/cmds_web.py` | v12 | 命令迁移到 runtime_commands.py |
| `tstdx/cli/cmds_hosts.py` | v12 | 命令迁移到 runtime_commands.py |

### 2.4 合并/迁移清单

| 来源 | 目标 | 内容 |
|---|---|---|
| `execution.py` 的 `SingleFlight` / `ExecutionBudget` | `batch.py` | v13 执行原语合并到已有的 batch 模块（`batch.py` 已有 `SingleFlight`/`NegativeCache`） |
| `execution.py` 的 `BatchPlan` / `BatchPlanner` | `batch.py` | 批量计划合并到 batch 模块 |
| `planned_service.py` 的 `UnifiedMarketDataService` | `runtime/runtime.py` | 服务逻辑合并到 v14 Runtime |
| `orchestration.py` 的 `FallbackPolicy` | `runtime/` 内部 | 降级策略合并到 v14 编排 |
| `direct_provider.py` 的 `DirectProviderExecutor` | `provider/` | 绑定逻辑合并到 v14 适配器 |
| `capability_catalog.py` | `typed_query.py` / `runtime/typed.py` | 能力目录合并到 Typed Query 体系 |
| `freshness.py` / `health.py` / `failure.py` | `runtime/` 内部 | 散落原语合并到 Runtime 包 |
| `cache_v2.py` / `semantic_cache.py` | `cache_semantic.py` / `cache_persistent.py` | 缓存统一 |
| CLI `cmds_market.py` 的 bars/quotes/count 命令 | `runtime_commands.py` | 命令迁移 |
| CLI `cmds_hosts.py` 的 hosts audit 命令 | `runtime_commands.py` | 命令迁移 |
| CLI `cmds_web.py` 的 web 命令 | `runtime_commands.py` | 命令迁移 |
| `http_server.py` 的 42 端点路由定义 | `runtime_http.py` | 路由迁移到 canonical 表面 |

---

## 3. 执行计划

### Phase 1：消除模块遮蔽（P0 紧急）

**目标**：解决 `runtime.py` vs `runtime/` 和 `execution.py` vs `execution/` 遮蔽冲突

**步骤**：

1. **合并 `execution.py` 到 `batch.py`**
   - 将 `SingleFlight`、`ExecutionBudget`、`BatchPlan`、`BatchPlanner` 从 `execution.py` 合并到 `batch.py`
   - `batch.py` 已有 `SingleFlight`（`__init__.py` 的 `_LAZY` 映射 `SingleFlight -> tstdx.batch`），需确认是否为同一实现
   - 更新 `planned_service.py:34` 的 `from .execution import BatchPlan, BatchPlanner, SingleFlight` → `from .batch import ...`
   - 更新 `failure.py:24` 的 `from .execution import ExecutionBudget` → `from .batch import ...`
   - 删除 `tstdx/execution.py`

2. **合并 `runtime.py`（UnifiedRuntime）到 `runtime/` 包**
   - 将 `UnifiedRuntime` 的核心逻辑迁移到 `runtime/runtime.py` 的 `Runtime` 类
   - 更新 `client_api.py:25` 的 `from .runtime import UnifiedRuntime` → 重写为使用 `from .runtime import Runtime`
   - 更新 `orchestration.py:16` 的 `from .runtime import UnifiedRuntime` → 重写或删除
   - 删除 `tstdx/runtime.py`

3. **验证导入链**
   - `python -c "import tstdx; print('OK')"`
   - `python -c "from tstdx.runtime import Runtime, RuntimeGateway; print('OK')"`
   - `python -c "from tstdx.batch import SingleFlight, BatchPlan; print('OK')"`

### Phase 2：清理 v13 散落根级文件

**目标**：删除或合并 15 个 v13/v12 根级散落文件

**步骤**：

1. **迁移执行原语**（`freshness.py` / `health.py` / `failure.py` → `runtime/`）
   - 在 `runtime/` 包内创建 `primitives.py`，合并散落原语
   - 更新所有导入引用

2. **迁移服务层**（`planned_service.py` / `service.py` / `async_service.py` → `runtime/`）
   - `UnifiedMarketDataService` 的核心逻辑合并到 `Runtime`
   - 删除三个文件

3. **迁移客户端 API**（`client_api.py` / `client_core.py`）
   - `Client` 类的功能已被 `RuntimeGateway` 覆盖
   - 保留 `client_api.py` 作为兼容入口，内部委托 `RuntimeGateway`
   - 删除 `client_core.py`

4. **迁移 Provider 层**（`direct_provider.py` / `provider_api.py`）
   - `DirectProviderExecutor` 合并到 `provider/` 包
   - `provider_api.py` 的 API 类型定义合并到 `providers/`
   - 删除两个文件

5. **迁移缓存层**（`cache_v2.py` / `semantic_cache.py` / `cache.py`）
   - 三个文件合并到 `cache_semantic.py` / `cache_persistent.py`
   - 删除三个文件

6. **迁移能力目录**（`capability_catalog.py`）
   - 合并到 `typed_query.py`
   - 删除文件

7. **删除 `orchestration.py`**
   - `FallbackPolicy` 合并到 `runtime/` 内部

### Phase 3：统一服务面

**目标**：删除 v12 HTTP/WS 服务器，保留 v13 canonical

**步骤**：

1. **迁移 HTTP 路由**
   - 将 `http_server.py` 的 42 端点路由定义迁移到 `runtime_http.py`
   - 内部实现从 `ProviderHttpClient` 切换到 `RuntimeGateway`
   - 删除 `http_server.py`、`http_app.py`、`http_runtime.py`

2. **迁移 WS 处理器**
   - 将 `ws_server.py` 的 JSON-RPC 方法表迁移到 `runtime_ws.py`
   - 删除 `ws_server.py`、`ws_app.py`

3. **更新 `integration/__init__.py`**
   - 导出从 `runtime_http.py` / `runtime_ws.py` / `runtime_ws_server.py`
   - 移除旧模块引用

### Phase 4：统一 CLI

**目标**：将 v12 CLI 命令迁移到 v13 `runtime_commands.py`

**步骤**：

1. **迁移命令**
   - `cmds_market.py` 的 `bars`/`quotes`/`count`/`list` 命令 → `runtime_commands.py`
   - `cmds_hosts.py` 的 `hosts audit`/`server-test` 命令 → `runtime_commands.py`
   - `cmds_web.py` 的 web/ESG/筹码 命令 → `runtime_commands.py`
   - `_common.py` 的工具函数 → `runtime_commands.py` 或保留 `_common.py`

2. **更新 `parser.py`**
   - 添加新的子命令定义（`hosts`、`server-test`、`serve`、`probe` 等）
   - 命令处理函数全部从 `runtime_commands.py` 导入

3. **删除旧文件**
   - `cmds_market.py`、`cmds_web.py`、`cmds_hosts.py`

### Phase 5：Facade 兼容层标注

**目标**：保留 facade/ 但标记为 deprecated，确保桥接 v14

**步骤**：

1. **验证 `facade/runtime_adapter.py`**
   - 确认 `RuntimeFacadeAdapter` 正确桥接到 v14 `Runtime`
   - 确保 `from ..runtime import Runtime` 导入正确

2. **添加 deprecation 警告**
   - `facade/api.py` 的 `UnifiedQuoteAPI.__init__` 添加 `DeprecationWarning`
   - 文档标注推荐使用 `RuntimeGateway`

3. **更新引用方**
   - `integration/runtime_http.py` 等新表面不直接引用 `UnifiedQuoteAPI`
   - 旧表面（如 `http_server.py`）的引用在 Phase 3 删除时自动消除

### Phase 6：导入链验证与测试

**目标**：确保所有导入链正确，测试通过

**步骤**：

1. **全量导入验证**
   ```bash
   python -c "import tstdx; print('OK')"
   python -c "from tstdx import TdxClient; print('OK')"
   python -c "from tstdx.runtime import Runtime, RuntimeGateway; print('OK')"
   python -c "from tstdx.execution import ExecutionPlanner; print('OK')"
   python -c "from tstdx.batch import SingleFlight, BatchPlan; print('OK')"
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

## 4. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 删除文件后导入断裂 | 每个 Phase 完成后运行全量导入验证 |
| Facade 引用方过多 | 保留 facade/ 为兼容层，不立即删除 |
| CLI 命令迁移遗漏 | 对比 v12 命令清单与 v13 命令清单，逐条迁移 |
| 测试失败 | 修复测试导入路径，不降低覆盖率门禁 |
| 合并冲突遗留 | 每个 Phase 独立提交，便于回滚 |

---

## 5. 执行顺序与提交策略

| Phase | 内容 | 预计文件变更 | 提交信息 |
|---|---|---|---|
| P1 | 消除模块遮蔽 | 删除 2 文件，修改 ~10 文件 | `refactor(v15): resolve runtime/execution module shadowing` |
| P2 | 清理 v13 散落文件 | 删除 ~15 文件，修改 ~20 文件 | `refactor(v15): consolidate v13 scattered modules into v14 packages` |
| P3 | 统一服务面 | 删除 5 文件，修改 ~5 文件 | `refactor(v15): unify integration surfaces to v13 canonical` |
| P4 | 统一 CLI | 删除 3-4 文件，修改 2 文件 | `refactor(v15): unify CLI to parser.py + runtime_commands.py` |
| P5 | Facade 兼容层标注 | 修改 ~3 文件 | `refactor(v15): mark facade as deprecated compat layer` |
| P6 | 验证与测试 | 修改测试文件 | `test(v15): fix import paths and verify all gates` |

---

## 6. 验收标准

- [ ] `python -c "import tstdx"` 无错误
- [ ] `python -c "from tstdx.runtime import Runtime, RuntimeGateway"` 无错误
- [ ] `python -c "from tstdx.execution import ExecutionPlanner"` 无错误
- [ ] `python -c "from tstdx.batch import SingleFlight, BatchPlan"` 无错误
- [ ] `ruff check tstdx/` 0 violations
- [ ] `mypy tstdx/` 0 errors
- [ ] `pytest tests/ -x -q` 全部通过
- [ ] `pytest --cov=tstdx --cov-fail-under=77` 通过
- [ ] `tstdx` 根目录 .py 文件数从 30 降到 ~15
- [ ] `integration/` 目录文件数从 14 降到 ~6
- [ ] `cli/` 目录文件数从 7 降到 3-4
- [ ] 无模块遮蔽冲突（`runtime.py` / `execution.py` 已删除）
