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
| F-25 | P2（口径类） | **`contract_audit.py` 的自述比它的行为强**：模块 docstring 写着规则 1「每个业务 capability **必须**有对应的 Typed Query 契约」、用法段写着「`--ci` 任何缺口 exit 1」「退出码 0=全绿」，而代码把"注册表有、契约无"判为 `PENDING` 且 `run()` 只对 `ERROR` 计数（`if ci and errors`）。实测口径：155 个业务 capability / 63 个有契约 ⇒ **92 项 PENDING 全部 exit 0**。README 另把它写成"契约↔注册表↔**绑定**三方对账"，而它的五段审计里根本没有 provider bindings 这一维 | **已清偿**（2026-09-19）：docstring 改为逐条标注级别（规则 1、4 为 PENDING，2、3、5 为 ERROR）并写清"`--ci` 仅在存在 ERROR 级缺口时 exit 1，PENDING 是登记在案的待补面不阻断"；README 的两处描述改成实际的五段对账。**没有**把 PENDING 升级为阻断——那需要一次性补 92 份契约，且会把一个已知待办伪装成既成事实；缺口尺寸记在本行而不是塞进门禁 |
| F-26 | P1（链路贯通类） | **唯一在每次构造执行器时运行的贯通审计，看不见它名字里那件事**：`tstdx/catalog/capability_audit.py::audit_capability_bindings()`（由 `runtime/audit.py` 在 `DirectProviderExecutor.__init__` 调用）只断言 `MIGRATED_BINDINGS ⊆ DIRECT_BINDINGS`，而**能力目录本身就是从绑定表生成的**——某项对外能力悄悄失去执行路径时两侧同时缩小，审计照绿；反向（绑定表里藏着没承诺过的暗绑定）也不查。Provider 注册表逐 channel 声明的 `(provider, channel, capability)` 这一独立事实源**只在离线测试 `tests/runtime/test_v13_architecture_alignment.py:50` 里对账过**——它保护的是"跑测试的人"，产品内一次单侧漂移不会被任何运行期判据捕获（同处的 `audit_direct_bindings()` 只查重复键与执行元数据，缺元数据仅 `warnings.warn`）。守卫测试本身也形同虚设：只断言 `report.* > 0`，剩 1 条绑定也通过 | **已清偿**（2026-09-19）：① 审计改为以**注册表声明**为独立分母的双向对账——`migrated ⊆ executable`、`declared − executable` 非空即报"registry-declared … with no executor binding"（声明了却没有执行路径）、`executable − declared` 非空即报"outside the Provider registry"（执行路径不受声明约束），报告新增 `declared_bindings`；运行期判据由此与离线测试同权重，任何一侧漂移都在 `Client()` 构造期失败；② **不引入 `Client` 依赖**：曾考虑用 `Client.capabilities()` 当对外面，但它自身由 `MIGRATED_CAPABILITIES` 推导（同源于绑定表，不是独立分母），且在 catalog 里惰性 import client 会在构造执行器时反向拉起入口层；③ 测试补真实断言：注册表 capability 名集合 == `Client.capabilities()`（172），并以 monkeypatch `DIRECT_BINDINGS` 做三次变异（丢目录内绑定 / 丢目录外绑定 / 加幽灵绑定）分别命中三条消息，证明守卫有牙。**复测**：当前三面对账 `migrated 229 ⊆ executable 251 = declared 251`、双向差集为空 ⇒ 主体链路在"声明↔执行"这一维确实闭合（这是对本轮"核心功能是否全部实现、主体链路是否贯通"的机器可复核回答，同时如实标注：绑定存在 ≠ 运行期正确，后者仍靠 golden/adversarial 与尚未执行的真实网络冒烟）。`tests/provider_isolation` / `tests/runtime` / `tests/architecture` RC=0，`mypy` RC=0，`ruff check`/`format --check` 干净 |
| F-27 | P1（配置面 ↔ 使用面） | **CLI 声明了一整排连接参数，却把它们丢在传输适配层**（Phase 5 第 8 步实测）。Phase 6 让 `UnifiedRuntime` 成为配置面唯一读者之后，CLI 这条最常用的入口并未跟着改：① `quotes`/`bars`/`snapshot`/`minute`/`trades`/`security-count`/`security-list` 7 个命令由 `_provider_args()` 声明了 `--host`，handler 却构造裸 `Client()`——`--host` 与 `--timeout` 双双丢弃，命令正常返回数据、退出码 0，即"幻影开关"（fail-open：用户以为钉住了主站，实际仍在配置/内置池上）；② 15 个命令的 `--timeout` 带 `default=5.0` 字面值，而 `[core] timeout` 默认同为 5.0 ⇒ 数值上看不见差异，**只要用户真在 `tstdx.toml` 里改过就静默失效**，F-16 刚承诺的"配置面即执行面契约"在 CLI 侧被重新破掉；③ `probe`/`blocks`/`list`/`quotes-snapshot` 4 个命令有意走传输客户端 `TdxClient`（不经内核），却把 `_resolve_hosts()` 的"未指定"折成 `None` 直接交给构造器 ⇒ `[hosts] servers` 对它们永远是空头支票（内核在 `kernel.py:62-67` 做的正是"未指定即读配置"这一步）；`goods`/`f10` 同族问题但**只修 timeout**——它们是多步流程的族客户端（goods/F10 协议），而 `[hosts] servers` 是 7709 标准族条目，把行情主站喂给它们是错的，故其 `hosts` 仍只认 `--host`；④ `stream --provider` 解析后从不转发，非 tdx Provider 静默按 tdx 跑。触发发现的是一条无关的文档修正：README 写着 `serve --host 0.0.0.0`（前面带 CLI 程序名），而 `serve` 早已改名 `--bind`，照抄即 exit 2——**没有任何门禁跑过文档里的 CLI 示例** | **已清偿**（2026-09-19）：① `_common.py` 新增三个单一职责助手，把"CLI 只有两种合法姿态"写成代码——`_client_kwargs`（内核路径：只转达用户显式说过的，未说即 `None` 让内核读配置）、`_transport_kwargs`（内核外传输客户端：就地复现内核的配置解析，`--host` 缺席时取 `[hosts] servers`）、`_transport_timeout`（同规则的单值版）；16 处内核侧构造点（8 `Client(**_client_kwargs(args))` + 8 `_ClientRows(**_client_kwargs(args))`）、7 处传输侧构造点（`probe`/`blocks`/`list`/`quotes-snapshot`/`stream` 走 `_transport_kwargs`，`goods`/`f10` 走 `_transport_timeout`）、`stream` 的 `--provider` 全部接线；② 15 个 `--timeout` 字面默认改 `None`，只保留 `hosts`/`server-test` 两处诊断命令的 5.0（它们要遍历候选池，本就不该吃 `[core] timeout`，就地注明理由）；③ **补两道门禁**：`tests/architecture/test_cli_connection_contract.py`（20 项，用 fake Client 捕获真实构造参数，含"逐命令结构性守卫"——凡声明 `--host`/`--timeout` 又非诊断白名单的命令，其 handler 源码必须出现三个助手之一）与 `test_doc_code_consistency.py::test_every_documented_cli_example_parses`（活文档的围栏块+行内 CLI 示例逐条过真实 parser）。**门禁上线即抓到 4 条已失效文档命令**：README 两处 `hosts audit --hosts-file X`（`--hosts-file` 属 `hosts` 组级选项，必须在 `audit` 之前）被 parser 拒为 exit 2、`docs/api/interfaces.md` 的 `--family all`（`all` 不是合法取值，默认即全 5 族）、`docs/api/README.md` `margin` 那一行缺必填位置参数——全部按真实语法改正，未放宽门禁。**变异验证**：把 `cmd_quotes` 改回裸 `Client()` ⇒ 三条断言分别命中 `KeyError: 'hosts'` ×2 与结构性守卫 `['quotes'] == []`，随后原样还原（`git diff --stat` 与改前一致）并复绿 |
| F-28 | P2（同类外溢） | **F-27 的守卫只盯连接参数，等于承认"其余选项没人管"**：把该守卫从"必须消费 `--host`/`--timeout`"推广到"parser 声明的每个选项都必须被读到"（对 31 个命令 × 全部 dest 逐个扫 handler 源码，豁免面收敛为四个点名的助手 `_client_kwargs`/`_transport_kwargs`/`_transport_timeout`/`_resolve_hosts` + 两个诊断命令），实测唯一命中项是 **`stream --max-queue`**：parser 声明它（`default=1024`），`cmd_stream` 却调 `stream.subscribe(symbols, interval=, diff_only=, on_quote=, on_error=)` 而**不传** `max_queue`，于是 `QuoteStream.subscribe` 自己的 `max_queue=1024` 默认值接管。与 F-27 同形：CLI 字面默认与库默认同为 1024，**只有把队列上限调小以约束内存的用户会被静默忽略**——而这恰恰是背压参数唯一的用途 | **已清偿**（2026-09-19）：① `cmd_stream` 补 `max_queue=args.max_queue` 转发；② 结构性守卫由"连接参数专用"改为**全量选项消费审计**（`_dead_cli_options()`），并把豁免面从"整个 `_common` 模块源码"收紧为四个点名助手——否则任意一处 `args.x` 会给所有命令开绿灯；③ `tests/unit/test_cli_semantics.py` 的 stream 假对象改用与真实 `subscribe` 一致的完整关键字签名并断言 `max_queue` 实到（此前它的假签名恰好缺该参数，正是"测试替身比生产接口更窄"导致幻影参数无人发现）。**变异验证**：删掉 `max_queue=args.max_queue` 一行 ⇒ 守卫报出 `stream: --max-queue (dest=max_queue)`，还原后复绿 |
| F-30 | **P0**（口径类：门禁读数失真） | **`*_hardening` 侧车在 import 期整体替换连接池方法，使类体内的真实实现成为永不执行的死代码**（Phase 5 第 11 步实测）。四个侧车（`_pool_hardening` 461 行 / `_async_pool_hardening` 530 行 / `_async_close_hardening` 129 行 / `_pool_provenance_hardening` 327 行）以 `ConnectionPool.request = _request` 之类的整方法赋值收尾，读代码的人在 `pool.py`/`async_.py` 里看到的 `request`/`update_hosts`/`close` 一行都不会跑；后果有三层——① 覆盖率读数被系统性压低（`pool.py` 46.2%、`async_.py` 44.7%，被测试覆盖的是侧车那份拷贝），F-15 的"覆盖率缺口"里有一块是这种记账假象；② 可达性门禁**结构上看不见**这种遮蔽（被覆盖的原方法在静态导入图里照样"可达"，F-22 的记录守卫无从命中）；③ 主站代际发布规则有两份事实源（`_pool_provenance_hardening` 与两个池类体各写一遍 `update_hosts`），且守卫测试 `test_public_pool_hardening_wiring.py` 把"实现住在侧车"钉成契约——想改回去必须先红一条测试 | **已清偿**（2026-09-19，Phase 5 第 11 步，提交 `9e5734a`）：按用户拍板的处置"**搬回类体，解散桩层**"执行——1 447 行侧车删除，实现搬回 owning 类体，三条共享发布原语（`validate_host_updates`/`new_endpoint_entry`/`next_generation_host`）上收到 `tstdx/transport/hosts.py` 供两池共用。**等价性以 AST 实证**：31 个搬迁成员逐个与 `git HEAD` 侧车原文比对归一化 `ast.dump()`（仅归一 `pool`→`self`、`_impl.X`→`X`、`helper(self, …)`→`self.helper(…)`、重命名成员、docstring/注解），`offenders: 0`。**守卫反转为防回潮**：同一测试改判"实现必须住在池模块 + `importlib.util.find_spec` 判侧车不存在 + `__wrapped__` 为空"，本地冒烟与两条 wheel 契约同批改判。**复测（同一轮日志）**：`PYTEST_RC=0`、整仓覆盖率 **80.60%**（阈值 77 未下调），`pool.py` → **80%**、`async_.py` → **89%**；`ruff check`/`format --check` 干净、`mypy`（CI 参数）188 文件 0 错、reachability `--strict` RC=0（`188 模块 / 可达 171 / 豁免 17`）、docs links 82 文件 OK。**未纳入本项**：`_pool_family_hardening.__init__`、`_ranking_hardening`、`_connection_contract_hardening`、`_host_selector_hardening` 与 client 层四件仍是 import 期打桩，但它们做**局部包装**（校验/参数注入）而非整方法替换，遮蔽面小一个量级；全部收敛为装饰器/显式调用登记为发布后独立 PR |
| F-29 | P2（链路贯通类） | **CLI 最后两条不经内核的数据命令：`changes` 与 `hot` 直接调用 `WebQuoteSession.stock_changes()` / `WebQuoteSession.hot_rank()` 静态方法**（第 9 步查完选项消费后，顺势排查同一模块的执行入口而暴露）。两项能力早已注册并有执行路径（`stock_changes` / `hot_rank` 各有 `derived`+`catalog` 与 `eastmoney`+ 同名 channel 的 `web_session` 绑定），所以这不是能力缺口，而是**同一个 Web 源有两条可达路径**：旁路那条拿不到 `QueryResult` 信封与 `Provenance.direct` 指纹，不经过 `validate_call` 的参数校验，也吃不到 F-27 的三个助手（`--host`/`--timeout` 对旁路命令天然失效）；更要紧的是它给验收口径「服务面只翻译不执行」留了一个活反例 | **已清偿**（2026-09-19）：① 两个 handler 改为 `with _ClientRows(**_client_kwargs(args))` 调用 `api.stock_changes(types, page=…, size=…)` / `api.hot_rank(page=…, size=…)`，`--types` 的 ValueError→exit 2 分支仍排在构造客户端之前（非法输入不触网）；② 新增**结构性守卫** `test_service_faces_never_import_the_web_layer`：以 AST 扫描 `tstdx/cli/**.py` 与 `tstdx/integration/**.py`（CLI + HTTP/WS/MCP 四个服务面）的全部 import，任何解析到 `tstdx.web` 的边即为红——判据与命令名无关，新增命令复刻旁路当场被抓；③ 逐命令断言 `test_web_backed_command_calls_the_kernel_not_the_source` 证明两命令确实以 capability 名调用 `Client`；④ 两处单元测试的假 `WebQuoteSession` 换成 fake `Client`（与既有 `fake_client` 夹具同形，避免替身比生产接口更窄）。**离线对拍**：stub 掉 `EastmoneyStockChangesSource.fetch_changes` / `EastmoneyHotRankSource.fetch_hot_rank` 后走内核，返回行与旁路一致，provenance 为 `ProvenanceKind.DIRECT`（provider/channel = `derived`/`catalog`），实参 `(8201, 8193)` 与 `page/size` 原样到达数据源。**变异验证**：把 `changes` 改回直接调用 ⇒ 结构性守卫报 `runtime_commands.py: import tstdx.web.session`、逐命令断言报 `kwargs is None`，双双 exit 1，还原后复绿 |
| F-31 | P2（口径类） | **包自己的门面 `tstdx/__init__.py` docstring 是三处矛盾的合集，而没有任何门禁看它一眼**（第 10 步核对验收清单「docstring 与代码零矛盾」时实测）：① Quick start 写着 `with Client(provider="tdx") as c:`，而 `Client.__init__(runtime=None, **runtime_kwargs)` 把关键字原样转给 `UnifiedRuntime.__init__`，其形参名是 `default_provider` ⇒ 照抄即 `TypeError: UnifiedRuntime.__init__() got an unexpected keyword argument 'provider'`（实测；仓内其余活文档一律写 `default_provider=`，只有这一处例外）；② 「分层（自底向上）」图列 14 层，磁盘上顶层包实为 24 个，`catalog`/`config`/`cli`/`integration`/`charset`/`profile`/`sink`/`tools`/`trade`/`feedback` 全部缺席——**这张图是新人理解本库的第一张地图，缺的恰是 v17 新增的服务面与配置面**；③ 门禁侧同源失明：`test_doc_code_consistency.py` 的 README 结构树检查（3 条）只覆盖 README，import 解析检查只看 markdown ⇒ 包 docstring 既不在文档门禁内，也不在 CLI 示例门禁内，①②永远不会被任何人抓到 | **已清偿**（2026-09-19）：① Quick start 改为 `Client(default_provider="tdx")`，**不给 `provider` 加别名**（v17 是 clean-break 口径，`Client(**runtime_kwargs)` 的键名以内核形参为准）；② 分层图补齐 24 层并逐条给一句话职责：服务面标注「只翻译不执行」，`trade` 标注「不接入内核」，`catalog` 标注「无执行」；③ README 结构树里 `cli/` 的「31 子命令，全部委托 Client」改为如实口径（数据命令全部经 `Client`，6 个传输/诊断命令除外并指向模块 docstring）；④ **包 docstring 纳入活文档门禁**：`test_dunder_docstring_layer_map_matches_the_package_layout` 双向对账（磁盘上的顶层包必须在图里、图里的层必须在磁盘上），`test_dunder_docstring_quickstart_examples_construct` 抽出 Quick start 的**名字直调**（`Client(…)` / `DayBarReader()` / `get_quotes(…)`）在禁网（`getaddrinfo` + `create_connection` 双拦）下真实求值——`TypeError`/`NameError`/`AttributeError`/`ImportError` 即矛盾，因禁网或文件缺失而失败则说明入口与签名成立（属性调用 `c.bars(…)` 留给真实网络冒烟）。**变异验证**：删掉分层图的 `integration` 行 ⇒ 报 `新增顶层包未写进包 docstring 分层图：['integration']`；插一行幽灵层 `execution` ⇒ 报 `分层图指向磁盘不存在的层：['execution']`；入参改回 `provider=` ⇒ 报 `Client(provider='tdx') -> TypeError: …`；三条各自 RC=1，还原后文档门禁 17 项全绿，`ruff check`/`format --check`、`mypy tstdx/`、originality `--strict`、docs links 均 RC=0 |
| F-32 | P2（测量口径类） | **验收清单要求"随机测试序无红"，但仓内从未有实现手段**（Phase 5 第 13 步实测）。第 1 步的执行记录写着"离线全量 `-p no:randomly`：0 failed"，并被第 8/9/10 步当作既成事实继续引用。两处错：① `-p no:randomly` 的语义是**关闭** `pytest-randomly` 的随机化，用它跑出的"0 failed"正是固定顺序的结果，把它读成随机序证据方向写反；② 本仓 dev 依赖与本环境**都没有 `pytest-randomly`**（`pip list` 只有 pytest / pytest-asyncio / pytest-cov），而 `-p no:<未安装插件>` 静默无操作——于是这条抽检无论真假永远读成绿，与 F-21"接上管道的 `$?`"同形：**测量装置 itself 缺判据时，绿灯是被构造出来的** | **已清偿**（2026-09-19）：不为一条抽检动 dev 钉版（`pytest-randomly` 会改变全套测试顺序基线），改用一次性 scratch 插件在 `pytest_collection_modifyitems` 里按种子洗牌。**实测**：seed 1 / 7 / 42 三轮整仓离线乱序全绿，各 `3276 passed / 5 skipped / 10 deselected / 1 xpassed`、RC=0（72–79 s）。**首轮踩到的新坑要记**：种子变量最初取名 `TSTDX_SHUFFLE_SEED`，被 F-16 之后 fail-closed 的配置加载器判为"无法识别的环境变量"，一次跑出 69 failed——**测量装置污染被测系统**（与 F-21 互为镜像），改名 `QODER_SHUFFLE_SEED` 后干净。那 53 条 `ConfigError` 反过来证明 F-16 的 fail-closed 边界有效 |
| F-33 | P2（守卫失效类） | **三轮乱序里唯一稳定出现的 `XPASS` 是一根反向的守卫**：`tests/domain/test_symbol_chains.py::test_protocol_chain_agrees_on_000300` 挂着 `xfail(strict=False)`，理由写着"协议链自建旧启发式、属本任务禁改域、待 protocol 同批修复"。而 `std7709.infer_market` 现在直接委派 canonical `domain.symbol.to_tdx_market`（`tstdx/protocol/parsers/_std7709_common.py:132-149`，docstring 明写"000xxx 歧义不再由协议层自建启发式裁决"），五链早已收敛、该断言实际在过。`strict=False` 使"标记过期"零成本滞留，后果是**协议链一旦回退到旧惯例就重新变成 xfail（预期失败）**——一个本应报警的回归被过期标记自动消音 | **已清偿**（2026-09-19）：先读实现确认是"缺陷已修"而非"断言变弱"，再删标记使该断言成为常态守卫（回归即红）。**复测**：`tests/domain/test_symbol_chains.py` 24 项 RC=0，`ruff check`/`format --check` 干净 |
| F-34 | P2（口径类：事实文档带假数字，而数字门禁只读 README） | **`docs/ARCHITECTURE.md` 开篇声明"本文只描述代码现状"，正文却留着两条与代码矛盾的现状**（Phase 5 第 14 步实测）：① 防回潮守卫条写着 `test_namespace_layout.py`「根级白名单 **11** 项」，而该测试的 `ROOT_WHITELIST` 与磁盘上的 `tstdx/*.py` 都是 **10**（Phase 3C 的验收上限正是 ≤10，多出的那 1 项是被迁走的模块，代码改了文档没跟）；② F-15 门禁基线条写着「覆盖率：**离线实测 76.14%** … 低于 77 阈值 ⇒ **门禁在本地为红**」，那是 Phase 3C/4 期间的读数；其后的配置接线、旁路收口与传输层桩层解散（F-30）把读数抬过了阈值，本轮离线全量实测 **80.53%**、同轮日志明写 `Required test coverage of 77.0% reached`，文档仍在宣称一条已经不存在的红。**根因是门禁的读数对象**：数字一致性检查 `test_readme_numbers_match_runtime` 只读 `_readme()`，于是 ARCHITECTURE 的 172 capabilities / 85 命令 / 61 解析器 / 5 段配置全部无人对账，README 自己的「85 命令账本」「61 精确解析器」同样在盲区——与第 12 步 F-31 同一格失明（文档门禁只看结构与路径，不看散文里的规模断言），只是这次落在另一份事实文档上 | **已清偿**（2026-09-19）：① 白名单条改回 10 项；② F-15 覆盖率条**删掉百分比**，改判据口径为「离线全量（CI 等价范围）已越过阈值 ⇒ 本地为绿」，并写明**逐轮实测数字一律记在本文的步骤日志里**——把会随每轮改动漂移的读数抄进事实文档，就是在制造下一条 F-34；「阈值单源 `pyproject [tool.coverage.report] fail_under`」「CI 环境（ubuntu+py3.11）重钉仍待 push 后实测」「阈值一次都没下调」三句原样保留；③ **数字门禁由 README 扩展到事实文档全体**：`test_fact_doc_numbers_match_their_truth_source` 以「文档 / 事实 / 定位模式 / 真相源」表把 README + ARCHITECTURE 的 capability 数、命令账本、解析器数、配置段数、根级白名单逐个钉回运行期对象（`Client.capabilities()`、`protocol.commands.COMMANDS`、`protocol.registry.PARSERS`、`dataclasses.fields(Config)`、磁盘 `tstdx/*.py` 计数），`test_documented_http_source_floor_still_holds` 对「45+ HTTP 源」按下界语义判定（加源不必改文档，掉到界下必须改；真相源为 `tstdx/web/` 里按 AST 数出的 `*Source` 类，实测 73）。**变异验证 10 条全部 RC=1 且各自指名**：172→167、85→84、61→62、5→6、10→11、删白名单宣称、README 85→86、README 61→60、下界 45→90、删下界宣称（README 两条因同一数字在文中出现多次，报出的是 `[85, 86]` 这种自相矛盾集合）。**复测（本机 Windows+py3.12.13，同一轮日志）**：`tests/architecture` 全绿；离线全量 `-m "not network"` **3285 passed / 7 skipped / 10 deselected**、0 失败；整仓 `--cov=tstdx` **80.53%**（阈值 77 未下调）；`ruff check`（`tstdx/`+`tests/`+`scripts/`）与 `ruff format --check`（触及文件）、`mypy`（CI 参数）、originality `--strict`（`Total: 189 Suspicious: 0`）、`spec_audit --json --strict`（`coverage_pct: 100.0`）、reachability `--strict`、docs links（82 文件）均 RC=0 |
| F-35 | P2（口径类：门禁只覆盖两份文档、清单只核条数、代码注释里的同一数字无人管） | 第 14 步把数字门禁铺到 README+ARCHITECTURE 后，同一批事实在 `docs/api/`、`quickstart`、`troubleshooting`、`cookbook` 里仍是盲区，且清单类宣称只核条数。铺满后抓出三处（Phase 5 第 15 步实测）：① **服务面过度承诺**——`docs/api/interfaces.md` 写"四个服务面全部委托同一个 `Client`，不存在第二套执行路径"，而 CLI 实有 6 个传输/诊断命令（`probe`/`goods`/`f10`/`blocks`/`list`/`quotes-snapshot`）直连传输层客户端，`docs/api/README.md` 的 CLI 行同病；② **一个错数在代码里繁殖**——盘中异动 `CHANGE_TYPES` 有 20 项、`tests/web/test_hot_rank.py` 早已断言 `len(et)==20`，文档与 6 处生产 docstring/注释却集体写着"16 类"（枚举扩容时只改了测试那一侧，注释与文档按 F-34 一样静默失真）；③ **清单只数个数**——`Domain Record` 族与 WS JSON-RPC 方法在文档里以 `A/B/C…` 公示名单，条数对上而名字漂移时用户照抄即得 `-32601` 或不存在的 Record，原门禁对此完全无感。**根因与 F-34 同格**：门禁的覆盖对象与断言强度，而非文档写作态度 | **已清偿**（2026-09-19）：① 两处服务面口径按事实改写为"数据命令全部委托 `Client`；6 个传输/诊断命令直连传输层，属协议诊断面而非第二套能力执行路径"，并指名口径来源（`runtime_commands.py` 模块 docstring）与守卫（`test_service_faces_never_import_the_web_layer`）；② 枚举数字 9 处统一到 20，新增 `test_code_comments_about_change_types_match_the_enum` 扫 `tstdx/` 全部含"异动"的行、把 `N 类` 钉回 `CHANGE_TYPES`——生产代码的注释首次进入事实门禁；③ `_EXACT_CLAIMS` 由 10 行扩到 **22 行**（新增 api/README 6 项、interfaces 5 项、quickstart/troubleshooting/cookbook 各 1 项），`_FLOOR_CLAIMS` 3 行（`45+ HTTP 源`×2 实际 73、`60+ 契约` 实际 63），并加两条**集合级**名单守卫：Record 名单比 `domain.records.__all__` 去 `Record` 词缀后的集合，WS 方法名单比 `runtime_ws._dispatch` 的 AST 提取结果（`method ==` 与 `method in {...}` 两种写法都要认——初版只认 `==` 数出 9 个，差点把正确的文档改错）；④ "11 领域基类"因分母含糊（直接子类 10 / 去重基类名 11）**刻意不钉**并在此登记，不制造下一条伪事实。**复测（同一轮日志）**：`tests/architecture/` 146 passed；离线全量 junit `3313 tests / 0 failures / 0 errors / 7 skipped`、RC=0；整仓 `--cov=tstdx` 80.54%（阈值 77 未下调）；`ruff check` + `format --check`（430 files）、`mypy`（CI 参数）、originality `--strict`（`Total: 189 Suspicious: 0`）、`spec_audit --json --strict`、`golden_audit --gate --require-markets`、reachability `--strict`、docs links（82 文件）均 RC=0。**变异验证 20 条全部 RC=1 且各自指名**：17 条文档侧（含清单里塞幽灵方法名 `runtime.ping`、幽灵 Record 名 `Warrants`、删清单宣称、契约下界 60→70），3 条枚举侧（两处生产注释 20→16、api/README 20→19） |
| F-36 | P1（发布链路类，与 F-24 同形） | **wheel 冒烟 import 了一个已被物理删除的模块，而所有路径类守卫看不见它**（Phase 5 第 16 步实测）：`scripts/build_package.py` 的 wheel 安装探针仍执行 `from tstdx.facade import UnifiedQuoteAPI;`，而 `tstdx/facade.py` 早已随 Phase 1b 单内核化删除（磁盘无此文件、`tstdx/__init__.py` 也不导出）。这段 import 住在 `python -I -c "<probe>"` 的**字符串**里 ⇒ F-24 补的三类存在性守卫（按文件路径对账 workflow / Makefile / `gates:`）结构性扫不到它，AST 与 import 门禁同理。后果：`make build`（即 `build_package.py --smoke`）与 wheels job 的最后一步会在装好的干净 venv 里 ImportError——发布链路固定为红，与 F-24 那条"release job 引用已删模块"同形，只是这次藏在字符串里 | **已清偿**（2026-09-19）：① 探针改判 `from tstdx import Client;` + `assert callable(Client.call) and callable(Client.typed)`——把"唯一业务入口的通用面在 wheel 内可用"这条真实契约补进去，而不是删掉了事；② **补字符串 import 守卫** `test_release_smoke_imports_only_symbols_that_still_exist`：正则抽出 `build_package.py` 与 `.github/workflows/wheels.yml` 两处冒烟里的全部 `from tstdx…` / `import tstdx…`，逐个过 `importlib.util.find_spec` 与 `hasattr`，并断言"解析结果非空"以免守卫自身失明；③ **变异验证**：把那行 facade import 原样塞回探针 ⇒ 守卫报 `发布冒烟 import 了不存在的模块：['tstdx.facade']`，还原后 `tests/compatibility/test_local_smoke_hardening_contract.py` 2 项 RC=0；④ **本轮把该冒烟真正跑通**（全离线：`python -m build --wheel --no-isolation` 出 `tstdx-1.0.0-py3-none-any.whl` → `_smoke()` 建临时 venv 装它）：`SMOKE_RC=0`，池层 `__module__` 断言（F-30 改判后）逐条通过、`tstdx --help` 与 `tstdx hosts audit --help` 均退出 0、`pip check` 回 `No broken requirements found`；wheel 内 194 个成员零 `facade`、零已解散的四个整方法侧车（余 8 件局部包装 `_hardening` 见 F-30 尾注） |
| F-37 | **P0**（链路贯通类） | **真实网络冒烟抓到：7709 K 线在当下可达主站上返回 2 字节空桩，而库把它读成成功**（Phase 5 第 16 步实测，2026-09-19 周六 09:14–09:19 北京时间）。`tstdx server-test` 读数 **3/8 主站可达**（180.153.18.170 23.8ms、218.6.170.47 25.1ms、123.125.108.14 31.6ms），而全部 golden 样本的采集主机 218.75.126.9 超时不可达。同一批可达主站上 `0x0530` 实时行情正常（600519 price=1257.12、000001 price=11.7），`0x052D` 却一律回 `zip_size=2 unzip_size=2 payload=2 字节 = 2003`（帧头 `b1cb74000c01000000002d0502000200`）——**请求字节与 `tests/golden/quotation/0x052d_security_bars_600000_cat4/20260831-125353/meta.yaml` 记录的 `body_hex`（26 字节 `01003630303030300400010000000a0000000000000000000000`）逐位相同，而那次同字节请求在 2026-08-31 拿到 180 字节 / 10 根真样本**。周期枚举 0…11 全 12 个 category、日线/1分/5分、SH/SZ 两市一律 0 根 ⇒ 与请求参数无关。三层后果：① `Client.bars()` 返回 `data=[]` 而 `provenance=ProvenanceKind.DIRECT / cache_tier=None`，`strict=True` 也不报错，即"空即成功"，与 `0x0537`/`0x0fc5` 的 `NotImplementedFeature`、`0x0fb4`/`0x051a` 的 `CommandOffline` 两条 fail-fast 口径自相矛盾；② 同批真机上 `capital_changes`(0x000f) 与 `finance_info`(0x0010) 分别回 250/37 行但字段错位（首行 `code='519\x01'`、`market=48` 即 ASCII `'0'`），而离线 `-k "capital or finance or bars or kline"` 本轮 RC=0 ⇒ 解析器与**归档样本**自洽、与**当下线路**不一致；③ `PROTOCOL_SPEC/7709/0x052D_SECURITY_BARS.yaml` 与解析器注释都标 "L1 精确解析，golden-verified"，而这条"真机"证据链实际只挂在单台主站上 | **未清偿——需用户裁决，本轮不猜协议**：① 按仓内既有口径（`parsers/mac.py`「差分基准待真机样本裁决（P1c，禁止盲改）」）**未做任何 body/header 试探性改写**，本轮只把事实钉进本文，并停止把 0x052D 记为"live 已验证"；② 时段口径要如实标注：本轮为周六休市，行情读到的是周五收盘快照（腾讯源 `time='20260918161427'`），**历史 K 线与交易时段无关**故 0 根仍成立，但严格结论需一次工作日盘中复跑；③ 三条待选路径由用户拍板：(a) 对当下可达主站重采 K 线/股本/财务样本并按需扩 golden 与 `PROTOCOL_SPEC`（多主站对账，需至少一台提供历史的服务）；(b) 先补"空结果不得读成成功"的运行期口径（0 根时 `strict=True` 抛错、默认给告警与不完整标记）——属产品契约改动，须与 F-13/F-16 的"不静默"口径一并定；(c) 接受"本次发布不含 7709 K 线 live 保证"，在 README/CHANGELOG 显式降级该能力口径，并把 tag 推迟到 (a) 或 (b) 落地 |
| F-38 | P1（测量口径类） | **整个 7709 数据面在门禁里没有任何 live 判据，所以 F-37 这类缺陷结构性隐形**：全库 `@pytest.mark.network` 仅 6 项，且逐文件 grep 显示它们全部落在 `tests/web/*`——协议层（`tstdx/protocol`、`tstdx/client`、`tstdx/transport`）**零 live 覆盖**；`live-smoke.yml` 每日 01:00 UTC 跑的就是这 6 项 web 用例。`tstdx/tools/host_audit.py` 名义上巡检 5 个协议族，但该模块 grep `security_bars\|bars` 零命中 ⇒ 巡检不看 K 线。于是"主站可达 + 行情能回"被隐式当成"链路通"，历史族命令的真实可用性只在本轮那次一次性人工冒烟里被碰过 | **部分清偿**（2026-09-19）：本轮把"三面 + 真实网络"写成可复现的七格判据与逐格证据（Phase 5 第 16 步），使这条验收不再是口头动作；**刻意未**新增 K 线的 `@pytest.mark.network` 断言——F-37 未裁决前加它，等于把每日 live job 钉成固定红（F-24 的教训正是"守卫把缺陷写成契约"）。待 F-37 选 (a)/(b) 落地后再补 live 断言并挂进 `live-smoke.yml` |
| F-39 | P2（口径类：对外契约数的分母由手抄名单决定，而名单已被自身判据遮蔽） | `scripts/contract_audit.py::_all_typed_queries` 用一份 12 个名字的 `skip` 集合排除抽象基类，同一份名单在 `tests/v14/test_contract_automation.py` 里又各自抄了 4 遍（共 5 份副本）。实测这 12 个类在四种构造路径（`cls()`、`_minimal_instance`、kwargs 阶梯、factory 映射）下**全部构造失败**，即它们的排除早已由各站点自带的 `except Exception: continue` 完成——名单不决定任何东西，却决定读者的信任。**危害方向是漏更而非多余**：新增一个抽象基类只要不在这 5 份名单里，就会被算进"契约数"，于是 `60+ 契约`、`10 领域基类`、PyPI 描述里的规模口径同时虚增，而审计依旧全绿（与 F-19"跨族同号互相覆盖使分母被读小"同族，这次是被读大）。连带：`docs/api/README.md`、`docs/api/interfaces.md`、README 两处宣称的"11 领域基类"按任何自然定义都不成立（`typed_query` 里被继承的抽象基类是 10 个，含根 `CapabilityQuery` 才 11 个，而根不是"领域"）——第 15 步 F-35 曾以"分母含糊"为由刻意不钉，本步给出显式判据后改判为必须钉 | **已清偿**（2026-09-19）：① 契约分母改为**纯结构判据**（可构造 + `capability` 为字符串），删掉 5 份手抄名单；`contract_audit --ci` 同轮实测 `contracts: 63 / business caps: 155`，与删名单前逐项相同 ⇒ 改动行为无损；② README（2 处）+ `docs/api/README.md` + `docs/api/interfaces.md` 的"11 领域基类"统一改为 **10**，判据写进 `_typed_domain_base_names()` 的 docstring（被其它契约直接继承的抽象 dataclass，不含根）；③ 三条新守卫：`test_fact_doc_numbers_match_their_truth_source` 新增 4 行（三处领域基类数 + README 的"9 Domain Record 族"，后者此前只在 api 文档被钉、README 的两处抄本在盲区）、`test_contract_audit_docstring_numbers_match_the_audit` 把审计脚本自述的 155/63 钉回它自己算出的数（F-25 只对齐了它跑什么，没对齐它抄什么）、`test_typed_query_denominator_is_not_a_hand_copied_list` 禁止名单回潮（名单里任何一个名字以字符串字面量出现在这两个文件即为红）；④ 顺手修掉该套件里一处测量装置缺陷：`test_cli_script_exits_zero` 以 `text=True` 捕获子进程输出却不指定编码，Windows 下子进程的 GBK 输出使 `_readerthread` 抛 `UnicodeDecodeError`（以 `PytestUnhandledThreadExceptionWarning` 形式滞留，且失败分支的 `result.stdout[-800:]` 会因 `stdout is None` 二次崩）——现显式 `PYTHONIOENCODING=utf-8` + `encoding="utf-8"`，警告消失。**复测（同一轮日志）**：`tests/architecture/` + `tests/v14/` junit `211 tests / 0 failures` RC=0；`contract_audit --ci` RC=0（`63 契约 / 155 业务 capability`，与删名单前逐项相同）；`ruff check`/`format --check`、reachability `--strict`、`spec_audit --json --strict`、docs links（82 文件）均 RC=0；整仓离线 junit `3313 tests / 0 failures / 7 skipped`、`--cov=tstdx` **80.52%**（阈值 77 未下调）。**变异验证 8 条全部 RC=1 且各自指名**（塞回一份 `skip = {"MarketDataQuery"}`、自述 155→150、63→62、三处领域基类各改 1、README Record 族 9→8） |
| F-40 | P1（零缓存口径类） | **缓存层删掉了，"缓存形状"留在包里，而且其中一个真的能跳过数据源**（Phase 5 第 18 步实测）。Phase 2 物理删除 v12 缓存层后，仓内还剩三处缓存遗产：① `tstdx/domain/finance.py::CapitalChangeCache`——除权事件 **TTL 缓存 + `~/.tstdx/factors` 落盘**，类 docstring 明写"命中（未过期且非空）直接返回，**跳过 0x0010/gpcw 网络与解析**"，即一句话就能把"数据请求不需要缓存"这条主口径变成可一键恢复的旁路；而它在 `tstdx/` 内**零调用方**（复权引擎 `domain/adjust.py::compute_factors(bars, events)` 由调用方直接喂事件），只有它自己的 8 项单测在测自己——典型的"测试维持死亡的公共面"；② `Provenance.cached(tier)` / `cache_hit` / `direct_fetch`：生产路径永不产出 tier（内核只经 `Provenance.direct()` 构造），故这三个成员在 `tstdx/` 里同样零消费者，唯一引用是 `tests/runtime/test_query_contracts.py::test_cache_hit_preserves_direct_origin`（自己造一个 tier 再断言它能被造出来）；③ `tstdx/result.py` 模块 docstring 仍在描述 `cache_tier='l1'/'l2'` 的取回模型，并称其为"later cache-poisoning and freshness gates 的地基"——**包内文档描述了一个不存在的层**，`help()` 与任何交互式阅读都会照单全收。**根因与 F-30/F-34 同格**：可达性门禁把 `__all__` 导出与"有测试覆盖"都算活，于是删层之后剩下的接口形状恰好躲过所有判据 | **已清偿**（2026-09-19）：① `CapitalChangeCache` 一族**物理删除**（`CapitalChangeCache`/`get_capital_change_cache`/`default_factor_cache_dir`/`ENV_FACTOR_CACHE_DIR`/`DEFAULT_FACTOR_TTL_SECONDS`/`_FETCHED_AT_KEY` 及其 `__all__` 条目，`finance.py` 301→162 行，连带删除那 8 项自测用例），不留别名，与 Phase 1b/2 的 clean-break 口径一致；② 删除 `cached()`/`cache_hit`/`direct_fetch`（`result.py` 161→148 行），**`cache_tier` 字段刻意保留**——它是三面 wire 上那个恒为 `null` 的零缓存证据，CLI/HTTP/MCP 冒烟与文档都以它作判据；③ `result.py` docstring 改写为当下事实：运行期不做结果缓存，所有生产 provenance 由 `direct()` 构造并带 `cache_tier=None`，保留该字段正是为了让调用方断言它仍为 `None`；④ 那项自测换成 `test_direct_provenance_carries_no_cache_tier`——除断言 `direct()` 的 `cache_tier is None`，还用 `hasattr` 反向钉住三个已删词汇，重新引入即红；⑤ 新增包级守卫 `tests/architecture/test_official_runtime_no_fallback.py::test_package_defines_no_data_cache_layer`：AST 扫 `tstdx/**` 全部类名含 `cache` 的类定义与 `get_*cache*` 函数，命中即列出路径。**变异验证**：临时塞入 `class QuoteCache` → 守卫报 `tstdx\domain\finance.py:class QuoteCache`、RC=1。纯函数记忆化（`functools.lru_cache`）明确不在禁止之列——它不省掉任何一次网络请求，守卫 docstring 里写明了这条边界 |
| F-41 | P1（对外承诺类） | **PyPI 元数据仍在宣称一个已被删除的架构层**：`pyproject.toml` 的 `description` 写着 "…with explicit Providers, **semantic caching**, Stateful streaming and canonical HTTP/WebSocket/MCP adapters"。这是 v12 语义缓存时代的残留，会出现在 `pip show`、PyPI 项目页与任何索引站的第一行——**对外最显眼的一句话恰好是仓内最错的一句**，而它不在任何事实门禁的扫描范围里（`FACT_DOC_PATHS` 只覆盖 README/ARCHITECTURE/docs，元数据文件从来不在名单上），`tests/` 里也没有任何断言读过 `description` | **已清偿**（2026-09-19）：① 描述改为 "…with explicit Providers, **direct Provider reads**, Stateful streaming…"——刻意**不提缓存**（连 "zero-cache" 这种写法也不用：包描述不该为一个不存在的东西占词，守卫也因此能保持"描述里出现 `cach` 即红"这个简单形状）；② 新增守卫 `test_pypi_description_claims_no_caching`：正则取出 `[project] description`（解析不到即报"守卫自身失效"），断言不含 `cach`。**变异验证**：把 `semantic caching` 塞回描述 → RC=1 且整句回显。同一轮顺带改写 `README.md` 的存储行（`~/.tstdx/` 配置/**缓存**/排名 → 配置/主站排名/反馈）：随 F-40 删掉落盘目录后，`~/.tstdx/` 下只剩配置、`server_ranking.json` 与 `feedback/` 三类，该行此刻正被并行会话编辑，故未并入本次提交 |
| F-42 | P2（口径类：门禁只钉总数与"第一种写法"，同一事实的第二种写法和矩阵每族分列全是手抄本） | **README 的规模数字有一半在门禁外，而协议覆盖矩阵的每族分布 5 行错 4 行、端口错 1 行**（Phase 5 第 19 步实测）。F-34/F-35 把"事实文档的抄本数字"钉回真相源后，钉的是**每张表里已登记的那一种写法**与**总数**：① 协议覆盖矩阵原有一列"精确解析"，逐族写 18 / 12 / 8 / 15 / 8，**和恰为 61**——与真相源总解析器数相同，于是"61 精确解析器"的总数门禁一直绿，而分列全错（真相源 `PARSERS` 的 `(family, code)` 键分组：18 / 15 / 16 / 1 / 11；命令账本 `COMMANDS` 另有其数 39 / 17 / 16 / 2 / 11，和 = 85），且**商品语义端口写反**（文档 7709，`protocol/commands.py::Command.port` 与主站池 `transport/hosts.py:351` 的 GOODS←EXTENDED 派生都是 7727）；② 同一事实换一种写法就脱离表：已钉"45+ HTTP 源"却看不见框图里的"web 45 源"、已钉 `docs/api/README.md` 的"Provider 数"却看不见 README 的"11 Provider · 172 capability"与"172 项 capability"，`CLI 31 子命令`/`10 端点`/`9 工具`/`5 套协议族`/`全 5 族`/`5 族客户端`/`61 × N 族`/`15 便捷方法`/`Client 15 方法`/`28 模块`/`6 源合并`/`251 条精确绑定`（README 三处三种措辞）此前一行未读——其中"6 族""17 模块"已经过期，"45 源"是精确抄本而真源在增长。**根因是表的形状**：行按 `(文档, 事实, 定位模式)` 登记，一个事实写两遍就只钉到第一遍；总数门禁则给分列错误提供了"看起来无害"的掩护 | **已清偿**（2026-09-19）：① 矩阵重写为"命令账本 / 精确解析器"两列并把族键写进每行标签（`**MAC 专属**（\`mac_quotation\`）`）——文档自己声明它指哪一族，测试因此不必再抄一份"显示名→族键"映射（那是 F-39 删掉的名单同形物）；② 新增 `test_readme_protocol_matrix_matches_the_registries`：逐行比 `(端口, 命令数, 解析器数)` 三元组，端口取自 `Command.port` 而非另一份常量表，族集合必须与 `COMMANDS`/`PARSERS` 三方相等（新增协议族不写这行即红），同一族写两行也红；③ 补 20 条 `_EXACT_CLAIMS` 行 + 3 条 `_FLOOR_CLAIMS` 行 + 10 个真相源 helper（`_protocol_families`/`_command_family_counts`/`_parser_family_counts`/`_family_port`/`_web_source_modules`/`_client_methods`/`_error_classes`/`_config_merge_layers`/`_direct_bindings` 等），按"写法"逐行登记而非按事实登记，每行 `assert claimed` 保证写法改名或删除即报"门禁失效"（F-35 同形）；④ README 的过期抄本改回真相源（`parsers(61 × 6 族)`→5 族两处、"17 模块"→28 模块），不可钉的裸抄本改成非数字写法（框图"web 45 源"→"web 多源"），下界写法统一为"45+ 源类/45+ 源"，`docs/api/README.md`"11 源 × channel"→"11 Provider × channel"以消除"源"这个量词的二义性。**变异验证 26 条全部 RED 且各自指名**（矩阵 4：解析器 18→17、MAC 命令 16→8、商品端口 7727→7709、族键 `ex_quotation`→`extended` 报"矩阵 ['extended'] 多、['ex_quotation'] 缺"；下界 3：`45+ 源类`→`45 源类` 报"门禁失效"、`40+ 异常类`→`50+` 报"实际只有 46 个"；精确宣称 16：17 模块 vs 28、170/171 capability、CLI 30、HTTP 12 端点、MCP 12 工具、全 4 族、61×6 族、14 便捷方法、`Client` 16 方法、4 套协议族、5/7 源合并、`85 命令账本（6 协议族）`；绑定条数 3：250/240/252 vs 251）。**复测（同一轮日志）**：主树整仓离线 junit `3334 tests / 24 failures / 0 errors / 7 skipped`、79.42%，24 条红同一个根因 `TypeError: QuerySpec.build() got an unexpected keyword argument 'max_age'`，全部来自并行会话在途的 `tstdx/query.py`，本步未代为修改；按第 18 步做法在 HEAD（`867f6d2`）+ 本步 3 个文件单开 worktree 复跑 junit **3337 / 0 failures / 0 errors / 7 skipped**、`ISO_FULL_RC=0`、覆盖率 **80.51%**（阈值 77 未下调，`3334 + 3 = 3337` 可核对），同树 originality（189 文件 Suspicious 0）/ `spec_audit`（`coverage_pct: 100.0`）/ golden / reachability / `contract_audit --ci`（63 契约 · 155 capability）/ docs links（82 文件）/ ruff + format（430 files）/ `mypy tstdx/` **全部 RC=0**。**尚未钉的剩余写法**（本步实测仍在，属低挥发或无唯一真相源）：README 的"3 Sink 策略""pool(4 槽)""11 步确定性门禁""6 个传输/诊断命令"与"Windows 10/11 · macOS 12+" |
| F-43 | P1（零缓存口径类，与 F-40/F-41 同族：对象是**旋钮与散文**而非代码形状） | **`max_age` 是五张入口都收、内核零消费的新鲜度旋钮，而缓存层删掉后仍有 8 个生产文件与 3 份文档在替它说话**（Phase 5 第 20 步实测）：`QuerySpec.max_age` 带着 `build()` 形参、`max_age < 0` 校验与 normalize 行住在 `tstdx/query.py`，Client（`kwargs.pop("max_age")`）、CLI `--max-age`（3 个子命令 + 3 处透传）、HTTP（5）、WS（3）、MCP（JSON Schema + impl）、`runtime/kernel.py`（16）、`orchestration.py`（4）共 **9 个文件 50 行**把值一路送到 `QueryPlan`，而执行面没有任何一处读它——调用方设置它只会得到"已经生效"的错觉（F-27/F-28 已给 CLI 选项立过同形判据，`QuerySpec` 字段侧一直没有对应门禁）。散文侧同批失真：`QueryFingerprint` docstring 写着 "used by cache/single-flight layers"、`domain/period.py` 写 "and cache lookup"、`config/schema.py` 把"缓存/降级链"列为可配置面、`result.py` 说生产 provenance 由缓存层构造、`__init__.py` 门面宣称缓存能力、`protocol/handshake.py`/`domain/symbol.py` 各一处；文档侧 `docs/providers/README.md` 的 **`### bounded cache` 整节 6 行**在规定 cache key/fingerprint/provenance/cache-hit 语义（描述一个已被物理删除的层，且 §13 CI 清单列着 `cache hit -> …`），`docs/api/interfaces.md` 6 处签名带 `max_age`，README 写"`allow_stale` 显式放行"（它实际恒被拒绝） | **已清偿**（2026-09-19）：① **旋钮整体物理删除**（不留别名）：字段、`build()` 形参、校验、normalize 与 9 个文件的全部透传一并删；新鲜度口径归 `currentness`，执行预算归 `deadline_ms`，二者都在 fingerprint 之内；`allow_stale` 的拒绝文案改为说明**为何**无对象可作用（"过期容忍没有可作用的对象，新鲜度口径请用 currentness"），构造期 ergonomic 折叠不变；② **散文门禁**（新增 `test_production_prose_never_claims_a_data_cache`）：按 AST 扫 `tstdx/` 全部模块/类/函数 docstring 与 `#` 注释里的 `cache|caching|缓存|single-flight` 词根，只放行 10 条**逐条给出口径来源**的形状（英文否定句、"零缓存/不存在…缓存"、`cache_tier` 证明字段、`hq_cache`/`__pycache__` 字面量、`functools` 纯函数记忆化、`RankingStore`/`disk cache` 主站排名、`tools/` 进程内解析复用、"服务端缓存"外部事实），其余一律红；配 `test_pure_function_memoization_claim_is_true` 反向核验"宣称 LRU 记忆化"的文件里真的挂着 `lru_cache`；③ **幻影开关门禁**（`test_every_query_spec_field_is_consumed`）：分母取 `dataclasses.fields(QuerySpec)`，判据是"有没有非 `self` 的属性读取"，豁免表 `_QUERY_SPEC_STORE_ONLY_FIELDS = {options_json}` 由 `test_query_spec_store_only_fields_are_still_reached` 自检（该字段必须仍被 `json.loads` 解码、且 `plan.spec.options` 仍是 executor 输入），两条都带"扫描器零命中即自曝失明"的自我校验；④ **首轮抓到 8 处散文红，全部改写散文而非放宽门禁**：4 处是否定句被换行截断（否定词与 cache 词根不同行），2 处是豁免表缺了合法形状（`live elsewhere`、`服务端缓存`），另删掉一条匹配不到任何真实代码的死豁免；⑤ **变异验证 6 条全部 RC=1 且各自指名**（kernel 反宣称缓存 → 指名 `tstdx/runtime/kernel.py`、撤销"服务端缓存"豁免 → 指名 `adapters_baidu.py`、注入无人消费字段 `phantom_knob` → 报 `['phantom_knob']`、executor 的 `plan.spec.options` 改名 → 报"options 袋不再是执行面输入"、`options_json` 不再解码 → 报"豁免不再成立"、在无 `lru_cache` 的文件宣称 LRU → 报"宣称 LRU 记忆化却没有 lru_cache"），**其中第 3 条先跑成 RC=0，暴露守卫自身的缺陷**：`dict.fromkeys(keys, set[str]())` 让所有字段共享同一个集合，任一字段被读就全体"已被消费"——幻影门禁当时是瞎的，改成推导式后才红；⑥ **复测（同一轮日志）**：守卫文件 9 项 RC=0，整仓离线 `-m "not network"` junit **3341 tests / 0 failures / 0 errors / 5 skipped**、`FULL_RC=0`（与第 19 步孤立 worktree 的 3337 差 4，正是本步新增的 4 条守卫），`--cov=tstdx` **80.59%**（日志明写 `Required test coverage of 77.0% reached`，阈值 77 未下调）；`ruff check`（`tstdx/`+`tests/`+`scripts/`）、`ruff format --check`（457 files）、`mypy`（CI 参数）、originality `--strict`（`Total: 189 Suspicious: 0`）、`spec_audit --json --strict`（`coverage_pct: 100.0`）、`golden_audit --gate --require-markets`、reachability `--strict`（无未登记孤儿 ✓）、`contract_audit --ci`（**63 契约 / 155 业务 capability**，与第 17/19 步逐项相同）、docs links（82 文件）**全部 RC=0**；⑦ **残留 `max_age` 只在两类地方**：`docs/archive/plans/*`（历史方案，按"历史不改写"口径保留）与守卫里指认它的注释/断言字符串。同一格失明在文档面的补集仍在：`docs/providers/*.md` 的 per-provider 承诺长文（`FreshnessViolation` 一类）尚未纳入 `_EXACT_CLAIMS`，见 F-44 |
| F-44 | P1（对外契约类：错误码树里有从未发生的叶子） | **49 个错误类中 7 个叶子没有任何 raise 站点，其中 2 个是文档明文承诺的对外行为**（Phase 5 第 20 步顺带实测，AST 遍历 `tstdx/` 全部 `raise` 目标与类继承树）：真正的基类可以只被继承——`TransportError`（6 个子类全部有 raise）、`StreamError`（`SubscriptionError` 有）、`ProfileError`（`ProfileUndetectable` 有）都属正常；**从未被抛且无被抛子类**的是 `SourceUnavailable(E7050)`、`FreshnessViolation(E4060)`、`UnknownCommand`、`ChecksumMismatch`、`AntiSpiderBlocked`、`BackpressureOverflow`、`GapUnfilledError` 7 个。前两处不只是"没用到"，而是**对外承诺了一个不会发生的行为**：`docs/providers/README.md` §12 写"规范语义：`SourceUnavailable == selected Provider unavailable`"并规定各 Provider 文档统一用它，`tstdx/errors.py:494` 的 docstring 也说"上层可包装成本异常并记录 provider/channel/capability 后结束本次 Query"——而全仓没有一个包装站点（Provider 不可用时用户实际拿到的是 `ConnectionFailed`/`AllHostsUnreachable`/`WebSourceError` 等传输层原异常）；`docs/providers/tdx.md:151` 写"无法证明满足 freshness profile 时严格模式返回 `FreshnessViolation`"，而运行期**没有任何 currentness 校验器**（`currentness` 只是进 plan 的声明口径）。与 F-43 同族（幻影开关），只是这次幻影在异常树上 | **未清偿——需用户裁决，本轮不擅自改对外错误契约**：三条待选路径 (a) 接线：在 Provider 失败路径统一包成 `SourceUnavailable`（context 记 provider/channel/capability），并为 `currentness` 落一个可判据的运行期校验器，无法证明时抛 `FreshnessViolation`；(b) 改口径：把 §12 与 `tdx.md` 的承诺改写为"实际抛点即传输层异常"，并从 `__all__` 与 E 段树里删掉不打算兑现的叶子（clean break，与 F-40/F-41 的删除口径一致）；(c) 折中：显式登记为 taxonomy placeholder，并加门禁禁止文档承诺未接线的错误类。本轮只把事实与分母钉进本文（判据可复算），**未**新增门禁——判据一旦落笔就必须先定 (a)/(b)/(c)，否则会把"未兑现"写成契约（F-24 的教训同形）；该裁决与 F-13/F-16 的"不静默"口径、F-37 的 (b) 属同一次产品决定 |
| F-45 | P1（可观测性类：分页终端把"空"读成"耗尽"，F-37 因此能在一片全绿里隐形） | **`bars()` 首页 0 条与"历史已经取完"共用同一个出口，调用方拿到的是"请求成功、恰好 0 根"**（Phase 5 第 21 步实测）。`tstdx/client/_mixin.py::_t_bars` 的分页循环里，空页出口是一行 `break  # 空页：历史耗尽（正常终止）`，紧随其后的截断判据 `len(bars) < count and drifted` 只在**锚点漂移**时成立——空首页因此既走不到告警、也走不到 `strict` 抛错，三张服务面同样看不见任何异常。F-37 在线冒烟实测到的现场正是这一形状：主站对 `0x052D` 只回 2 字节 `count=0` 空桩。与 F-43/F-44 同族（幻影开关、幻影叶子），但这一格更严重：**幻影在这里以"成功"的面目出现**，离线门禁的判据（"有返回、无异常"）永远满足，于是结构性失明不是缺一条断言而是缺一个区分。`_t_export_security_list` 的 `0x044D` 同形缺陷一并实测在案（首页 0 条 ⇒ 读成"这个市场没有证券"，而 0/1 两个市场必有数千标的）。一项旧测试 `test_client_f1.py::test_empty_first_page_no_warning` 用 `warnings.simplefilter("error")` 断言"空首页不许留痕"，**把缺陷本身钉成了契约** | **已清偿**（2026-09-19，`44b02fb`）：① 判据改为**耗尽只会表现为短页或次页空**，首页空响应是另一件事（该标的无此周期历史，或主站对这个命令只回空桩）；② **默认只加可观测性、不改数据**——首页空 → `UserWarning` 且返回仍是空序列（与旧行为逐字节一致），`strict=True` → `TruncatedDataError`（与既有漂移截断同级），`_t_export_security_list` 接同一判据；③ 那条把缺陷写成契约的测试改判为 `test_empty_first_page_warns`，理由写进 docstring，另新增离线回归 4 项，其中"次页空仍须静默"用 `simplefilter("error")` **反向**钉住，防止这次改动把正常耗尽一并变成噪声；④ `docs/errors.md` 的 `TruncatedDataError` 条目列出 `_mixin.py` 全部抛出点，**刻意不写条数**（F-42 的教训：数量抄本就是下一个过期点）；⑤ 变异验证 2 条（`empty_first_page = not seen`→`= False`、`if not out:`→`if False:`）各自行列失败——把判据退回静默路径必红。合树复测数字见 §1 第 21 步条目。**两个未闭合点如实登记**：`QueryResult` 没有 warnings 通道，这条告警只活在调用方进程 stderr，HTTP/WS/MCP 三面的 wire 里看不见"本次结果为首页空桩"；唯一业务入口 `Client.bars()` 没有 `strict` 形参（`strict` 只存在于 `TdxClient.bars`），经内核绑定的路径拿不到"必须完整"的语义。两处都要改 `tstdx/client/api.py` 与 `tstdx/result.py`，第 21 步落地时这两个文件正被并行会话编辑，故本步未代为决定——与 F-44 不同，它们是**内部形状**而非对外承诺，接线不引入新的对外契约，可与 F-18 一并处置 |

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

1. 全量门禁：`pytest` 0 failed（含单跑随机序 `-p no:randomly` 抽检 ⚠️ **该口径当场写反，
   见 F-32**：`-p no:randomly` 是**关闭**随机化，且本仓从未声明该插件，这项抽检无实现手段）、
   ruff 0、mypy 0、
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

7. ✅ **声明面 ↔ 执行面三面贯通审计（Phase 5 第 7 步，2026-09-19，见 §0.3 F-26）**：
   回答"主体链路是否全部贯通"不能靠散文，需要一个每次构造执行器都会跑的机器判据。

   - 原判据是 `MIGRATED_BINDINGS ⊆ DIRECT_BINDINGS`，而目录由绑定表生成 ⇒ 同向缩小不可见；
     Provider 注册表（`PROVIDERS.*.channels[].capabilities`）是全仓**唯一独立书写**的
     "对外声明"，此前只在离线测试里对账（`test_v13_architecture_alignment.py:50`），
     运行期判据里没有它。现改为三面：目录 ⊆ 绑定、声明 − 绑定 = ∅、绑定 − 声明 = ∅。
   - **分母选择的教训**：`Client.capabilities()` 看着像"对外面"，但它由 `MIGRATED_CAPABILITIES`
     推导，与绑定表同源——拿它当分母等于左手查右手；同时 catalog 惰性 import 入口层会在
     构造执行器时反向拉起 `Client`。公共面一致性因此下沉到测试层断言。
   - **现状数字**（本机运行期实测，非文档抄录）：`migrated 229 ⊆ executable 251 = declared
     251`，双向差集为空；注册表 capability 名并集 == `Client.capabilities()` == **172**。
     这构成"11 个 Provider 的每条声明都有唯一执行路径、每条执行路径都受声明约束"的结论，
     边界同样写清：绑定存在 ≠ 运行期正确，后者仍靠 golden / adversarial 与未获准的真实网络冒烟。
   - **复测**：`tests/provider_isolation`（含本守卫 7 项）、`tests/runtime`、
     `tests/architecture` 均 RC=0；`mypy tstdx/catalog/capability_audit.py` RC=0；
     `ruff check` / `format --check` 干净（430 files already formatted）；originality
     `Total: 193 / Suspicious: 0`、spec `coverage_pct: 100.0`、golden、reachability
     `--strict`、adversarial+bridges、benchmark smoke、docs-code、docs links（82 文件）
     逐条 RC=0；离线全量套件 **3255 项 / 0 失败 / 7 跳过**，覆盖率 **78.82%**
     （阈值 77 未动）。三次变异（丢目录内绑定 / 丢目录外绑定 / 加幽灵绑定）分别命中
     三条不同错误消息。

8. ✅ **CLI 连接参数 ↔ 配置面对齐（Phase 5 第 8 步，2026-09-19，见 §0.3 F-27）**：
   第 7 步证明"声明 ↔ 执行"闭合之后，这一步查的是另一条容易被忽略的边——**用户当面
   说过的话**（命令行选项）有没有走到执行面。答案是四项都在静默丢失（详见 F-27 行）。

   - **判据不是"数字对不对"，而是"谁有优先权"**。改前 `--timeout` 的字面 5.0 与
     `[core] timeout` 的默认 5.0 数值相同，因此任何默认状态下的手工测试都看不出问题；
     只有真去改配置文件的人才撞得上——这类缺陷的通用形状就是"CLI 自带默认值 = 静默否决
     配置面"。修法是让 CLI 只在用户显式说话时发言：`_client_kwargs` 对未指定的项交 `None`
     （内核读配置），`_transport_kwargs` 对内核外自建的传输客户端就地复现同一条优先级
     规则（与 `runtime/kernel.py:62-67` 同构）。**没有**为此把 CLI 塞进内核：那会让
     适配层反过来依赖被适配者。
   - **一族两式的取舍**：`goods`/`f10` 与 `probe`/`blocks` 同为"内核外传输客户端"，却
     只统一 timeout 不统一 hosts——`[hosts] servers` 里的条目是 7709 标准族主站，喂给
     goods/F10 协议客户端是错的（会把请求指到不应答的端口）。这类"看着对称、实际不对称"
     的点写进注释与助手 docstring（`_transport_kwargs` 明写"仅 TDX 标准族"），避免后来者
     顺手统一。
   - **两道门禁**：`tests/architecture/test_cli_connection_contract.py` 用 fake `Client`
     捕获真实构造参数（`Client` 在 `runtime_commands` 与 `tstdx.client.api` 两处都要换，
     因为 `_ClientRows` 在 `__init__` 内 import），除逐命令的 host/timeout 转发的正反面
     外，加一条**结构性守卫**：凡 parser 声明了 `--host`/`--timeout`、又不属诊断白名单
     （`hosts`/`server-test`：要遍历候选池，本就不该吃 `[core] timeout`）的命令，其
     handler 源码必须出现三个助手之一——这条不看具体命令，故对**新增**命令同样有效。
     另一道 `test_doc_code_consistency.py::test_every_documented_cli_example_parses`
     把活文档（围栏块 + 行内代码）里以 CLI 程序名开头的命令行逐条过真实 parser，含 `<>`/`[…]` 的
     用法语法行豁免。
   - **门禁上线即抓到 4 条已失效命令**（都是本轮之前就存在的缺陷，非本次引入）：README
     两处 `hosts audit --hosts-file …`（组级选项必须前置于 `audit`）、
     `docs/api/interfaces.md` 的 `--family all`（非法取值，默认即全 5 族）、
     `docs/api/README.md` `margin` 那一行缺必填位置参数 `symbol`。一律按真实语法
     改正，**未放宽门禁**；同时改掉 README 里 `serve` 的 `--host` → `--bind`。
   - **变异验证**：把 `cmd_quotes` 退回裸 `Client()` ⇒ 三条断言同时命中
     （`KeyError: 'hosts'` ×2 + 结构性守卫报出 `['quotes']`），原样还原后复绿；
     `git diff --stat` 与变异前一致，无残留。**该守卫随后在第 9 步被推广为全量选项消费
     审计**（F-28），此处按推广前的口径记录。
   - **复测**（本机 Windows/py3.12，同一轮日志）：`ruff check` / `format --check` RC=0，
     `mypy tstdx/`（CI 参数）RC=0，originality `Total: 193 / Suspicious: 0`，
     spec `coverage_pct: 100.0`，reachability `--strict` RC=0（记录缺陷 0），
     golden / contract_audit `--ci` / benchmark smoke / docs links（82 文件）逐条 RC=0；
     离线全量套件 `-m "not network"` **3276 项 / 0 失败 / 0 错误 / 7 跳过**，
     覆盖率 **78.83%**（阈值 77 未动）。

9. ✅ **CLI 全量选项消费审计（Phase 5 第 9 步，2026-09-19，见 §0.3 F-28）**：第 8 步的
   守卫按参数名白名单工作（`--host`/`--timeout`），这本身就是一处自我设限——它证明的是
   "我检查过的这几项没问题"，不是"没有同类缺陷"。把判据换成**与选项名无关**的形式：
   parser 声明的每个 dest 都必须出现在 handler 源码或四个点名助手里。

   - 全量扫描 31 个命令的实测命中项只有一个：`stream --max-queue` 声明后从未转发给
     `QuoteStream.subscribe()`，被库侧同名默认值接管——与 F-27 完全同形（CLI 字面默认与
     库默认同为 1024，只有主动调小背压上限的用户会撞上线）。补转发并在单元测试里断言实到。
   - **两道收紧**：守卫的豁免面从"整个 `tstdx/cli/_common.py` 源码"改为逐个点名的四个
     助手（否则任意一处 `args.x` 会给所有命令开绿灯）；单元测试的 stream 假对象签名改为
     与真实 `subscribe` 一致（原假签名恰好缺 `max_queue`，替身比生产接口更窄，是这类幻影
     参数能长期存活的直接原因）。
   - **变异验证**：删掉 `max_queue=args.max_queue` ⇒ 守卫报出
     `stream: --max-queue (dest=max_queue)`；还原后 `tests/architecture` +
     `tests/unit` + `tests/runtime` RC=0。

10. ✅ **CLI 最后两条旁路命令收口（Phase 5 第 10 步，2026-09-19，见 §0.3 F-29）**：
    第 9 步证明"选项是否被消费"有守卫，却没有回答"命令是否走内核"。逐模块排查 CLI 的
    执行入口后，`changes` 与 `hot` 是仅剩的两条直接调用 `WebQuoteSession` 静态方法的数据
    命令——它们绕开的正是本轮反复收紧的那条链（信封、provenance、`validate_call`、F-27 的
    连接参数助手）。

    - 两命令改为 `_ClientRows(**_client_kwargs(args))` 下的 `stock_changes` / `hot_rank`
      capability 调用。二者本就各有 `web_session` 绑定，故**执行路径不变、只删掉旁路**：
      离线 stub 数据源后对拍，返回行一致，provenance 为 `ProvenanceKind.DIRECT`
      （`derived`/`catalog`），`--types` 解析结果 `(8201, 8193)` 与 `page/size` 原样到达源。
    - **守卫与命令解耦**：`test_service_faces_never_import_the_web_layer` 用 AST 扫四个服务面
      （`tstdx/cli/**.py` + `tstdx/integration/**.py`）的全部 import，出现 `tstdx.web` 即红；
      另有一条逐命令断言确认二者以 capability 名调用 `Client`。前者对新增命令同样有效。
    - **变异验证**：把 `changes` 改回直接调用 ⇒ 两条守卫分别报出
      `runtime_commands.py: import tstdx.web.session` 与 `recorder.kwargs is None`（RC=1），
      还原后复绿。
    - **复测**（本机 Windows，同一日志）：`ruff check` / `ruff format --check`、`mypy tstdx/`
      （CI 参数）、originality `--strict`、spec 100.0%、golden、reachability `--strict`、
      contract_audit `--ci`、docs links、benchmark smoke 全 RC=0；离线全量套件
      `-m "not network"` **3280 项 / 0 失败 / 0 错误 / 7 跳过**，同轮 `--cov=tstdx` 实测
      `Total coverage: 80.53%`、`Required test coverage of 77.0% reached`（阈值 77 未动；
      高于第 8/9 步记录的 78.83% / 78.09%，差异来源未做归因——工作区此刻含其他会话的
      transport 改动，重钉仍以 CI 环境实测为准）。
11. ✅ **传输层"整方法桩层"解散（Phase 5 第 11 步，2026-09-19，见 §0.3 F-30）**：
    第 10 步那条 80.53% 的读数是**记账假象修正前**的最后一版——两个连接池的类体里放着
    永不执行的原件，执行的是侧车拷贝，于是 `pool.py` 46.2% / `async_.py` 44.7% 的缺口
    不是"测试不够"，而是"被测代码不在那里"。用户就此拍板 **搬回类体，解散桩层**。

    - **一次搬完两侧**：同步池与异步池的 `request`/`request_multi`/`iter_frames`/
      `update_hosts`/心跳/`close` 全部回到 owning 类体；4 个侧车（合计 1 447 行）连同
      被遮蔽的原件一起删除，提交 `9e5734a` 净减 **1 034 行**（+1 012 / −2 046）；
      主站代际发布的三条共享原语上收 `hosts.py`，"两份事实源"归一。
    - **等价性判定不靠目测**：写一次性 AST 对拍工具，把侧车原文（`git show HEAD:…`）
      与类体现做归一化后比 `ast.dump()`——归一面只允许机械差异（`pool`→`self`、
      `_impl.X`→`X`、`helper(self, …)`→`self.helper(…)`、成员重命名、docstring/注解）。
      首轮报 15 处 DIFF，逐条追因**全部是工具自身缺陷**（`find_func` 未按类定界，
      把 `AsyncTcpConnection.request` 当成 `AsyncConnectionPool.request` 来比），
      修工具后 `31/31 same / offenders: 0`。登记此段是因为它与 F-21 同形：
      **测量工具的假阳性会让人去"修"一段本来正确的代码**。
    - **守卫反转**：`test_public_pool_hardening_wiring.py` 原先断言
      `ConnectionPool.request.__module__ == "tstdx.transport._pool_hardening"`，
      即把遮蔽写成契约；现改为断言实现住在 `tstdx.transport.pool` / `tstdx.transport.async_`、
      `__wrapped__` 为空、且 `importlib.util.find_spec("tstdx.transport._pool_hardening")`
      为 `None`——重新引入任一桩层即红。本地冒烟（`test_local_smoke_hardening_contract.py`
      等 4 个契约文件）与 `scripts/build_package.py`、`wheels.yml` 的 wheel 冒烟同批改判。
    - **复测（本机 Windows+py3.13，同一轮日志）**：`PYTEST_RC=0`；整仓
      `--cov=tstdx` **80.60%**（阈值 77 未下调）；`pool.py` 46.2%→**80%**、
      `async_.py` 44.7%→**89%**——整仓数字几乎不动（80.53%→80.60%）而两个池各抬
      30~40 个点，正说明原先那块缺口是"记账假象"而非新获得的测试；
      `ruff check`/`ruff format --check` 干净、`mypy`（CI 参数）188 文件 0 错、
      reachability `--strict` RC=0（`188 模块 / 可达 171 / 豁免 17`）、docs links 82 OK。
      真实网络冒烟与 `v1.1.0-dev.1`  tagging 按用户选择**继续延后到本步收口之后**（第 3 步）。
12. ✅ **包 docstring 纳入活文档门禁（Phase 5 第 12 步，2026-09-19，见 §0.3 F-31）**：
    验收清单的「README / ARCHITECTURE / `__init__` docstring 与代码零矛盾」长期挂空，
    根因是文档门禁的三项检查（import 解析、反引号路径、README 数字与结构树）都只扫
    markdown——**包自己的门面文档不在任何门禁的读数范围内**。据此实测三处矛盾：
    Quick start 的 `Client(provider="tdx")` 照抄即 `TypeError`（内核形参名是
    `default_provider`）、分层图只画了 24 个顶层包中的 14 个（缺的正是 v17 新增的服务面与
    配置面）、README 结构树把 `cli/` 写成「31 子命令，全部委托 Client」。

    - 前两项按事实改正；第三项改为如实口径（数据命令全部经 `Client`，6 个传输/诊断命令除外
      并指向 `runtime_commands.py` 的模块 docstring——即第 8/10 步建立的口径）。
      **不给 `provider` 加入参别名**：`Client(**runtime_kwargs)` 的键名以内核形参为准，
      加别名等于违反 v17 的 clean-break 口径。
    - **门禁补齐两条**：分层图与磁盘顶层包**双向**对账（缺一层或指一层幽灵都红），
      Quick start 的名字直调在禁网下真实求值（`TypeError`/`NameError`/
      `AttributeError`/`ImportError` 记为矛盾，因禁网/读文件失败说明入口与签名成立）。
    - **变异验证**：删 `integration` 行 ⇒ `新增顶层包未写进包 docstring 分层图：
      ['integration']`；插幽灵层 `execution` ⇒ `分层图指向磁盘不存在的层：['execution']`；
      入参退回 `provider=` ⇒ `Client(provider='tdx') -> TypeError: …`；三条分别 RC=1。
    - **复测（本机 Windows，同一轮日志）**：文档门禁 17 项、`tests/architecture` 全绿；
      `ruff check`/`ruff format --check`（本次触及文件）、`mypy tstdx/`（CI 参数）、
      originality `--strict`、docs links（82 文件）均 RC=0；离线全量套件
      `-m "not network"` **3282 项 / 0 失败 / 0 错误 / 7 跳过**。
13. ✅ **随机测试序抽检补上真实手段，并清掉一处过期 xfail（Phase 5 第 13 步，
    2026-09-19，见 §0.3 F-32/F-33）**：第 1 步把"随机序抽检"记成既成事实，实测是
    `-p no:randomly`（关随机）+ 插件根本没装，即该验收项**无判据**。

    - **不加依赖地补判据**：一次性 scratch 插件在 `pytest_collection_modifyitems`
      按种子洗牌，seed 1 / 7 / 42 各跑整仓离线全量。
    - **首轮被自己的装置咬了一口**：种子变量最初叫 `TSTDX_SHUFFLE_SEED`，撞上 F-16
      之后 fail-closed 的配置加载器（未知 `TSTDX_*` 一律报错），一次跑出 69 failed /
      53 条 `ConfigError`。改名 `QODER_SHUFFLE_SEED` 后干净——顺带反向证明了那条
      fail-closed 边界确实有牙（与 F-21 互为镜像：**测量装置也会污染被测系统**）。
    - **复测**：三轮各 `3276 passed / 5 skipped / 10 deselected / 1 xpassed`、RC=0
      （72–79 s）⇒ 主体链路对测试顺序不敏感，验收项自此有真实证据。
    - **XPASS 不放过**：唯一稳定 xpass 的 `test_protocol_chain_agrees_on_000300`
      经读 `_std7709_common.infer_market` 确认是"协议链已委派 canonical symbol engine"
      （缺陷真已修）而非断言变弱，遂删 `xfail(strict=False)` 让它成为常态守卫——
      留着等于给未来的回归装了个消音器。`tests/domain/test_symbol_chains.py` 24 项 RC=0。
14. ✅ **事实文档的规模数字全部钉回真相源（Phase 5 第 14 步，2026-09-19，见 §0.3 F-34）**：
    第 12 步把包 docstring 接进文档门禁后，同一格盲区还剩一块——**数字一致性检查只读
    README**。这次落在 `docs/ARCHITECTURE.md` 上，抓出两条假事实（白名单 11 项 vs 实际 10；
    覆盖率"本地为红"vs 本轮实测已越阈），详见 §0.3 F-34。

    - **改判据而不是改读数**：F-15 那条覆盖率陈述删掉了百分比，只留"离线全量已越过阈值 ⇒
      本地为绿"与"重钉需要 CI 环境实测"两句口径。事实文档抄一个每轮都会漂移的读数，等于
      预约下一条失真；逐轮数字统一记在本文的步骤日志里（本步末尾即是）。
    - **门禁 shape**：一张「文档 / 事实 / 定位模式 / 真相源」表 + 一条下界判定。真相源全部
      是运行期对象或磁盘事实（能力目录、命令账本、解析器表、配置 dataclass 字段数、根目录
      `*.py` 计数、`tstdx/web/` 的 AST 类计数），文档里的数字只能算它的抄本。
    - **"45+ HTTP 源"按下界判**：加源不必改文档（否则会诱使作者每次新增都改一遍 doc，
      最终又变成一处过期数字），掉到宣称界下必须改——这条与实际 73 类的差距是刻意保留的
      宽松度，不是漏网。
    - **变异验证（10 条全部 RC=1 且各自指名）**：ARCHITECTURE 的 172→167、85→84、61→62、
      5→6、10→11、删掉白名单宣称；README 的 85→86、61→60、下界 45→90、删掉下界宣称。
      README 两条报出的是 `[85, 86]` 这种"同一文档自相矛盾"集合，因为这两个数字在正文与
      结构树里各出现一次——只改一处也是红，这正是把集合而非单值当判据的意义。
    - **复测（本机 Windows+py3.12.13，同一轮日志）**：`tests/architecture` 全绿；离线全量
      `-m "not network"` **3285 passed / 7 skipped / 10 deselected**、0 失败；整仓
      `--cov=tstdx` **80.53%**（阈值 77 未下调）；`ruff check`、`ruff format --check`（触及
      文件）、`mypy`（CI 参数）、originality `--strict`、`spec_audit --json --strict`、
      reachability `--strict`、docs links（82 文件）均 RC=0。
15. ✅ **事实文档的名单、代码注释数字与服务面口径一起钉回真相源（Phase 5 第 15 步，
    2026-09-19，见 §0.3 F-35）**：第 14 步的表当时只铺了 README + ARCHITECTURE 两份，
    `docs/api/`、`quickstart`、`troubleshooting`、`cookbook` 里的同一批事实仍在盲区；
    把表铺满后另抓出三类矛盾（服务面"不存在第二套执行路径"过度承诺、公开枚举的类数在
    文档与 6 处生产注释里集体写错、清单只数个数不核名字），详见 §0.3 F-35。

    - **清单要逐个核名字，不能只对个数**：`Domain Record` 族与 WS JSON-RPC 方法都在文档里
      以 `A/B/C…` 形式公示。条数对上而名字漂移时，用户照抄即得到不存在的 Record 或
      `-32601`。故两条新守卫把文档清单拆成集合，逐个比对
      `tstdx.domain.records.__all__` 与 `runtime_ws._dispatch`（后者用 AST 提取
      `method ==` 与 `method in {...}` 两种写法的字符串常量——初版按 `==` 提只数到 9 个，
      差点把正确的文档"修"成错的）。
    - **口径矛盾按事实改写，不靠门禁掩盖**：`docs/api/interfaces.md` 原写"四个服务面
      全部委托同一个 `Client`，不存在第二套执行路径"，而 CLI 另有 6 个传输/诊断命令
      （`probe`/`goods`/`f10`/`blocks`/`list`/`quotes-snapshot`）直连传输层客户端。这 6 条是
      协议诊断面、不是第二套能力执行路径（口径与 `runtime_commands.py` 的模块 docstring
      一致，守卫是 `test_service_faces_never_import_the_web_layer`），文档遂改为如实写明
      例外；`docs/api/README.md` 的 CLI 行同步补"6 个传输/诊断命令除外"。
    - **同一个错数会在代码注释里繁殖**：盘中异动枚举的 `CHANGE_TYPES` 有 20 项、且
      `tests/web/test_hot_rank.py` 早已断言 `len(et) == 20`，可文档与 6 处生产 docstring /
      注释仍写"16 类"。数字改了 9 处，并新增 `test_code_comments_about_change_types_match_the_enum`
      扫 `tstdx/` 里所有含"异动"的行、把 `N 类` 钉回该字典——事实不只住在 markdown 里。
    - **刻意不钉的项**：`docs/api/README.md` 的"11 领域基类"分母含糊（`domain` 下直接
      子类 10 个、去重后的基类名 11 个），钉一个定义不清的事实只会制造下一条失真；待
      该口径在文档侧收敛后再入门禁。运行期规模事实统一经 `@lru_cache` 的 `_actual_facts()`
      取一次（构造 Client + HTTP app 的代价不为每条断言重复付）。
    - **变异验证（本轮 20 条全部 RC=1 且各自指名）**：17 条落在文档侧
      （api/README 的 CLI 31→30、MCP 9→8、WS 方法数 10→9、Record 类数 9→8、契约下界
      60→70，interfaces 的 capability/路由/工具/子命令/Record 各 1 条，quickstart、
      troubleshooting、cookbook 的命令账本，删清单宣称、清单里塞一个幽灵方法名
      `runtime.ping`、幽灵 Record 名 `Warrants`），3 条落在枚举侧
      （`_session_info.py` 与 `fundflow.py` 的"20 类"→"16 类"、api/README 的"20"→"19"），
      报出的都是"声称 X，真相源 Y"这一形式的定位结论。
    - **复测（本机 Windows+py3.12.13，同一轮日志）**：`tests/architecture/` **146 passed**；
      离线全量 `-m "not network"` junit 计数 **3313 tests / 0 failures / 0 errors /
      7 skipped**、RC=0；整仓 `--cov=tstdx` **80.54%**（阈值 77 未下调，日志明写
      `Required test coverage of 77.0% reached`）；全仓 `ruff check`、`ruff format --check`
      （430 files already formatted）、`mypy`（CI 参数）、originality `--strict`
      （`Total: 189 Suspicious: 0`）、`spec_audit --json --strict`、
      `golden_audit --gate --require-markets`、reachability `--strict`、docs links（82 文件）
      均 RC=0。
16. ✅ **三面冒烟 + 真实网络 + wheel 安装冒烟一次性跑完，并把两处发布缺陷钉成守卫
    （Phase 5 第 16 步，2026-09-19，见 §0.3 F-36/F-37/F-38）**：桩层解散（第 11 步）后，
    按用户"等桩层收口后再冒烟"的决定执行了这项一直延后的验收。**tag 未启动**——
    打标签需另行确认，且本轮冒烟暴露 F-37 后本就不该继续。

    - **wheel 安装冒烟（全离线，本轮真正跑通）**：`python -m build --wheel --no-isolation`
      出 `tstdx-1.0.0-py3-none-any.whl` → 直接调 `build_package._smoke()` 建临时 venv 装它：
      `SMOKE_RC=0`、日志 `tstdx 1.0.0 …\site-packages\tstdx\__init__.py wheel smoke OK`、
      `tstdx --help` 列出 33 个子命令、`tstdx hosts audit --help`（5 个 `--family` 族）退出 0、
      `pip check` 回 `No broken requirements found`。这一步顺带抓出 **F-36**：探针里
      `from tstdx.facade import UnifiedQuoteAPI` 引用了 Phase 1b 已物理删除的模块，而这段
      import 住在 `python -I -c "<字符串>"` 里，F-24 那套按文件路径对账的守卫结构性看不见。
      已改为判 `from tstdx import Client` + `callable(Client.call)/callable(Client.typed)`
      （把"唯一业务入口的通用面在 wheel 内可用"这条真契约补进去），并新增字符串 import
      守卫 `test_release_smoke_imports_only_symbols_that_still_exist`（扫 `build_package.py`
      与 `wheels.yml` 的全部 `tstdx` import，逐个过 `find_spec`/`hasattr`，且断言解析结果
      非空以防守卫自身失明）。**变异验证**：把那行 facade import 塞回探针 → 守卫报
      `发布冒烟 import 了不存在的模块：['tstdx.facade']`；还原后本轮
      `tests/compatibility + tests/architecture` **264 项通过、PYTEST_RC=0**，
      `ruff check` `All checks passed!`、`ruff format --check` `2 files already formatted`。
    - **七格真实网络/服务面冒烟（同一脚本、同一次运行，6 PASS / 1 FAIL）**：
      ①`network/tdx-direct` PASS（`provider=tdx channel=quotation kind=direct
      cache_tier=null` 000001 price=11.7）；②`network/web-direct` PASS（`tencent`
      `kind=direct`，平安银行 11.7）；③`network/tdx-bars` **形式 PASS 但 rows=0**；
      ④`network/stream-3-frames` **FAIL：frames=0 errors=[]**；⑤`surface/cli-live`
      PASS（`python -m tstdx quotes sz000001 --provider tdx` rc=0，JSON meta 里
      `kind=direct / cache_tier=null / fallback=false`）；⑥`surface/http-live` PASS
      （`tstdx serve` 起网关，`/v13/runtime/health` 回
      `direct_bindings=251 migrated_capabilities=172`，`/v13/quotes` 1 行且 provenance
      为 direct）；⑦`surface/mcp-live` PASS（`tools/list` 后按 `get_quotes` 的
      `inputSchema.required` 组参，`provenance_direct=true`、`isError=false`）。
      ⇒ **零缓存直连这条主链在 CLI / HTTP / MCP 三个服务面与两个 provider 上一致成立**
      （`cache_tier=null` + `fallback=false` 即 v17 的验收判据本身）。
    - **两处 FAIL 的归因要如实分开**：第一轮 5 格里 3 格的失败是我的**冒烟脚手架**缺陷
      （脚本放在 TEMP 导致 `ModuleNotFoundError: No module named 'tstdx'`；MCP 格误选
      `get_bars` 而触发 `ValidationError: missing required parameter`；HTTP/MCP 判据只做了
      子串匹配）——补齐 `sys.path`、按名字选工具、把判据改成解析 JSON 后核
      `provenance.kind/fallback/cache_tier` 才是本轮那 6/7。**不是**运行期回归。
    - **抓出 F-37（P0）**：`tstdx server-test` 本轮 **3/8 主站可达**
      （180.153.18.170 23.8ms、218.6.170.47 25.1ms、123.125.108.14 31.6ms），而全部 golden
      样本的采集主机 218.75.126.9 超时。同一批可达主站 `0x0530` 实时行情正常、`0x052D`
      一律回 `zip_size=2 payload=2 字节 = 2003`——**与 golden `body_hex` 逐位相同的 26 字节
      请求**在 2026-08-31 曾拿到 180 字节 / 10 根真样本；12 个 category、SH/SZ、日线/1分/5分
      全 0 根。`Client.bars()` 因此返回 `data=[]` 却标 `ProvenanceKind.DIRECT / cache_tier=None`
      且 `strict=True` 不报错。同批真机上 `capital_changes`(0x000f, 250 行)、
      `finance_info`(0x0010, 37 行) 有数据但字段错位（`code='519\x01'`、`market=48`），
      而离线 `-k "capital or finance or bars or kline or hand"` 本轮 8 项 **RC=0**
      ⇒ 解析器与归档样本自洽、与当下线路不一致。**按仓内既有口径（"差分基准待真机样本
      裁决，禁止盲改"）本轮未改任何协议字节**，三条处置路径交用户拍板（见 §0.3 F-37）。
    - **抓出 F-38（P1，测量口径）**：全库 `@pytest.mark.network` 仅 6 项且全在
      `tests/web/*`，`host_audit` 从不发 K 线请求 ⇒ 7709 数据面在门禁里零 live 判据，
      F-37 这类缺陷结构性隐形。本轮刻意**未**先加带 network 标记的 K 线断言（F-24 的教训：
      守卫把缺陷钉成契约＝每日 live job 固定红），待 F-37 裁决后再补并挂进 `live-smoke.yml`。
    - **时段口径**：本轮为周六休市，行情读到周五收盘快照（腾讯 `time='20260918161427'`）。
      历史 K 线与交易时段无关，故"0 根"结论不受影响；但 stream 的 0 帧与字段错位需一次
      **工作日盘中**复跑才能出严格结论，已登记为后续动作。
17. ✅ **契约分母改判为结构口径，"领域基类"数从口头事实变成门禁（Phase 5 第 17 步，
    2026-09-19，见 §0.3 F-39）**：第 15 步留下一格待办（"11 领域基类"分母含糊故不钉），
    顺藤摸到它背后的真问题——对外契约数的分母由一份手抄的抽象基类名单决定，而同一份名单
    在仓内抄了 5 遍。

    - **名单是被自身判据遮蔽的冗余**：实测那 12 个名字在四种构造路径（`cls()`、
      `_minimal_instance`、kwargs 阶梯、factory 映射）下**全部构造失败**，各站点自带的
      `except Exception: continue` 早就把它们排除了——名单不决定读数，却决定读者的信任。
      危险方向是**漏更**：新增抽象基类若没同步这 5 份名单就会被算进契约数，`60+ 契约` 与
      PyPI 描述同时虚增而审计全绿（F-19 是同类缺陷的反方向：那次分母被读小）。现改为
      纯结构判据（可构造 + `capability` 为字符串），5 份名单全部删除；`contract_audit --ci`
      同轮实测 `contracts: 63 / business caps: 155`，与删除前逐项相同 ⇒ 改判无损。
    - **"11 领域基类"改判为 10**：显式判据（被其它契约直接继承的抽象 dataclass，不含根
      `CapabilityQuery`）写进 `_typed_domain_base_names()`，README（2 处）、
      `docs/api/README.md`、`docs/api/interfaces.md` 的抄本统一改 10。第 15 步"分母含糊
      所以不钉"的口径就此撤销——含糊的不是事实，是没写判据。同批补上 README 两处
      "9 Domain Record 族"（此前只在 api 文档钉过，README 抄本在盲区）。
    - **审计脚本的自述也进门禁**：`test_contract_audit_docstring_numbers_match_the_audit`
      把它 docstring 里的 155/63 钉回它自己算出的数（F-25 对齐了"它跑什么"，没对齐
      "它抄什么"）；`test_typed_query_denominator_is_not_a_hand_copied_list` 禁止名单回潮。
    - **顺带修一处测量装置缺陷**：`test_cli_script_exits_zero` 用 `text=True` 捕获子进程
      输出却不指定编码，Windows 下子进程的 GBK 输出使 `_readerthread` 抛
      `UnicodeDecodeError`（以 warning 形式长期滞留；且一旦审计真失败，
      `result.stdout[-800:]` 会因 `stdout is None` 二次崩掉诊断信息）。现固定
      `PYTHONIOENCODING=utf-8` + `encoding="utf-8"`，warning 消失。
    - **变异验证 8 条全部 RC=1 且各自指名**：塞回 `skip = {"MarketDataQuery"}`、
      自述 155→150、63→62、README/api-README/interfaces 三处领域基类各改 1、
      README 的 Record 族 9→8。
    - **复测（同一轮日志）**：`tests/architecture/` + `tests/v14/` junit `211 tests /
      0 failures / 0 errors`、RC=0；`contract_audit --ci` RC=0（`63 契约 / 155 业务
      capability`）；`ruff check` 与 `format --check`（触及的 3 个文件）干净，
      reachability `--strict`、`spec_audit --json --strict`、docs links（82 文件）均 RC=0。
    - **同一轮的整仓离线复测**：junit `3313 tests / 0 failures / 0 errors / 7 skipped`、
      `FULL_RC=0`，`--cov=tstdx` **80.52%**（日志明写 `Required test coverage of 77.0%
      reached`，阈值 77 从未下调）。本步中途曾有 2 条红，均来自并行会话在同一工作树里的
      在途改动（`pyproject.toml` 描述一度含 `zero-cache`；ProtocolSniffer 的一次性抓包产物
      一度落在 `PROTOCOL_SPEC/_sniffer/`），两处都由对方在 **F-40/F-41（第 18 步）** 内
      自行结清，本步未代为修改——详见下一条步骤日志。
18. ✅ **缓存层删除后残留的"缓存形状"一并清偿（Phase 5 第 18 步，2026-09-19，
    见 §0.3 F-40/F-41）**：目标口径是"数据请求不需要缓存，直接请求对应数据源"。
    第 16 步的零缓存证据（CLI/HTTP/MCP 三面 `cache_tier=null`）只证明了**运行期**没有
    缓存；这一步把"包里没有缓存形状"也变成事实与门禁。

    - **零调用方的缓存面 = 一句 import 就能恢复的旁路**：`CapitalChangeCache` 自带
      TTL + `~/.tstdx/factors` 落盘，docstring 明写命中即"跳过 0x0010/gpcw 网络与解析"，
      却在 `tstdx/` 内无任何调用方（复权引擎 `compute_factors(bars, events)` 由调用方喂
      事件），只有它自己的 8 项单测维持其"活着"。按 clean-break 口径整族物理删除、
      不留别名（`finance.py` 301→162 行）。`Provenance` 上同源的 `cached()`/`cache_hit`/
      `direct_fetch` 三个生产零消费者成员一并删除（`result.py` 161→148 行）；
      **`cache_tier` 字段保留**——它是 wire 上那个恒为 `null` 的零缓存证据，删掉它等于
      把判据本身删掉。
    - **包内文档不再描述不存在的层**：`result.py` 模块 docstring 原写 `cache_tier='l1'/
      'l2'` 取回模型并称其为"later cache-poisoning and freshness gates 的地基"，改写为
      当下事实（运行期不做结果缓存，生产 provenance 一律 `direct()` + `cache_tier=None`）。
      `pyproject.toml` 的 `description` 仍宣称 "semantic caching"——PyPI 项目页第一行是
      对外最显眼的承诺，也是全仓最错的一句，且元数据从来不在任何事实门禁的扫描名单里
      （F-41）。改为 "direct Provider reads"，刻意连 "zero-cache" 都不写：包描述不该为
      不存在的东西占词，守卫也因此保持"描述含 `cach` 即红"的简单形状。
    - **两条新守卫 + 一条改判**：`test_package_defines_no_data_cache_layer`（AST 扫
      `tstdx/**` 的 `*Cache*` 类与 `get_*cache*` 函数，`lru_cache` 这类纯函数记忆化明确
      排除并在 docstring 写明边界）、`test_pypi_description_claims_no_caching`；
      `test_cache_hit_preserves_direct_origin`（自造 tier 再断言能造出来）换成
      `test_direct_provenance_carries_no_cache_tier`（断言 `direct()` 的 `cache_tier is None`
      + `hasattr` 反向钉住三个已删词汇）。**变异验证 2 条各自 RC=1**：塞入
      `class QuoteCache` → 报 `tstdx\domain\finance.py:class QuoteCache`；把 `semantic
      caching` 写回描述 → 整句回显。
    - **复测（同一轮日志）**：整仓离线 `-m "not network"` junit
      **3313 tests / 0 failures / 0 errors / 5 skipped**、`FULL_RC=0`，`--cov=tstdx`
      **80.58%**（日志明写 `Required test coverage of 77.0% reached`，阈值 77 未下调）；
      `ruff check` + `format --check` 触及的 5 个文件干净，`mypy`（CI 参数）对
      `tstdx/result.py`、`tstdx/domain/finance.py` Success；`tests/test_spec_coverage.py`
      15 项 RC=0。
    - **孤立提交复验（把并行会话的在途改动排除在外）**：上一次的 3313 是在**含对方 WIP**
      的工作树里跑的，因此对 `28abb69` 单独开 worktree 复跑——junit
      **3307 tests / 0 failures / 0 errors / 5 skipped**、`ISO_FULL_RC=0`；计数关系
      可核对：`3313 - 8（删除的缓存自测）+ 2（新增守卫）= 3307`。同一 worktree 内
      `audit_reachability --strict`（无未登记孤儿 ✓）、`mypy tstdx/`（CI 参数）、
      `check_originality --strict`、`spec_audit --json --strict`、
      `golden_audit --gate --require-markets`、`contract_audit --ci`、docs links
      **全部 RC=0**（且每条 RC 直接取自命令本身而非管道尾部，见 F-21）。
    - **顺带把第 17 步那"2 条红"如实结清**：它们不是谁的口径冲突，而是两个会话在
      同一工作树里交叉撞上的**我方在途改动**——① `description` 含 `zero-cache` 触发
      `cach` 守卫：现已改为完全不含 cache 词根，守卫转绿；② 未跟踪的
      `PROTOCOL_SPEC/_sniffer/mac_quotation/{052d,0530}/` 是第 16 步诊断 F-37 时
      ProtocolSniffer 的一次性抓包产物（含 `DRAFT.yaml`，其文件头本就写着"请勿直接提交"），
      它撞中 `test_audit_covers_every_non_probe_yaml` 的精确清单断言。原始 `.bin`/
      `.meta.json` 证据已整目录移出仓库保存在于 F-37 裁决使用（Windows
      `%TEMP%/qoder_live_smoke/sniffer_mac_quotation_20260919/`，仓库内已无残留），守卫复跑 RC=0。
      这条顺序本身就是 F-38 的另一面证据：**门禁只看得到仓内文件，冒烟留下的现场证据
      要么进 golden 要么滚出仓库**，中间态必然产生噪声红。
19. ✅ **README"第二种写法"的数字与协议覆盖矩阵每族分布入门禁（Phase 5 第 19 步，
    2026-09-19，见 §0.3 F-42）**：F-34/F-35 已经把"事实文档抄数字"钉回真相源，但钉的是
    **每张表里已登记的那一种写法**与**总数**。这一步把两类漏网变成门禁。

    - **总数门禁恰好掩护了分布错误**：协议覆盖矩阵原先只有一列"精确解析"，逐族写
      18 / 12 / 8 / 15 / 8——**和恰为 61**，等于总解析器数，所以"61 精确解析器"的门禁一路
      绿灯，而 5 行里 4 行是错的（真相源是 `PARSERS` 按 `(family, code)` 键分组：
      18 / 15 / 16 / 1 / 11）；命令账本的每族分布（`COMMANDS`：39 / 17 / 16 / 2 / 11，
      和 = 85）从未被写过，把 61 的分列读成"每族命令数"是必然误读。商品语义的端口还写反
      （7709），而 `protocol/commands.py::Command.port` 与主站池（`transport/hosts.py:351`
      把 GOODS 池由 EXTENDED 池派生）都是 7727。矩阵现拆成"命令账本 / 精确解析器"两列，
      **族键写进每行标签**（`**MAC 专属**（\`mac_quotation\`）`）——文档自己声明它指哪一族，
      测试就不必另抄一份"显示名→族键"映射（那正是 F-39 删掉的名单同形物）。新门禁
      `test_readme_protocol_matrix_matches_the_registries` 逐行比 `(端口, 命令数, 解析器数)`
      三元组，并要求族集合与 `COMMANDS`/`PARSERS` 三方相等：新增协议族不写这行即红、
      同一族写两行也红。
    - **表的登记单位是"写法"而不是"事实"**：同一事实写两遍，只有第一遍进过表。已钉
      "45+ HTTP 源"，框图里的"web 45 源"没人读；已钉 `docs/api/README.md` 的 Provider 数，
      README 的"11 Provider · 172 capability"与"172 项 capability"两种写法都在表外。本步
      补 20 条 `_EXACT_CLAIMS` 行 + 3 条 `_FLOOR_CLAIMS` 行 + 10 个真相源 helper，把
      `CLI 31 子命令`、`10 端点`、`9 工具`、`5 套协议族`、`全 5 族`、`5 族客户端`、
      `61 × N 族`、`15 便捷方法`、`Client 15 方法`、`28 模块`、`6 源合并`（README /
      api-README / configuration 三处）、`251 条精确绑定`（框图 / 特性表 / 目录树三种措辞）
      逐一钉回 `build_parser()`、`create_runtime_app()`、`TOOLS`、`Family`、`PARSERS`、
      `Client`、`tstdx/web/`、`loader.py` docstring、`errors.py`、`DIRECT_BINDINGS`。
      每行的 `assert claimed` 让"换一种写法"不能静默逃逸：宣称串改名或删除就报"门禁失效"。
    - **改判与消歧**：README 的过期抄本按真相源改（`parsers(61 × 6 族)`→5 族两处、
      "17 模块"→28 模块），无法钉成精确值的裸抄本改成非数字写法（框图"web 45 源"→
      "web 多源"），下界统一为"45+ 源类 / 45+ 源"；`docs/api/README.md`"11 源 × channel"
      改"11 Provider × channel"，让 Provider 数与 HTTP 源类不再共用"源"这个量词——
      量词二义正是这类抄本能长期共存的原因。
    - **变异验证 26 条全部 RED 且各自指名**：矩阵 4 例（解析器 18→17、MAC 命令 16→8、
      商品端口 7727→7709、族键 `ex_quotation`→`extended` 报"矩阵 ['extended'] 多、
      ['ex_quotation'] 缺（新增协议族必须同步这张表）"）、下界 3 例（"45+ 源类"→"45 源类"
      报"门禁失效"、"40+ 异常类"→"50+" 报"实际只有 46 个"）、精确宣称 16 例、绑定条数 3 例
      （250 / 240 / 252 vs 251，三种措辞各打红一次）。
    - **复测（同一轮日志）+ 孤立 worktree 复验**：主树整仓离线 `-m "not network"` junit
      `3334 tests / 24 failures / 0 errors / 7 skipped`、覆盖率 79.42%——24 条红**全部是
      同一个根因** `TypeError: QuerySpec.build() got an unexpected keyword argument 'max_age'`，
      落在 `tests/v14`（14）、`tests/sink`（3）、`tests/runtime/test_orchestration_v12`（3）、
      `tests/query`（2）、`tests/web`（1）、`tests/errors`（1），来自并行会话在途的
      `tstdx/query.py` 改动（本步复跑该次运行日志时对方仍在改，主树稍后 `mypy` 已回到
      RC=0），本步未代为修改。按第 18 步的做法在 HEAD（`867f6d2`）+ 本步 3 个文件单开 worktree
      复跑：junit **3337 tests / 0 failures / 0 errors / 7 skipped**、`ISO_FULL_RC=0`、
      `--cov=tstdx` **80.51%**（日志明写 `Required test coverage of 77.0% reached`，阈值 77
      未下调），计数可核对：`3334 + 3（新增的三条绑定宣称行）= 3337`。同树
      `check_originality --strict tstdx/`（189 文件 · Suspicious 0）、`spec_audit --json --strict`
      （`coverage_pct: 100.0`）、`golden_audit --gate --require-markets`、
      `audit_reachability --strict`（无未登记孤儿 ✓）、`contract_audit --ci`
      （63 契约 · 155 capability，与第 17 步逐项相同）、docs links（82 文件）、
      `ruff check` + `format --check`（430 files）、`mypy tstdx/`（CI 参数）**全部 RC=0**；
      主树收尾时 `tests/architecture/` 单跑 **182 项 RC=0**（含对方当前在途改动），其中
      `test_doc_code_consistency.py` 77 项。
    - **本步只动文档与门禁测试**：`tstdx/` 零改动。仍在表外的剩余写法也已实测登记（不是
      遗漏而是取舍）：README 的"3 Sink 策略""pool(4 槽)""11 步确定性门禁""6 个传输/诊断
      命令"与"Windows 10/11 · macOS 12+"——前者没有唯一的运行期真相源（分属枚举、模块常量、
      Makefile 步骤数、CLI 命令集合的补集），后者是支持矩阵而非规模事实，钉住它们只会把
      "改文案"变成"改门禁"。

20. ✅ **`max_age` 幻影旋钮退场，"零缓存"第一次连散文与字段一起门禁（Phase 5 第 20 步，
    2026-09-19，见 §0.3 F-43/F-44）**：第 18 步删掉的是**代码形状**（`CapitalChangeCache`
    一类能跳过数据源的对象），本步删掉的是**口径形状**——一个五张入口都收、执行面零消费的
    新鲜度旋钮，以及 8 个生产文件与 3 份文档里仍在替缓存层说话的散文。

    - **判据形式与先例同源**：F-27/F-28 早已把"CLI 声明的每个选项都必须被消费"写成门禁，
      `QuerySpec` 字段侧却是空白。新守卫 `test_every_query_spec_field_is_consumed` 不点名
      任何字段，分母取 `dataclasses.fields(QuerySpec)`、判据取"有无非 `self` 的属性读取"，
      所以 `max_age` 那类形状回不来，未来任何一个只进不出的字段同样进不来。唯一的豁免
      `options_json` 是**存储形态**，由 `test_query_spec_store_only_fields_are_still_reached`
      反向核验它仍被 `json.loads` 解码、且 `plan.spec.options` 仍是 executor 输入——
      豁免表不是免检通道。
    - **散文门禁补的是"标识符之外"的那一格**：`test_package_defines_no_data_cache_layer`
      （第 18 步）只看类名/函数名，于是 `QueryFingerprint` 写着 "used by cache/single-flight
      layers"、`period.py` 写着 "and cache lookup" 都能过关，而**读者照注释理解系统**。
      新守卫扫全部模块/类/函数 docstring 与 `#` 注释里的缓存词根，10 条放行形状各自登记
      口径来源（`cache_tier` 证明字段、`hq_cache` 字面量、`functools` 纯函数记忆化、
      `RankingStore` 主站排名、"服务端缓存"外部事实…），其余一律红。首轮 8 处红**全部改写
      散文**（4 处是否定词被换行截断，2 处补豁免形状，1 处删掉匹配不到真实代码的死豁免），
      没有一条是靠放宽门禁变绿。
    - **两条守卫都带自曝条款**：扫描器零命中即红（`assert hits` / `assert any(consumed.values())`），
      延续第 15 步 F-35 的"assert claimed"口径。
    - **变异验证 6 条全部 RC=1 且各自指名**，其中一条先跑成 RC=0 **抓出守卫自身失明**：
      `dict.fromkeys(keys, set[str]())` 让所有字段共享同一个集合，任一字段被读就全体
      "已被消费"——幻影门禁当时是瞎的，改成推导式后 `['phantom_knob']` 才报出来（与 F-39
      顺手修掉的那条测量装置缺陷同形，再次说明"守卫写完必须打红一次"）。
    - **口径落点**：新鲜度**口径**是 `currentness`、执行**预算**是 `deadline_ms`，两者都在
      fingerprint 内；`allow_stale` 恒被拒绝，其文案现在说明**为何**无对象可作用。
      `docs/providers/README.md` 的 `### bounded cache` 整节（6 行 cache key/provenance/
      cache-hit 规则）改写为 `### no result cache`，§13 CI 清单的 `cache hit -> …` 换成
      `no result cache -> provenance.cache_tier is always null` + provenance 一致性一条。
    - **文档面的边界**：散文门禁的对象是 `tstdx/`（描述当下实现的注释），本轮**刻意不**
      扩到 markdown——`docs/archive/`、`docs/adr/` 与 CHANGELOG 是带日期的历史记录，按
      F-40/F-41 已确立的口径"历史可以是真、但不能冒充现状"保留。全量 grep `缓存` 后核对
      每条 live 文档均为否定句或历史句，只有一处现在时假事实：`ADR-014` 的 Context 仍写
      `QuoteCache`/`KlineCache` "remain useful as compatibility optimizations"（两个类都已
      随 Phase 2 物理删除，全仓零命中），已改为过去时并把删除事实补进 Status 行。
    - **复测（同一轮日志，孤立 worktree 口径）**：主树此刻同时载着并行会话的在途改动
      （`tstdx/client/_mixin.py`、`docs/errors.md`、`docs/configuration.md`、三条 unit/client
      测试），所以本步按第 18/19 步的做法在 HEAD（`85a97e3`）+ 本步 26 个文件单开 worktree
      复跑：整仓离线 `-m "not network"` junit **3341 tests / 0 failures / 0 errors /
      5 skipped**、`ISO_FULL_RC=0`、`--cov=tstdx` **80.59%**（日志明写 `Required test coverage
      of 77.0% reached`，阈值 77 未下调）。计数可核对：第 19 步孤立 worktree 的 3337
      **+ 本步新增 4 条守卫 = 3341**；主树同轮合并跑为 **3344 / 0 failures / 5 skipped**
      （+3 来自对方在途测试），本步提交不含该 3 项。同一 worktree 内 `ruff check` +
      `format --check`（457 files）、`tests/architecture`+`query`+`runtime`+`unit/test_cli_runtime_handlers`
      +`v14`+`compatibility` 单跑、
      `mypy`（CI 参数）、originality（`Total: 189 Suspicious: 0`）、`spec_audit`
      （`coverage_pct: 100.0`）、`golden_audit --gate --require-markets`、reachability
      `--strict`、`contract_audit --ci`（63 契约 / 155 capability，与第 17/19 步逐项相同）、
      docs links（82 文件）**全部 RC=0**。
    - **提交面的固有缺口（如实登记，不是本步缺陷而是共享工作树的机制事实）**：提交以 26 个
      pathspec 精确限定，`git show --stat` 逐项核对确无对方在途的 `tstdx/client/_mixin.py`、
      `docs/errors.md`、`docs/configuration.md`、三条 unit/client 测试混入；但**共享文件按整文件
      入账**——`CHANGELOG.md` 里对方 `第 21 步 / F-45` 的条目当时已写好尚未提交，随本提交一并
      落库（其代码已在 `44b02fb` 落地，无工作丢失，只是署名落在本提交），而同一文件的
      plan doc 侧经复核**零外溢**（提交后的 `REFACTOR_PLAN_V17_CLOSURE.md` 里 `F-45`/`第 21 步`
      命中数为 0）。口径：pathspec 挡不住"两个人写同一份台账"，台账类文件（CHANGELOG /
      本文）在并发会话下只能事后对账，不能指望提交粒度——与 F-21"管道尾部读数不是命令读数"
      同一格：**门禁的粒度必须落在它真正能观察的对象上**。
    - **顺带量出的下一格（F-44，未清偿待裁决）**：同一次 AST 遍历发现 49 个错误类里
      7 个叶子从未被 `raise`，其中 `SourceUnavailable(E7050)` 被 `docs/providers/README.md`
      §12 写成规范语义、`FreshnessViolation(E4060)` 被 `tdx.md` 写成严格模式返回值，而
      运行期既没有包装站点也没有 currentness 校验器。本轮只登记分母与判据，**未**加门禁、
      **未**改文档：对外错误契约的 (a) 接线 / (b) 改口径 / (c) 登记为 taxonomy placeholder
      属产品决定，与 F-13/F-16 的"不静默"口径和 F-37(b) 一并裁决。


21. ✅ **首页空响应不再是"成功"：把 F-37 的机制面补上，"空"与"耗尽"从此是两件事
    （2026-09-19，见 §0.3 F-45）**：第 20 步删掉的是**口径形状**（幻影旋钮、幻影散文），
    本步补的是**判据形状**——分页终端
    把"服务端声明 0 条"读成"历史已经取完"，于是 F-37 那块空桩（主站对 `0x052D` 只回
    2 字节 `count=0`）在一整条离线门禁链路上表现为"请求成功、恰好 0 根"。

    - **为什么这条值得单独一步**：F-43/F-44 的幻影（无人消费的字段、从未抛出的错误类）至少
      在某个观察点上是"缺席"的；这一格是**在场且报错方向相反**——返回正常、无异常、无告警，
      三张服务面的 wire 上没有任何字段能区分"该标的无此周期历史"与"主站对这个命令只回空桩"。
      所以修的不是缺一条断言，而是缺一个区分：判据改为**耗尽只会表现为短页或次页空**，
      首页空是另一件事。
    - **默认只加可观测性，不改数据**：首页空 → `UserWarning`，返回值仍是空序列（与旧行为
      逐字节一致），`strict=True` 才升级为 `TruncatedDataError`（与既有锚点漂移截断同级）。
      `_t_export_security_list`（`0x044D`）接同一判据：0/1 两个市场必有数千标的，空首页只能
      是空桩，不能读成"这个市场没有证券"。
    - **一项测试改判，并写明理由**：`test_client_f1.py::test_empty_first_page_no_warning` 用
      `warnings.simplefilter("error")` 断言"空首页不许留痕"——它把缺陷本身钉成了契约。现改为
      `test_empty_first_page_warns`。新增离线回归 4 项（同步告警、同步 strict 抛错、异步镜像、
      **次页空仍须静默**）；最后一条用 `simplefilter("error")` 反向钉住，防止这次改动把正常
      耗尽一并变成噪声——判据必须只咬住"首页"这一半。
    - **口径文档**：`docs/errors.md` 的 `TruncatedDataError` 条目原先只说"网络响应数据被截断"，
      读者会以为只有漂移一个触发点；现列出 `_mixin.py` 的全部抛出点，**刻意不写条数**
      （F-42 的教训：数量抄本就是下一个过期点）。
    - **变异验证 2 条各自行列失败**：`empty_first_page = not seen`→`= False`、
      `if not out:`→`if False:`——把判据退回静默路径必红。
    - **合树复测（同一轮日志；本步代码与第 20 步已同处 HEAD）**：整仓离线 `-m "not network"`
      junit **3344 tests / 0 failures / 0 errors / 7 skipped**，`--cov=tstdx` **80.53%**
      （日志明写 `Required test coverage of 77.0% reached`，阈值 77 未下调）；`ruff check`
      （`tstdx/`+`tests/`+`scripts/`，All checks passed）、`ruff format --check`（427 files
      already formatted）、`mypy`（CI 参数，RC=0 零输出）、originality `--strict`
      （`Total: 189 Suspicious: 0`）、`spec_audit --json --strict`（`coverage_pct: 100.0`）、
      `golden_audit --gate --require-markets`（L1 命令均有真实样本 ✓）、reachability
      `--strict`（无未登记孤儿 ✓）、`contract_audit --ci`（RC=0，PENDING 仍是既有的 2 条
      Typed Query 契约缺口，本步未新增）、docs links（82 文件）**全部 RC=0**。本步**不跑**
      孤立 worktree：`44b02fb` 与 `5c50487` 都已在 HEAD，主树即合树，再造一个树形只会多测一遍
      同一形状（第 18/19/20 步开 worktree 是因为对方在途文件同树，判据已随 `5c50487` 消失）。
    - **共享工作树的提交面（如实登记）**：本步代码先行落在 `44b02fb`，而 `CHANGELOG.md` 的本步
      条目当时已写入工作文件尚未提交，随并行会话的 `5c50487` 整文件入账（对方在同一轮第 20 步
      末条已登记此事）。这是台账类文件在并发会话下的固有形状，判据见该条。
    - **本步刻意未做的两件事**：① `QueryResult` 没有 warnings 通道，这条告警只活在调用方进程
      stderr——HTTP/WS/MCP 三面的 wire 里仍看不见"本次结果为首页空桩"；② 唯一业务入口
      `Client.bars()` 没有 `strict` 形参（`strict` 只存在于 `TdxClient.bars`），经内核
      `DIRECT_BINDINGS` 的路径拿不到"必须完整"的语义。两处都要改 `tstdx/client/api.py` 与
      `tstdx/result.py`，落地本步时这两个文件正被并行会话编辑，故未代为决定；它们是**内部形状**
      （接线不引入新的对外契约），已与 F-44 区分登记在 §0.3 F-45 尾条，可与 F-18 一并处置。

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

- [x] `tstdx` 内仅存一条执行链（Client → kernel → executor），四个服务面只翻译不执行
      （原 `RuntimeGateway` 并列口径已随 Phase 3A 删除该接缝而失效；Phase 5 第 10 步 F-29
      收口 CLI 最后两条旁路命令，守卫 `test_service_faces_never_import_the_web_layer` 禁止
      服务面直接 import `tstdx.web`。六个点名的传输命令 `probe`/`goods`/`f10`/`blocks`/
      `list`/`quotes-snapshot` 仍走传输客户端，是"传输诊断/分页原始调用"的有意例外，
      见 `tstdx/cli/runtime_commands.py` 模块 docstring）
- [x] executor registry 三件套不存在，`DIRECT_BINDINGS` 唯一
      （Phase 3B；守卫 `tests/architecture/test_single_kernel_guards.py`
      的 `DELETED_MODULES` / `test_runtime_package_exports_only_the_kernel`）
- [x] `tstdx.toml` / `TSTDX_*` / `configure()` 三条路径对 `Client()` 真实生效，且
      配置面不引入任何缓存或降级语义（Phase 6；`tests/runtime/test_kernel_config_wiring.py`）
- [x] 根级 `.py` ≤ 10（实测 10）；`execution/` DAG 与 `provider/` v14 适配器目录已不存在
      （Phase 3A/3C；根级白名单守卫见 `tests/architecture`）
- [x] typed 契约端到端可达，`QueryResponse` 视图口径作废
      （Phase 3A/3D 把信封类型 `QueryRequest`/`QueryResponse` 随第二执行接缝一起物理删除，
      `tstdx/runtime/__init__.py` 仅存"已删除"说明，故"仅为视图"一项不再成立；端到端证据是
      `Client.typed(query)` 经 `QueryPlanner` 编译并按注册表路由的真实断言——
      `tests/v14/test_typed_query_runtime.py`（canonical 编译、别名归一、未注册能力执行前拒绝）
      与 `tests/v14/test_domain_typed_capabilities.py`（全 domain capability 语义就绪 + 逐能力路由）。
      契约规模以 `scripts/contract_audit.py` 为准，"缺契约"按 F-25 定为 PENDING 登记项）
- [x] README / ARCHITECTURE / `__init__` docstring 与代码零矛盾；旧方案归档
      （Phase 4 文档面统一 + 归档；对账由 `tests/architecture/test_doc_code_consistency.py`
      承担：markdown import 可解析、反引号 `tstdx.*` 路径可导入、README 数字对运行期事实、
      README 结构树双向、CLI 示例过真实 parser、包 docstring 分层图与 Quick start
      （Phase 5 第 12 步 F-31 补上最后一格）；第 14 步 F-34 再把 README 与 ARCHITECTURE
      的规模数字（能力数 / 命令账本 / 解析器数 / 配置段数 / 根级白名单 / HTTP 源下界）
      逐个钉回运行期真相源；第 15 步 F-35 把同一张表铺满 `docs/api/`、`quickstart`、
      `troubleshooting`、`cookbook`，并把 Domain Record 与 WS 方法两处**名单**、
      生产注释里的枚举类数一并纳入——事实文档与代码注释里都不再有"抄一次就过期"的藏身处）
- [x] CLI 声明的每个连接参数都到达执行面，且活文档里的 CLI 示例逐条过真实 parser
      （Phase 5 第 8 步 F-27 + 第 9 步 F-28；`tests/architecture/test_cli_connection_contract.py`
      含"全量选项消费审计"守卫 +
      `test_doc_code_consistency.py::test_every_documented_cli_example_parses`）
- [x] 随机测试序无红、全量离线门禁绿（F-32 补上真实判据后的实测：seed 1/7/42 三轮
      整仓乱序各 `3276 passed / 5 skipped / 10 deselected`、RC=0；固定序同轮
      `--cov=tstdx` 80.60% ≥ 阈值 77，`ruff check`/`format --check`、`mypy`（CI 参数）、
      originality/spec/reachability `--strict`、contract_audit `--ci`、docs links 全 RC=0）
- [x] 三面冒烟（CLI / HTTP / MCP）+ 真实网络 smoke（tdx 1 所 + web 1 源 + stream 3 帧）
      + wheel 安装冒烟（Phase 5 第 16 步已执行：wheel 冒烟 `SMOKE_RC=0` 并顺带抓出并清偿
      F-36；七格真实冒烟 6 PASS / 1 FAIL，`cache_tier=null + fallback=false` 在 CLI/HTTP/MCP
      三面与两个 provider 上一致 ⇒ 零缓存直连主链贯通）
- [ ] 发布 `v1.1.0-dev.1` tag：⏳ **仍待用户明确确认**，且本轮冒烟暴露 **F-37（P0：7709
      K 线在当下可达主站回 2 字节空桩却被读成成功）与 F-38（该缺陷在门禁里结构性隐形）**，
      需先取 F-37 的 (a)/(b)/(c) 处置裁决；另有工作日盘中复跑（stream 0 帧、字段错位）、
      F-18（`tstdx/providers/http` 守卫未接线）与 F-44（7 个从未被抛的错误叶子，其中
      `SourceUnavailable`/`FreshnessViolation` 两处被文档明文承诺）三项待裁决
