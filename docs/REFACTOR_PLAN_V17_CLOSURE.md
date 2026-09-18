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
| F-15 | P1 | 格式门禁长期为红：`ruff format --check tstdx/ tests/ scripts/` 在 0.9.6 与 0.14.4 下均报 74 个文件待重排，而 CI 用浮动的 `ruff>=0.5` | **格式与版本已清偿**（2026-09-19，Phase 5 第 2 步）：65 个待重排文件一次纯格式提交清零，重排前后 `ast.dump()` 逐个比对无差异；dev 依赖钉死 `ruff==0.15.2` / `mypy==2.3.1`。**覆盖率部分仍待办**：阈值数字已收敛为 `pyproject.toml [tool.coverage.report] fail_under` 单一事实源（删 Makefile/CI 的 `--cov-fail-under` 副本并加守卫测试），**未下调阈值**；**离线缺口已闭合**（2026-09-19，Phase 5 第 4 步实测）：Windows+py3.12 整仓 `-m "not network"` 为 **78.79%**、`PYTEST_RC=0`，已高于阈值 77；**重钉阈值数字仍待 CI 环境（ubuntu+py3.11）数字**，本机值不作依据 |
| F-18 | P1 | **安全资产躺在链外**：`tstdx/providers/http.py` 的 Provider 主机边界守卫（`host_allowed` + `ProviderBoundHttpClient` + 逐跳 `Location` 校验，11 项离线测试全覆盖）在 v16 删除跨源路由层后**没有任何生产调用点**。SECURITY.md 与各文档均未声称它在运行 ⇒ 不是"防线失效"，而是"一件造好并测过的防线没人接"。接进 `tstdx/web/_base_http.py` 会改变 web 传输的失败语义（凡未登记在 `PROVIDER_HTTP_HOST_SUFFIXES` 的主机一律挡掉），需逐源核表并真机验证 | ⏳ **待用户决策**（属安全面，不自行拍板）：(a) 接线 `build_client(provider=…)`，先补全 11 个 Provider 的主机表；(b) 连同测试删除，回到"由调用方自证单源"；(c) 维持现状 + 白名单豁免（附理由）。当前默认执行 (c)，见 `scripts/_reach_allow.txt` |
| F-19 | P1 | **spec_audit 的三条口径缺陷使 strict 门禁失真**：① `audit_all` 复用 `codegen.load_all_specs`（以 `spec_id` 为键），跨族同号互相覆盖——实测 `TRADE/0x0001` 吞掉 `F10/0x0001`、`TRADE/0x0100` 吞掉 `7727/0x0100`，**这两条命令永远不会出现在审计输出里**（分母 44 被读成 42）；② `_family_to_constant` 对未知 family 静默回落 STANDARD，于是拿 7709 账本查交易命令，把"查错账本"报成"命令未登记"；③ 无载荷控制帧（`0x0004` 心跳 / `0x000D` 握手，spec 自声明响应 `fields/header/record_size` 全空）被要求"有注册解析器"，而它们按定义没有载荷可解析 | **已清偿**（2026-09-19，Phase 5 第 4 步）：改为逐个 YAML 遍历（自动探测 draft 显式排除并可枚举）；TRADE 族查自己的账本与帧层（`tstdx.trade.constants` 常量值 + `CMD_NAMES` 双向对齐、`tstdx.trade.frames` 编解码锚点）；控制帧豁免**判定源自 spec 内容**而非硬编码清单，且"未声明字段"不等于"声明为空"。复测 `Total: 44 / In Ledger: 44 / Coverage 100.0%`、`--strict` RC=0，**100% 阈值未动**；4 项防回潮断言见 `tests/test_spec_coverage.py` |
| F-20 | P2 | 7709 账本把 `0x0004 HEARTBEAT` 标为 `verified=True`，但全仓**没有发送方**；传输层探活用未入账本的 `0x0002`（`DEFAULT_HEARTBEAT_CMD`，其注释说明"服务端对未知命令回短帧，探活只判通畅"）。同时该注释指向一个不存在的配置键 `hosts.heartbeat_cmd`（Phase 6 后 `HostsConfig` 只有 `servers`/`slots_per_host`） | **部分处理**（2026-09-19）：只把幻影配置说法改成真实覆盖点（连接池构造参数 `heartbeat_cmd`，并写明"配置面没有这个键"）。**改默认探活码属真实网络行为变化**，须真机验证 ⇒ 未动，登记为发布后小 PR |
| F-21 | P2 | **测量方法缺陷比红灯更危险**：`cmd \| tail; echo $?` 量到的是管道末端的退出码，因此 originality / spec_audit / reachability 三项曾被读成"已绿"。CI 上它们是硬门禁 | **已清偿**：本仓所有门禁复测改用 `${PIPESTATUS[0]}` 或先重定向再取 `$?`；教训与正确写法写入 CONTRIBUTING 门禁段 |
| F-22 | P1 | **豁免记录自己无人审计**（Phase 5 第 5 步实测）：`scripts/_reach_allow.txt` 28 条里 **9 条是死记录**——2 条指向 v10/v9 就消失的 `tstdx.sinks`、`tstdx.native`，7 条（`tstdx.feedback*`、`tstdx.domain.adjust`、`tstdx.streaming.{engine,push}`）指向**早已接线、现已从入口可达**的模块。扫描器只把白名单当"孤儿减集"，既不查条目是否还存在，也不查它是否还在豁免任何东西，所以 `--strict` 绿≠记录有效。附带：14 条理由 <40 字符（含 4 条短到"公开：用户统计"），`tstdx.charset` 与 `tstdx.deprecation` 的理由写着"_LAZY 导出"，而根包 `_LAZY` 实测 17 个值里**没有这两项**（理由是假的）；`pyproject.toml` 同一形状的死配置 2 处（mypy 覆盖 `tstdx.native.*`、`keyring.*` 忽略表），后者被 mypy 自己的 `warn_unused_configs` 报了出来但没人当回事 | **已清偿**（2026-09-19）：① 扫描器新增记录守卫，四类缺陷与孤儿同权重使 `--strict` 失败——`[dead]`（指向不存在模块）、`[stale]`（指向已可达模块：**保留它等于把将来真正的断链读成绿**）、`[thin]`（理由 <`MIN_REASON_CHARS=40`）、`[dup]`（同模块重复登记，后一条静默覆盖前一条）；② `tstdx.__main__` 从"豁免"改判为 `_entrypoints()` 种子（与 `tstdx.cli`/`tstdx.tools.*` 同类：静态图永无对它的 import 边，`python -m tstdx` 却必然加载）；③ 清单重写为 17 条，逐条给出可核验证据（docs 路径 + 具体测试文件 + 为何生产链路不 import），"内核不 import"一族按"用户显式导入的公共 API"与"契约/守卫资产"分组，TRADE 族额外写明 `spec_audit` 是**按字符串模块名走 importlib** 解析它（AST 图看不见这种边）；④ 删 9 条死记录、修 2 条假理由、删 `pyproject.toml` 两处死配置。**复测**：`192 模块 / 可达 175 / 豁免 17 / 记录缺陷 0`，`--strict` RC=0；`mypy`（CI 参数）RC=0 且 `unused section` note 消失；守卫回归 8 项见 `tests/architecture/test_reachability_allowlist.py` |
| F-23 | **P0**（对外承诺类） | **文档声称存在一个已被删除的安全能力**：SECURITY.md「凭据保护」整节写着"tstdx 使用三级凭据存储：系统 keyring / 环境变量 / 加密文件 `~/.tstdx/credentials.enc`"，README 特性表与结构树也各写一遍（`├── security/ # 凭据三级存储…`）。而 `CredentialStore` 早在 **v10** 就按 ADR-007-010 判定"全库零调用方、属过度工程"删除，只剩 `tstdx/security/__init__.py` 一个 `__all__ = []` 的空壳在替它"作证据"。docs-code 门禁当时只校验反引号里的 `tstdx.*` 点号路径与 README 数字，**散文式能力承诺不在射程内**，所以这条假承诺一路全绿 | **已清偿**（2026-09-19，Phase 5 第 5 步）：① 空壳包 `tstdx/security/` 物理删除（历史决议留在 ADR-007-010，不需占位包），其白名单行随之删除；② SECURITY.md「凭据保护」改写为"本库不存储凭据"+ 四条现状（行情链路无凭据 / 交易侧只有纯内存模拟器 / 真券商由调用方自管密钥 / 错误上下文与反馈先脱敏）；③ README 特性行改为可核验的 `security.use_tls` TLS 与错误脱敏事实，并显式标注 `tstdx.providers.http` 主机守卫"已实现但未接入 web 链路"（与 F-18 一致），结构树删去 `security/` 行、补上曾漏掉的 `__main__.py` 行；④ **补门禁**：`test_doc_code_consistency.py` 新增 3 项，把 README 结构树条目与磁盘做双向对账（列出的必须存在 + 磁盘上的顶层包/模块必须都列出），使这类幻影行不能再隐身 |
| F-24 | **P0**（发布链路类） | **一条 PR 阻塞 CI job 固定为红，且守卫测试把缺陷写成契约**（Phase 5 第 6 步本地全链复现时暴露）：`77bc2fe`（v16 Phase 2）物理删除 `tstdx/native.py` 与 `tests/compatibility/test_native_fallback_contract.py`，但三处消费者原地未动——① `.github/workflows/native.yml` 的 "Native compatibility & fallback parity" job 仍在 `pull_request: [main]` 上跑：`compileall -q tstdx/native.py` 对不存在的路径**打印 "Can't list" 却退出 0**（本机实测），于是一步静默"通过"，真正跑测试的下一步以 pytest 退出码 4 固定失败；② `Makefile` 的 `native-compat` target 与 `gates` 依赖链仍指向那个已删除的测试 ⇒ `make gates` 走到底必红；③ `test_ci_workflow_contracts.py` 有一条**断言 workflow 必须包含该已删除测试路径**的守卫，即"防止回归"的测试正好把回归钉住。同批发现的文档口径失真：PR 模板要求勾选这条不可能为真的门禁、CONTRIBUTING 门禁清单列着它、README 写 `CI：9 jobs`（实际 11）与"六步门禁"（`gates:` 实际挂了 12 项 target，删掉 ghost 后 11） | **已清偿**（2026-09-19）：① 删 `native.yml` 与 `native-compat` target 及 `gates` 依赖（能力已随 `tstdx.native` 一起退役，无保留理由；不做"恢复测试"是因为被测模块本身不存在）；② **补三类守卫**取代那条反向契约：workflow 与 Makefile 里写死的 `tests/…\|scripts/…\|tstdx/….py` 路径必须存在于磁盘、`gates:` 的每个前置 target 必须已定义；③ 文档面同步（PR 模板勾选项、CONTRIBUTING 清单、README 的 11 jobs / 11 步门禁 / ruff format 既成事实）。**每条守卫都用变异验证过**：往 ci.yml 与 Makefile 各塞一条指向不存在文件的路径、给 `gates:` 加一个未定义 target、把 README 数字改回 9/12，三处分别 RC=1 并指名缺陷，随后原样还原 |

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
| `client_api.py → client/api.py` | ✅ 收口（2026-09-18）：唯一业务入口并入 `client/` 包，根级只留契约层 |
| `client_core.py → client/core.py` | ✅ 同名落地 |
| `direct_provider.py → runtime/executor.py` | ✅ |
| `orchestration.py → runtime/orchestration.py` | ✅ |
| `runtime_audit/identity/provenance.py` | ✅ → `runtime/{audit,identity,provenance}.py` |
| `capability_catalog.py / capability_audit.py` | ✅ → 新包 `catalog/{capability,capability_audit}.py` |
| `provider_api.py / provider_contract.py / provider_guard.py / provider_audit.py` | ✅ → `catalog/{provider_bindings,provider_contract,provider_guard,provider_audit}.py` |
| ~~`freshness/health/failure.py → runtime/support.py`~~ | **偏差 1**：改为整体删除（见下） |
| 根级白名单 ≤10 | ~~偏差 2（曾落地 11 项）~~ **已收口**（2026-09-18）：`client_api.py` 移入 `client/api.py`，白名单回到原案 10 项，公开面 `from tstdx import Client` 不变 |

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
13 个旧路径磁盘不可见且不可导入、新路径可导入）；`test_official_runtime_no_fallback`
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
- **F-11 命名债 —— 已清偿（2026-09-19，Phase 5 第 3 步）**：`tstdx/web/facade.py` →
  `tstdx/web/session.py`，`tstdx/web/_facade_mixin_*.py`（11 个）→ `_session_*.py`，
  clean-break 不留别名。原语义词是"Web Provider 的会话/适配器分组"，但"门面"一名指向的
  `UnifiedQuoteAPI` 已随 v16 Phase 2 物理删除，读者会误以为存在跨源聚合门面。
  引用面同步：47 个文件重写（生产代码里含 `catalog/provider_bindings.py` 的绑定字符串与
  `tstdx/web/__init__.py` 的 `_LAZY` 子模块名——后者是**字符串键**，按点号路径 grep 会漏，
  实测由 `tests/web` 的 ImportError 暴露）；`docs/api/README.md` 事实路径、
  4 份审计文档的路径引用一并更正；`[1.0.0]` 及归档方案文档的历史叙述保留原名。
  测试函数名 `test_facade_*` → `test_session_*`（同文件内），测试**文件名**不改：
  它们被 CHANGELOG/审计文档按名引用，改名会把一次纯改名 PR 变成文档考古 PR。
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
   ⚠️ 仍未执行：该步会产生真实网络请求并在远端可见（tag），须用户明确确认后启动。
4. ✅ **CI 硬门禁本地红清偿（Phase 5 第 4 步，2026-09-19）**：`.github/workflows/ci.yml`
   上 originality / spec-coverage / reachability 三个 job 是 `--strict` 硬门禁，本机
   **三项全红**。测量教训：**门禁命令一旦接上管道，`$?` 量到的是管道末端**——
   `cmd | tail; echo $?` 会把红灯读成绿灯，此前的"门禁已绿"结论就是这么来的。以下
   结论全部以 `${PIPESTATUS[0]}` / 重定向后独立取 RC 复测。

   | 门禁 | 原判 | 真缺陷 / 误报 | 处置 |
   |---|---|---|---|
   | originality | 红 | 2 文件缺许可证头（`catalog/provider_contract.py`、`py.typed`）＝真缺陷；`batch.py` 一句 "follows from" 散文被 `derived from` 模式命中＝措辞巧合 | 补头；改写该句不含被禁措辞。复测 `Total: 194 … Suspicious: 0`，RC=0 |
   | reachability | 红 | 3 个真孤儿 + 扫描器自身 3 处漏报（见下） | 1 项接线、2 项删除、2 项登记豁免，RC=0 |
   | spec-coverage | 红（81.0%） | 工具口径缺陷：分母被 `spec_id` 去重吞掉 2 条命令；TRADE 族被当作 7709 查账本；无载荷控制帧被要求"有解析器" | 口径修正（见 §0.4 F-19），复测 **Total: 44 / Coverage 100.0%**，RC=0，**100% 阈值未动** |

   - 死面删除 3 项：`tstdx/observability/planned.py`（含其测试）、`tstdx/providers/adapter.py`、
     `BatchSpec` 符号。**BatchSpec 取"删除"而非"接线"**：它文档化的职责指向 v16 已物理
     删除的 `UnifiedRuntime.execute_batch`，且内核的批量循环已经把归一/去重/逐 symbol
     直连做实——再造一个信封只会有第二个事实源。根面 46 → 45 项。
   - 孤儿 `tstdx/domain/calendar.py` **接线**：`tools/capture.py` 的法律自检曾自带一份
     只认周末的交易时段副本（法定节假日被误判为交易时段 ⇒ 错误中止采集）。改判
     `TradingSession` 常量 + `is_trading_day` 后，重复事实消除且日历自然生产可达。
   - 豁免登记 2 项（附评审理由，见 `scripts/_reach_allow.txt`）：
     `tstdx.catalog.provider_contract`（隔离契约，消费方是 `tests/provider_isolation`；
     内核不 import 正是它"零依赖可被任一 Provider 使用"的前提）、
     `tstdx.providers.http`（**F-18**，见下）。
   - 扫描器修正 3 处，都是**漏报方向**（放宽判定只在"看得更全"这一侧）：种子名单换成
     v16 现行模块名；`_LAZY` 边支持 `AnnAssign` 与不带包前缀的子模块名字符串；
     `tstdx.tools.*` / `tstdx.cli` 由"整包直接标可达"改为作为种子参与 BFS。
   - 复测（每项独立取 RC，不走管道）：`ruff check .` RC=0、`ruff format --check .` RC=0
     （433 文件）、`mypy tstdx/`（CI 参数）RC=0（193 源文件）、golden gate RC=0、
     docs-code 一致性门禁 RC=0。
   - 离线全量套件（当前树，无任何 `--ignore`）：**3227 passed / 7 skipped / 1 xpassed /
     0 failed**，coverage **78.79%**，`PYTEST_RC=0`（阈值 77 未动）。
   - 过程中两处非源码噪声，都按"补环境 / 等并行会话"处理而非改测试：
     ① `tests/reader/test_formats.py` 3 项 DataFrame 断言抛 `DependencyMissingError [E1020]`
     ＝本机 `.venv` 缺 `[dataframe]` extra（CI 的 `.[dev]` 含它）。`uv pip install
     "pandas>=2.0"` 后该文件 41 项全绿；**没有加 `importorskip`**——那能当场抹掉这 3 项，
     但会把本机环境与 CI 永久分叉，也违反"不得为过 CI 增加跳过"。
     ② 测量当时工作区有一个未跟踪的并行会话 WIP 文件
     `tests/transport/test_async_transport_coverage.py`（12 项失败：`AsyncConnectionPool([])`
     的断言意图与实现不符、`family='standard'` 非合法族名），使含它的整仓运行读到
     `15 failed / 78.61%`。该文件随后由并行会话修好并入库（`4ae1e38`，42 项全绿），
     故上表数字为不含任何豁免的整仓值。

5. ✅ **豁免清单与对外安全承诺审计（Phase 5 第 5 步，2026-09-19，见 §0.3 F-22/F-23）**：
   第 4 步把三个红灯门禁改成绿灯之后，绿灯本身成了新的审查对象——**"门禁绿"只说明
   判定规则没被违反，不说明规则读到的输入是真的**。两处都属这一类。

   - **F-22 豁免清单**：`scripts/_reach_allow.txt` 28 条中 9 条已无对应事实——2 条指向
     v10/v9 就消失的模块（`tstdx.sinks`、`tstdx.native`），7 条指向**早已从入口可达**的
     模块（`tstdx.feedback*`、`tstdx.domain.adjust`、`tstdx.streaming.{engine,push}`）。
     后者比前者更危险：留着一条"已不需要豁免"的豁免，将来这个模块真被断链时扫描器
     照样报绿。同时 14 条理由短到无法核验，其中 2 条写着"_LAZY 导出"而根包 `_LAZY`
     实测根本不含这两项——**理由是编的**。
   - **处置**：扫描器加记录守卫，`[dead] / [stale] / [thin] / [dup]` 四类记录缺陷与孤儿
     同权重使 `--strict` 失败；`tstdx.__main__` 由"豁免"改判为进程入口种子（静态图里
     永不出现对它的 import 边，`python -m tstdx` 却必然加载，与 `tstdx.cli` 同类）；
     清单重写为 17 条，逐条给出可核验证据（文档路径 + 具体测试文件 + 为何生产链路不
     import），TRADE 族额外写明 `spec_audit` 是**按字符串模块名走 importlib** 解析它
     ——AST 静态图看不见这种边，因此它的"不可达"是工具口径而非死代码。
     `pyproject.toml` 同形状的死配置一并删除（mypy 覆盖 `tstdx.native.*`、`keyring.*`
     忽略表）；后者 mypy 自己的 `warn_unused_configs` 早已报出，只是没人把 note 当缺陷。
   - **F-23 对外承诺**：SECURITY.md 整节 + README 两处宣称"三级凭据存储（keyring /
     环境变量 / 加密文件）"，而 `CredentialStore` 在 **v10** 就按 ADR-007-010 判定过度
     工程删除，只剩 `tstdx/security/__init__.py` 一个 `__all__ = []` 的空壳替它作证据。
     docs-code 门禁只校验反引号里的 `tstdx.*` 点号路径，散文式承诺不在射程内 ⇒ 假承诺
     一路全绿。处置：空壳包物理删除（历史决议在 ADR，不需占位包）、SECURITY.md 改写为
     "本库不存储凭据"+ 四条现状、README 特性行换成可核验事实（`security.use_tls` TLS、
     关键字脱敏），并就地标注 `tstdx.providers.http` 守卫"已实现但未接入 web 链路"
     （与仍待决策的 **F-18** 保持同一口径，不再单方面宣称已覆盖）。
   - **补门禁**：`test_doc_code_consistency.py` 新增 3 项，把 README 结构树条目与磁盘做
     **双向**对账（列出的必须存在；磁盘上的顶层包与顶层模块必须都列出）。而"散文式能力
     承诺"这一类缺陷本质不可机器判定，只能靠结构树对账 + 删空壳包压缩它的隐身空间——
     这一点如实记录，不过度声称已根治。
   - **复测**（每项独立取 RC，不走管道）：reachability `--strict` RC=0，
     `模块总数: 192 / 可达: 175 / 白名单豁免: 17`，`[ALLOW-DEFECT]` **0** 条；
     `tests/architecture/` **88 passed**（含新增 8 项记录守卫回归
     `test_reachability_allowlist.py`、12 项 docs-code 一致性）；`mypy`（CI 参数）RC=0
     且 `unused section(s)` note 消失；`ruff check` / `format --check` 干净。阈值与
     白名单之外的判定强度均未下调。

6. ✅ **本地整条门禁链复现，抓出一条固定为红的 ghost CI job（Phase 5 第 6 步，
   2026-09-19，见 §0.3 F-24）**：把 `.github/workflows/*.yml` 与 `Makefile` 的
   `gates` 链逐条按 CI 参数在本机复跑，每条单独重定向取 RC（不走管道，见 F-21）。

   | 门禁 | CI 参数 | 本机 RC |
   |---|---|---|
   | ruff check / format --check | `tstdx/ tests/ scripts/` | 0 / 0 |
   | mypy | `--ignore-missing-imports --no-error-summary --warn-unused-ignores` | 0 |
   | originality | `--strict tstdx/` | 0（`Total: 193 / Suspicious: 0`） |
   | spec-coverage | `--json --strict` | 0（`coverage_pct: 100.0`） |
   | golden | `--gate --require-markets --require-kline-categories 0,4,9 --require-payloads` | 0 |
   | reachability | `--strict` | 0（`192 模块 / 可达 175 / 豁免 17`） |
   | bridges | `tests/test_bridges.py` | 0 |
   | adversarial | `tests/adversarial` | 0（4 项） |
   | docs links | `scripts/check_docs_links.py` | 0（82 文件） |
   | benchmark smoke | `scripts/run_benchmark_smoke.py` | 0 |
   | **native compat** | `tests/compatibility/test_native_fallback_contract.py` | **4 ＝ 文件不存在** |
   | docs-code 一致性（仓内守卫） | `tests/architecture` | 0 |
   | 离线全量套件 + coverage | `-m "not network" --cov=tstdx --cov-report=xml` | 0（**3238 passed / 7 skipped / 1 xpassed / 0 failed，78.80%**，阈值 77 未动） |

   - `native-compat` 这一行是本步的全部收获：它不是"这次没跑起来"，而是**自 `77bc2fe`
     起就永远跑不起来**——它和被它守护的 `tstdx/native.py` 同批被删。同一形状的错误在
     CI 侧是 `native.yml` 的一个 PR 阻塞 job，其"编译"步骤用了
     `compileall -q <不存在的路径>`：该命令**打印 "Can't list" 而退出码 0**（本机实测），
     所以 job 的前一步是**静默假通过**，红只在下一步才显形。
   - 更值得记的是**守卫反向**：`test_ci_workflow_contracts.py` 里有一条断言"workflow
     必须引用那个已删除的测试路径"。它原本用于防止有人把该门禁软化，删除动作落地后
     却变成把缺陷钉成契约——**任何"必须包含某字符串"的守卫，都要连带断言该字符串指向
     的东西存在**，否则它只会阻止你删掉僵尸。
   - 处置：`native.yml`、`Makefile` 的 target 与 `gates` 依赖、PR 模板勾选项、
     CONTRIBUTING 清单一并删除；新增三项存在性守卫（workflow 路径、Makefile 路径、
     `gates:` 前置 target 已定义）+ README 门禁规模数字与 `ci.yml`/`Makefile` 对账。
   - **变异验证**（守卫不亲测等于没有）：往 ci.yml、Makefile 各插一条指向不存在文件的
     路径，`gates:` 加一个未定义 target，README 数字改回 9/12 —— 四次分别 RC=1 并指名
     具体缺陷，随后原样还原（`git diff` 确认无残留）。

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
   **2026-09-19 追记**：76.14% 的实测缺口已由后续真实离线测试填平，本机 Windows+py3.12
   整仓现为 **78.79%**（`PYTEST_RC=0`，见 Phase 5 第 4 步）；阈值 77 全程未动，重钉仍需
   CI 侧数字。
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
