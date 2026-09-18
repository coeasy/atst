# tstdx v17 收口方案：单内核最后一公里与债务清偿

> **文档状态**：执行中 —— Phase 3A(方案 b)/3B/3C/3D 已于 2026-09-19 落地（F-1/F-2/F-9 物理
> 删除、防回潮守卫、typed 全契约对齐内核签名 + CHANGELOG 迁移表、根级模块 26→11）；
> Phase 4/5 待续
> **取代文档**：REFACTOR_PLAN_v16_CONVERGENCE.md 的 Phase 3–5（其 Phase 0/1/2 已于
> `528ad18` / `6a45215` / `77bc2fe` 落地）
> **前置决策沿用 v16**：v13 内核唯一执行；`Client` 唯一业务入口；数据请求零缓存；clean-break。

---

## 0. 现状判定（实证，非推断）

### 0.1 主链路贯通状态

| 链路 | 状态 | 证据 |
|---|---|---|
| `Client` → `UnifiedRuntime`（`runtime/kernel.py`）→ `QueryPlanner` → `DirectProviderExecutor`（DIRECT_BINDINGS 精确派发）→ provenance 校验 | ✅ 贯通，零缓存 | `Client()` 冒烟通过；172 个 capability 注册；`tests/v14 tests/runtime tests/query tests/provider_isolation` **全绿** |
| 服务面 CLI / HTTP(`integration/runtime_http.py`) / WS(`runtime_ws.py`) / MCP(`integration/mcp/`) → `Client` | ✅ 贯通 | 全部 import `client_api.Client` |
| v14 信封线 `RuntimeGateway.execute/execute_typed/execute_batch` → `Runtime` → `ExecutionPlanner` → `SemanticExecutionAdapter` → `ProviderRouter` | ❌ **默认不可用**（F-1） | `RuntimeGateway().execute(QueryRequest(bars))` 实测返回 `unsupported operation: bars`；默认 `Runtime()` 的 router 为空 |
| executor binding registry 线（`executor_registry.resolve_executor`） | ❌ **断线**（F-2） | `register_direct_binding` 非测试代码零调用，注册表恒空；相关契约测试**单跑必红**，全绿仅因 pytest 文件序状态泄漏 |

**结论：核心查询功能已实现且唯一主链贯通；v14 编排信封与 registry 三件套是仅剩的两条断链。**

### 0.2 遗留不合理点（Phase 3–5 处理对象）

| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| F-1 | P0 | **第二执行接缝**：`Runtime.execute` 走 `ExecutionPlanner` DAG + `provider/router.ProviderRouter`，与 Client 内核并行；typed/batch 信封全部悬空。**2026-09-19 补充实证：信封层在 `tstdx/runtime/` 包外零生产消费者**（CLI/HTTP/WS/MCP 全直连 Client，仅 13 个测试文件引用）→ 处置新增选项 (b) 整层删除，见 §1-3A 与 §2 决策点 1 | `runtime/runtime.py:43-58`、`execution/semantic.py`、实测见 0.1；引用面 grep 复核 |
| F-2 | P0 | registry 三件套（`executor_bindings.py`/`executor_binding_registry.py`/`executor_registry.py`）为恒空注册表 + 4 个泄漏序依赖的测试；与 `DIRECT_BINDINGS` 构成第三份 binding 三元组 | 单跑红证据见 0.1 |
| F-3 | P1 | `QueryResponse`/`QueryResult` 双结果类型并存，gateway/Runtime 需 `_wrap`/`_unwrap_result` 双份翻译 | `runtime/gateway.py:91-122`、`runtime/runtime.py:180-199` |
| F-4 | P1 | 根级仍散落 28 个模块，命名债未清：`client_api` vs `client_core` vs `client/`；`provider_contract/guard/audit` 游离在 `provider/` 包外；`query.py`/`result.py`/`typed_query.py`/`stream_contract.py` 契约文件平铺 | `ls tstdx/*.py` |
| F-5 | P1 | typed query（60 契约 + Domain Record）无 `Client.typed()` 入口，只能经悬空的 F-1 信封线；端到端糖衣未兑现 | 冒烟：`tstdx.typed_query` 无 REGISTRY 导出，仅 `Runtime.execute_typed` |
| F-6 | P1 | 文档-代码矛盾：README 架构图仍宣传 "v14 编排内核 / 语义缓存 L1/L2 / 5 级降级路由 / UnifiedQuoteAPI 门面层 / sources 路由层"，全部已物理删除；`tstdx/__init__.py` docstring 仍含"语义缓存"设计目标 | `README.md` 架构总览 |
| F-7 | P2 | `trade/` 仅自测引用、未列入 README 能力账（模块自身已声明"独立可选 + 模拟红线"） | `tstdx/trade/__init__.py` docstring |
| F-8 | P2 | `sinks→sink`、`sources` 删除后，`output/`、`profile/`、`feedback/`、`tools/` 归属与门禁未审计；`.venv` 环境要求（本机 `python` 为坏 stub，须用 `uv`/`.venv`）未写入 CONTRIBUTING | 本次调研踩坑 |

---

## 1. 分阶段执行计划

### Phase 3A —— 消灭第二执行接缝（P0，1 个 PR）✅ 已落地（方案 b，2026-09-19）

> **执行记录**：`runtime/{runtime,gateway,bootstrap,request,response,typed,stream,context}.py`、
> `execution/`、`provider/` 全部物理删除；`kernel.py` 增加 `KernelExecutor` 注入口；
> `Client.typed`/`AsyncClient.typed` 接通（F-5 糖衣落地并顺带完成 3D 的 60 契约内核对齐：
> typed 字段名与内核真实签名逐一收敛，`scripts/contract_audit.py` 改走 `QueryPlanner`）；
> 信封测试删除、typed/batch/stream 有效断言迁移至 `Client.typed` + 假 executor 模式；
> 防回潮守卫见 `tests/architecture/test_single_kernel_guards.py`。

> **2026-09-19 修订**：实证信封层零生产消费者后，存在两种走法，**默认推荐 (b)**：
> - **(a) 薄壳改写**：信封保留，内部全部改走 Client（下述 1–6 条按原案执行）；
> - **(b) 整层删除（推荐）**：删除 `runtime/{runtime,gateway,bootstrap,request,response,typed,stream}.py`
>   中的 v14 信封件与 `execution/` DAG、`provider/`（v14 router/adapters）；
>   保留 `runtime/kernel.py`（唯一内核）+ `runtime/context.py`（如仍被引用）；
>   typed 糖衣直接并入 Phase 3D 的 `Client.typed()`；13 个 `tests/v14` 信封测试同步删除，
>   其中有独立价值的断言（stream 生命周期、batch 并发语义）迁移为 Client/内核测试。
>   收益：包规模再减、无第二 API 风格；`Client` 名实相符为唯一入口。

1. `runtime/kernel.py::UnifiedRuntime` 构造增加 `executor` 注入口（默认为
   `DirectProviderExecutor`，测试可注入 Fake），使 Client 成为唯一可注入执行面。
2. `runtime/runtime.py::Runtime.execute` 改为：`QueryRequest → QuerySpec →
   self._client.execute(spec) → QueryResponse`（信封只做字段搬运）；
   `register()` compat-handler 面保留（显式注册的 handler 不是第二内核）；
   `execute_batch` 保留并发编排但逐项走 `execute`；`subscribe` 走 `Client.stream`。
3. `RuntimeGateway` 删除自带 `Runtime()` 构造依赖，`providers` 属性改读
   `providers.PROVIDERS` 注册表；`execute/execute_typed/execute_batch` 全部直通 Client。
4. `execution/`（planner/graph/node/plan/semantic）与 `provider/`（router/base/tdx/web/local）、
   `runtime/bootstrap.py` 的 Provider 注入语义**先冻结摘线**（Runtime 不再 import），
   物理删除随 Phase 3C 一并执行（避免一次提交过大——吸取 `cdc7e2f` 教训）。
5. 测试同步：`tests/v14` 中以 `create_runtime(tdx=...)` 注入假 Provider 的用例改为
   经 `UnifiedRuntime(executor=Fake)` 注入；新增守卫测试锁死：
   **`tstdx.runtime.runtime` 不得 import `tstdx.execution.*` / `tstdx.provider.router`**。
6. 验收：`RuntimeGateway().execute(QueryRequest("bars"...))` 返回与
   `Client.bars(...)` 字段级一致的结果；全量门禁绿。

### Phase 3B —— registry 三件套清理（P0，同 PR 或紧随）✅ 已落地（2026-09-19）

**裁决建议：删除而非接线。** `DIRECT_BINDINGS`（`direct_provider.py:79`）是内核实际使用的
唯一事实源；三件套只是其平行空壳，接线等于给同一三元组引入第三处定义。

1. 删除 `executor_bindings.py`、`executor_binding_registry.py`、`executor_registry.py`
   及 `tests/provider_isolation/test_executor_*` 4 个文件。
2. `tests/architecture/` 新增防回潮守卫：上述模块名不得重新出现。
3. 若项目方坚持"registry 化"方向（决策点 2 选 (a)），替代方案为：`direct_provider` 的
   `_bindings` 字典改由 registry 填充并全量注册 `DIRECT_BINDINGS`，删除本地字典——
   二选一，禁止双轨。

### Phase 3C —— 命名空间整理（P1，1–2 个 PR）✅ 已落地（2026-09-19）

> 执行结果：根级模块由 26 个收敛到 **11 个**；12 个模块纯移动（无合并）、
> 3 个孤儿模块删除、1 个一次性脚本删除。两处偏离原案，理由见"偏差说明"。

**实际映射（旧 → 新）**

| 原案 | 落地 |
|---|---|
| `client_core.py → client/core.py` | ✅ 同名落地 |
| `direct_provider.py → runtime/executor.py` | ✅ |
| `orchestration.py → runtime/orchestration.py` | ✅ |
| `runtime_audit/identity/provenance.py` | ✅ → `runtime/{audit,identity,provenance}.py` |
| `capability_catalog.py / capability_audit.py` | ✅ → 新包 `catalog/{capability,capability_audit}.py` |
| `provider_api.py / provider_contract.py / provider_guard.py / provider_audit.py` | ✅ → `catalog/{provider_bindings,provider_contract,provider_guard,provider_audit}.py` |
| ~~`freshness/health/failure.py → runtime/support.py`~~ | **偏差 1**：改为整体删除（见下） |
| 根级白名单 ≤10 | **偏差 2**：落地 11 项——多出的 `client_api.py` 是唯一业务入口，保留根级可发现性 |

**偏差说明**

1. 原案要"合并同族"为 `runtime/support.py`。执行前实测：`tstdx/freshness.py`（427 行）与
   `tstdx/health.py`（256 行）**全仓零引用**（生产/测试/脚本皆无），`tstdx/failure.py` 仅被
   自身测试引用——即三者是 F-2 同族的"先写契约、永不上线"孤儿。按零缓存/clean-break 原则
   **直接删除**（F-9），而非合并成 800 行无人调用的支持模块。
2. 原案把契约件放进 `provider/`。该包名刚因 v14 router/adapters 被物理删除，且
   `test_single_kernel_guards` 明令禁止 runtime 包引用 `tstdx.provider.*`——复用会制造
   "已删层复活"的假象，故新包命名 `tstdx/catalog/`（纯声明与一致性审计，依赖方向单向：
   `runtime → catalog`，不得反向）。

**守卫与门禁**：`tests/architecture/test_namespace_layout.py`（根级白名单精确相等、
12 个旧路径磁盘不可见且不可导入、新路径可导入）；`test_official_runtime_no_fallback`
的官方运行时文件清单同步更新；mypy 错误数与 `9c99635` 基线**逐条持平**（47，未新增）。
一次性迁移脚本 `scripts/_v16_strip_use_cache.py`（目标模式已应用且路径过期）一并删除。

### Phase 3D —— typed 糖衣端到端（P1，1–2 个 PR）✅ 已落地（2026-09-19）

> 第 1、2 条：`Client.typed` + 全部领域契约的内核编译/校验闭环，`contract_audit --ci` rc=0。
> 第 3 条 F-3 已随 `QueryResponse` 删除而消解。
> 追加收口：60+ 契约字段名对齐内核真实方法签名（clean-break），迁移表见 CHANGELOG
> "Changed（v16 Phase 3D）"；信封删除与 registry 三件套删除见 "Removed（Phase 3A/3B）"。

1. `Client.typed(query) -> TypedQueryResult` / `AsyncClient.typed(query)`：
   `request_from_typed → QuerySpec → Client.execute → records_from_response`。
2. 60 契约逐条离线契约测试（fail-closed 校验 + 假 Provider 往返）；不可达契约当场
   补 binding 或下线——兑现 "Typed = Registry = Binding = Runtime = Test"。
3. F-3：`QueryResponse` 收敛为 `QueryResult` 的响应视图（序列化层），不再独立承载执行语义。

### Phase 4 —— 文档与对外面统一（与 3C/3D 并行）

1. 新增 `docs/ARCHITECTURE.md` 描述当前事实（首版随本方案落库）。
2. README：删 v12/v14 旧宣传（门面层、5 级降级、L1/L2 缓存、sources 路由），
   改为 Client 单入口 + provider-first + 零缓存事实；`trade/` 以"实验性可选模块"入册。
3. `docs/` 归档：v1–v16 方案与状态文档全部移入 `docs/archive/`。
4. 新增文档-代码一致性门禁（README 导出符号 ⇔ `tstdx.__all__`）。
5. CONTRIBUTING 增补：本机 Python 环境用 `uv`（`.venv`），禁止跨 PR "先删后补"；
   contract-first 提交必须附带使测试单跑亦绿的实现（F-2 教训）。

### Phase 5 —— 发布硬化

1. 全量门禁：`pytest` 0 failed（含单跑随机序 `-p no:randomly` 抽检）、ruff 0、mypy 0、
   覆盖率按**有效代码**重新校准基线（Phase 2 净删 1.4 万行后旧基线失真）。
2. 真实网络 smoke（tdx 1 所 + web 1 源 + stream 3 帧）+ CLI/HTTP/MCP 三面各一发 +
   wheel 安装冒烟 → tag `v1.1.0-dev.1`。

---

## 2. 决策点默认值（未收到异议即按此执行）

| # | 决策 | 默认 |
|---|---|---|
| 1 | v14 编排线归宿 | **(b) 整层删除——已于 2026-09-19 执行**：信封/DAG/router 删除，typed 并入 `Client.typed` |
| 2 | executor registry 三件套 | **删除**（`DIRECT_BINDINGS` 唯一事实源）+ 防回潮守卫 |
| 3 | `trade/` | 保留，README 标注 experimental（自设模拟红线，无架构冲突） |
| 4 | 覆盖率门禁 | Phase 2 后重测并按有效代码重新校准数值（不硬凑旧 77%） |
| 5 | 文档形式 | 本 v17 文档 + ARCHITECTURE.md；v1–v16 移 archive；v16 加状态横幅 |
| 6 | `cache.py`(KlineCache) | 已被 Phase 2 直删——追认 |

## 3. 提交策略与风险

| Phase | 提交粒度 | 主要风险 | 缓解 |
|---|---|---|---|
| 3A | 单 PR（摘线 + gateway + 测试改写） | tests/v14 注入面大改 | 先加 executor 注入口再改测试，逐步迁移 |
| 3B | 可并入 3A | 无 | 守卫测试兜底 |
| 3C | 每 3–5 个模块一个 PR | 导入路径风暴 | 每 PR 全量门禁；根级白名单守卫最后一步加 |
| 3D | 按契约批次 2–3 PR | 契约不可达暴露 | fail-closed：当场补 binding 或下线并记录 |
| 4/5 | docs + release | 无 | — |

## 4. 总体验收清单

- [ ] `RuntimeGateway` 任意路径与 `Client` 同 capability 返回字段级一致结果；`tstdx` 内
      仅存一条执行链（Client → kernel → executor）
- [ ] executor registry 三件套不存在，`DIRECT_BINDINGS` 唯一
- [ ] 根级 `.py` ≤ 10；`execution/` DAG 与 `provider/` v14 适配器已物理删除
- [ ] 60 typed 契约端到端或有下线记录；`QueryResponse` 仅为视图
- [ ] README / ARCHITECTURE / `__init__` docstring 与代码零矛盾；旧方案归档
- [ ] 随机测试序无红；全量门禁绿；三面冒烟通过（同 SHA 证据）
