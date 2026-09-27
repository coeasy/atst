# tstdx v16 重构方案：单内核收敛与断链修复

> **文档状态**：Phase 0/1/2 已落地（`528ad18` / `6a45215` / `77bc2fe`）；Phase 3–5 由
> **REFACTOR_PLAN_V17_CLOSURE.md** 接续（含 2026-09-19 实证的新断链 F-1/F-2 与决策默认值）
> **前置决策**（已与项目方确认）：
> 1. **v13 UnifiedRuntime 为唯一执行内核**，v14 runtime/ 收敛为薄编排壳；
> 2. **兼容层彻底删除**，只留必要别名（clean-break）；
> 3. **`Client` 为唯一对外业务入口**（README/导出面/文档同步收敛）；
> 4. **先修复 3 个存量失败测试再动工**，全部按最新版本优化重构，**先打通基础版本（主链路端到端）**；
> 5. **数据请求零缓存**：彻底移除 L1/L2/负缓存/结果缓存与 `use_cache` 参数，每次请求直达绑定的数据源回源执行（2026-09-18 追加决策，最高优先级约束）。
> **取代文档**：REFACTOR_PLAN_v15_CONSOLIDATION.md（其 Phase 0/1/3 已落地，Phase 2/4/5 由本方案接续）

---

## 0. 执行摘要与链路贯通判定

### 0.1 当前主干（main @ 1048b78，2026-09-18）处于断裂状态 ⚠️ P0

9 月 18 日当天两个提交叠加打断了 v13 执行内核，**当前 HEAD 上 `Client()` 无法实例化、`UnifiedRuntime` 无 `execute` 方法**：

| # | 断裂 | 根因 | 证据 |
|---|---|---|---|
| B-1 | UnifiedRuntime 执行方法全部丢失 | `cdc7e2f` "run unified runtime audit on startup" 误删 `tstdx/runtime_v13.py` 285 行，仅剩 `__init__`/`close`（368→85 行） | `git show cdc7e2f -- tstdx/runtime_v13.py`；`dir(UnifiedRuntime)` 只有 `close` |
| B-2 | 构造函数启动即崩 | `tstdx/runtime_audit.py:33` 访问 `report.capabilities`，而 `CapabilityAuditReport`（capability_audit.py:16-18）字段是 `migrated_capabilities`/`executable_bindings` | `Client()` 冒烟直接 `AttributeError`；`pytest tests/runtime -x` 首个用例即挂 |

> 结论：**"核心功能是否全部实现"的答案在 9-16 为"是"（314 个测试文件仅 3 个失败），但 9-18 的 provider-isolation 收尾提交把内核切断了。任何重构动工前必须先回血。**

### 0.2 三代链路贯通判定（以 9-16 基线 + HEAD 现状双视角）

| 链路 | 9-16 状态 | HEAD 状态 |
|---|---|---|
| v13 主线：CLI/MCP/HTTP/WS → `Client` → `UnifiedRuntime` → DirectBinding → Provider → L1/L2/负缓存/SingleFlight → `QueryResult` | ✅ 全贯通（唯一真正执行的内核） | ❌ 断裂（B-1/B-2） |
| v14 编排线：`RuntimeGateway` → `RuntimeFacadeAdapter` → `Runtime` → `SemanticExecutionAdapter` → `LegacyRuntimeBridge` → v13 | 🟡 仅 7 个核心能力可用；迁移能力经 `gateway.__getattr__` **必失败**（legacy_bridge.py:242 拒绝非核心 capability） | ❌ 底层同断 |
| v12 遗留线：`facade.UnifiedQuoteAPI`/`planned_service`/旧 HTTP/WS 服务面 | 🟡 可运行，但仅靠专属测试与 README 续命；v15 Phase 2 未执行 | 不依赖 v13 内核，暂存活 |
| 协议层：codec/protocol/transport/client/reader/web | ✅ 独立完备、不依赖上层 | ✅ 不受影响 |

### 0.3 主要不合理点 Top 10（按严重度）

| 级别 | 问题 | 证据 |
|---|---|---|
| P0 | 内核断链（B-1/B-2），且此类"契约测试先行 + 大删大改"提交没有全量测试门禁兜底 | §0.1 |
| P0 | **双内核双缓存**：v14 路径上 SemanticExecutionAdapter 自带 L1 检查（semantic.py:228-238）又委托 v13 全套 L1/L2/负缓存/SingleFlight——两个缓存键空间、结果可能被缓存两次 | `tstdx/execution/semantic.py` |
| P0 | **v14 私自跨 Provider 回退**：adapter 内建多 provider 顺序回退循环并改写 provenance（semantic.py:195-280），违反 v13 宪法"跨 Provider 回退只能走显式 FallbackPolicy → ProviderOrchestrator"（V13_REFACTOR_EXECUTION_STATUS.md:69-71） | 同上 |
| P0 | **RuntimeGateway 迁移能力断链**：`__getattr__` 把 200+ 迁移能力转给只支持 7 个核心能力的 bridge（gateway.py:256-268 → legacy_bridge.py:242），静默能力回归 | 见左 |
| P0 | 三个公开业务入口并存（`Client` / `RuntimeGateway` / `facade.UnifiedQuoteAPI`），README 同时宣传；而 V13 状态文档宣称 UnifiedQuoteAPI 已删除——**文档与代码互相矛盾** | README.md 架构总览 vs `tstdx/facade/__init__.py` |
| P1 | 重复原语：`SingleFlight`/`NegativeCache` 两份实现（batch.py:314,354 vs execution_primitives.py:195）；`execution/primitives.py` 再转发一层 | grep 计数 |
| P1 | v12 服务面 8 个旧文件（http_server/http_app/http_runtime/ws_server/ws_app/tasks/mcp_app/mcp_server）仅被专属测试保命；`mcp_app.py` 在非测试代码中零引用（孤儿） | 导入分析 |
| P1 | 缓存 5 模块并存：`cache.py`(KlineCache,v12 原始层)/`cache_v2.py`/`semantic_cache.py`/`cache_semantic.py`(v13 L1)/`cache_persistent.py`(v13 L2)；其中 cache_v2、semantic_cache 是 v12 死债 | 导入分析 |
| P1 | 根级 30 个散落文件命名混淆（`client_api` vs `client_core` vs `client/` vs `facade/`；`provider/` vs `providers/` vs `provider_api.py` vs `direct_provider.py`）；facade/api.py 单文件 2603 行 | wc -l 排序 |
| P2 | 3 个存量失败测试（bestip 池健康语义 ×2、feedback 禁用 noop 契约 ×1）；sinks/ 更名 shim 逾期未删；native.py 空壳承诺 v1.6.0 删；版本语义三套混用（包 1.0.0 / 方案 v15 / 注释"v10 删除"） | `__tmp_fulltest.txt`、各文件 docstring |

---

## 1. 目标架构

### 1.1 收敛后唯一主链

```
                 ┌────────────────────────────────────────────┐
 对外入口（唯一）  │  tstdx.Client / tstdx.AsyncClient           │
                 │  （typed_query 60 契约 + domain records      │
                 │    作为 Client 之上的类型化糖衣）              │
                 └──────────────────┬─────────────────────────┘
                                    │ QuerySpec / capability 名
                 ┌──────────────────▼─────────────────────────┐
 唯一执行内核     │  v13 UnifiedRuntime（零缓存）              │
                 │  QueryPlanner → DirectBinding → Provider    │
                 │  每次请求直达绑定 Provider 回源执行          │
                 │  provider-first：单 Plan 永不私选第二 Provider │
                 │  跨 Provider 回退 = 显式 FallbackPolicy       │
                 │    → ProviderOrchestrator（唯一）            │
                 └──────────────────┬─────────────────────────┘
                                    │
      ┌──────────┬──────────┬───────┴────┬───────────┐
   tdx Provider  web     local_vipdoc  derived    （注册表 providers/）
      │           │           │
  protocol/ + client/ + transport/ + web/ + reader/   ← 协议层，冻结不动
```

**薄壳服务面（全部只做翻译，禁止第二套路由/缓存/provenance）**：

| 服务面 | 收敛后实现 | 委托到 |
|---|---|---|
| HTTP REST | `integration/runtime_http.py`（唯一） | Client |
| WS JSON-RPC | `integration/runtime_ws.py` + `runtime_ws_server.py` | Client |
| MCP | `integration/mcp/`（已是 Client-backed，保留） | Client |
| CLI | `cli/parser.py` + `cli/runtime_commands.py`（已是 Client-backed，保留） | Client |
| v14 Runtime/ | 收敛为 `runtime/` 薄层：Typed Query 编译、execute_batch、stream 编排句柄 | Client / UnifiedRuntime |

### 1.2 v14 资产的处置原则（保留什么、砍什么）

| v14 资产 | 处置 | 理由 |
|---|---|---|
| `runtime/gateway.py` RuntimeGateway | **重写为 Client 的别名壳**：所有方法直通 `Client.call`/Client 方法，删除自带 bridge 与 `_adapter` 双路 | 消除双内核；QueryResponse 保留为 Client 结果的响应式包装（success/data/error 视图），不再独立执行 |
| `execution/semantic.py` 自带 cache 检查 + provider 回退循环 | **删除这两段**，adapter 退化为 QueryRequest→QuerySpec 翻译器 | 缓存/路由唯一归 v13 |
| `execution/planner.py·graph.py·node.py` DAG | **保留但冻结**（标记 experimental），Phase 7 优化器（CSE/并行）不再投入，仅 `execute_batch` 保留（语义 = 逐项 compile + v13 SingleFlight 合并） | 当前无收益的中间层，但删掉破坏面大；冻结成本最低 |
| `runtime/stream.py` StreamHandle + `streaming/` | 保留，回源统一走 `Client.stream`（StatefulQuoteStream） | 已是编排+生命周期定位 |
| `typed_query.py` 60 契约 + `domain/records.py` | 保留并接通：`Client.typed(query) -> TypedQueryResult`，内部 `request_from_typed → QuerySpec → Client.execute → records_from_response` | 领域化糖衣是 v14 最有价值的对外资产 |
| `provider/` 动态适配器 + `providers/` 静态注册表 | 合并为一个 `provider/` 包（`providers/__init__` 注册表数据并入），根级 `provider_api.py`、`direct_provider.py`、`providers/adapter.py` 迁入 | 消除三处 Provider 命名 |

### 1.3 彻底删除清单（clean-break，含专属测试同步删除）

| 删除对象 | 文件 | 波及测试 | 必要别名保留 |
|---|---|---|---|
| v12/v13 缓存全组（**追加决策 5**） | `cache_semantic.py`(L1)、`cache_persistent.py`(L2)、`semantic_cache.py`、`cache_v2.py`、`cache.py`(KlineCache)、`runtime_cache_adapter.py`、`runtime_cache_policy.py`；`batch.py` 的 `NegativeCache`/`SingleFlight` 与 `execution_primitives.py` 对应实现 | 一切断言缓存命中/提升/负缓存/singleflight 的测试（tests/runtime、tests/provider_isolation 缓存边界族、test_c5_quote_cache 等） | 无——`use_cache`/`cache_ttl`/`persistent_*` 参数从 Client/Runtime/服务面签名整体移除 |
| v12 facade 全组 | `facade/`（13 文件）、`service.py`、`async_service.py`、`planned_service.py` | tests/facade(约 20 文件)、tests/service、部分 integration | 无（README 移除整节） |
| v12 旧服务面 | `integration/http_server.py`、`http_app.py`、`http_runtime.py`、`ws_server.py`、`ws_app.py`、`tasks.py`、`mcp_app.py`、`mcp_server.py` | tests/integration 中对应 _f4/_v12/planned_taskstore/body_envelope/notification_isolation 等 | 无；`RuntimeTaskStore` 已覆盖任务面 |
| v12 缓存 | `cache_v2.py`、`semantic_cache.py` | tests/test_cache.py 相应类 | 无 |
| 更名 shim | `sinks/`（v9 更名债，逾期） | 引用它的 3 处测试 | 无 |
| native 空壳 | `native.py`（承诺 v1.6.0 删除，直接兑现） | tests/unit/test_native.py | 无 |
| 重复原语 | `execution_primitives.py` 独立实现 | 导入方改指 `batch.py`（唯一 SingleFlight/NegativeCache/BatchPlanner 之家）；`execution/primitives.py` 改 re-export batch | 保留 `tstdx.execution.SingleFlight` 导入路径兼容 |
| 保留但迁移 | `cache.py::KlineCache`（sources/ 依赖的原始层缓存）→ `reader/cache.py`；`freshness/health/failure` → `runtime/support.py` 或独立 `runtime/` 子模块；`client_core.py` → `client/core.py`；`orchestration.py` → `runtime/orchestration.py`；`provider_api.py` → `provider/api.py` | 全量导入路径更新 | 顶层旧导入路径**不保留**（clean-break），CHANGELOG 记迁移表 |

> KlineCache 不并入 SemanticResultCache：两层语义不同（原始 bar 数据 vs 结果语义缓存），只换地址不换实现。

---

## 2. 分阶段执行计划

### Phase 0 —— 紧急止血：恢复内核 + 修复存量失败（1 个 PR，今天完成）

**目标**：main 恢复到"基础版本主链路端到端打通"，这是后续一切重构的前置门禁。

1. 恢复 `cdc7e2f` 误删的 285 行：`git show dbbeae4:tstdx/runtime_v13.py` 取回执行方法（execute/quotes/bars/snapshot/minute/trades/security_count/security_list/quotes_batch/_promote_l2 等），与今日 provider-isolation 改动（audit 调用、identity 集成）手工合并——**只回滚删除，不回滚功能**。
2. 修 `tstdx/runtime_audit.py:33`：`report.capabilities` → `report.migrated_capabilities`。
3. 修 3 个存量失败测试（先定性再修，不盲改）：
   - `tests/client/test_bestip.py::test_refreshes_probe_metrics_without_overwriting_live_health` 与 `test_async_bestip_cancellation.py` 同族：`ConnectionPool.update_hosts` 的"probe 观测不得覆盖 live 健康"语义与 `_pool_binding_hardening` 重构后行为漂移——以测试文档字符串声明的语义为准修 `transport/pool.py`。
   - `tests/test_feedback_payload_contract.py::test_disabled_usage_feedback_remains_noop_before_payload_validation`：`FeedbackReporter.report_usage` 在 disabled 分支先建目录后校验——调整为校验/短路在前的顺序（feedback/reporter.py）。
4. 验收（全绿才允许合入）：
   - `python -c "from tstdx.client_api import Client; Client().bars('sh600519', count=5)"`（可 mock provider 的离线等价冒烟）；
   - `pytest tests/ -q` 全量 0 failed；`ruff check` 0；`mypy tstdx/` 0；`--cov-fail-under=77`。
5. **流程门禁补强（防复发）**：本阶段在 CI/Makefile 增加"HEAD 提交前必跑全量离线 pytest"的 pre-push 检查说明；`CONTRIBUTING.md` 明确：contract-test-first 的提交必须同一 PR 内附实现，禁止"先删后补"跨 PR 断链。

### Phase 1 —— 单内核化：消灭双缓存/双路由（Bridge 拆除，1-2 个 PR）

1. `execution/semantic.py`：删除自带 `self.cache.get` 分支与 provider 循环回退，退化为 `QueryRequest → QuerySpec` 翻译 + 单 provider 委托 `UnifiedRuntime.execute`。
2. `runtime/gateway.py`：重写为持有 `Client` 的薄壳；`call`/`__getattr__`/`execute_with_policy` 全部直通 Client（同时修复迁移能力断链，见 1.2）；删除 `RuntimeFacadeAdapter` 中重复的路由逻辑（`facade/runtime_adapter.py` 随 facade 组在 Phase 2 删除，本阶段先把 gateway 依赖摘干净）。
3. `runtime/bootstrap.py::create_runtime`：改为构造 `Client` 并暴露 `runtime` 引用；provider 注入语义不变。
4. 合并 SingleFlight/NegativeCache 到 `batch.py` 唯一实现：`execution_primitives.py` 清空为 `from .batch import ...` 的过渡模块 → 下个 Phase 删除；更新 `failure.py`、`planned_service`（若尚存）等导入。
5. 契约测试同步：`tests/v14/*` 中"bridge L2 提升/singleflight 合并"断言改为断言**委托关系**（gateway 调用 Client，无第二缓存实例），锁定"网关禁止实现独立 Provider 选择/回退/缓存/provenance"。
6. 验收：gateway 路径与 Client 路径对同一 capability 返回**同一** `QueryResult`（字段级相等）；`RuntimeGateway.<任意迁移能力>` 可调用；全量门禁绿。

### Phase 2 —— v12 大清除（facade/service/旧服务面/旧缓存，1 个 PR）

1. 按 §1.3 删除清单执行（源码 + 专属测试 + `tstdx/__init__.py`、`docs/api`、README 引用同步清除）。
2. 迁移残余引用：`cli/runtime_commands.py` 对 facade 的引用改指 Client；`sink/local_day.py` 同理；`domain/symbol.py` 只清注释。
3. 删除 `runtime_v13.py` 过渡壳：其内容（UnifiedRuntime）升格为 `runtime/kernel.py`，`tstdx.runtime.UnifiedRuntime` 导出；同步消除 `runtime.py/runtime_v13.py/runtime/` 命名债（历史三轮遮蔽事故源头）。
4. `execution_primitives.py`、`sinks/`、`native.py` 物理删除；`tstdx/execution/__init__.py` 直接 re-export `tstdx.batch` 原语。
5. 验收：`grep -rn "facade\|http_server\|ws_app\|cache_v2\|semantic_cache\|native" tstdx --include=*.py` 零残留；全量门禁绿；`import tstdx` 冷启动无 DeprecationWarning（v12 时代的）。

### Phase 3 —— 命名空间整理 + typed/domain 糖衣接通（1-2 个 PR）

1. §1.3"保留但迁移"表落地：根级 30 个散落文件归位，目标根级只保留 `__init__.py`、`__main__.py`、`errors.py`、`error_envelope.py`、`deprecation.py` 与包目录。
2. Provider 三合一：`providers/` 注册表数据 + `provider/` 适配器 + `provider_api.py` 类型 → 单一 `tstdx/provider/`（`registry.py / api.py / base.py / tdx.py / web.py / local.py / derived.py / guard.py`）。
3. `Client.typed(query)` + `AsyncClient.typed(query)`：60 个 Typed Query 全量走通 `编译→执行→Domain Record` 端到端，为每个契约补 1 条离线契约测试（fail-closed 校验 + 假 Provider 往返）；不达标的契约（无法编译或无法执行）当场补齐 binding 或下线该契约——兑现 "Typed Query = Registry = Binding = Runtime = Surface = Test" 验收标准。
4. 删除 `QueryResponse`/`QueryResult` 双类型：统一 `QueryResult`；gateway/HTTP/WS 序列化层做视图转换。
5. 验收：全量门禁 + 模块可达性审计（零孤儿）+ `tests/architecture/` 增加"根级白名单"守卫测试（防散落文件回潮）。

### Phase 4 —— 文档与对外面统一（与 Phase 3 并行）

1. README 重写：单一入口 Client、删除 v14 Runtime 顶层宣传（改为"类型化查询 + 领域模型"章节）、5 级降级路由等 v12 话术校准为 v13 provider-first 事实。
2. `docs/`：v1–v16 方案与状态文档全部移入 `docs/archive/`，仅保留一份 `docs/ARCHITECTURE.md`（当前事实）+ `docs/CHANGELOG 迁移表`（旧导入路径 → 新路径）。
3. `V13_REFACTOR_EXECUTION_STATUS.md` 的"已删除"声明与代码对齐（Phase 2 后即可成立），并归档。
4. 新增文档-代码一致性门禁：`tests/architecture/test_docs_consistency.py`（README 宣称的导出符号必须在 `tstdx.__all__` 存在；反之亦然）。
5. 版本号规则统一：包语义版本（1.x）为弃用窗口唯一时钟；文档方案编号不再出现在代码注释的删除承诺里。

### Phase 5 —— 发布硬化与端到端验证

1. 全部门禁（同 Phase 0.4）+ 真实网络 smoke（tdx 主站 1 所 + web 1 源 + cache 命中/回源 + stream 3 帧）手动记录。
2. wheel/sdist 安装冒烟；`tstdx query bars` CLI、`/v13/query/{cap}` HTTP、MCP `query_capability` 三面各打一发。
3. 打 tag `v1.1.0-dev.1`，PR 离开 Draft 条件按 GOVERNANCE.md 同 SHA 全证据执行。

## 3. 提交策略与回滚

| Phase | 提交 | 回滚方式 |
|---|---|---|
| P0 | `fix(runtime): restore UnifiedRuntime execution methods dropped by cdc7e2f` + `fix(transport,feedback): resolve 3 stale-gate failures` | 纯修复，无需回滚 |
| P1 | `refactor(runtime): single-kernel — gateway delegates to Client, drop dual cache/fallback` | 单 PR revert 即回 |
| P2 | `refactor!: remove v12 facade/service/legacy integration surfaces and caches` | 单 PR revert 即回 |
| P3 | `refactor: namespace consolidation + typed query end-to-end`（可按子项拆 2-3 PR） | 逐 PR revert |
| P4/P5 | docs + release | 无风险 |

每 Phase 独立 PR、独立全量门禁；Phase 间禁止交叉引用未合并改动（吸取 cdc7e2f 教训：一次提交只做一件事）。

## 4. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Phase 0 恢复的 285 行与 provider-isolation 新原语（identity/policy/provenance）合并出错 | 以 `dbbeae4` 版本为底逐方法搬运，`tests/provider_isolation/` 全绿为验收 |
| 删 facade 连带 ~70 个测试文件，覆盖率跌破 77 | 删除清单里"测试与源码成对删除"，覆盖率先测后调，必要时把门禁基线声明为"有效代码覆盖率" |
| `RuntimeGateway` 行为改变影响已写 v14 契约测试 | Phase 1 同步重写 `tests/v14/` 断言为委托关系断言，不留兼容双路 |
| 外部（若已有）用户依赖 facade | 项目定位 Draft 1.0.0 且刚发布 v1.0.0；CHANGELOG 迁移表 + 一次 `DeprecationWarning` 不保留（clean-break 决策），发布说明中显著公告 |
| UnifiedRuntime 升格 `runtime/kernel.py` 再次引发遮蔽 | 物理删除根级 `runtime_v13.py`，CI 加根级白名单守卫（Phase 3.4） |

## 5. 总体验收清单

- [ ] `Client()` 可实例化；bars/quotes/call(任意迁移能力) 离线契约全绿
- [ ] 全仓只剩**一个** SingleFlight、一套语义缓存栈（L1+L2+negative，均在 v13 内核内）、一条跨 Provider 回退通道（显式 FallbackPolicy）
- [ ] `tstdx.facade`、`service.py`、`http_server/ws_app/cache_v2/semantic_cache/sinks/native` 不存在
- [ ] 根级散落文件 ≤ 6；`tstdx/provider/` 唯一
- [ ] 60 个 Typed Query 全部端到端（编译+执行+Record 归一）或有明确下线记录
- [ ] README/docs 与代码零矛盾，旧方案归档
- [ ] ruff 0 / mypy 0 / pytest 0 failed / cov ≥ 77 / 三服务面冒烟通过（同 SHA 证据）
