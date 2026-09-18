# tstdx v17 收口方案：单内核最后一公里与债务清偿

> **文档状态**：执行中 —— Phase 3A(方案 b)/3B/3C/3D/4 已于 2026-09-19 落地（F-1/F-2/F-9 物理
> 删除、防回潮守卫、typed 全契约对齐内核签名 + CHANGELOG 迁移表、根级模块 26→11、
> 文档面对齐代码事实 + 文档-代码一致性门禁，见 F-10/F-11）；Phase 5 第 1 步已落地
> （mypy 47→0、F-12 死守卫修复、缓存时代残留清除），第 2 步已落地（65 文件全量 `ruff
> format` 纯格式提交清零、dev 工具版本钉死，见 F-15）；**Phase 6（配置面接线，F-16 曾为
> P0 发布阻塞项）已于 2026-09-19 落地**：`Client()` 现在真的读 `tstdx.toml`/`TSTDX_*`，
> 配置面从 12 段收缩为 5 段且每键都有读者（ADR-016）。其后才是网络 smoke 与 tag
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

### 0.3 Phase 4/5 执行期实测新发现（按严重度）

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| F-12 | **P0** | `Prober.only_offline_hours()` 比较 `SessionState.IN_SESSION`——该成员**从不存在**（真实成员为 `call_auction/continuous/noon_break/closed`）。任何未打桩的调用必抛 `AttributeError`，即盘中探测保护一直是死代码；因所有测试都 monkeypatch 掉该方法，全绿从未暴露 | **已修**（2026-09-19）：改判 `state not in (CALL_AUCTION, CONTINUOUS)`，补 `tests/protocol/test_prober_offline_guard.py` 逐时段回归（含周末与 `_guard_offline` 抛错路径） |
| F-13 | P1 | **配置面大面积装饰化**（Phase 5 实测复核）。`tstdx/config/schema.py` 的 12 个段中，`cache`/`output`/`profile`/`sources`/`observability`/`compatibility`/`feedback` 七个段的 dataclass 在 `config/` 包外**零引用**（`CacheConfig`/`CompatibilityConfig` 亦仅被 `config/__init__.py` 再导出）。`[cache]` 段更与"数据请求零缓存"直接冲突 | **已清偿**（2026-09-19，Phase 6）：七段连同 dataclass 与再导出物理删除（`schema.py` −320/+45 行），loader 对未知段/字段/环境变量一律 fail-closed 并在消息里给出当前有效段清单 |
| F-16 | **P0** | **配置系统与产品链路未接线**（Phase 5 实测）：`load_config` 在 `tstdx/` 包内**零调用者**，CLI/HTTP/WS/MCP/`Client` 全都不读配置文件；`Client.__init__` 只接受 `runtime`/`**runtime_kwargs`，没有 `config=` 入口。`Config` 唯一进入运行期的路径是调用方自己构造后传给 `ConnectionPool.from_config`（`pool.py:242` 读 `cfg.rate_limit`，`core/hosts/security` 同族）——即"写 `tstdx.toml` 不生效"。而 `docs/troubleshooting.md` 长期指导用户"尝试 80/443 端口主站（配置 `tstdx.toml`）"（Phase 5 已改为显式 `Client(hosts=[...])` 并就地标注未接线）| **已清偿**（2026-09-19，Phase 6，方案 a）：`UnifiedRuntime` 成为唯一读者并缺省经 `get_config()` 取六源合并单例，保留的 5 段全部键逐个贯通到 `TdxClient`/`ConnectionPool`/`WebQuoteClient`；配置→传输只有 `pool_settings_from_config` 一个翻译点。**接线时实测出的加重情节**：被删除的第二读者 `ConnectionPool.from_config` 读的键名（`rate_call_auction`…）与 `RateLimitConfig` 字段名（`in_session`/`pre_post`/`closed`）从不重合，`getattr(..., 默认)` 把所有配置值静默丢弃为限流器默认值，而它的契约测试正是照幻影键名写的 ⇒ 全绿掩盖。口径见 ADR-016 |
| F-17 | P2 | 接线时新暴露的两处静默失效（Phase 6）：`tstdx.configure()` 合并后**丢弃返回值**、从不写回单例（调用即无效果）；`WebQuoteClient.__init__` 用 `try/except Exception: pass` 包裹配置读取，配置出错即悄悄退回硬编码默认 | **已修**（2026-09-19）：`configure()` 改为 `load_config(overrides=…, set_global=True)` 并如实记录语义；`WebQuoteClient` 直接 `get_config()`，配置解析失败 fail-closed |
| F-14 | P2 | CHANGELOG `[Unreleased]` 的 P13/P14 条目仍以已删除的 `UnifiedQuoteAPI` 门面为"暴露面"叙述；新工具未纳入 `test_doc_code_consistency.py` 的活文档集合（CHANGELOG 不在集合内） | **已清偿**（2026-09-19）：`### Added` 顶部加"当时口径 vs 现行入口"标注（门面已随 v16 Phase 2 物理删除，照抄即 `ImportError`；能力全部存活于 catalog，入口 `Client.call(<capability>, ...)`），4 处"门面暴露 N 个方法"改写为 catalog 事实；条目点名的 46 个 capability 逐个对运行期 `Client().capabilities()`（172 项）核验存在，无一失配。`[1.0.0]` 及更早版本段属既成发布史，保留原口径 |
| F-15 | P1 | 格式门禁长期为红：`ruff format --check tstdx/ tests/ scripts/` 在 0.9.6 与 0.14.4 下均报 74 个文件待重排，而 CI 用浮动的 `ruff>=0.5` | **格式与版本已清偿**（2026-09-19，Phase 5 第 2 步）：65 个待重排文件一次纯格式提交清零，重排前后 `ast.dump()` 逐个比对无差异；dev 依赖钉死 `ruff==0.15.2` / `mypy==2.3.1`。**覆盖率部分仍待办**：阈值数字已收敛为 `pyproject.toml [tool.coverage.report] fail_under` 单一事实源（删 Makefile/CI 的 `--cov-fail-under` 副本并加守卫测试），**未下调阈值**；离线实测 76.14% 仍低于 77，重钉待 CI 环境（ubuntu+py3.11）数字 |

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

### Phase 4 —— 文档与对外面统一（与 3C/3D 并行）✅ 已落地（2026-09-19）

1. ✅ 新增 `docs/ARCHITECTURE.md` 描述当前事实（首版随本方案落库）。
2. ✅ README：删 v12/v14 旧宣传（门面层、5 级降级、L1/L2 缓存、sources 路由），
   改为 Client 单入口 + provider-first + 零缓存事实；`trade/` 以"实验性可选模块"入册。
3. ✅ `docs/` 归档：v1–v16 方案与状态文档全部移入 `docs/archive/plans/`（30 份）。
4. ✅ 新增文档-代码一致性门禁 `tests/architecture/test_doc_code_consistency.py`：
   比原计划更强——不止校验 `__all__`，还解析活文档代码块内全部 `from tstdx… import …`、
   事实型文档反引号中的 `tstdx.*` 路径，并核对 README 的 5 个动作数（CLI/HTTP/MCP/
   capability/Provider）与运行期事实一致。
5. ✅ CONTRIBUTING 增补：本机 Python 环境用 `uv`（`.venv`）+ `-X utf8`；禁止跨 PR
   "先删后补"；contract-first 提交必须附带使测试单跑亦绿的实现（F-2 教训）；
   "文档即门禁对象"。

落地时暴露并修复的新事实（记入 F-10/F-11）：

- **F-10 文档面系统性失真**：`from tstdx import TdxClient` 在 14 处文档/脚本中不成立
  （根面自 v15 起不再导出协议客户端），其中 `ops/smoke_30d.py` 是**运行期 ImportError**；
  落地 sink URI（`output://`、`parquet://`、`duckdb://…?table=`）整套失效；
  `read_lc1_file`/`read_lc5_file`、`adjust_bars`、`domain.records` 的类名清单均为幻影；
  `docs/api/interfaces.md` §2–§4 与 `docs/cookbook/02,07` 整节描述已删除层。
  处置：逐项核对更正 + 上述门禁防回潮。
- **F-11 命名债残留**：`tstdx/web/_facade_mixin_*.py`（11 个文件）与 `tstdx/web/facade.py`
  仍以已删除的"门面层"命名。当前语义是 Web Provider 的会话/适配器分组，不影响契约，
  但名字误导。**后续小 PR**：`_facade_mixin_*` → `_session_*`（或按域命名），
  与 `WebQuoteSession` 的实际角色对齐；纯改名、无行为变化。
- 根级公开面回归单一事实源：`tstdx.__all__` ⇔ `tstdx._LAZY`（46 项）；
  删除 7 个只挂在惰性表、无 `__all__` 条目亦无调用方的根名字。

### Phase 5 —— 发布硬化

1. 全量门禁：`pytest` 0 failed（含单跑随机序 `-p no:randomly` 抽检）、ruff 0、mypy 0、
   覆盖率按**有效代码**重新校准基线（Phase 2 净删 1.4 万行后旧基线失真）。
   **执行记录（2026-09-19）**：
   - 离线全量 `-p no:randomly`：0 failed；`ruff check tstdx/ tests/ scripts/`：0。
   - **mypy 47 → 0**（`mypy tstdx/ --ignore-missing-imports --warn-unused-ignores`）。
     其中 14 处是真实缺陷或坏味道：`Prober.only_offline_hours` 引用不存在的
     `SessionState.IN_SESSION`（F-12，运行期必抛 `AttributeError`）、
     `runtime/identity.py` 仍以 `plan: object` 掩盖缓存时代残留的 `RuntimeCacheIdentity`
     （零生产消费者，已连同 2 个自测文件删除/改写）、`golden_audit`/`speedtest`/
     `streaming.base`/`_pool_provenance_hardening` 四处**同名变量复用**导致 mypy 把
     Optional 与非 Optional 合并成同一声明类型（局部重命名即解，无运行期行为变化）、
     `integration/runtime_http` 健康端点伸手拿 `executor._bindings`
     私有属性（改读 `DIRECT_BINDINGS` 事实源）、`config.schema` 的
     `getattr(self, name)` 循环缺 `_Validatable` Protocol 锚点。
     其余 37 处集中在 11 个 `*_hardening.py` 运行期打桩尾部（`Class.method = wrapper`），
     逐行 `# type: ignore[method-assign|attr-defined]` 标注——**不做整模块豁免**，
     因为 `--warn-unused-ignores` 会把失效标注退回成红灯，桩层解散时标注同批消失。
   - 覆盖率：**离线实测 76.18%**（23356 stmt / 4885 miss，Windows+py3.12），
     低于 Makefile/CI 共用的 `--cov-fail-under=77` ⇒ 该门禁当前为红。
     **不在本地下调阈值**：先删死面（F-13 死配置段）再按 CI 环境（ubuntu+py3.11）
     的实测值重钉单一事实源（把数值收敛到 `pyproject.toml [tool.coverage.report]
     fail_under`，Makefile/CI 不再各写一份），避免本地数字误伤跨平台差异。
2. ✅ **`ruff format` 漂移清偿（Phase 4 实测新发现，见 F-15）**（2026-09-19，Phase 5
   第 2 步）：HEAD 既有 74 个待重排文件，Phase 6 删改触及后净减至 **65**。本次以
   `ruff 0.15.2` 全量重排，并把工具版本钉进 dev 依赖：
   - **纯格式提交与功能提交分离**：格式提交只含 65 个 `.py`（+665/−396），不夹带任何
     语义改动；类型修复提交（第 1 步）此前刻意未含格式重排，diff 保持可审。
   - **语义不变已实证**：对 432 个受跟踪文件建快照，逐个比较 65 个改动文件重排前后的
     `ast.dump()` ⇒ `offenders: none`（无一处 AST 差异）。
   - **版本钉死**：dev extras 从 `ruff>=0.5`/`mypy>=1.10` 改为 `ruff==0.15.2`/
     `mypy==2.3.1`（实测通过门禁的版本）。`ruff format` 的重排结果与 mypy 的错误集均
     版本敏感，浮动区间意味着任何一次 `pip install -U` 都能让门禁无故变红或变绿。
   - **门禁复测**：`ruff format --check .` → 432 files already formatted；
     `ruff check .` → All checks passed；`mypy tstdx/` → 0；
     离线全量 `-m "not network"` → 0 failed / RC=0。
3. 真实网络 smoke（tdx 1 所 + web 1 源 + stream 3 帧）+ CLI/HTTP/MCP 三面各一发 +
   wheel 安装冒烟 → tag `v1.1.0-dev.1`。**延后到 Phase 6 之后执行**（见下）。

### Phase 6 —— 配置面接线与死面清偿（F-13/F-16，发布 v1.1.0 前必须完成）✅ 已落地（2026-09-19）

> **为什么插到发布之前**：F-16 是 P0 口径缺陷——对外声称支持 `tstdx.toml` 配置，
> 实际链路零生效。带着它打 tag 等于把假承诺固化进发布说明。

1. ✅ **接线（决策点 7 = 方案 a）**：`UnifiedRuntime.__init__` 增 `config: Config | None`
   与 `default_provider/timeout/hosts/vipdoc_root` 的 `| None` 化，缺省取
   `get_config()`；`Client(**runtime_kwargs)` 因此天然获得 `Client(config=...)` 入口。
   贯通键：`core.default_provider → QueryPlanner`、`core.timeout/max_retries/
   heartbeat_interval → TdxClient/ConnectionPool`、`core.vipdoc_root → 执行器
   （`adjusted_bars`/`sync_daily`/`local_vipdoc`）`、`hosts.servers → 执行器 hosts`、
   `hosts.slots_per_host`、`rate_limit.* → SessionRateLimiter`、`security.use_tls`、
   `web.* → WebQuoteClient`。端到端回归：`tests/runtime/test_kernel_config_wiring.py`
   （写 TOML → 内核/传输层参数一致、env 覆盖文件、显式入参覆盖配置、注入 executor
   时内核仍持有 config 以供溯源）。
2. ✅ **死面删除**：`cache`/`output`/`profile`/`sources`/`observability`/`compatibility`/
   `feedback` 七段 dataclass、`_SUBCONFIGS` 条目、`config/__init__.py` 再导出全部物理
   删除（`schema.py` −320/+45 行）；未知段/字段/环境变量 fail-closed 且消息含当前有效段
   清单。同批删除第二个配置读者
   `ConnectionPool.from_config` 与其硬化层 `transport/_pool_factory_hardening.py`（76 行）
   及 2 个幻影键契约测试；发布 wheel 冒烟改判"唯一 seam 存在 + `from_config` 不存在"。
3. ✅ **门禁复测**：`ruff check` 0、`mypy tstdx/` 0、离线全量 `-m "not network"` 0 failed；
   覆盖率实测 **76.14%**（Windows+py3.12）。阈值数字已收敛为 `pyproject` 单源
   （Makefile/CI 的 `--cov-fail-under` 副本删除，`test_local_gate_contract.py` 与
   `test_ci_workflow_contracts.py` 双向锁定），**阈值一次都没下调**。
   ⏳ 未完成：按 CI 环境（ubuntu+py3.11）实测值重钉覆盖率数字——本机 Windows 值不作依据。
   （同批挂账的 dev 工具版本钉死与全量 `ruff format` 纯格式提交已在 Phase 5 第 2 步落地。）
4. ✅ **文档同步**：新增用户面 [docs/configuration.md](configuration.md)（5 段全键清单 +
   读取方 + 取值范围 + fail-closed 语义 + 环境变量规则），已纳入事实型文档门禁
   （`FACT_DOC_PATHS`）；`docs/troubleshooting.md` 的"配置文件尚未接入"改写为可用指令；
   `docs/ARCHITECTURE.md` §4 第 7 条转"已清偿"、§5 真相源补两行、§2 不变量加一条；
   README 门禁口径两行更正（并顺手把 mypy 行从"47 项待清零"改回既成事实）；
   新增 [ADR-016](adr/ADR-016-config-surface-covers-execution-only.md)
   「配置面只覆盖执行参数」。
5. 接线过程中顺手修复的静默失效（记入 F-17）：`tstdx.configure()` 调用即无效果
   （合并结果被丢弃、不写回单例）、`WebQuoteClient` 以 `except Exception: pass`
   吞掉配置错误、`get_config()` 惰性解析一次而非返回冻结默认值、`reset_config()`
   语义从"回到 DEFAULT_CONFIG"改为"清空使下次重新读源"。

---

## 2. 决策点默认值（未收到异议即按此执行）

| # | 决策 | 默认 |
|---|---|---|
| 1 | v14 编排线归宿 | **(b) 整层删除——已于 2026-09-19 执行**：信封/DAG/router 删除，typed 并入 `Client.typed` |
| 2 | executor registry 三件套 | **删除**（`DIRECT_BINDINGS` 唯一事实源）+ 防回潮守卫 |
| 3 | `trade/` | 保留，README 标注 experimental（自设模拟红线，无架构冲突） |
| 4 | 覆盖率门禁 | Phase 2 后重测并按有效代码重新校准数值（不硬凑旧 77%）。**现状**：阈值数字已收敛为 `pyproject` 单源，但**一次都没下调**；重钉需要 CI 环境（ubuntu+py3.11）实测数字，本机 Windows 数字不作依据 |
| 5 | 文档形式 | 本 v17 文档 + ARCHITECTURE.md；v1–v16 移 archive；v16 加状态横幅 |
| 6 | `cache.py`(KlineCache) | 已被 Phase 2 直删——追认 |
| 7 | 配置面归宿（F-16） | **(a) 最小接线——已于 2026-09-19 执行（Phase 6）**：内核读 `get_config()`，只保留真实生效键，七个装饰段删除；不选 (b) 全删，因为 `tstdx.toml` 已是公开导出面 |
| 8 | 发布次序 | Phase 6（配置接线）先于真实网络 smoke 与 `v1.1.0-dev.1` tag；未接线状态不得进发布说明 |

## 3. 提交策略与风险

| Phase | 提交粒度 | 主要风险 | 缓解 |
|---|---|---|---|
| 3A | 单 PR（摘线 + gateway + 测试改写） | tests/v14 注入面大改 | 先加 executor 注入口再改测试，逐步迁移 |
| 3B | 可并入 3A | 无 | 守卫测试兜底 |
| 3C | 每 3–5 个模块一个 PR | 导入路径风暴 | 每 PR 全量门禁；根级白名单守卫最后一步加 |
| 3D | 按契约批次 2–3 PR | 契约不可达暴露 | fail-closed：当场补 binding 或下线并记录 |
| 4/5 | docs + release | 无 | — |

## 4. 总体验收清单

- [ ] `tstdx` 内仅存一条执行链（Client → kernel → executor），四个服务面只翻译不执行
      （原 `RuntimeGateway` 并列口径已随 Phase 3A 删除该接缝而失效）
- [ ] executor registry 三件套不存在，`DIRECT_BINDINGS` 唯一
- [x] `tstdx.toml` / `TSTDX_*` / `configure()` 三条路径对 `Client()` 真实生效，且
      配置面不引入任何缓存或降级语义（Phase 6；`tests/runtime/test_kernel_config_wiring.py`）
- [ ] 根级 `.py` ≤ 10；`execution/` DAG 与 `provider/` v14 适配器已物理删除
- [ ] 60 typed 契约端到端或有下线记录；`QueryResponse` 仅为视图
- [ ] README / ARCHITECTURE / `__init__` docstring 与代码零矛盾；旧方案归档
- [ ] 随机测试序无红；全量门禁绿；三面冒烟通过（同 SHA 证据）
