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


> **口径**：本节用**现在时**——只描述磁盘上现存的东西，反引号里每个 `…/….py` 路径都由
> `tests/architecture/test_plan_status_gates.py` 核对存在（F-58）。历史上断过、后来整层删除的
> 接缝不摆在这里当"现行断链"，它们的经过记在 §0.2 的现状列、§0.3 与 §1 的执行记录里。

| 链路 | 状态 | 证据 |
|---|---|---|
| `Client` / `AsyncClient`（`tstdx/client/api.py`）→ `UnifiedRuntime`（`tstdx/runtime/kernel.py`）→ `QueryPlanner` → `DirectProviderExecutor`（`tstdx/runtime/executor.py`，`DIRECT_BINDINGS` 精确派发）→ provenance 校验 | ✅ 唯一执行主链，零缓存 | 构造期把注册表声明与执行绑定做双向对账（`tstdx/catalog/capability_audit.py`，第 7 步 F-26）；`Client().capabilities()` 实测 172 项；`tests/v14`、`tests/runtime`、`tests/query`、`tests/provider_isolation` 全绿 |
| 服务面 CLI（`tstdx/cli/`）/ HTTP（`tstdx/integration/runtime_http.py`）/ WS（`tstdx/integration/runtime_ws.py`）/ MCP（`tstdx/integration/mcp/`）→ `Client` | ✅ 四面只翻译不执行 | 两条 AST 结构门禁共用同一次服务面 import 边扫描：`test_service_faces_never_import_the_web_layer`（F-29）与 `test_service_faces_never_build_a_stream_themselves`（F-56，第 31 步），函数体里的 import 同样算 |
| 流式面 `Client.stream` → `StreamPlanner.compile`（`tstdx/stream_contract.py`）→ `StatefulQuoteStream` | ✅ 贯通，tdx-only 判据只有一个落点 | CLI 与库面对同一个 `--provider` 同答案（`test_stream_command_refuses_a_provider_the_stream_contract_refuses`）；计划面字段判据 `test_stream_plan_carries_only_the_fields_the_client_reads`（F-55） |
| 配置面 `[core]`/`[hosts]`/`[rate_limit]`/`[web]`/`[security]`（`tstdx/config/`）→ 内核唯一读者 | ✅ 贯通（Phase 6，2026-09-19） | `tests/runtime/test_kernel_config_wiring.py`：写 TOML → 内核与传输层参数一致、env 覆盖文件、显式入参覆盖配置；未接线前"配置即装饰"的假承诺登记在 §0.3 F-16 |
| 曾经的第二接缝：v14 编排信封（`RuntimeGateway`/`Runtime`/`ExecutionPlanner`/`ProviderRouter`）与 registry 三件套（`executor_registry`/`executor_bindings`/`provider/router`） | ✅ 已整层物理删除，因此不再可能是断链 | Phase 3A 方案 (b) 与 Phase 3B（§2 决策点 1/2，2026-09-19）；防回潮由 `test_deleted_modules_stay_unimportable`（16 项）与 `test_moved_modules_are_gone_from_disk` 把守 |

**结论：核心查询、流式、传输与诊断功能全部实现，主链只剩一条且四个服务面贯通。**"实现"的两处
边界照旧如实标注——既不把没做的写成做了，也不把做过的写成没做（F-61）：① 现网证据只到一次性
冒烟为止，不是"从未上过现网"：七格真实网络/服务面冒烟已在 Phase 5 第 16 步逐格执行并留下判据
（6 PASS / 1 FAIL；`stream` 格 `frames=0` 因当日为周六休市，`network/tdx-bars` 格形式通过却
`rows=0`——那一格已于第 34 步归因为本端握手字节并修好，见 F-59）。因此仍缺的不是"跑没跑"，而是
**一次工作日盘中复跑**与 **F-37 余条的处置裁决**；再跑一次真实网络仍须用户授权；
② 已知功能缺口各有账——`0x000F` 资本变动与 `0x0010` 财务信息在新握手下仍解出错位字段（F-37
余条，待裁决）、7709 数据面在门禁里没有 live 判据（F-38，部分清偿：刻意不在裁决前把每日 live
job 钉成固定红）、92 项注册能力尚无 Typed Query 契约（F-25 的 PENDING 面，`contract_audit --ci`
不阻断）。第四项已经不在这张清单上：一件造好并测过、却没有任何生产调用点的 HTTP 主机边界守卫
按 F-18 裁决 (b) 于第 45 步删除，链外安全资产不再作为开放边界登记。

### 0.2 遗留不合理点（Phase 3–5 处理对象；第 33 步起逐行现状见最后一列）

| # | 级别 | 问题 | 证据 | 现状（第 33 步补，F-58） |
|---|---|---|---|---|
| F-1 | P0 | **第二执行接缝**：`Runtime.execute` 走 `ExecutionPlanner` DAG + `provider/router.ProviderRouter`，与 Client 内核并行；typed/batch 信封全部悬空。**2026-09-19 补充实证：信封层在 `tstdx/runtime/` 包外零生产消费者**（CLI/HTTP/WS/MCP 全直连 Client，仅 13 个测试文件引用）→ 处置新增选项 (b) 整层删除，见 §1-3A 与 §2 决策点 1 | `runtime/runtime.py:43-58`、`execution/semantic.py`、实测见 0.1；引用面 grep 复核 | **已清偿**（2026-09-19，Phase 3A 方案 (b)：信封/DAG/router 整层物理删除，防回潮 16 项 unimportable 守卫；§2 决策点 1 已追认） |
| F-2 | P0 | registry 三件套（`executor_bindings.py`/`executor_binding_registry.py`/`executor_registry.py`）为恒空注册表 + 4 个泄漏序依赖的测试；与 `DIRECT_BINDINGS` 构成第三份 binding 三元组 | 单跑红证据见 0.1 | **已清偿**（2026-09-19，Phase 3B：三件套删除，`DIRECT_BINDINGS` 为唯一事实源；第 7 步把构造期审计改成注册表↔绑定的双向判据，见 §0.3 F-26） |
| F-3 | P1 | `QueryResponse`/`QueryResult` 双结果类型并存，gateway/Runtime 需 `_wrap`/`_unwrap_result` 双份翻译 | `runtime/gateway.py:91-122`、`runtime/runtime.py:180-199` | **已清偿**（2026-09-19，随 Phase 3A 的信封层一起消失：`tstdx/result.py` 现在只有 `QueryResult` 一个结果类型，`QueryResponse` 与 `_wrap`/`_unwrap_result` 双份翻译均不存在） |
| F-4 | P1 | 根级仍散落 28 个模块，命名债未清：`client_api` vs `client_core` vs `client/`；`provider_contract/guard/audit` 游离在 `provider/` 包外；`query.py`/`result.py`/`typed_query.py`/`stream_contract.py` 契约文件平铺 | `ls tstdx/*.py` | **已清偿**（2026-09-19，Phase 3C：根级从 28 个模块收到 11 个，白名单由 `test_root_namespace_matches_whitelist` 逐名钉住；11 超出原案上限 10 的偏差登记在 §1 第 26 步，未静默改口径） |
| F-5 | P1 | typed query（60 契约 + Domain Record）无 `Client.typed()` 入口，只能经悬空的 F-1 信封线；端到端糖衣未兑现 | 冒烟：`tstdx.typed_query` 无 REGISTRY 导出，仅 `Runtime.execute_typed` | **已清偿**（2026-09-19，Phase 3D：`Client.typed()` 上线，typed 面不再依赖已删除的信封线；其返回形状不带 provenance 与 warnings 的边界另立 F-51 尾条 (a)，属新增对外承诺而非本行回潮） |
| F-6 | P1 | 文档-代码矛盾：README 架构图仍宣传 "v14 编排内核 / 语义缓存 L1/L2 / 5 级降级路由 / UnifiedQuoteAPI 门面层 / sources 路由层"，全部已物理删除；`tstdx/__init__.py` docstring 仍含"语义缓存"设计目标 | `README.md` 架构总览 | **已清偿**（2026-09-19，Phase 4 起：README 架构图与包 docstring 改写为可核验事实（F-31 族），此后活文档数字门禁、结构树双向对账与 CLI 示例解析门禁逐轮把住口径（第 12/14/15/19/23 步）） |
| F-7 | P2 | `trade/` 仅自测引用、未列入 README 能力账（模块自身已声明"独立可选 + 模拟红线"） | `tstdx/trade/__init__.py` docstring | **已清偿**（2026-09-19，§2 决策点 3：README 结构树把 `tstdx/trade/` 标注为「交易协议模拟器（实验性可选模块，`SimTransport` 纯内存模拟，不接入内核）」） |
| F-8 | P2 | `sinks→sink`、`sources` 删除后，`output/`、`profile/`、`feedback/`、`tools/` 归属与门禁未审计；`.venv` 环境要求（本机 `python` 为坏 stub，须用 `uv`/`.venv`）未写入 CONTRIBUTING | 本次调研踩坑 | **已清偿**（2026-09-19，归属与门禁两半都补上：可达性豁免逐条附可核验证据（登记时 17 条；第 45 步撤销 `providers.http` 那条豁免后同参数实测 **16 条**，见本表 F-18 行），其余取证对象 `output`/`profile`/`charset`/`deprecation`/`catalog`/`trade`/`transport.sniff` 仍在清单内，记录守卫见 §0.3 F-22），CONTRIBUTING 已写 `uv sync` 与「Windows 下别用裸 `python`」的踩坑说明） |

### 0.3 Phase 4/5 执行期实测新发现（按严重度）

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| F-12 | **P0** | `Prober.only_offline_hours()` 比较 `SessionState.IN_SESSION`——该成员**从不存在**（真实成员为 `call_auction/continuous/noon_break/closed`）。任何未打桩的调用必抛 `AttributeError`，即盘中探测保护一直是死代码；因所有测试都 monkeypatch 掉该方法，全绿从未暴露 | **已修**（2026-09-19）：改判 `state not in (CALL_AUCTION, CONTINUOUS)`，补 `tests/protocol/test_prober_offline_guard.py` 逐时段回归（含周末与 `_guard_offline` 抛错路径） |
| F-13 | P1 | **配置面大面积装饰化**（Phase 5 实测复核）。`tstdx/config/schema.py` 的 12 个段中，`cache`/`output`/`profile`/`sources`/`observability`/`compatibility`/`feedback` 七个段的 dataclass 在 `config/` 包外**零引用**（`CacheConfig`/`CompatibilityConfig` 亦仅被 `config/__init__.py` 再导出）。`[cache]` 段更与"数据请求零缓存"直接冲突 | **已清偿**（2026-09-19，Phase 6）：七段连同 dataclass 与再导出物理删除（`schema.py` −320/+45 行），loader 对未知段/字段/环境变量一律 fail-closed 并在消息里给出当前有效段清单 |
| F-16 | **P0** | **配置系统与产品链路未接线**（Phase 5 实测）：`load_config` 在 `tstdx/` 包内**零调用者**，CLI/HTTP/WS/MCP/`Client` 全都不读配置文件；`Client.__init__` 只接受 `runtime`/`**runtime_kwargs`，没有 `config=` 入口。`Config` 唯一进入运行期的路径是调用方自己构造后传给 `ConnectionPool.from_config`（`pool.py:242` 读 `cfg.rate_limit`，`core/hosts/security` 同族）——即"写 `tstdx.toml` 不生效"。而 `docs/troubleshooting.md` 长期指导用户"尝试 80/443 端口主站（配置 `tstdx.toml`）"（Phase 5 已改为显式 `Client(hosts=[...])` 并就地标注未接线）| **已清偿**（2026-09-19，Phase 6，方案 a）：`UnifiedRuntime` 成为唯一读者并缺省经 `get_config()` 取六源合并单例，保留的 5 段全部键逐个贯通到 `TdxClient`/`ConnectionPool`/`WebQuoteClient`；配置→传输只有 `pool_settings_from_config` 一个翻译点。**接线时实测出的加重情节**：被删除的第二读者 `ConnectionPool.from_config` 读的键名（`rate_call_auction`…）与 `RateLimitConfig` 字段名（`in_session`/`pre_post`/`closed`）从不重合，`getattr(..., 默认)` 把所有配置值静默丢弃为限流器默认值，而它的契约测试正是照幻影键名写的 ⇒ 全绿掩盖。口径见 ADR-016 |
| F-17 | P2 | 接线时新暴露的两处静默失效（Phase 6）：`tstdx.configure()` 合并后**丢弃返回值**、从不写回单例（调用即无效果）；`WebQuoteClient.__init__` 用 `try/except Exception: pass` 包裹配置读取，配置出错即悄悄退回硬编码默认 | **已修**（2026-09-19）：`configure()` 改为 `load_config(overrides=…, set_global=True)` 并如实记录语义；`WebQuoteClient` 直接 `get_config()`，配置解析失败 fail-closed |
| F-14 | P2 | CHANGELOG `[Unreleased]` 的 P13/P14 条目仍以已删除的 `UnifiedQuoteAPI` 门面为"暴露面"叙述；新工具未纳入 `test_doc_code_consistency.py` 的活文档集合（CHANGELOG 不在集合内） | **已清偿**（2026-09-19）：`### Added` 顶部加"当时口径 vs 现行入口"标注（门面已随 v16 Phase 2 物理删除，照抄即 `ImportError`；能力全部存活于 catalog，入口 `Client.call(<capability>, ...)`），4 处"门面暴露 N 个方法"改写为 catalog 事实；条目点名的 46 个 capability 逐个对运行期 `Client().capabilities()`（172 项）核验存在，无一失配。`[1.0.0]` 及更早版本段属既成发布史，保留原口径 |
| F-15 | P1 | 格式门禁长期为红：`ruff format --check tstdx/ tests/ scripts/` 在 0.9.6 与 0.14.4 下均报 74 个文件待重排，而 CI 用浮动的 `ruff>=0.5` | **格式与版本已清偿**（2026-09-19，Phase 5 第 2 步）：65 个待重排文件一次纯格式提交清零，重排前后 `ast.dump()` 逐个比对无差异；dev 依赖钉死 `ruff==0.15.2` / `mypy==2.3.1`。**覆盖率部分仍待办**：阈值数字已收敛为 `pyproject.toml [tool.coverage.report] fail_under` 单一事实源（删 Makefile/CI 的 `--cov-fail-under` 副本并加守卫测试），**未下调阈值**；**离线缺口已闭合**（2026-09-19，Phase 5 第 4 步实测）：Windows+py3.12 整仓 `-m "not network"` 为 **78.79%**、`PYTEST_RC=0`，已高于阈值 77；**重钉阈值数字仍待 CI 环境（ubuntu+py3.11）数字**，本机值不作依据 |
| F-18 | P1 | **安全资产躺在链外**：`tstdx/providers/http.py` 的 Provider 主机边界守卫（`host_allowed` + `ProviderBoundHttpClient` + 逐跳 `Location` 校验，11 项离线测试全覆盖）在 v16 删除跨源路由层后**没有任何生产调用点**。SECURITY.md 与各文档均未声称它在运行 ⇒ 不是"防线失效"，而是"一件造好并测过的防线没人接"。接进 `tstdx/web/_base_http.py` 会改变 web 传输的失败语义（凡未登记在 `PROVIDER_HTTP_HOST_SUFFIXES` 的主机一律挡掉），需逐源核表并真机验证 | **已清偿**（2026-09-19，第 45 步按用户裁决 **(b)** 执行；(a)/(c) 未采纳）——三条路径原文保留：**待用户决策**（属安全面，不自行拍板）：(a) 接线 `build_client(provider=…)`，先补全 11 个 Provider 的主机表；(b) 连同测试删除，回到"由调用方自证单源"；(c) 维持现状 + 白名单豁免（附理由）。第 45 步之前默认执行的是 (c)。**执行记录（第 45 步）**：① `tstdx/providers/http.py`（360 行，`PROVIDER_HTTP_HOST_SUFFIXES` + `host_allowed` + `ProviderBoundHttpClient` 及其逐跳 `Location` 校验）与其唯一消费者 `tests/providers/test_http_boundary.py` 一并物理删除，不留别名、不留空壳；② `scripts/_reach_allow.txt` 的对应豁免记录同时撤销（不撤销就会变成 F-22 定义的 `[dead]` 缺陷），复测 reachability `--strict` RC=0：**190 模块 / 174 可达 / 16 豁免 / 记录缺陷 0**；③ 对外口径同步两处（README 特性表「传输与错误卫生」行原本写着"已实现但尚未接入 web 链路"、README 路线图「可达性收口」行原本把它列为"仍待裁决的一项"），改为"HTTP 传输层不做 Provider 主机白名单，单源边界由调用方自证"，内核侧那条真实约束（选定的 Provider 不会被悄悄换成别家）不变；④ 登记本行的两处抄录错误并就地给出实测值——**"11 项离线测试"实为 10 项**（`--collect-only` 实测 10），**"补全 11 个 Provider 的主机表"里守卫表实为 7 个键**（`tencent/sina/eastmoney/baidu/jsl/boc/iwencai`），而 registry 的 11 个 id 中另有 4 个（`builtin/derived/local_vipdoc/tdx`）不经由该 HTTP 守卫，故 (a) 当时的实际缺口不是"补到 11"。**删除的代价如实记下**：这条判据是仓内唯一"跨 Provider 主机名拒绝"的可执行形状，删后 web 面只剩 `_base_http.py` 的单源约定；SECURITY.md 从未声称它在运行，故本次删除不使任何对外承诺变假。**本行的第三处抄录错误**（第 45 步复核时实测）：路径 (a) 写成"接线 `build_client(provider=…)`"，而 `tstdx/web/_base_http.py:498` 的签名是 `build_client(prefer_httpx=True, default_headers=None)`——**没有 `provider` 形参**。即 (a) 当时不是"加一行接线"，而是要先把 Provider 身份送进 web 传输层的构造面（该层的全部调用方都不传身份），改动面比登记的大一格。**本步抓到的门禁自身缺陷**：`tests/architecture/test_official_runtime_no_fallback.py` 把 `tstdx/providers/http.py` 手抄进 `OFFICIAL_RUNTIME` 清单，文件消失后三条判据以 `FileNotFoundError` 崩在 `read_text`（读者会读成"环境坏了"而非"清单过期"）——清单是 curated 子集不能 glob 推导，故补 `test_official_runtime_inventory_points_at_real_files`：死路径以人读消息报出，并加"清单 ≥15 项"金丝雀防止覆盖面塌成空转。 |
| F-19 | P1 | **spec_audit 的三条口径缺陷使 strict 门禁失真**：① `audit_all` 复用 `codegen.load_all_specs`（以 `spec_id` 为键），跨族同号互相覆盖——实测 `TRADE/0x0001` 吞掉 `F10/0x0001`、`TRADE/0x0100` 吞掉 `7727/0x0100`，**这两条命令永远不会出现在审计输出里**（分母 44 被读成 42）；② `_family_to_constant` 对未知 family 静默回落 STANDARD，于是拿 7709 账本查交易命令，把"查错账本"报成"命令未登记"；③ 无载荷控制帧（`0x0004` 心跳 / `0x000D` 握手，spec 自声明响应 `fields/header/record_size` 全空）被要求"有注册解析器"，而它们按定义没有载荷可解析 | **已清偿**（2026-09-19，Phase 5 第 4 步）：改为逐个 YAML 遍历（自动探测 draft 显式排除并可枚举）；TRADE 族查自己的账本与帧层（`tstdx.trade.constants` 常量值 + `CMD_NAMES` 双向对齐、`tstdx.trade.frames` 编解码锚点）；控制帧豁免**判定源自 spec 内容**而非硬编码清单，且"未声明字段"不等于"声明为空"。复测 `Total: 44 / In Ledger: 44 / Coverage 100.0%`、`--strict` RC=0，**100% 阈值未动**；4 项防回潮断言见 `tests/test_spec_coverage.py` |
| F-20 | P2 | 7709 账本把 `0x0004 HEARTBEAT` 标为 `verified=True`，但全仓**没有发送方**；传输层探活用未入账本的 `0x0002`（`DEFAULT_HEARTBEAT_CMD`，其注释说明"服务端对未知命令回短帧，探活只判通畅"）。同时该注释指向一个不存在的配置键 `hosts.heartbeat_cmd`（Phase 6 后 `HostsConfig` 只有 `servers`/`slots_per_host`） | **部分处理**（2026-09-19）：只把幻影配置说法改成真实覆盖点（连接池构造参数 `heartbeat_cmd`，并写明"配置面没有这个键"）。**改默认探活码属真实网络行为变化**，须真机验证 ⇒ 未动，登记为发布后小 PR |
| F-21 | P2 | **测量方法缺陷比红灯更危险**：`cmd \| tail; echo $?` 量到的是管道末端的退出码，因此 originality / spec_audit / reachability 三项曾被读成"已绿"。CI 上它们是硬门禁 | **已清偿**：本仓所有门禁复测改用 `${PIPESTATUS[0]}` 或先重定向再取 `$?`；教训与正确写法写入 CONTRIBUTING 门禁段 |
| F-22 | P1 | **豁免记录自己无人审计**（Phase 5 第 5 步实测）：`scripts/_reach_allow.txt` 28 条里 **9 条是死记录**——2 条指向 v10/v9 就消失的 `tstdx.sinks`、`tstdx.native`，7 条（`tstdx.feedback*`、`tstdx.domain.adjust`、`tstdx.streaming.{engine,push}`）指向**早已接线、现已从入口可达**的模块。扫描器只把白名单当"孤儿减集"，既不查条目是否还存在，也不查它是否还在豁免任何东西，所以 `--strict` 绿≠记录有效。附带：14 条理由 <40 字符（含 4 条短到"公开：用户统计"），`tstdx.charset` 与 `tstdx.deprecation` 的理由写着"_LAZY 导出"，而根包 `_LAZY` 实测 17 个值里**没有这两项**（理由是假的）；`pyproject.toml` 同一形状的死配置 2 处（mypy 覆盖 `tstdx.native.*`、`keyring.*` 忽略表），后者被 mypy 自己的 `warn_unused_configs` 报了出来但没人当回事 | **已清偿**（2026-09-19）：① 扫描器新增记录守卫，四类缺陷与孤儿同权重使 `--strict` 失败——`[dead]`（指向不存在模块）、`[stale]`（指向已可达模块：**保留它等于把将来真正的断链读成绿**）、`[thin]`（理由 <`MIN_REASON_CHARS=40`）、`[dup]`（同模块重复登记，后一条静默覆盖前一条）；② `tstdx.__main__` 从"豁免"改判为 `_entrypoints()` 种子（与 `tstdx.cli`/`tstdx.tools.*` 同类：静态图永无对它的 import 边，`python -m tstdx` 却必然加载）；③ 清单重写为 17 条，逐条给出可核验证据（docs 路径 + 具体测试文件 + 为何生产链路不 import），"内核不 import"一族按"用户显式导入的公共 API"与"契约/守卫资产"分组，TRADE 族额外写明 `spec_audit` 是**按字符串模块名走 importlib** 解析它（AST 图看不见这种边）；④ 删 9 条死记录、修 2 条假理由、删 `pyproject.toml` 两处死配置。**复测**：`192 模块 / 可达 175 / 豁免 17 / 记录缺陷 0`，`--strict` RC=0；`mypy`（CI 参数）RC=0 且 `unused section` note 消失；守卫回归 8 项见 `tests/architecture/test_reachability_allowlist.py`。**计数是当时读数**：第 45 步撤销 `tstdx.providers.http` 那条豁免后同参数实测 `190 模块 / 174 可达 / 16 豁免 / 记录缺陷 0`（本轮第 46 步日志再次复算同值），192/175/17 只对本行执行时点负责 |
| F-23 | **P0**（对外承诺类） | **文档声称存在一个已被删除的安全能力**：SECURITY.md「凭据保护」整节写着"tstdx 使用三级凭据存储：系统 keyring / 环境变量 / 加密文件 `~/.tstdx/credentials.enc`"，README 特性表与结构树也各写一遍（`├── security/ # 凭据三级存储…`）。而 `CredentialStore` 早在 **v10** 就按 ADR-007-010 判定"全库零调用方、属过度工程"删除，只剩 `tstdx/security/__init__.py` 一个 `__all__ = []` 的空壳在替它"作证据"。docs-code 门禁当时只校验反引号里的 `tstdx.*` 点号路径与 README 数字，**散文式能力承诺不在射程内**，所以这条假承诺一路全绿 | **已清偿**（2026-09-19，Phase 5 第 5 步）：① 空壳包 `tstdx/security/` 物理删除（历史决议留在 ADR-007-010，不需占位包），其白名单行随之删除；② SECURITY.md「凭据保护」改写为"本库不存储凭据"+ 四条现状（行情链路无凭据 / 交易侧只有纯内存模拟器 / 真券商由调用方自管密钥 / 错误上下文与反馈先脱敏）；③ README 特性行改为可核验的 `security.use_tls` TLS 与错误脱敏事实，并显式标注 `tstdx.providers.http` 主机守卫"已实现但未接入 web 链路"（与 F-18 一致；该标注与守卫本身已随第 45 步按 F-18 裁决 (b) 一并删除，README 现口径见本表 F-18 行），结构树删去 `security/` 行、补上曾漏掉的 `__main__.py` 行；④ **补门禁**：`test_doc_code_consistency.py` 新增 3 项，把 README 结构树条目与磁盘做双向对账（列出的必须存在 + 磁盘上的顶层包/模块必须都列出），使这类幻影行不能再隐身 |
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
| F-37 | **P0**（链路贯通类） | **真实网络冒烟抓到：7709 K 线在当下可达主站上返回 2 字节空桩，而库把它读成成功**（Phase 5 第 16 步实测，2026-09-19 周六 09:14–09:19 北京时间）。`tstdx server-test` 读数 **3/8 主站可达**（180.153.18.170 23.8ms、218.6.170.47 25.1ms、123.125.108.14 31.6ms），而全部 golden 样本的采集主机 218.75.126.9 超时不可达。同一批可达主站上 `0x0530` 实时行情正常（600519 price=1257.12、000001 price=11.7），`0x052D` 却一律回 `zip_size=2 unzip_size=2 payload=2 字节 = 2003`（帧头 `b1cb74000c01000000002d0502000200`）——**请求字节与 `tests/golden/quotation/0x052d_security_bars_600000_cat4/20260831-125353/meta.yaml` 记录的 `body_hex`（26 字节 `01003630303030300400010000000a0000000000000000000000`）逐位相同，而那次同字节请求在 2026-08-31 拿到 180 字节 / 10 根真样本**。周期枚举 0…11 全 12 个 category、日线/1分/5分、SH/SZ 两市一律 0 根 ⇒ 与请求参数无关。三层后果：① `Client.bars()` 返回 `data=[]` 而 `provenance=ProvenanceKind.DIRECT / cache_tier=None`，`strict=True` 也不报错，即"空即成功"，与 `0x0537`/`0x0fc5` 的 `NotImplementedFeature`、`0x0fb4`/`0x051a` 的 `CommandOffline` 两条 fail-fast 口径自相矛盾；② 同批真机上 `capital_changes`(0x000f) 与 `finance_info`(0x0010) 分别回 250/37 行但字段错位（首行 `code='519\x01'`、`market=48` 即 ASCII `'0'`），而离线 `-k "capital or finance or bars or kline"` 本轮 RC=0 ⇒ 解析器与**归档样本**自洽、与**当下线路**不一致；③ `PROTOCOL_SPEC/7709/0x052D_SECURITY_BARS.yaml` 与解析器注释都标 "L1 精确解析，golden-verified"，而这条"真机"证据链实际只挂在单台主站上 | **部分清偿（2026-09-19，第 39 步按用户裁决 (c) 执行；`0x000F`/`0x0010` 的解码布局本身仍未闭合）**——原三条路径与理由按 F-52/F-56 口径保留如下：① 按仓内既有口径（`parsers/mac.py`「差分基准待真机样本裁决（P1c，禁止盲改）」）**未做任何 body/header 试探性改写**，本轮只把事实钉进本文，并停止把 0x052D 记为"live 已验证"；② 时段口径要如实标注：本轮为周六休市，行情读到的是周五收盘快照（腾讯源 `time='20260918161427'`），**历史 K 线与交易时段无关**故 0 根仍成立，但严格结论需一次工作日盘中复跑；③ 三条待选路径由用户拍板：(a) 对当下可达主站重采 K 线/股本/财务样本并按需扩 golden 与 `PROTOCOL_SPEC`（多主站对账，需至少一台提供历史的服务）；(b) 先补"空结果不得读成成功"的运行期口径（0 根时 `strict=True` 抛错、默认给告警与不完整标记）——属产品契约改动，须与 F-13/F-16 的"不静默"口径一并定；(c) 接受"本次发布不含 7709 K 线 live 保证"，在 README/CHANGELOG 显式降级该能力口径，并把 tag 推迟到 (a) 或 (b) 落地。**执行记录（第 34 步，2026-09-19）：本行的 K 线那一半已归因于本端握手字节（见 §0.3 F-59），既不是服务端限制也不是时段效应**——默认标识块换成 30 零字节后，公开入口 `Client.bars(..., strict=True)` 在**无任何 monkeypatch 的默认路径**上 3 只标的 × 8 个周期 **24/24 全部 10 根**、`kind=direct`、`cache_tier=None`、`warnings=0` 条，`count=320` 拿回 320 根，`BAD=0`。因此本行③的 (a) 不需要重采 K 线样本（`golden_audit` 同轮报 `0x052D real category coverage: [0,1,…,11]`，12 个周期都有真样本），(b) 的"空即成功"口径已由第 21/26 步（F-45/F-51）落地，tag 在 K 线这一格上不再被阻塞。**两处仍未闭合，如实分开**：① 本行②的时段口径——本轮数据最新到 2026-09-18 15:00（周五收盘），历史 K 线本就与交易时段无关，故 0 根之谜的归因不需要盘中复跑，但**盘中实时读数**仍待一次交易时段内的复测；② 本行②里 `capital_changes`(0x000F) 与 `finance_info`(0x0010) 的字段错位**与本因无关**：第 34 步在**新默认握手下**重打，两条仍解出错值（0x000F 首行 `market=48`、`code='000\x01'`、`date='00000000'`；0x0010 首行 `market=0`、`code=''`、`values` 全 0.0），且 `warns=0`——而 0x0010 的 live 载荷长度在两台主机上都与 golden **同长 14302 字节**、头部 `6400…` 同形，0x000F 的头部仍是`<count>00 30303030 0100`（两台各自声明 188 / 250 条并解出同样条数）。⇒ 错位属**解码布局**一侧，且这两条命令的 golden 从不校验解析出的字段值。这是需用户裁决的独立缺陷（与 F-44/F-47/F-18 同批），本步不擅自改解析器。**执行记录（第 39 步，2026-09-19）：本行③的 (c)「下调能力声称 + 推迟 tag」按用户裁决落地**——`_t_capital_changes` 与 `_t_finance_info` 两个模板各挂 `.. warning::`，口径写成「条数可用、字段语义不保证」并点名本 F 号；`docs/providers/tdx.md` 的 `finance`/`capital_changes` 两行同口径批注；README 的 7709 覆盖率行与发布硬化行不再声称分时/逐笔可用、并就地记着 tag `v1.1.0-dev.1` 继续推迟。降级由 `tests/architecture/test_offline_capability_honesty.py::test_the_two_unclosed_field_layouts_stay_downgraded` 把守（删回无保留即红，变异 M7）。**解析器一个字节没动**：本行②的错位仍需一次真机布局判据，无判据时不猜（与 `parsers/mac.py`「禁止盲改」同一条口径）。本行仍开放的两件事：① 工作日盘中的实时读数复跑（须用户授权）；② `0x000F`/`0x0010` 字段布局的闭合 |
| F-38 | P1（测量口径类） | **整个 7709 数据面在门禁里没有任何 live 判据，所以 F-37 这类缺陷结构性隐形**：全库 `@pytest.mark.network` 仅 6 项，且逐文件 grep 显示它们全部落在 `tests/web/*`——协议层（`tstdx/protocol`、`tstdx/client`、`tstdx/transport`）**零 live 覆盖**；`live-smoke.yml` 每日 01:00 UTC 跑的就是这 6 项 web 用例。`tstdx/tools/host_audit.py` 名义上巡检 5 个协议族，但该模块 grep `security_bars\|bars` 零命中 ⇒ 巡检不看 K 线。于是"主站可达 + 行情能回"被隐式当成"链路通"，历史族命令的真实可用性只在本轮那次一次性人工冒烟里被碰过 | **部分清偿**（2026-09-19）：本轮把"三面 + 真实网络"写成可复现的七格判据与逐格证据（Phase 5 第 16 步），使这条验收不再是口头动作；**刻意未**新增 K 线的 `@pytest.mark.network` 断言——F-37 未裁决前加它，等于把每日 live job 钉成固定红（F-24 的教训正是"守卫把缺陷写成契约"）。待 F-37 选 (a)/(b) 落地后再补 live 断言并挂进 `live-smoke.yml` |
| F-39 | P2（口径类：对外契约数的分母由手抄名单决定，而名单已被自身判据遮蔽） | `scripts/contract_audit.py::_all_typed_queries` 用一份 12 个名字的 `skip` 集合排除抽象基类，同一份名单在 `tests/v14/test_contract_automation.py` 里又各自抄了 4 遍（共 5 份副本）。实测这 12 个类在四种构造路径（`cls()`、`_minimal_instance`、kwargs 阶梯、factory 映射）下**全部构造失败**，即它们的排除早已由各站点自带的 `except Exception: continue` 完成——名单不决定任何东西，却决定读者的信任。**危害方向是漏更而非多余**：新增一个抽象基类只要不在这 5 份名单里，就会被算进"契约数"，于是 `60+ 契约`、`10 领域基类`、PyPI 描述里的规模口径同时虚增，而审计依旧全绿（与 F-19"跨族同号互相覆盖使分母被读小"同族，这次是被读大）。连带：`docs/api/README.md`、`docs/api/interfaces.md`、README 两处宣称的"11 领域基类"按任何自然定义都不成立（`typed_query` 里被继承的抽象基类是 10 个，含根 `CapabilityQuery` 才 11 个，而根不是"领域"）——第 15 步 F-35 曾以"分母含糊"为由刻意不钉，本步给出显式判据后改判为必须钉 | **已清偿**（2026-09-19）：① 契约分母改为**纯结构判据**（可构造 + `capability` 为字符串），删掉 5 份手抄名单；`contract_audit --ci` 同轮实测 `contracts: 63 / business caps: 155`，与删名单前逐项相同 ⇒ 改动行为无损；② README（2 处）+ `docs/api/README.md` + `docs/api/interfaces.md` 的"11 领域基类"统一改为 **10**，判据写进 `_typed_domain_base_names()` 的 docstring（被其它契约直接继承的抽象 dataclass，不含根）；③ 三条新守卫：`test_fact_doc_numbers_match_their_truth_source` 新增 4 行（三处领域基类数 + README 的"9 Domain Record 族"，后者此前只在 api 文档被钉、README 的两处抄本在盲区）、`test_contract_audit_docstring_numbers_match_the_audit` 把审计脚本自述的 155/63 钉回它自己算出的数（F-25 只对齐了它跑什么，没对齐它抄什么）、`test_typed_query_denominator_is_not_a_hand_copied_list` 禁止名单回潮（名单里任何一个名字以字符串字面量出现在这两个文件即为红）；④ 顺手修掉该套件里一处测量装置缺陷：`test_cli_script_exits_zero` 以 `text=True` 捕获子进程输出却不指定编码，Windows 下子进程的 GBK 输出使 `_readerthread` 抛 `UnicodeDecodeError`（以 `PytestUnhandledThreadExceptionWarning` 形式滞留，且失败分支的 `result.stdout[-800:]` 会因 `stdout is None` 二次崩）——现显式 `PYTHONIOENCODING=utf-8` + `encoding="utf-8"`，警告消失。**复测（同一轮日志）**：`tests/architecture/` + `tests/v14/` junit `211 tests / 0 failures` RC=0；`contract_audit --ci` RC=0（`63 契约 / 155 业务 capability`，与删名单前逐项相同）；`ruff check`/`format --check`、reachability `--strict`、`spec_audit --json --strict`、docs links（82 文件）均 RC=0；整仓离线 junit `3313 tests / 0 failures / 7 skipped`、`--cov=tstdx` **80.52%**（阈值 77 未下调）。**变异验证 8 条全部 RC=1 且各自指名**（塞回一份 `skip = {"MarketDataQuery"}`、自述 155→150、63→62、三处领域基类各改 1、README Record 族 9→8） |
| F-40 | P1（零缓存口径类） | **缓存层删掉了，"缓存形状"留在包里，而且其中一个真的能跳过数据源**（Phase 5 第 18 步实测）。Phase 2 物理删除 v12 缓存层后，仓内还剩三处缓存遗产：① `tstdx/domain/finance.py::CapitalChangeCache`——除权事件 **TTL 缓存 + `~/.tstdx/factors` 落盘**，类 docstring 明写"命中（未过期且非空）直接返回，**跳过 0x0010/gpcw 网络与解析**"，即一句话就能把"数据请求不需要缓存"这条主口径变成可一键恢复的旁路；而它在 `tstdx/` 内**零调用方**（复权引擎 `domain/adjust.py::compute_factors(bars, events)` 由调用方直接喂事件），只有它自己的 8 项单测在测自己——典型的"测试维持死亡的公共面"；② `Provenance.cached(tier)` / `cache_hit` / `direct_fetch`：生产路径永不产出 tier（内核只经 `Provenance.direct()` 构造），故这三个成员在 `tstdx/` 里同样零消费者，唯一引用是 `tests/runtime/test_query_contracts.py::test_cache_hit_preserves_direct_origin`（自己造一个 tier 再断言它能被造出来）；③ `tstdx/result.py` 模块 docstring 仍在描述 `cache_tier='l1'/'l2'` 的取回模型，并称其为"later cache-poisoning and freshness gates 的地基"——**包内文档描述了一个不存在的层**，`help()` 与任何交互式阅读都会照单全收。**根因与 F-30/F-34 同格**：可达性门禁把 `__all__` 导出与"有测试覆盖"都算活，于是删层之后剩下的接口形状恰好躲过所有判据 | **已清偿**（2026-09-19）：① `CapitalChangeCache` 一族**物理删除**（`CapitalChangeCache`/`get_capital_change_cache`/`default_factor_cache_dir`/`ENV_FACTOR_CACHE_DIR`/`DEFAULT_FACTOR_TTL_SECONDS`/`_FETCHED_AT_KEY` 及其 `__all__` 条目，`finance.py` 301→162 行，连带删除那 8 项自测用例），不留别名，与 Phase 1b/2 的 clean-break 口径一致；② 删除 `cached()`/`cache_hit`/`direct_fetch`（`result.py` 161→148 行），**`cache_tier` 字段刻意保留**——它是三面 wire 上那个恒为 `null` 的零缓存证据，CLI/HTTP/MCP 冒烟与文档都以它作判据；③ `result.py` docstring 改写为当下事实：运行期不做结果缓存，所有生产 provenance 由 `direct()` 构造并带 `cache_tier=None`，保留该字段正是为了让调用方断言它仍为 `None`；④ 那项自测换成 `test_direct_provenance_carries_no_cache_tier`——除断言 `direct()` 的 `cache_tier is None`，还用 `hasattr` 反向钉住三个已删词汇，重新引入即红；⑤ 新增包级守卫 `tests/architecture/test_official_runtime_no_fallback.py::test_package_defines_no_data_cache_layer`：AST 扫 `tstdx/**` 全部类名含 `cache` 的类定义与 `get_*cache*` 函数，命中即列出路径。**变异验证**：临时塞入 `class QuoteCache` → 守卫报 `tstdx\domain\finance.py:class QuoteCache`、RC=1。纯函数记忆化（`functools.lru_cache`）明确不在禁止之列——它不省掉任何一次网络请求，守卫 docstring 里写明了这条边界 |
| F-41 | P1（对外承诺类） | **PyPI 元数据仍在宣称一个已被删除的架构层**：`pyproject.toml` 的 `description` 写着 "…with explicit Providers, **semantic caching**, Stateful streaming and canonical HTTP/WebSocket/MCP adapters"。这是 v12 语义缓存时代的残留，会出现在 `pip show`、PyPI 项目页与任何索引站的第一行——**对外最显眼的一句话恰好是仓内最错的一句**，而它不在任何事实门禁的扫描范围里（`FACT_DOC_PATHS` 只覆盖 README/ARCHITECTURE/docs，元数据文件从来不在名单上），`tests/` 里也没有任何断言读过 `description` | **已清偿**（2026-09-19）：① 描述改为 "…with explicit Providers, **direct Provider reads**, Stateful streaming…"——刻意**不提缓存**（连 "zero-cache" 这种写法也不用：包描述不该为一个不存在的东西占词，守卫也因此能保持"描述里出现 `cach` 即红"这个简单形状）；② 新增守卫 `test_pypi_description_claims_no_caching`：正则取出 `[project] description`（解析不到即报"守卫自身失效"），断言不含 `cach`。**变异验证**：把 `semantic caching` 塞回描述 → RC=1 且整句回显。同一轮顺带改写 `README.md` 的存储行（`~/.tstdx/` 配置/**缓存**/排名 → 配置/主站排名/反馈）：随 F-40 删掉落盘目录后，`~/.tstdx/` 下只剩配置、`server_ranking.json` 与 `feedback/` 三类，该行此刻正被并行会话编辑，故未并入本次提交 |
| F-42 | P2（口径类：门禁只钉总数与"第一种写法"，同一事实的第二种写法和矩阵每族分列全是手抄本） | **README 的规模数字有一半在门禁外，而协议覆盖矩阵的每族分布 5 行错 4 行、端口错 1 行**（Phase 5 第 19 步实测）。F-34/F-35 把"事实文档的抄本数字"钉回真相源后，钉的是**每张表里已登记的那一种写法**与**总数**：① 协议覆盖矩阵原有一列"精确解析"，逐族写 18 / 12 / 8 / 15 / 8，**和恰为 61**——与真相源总解析器数相同，于是"61 精确解析器"的总数门禁一直绿，而分列全错（真相源 `PARSERS` 的 `(family, code)` 键分组：18 / 15 / 16 / 1 / 11；命令账本 `COMMANDS` 另有其数 39 / 17 / 16 / 2 / 11，和 = 85），且**商品语义端口写反**（文档 7709，`protocol/commands.py::Command.port` 与主站池 `transport/hosts.py:351` 的 GOODS←EXTENDED 派生都是 7727）；② 同一事实换一种写法就脱离表：已钉"45+ HTTP 源"却看不见框图里的"web 45 源"、已钉 `docs/api/README.md` 的"Provider 数"却看不见 README 的"11 Provider · 172 capability"与"172 项 capability"，`CLI 31 子命令`/`10 端点`/`9 工具`/`5 套协议族`/`全 5 族`/`5 族客户端`/`61 × N 族`/`15 便捷方法`/`Client 15 方法`/`28 模块`/`6 源合并`/`251 条精确绑定`（README 三处三种措辞）此前一行未读——其中"6 族""17 模块"已经过期，"45 源"是精确抄本而真源在增长。**根因是表的形状**：行按 `(文档, 事实, 定位模式)` 登记，一个事实写两遍就只钉到第一遍；总数门禁则给分列错误提供了"看起来无害"的掩护 | **已清偿**（2026-09-19）：① 矩阵重写为"命令账本 / 精确解析器"两列并把族键写进每行标签（`**MAC 专属**（\`mac_quotation\`）`）——文档自己声明它指哪一族，测试因此不必再抄一份"显示名→族键"映射（那是 F-39 删掉的名单同形物）；② 新增 `test_readme_protocol_matrix_matches_the_registries`：逐行比 `(端口, 命令数, 解析器数)` 三元组，端口取自 `Command.port` 而非另一份常量表，族集合必须与 `COMMANDS`/`PARSERS` 三方相等（新增协议族不写这行即红），同一族写两行也红；③ 补 20 条 `_EXACT_CLAIMS` 行 + 3 条 `_FLOOR_CLAIMS` 行 + 10 个真相源 helper（`_protocol_families`/`_command_family_counts`/`_parser_family_counts`/`_family_port`/`_web_source_modules`/`_client_methods`/`_error_classes`/`_config_merge_layers`/`_direct_bindings` 等），按"写法"逐行登记而非按事实登记，每行 `assert claimed` 保证写法改名或删除即报"门禁失效"（F-35 同形）；④ README 的过期抄本改回真相源（`parsers(61 × 6 族)`→5 族两处、"17 模块"→28 模块），不可钉的裸抄本改成非数字写法（框图"web 45 源"→"web 多源"），下界写法统一为"45+ 源类/45+ 源"，`docs/api/README.md`"11 源 × channel"→"11 Provider × channel"以消除"源"这个量词的二义性。**变异验证 26 条全部 RED 且各自指名**（矩阵 4：解析器 18→17、MAC 命令 16→8、商品端口 7727→7709、族键 `ex_quotation`→`extended` 报"矩阵 ['extended'] 多、['ex_quotation'] 缺"；下界 3：`45+ 源类`→`45 源类` 报"门禁失效"、`40+ 异常类`→`50+` 报"实际只有 46 个"；精确宣称 16：17 模块 vs 28、170/171 capability、CLI 30、HTTP 12 端点、MCP 12 工具、全 4 族、61×6 族、14 便捷方法、`Client` 16 方法、4 套协议族、5/7 源合并、`85 命令账本（6 协议族）`；绑定条数 3：250/240/252 vs 251）。**复测（同一轮日志）**：主树整仓离线 junit `3334 tests / 24 failures / 0 errors / 7 skipped`、79.42%，24 条红同一个根因 `TypeError: QuerySpec.build() got an unexpected keyword argument 'max_age'`，全部来自并行会话在途的 `tstdx/query.py`，本步未代为修改；按第 18 步做法在 HEAD（`867f6d2`）+ 本步 3 个文件单开 worktree 复跑 junit **3337 / 0 failures / 0 errors / 7 skipped**、`ISO_FULL_RC=0`、覆盖率 **80.51%**（阈值 77 未下调，`3334 + 3 = 3337` 可核对），同树 originality（189 文件 Suspicious 0）/ `spec_audit`（`coverage_pct: 100.0`）/ golden / reachability / `contract_audit --ci`（63 契约 · 155 capability）/ docs links（82 文件）/ ruff + format（430 files）/ `mypy tstdx/` **全部 RC=0**。**尚未钉的剩余写法**（本步实测仍在，属低挥发或无唯一真相源）：README 的"3 Sink 策略""pool(4 槽)""11 步确定性门禁""6 个传输/诊断命令"与"Windows 10/11 · macOS 12+" |
| F-43 | P1（零缓存口径类，与 F-40/F-41 同族：对象是**旋钮与散文**而非代码形状） | **`max_age` 是五张入口都收、内核零消费的新鲜度旋钮，而缓存层删掉后仍有 8 个生产文件与 3 份文档在替它说话**（Phase 5 第 20 步实测）：`QuerySpec.max_age` 带着 `build()` 形参、`max_age < 0` 校验与 normalize 行住在 `tstdx/query.py`，Client（`kwargs.pop("max_age")`）、CLI `--max-age`（3 个子命令 + 3 处透传）、HTTP（5）、WS（3）、MCP（JSON Schema + impl）、`runtime/kernel.py`（16）、`orchestration.py`（4）共 **9 个文件 50 行**把值一路送到 `QueryPlan`，而执行面没有任何一处读它——调用方设置它只会得到"已经生效"的错觉（F-27/F-28 已给 CLI 选项立过同形判据，`QuerySpec` 字段侧一直没有对应门禁）。散文侧同批失真：`QueryFingerprint` docstring 写着 "used by cache/single-flight layers"、`domain/period.py` 写 "and cache lookup"、`config/schema.py` 把"缓存/降级链"列为可配置面、`result.py` 说生产 provenance 由缓存层构造、`__init__.py` 门面宣称缓存能力、`protocol/handshake.py`/`domain/symbol.py` 各一处；文档侧 `docs/providers/README.md` 的 **`### bounded cache` 整节 6 行**在规定 cache key/fingerprint/provenance/cache-hit 语义（描述一个已被物理删除的层，且 §13 CI 清单列着 `cache hit -> …`），`docs/api/interfaces.md` 6 处签名带 `max_age`，README 写"`allow_stale` 显式放行"（它实际恒被拒绝） | **已清偿**（2026-09-19）：① **旋钮整体物理删除**（不留别名）：字段、`build()` 形参、校验、normalize 与 9 个文件的全部透传一并删；新鲜度口径归 `currentness`，执行预算归 `deadline_ms`，二者都在 fingerprint 之内；`allow_stale` 的拒绝文案改为说明**为何**无对象可作用（"过期容忍没有可作用的对象，新鲜度口径请用 currentness"），构造期 ergonomic 折叠不变；② **散文门禁**（新增 `test_production_prose_never_claims_a_data_cache`）：按 AST 扫 `tstdx/` 全部模块/类/函数 docstring 与 `#` 注释里的 `cache|caching|缓存|single-flight` 词根，只放行 10 条**逐条给出口径来源**的形状（英文否定句、"零缓存/不存在…缓存"、`cache_tier` 证明字段、`hq_cache`/`__pycache__` 字面量、`functools` 纯函数记忆化、`RankingStore`/`disk cache` 主站排名、`tools/` 进程内解析复用、"服务端缓存"外部事实），其余一律红；配 `test_pure_function_memoization_claim_is_true` 反向核验"宣称 LRU 记忆化"的文件里真的挂着 `lru_cache`；③ **幻影开关门禁**（`test_every_query_spec_field_is_consumed`）：分母取 `dataclasses.fields(QuerySpec)`，判据是"有没有非 `self` 的属性读取"，豁免表 `_QUERY_SPEC_STORE_ONLY_FIELDS = {options_json}` 由 `test_query_spec_store_only_fields_are_still_reached` 自检（该字段必须仍被 `json.loads` 解码、且 `plan.spec.options` 仍是 executor 输入），两条都带"扫描器零命中即自曝失明"的自我校验；④ **首轮抓到 8 处散文红，全部改写散文而非放宽门禁**：4 处是否定句被换行截断（否定词与 cache 词根不同行），2 处是豁免表缺了合法形状（`live elsewhere`、`服务端缓存`），另删掉一条匹配不到任何真实代码的死豁免；⑤ **变异验证 6 条全部 RC=1 且各自指名**（kernel 反宣称缓存 → 指名 `tstdx/runtime/kernel.py`、撤销"服务端缓存"豁免 → 指名 `adapters_baidu.py`、注入无人消费字段 `phantom_knob` → 报 `['phantom_knob']`、executor 的 `plan.spec.options` 改名 → 报"options 袋不再是执行面输入"、`options_json` 不再解码 → 报"豁免不再成立"、在无 `lru_cache` 的文件宣称 LRU → 报"宣称 LRU 记忆化却没有 lru_cache"），**其中第 3 条先跑成 RC=0，暴露守卫自身的缺陷**：`dict.fromkeys(keys, set[str]())` 让所有字段共享同一个集合，任一字段被读就全体"已被消费"——幻影门禁当时是瞎的，改成推导式后才红；⑥ **复测（同一轮日志）**：守卫文件 9 项 RC=0，整仓离线 `-m "not network"` junit **3341 tests / 0 failures / 0 errors / 5 skipped**、`FULL_RC=0`（与第 19 步孤立 worktree 的 3337 差 4，正是本步新增的 4 条守卫），`--cov=tstdx` **80.59%**（日志明写 `Required test coverage of 77.0% reached`，阈值 77 未下调）；`ruff check`（`tstdx/`+`tests/`+`scripts/`）、`ruff format --check`（457 files）、`mypy`（CI 参数）、originality `--strict`（`Total: 189 Suspicious: 0`）、`spec_audit --json --strict`（`coverage_pct: 100.0`）、`golden_audit --gate --require-markets`、reachability `--strict`（无未登记孤儿 ✓）、`contract_audit --ci`（**63 契约 / 155 业务 capability**，与第 17/19 步逐项相同）、docs links（82 文件）**全部 RC=0**；⑦ **残留 `max_age` 只在两类地方**：`docs/archive/plans/*`（历史方案，按"历史不改写"口径保留）与守卫里指认它的注释/断言字符串。同一格失明在文档面的补集仍在：`docs/providers/*.md` 的 per-provider 承诺长文（`FreshnessViolation` 一类）尚未纳入 `_EXACT_CLAIMS`，见 F-44 |
| F-44 | P1（对外契约类：错误码树里有从未发生的叶子） | **49 个错误类中 7 个叶子没有任何 raise 站点，其中 2 个是文档明文承诺的对外行为**（Phase 5 第 20 步顺带实测，AST 遍历 `tstdx/` 全部 `raise` 目标与类继承树）：真正的基类可以只被继承——`TransportError`（6 个子类全部有 raise）、`StreamError`（`SubscriptionError` 有）、`ProfileError`（`ProfileUndetectable` 有）都属正常；**从未被抛且无被抛子类**的是 `SourceUnavailable(E7050)`、`FreshnessViolation(E4060)`、`UnknownCommand`、`ChecksumMismatch`、`AntiSpiderBlocked`、`BackpressureOverflow`、`GapUnfilledError` 7 个。前两处不只是"没用到"，而是**对外承诺了一个不会发生的行为**：`docs/providers/README.md` §12 写"规范语义：`SourceUnavailable == selected Provider unavailable`"并规定各 Provider 文档统一用它，`tstdx/errors.py:494` 的 docstring 也说"上层可包装成本异常并记录 provider/channel/capability 后结束本次 Query"——而全仓没有一个包装站点（Provider 不可用时用户实际拿到的是 `ConnectionFailed`/`AllHostsUnreachable`/`WebSourceError` 等传输层原异常）；`docs/providers/tdx.md:151` 写"无法证明满足 freshness profile 时严格模式返回 `FreshnessViolation`"，而运行期**没有任何 currentness 校验器**（`currentness` 只是进 plan 的声明口径）。与 F-43 同族（幻影开关），只是这次幻影在异常树上 | **部分清偿（2026-09-19 第 41 步：(a) 中有落点的半边已接线，`FreshnessViolation` 自此有真实抛点；`SourceUnavailable` 半边实测无落点，余下面待 (b)/(c) 裁决）；三条路径原文保留**：三条待选路径 (a) 接线：在 Provider 失败路径统一包成 `SourceUnavailable`（context 记 provider/channel/capability），并为 `currentness` 落一个可判据的运行期校验器，无法证明时抛 `FreshnessViolation`；(b) 改口径：把 §12 与 `tdx.md` 的承诺改写为"实际抛点即传输层异常"，并从 `__all__` 与 E 段树里删掉不打算兑现的叶子（clean break，与 F-40/F-41 的删除口径一致）；(c) 折中：显式登记为 taxonomy placeholder，并加门禁禁止文档承诺未接线的错误类。本轮只把事实与分母钉进本文（判据可复算），**未**新增门禁——判据一旦落笔就必须先定 (a)/(b)/(c)，否则会把"未兑现"写成契约（F-24 的教训同形）；该裁决与 F-13/F-16 的"不静默"口径、F-37 的 (b) 属同一次产品决定。**执行记录（第 41 步，2026-09-19：(a) 只做到有落点的那半边）**——`currentness` 半边已接线：新增 `tstdx/runtime/freshness.py`，`DirectProviderExecutor.execute()` 在任何 Provider I/O 之前调用 `verify_currentness(plan, strict=…)`，判据只读两件事——注册表事实 `ChannelSpec.local` 与 plan 声明的 `CurrentnessMode`，因此可判据、不依赖墙钟、不解析行内时间戳（行内时间戳在本仓有三种互不相通的写法且时区口径未定，拿它做"未来日期"判据会因主机时区在每天前 8 小时误报，见 §1 第 41 步）。落地效果：`local` channel（实测全仓只有一个：`local_vipdoc/vipdoc → bars`）被要求 `live`/`business` 当期口径时，`strict=True` 当场抛 `FreshnessViolation`(E4060/503) 并带 `provider/channel/capability/currentness/local/reason` context，非严格模式把它作为新告警类别 `currentness_unproven` 随结果出发（不静默，F-13/F-16 口径）。`live` 打非 live channel 仍留在规划期 `ValidationError`/422（输入错误与结果是否新鲜无关，本步不改写成 503）。**`SourceUnavailable` 半边实测无落点**：2100 个 (provider, capability) 组合里 0 个能到达执行器的"没有可执行 Direct Provider binding"分支（静态矩阵不匹配在 `QueryPlanner.compile` 就以 422 结束），而 §12 同时规定 Provider 的 `TdxError` 必须原样保留——即"选定 Provider 不可用"这件事今天的真身就是传输层原异常；把它统一包成 `SourceUnavailable` 需要推翻 §12 那条保护，属另一格对外契约。5 个既不被抛也不被投递的叶子（`SourceUnavailable`/`ChecksumMismatch`/`UnknownCommand`/`BackpressureOverflow`/`CompatibilityWarning`）就地登记为 F-68，并由 `tests/architecture/test_error_promises.py` 把守：对外文档点名的错误类必须有真实站点，豁免必须挂仍然开着的 F 号、且一旦接线就必须撤销 |
| F-45 | P1（可观测性类：分页终端把"空"读成"耗尽"，F-37 因此能在一片全绿里隐形） | **`bars()` 首页 0 条与"历史已经取完"共用同一个出口，调用方拿到的是"请求成功、恰好 0 根"**（Phase 5 第 21 步实测）。`tstdx/client/_mixin.py::_t_bars` 的分页循环里，空页出口是一行 `break  # 空页：历史耗尽（正常终止）`，紧随其后的截断判据 `len(bars) < count and drifted` 只在**锚点漂移**时成立——空首页因此既走不到告警、也走不到 `strict` 抛错，三张服务面同样看不见任何异常。F-37 在线冒烟实测到的现场正是这一形状：主站对 `0x052D` 只回 2 字节 `count=0` 空桩。与 F-43/F-44 同族（幻影开关、幻影叶子），但这一格更严重：**幻影在这里以"成功"的面目出现**，离线门禁的判据（"有返回、无异常"）永远满足，于是结构性失明不是缺一条断言而是缺一个区分。`_t_export_security_list` 的 `0x044D` 同形缺陷一并实测在案（首页 0 条 ⇒ 读成"这个市场没有证券"，而 0/1 两个市场必有数千标的）。一项旧测试 `test_client_f1.py::test_empty_first_page_no_warning` 用 `warnings.simplefilter("error")` 断言"空首页不许留痕"，**把缺陷本身钉成了契约** | **已清偿**（2026-09-19，`44b02fb`）：① 判据改为**耗尽只会表现为短页或次页空**，首页空响应是另一件事（该标的无此周期历史，或主站对这个命令只回空桩）；② **默认只加可观测性、不改数据**——首页空 → `UserWarning` 且返回仍是空序列（与旧行为逐字节一致），`strict=True` → `TruncatedDataError`（与既有漂移截断同级），`_t_export_security_list` 接同一判据；③ 那条把缺陷写成契约的测试改判为 `test_empty_first_page_warns`，理由写进 docstring，另新增离线回归 4 项，其中"次页空仍须静默"用 `simplefilter("error")` **反向**钉住，防止这次改动把正常耗尽一并变成噪声；④ `docs/errors.md` 的 `TruncatedDataError` 条目列出 `_mixin.py` 全部抛出点，**刻意不写条数**（F-42 的教训：数量抄本就是下一个过期点）；⑤ 变异验证 2 条（`empty_first_page = not seen`→`= False`、`if not out:`→`if False:`）各自行列失败——把判据退回静默路径必红。合树复测数字见 §1 第 21 步条目。**两个未闭合点如实登记**（**已清偿**，2026-09-19 第 26 步：告警改由 `tstdx/diagnostics.py` 的单一发射口进入 `ResultMeta.warnings` 并逐条上 wire，`strict` 由内核集中读取，见 §1 第 26 步与 §0.3 F-51）：`QueryResult` 没有 warnings 通道，这条告警只活在调用方进程 stderr，HTTP/WS/MCP 三面的 wire 里看不见"本次结果为首页空桩"；唯一业务入口 `Client.bars()` 没有 `strict` 形参（`strict` 只存在于 `TdxClient.bars`），经内核绑定的路径拿不到"必须完整"的语义。两处都要改 `tstdx/client/api.py` 与 `tstdx/result.py`，第 21 步落地时这两个文件正被并行会话编辑，故本步未代为决定——与 F-44 不同，它们是**内部形状**而非对外承诺，接线不引入新的对外契约，可与 F-18 一并处置 |
| F-46 | P1（零缓存口径类，与 F-43 同族：`max_age` 的判据只覆盖一等字段，袋里的字符串键是它的补集） | **`allow_partial` 是折进 `options` 袋的 `max_age`**（Phase 5 第 22 步实测）：`QuerySpec.build(allow_partial=...)` 把它写进袋、`QuerySpec.allow_partial` 属性把它读回来，而全仓对它只有**一条校验**（"仅支持 quotes"），执行面 `tstdx/runtime/`、`tstdx/client/`、`tstdx/integration/` **零读取**——`BatchResult.partial` 由 `errors` 与逐 symbol 三态推导（`tstdx/batch.py:52-79` 要求 `partial` 与 `errors` 严格一致），所以它本来就是结果事实，不是可以被"放行"的策略。净效果：在 quotes 上设置它什么也不改变，在非 quotes 上只是提前报错；而 `Client.quotes_batch()` 根本不接受该参数，它只能经 `QuerySpec.build` / `options=` 这张通用面到达。第 20 步的字段门禁看不见它，因为 `dataclasses.fields(QuerySpec)` 里已经没有这个名额——**降级成袋里的字符串键，正是绕过"字段必须被消费"判据的那条路** | **已清偿**（2026-09-19）：① `build()` 形参与 `allow_partial` 属性物理删除（clean break，不留别名），quotes 之外的专属校验一并删除；② 新增 `tstdx.query.REJECTED_OPTIONS: dict[str, str]`（`allow_stale` + `allow_partial`，各带一句"为何无对象可作用"），`normalized()` 逐键当场抛 `ValidationError`，袋里的键从此**要么被执行面消费、要么明确拒绝**，没有第三种；③ 新门禁 `test_option_bag_keys_are_executed_or_rejected`：AST 取两处写入点的字面量键（`build()` 内折叠 + 生产代码 `QuerySpec.build(..., options={...})` 显式注入），与执行面实际读取的键（`options.get("k")`/`options["k"]`）求差必须为空，且每个 `allow_*` 形参必须落进袋里；消费扫描与调用点扫描各带"零命中即自曝失明"断言。同一轮把 README 与 `docs/providers/README.md` 里被拒键的手抄清单改成**按 `REJECTED_OPTIONS` 引用**（F-39/F-42：抄本必过期）；④ **变异验证 6 条**：未折叠的 `allow_noop` 形参 → `['allow_noop']（收下即丢）`、`build()` 内折叠幽灵键 → `幻影开关：['allow_noop']`、生产调用点注入 → `幻影开关：['phantom_key']`、分别改瞎消费扫描与调用点扫描 → 两条自曝断言各自命中、未变异对照 GREEN。首轮还暴露门禁自身一个**运算符优先级缺陷**（`folded | injected - rejected` 因 `|` 优先级低于 `-` 而让折叠键绕开拒绝集，`allow_stale` 被误报），加括号后修正——继第 20 步的 `dict.fromkeys` 共享集合之后，同类门禁第二轮又一次先红在自己身上；⑤ 复测数字见 §1 第 22 步。**第 23 步追记（本行的②当时言过其实）**：`REJECTED_OPTIONS` 只拦名单内的策略键，任意陌生键（`options={'max_age': 0}`）仍可入袋并被 `compile` 原样带出——"没有第三种"在字面上并不成立，实测见 §1 第 23 步；现已由 `EXECUTED_OPTIONS` 白名单补全，袋里的键从此真的只剩两种下场。
| F-47 | P1（对外契约类：入参面的另一半） | **三张服务面对"未声明的请求字段"一律静默忽略，于是刚被删掉的旋钮在 wire 面上重新变成"看起来生效"**（Phase 5 第 23 步实测）。F-43/F-46 把 `max_age` / `allow_partial` 从构造面清掉后，同一个调用在三个入口上的下场并不一致：`Client.bars(..., max_age=0)` 与 `QuerySpec.build(..., max_age=0)` 都 `TypeError`，而 HTTP `GET /v13/bars/600519?max_age=0` 返回 **200 + 正常结果**——FastAPI 只绑定声明过的形参，多余的查询串参数无人过问；`POST /v13/query/{capability}` 只 `payload.get("args"/"kwargs"/"provider"/"channel"/"currentness")`（`runtime_http.py:79-96`），body 里其他键直接蒸发；WS `_dispatch` 全部经 `params.get(...)`（`runtime_ws.py:117-…`），未知 params 同样静默；MCP `tools/call` 把 `arguments` 原样交给 handler（`_server.py:218-221` 只做"是不是对象"的形状检查），而 9 张 `inputSchema` **没有一张声明 `additionalProperties: false`**（`mcp/_common.py` 的 `_str_prop`/`_int_prop`/`_list_of_strings` 产出的形状里根本没有这个键）——schema 因此既不构成约束也不构成拒绝。以上四条在同一轮以探针实测（`probe_f47.py`，孤立 worktree `ac49328` + 本步文件，`PROBE_RC=0`）：`Client.bars(..., max_age=0)` → `TypeError: Client.bars() got an unexpected keyword argument 'max_age'`、`QuerySpec.build('bars', symbols=[…], max_age=0)` → 同形 `TypeError`，而 `GET /v13/bars/600519?max_age=0` → **200** 且转发参数只有 `{provider, policy, period, count, start, adjustment, currentness}`（`max_age` 蒸发）、`POST /v13/query/rates` body 带 `max_age` → **200** 且转发只剩 `{provider:'boc', channel:None, currentness:'business'}`、WS `bars` 的 params 带 `max_age` + `bogus` → `"result" in out` 且 `error: None`，MCP 侧 `additionalProperties` 命中清单为 **`[]`（9 张 schema 全无）**。**同一轮还量出构造面的同形缺口**：`REJECTED_OPTIONS` 只拦已知策略键，任意键仍可入袋——`QuerySpec.build('rates', provider='boc', options={'max_age': 0})` 通过 `normalized()`，`QueryPlanner().compile(spec)` 原样带出 `options == {'max_age': 0}`，而 `Client.execute(spec)`（`tstdx/client/api.py:90`，公开方法）正是这条袋路径的公开落点，故第 22 步"剩下的风险需要调用方自己构造"一句应当读作"调用方经 `Client.execute` 就能构造"。**这一格本步已就地清偿**：`normalized()` 现按 `tstdx.query.EXECUTED_OPTIONS` 白名单 fail-closed，`options={'max_age': 0}` 当场报 `unknown_options=['max_age']`（变异验证见 §1 第 23 步），于是 F-47 的未决面收缩为下面这三张 wire 面。**同一轮把"已声明字段"这一半钉住了**：新增 3 条门禁实测 10 条 HTTP 路由的每个形参都进入执行路径、9 个 MCP 工具的"声明属性 ↔ handler 读取"双向相等、`METHODS` 名单与分派分支双向一致（其中 `trades` 经 `method in {...}` 分派，扫描必须认识集合字面量，否则门禁自己先误报）。剩下没钉的是"未声明字段"这一半 | **已清偿（2026-09-19 第 40 步按用户裁决 (a) 三面 fail-closed 落地）；三条路径与理由原文保留**：(a) 三面 fail-closed——HTTP 查询串与 body、WS `params`、MCP `arguments` 统一按白名单核对，未知键即 `ValidationError` / `ERR_INVALID_PARAMS`，并让 MCP schema 的 `additionalProperties: false` 真正被执行（只加 schema 不加执行 = 把 F-44 那格"文档承诺一个不会发生的行为"复制到入参面）；(b) 维持宽容 + 把口径写进 `docs/api/*` 与 MCP 工具描述，并纳入事实型文档门禁，让"未声明字段被忽略"成为显式承诺而非沉默；(c) 折中——JSON 入参面（POST body / WS params / MCP arguments）fail-closed，HTTP GET 查询串维持宽容（浏览器与代理会追加 `_=…` 一类缓存穿透参数，严格化会误伤）并把这处不对称写进文档。**为什么不自行拍板**：与 F-44 同格，这是对外契约——(a) 会让今天返回 200 的请求开始返回 4xx，属破坏性变更；判据本身已可复算（三面各自的读取点即白名单），故先钉事实与三条路径，与 F-13/F-16/F-37/F-44 一并裁决。**执行记录（第 40 步，2026-09-19：(a) 落地，(b)/(c) 未采纳）**——唯一拒绝口新增于 `tstdx/integration/wire_fields.py`（`reject_undeclared`：人读的那句话把未知键念出来，机读侧同给 `context.unknown_fields` 与 `declared_fields`，`phase=wire_validation`，`ValidationError` ⇒ HTTP 422 / JSON-RPC `-32602`），四面各接这一条、没有第二份实现可漂移。白名单来源逐面不同但都是**声明本身**：HTTP 查询串 = `route.dependant.query_params`（运行时现取，实测 10 条路由、7 条共 19 个已声明字段）、HTTP body = `QUERY_BODY_FIELDS`（5 键）、WS `params` = `WS_PARAMS_FIELDS`（按 `METHODS` 逐方法给，含两个空集）、MCP `arguments` = 该工具的 `inputSchema.properties`，同时 9 张 schema 补上 `additionalProperties: false` 且由服务端真实执行。本行预告的破坏性变更如期发生：多余的查询串 / body / `params` / `arguments` 键从静默忽略改为当场被拒，`docs/api/interfaces.md` §3 就地写明口径（含客户端爱加的 `_=…` 缓存穿透参数同样被拒；同轮实测仓内没有任何内部代码或文档示例请求这些路由，故没有一处示例因本步变成错的）。**两处实测事实记在这里以免后人重踩**：① `runtime_http.py` 不许再加 `from __future__ import annotations`——`Request` 在工厂函数内局部 import，PEP 563 下 FastAPI 解析不出该注解，会把依赖的形参当成必填查询参数，接上闸后**全站 422**（两变体探针定因，见 §1 第 40 步）；② 两张 JSON-RPC 面的人读位置不对称，WS 在 `error.data.message`、MCP 在 `error.message`，判据各按本面真实公开的读法断言。新门禁 46 项（6 推导 + 40 行为）与 13 发变异的逐发红数、孤立 worktree 复测数字见 §1 第 40 步 |
| F-48 | P1（零缓存口径类，与 F-43 同族但深一层：幻影不在字段上，而在字段折进的对象里） | **`deadline_ms` 被规划器折进 `plan.budget` 之后再无人读，于是"查询总预算"是一张对外收、对内作废的支票**（Phase 5 第 24 步实测，AST 扫 `tstdx/` 全部 `plan.X`/`*.plan.X`/`spec.X`/`*.spec.X` 读取点）。`QuerySpec.deadline_ms` 带着 `build()` 形参与 `> 0` 校验住在 `tstdx/query.py`，`QueryPlanner.compile()` 把它折算成 `QueryPlan.budget: ExecutionBudget`，而 `ExecutionBudget.ensure_remaining()` / `remaining_s()` 两个方法在 `tstdx/` 内**零调用点**——执行面每一跳的 socket 超时自始至终取的是配置值 `DirectProviderExecutor.timeout`。后果不是"数值差一点"而是**方向相反**：调用方要 250ms 预算，仍然拿到 5 秒（`tstdx.toml` 调到 30 秒时更糟）的挂起容忍；`min()` 之所以从未生效，是因为根本没人取小。第 20 步的字段门禁抓不到它，因为判据是"`QuerySpec` 字段有没有被读到"，而 `deadline_ms` 的读取点全在规划器自己身上（折进 budget 的那一行）——**规划器自读被算成了消费，而它只是搬运**。**同一轮实测另一处同形失真**：`ExecutionBudget` 的 docstring 写着 "shared by the whole logical query"，但 `policy` 扇出会为每个 symbol 重新 `compile()`、从而重新计时，该句只对单次 `execute()` 成立 | **已清偿**（2026-09-19，`524c687`）：① 执行面新增唯一取数口 `DirectProviderExecutor._hop_timeout(plan)` = `min(配置 timeout, budget.remaining_s())`，并先跑 `ensure_remaining("provider_request")`——**预算已耗尽时在建立任何连接之前**抛 `ReadTimeout`；② 该读数贯穿每一跳：`_tdx_client(timeout)` 改为收形参（7 个 `_tdx_*` 直调执行器 + composed 原始读全部经它），`_migrated_capability` 一次取值 `hop` 后分发给 `WebQuoteSession` / `F10Client` / ex·goods·mac 族客户端 / `direct_adapter` / `_web_adapter_call` / `_composed_call`（后者签名从此强制要求 `timeout`），5 个 Web 直调执行器（`_web_quotes`/`_tencent_bars`/`_sina_bars`/`_eastmoney_bars`/`_baidu_bars`）各取一次；③ **默认路径可证明未变**：`deadline_ms` 默认 5000 与 `[core] timeout` 默认 5.0 同值，故 `min()` 在默认配置下逐字节等于旧行为，专门留一条断言钉住（`== 5.0`；**该形状自本步提交起从未通过，判据现为 `4.9 < hop <= 5.0`，见 §0.3 F-53 ①**），本步不引入任何默认超时收紧；④ 判据补齐：字段侧改为"读的人必须在执行面"（`tstdx/query.py` 自身的读取不算），`spec → plan.budget → 执行面` 这条折叠链由 `QueryPlan(...)` 构造处的 AST **推导**而非手抄名单（F-39 口径），另点名核验 `budget` 确由 `deadline_ms` 承载；⑤ **结构判据而非逐函数断言**：`self.timeout` 在 `executor.py` 里只允许被 `_hop_timeout` 读一次，其余任何函数出现即红——逐函数写断言追不上"新增一个直调执行器"，变异 M3 正是首轮从这条缝里活下来的（当时只有 `_tencent_bars` 被测到），补结构判据后同一变异点名 `test_web_route_also_gets_the_bounded_timeout` 与结构判据双双变红；⑥ `ExecutionBudget` docstring 的"whole logical query"措辞**本步未改**：该文件（`tstdx/query.py`）测量期间正被并行会话编辑（第 23 步的 `EXECUTED_OPTIONS`），按其 docstring 只描述对象自身契约的方式重写属下一轮；⑦ **本步判据的已知边界**：预算约束的是每一跳开始时的超时上界，不是硬取消——一跳已经发出请求后只能靠 socket 超时回来，故总墙钟时间上界是"deadline + 最后一跳的容忍"，且多跳累计会随重试增长（每跳重读 `remaining_s()`，只会越给越少，不会放大）；`stream` / `trade` 两张面不经执行器，因此不受该预算约束。复测与变异数字见 §1 第 24 步 |
| F-49 | P1（重复实现类：F-45 的判据被一份影子实现绕开） | **`security_list_all` 在 composed 面自带第二份游标分页，而它恰好是 F-45 刚修完那一族的漏网之鱼**（Phase 5 第 24 步实测）。`TdxClient.export_security_list` 早已有一份带页数上限、短页判据与空首页告警（第 21 步）的实现，`MigratedCapabilityBinding("security_list_all", …, "composed", "security_list_all")` 却在 `DirectProviderExecutor._composed_call` 里另写了一条 `while True` + `start` 游标循环：它**没有页数上限**（主站回桩时按 `page_size` 无界自增）、**空首页静默**（`if not page: break` 直接返回 `[]`），因而第 21 步给它立的判据永远覆盖不到这条路径；`catalog/capability.py::_validate_composed` 还同时为它保留了 `required`/`allowed` 参数名单，把影子实现写进了契约校验。**实测口径如实**：`0x044D SECURITY_LIST` 在 `COMMANDS` 里是 `STATUS_OFFLINE`，`_guard_offline` 会先 fail-fast，故这条路径今天不会返回错数据——缺陷是**形状**（一旦该命令解封或有人复用这条 composed 路径，无界循环与静默空页立刻生效），不是当场的数据事故 | **已清偿**（2026-09-19，`524c687`）：① 绑定改指真相源 `("tdx_client", "export_security_list")`——页数上限、短页判据、空首页告警从此只有那一处，F-45 的判据自动覆盖这条能力；② `_composed_call` 里那段 `while True` 游标循环**物理删除**（clean break，不留兼容分支），`_validate_composed` 的 `required`/`allowed` 两张表同时失去 `security_list_all` 条目（能力目录仍由绑定表生成，对外 capability 名与签名不变）；③ 反向门禁 `test_security_list_all_is_not_a_second_pagination_implementation` 钉两件事：绑定 `(backend, method)` 必须仍是 `("tdx_client", "export_security_list")`，且 `_composed_call` 函数体内**不允许出现任何 `ast.While`**——判据落在形状上而非命令名清单，故"给另一条能力再写一份游标循环"同样当场变红；④ **变异验证 2 条各自指名**：改回 `"tdx_client", "security_list"`（原始单页调用）→ 报绑定元组不等；在 `_composed_call` 里插回一个 `while True` → 报 `composed 面重新出现了游标分页循环（第二份实现回来了）`。**本步判据的已知边界**：结构检查只覆盖 `_composed_call` 这一处宿主；若第二份实现以后落在别的函数里，需要把同一扫描扩到执行器全部成员——已按"未登记孤儿一律接线或删除"的口径留在 F-45 的尾条一并处置 |
| F-50 | P1（计划面形状类，F-48 判据的对偶：缺陷不在入参上，而在入参折进对象之后留下的副本上） | **规划器把注册表事实抄到 plan 上、抄本没有读者，而预算对象的另一半从来没有一个调用点**（Phase 5 第 25 步实测：AST 遍历 HEAD `288e62f` 的 `tstdx/**` 全部属性读取点）。三件事同时成立：① `QueryPlan` 带着 `deadline_ms / batch_limit / live_channel / local_channel` 四个字段，`compile()` 逐行写入，而 plan 之外的读取点为 **0**——`deadline_ms` 全包 4 次命中全部落在 `query.py` 自己体内，其余三个字段在全包内**连一次属性读取都没有**。第 24 步刚把 `deadline_ms` 接进 `plan.budget`，plan 上那份旧副本因此成了第二个真相源；② `ExecutionBudget` 的 `max_attempts` / `attempts` / `begin_attempt()` 在 `tstdx/` 内零调用点（`begin_attempt` 全包读取 0 次），第 24 步点亮的是 `remaining_s` / `ensure_remaining` 这半边，"执行次数预算"仍是一张空支票，而且带着 `threading.Lock`——比幻影入参更隐蔽，因为读代码的人会以为计时器在工作；③ `ChannelSpec.batch_limits={"quotes": 60}` → `batch_limit_for()` → `plan.batch_limit` 是一条完整死链，其唯一读者就是写入它的规划器（`batch_limit_for` 全包命中 **1** 次），而它声称的 60 与真正生效的分片上限 `tstdx/client/_mixin.py:75` 的 `_QUOTES_SNAPSHOT_BATCH = 80` **直接矛盾**。**根因与 F-43/F-48 同族：规划期的"搬运"被算成了消费**，而第 20/24 步的判据分母都只覆盖到入参那一侧 | **已清偿**（2026-09-19）：① 四个 plan 字段与 `ExecutionBudget` 的尝试记账整体**物理删除**（clean break，不留别名），`budget` 从此是 deadline 的唯一载体；对象 docstring 同步改写为"一次 `execute()` 的墙钟上界"并写明为何刻意不含尝试计数（重试属传输池 `[core] max_retries` 与 fallback 策略）——顺带关闭 F-48 尾条 ⑥；② 死的 `batch_limit` 链按"无理由孤儿一律接线或删除"取**删除**一侧：接成执行期校验会静默把 quotes 分片从 80 改成 60（改变默认行为），与第 24 步"默认路径逐字节不变"的口径冲突，故注册表不再声称任何批量上限，数字只留在真正执行它的 `_QUOTES_SNAPSHOT_BATCH` 旁边；③ 随链一并删除 `remaining_ns()`（外部读者为零，`remaining_s()` 就地展开）与 `ExecutionBudget` 唯一需要的 `threading` 导入；④ **不留新孤儿**：`ChannelSpec.live/local` 的真实读取点仍在（`query.py:597` 的 `currentness=live` fail-fast、`:605` 的错误上下文、`catalog/provider_bindings.py:240` 的绑定对账），删掉的只是 plan 上的抄本；⑤ 两条新的**计划面结构门禁**：`test_every_query_plan_field_is_read_by_the_execution_face`（分母取 `dataclasses.fields(QueryPlan)`，读取扫描跳过 `query.py`）与 `test_execution_budget_has_no_unexercised_member`（每个公开实例方法必须经由 `budget`/`plan.budget` 被执行面读到；构造子 `from_deadline_ms` 因"它就是折叠那一步"例外，且例外前提自身要核验），两条都带"零命中即自曝失明"断言；⑥ 注册表侧反向门禁 `test_the_registry_declares_no_batch_quota`（字段不存在 + `repr` 不含 `batch_limit` + `build()` 签名无该形参 + 真正生效的 `_QUOTES_SNAPSHOT_BATCH == 80`），并把原先维持死面的两条 `batch_limits` 用例改写成注册表**真正执行**的那条规则（`periods` 只属于 bars channel）与位置式构造的兼容断言；⑦ 三处"对着抄本断言"的测试改为对着真相源断言：`test_query_contracts.py` 的 live/local 判断改读 `PROVIDERS…channel(...).live/.local`，`test_query_planner.py` 的 deadline 断言改比 `plan.budget.deadline_ns` 差值与 `remaining_s()` 次序。复测与变异数字见 §1 第 25 步。**编号说明**：F-51 已被并行会话的 warnings/caveat 一侧占用（见 `tstdx/client/_mixin.py` 注释），该行留待其自行登记，本表跳号不是漏项 |
| F-51 | P1（可观测性的最后一段，F-43/F-45/F-47 同族里"结果侧"的那一半） | **一次查询里所有"这条数据有瑕疵"的判断只活在执行进程的 stderr 上，结果本身不带任何痕迹；而解码层的静默修正连 stderr 都到不了**（Phase 5 第 26 步实测）。两半同时成立：① 第 21 步给分页立了判据（首页空 ≠ 耗尽、锚点漂移 ⇒ 截断），但 `warnings.warn` 的落点是**调用方进程**——经 HTTP/WS/MCP 进来的请求，告警打在服务端一侧，`QueryResult` 与 `meta` 没有任何字段承载它，wire 上读不到"本次结果为首页空桩"；唯一业务入口 `Client.bars()` 也没有 `strict` 形参（`strict` 只存在于 `TdxClient.bars`），走内核绑定的路径拿不到"必须完整"的语义。这正是 F-45 尾条登记的两处未闭合内部形状；② 更靠前一层同形断链：解析器经 `BaseParser.warn_ctx()` 记录"count 失真已钳制 / 第 N 条记录不完整已停止解析 / 资讯正文截断 / L2 置信度不足回落 L3"等**静默修正**（`tstdx/protocol/` 内 9 个发射点：`registry.py:304`、`parsers/_std7709_bars.py:321/336/437/451`、`parsers/std7709_extra.py:275`、`parsers/std7727.py:312`、`parsers/mac.py:332`，另加 `registry.py:408` 的 L3 回落），这些字符串进了 `ParseResult.warnings` 之后在执行路径上**零读取点**——唯一读者是 `ParseResult.to_dict()` 的自省字典（`protocol/registry.py:91`）与 `protocol/generic.py:339` 的 L3 原始透传，即"声明 5 条实收 3 条"从产生起就没打算让任何人看见。**②比①更静**：①至少还有人拿到 stderr，②两边都没有 | **已清偿**（2026-09-19）：① 新增唯一发射口 `tstdx/diagnostics.py`——`record_warning(code, message, *, stacklevel=2, stderr=True)` + 12 个 `WarningCode` + `ResultWarning(code, message).to_dict()` + `contextvars` 收集器 `warning_sink()`；全仓结果侧告警改由它发射（裸 `warnings.warn` 只剩 `diagnostics.py`/`deprecation.py`/`runtime/executor.py` 三处，由架构门禁的白名单双向把守），stderr 文案与 `stacklevel` 语义逐字不变，**对外行为零变化**；② `ResultMeta` 新增 `warnings: tuple[ResultWarning, ...] = ()`，`DirectProviderExecutor.execute()` 在返回前读本次收集器并填入——空元组从此是"这次结果干净"的**证据**而非沉默；`integration/serialization.py` 逐条写进 `meta.warnings`（additive 键，HTTP/WS/MCP 共用同一份序列化），三张服务面第一次能在响应里看到瑕疵清单；③ `strict` 收进内核单点：`execute()` 在发起任何 I/O 之前集中读 `options["strict"]`（非 bool 当场 `ValidationError`；键已入 `EXECUTED_OPTIONS` 白名单，第 23 步的袋门禁因此覆盖它），有任何瑕疵即抛 `TruncatedDataError`——任何 Provider、任何瑕疵类别同一条判据，`Client.bars(..., strict=True)` 从此是公开入口；④ 解码侧断链接通：`_t_bars` 分页循环把每一页的 `result.warnings` 以 `WarningCode.DECODE_CAVEAT` 接进通道（②那一半的现场）；⑤ **三条防回潮门禁**（`tests/architecture/test_caveat_channel_gates.py`）：裸 `warnings.warn` 白名单正反两向、每个声明的 `WarningCode` 必须有发射点（分母取枚举而非名单，加了成员不发射即红）、`_t_bars` 必须**同时**读 `.warnings` 与调用 `record_warning`（只留一侧即红）；⑥ **本步判据的已知边界**：(a) `Client.typed()` 返回 `TypedQueryResult(data, capability)`，既不带 provenance 也不带 `warnings`——把瑕疵通道接进 Typed Query 面要改 63 张契约的返回形状，属对外契约变更，按 F-44/F-47/F-52 口径只登记不代拍板；(b) `strict` 刻意**未**开放为 HTTP/WS/MCP 请求参数，那属 F-47 的未声明字段裁决；(c) 通道覆盖"结果侧瑕疵"，不覆盖异常路径（`errors` 那一半另有归属）。复测与变异数字见 §1 第 26 步 |
| F-52 | P1（对外契约类，与 F-50 同族：注册表与 provenance 里另一批只写不读的声称） | **`ProviderSpec.display_name`/`role` 与 `Provenance.provider_timestamp` 都没有读取点**（Phase 5 第 25 步顺带实测，同一把 AST 尺子换到 `ProviderSpec`/`Provenance` 上）：11 个 Provider 各自写着 `display_name="Tencent Finance"` 与 `role="auxiliary_live"`，而 `tstdx/`、`scripts/`、`tests/` 三面对这两个字段的读取点均为 **0**；`Provenance.provider_timestamp`（`tstdx/result.py:57`）连同 `direct(..., provider_timestamp=...)` 形参在生产路径里从未被传入非默认值，序列化面（`integration/serialization.py`）按显式键构造 wire，**根本不包含它**——所以它既不影响执行也不到达调用方，只是让 `help()` 与 ADR 读起来像"provenance 里有源时间戳" | **已清偿（2026-09-19，两半各自按 (b) 删除：注册表半边第 27 步、provenance 半边第 30 步；下面三条路径与判据原文保留）**：(a) 接进自省面（`providers` 子命令与 HTTP 的 Provider 目录回显 `display_name`/`role`，Provider 真给出时间戳时才填 `provider_timestamp` 并补进 wire）；(b) 删除（clean break，与 F-40/F-50 同口径），并同步改写 `docs/providers/README.md` 的"Provider ID / display name / role"条目与 `docs/adr/ADR-013` 的示例；(c) 折中——`display_name` 留给文档/自省，`role` 与 `provider_timestamp` 删除。与 F-44/F-47 同批裁决：(b) 会改变两个公开 dataclass 的形状，属对外契约的破坏性变更，判据本身已可复算，故先钉事实与路径。**执行记录**：本行的 (b) 已由两步分头落地——`ProviderSpec` 现为 `id / channels / default`（第 27 步），`Provenance` 移除 `provider_timestamp` 并连带删除 `direct()` 的同名形参（第 30 步）；读取点判据与顺序敏感的形状清单分别写在 `tests/providers/test_registry.py` 与`tests/architecture/test_result_shape_gates.py`，判据本身不再要求裁决。(a)/(c) 未采纳：若日后要做，属新增对外承诺而不是本行回潮。数字与变异复测见 §1 第 30 步 |
| F-53 | P1（测量诚实类，F-21 家族：账本记的"绿"不属于它所声称的那棵树） | **两处"判据从未通过，而记录是全绿"同时存在于最近两步**（Phase 5 第 26 步实测）。① 第 24 步为"默认预算下超时不变"留的断言，是对单调时钟剩余量求浮点相等：`_hop_timeout()` 取 `min(self.timeout, budget.remaining_s())`，右侧必然是 `5.0 − 已经过的纳秒`，故 `assert client.kwargs["timeout"] == 5.0` 不可能成立。本轮在 HEAD `288e62f` 单开 worktree、按 CI 参数跑该文件，实测 `assert 4.9999834 == 5.0` **失败**；而同一步的账本记录写着 `ISO_FULL_RC=0 / 3356 tests / 0 failures`——二者互斥，只能是被测树与提交树不逐字节相同（第 25 步恰好把这条口径写成了自己的整改）。相邻的第 23 步当时量到同一条红（实得 `4.9999891`）却归因为"并行会话在途文件"，随后它带着这个形状进了提交。② 同一把尺子量 HEAD `9fbece0`：`test_every_documented_cli_example_parses` 红 **1 项**，offender 只有一条——第 25 步在 F-52 行的 (a) 路径里，把 `providers` 这个不存在的子命令写成了带 CLI 前缀的行内示例，门禁分不出『我在引用一条缺陷』与『我在推荐一个命令』。本轮在 `9fbece0` 的干净 worktree 复跑实测到它（CLI 当场报 invalid choice: providers，RC=1），同一测试在当前 HEAD `15bfb61` 上 RC=0：这条红由并行会话以 `9127d78` 单行改写清偿，`15bfb61` 顺带撤回了第 25 步挂在拼装树上的复测数字。**本步起初把它记成红 2 条、并由本步改写清零，那是两处失真**：2 来自一次跑在他人测量 worktree 里的读数（那棵树同时带着第 25 步原文与本步引用它时写下的可执行前缀，同一 offender 被列了两遍），而清偿动作根本不属于本步——记绿要靠复算，记红同样要靠复算，本行的判据反过来量到自己身上时一样成立。**根因同族**：把计时噪声写成契约（①）、把他人已清偿的红记在自己账上（②），都是 F-43/F-48 那一族"声称一个不会发生的行为"，只不过这次声称的是自己的测量 | **已清偿（代码 + 账本）**（2026-09-19）：① 断言判据改为 `4.9 < hop <= 5.0`，并把"另一侧是单调时钟剩余量、逐位相等即把噪声写成契约"的理由写在断言上方——本步真正要钉的两件事一件没松：默认预算下超时未被缩短到可观察量级、且绝不超过配置值；`4.9` 不是新造的宽容带（1 秒余量远大于计时噪声，又远小于任何可观察的超时收紧：默认 5 秒、被收紧时是 250ms 量级）。该文件已由并行会话带进 `9fbece0`（**代码在此、账本不在**），本轮只补账本、不重复改代码；② 归属更正、不复述为他人未做的清偿：F-52 行按 HEAD 原文整行保留（并行会话正在动注册表与 `tstdx/providers/__init__.py`，那一格的归属是他们，本步不越俎），只把自己账本里的数字与主语改对，并在提交前用两棵干净 worktree（`9fbece0` 与 `15bfb61`）各自复跑该门禁把现状钉死；③ §0.3 F-48 ③ 与 §1 第 24 步"专门留一条 `== 5.0` 的断言钉住"两处措辞就地更正，注明原形状从未通过；④ 顺带修一处从 `5c50487` 起就语法不通的活文档 python 示例（`docs/api/interfaces.md` 的 `options={,` → `options={}`），并如实登记它无人把守：CLI 侧门禁只认以 CLI 主命令名开头的行内/围栏命令位，围栏 python 块不在任何门禁的解析范围内（F-31 只校验 import 路径可解析）；⑤ **本步判据的已知边界**：这不是"门禁太松"，而是任何浮点/时钟**相等**断言都不可满足；同类形状再出现的出路是断言**次序**而非数值（第 25 步 ⑦ 已把 deadline 断言改成比 `plan.budget.deadline_ns` 差值与 `remaining_s()` 次序，同一条路） |
| F-54 | P1（注册表形状类，F-50/F-52 同族的第三处：注册表里没人执行的第二套词汇） | **`ChannelSpec.markets` 在 23 个 channel 上逐个声明，三面读取点为 0**（Phase 5 第 28 步实测，尺子沿用第 27 步那把）：注册表写着 `cn_a`/`cn_bse`/`hk`/`us`/`future`/`commodity`/`option`/`bond`/`fx` 这套字符串词汇，而 `tstdx/`、`scripts/`、`tests/` 里没有任何一处读它，也没有任何一处把它与 `tstdx.domain.symbol.Market` 对上——市场正确性实际由 `Symbol.tdx_market`（`tstdx/domain/symbol.py:122`，HK/US 抛 `SymbolError` 且 `provider_switch_allowed=False`）承担。注册表有 `require()` 管 capability、`require_period()` 管 period，唯独没有市场的执行位。`ChannelSpec.notes` 同病：零生产读取，全仓唯一的"读取"是第 25 步自己钉位置形状的那条测试，而它承载的那句"本地 vipdoc 不替代在线 TDX"在 `docs/providers/tdx.md:92` 早已存在 | **已清偿（第 28 步，默认走删除而非接线）**：(a) 接进执行面要先新造 `Market → 字符串 token` 映射，再与 `tdx_market` 形成同一件事的两套实现（第 24 步 F-49 刚清掉一个"第二实现"），且会把今天能成的请求变成报错——属对外契约变更且无实测收益，不取；(b) 采纳：`ChannelSpec` 收缩为 `id / capabilities / live / local / periods`，市场与定位留在 `docs/providers/*.md`（§8 模板第 2 条与 §9 已同步改写，注明代码面不再持有）。门禁 `test_every_channel_spec_field_has_a_reader` 与 `test_every_provider_spec_field_has_a_reader` 共用同一把尺子（分母取自 `dataclasses.fields`，读取点由 AST 扫 `tstdx/` 得出，owner 名人工核对），另加形状钉 `fields == {id, capabilities, live, local, periods}`；变异 M1（把 `markets` 以默认值加回）当场红并点名该字段，M2（把 owner 集合换成不可能命中的名字）触发"扫描零命中"自检。**测量方法本身的一条边界，如实登记**：曾尝试把这把尺子推广到 168 个 dataclass 做无人值守普查，两条独立证据判它不成立——它把第 27 步刚测过有 14/6/3 个读取点的 `ProviderSpec.id/channels/default` 判成零读，也把 `CoreConfig.*` 判成零读而 `tstdx/runtime/kernel.py:66` 真在读 `cfg.core.timeout`；原因是实例未绑定到具名变量（注册表里是元组成员）与 `self.` 根的属性链。因此该判据只在"owner 名经人工核对的单个类"上有效，本账本里所有零读取结论都是逐类人工核对后写下的 |
| F-55 | P2（计划形状类，F-50/F-52/F-54 同族的第四处，这次在流计划面上） | **`StreamPlan.capability` 与 `StreamPlan.channel` 由 `compile()` 写入、`tstdx/` 全包读取点为 0**（Phase 5 第 29 步实测，尺子沿用第 27/28 步那把并加以强化）：`StreamPlanner.compile()`（`tstdx/stream_contract.py:65`）先对 `capability != "quotes"`、`channel != "quotation"` 逐条 fail-closed，再把两个结论原样抄进 `StreamPlan`；而 `StreamPlan` 的唯一消费方是 `Client.stream`（`tstdx/client/api.py:388`）与 `AsyncClient.stream`（`tstdx/client/api.py:512`），两者读走的只有 `symbols / provider / interval / diff_only / max_queue`——恰好是交给 worker 的实参。`runtime/executor.py`、`tstdx/result.py`、`runtime/identity.py` 里出现的 `plan.provider`/`plan.channel`/`plan.spec.capability` 全部属于 `QueryPlan`（同名不同物，逐处人工核对，第 28 步登记的"普查不成立"边界正是为此）。全仓对 `plan.channel` 唯一的"读取"是一条测试断言（`tests/runtime/test_v13_architecture_alignment.py`），它验证的是规则的抄本而非规则本身；`plan.capability` 连抄本读取都没有 | **已清偿（第 29 步，默认走删除而非接线）**：(a) 接线的形状是把 `StatefulQuoteStream` 改成收 plan 而非具名参数，即让公开的 `subscribe(symbols, interval=…, diff_only=…, max_queue=…)` 变成一个内部类型的投影；而今天只有 `provider=tdx` + `channel=quotation` 能通过编译，多传这两个值不改变任何一次请求的成败——不取；(b) 采纳：`StreamPlan` 收缩为 `symbols / provider / interval / diff_only / max_queue`，编译期两条 `ValidationError` 与 `PROVIDERS.require(…, channel="quotation")` 原样保留，删掉的只是抄件；抄本断言改为行为断言（`StreamSpec.build("sh600519", channel="quote")` 当场 `ValidationError`）。门禁 `test_stream_plan_carries_only_the_fields_the_client_reads` 比第 27/28 步更强：`dataclasses.fields(StreamPlan)` 必须与 AST 扫 `tstdx/client/api.py` 得到的 `plan.*` 读取集合**逐字相等**——新增字段没接线红，消费者读到幻影字段同样红；防盲保险为"`plan_fields` 非空"与"`api.py` 至少要读到一条 `plan.*`"。变异：M1 把 `capability` 以默认值加回 → RC=1 点名 `['capability']`；M2 把 owner 名换成不可能命中的 `no_such_plan_var` → RC=1 报"api.py 里读不到任何 plan.*，判据自身失效"；CONTROL 与还原后 RC=0 |
| F-56 | P1（服务面执行类，F-29 未覆盖的另一半：旁路不在 web 层，在流式面上） | **CLI `stream` 是最后一条不经 `Client` 的数据命令，而它绕开的恰是第 29 步那份抄件所指向的 fail-closed 契约**（Phase 5 第 29 步为找 `StreamPlan` 消费方而排查流式入口时实测）：`cmd_stream`（`tstdx/cli/runtime_commands.py:161`）`from ..streaming import QuoteStream`，以 `QuoteStream(provider=args.provider, **_transport_kwargs(args))` 自建对象，该类的 `_get_runtime()`（`tstdx/streaming/base.py:242`）惰性 new 一个自己的 `UnifiedRuntime`——这条命令既不经 `Client`（唯一业务入口），也不经 `StreamPlanner`（唯一流式契约）。两处矛盾随之落地：① `--provider` 是自由字符串（`tstdx/cli/parser.py:150`），`QuoteStream.__init__`/`_get_runtime` 只把它当 `default_provider` 透传、全路径无任何流式白名单判定，于是 CLI 面实际承诺"任何支持 `quotes` 轮询的 Provider 都能 stream"（`PROVIDERS.supports("tencent","quotes")` 为真），而库面 `Client.stream(provider="tencent")` 抛 `ValidationError`（"Stateful quotes stream 当前只存在 tdx Direct stream binding"）——同一件事两个面给出不同答案；② `tstdx/streaming/__init__.py` 的模块 docstring 声称"受支持的业务流实现是 `StatefulQuoteStream`/`AsyncStatefulQuoteStream`，历史的 `QuoteStream`/`AsyncQuoteStream` 已从公开/核心面移除"，而 `QuoteStream` 既在 `__all__` 里、又是 CLI 的生产路径（F-31 家族：docstring 与代码零矛盾的要求），`docs/api/interfaces.md` §5 更是把 `QuoteStream`/`AsyncQuoteStream` 当作流式订阅门面来写、通篇不提 `StatefulQuoteStream`。既有守卫抓不到它：F-29 的 `test_service_faces_never_import_the_web_layer` 只看服务面是否 import `tstdx.web`，第 9 步（F-28）只看选项有没有转发 | **已清偿（2026-09-19，第 31 步按默认 (a) 落地；下面三条路径与原始理由原文保留，作为裁决记录）**：(a) **采纳**：收口为 `Client(**_client_kwargs(args)).stream(syms, provider=…, interval=…, diff_only=…, max_queue=…)`，CLI 只翻译不执行；代价是 `tests/architecture/test_cli_connection_contract.py::test_stream_forwards_provider_and_connection_args`（当前以 monkeypatch `tstdx.streaming.QuoteStream` 钉住旧形状）要随之改写，该断言本身随第 26 步一起入库，不再是并发在途面。(b) 承认 CLI 需要一条"不建 `Client` 也能起流"的旁路，则同步改写 `tstdx/streaming/__init__.py` 的 docstring 与 `docs/` 口径，并把 tdx-only 判据从 `StreamPlanner` 下沉到 `QuoteStream`，使两面同答案。(c) 只收紧门禁不改行为：把服务面守卫从"不得 import `tstdx.web`"扩为"除 `tstdx.client` 外不得 import 任何执行面模块"，让这条旁路当场红着挂账。默认取 (a)，与 F-29 同口径；其代价可接受的理由是第 10 步已两次把 fake `WebQuoteSession` 换成 fake `Client`，"便于离线 monkeypatch"这一原始理由在同族里已被证明不成立 **执行记录（第 31 步）**：`cmd_stream` 现为 `with Client(**_client_kwargs(args)) as client: client.stream(...)`，连接参数走 F-27 的"只转达用户显式说过的"口径；`stream` 子命令的 `--provider`/`--host` 改由 `_provider_args()` 统一声明（该命令此前是 Tier-A 数据命令里唯一没有 `--host` 的一条）；`tstdx/streaming/__init__.py` 的 docstring 与 `docs/api/interfaces.md` §3/§5、README 的"流式订阅"行同步为"唯一入口 `Client.stream`，轮询基类不得由服务面直接构造"；新增结构性门禁 `test_service_faces_never_build_a_stream_themselves`，与 F-29 那条共用一次 AST import 边扫描，任何服务面 → `tstdx.streaming` / `tstdx.stream_contract` 的边即为红，扫描零命中时自曝"两条门禁同时失明"。数字与变异复测见 §1 第 31 步。 |
| F-57 | P2（出处词汇类，F-50/F-52/F-54/F-55 同族的第五处，这次在出处类别词表上） | **`ProvenanceKind` 声明三种出处，而零缓存内核只能生产一种；配套三条判定属性对 `tstdx/` 生产代码读取点为 0**（Phase 5 第 30 步量完 `Provenance` 的字段后顺着 `kind` 那一格读到时实测，尺子沿用第 27-30 步那把并补出成员维度）：`tstdx/` 全部 189 个模块里对 `kind` 的唯一赋值是 `Provenance.direct()` 写死的 `ProvenanceKind.DIRECT`，`REPLAY`/`SYNTHETIC` 两条成员的全部引用就是它们自己的判定属性 `replay`/`synthetic`（`tstdx/result.py:95,99`）——没有任何代码能造出这两种出处，却有代码在教调用方如何判断它；`real` 同理，全仓唯一读取点是 `tests/runtime/test_query_contracts.py` 的 `assert direct.real is True`，它验证的是规则的抄本而不是规则本身（第 29 步为 `StreamPlan` 登记过的同款现场）。`tstdx/tools/golden_audit.py` 里成片的 `ORIGIN_SYNTHETIC`/`.real`/`.synthetic` 属于该工具自有的 `Coverage` 类（同名不同物，逐处人工核对，第 28 步登记的"普查不成立"边界正是为此） | **已清偿（第 32 步，默认走删除而非接线）**：(a) 接线的形状是给测试/回放运行时开一条生产 `REPLAY` 结果的通路（把 golden 回放标成 `REPLAY` 再送进 `QueryResult`）——那是新功能，且方向与 v17 相反：它要在**公开结果面**上出现一种真实 Provider 读取永远给不出的出处；本仓的回放数据一律停在解码层断言字节，从不冒充运行期结果，不取；(c) 保留成员并注释"将来会用到"正是 F-50/F-52/F-54/F-55 逐次删除的形状；(b) 采纳：`ProvenanceKind` 收缩为 `DIRECT` 一个成员，`real`/`replay`/`synthetic` 三条判定属性一并物理删除（出处由 `kind` 字段本身说明，不需要一个只能返回 `True/False` 却无人按它行动的翻译层），`tests/runtime/test_query_contracts.py` 那条断言随属性一起删除——同族规则仍由 `assert direct.kind is ProvenanceKind.DIRECT` 与退役词汇 `cached`/`cache_hit`/`direct_fetch` 循环钉住；wire 侧 `integration/serialization.py:41` 发射 `provenance.kind.value`，删成员等于收窄线上词汇，这正是零缓存口径想要的而非新增限制（此前 `replay`/`synthetic` 也从没出现在任何真实响应里，`cache_tier` 仍恒为 `null`，第 30 步口径不变）。门禁 `tests/architecture/test_result_shape_gates.py` 两条新判据：`test_every_declared_provenance_kind_has_a_producer` 分母取 `{item.name for item in ProvenanceKind}`（F-43 口径：清单会过期，枚举不会），分子用 AST 扫 `tstdx/` 得到的 `ProvenanceKind.<MEMBER>` 具名引用集合，**双向**做差——声明了没人生产红、引用了不存在的成员同样红，再加"只能有 `DIRECT`"这条形状锁；`test_provenance_exposes_no_judgement_property` 钉住 `vars(Provenance)` 里 property 恒为空集，即翻译层不得回长。防盲三条沿用（`scanned > 30`、引用集合非空、声明集合非空）。尺子补出成员维度并顺带收掉第三份抄件：`tests/support/field_readers.py` 新增 `members_referenced(owner, skip=…)`，`tests/architecture/test_caveat_channel_gates.py` 那条 `WarningCode` 发射点判据改为调用它（原先自带一份私有 AST 遍历，同一把尺子抄第三次即本族要删之物）。变异 4 项：**M1** 把 `REPLAY` 成员装回枚举 → 红，点名 `['REPLAY']`；**M2** 让判定属性 `real` 回长 → 红；**M3** 让尺子失明（owner 名换成不可能存在的 `NoSuchKindEnum`）→ 红且报的是自检而不是"零幻影"；**M4** 生产者躲开扫描（`Provenance.direct()` 改为按值构造 `ProvenanceKind("direct")`）→ 红，`DIRECT` 也失去具名生产点。CONTROL 与逐项复原后 RC=0，`MUT_RC 0`。**本步不动的**：`docs/adr/ADR-013`、`CONTRIBUTING.md`、PR 模板里"replay/synthetic 不得冒充实时数据"那句是**禁令**而不是存在性声称，删成员让它更强而不是矛盾，故原文保留 |
| F-58 | P1（口径类，F-23/F-34/F-35 同族，这次量的是方案文档自己） | **§0.1「主链路贯通状态」与 §0.2「遗留不合理点」长期停在 Phase 3 之前的时态**（第 31 步之后，为回答「主体链路是否全部贯通」而复读本文档时实测）：§0.1 的两行 ❌ 把 v14 编排信封与 registry 三件套写成**现行断链**，其证据列点名的 `runtime/gateway.py`、`executor_registry.py`、`provider/router.py` 与`executor_bindings.py` 在磁盘上全部不存在（Phase 3A/3B 已整层物理删除），服务面那行还写着「全部 import `client_api.Client`」——那个根级模块也早在 Phase 3C 并入 `tstdx/client/`；§0.2 八行统一挂在「Phase 3–5 处理对象」标题下，其中 F-1…F-6 已在别处记为清偿、F-7/F-8 也已落地，却没有一行把判决写回表格，读起来像仍有八条待办。既有活文档门禁抓不到它：它校验反引号里的 `tstdx.x.y` 点号路径与 README 数字，**带斜杠的文件路径与表格时态都不在射程内** | **已清偿（2026-09-19，第 33 步）**：① §0.1 重写为五行现在时（内核主链 / 四个服务面 / 流式面 / 配置面 / 已删除的旧接缝），把「断链」改为「已整层删除故不可能断链」并指向防回潮守卫，同时就地写清两处「实现」的边界（真机冒烟未跑、F-37 的 2 字节桩、92 项 PENDING 契约、F-18 的链外守卫）；② §0.2 增列「现状」，八行逐条给裁决＋日期并指向清偿它的第几步；③ 新门禁 `tests/architecture/test_plan_status_gates.py`——§0.1 引用的每个 `…/….py` 必须存在于磁盘、§0.2 每行必须带裁决（已清偿/待用户决策/…）与日期或提交号，两节解析不出行即判「门禁自身失效」。本步只改文档与门禁，不改任何生产代码；变异与数字见 §1 第 33 步 |
| F-59 | **P0**（口径类，F-23/F-31/F-58 同族，但这次落在承重墙上：一句从未验证的协议断言决定了默认出站字节，而默认字节让 7709 K 线整族失效） | **`tstdx/protocol/handshake.py` 用两句"服务端不校验帧 3 内容、只校验长度"的断言，为一个不存在的测试文件与一个不存在的 golden 目录作证据，并据此起草默认的产品标识块——F-37 的"可达主站对 `0x052D` 只回 2 字节空桩"从头到尾是本端握手问题**（第 34 步实测）。三件事同时成立：① 断言点名的 `tests/integration/test_handshake.py`（含 `test_opaque_blob_tolerance`）与 `tests/golden/7709/_handshake/` **在仓库任何一次提交里都没有出现过**（`git log --all --diff-filter=A -- <两条路径>` 空输出；HEAD 的 `tests/integration/` 只有 4 个文件），即那句"实测"从未被实测；② 全仓对握手帧字节**零测试**（本步之前 `grep -rln "SETUP_FRAMES\|setup_frames" tests/ scripts/` 只命中 `.pyc`），默认值没有任何线路形状判据；③ 该断言把默认值推向"原样重放自采集样本"，而 2026-09-19 的逐位 A/B 证明**这块字节的内容会决定服务端给不给数据**：同主机、同一条新建连接、请求体逐字节照抄 golden，只换这 30 字节——样本块（含 GBK 券商名）⇒ `0x052D` 回 2 字节 `2003`，30 个零字节 ⇒ 180 字节 / 10 根真日线；7 台可达主机各 2 轮全同向，golden 采集主机 218.75.126.9 再交替 3 轮（该机累计 5 次），7/7 无一例外（第 8 台候选 119.147.212.81 两轮均连接超时，不计）。同一轮另测出三条边界：`b"A"*30` 与 `b"\xff"*30` 同样拿回 180B / 10 根 ⇒ 削成空桩是那 30 个样本字节特有的，不是"任何非零值都被拒"；只发帧 1+2（不发帧 3）在 7/7 可达主机上照样回数据 ⇒ **帧 3 不是取数的必要条件**，此前"三帧都得发"同样是未测断言；把全零块末 4 字节改成样本块尾巴 `00000002` 则 3/3 主机在握手中途直接断开连接 ⇒ 这块字节被**结构化解析**，"opaque/不透明"这个命名本身就在替一个错误模型说话。另有一条不稳定性按事实登记、不写成契约：完全不发握手帧时 6/7 主机对 `0x052D` 读超时，60.191.117.167 却回了数据，且 218.75.126.9 在相隔两分钟的两轮里一次回数据、一次读超时 | **已清偿**（2026-09-19，第 34 步）：① 默认产品标识块改为 **30 个零字节**（`_PRODUCT_ID_BLOB`，附 `assert len == 30`），"照抄抓包样本"这个做法连同其依据一并撤回；② **物理删除**三个从未有调用点的符号 `OPAQUE_BLOB_BYTES` / `opaque_blob()` / `build_setup_frame3()`（HEAD 全仓 `git grep` 除 `handshake.py` 自身 13 处外命中 0），`__all__` 同步收缩为 3 项，clean break 不留别名；③ 模块 docstring 重写为本步实测口径，并在 `.. important::` 段落里点名那两处假证据与"从未存在"的路径，`setup_frames()` 的 `blob` 说明改为"服务端对这块内容的反应见模块 docstring——它不是可有可无的填充"；④ `PROTOCOL_SPEC/7709/0x000D_HANDSHAKE.yaml` 的两处 "Server does not validate content, only length. Zeroed block also accepted." 与尾注段改写为可复算的实测事实（A/B 轮次、`A`/`FF` 反例、缺帧分布）；⑤ 新增 `tests/protocol/test_handshake_frames.py` **9 项**离线线路形状守卫：默认块 == 30 零字节、帧 3 的 `pkg_len == 32` 且两处长度字段与 `method=0x0FDB` 逐个对齐（`<BIBHHH>` 头的 [6:8]/[8:10]/[10:12]）、帧 1/2 的步骤号回显未动、覆盖只替换帧 3 的 body（STANDARD 与 MAC 两族）、长度非 30 即 `ValueError`、EXTENDED/F10/GOODS 三族仍返回空元组。**判据边界如实登记**：离线测试钉的是**出站字节形状**，服务端对这 30 字节的反应属真实网络行为、不在射程内（正是 F-38 那一格的形状），docstring 因此明写反应的重测方法见 §1 第 34 步，不把 A/B 结论伪装成断言。**变异 4 条全部 RC=1 且各自指名**（M1 默认块改回样本字节 → `test_frame3_default_product_block_is_thirty_zero_bytes`；M2 覆盖参数被忽略 → `test_override_replaces_only_frame3_body[quotation]` 与 `[mac_quotation]`；M3 删长度校验 → `test_override_rejects_wrong_length`；M4 无握手族也发三帧 → `test_families_without_handshake_send_nothing`[ex_quotation/f10/goods] 三条），CONTROL 与逐项还原 RC=0，测量树与主树 `cmp` 逐字节相同。**为什么取全零而不是"干脆不发帧 3"**：两条在本步实测都拿得到数据；取全零保留与真实客户端同形的三帧会话形状，`setup_frames()` 的帧数契约、两池握手计数与全部既有测试形状都不动，而"不发帧 3"要新增一项对外协议声称，不属本步。**未解释的**：服务端为什么对那 30 个字节回空桩而不是报错——机制未知，本行只登记可复算的行为，不写成因 |
| F-60 | P2（口径类，F-51/F-53/F-59 同族：接线是对的，漏网的是旁边那句硬编码） | **同一个空桩结果会发出两条互相否证的告警：`decode_caveat` 说『声明 800 条』，`bars_empty_first_page` 说『服务端声明 0 条记录』**（第 34 步实测）。把 2026-09-19 实测到的那个 2 字节空桩（帧头 `b1cb74000c01000000002d0502000200`，载荷 `2003`）原样注入传输层，其余走生产链路（`TdxClient(pool=fake).bars(...)`，解码器/分页模板/告警通道一律真代码；脚本 `s34_stubpair.py`，日志 `s34_stubpair.log`），实收 **2 条**：`[decode_caveat] ... 分页解码：count 失真已钳制：声明 800 条，按剩余字节 16B/条 只能容纳 0 条` 与 `[bars_empty_first_page] ... 首页即空响应：服务端声明 0 条记录，实取 0 根...`。前者是线路事实（`2003` 小端 = `0x0320 = 800`），后者是 `tstdx/client/_mixin.py:254-258` 写死的字符串，其上方 `:226-227` 与 `:252-253` 两处注释同样把空桩说成 `count=0`——**『声明 0』与『声明 800 而记录字节为 0』是两回事，而区分它们正是这条告警存在的唯一理由**；第 26 步 F-51 已把解码侧真话接进 wire（`:216-223`），本条因此不是静默，而是**一句旧文案与新接线当面打架**。**全仓 3413 项测试为何仍绿**：`tests/client/test_v5_pagination.py:173-185` 的 fake 用 `_bars_payload(0)` 合成空页，即载荷 `0000` = **真的声明 0 条**，fake 与代码犯了同一个错，两条断言于是彼此自洽；该测试还把 `caveats` 写死为恰 1 条，等于把『同一结果上两条互证』这个真实形状排除在射程外 ⇒ 门禁测的是自己编造的场景。与 F-59『证据指向不存在的文件』同族，但更隐蔽：这里的 fake、断言、文案三者全在，只是全都不等于线路。 | **已清偿**（2026-09-19，第 36 步）。① 声明数改为随 `ParseResult.meta` 暴露：`tstdx/protocol/registry.py:253-256` 把 `state["declared_count"]` 写进 `meta`（走 `meta` 的内部形状接线，不引入对外契约），分页侧 `_mixin.py:199` 声明 `first_page_declared`、`:228-232` 在首页空分支上读 `result.meta.get("declared_count")`；`:258-266` 把`BARS_EMPTY_FIRST_PAGE` 的文案改成按声明数**三分支**——`N>0` 说「声明 N 条记录却一个记录字节都没回，这是空桩，不是该标的没有历史」、`0` 说「声明 0 条：该标的无此周期历史，或主站对这条命令只回空桩」、读不到计数头（`Optional` 为 `None`）说「解析器没在响应里读到记录数头，声明数未知」，**不拿『未知』冒充『0』**，这正是本条原来犯的错的镜像；strict 分支的 `context` 加 `"declared"` 键（`:274-279`），让 `TruncatedDataError` 的机读侧与文案同数。② 空桩 fake 改喂线路实测的 `2003`（新增 `_StubPayloadPool`），判据从「恰 1 条告警」换成「**2 条且两条都含 800**、第二条不得出现『声明 0 条』」（`tests/client/test_v5_pagination.py:211-235`），并在真声明 0 的那条既有测试上补 `assert "声明 0 条记录" in ...`（`:202-204`）——两支各有主，互不覆盖。③ 原 `:226-227`、`:252-253` 两处注释同批改写为「按服务端声明数分家，而不是替它编一个数字」，`tstdx/diagnostics.py:56-57` 与 `docs/errors.md:25-26` 的口径同步。变异 M1–M4 各自 rc=1 并逐条指名（`mutate_s36.log`），全量与门禁读数见 §1 第 36 步。 |
| F-61 | P1（口径类，F-58 的镜像：同一张表现在把**做过**的事写成没做） | **§0.1 结论的第一条边界、本文 Phase 5 计划项 3 与 README 两处路线图，都把 Phase 5 第 16 步已经跑过的真实网络/服务面冒烟写成"尚未执行"**（第 34 步落地后重读 §0.1 时实测）：§1 第 16 步逐格记着七格真实冒烟 6 PASS / 1 FAIL 与 wheel 安装冒烟 `SMOKE_RC=0`，§4 验收清单该格已勾 `[x]`；而 §0.1 写着"真机冒烟尚未执行……需用户授权"、Phase 5 计划项 3 仍挂"⚠️ 仍未执行"、README 路线图行写"仍待：…… + 真实网络 smoke + tag"，「下一阶段」表还把已经跑通的 wheel 冒烟与三面 live 各一发列为计划。四处都不是保守而是失真：读者会以为主链从未在现网验证过，并把 F-37 读成"冒烟还没跑"而不是"跑过；K 线那一格已在第 34 步归因为本端握手字节并修好，余下是 `0x000F`/`0x0010` 字段错位待裁决"——本轮向用户复述现状时确实这样读过一次，错的是文档。成因：第 33 步重写 §0.1 时把"再跑一次须授权"（现行约束）与"从未跑过"（历史事实）揉进同一个短句，而凭印象写的边界既不挂账、也没有任何判据核对，于是全绿。第 33 步的门禁抓不到它——它核对文件路径存在性与 §0.2 的裁决格，**边界子句里的动作声称不在射程内** | **已清偿**（2026-09-19，第 35 步）：① §0.1 结论第一条改为"现网证据只到一次性冒烟为止"，就地标注第 16 步读数与第 34 步对 `rows=0` 那一格的归因，并把"仍缺"精确到两件事（一次工作日盘中复跑、F-37 余条处置裁决），"再跑一次真实网络仍须用户授权"作为现行约束保留；② §0.1 的第二条缺口清单同步换成有账可查的四项（F-37 余条、F-38、F-25 的 PENDING 面、F-18）；③ Phase 5 计划项 3 与 README 三处同批改口径，README 覆盖率一格按本步同轮日志重钉（原值 78.80% 是 Phase 5 第 4 步读数，此后整仓删码已把它推高）；④ **门禁补第三条判据** `test_open_boundaries_cite_an_open_finding`：§0.1 结论里每个带圈编号的边界子句必须点一个 §0.3 真实存在的 F 号，且其中至少一个是开放裁决（未清偿/部分清偿/部分处理/本轮只登记/本步只登记/待用户决策/维持现状/未处理）——一个都不点＝给凭印象的说法发通行证，只点已清偿的账＝把做完的事写成待办，点了账本里没有的号＝幻影引用，三者各有自己的失败消息。变异 M4/M5/M6 各自 RC=1 并逐条指名，读数见 §1 第 35 步。本步零改生产代码 |
| F-62 | P3（测量口径类，本行由第 35 步**自我撤回**后重写） | **一条只凭仓内 `.venv` 的推断，把并发会话在另一解释器上的真实读数改成了历史假账**：本步原登记"账本 12 处复测行写着 `Windows+py3.13`，而这台机器上从未存在过 3.13"，依据为 `.venv/pyvenv.cfg`（`cpython-3.12.13`）、`%APPDATA%\uv\python` 目录清单与本步覆盖率头 `python 3.12.13-final-0`，并据此把 `CHANGELOG.md` 8 处、本文 4 处标签批量改为 `py3.12`。撤回依据出现在让号之后的同机对照：同一棵 `f60c3b5` 基线，并发会话第 36 步记 **5 skipped / 80.82%**，仓内 `.venv` 这轮记 **7 skipped / 80.75%**（4 项缺 pyarrow、3 项缺 duckdb）——同一棵树、两个环境、两套数字都真，故"从未存在过 3.13"不成立，那 12 处是别人合法的实测环境。批量改写的后果不是排版问题而是**把他人的正确实测涂成假的**：与 F-59"证据指向不存在的文件"同族而方向相反（那里是编造证据，这里是凭局部证据否证别人的证据）。真正可登记的缺陷只剩一个：**复测行没有强制写明测量者当轮实际使用的解释器与所在环境**，同一机器上的多解释器因此让"环境标签"成为可互相误读的模糊信息 | **已修正**（2026-09-19，第 35 步自我撤回）：① 12 处 `Windows+py3.13` 全部恢复原文（回改脚本只在"该行其余文本与基线中含 `py3.13` 的那一行完全相同"时才动手，未改动任何数字、阈值或判据），本行即为撤回记录；② 立新规：自本步起 §1 与 `CHANGELOG.md` 的复测行**必须写明测量者当轮实际使用的解释器与所在环境**（仓内 `.venv` 或外部解释器），历史条目的环境标签由其作者自行维护，其它会话不得批量改写；③ **不给这条新规加门禁**，理由与 F-61 同：环境标签是每条记录各自的环境事实，硬钉判据只会造出随环境漂移变红的假门禁。本步自己的复测行按新规写 `本机 Windows+py3.12（仓内 .venv）` |
| F-63 | P2（口径类，F-51/F-60 同族：接线只修到一条命令，其余照旧丢弃；另一句告警替现场编数字，而它自己在那条路径上永不可达） | **①`DECODE_CAVEAT` 全仓只有一个发射点，而解码层为其它命令产出的判断仍在整族丢弃**：`tstdx/client/_mixin.py` 里有 15 处 `_client_pkg.dispatch(...)` 调用点（`:216`、`:313`、`:336`、`:346`、`:354`、`:369`、`:402`、`:463`、`:603`、`:641`、`:699`、`:706`、`:739`、`:748`、`:789`），其中只有 bars 分页那一处（`:216-223`）读 `result.warnings`——其余 14 处只取 `result.rows`（三种写法：`return result.rows`、`_emit(result.rows, …)`、`[… for row in result.rows]`，如 `:370`、`:463`、`:604`、`:700`），`ParseResult.warnings` 就地丢弃；发射侧的规模是 `guarded_count` 在 8 个解析器模块里的 43 个调用点（本步实测：脚本 `s36_probe_nonbars.py` 把 `2003` 空桩喂 `dispatch(0x044D, family="quotation")` → `rows=0`、`meta.declared_count=800` 与告警 `count 失真已钳制：声明 800 条，按剩余字节 29B/条 只能容纳 0 条`）。⇒ 第 26 步 F-51「把解码告警接进 wire」实际只覆盖了一条命令。**②`SECURITY_LIST_EMPTY_FIRST_PAGE` 的文案与 F-60 同法写死数字，而它的发射点在真实客户端路径上不可达**：`_mixin.py:435-437` 说「0x044D 在 start=0 就声明 0 条记录」，可这条命令在 `tstdx/protocol/commands.py:125-133` 登记为 `status=STATUS_OFFLINE`，`_guard_offline`（`tstdx/client/core.py:296-301`）在 `TdxClient._req` 里 fail-fast——本步实测 `TdxClient(pool=fake).security_list(0, 0)` 直接抛 `CommandOffline`，走不到解析器。仓库里唯一让这条告警变绿的 `tests/unit/test_batch_e.py:187-196` 是先把 `client.security_list` 整个 monkeypatch 掉再跑的 ⇒ **它的绿是一个假桩的绿**，而 `catalog/capability.py:101,146-149` 仍把 `security_list`/`security_list_all` 挂在 tdx 面，CLI（`cli/runtime_commands.py:155`）、HTTP（`integration/runtime_http.py:161`）、MCP（`integration/mcp/_tools_impl.py:98`）三个入口按调用链都会撞上那条 fail-fast。 | **已清偿（第 37 步①；第 39 步②按用户裁决「保留，只把『已下线』写清」落地：2026-09-19）**——②的裁决问题与理由原文保留：① 已在第 37 步收口。`_mixin.py:104-118` 新增**唯一**转发口 `_forward_decode_caveats(result, label)`（读 `result.warnings` → `record_warning(DECODE_CAVEAT, f"{label}：{caveat}", stacklevel=_caller_stacklevel())`，原样返回 `ParseResult`），上列 15 个分派点全部过它一次，bars 原来的 inline 循环删掉、文案逐字未动（第 36 步的空桩守卫照旧有效）。登记的判据「发射点数 ≥ 分派点数」没有采用——它会被「把同一处复制十五遍」满足；实际落地的是三条更强的门禁（`tests/architecture/test_caveat_channel_gates.py`）：含 `dispatch` 调用的函数必须调用该转发口（AST 扫描，`scanned ≥ 15` 防扫描自身失效）、每条转发文案必须以发起它的公共方法名开头（复制粘贴的归属错位即红）、转发口本体必须同时读 `.warnings`、调 `record_warning`、引用 `DECODE_CAVEAT`（空转即红）。归属行号按当前调用栈实测（`_caller_stacklevel()`，`:121-136`），不用写死常量：`_op_call` 会让一次公共 API 嵌套三跳 trampoline，实测常量形状把 `block_list` 的告警记在 `_mixin.py:169`。② 仍要先由用户裁决 `security_list` 面是「已下线，删掉 capability 与三个入口」还是「保留，但明确只走替代命令」——两条路都不该留下一句永不可发、且措辞替现场编造数字的告警。（F-61/F-62 由并发会话以第 35 步落在 `abef5e1`，本步不复述、不改它们的行。）**执行记录（第 39 步，2026-09-19）**：裁决取「保留」一侧，于是把「已下线」写进调用方读得到的每一面——`_mixin.py` 里 9 个注定失败的 trampoline 模板 docstring（含 `_op_call` 传递闭包判出的 `_t_export_security_list`，以及被 `_OFFLINE_FALLBACK_OK` 放行、口径恰好相反的 `_t_quotes_snapshot`）、`Client.security_list`/`minute`/`trades` 三个业务入口面（各点名它总是抛的那个异常）、`mcp/_tools_spec.py` 三条工具描述（账本 offline 写 offline、inferred 拦截写 unavailable，两种失败不混为一谈）、`docs/providers/tdx.md` 能力清单 5 行、`docs/api/interfaces.md` 13 行。判据不抄命令号也不抄能力名：分母由账本状态 ∪ 拦截集 ∪ 内核直绑表 ∪ 三层调用图现推，五张面同源（`tests/architecture/test_offline_capability_honesty.py`，8 项）。永不可达的 `SECURITY_LIST_EMPTY_FIRST_PAGE` 按裁决**留在原处**，改为把「到不了」写成判据：它的发射点必须仍然只有 `_t_export_security_list` 一个，搬家或新增即红（变异 M8）。 |
| F-64 | P2（口径类，F-50/F-52/F-54/F-55/F-57 同族：数据面登记了没人读的东西；本条另有一半——那个没人读的字段同时是全账本唯一的描述） | **命令账本 `Command` 登记 10 个字段，其中 4 个在 `tstdx/` 里没有任何读取点**（第 38 步在干净 `bb201b1` 上 AST 扫 189 个模块实测，脚本 `s38_probe.py`、日志 `s38_probe_base.log`）：`request_fields` 0 处、`aliases` 0 处、`spec_file` 1 处而那处属 `tstdx/tools/spec_audit.py:475` 的 `AuditResult.spec_file`（同名不同物）——三个是纯登记。第 4 个是 `summary`：85 条命令逐条写着中文描述（空 0 条），全包读它的却是 0 处（扫到的 3 处 `.summary` 属 `tstdx/domain/records.py` 的 `NewsRecord`/`ResearchRecord`/`SearchRecord`）。它与前三个不该同判：账本 85 条只有 39 条在 `PROTOCOL_SPEC/` 有 YAML 条目（该目录 44 个 yaml、42 个命令号），另外 **46 条**的语义说明只活在 `summary` 这一行，而客户端唯一把命令说给用户听的地方（`_guard_offline` 的两条 fail-fast）只报了名字。另有一条只剩「写」那半边的生成线：`tstdx/tools/codegen.py:generate_command_entry` 从 YAML 的 `request.fields` 推出 `request_fields=(...)` 写进 `_c(...)`，账本里因此躺着 13 处该实参而读侧无人消费 ⇒ 只删字段不删生成线，下一次真跑 `python -m tstdx.tools.codegen --write` 就是 TypeError | **已清偿**（2026-09-19，第 38 步）：① `Command` 由 10 字段收到 7（`cmd`/`name`/`family`/`tier`/`verified`/`status`/`summary`），13 处 `request_fields=(...)` 实参与生成器那 7 行推导同删；② `summary` 按「无理由孤儿一律接线或删除」取接线：`_guard_offline` 的两条文案由「0x07E5（BLOCK_QUOTES）」改为「0x07E5（BLOCK_QUOTES：板块行情（2026-09 三主站实测无响应，client 方法保留待参数校正））」，`CommandOffline` 与 `NotImplementedFeature` 的 `context` 各加 `"summary"` 键；③ 判据四条加一条分母自曝：形状锁（`fields(Command)` 逐字相等，删完再走后门登记回来当场红）、85 条 `summary` 普查、按账本自身分母（8 条被拦 offline + 2 条 inferred-block）逐个钉「`message` 里有这句话」与「`context` 里有这个键」两侧、codegen 生成的那一行喂回 `_c` 求值；④ 变异 6 发各自 rc=1（`s38b_mut.log`），其中 M1 第一轮以 RC=0 溜过，见 §1 第 38 步；⑤ 同族函数侧的孤儿登记为 F-65，本步不删公开查询面 |
| F-65 | P3（对外面口径，F-44/F-47 同族：删它就是收窄契约，不该由本步替用户定） | **账本的函数侧躺着同一批孤儿**（同一轮同一把尺子量出，孤立树 `bb201b1`）：`commands.stats()` 在 `tstdx/` 内零调用点（`tstdx/transport/sniff.py:150` 那个 `stats` 是另一个类的方法定义），唯一的读者是它自己的测试；`get_command_by_name()` 连测试都没有，全包零调用且未从 `tstdx/protocol/__init__.py` 再导出；`unknown_command_ids()` 同样零调用却挂在 `__all__` 上，`by_family()` 只被 `commands.py` 内部的 `by_status`/`unknown_command_ids` 调用；而 `docs/archive/OPTIMIZATION_PLAN.md:32` 还写着「`unknown_command_ids` 保留为别名」——被别名掉的那个 `unknown_commands()` 早已不在模块里，这正是 v16「兼容层删除而非别名」该处理而未处理的一件 | **已清偿**（2026-09-19，第 46 步按用户裁决 **(b)** 执行；(a)/(c) 未采纳）——三条路径原文保留：路径 (a) 四个全删，账本对外只留 `get_command`/`by_status`/`CMD`/`COMMANDS`；路径 (b) 保留 `by_family`/`unknown_command_ids` 为公开查询面，补文档与用例，删 `stats()`/`get_command_by_name()`；路径 (c) 只删两个纯孤儿（`stats()`/`get_command_by_name()`），公开面原样。本步只收口 `Command` 的字段面，未动这四个函数，也没动那句说错了的归档文档。**执行记录（第 46 步，读数见 §1 第 46 步）**：① `stats()`/`get_command_by_name()` 物理删除，模块 `__all__` 同步收窄，不留别名——HEAD `e47538d` 实测 `stats()` 全仓唯一读者是它自己的测试（`tests/unit/test_commands.py:104`），`get_command_by_name()` 除定义行与 `__all__` 行外零命中（也未从包面再导出）；② 裁决 (b) 保留的两个公开查询面补齐文档：`docs/api/interfaces.md` 新增「命令账本查询面」（5 行表 + 逐名口径），五个函数各写 docstring，`docs/api/README.md` 的账本行改为指向该节并写明"5 个查询函数"（该个数由门禁与名单对账）；③ **本行登记文本有三处失真，按实测改**：`by_family()` 的生产读取点只有 `unknown_command_ids` 一处（`by_status` 直接遍历 `COMMANDS`，登记那句把它也算成了调用方）、`unknown_command_ids` 返回的是 `list[Command]` **而非裸命令号**（名字极易误读，docstring 已写明取号方式）、`by_status` 的 docstring 与本行同源的文档句"客户端 fail-fast 就以它为依据"不成立（`_guard_offline` 走 `get_command` + 单行 `status` 字段，两面均已改写）；④ **裁决 (b) 未覆盖的第三个孤儿 `cmd()`**：`tstdx/` 内调用点实测 0、消费者只有测试——本步不擅自扩大删除射程，改为保留 + 补文档 + 补用例，并交新门禁"公开名必须有测试真的调用"持续把住；⑤ 模块面与包面 `__all__` 的**不对称如实保留**（`tstdx.protocol` 只再导出 `get_command`/`by_family`/`unknown_command_ids`，`cmd`/`by_status` 只在模块面）：新门禁只查"包面不得声明模块面否认的名"，不把两面强行拉平——收窄包面是另一次对外决定；⑥ 被删能力的等价归宿有证据：`get_command(cmd(name), family)` 与被删的按名线性扫描对 85 行**逐行等值**（一条用例实证），名字唯一性另有一条断言钉住（这正是 `cmd()` 不需要族上下文的前提）；⑦ 归档那句"`unknown_command_ids` 保留为别名"按"不抹史"处理：原文不动，就地加修订注记（该"别名"关系从未成立——初始提交 `f73ef61` 里两者已在同一模块；真实同名物是 `tstdx/transport/sniff.py` 的 `ProtocolSniffer.unknown_commands()`，语义是"探测期观察到、未登记命令号"，与账本无关）。 |
| F-66 | P2（对外契约类：机器可读的能力声称面，F-63② 的同格补集） | **第 39 步为 F-63② 推导「哪些面写着已下线」时实测出第六张面，而它是唯一机器可读的那张：能力发现面**（2026-09-19）。`GET /v13/capabilities`（`tstdx/integration/runtime_http.py:67`）返回 `{"capabilities": [...], "providers": {provider: {channel: [能力名…]}}}`——两份名单都只有名字，**没有任何状态字段**（探针 `step39/probe_f66.log` 直接打该路由：顶层 `capabilities` 172 项、`providers.tdx.quotation` 16 项，条目类型清一色是裸 `str`，形状上就没有放状态的地方）。实测其 tdx/quotation 平铺 16 个名字，其中 **8 个在客户端就发不出去**：`minute`(0x0537)/`trades`(0x0FC5) 属 inferred 拦截、`security_list`(0x044D)/`block_quotes`(0x07E5)/`minute_history`(0x0FB4) 属账本 offline、`security_list_all` 经 `catalog/capability.py:149` 绑到 `export_security_list`（下线由传递闭包判出），另两条 `auction`/`volume_price` 在本轮**当场量出** `CommandOffline`（同一探针还逐项 `Client().call(cap, '600519')`，两条各回 `CommandOffline: 0x056A` 与 `0x051A`，与账本 summary 同口径）。同一轮量出本步门禁自身的一条边界：第 39 步的推导以「方法名 = 能力名」为键，注册表这套 channel 词汇是**第三套命名**（`auction` vs `_t_auction_snapshot`、`volume_price` vs `_t_volume_price_dist`、`security_list_all` vs `export_security_list`），因此 `auction`/`volume_price` 落在本步五张面之外——它们既不在文档里被批注，也不被新门禁判出。后果与 F-63② 同形但受众不同：文档面骗的是读文档的人，这张面骗的是自动发现能力的调用方（`tests/runtime/test_migrated_surfaces_v13.py:127` 与 `tests/test_bridges.py:231` 已在消费它） | **已清偿**（2026-09-19，第 47 步按用户裁决 **(c)** 执行；(a)/(b) 两条未采纳）——三条路径原文保留：(a) 发现面加状态——每个能力附带由账本 ∪ 拦截集 ∪ 绑定闭包现推的 `available`/`offline`/`unverified`，`Client.capabilities()` 与 wire 同批改，属新增对外键；(b) 收窄名单——把发不出去的名字从声明里拿掉（发现面 fail-closed，对按名单枚举的调用方是破坏性变更）；(c) 不改面、只补判据——把第 39 步的门禁扩到注册表词汇（能力名 → 绑定 → 命令号），并在 `docs/api/*` 写明「发现面只声明名字、不声明可用性」。与 F-44 同批裁决（同格里曾挂着的 F-47 已由第 40 步清偿）：三者都动对外契约的形状，而本步的授权范围是「把已下线写清」而非「重设计发现面」；判据本身可复算（上面那两条 `CommandOffline` 与 16 个名字即现场），故先钉事实与路径，不代拍板。**执行记录（第 47 步，读数见 §1.5 第 47 步）**：① 三处出口（`Client.capabilities()` / `GET /v13/capabilities` / WS `runtime.capabilities`）的形状**零改动**，不加状态键、不收窄名单，改由一条判据把『不许长出状态字段』钉住——(c) 的『不改面』因此是可证的而不是承诺；② 推导补上注册表那一跳（能力名 → `catalog` 绑定 → 实现方法 → `_t_*` 模板 → 命令号），实测把发现面里『账本可达』的名字从 7 个扩到 **25 个**（7 内核直绑 + 18 个 tdx 客户端族绑定）、判死的名字从 3 个扩到 **8 个**（6 个名字踩在被账本判 `offline` 的 **5 条**命令上、2 个被 inferred 结构化拦截），旧那 7 个的判定一字未变，摘掉这一跳的变异（M10）实测让判据红；③ `docs/api/interfaces.md` 新增小节「能力发现面：只有名字，没有可用性」，含三处出口的形状表与一张八行死名字表（每行的命令号、实现方法、抛哪个异常、出路 Provider 全部由运行期现推后逐字对账，出路 Provider 必须是 `PROVIDERS` 真的声明过的那几家）；④ 门禁新增 4 项判据（本文件 8 → 12 项），文档里 7 处计数与 README 的 `11 Provider × 56 channel × 172 capability` 三元组全部现算回查，§3 的 HTTP/WS 两小节必须互指这一口径；⑤ 唯一解析盲区 `f10`（执行器按 capability 而非 `meta.method` 选方法）按名字登记，并要求 F10 族账本一条 offline/拦截命令都没有，否则该格判据自红。(a)/(b) 两条路径的代价这一轮也量出来了：加状态要覆盖 172 个名字而其中 147 个与 tdx 账本无关（须另找真值源），收窄名单则要动三处对外出口的形状。 |
| F-67 | P3（事实型文档里的死路径，Phase 4 文档统一的第一格） | **`docs/errors.md` §四「上层边界约定」把两个早已不存在的模块写成今天的边界**（Phase 5 第 40 步顺带实测，2026-09-19）。该节三条 bullet 里有两条点名死路径：门面层 `facade/api.py`（`tstdx/facade/` 目录 `exists=False`）与「服务面（`integration/http_server.py`）」（`exists=False`，今天的真身是 `tstdx/integration/runtime_http.py`）。第一条还承诺了一整套不存在的形状——「`query()`/`aquery()` 把任何异常转 `ApiResponse{success=False, error, code}`」与「路由链失败时最后一路由异常的 context 里 `route_errors` 聚合各路由失败摘要（W11）」：实测 `hasattr(tstdx, "ApiResponse")` 为 False，`route_errors` 与 `W11` 在 `tstdx/` 内 grep **零命中**。**事实型文档门禁为什么看不见它**：`tests/architecture/test_doc_code_consistency.py` 的解析正则只认反引号里的**点号**模块路径（形如 `tstdx.a.b.C` 那种一段一个标识符的形状），而这三处写的都是**斜杠**形式的文件名——形状上就不进判据。同轮把 7 份活文档全扫了一遍：含死路径的只有 `docs/errors.md` 一份（README 与 `docs/ARCHITECTURE.md` 的 `facade` 命中是 `tstdx/web/facade.py` → `session.py` 的重命名史，写的就是删除本身，属实）。全仓范围另有 `facade/api.py` 24 个 markdown、`integration/http_server.py` 9 个，多数在 `docs/archive/`（刻意的历史语境，不参与事实检查）。取证两份都留档：`step40/probe_f67.log` 与 `step40/probe_f67_precise.log`——**前一份是错的取证**，它按文件名尾串匹配，把 `tstdx/client/api.py` 也算成命中，虚报了 README 与 ARCHITECTURE 两份活文档；改成整串匹配才对上事实。两处都记在这里，因为「探针多报」与「判据漏报」是同一族缺陷的两面 | **已清偿（2026-09-19，第 42 步执行 (a)）**：登记时的三条路径——(a) 把 §四 改写为今天的边界事实（唯一业务入口 `Client` 抛 `TdxError` 家族、`error_envelope` 在对外层收敛为 fail-closed 信封、HTTP 面 `runtime_http.py` 用 `http_status_for()` 映射状态码），并把门禁正则扩成同时认斜杠形式的活路径；(b) 只改文字、不动门禁（同类形状以后仍会漏）；(c) 只扩门禁、把 §四 改成指向 `docs/api/interfaces.md` §3 的一句话。**为什么不顺手改掉**：本步的授权范围是 F-47 的三面入参契约，改一份对外文档的边界章节属 Phase 4「文档统一」的题；且 (a) 的扩门禁会不会牵出别的活文档死路径需要单独一轮取证与复测，不该混进一次入参契约的提交。**第 42 步按 (a) 落地**：§四 整节按实测重写（Client 模块零 `except`、越过信任边界的唯一形状是 `ErrorEnvelope`、HTTP/WS/MCP/CLI 四面各自真实的落点与退出码），并把事实文档门禁扩出**斜杠形式**的死路径判据；扩判据前按登记时的要求先做单独一轮全量取证，除 §四 之外另抓出一处假事实——`docs/ARCHITECTURE.md` §3 把已随 `fcf8e92` 删除的 `tstdx/security/` 写成「活」，一并改掉。(b)「只改文字不动门禁」与 (c)「只扩门禁把 §四 压成一句话」都不再需要。执行记录见 §1.5 条目 42
| F-68 | P2（对外契约类：错误树上的占位叶子与文档独有的幻影名，F-44 的剩余面 / F-43 同族） | **F-44 的 (a) 接线在 `SourceUnavailable` 这一面无落点，而同一次扫描量出四处"文档写着、代码里没有"的名字**（Phase 5 第 41 步实测，2026-09-19）。① 站点普查（`step41/probe_sites.log`，AST 走查全仓 191 模块，分 raise / `raise <变量>`回溯 / `on_error(...)` 投递三类）：错误树 46 类中 5 类既无抛点也无投递——`SourceUnavailable` E7050、`ChecksumMismatch` E3050、`UnknownCommand` E3030、`BackpressureOverflow` E6030、`CompatibilityWarning`（`UserWarning`，不在 `TdxError` 树下）；抽象基类 `TransportError`/`StreamError`/`ProfileError` 自身也不抛但子类全部有站点，属正常分类节点。② 文档点名普查（`step41/probe_promises.log`：82 份 md 里 46 份是对外文档，排除台账/变更日志/归档）：对外文档以反引号点名 45 个错误类，37 个已接线，上面这 5 个未接线的**全部**被对外文档承诺过——`SourceUnavailable` 被 4 份文档点名（`docs/providers/README.md` §12 规范语义、`docs/tdx_status.md` 六处 P13-A、`docs/providers/tdx.md` §7、`docs/adr/ADR-013-provider-source-terminology.md` 称其为"稳定公共资产"），`BackpressureOverflow` 被 `docs/cookbook/04_streaming.md` 与 ADR-006-010 承诺，而 `BackpressureQueue.put`的既定语义是丢最旧元素并计数、从不抛。③ 同一轮量出四处**只存在于文档**的名字：`docs/providers/tdx.md` §7 的 `CapabilityUnsupported` 与 `DataIntegrityError`（`hasattr(tstdx.errors, …)` 双双 False，`tstdx/errors.py` 里根本没有这两个类）、`docs/providers/README.md` §11 的 freshness 模式名 `historical_closed`/`current_series`（全仓 grep 零命中，运行期只有 `CurrentnessMode` 四值）、`docs/errors.md` §二 把 `RetryAdvice.fallback_to_offline`/`fallback_to_web` 的消费方写成"sources 路由"（该路由已随单内核删除，实测全仓对这两个字段的唯一读点是 `tstdx/errors.py:154` 自己的序列化字典，而 `errors.py:20` 早已写明"新内核不消费"——与 F-43 幻影开关同族）；同一份 `docs/errors.md` 头部还抄着"44 个类"，当前是 46 个类（`errors.py` 顶层 AST 计数；`tstdx.errors.__all__` 的 49 个名字里含 `RETRY_ADVICE`/`advice_for`/`http_status_for` 三个非类名，v8 那个 44 与之并非同一口径）。④ 判据自身的第一版是错的：`step41/probe_orphan.log` 只认字面 `raise X(`，把 `last_exc = AntiSpiderBlocked(...)` + `raise last_exc` 这条已接线的重试环误报成幽灵（多报 3 类）；补上同作用域赋值回溯后才对上事实——与第 40 步 F-67 的"探针多报/判据漏报同一族"同形，两份读数都留档 | **本轮只登记（2026-09-19，第 41 步），待用户裁决**：本步已做的是**把不兑现的承诺改成实话**（§12 改为"占位、不承诺 `except` 得到"、tdx.md §7 换成真实类名、README §11 换成 `currentness` 四值口径、errors.md §二 两行改为"无消费方"并新增"一之二、树里存在但运行期永不发生的类"表）+ 把事实钉进本文 + 上新门禁（文档点名 ⇒ 必须有站点，豁免表自洁）。剩下没做的是这 5 个叶子本身，三条路径：(a) 全删——5 个类连同 `errors.py` 的 E 段声明与 `__all__` 一起删，文档同步（与 F-40/F-41 的删除口径一致，clean break；代价是推翻 ADR-013 里"`SourceUnavailable` 是稳定公共资产"那条，以及 `docs/api/*` 的码表）；(b) 全留为登记占位——保持现状：代码树不动、文档已改成"不承诺"、门禁把守不许新增未接线的承诺（本步交付即此路径的形态）；(c) 折中——删掉 4 个纯内部叶子（`ChecksumMismatch`/`UnknownCommand`/`BackpressureOverflow`/`CompatibilityWarning`），保留 E7050 `SourceUnavailable` 作跨 Provider 术语资产（ADR-013 的 `ProviderUnavailable = SourceUnavailable` 映射仍可读），并把它的接线挂到"若 auto 路由某天回来"**为什么不自行拍板**：与 F-44/F-47/F-66 同格，动的是对外可见的错误面；且 (a) 与 (c) 的差异恰好落在 ADR-013 承诺过的稳定资产上——删除一个从未抛过的类在运行期是零影响，在文档与用户已写的 `except` 分支上不是。判据本身可复算（两份探针日志 + 门禁的豁免表就是清单）。**第 44 步按 (a) 落地**：4 个零站点叶子（`UnknownCommand` E3030 / `ChecksumMismatch` E3050 / `SourceUnavailable` E7050 / `CompatibilityWarning`）连同 `__all__` 条目物理删除；第 5 个 `BackpressureOverflow` E6030 在补了投递站点判据后**实测有真实站点**（`tstdx/streaming/base.py` 的 `sub.on_error(BackpressureOverflow(...))`），不但没删还新增一条行为测试证明溢出确实投递到 `on_error`——(a) 的"全删 5 个"前提在这一格不成立，属执行期偏离裁决字面，已按裁决意图（只删从不兑现的叶子）处理并留证。删除后树为 40 个 `TdxError` 子类 / 40 个唯一 code / E1–E9 九域；ADR-013 §11 那句「稳定公共资产」按历史保留并加作废修订。同一轮把门禁从单向扩成**双向**（文档点名 ⇒ 必须有站点；文档点名的名字 ⇒ 必须存在于树里），并新增 `docs/errors.md` §一之二 退役登记表。逐条取证见 §1.5 第 44 步 |
| F-69 | P1（对外口径类，F-13/F-16/F-20 同族：一个「照本库自己的指引做就会自毁」的开关） | **`TSTDX_` 是 strict 环境扫描的保留命名空间，而发行代码里有一个读它却没登记的名字**（Phase 4 第 43 步实测，2026-09-19）。第 43 步给 `docs/configuration.md` 的键/默认值/取值范围/错误消息/环境变量五类抄本上双向对账门禁时，把环境变量的判据从「文档 §4 那张表 ⇄ loader 登记表」的一致性，扩成**发行代码里出现的每一个 `TSTDX_*` 名字都必须有归属**（登记进 `loader._RUNTIME_ENV_KEYS`，或本身是 schema 形 `TSTDX_<SECTION>_<KEY>`）。当场量出 `TSTDX_WENCAI_COOKIE`：`tstdx/web/wencai.py:77` 读它取值，同文件 `:122` 的错误消息直接叫用户「设置 `TSTDX_WENCAI_COOKIE`」，`docs/providers/` 与 `tstdx/web/sources.py` 也把它写成正式注入途径——可它既不在 §4 的 runtime 变量表里，也不在 loader 的登记表里。后果不是「这个变量无效」，而是**用户照本库指引做完之后 `Client()` 直接抛 `ConfigError [E1000] 无法识别环境变量 TSTDX_WENCAI_COOKIE`**：strict 扫描把它判成拼写错误并让整条配置加载 fail closed。基线复现（干净 `7a3bae7` 树、同一解释器、`TSTDX_WENCAI_COOKIE=v=sometoken` 下构造 `Client()`）实测抛错，修复后同一命令构造成功。同轮另量出一条同形缺陷：`tests/unit/test_golden.py` 的合成样本开关写作 `TSTDX_GOLDEN_SYNTHETIC`——测试夹具占用了运行时保留命名空间，任何设了它的会话会让 5 个配置贯通测试红。**为什么既有门禁看不见**：§4 表与 loader 登记表此前互为真相源的两份抄本，同一个名字两边都漏时它们完全自洽（一致的错）；而 `wencai.py` 里那行读取、以及它自己的错误消息，都不在任何对账射程内 | **已清偿（2026-09-19，第 43 步取路径 (a)）**：三条候选——(a) 登记该变量 + 把测试开关改出保留前缀 + 上「代码读取 ⇒ 必须有归属」的派生判据；(b) 把问财 cookie 改名成非 `TSTDX_` 前缀（推翻已写进多份文档与错误消息的对外口径，代价大于收益）；(c) 让 strict 扫描放过未知变量（与 F-13/F-16 的「不静默」口径正面冲突：拼错的键将悄悄失效）。本步按 (a) 落地：`TSTDX_WENCAI_COOKIE` 入 `_RUNTIME_ENV_KEYS`（附 WHY 注释，并在 loader 模块 docstring 写明该前缀是保留命名空间这条不变量），`TSTDX_GOLDEN_SYNTHETIC` → `GOLDEN_INCLUDE_SYNTHETIC`（全仓 grep 证明旧名除 `test_golden.py` 那两行外零引用：无 CI/Makefile/文档依赖 ⇒ 契约零变化），§4 表补一行并写明「该前缀为保留命名空间、测试与工具不得占用」，防回潮判据落在 `tests/architecture/test_config_doc_contract.py` 的 `test_every_env_var_the_library_reads_is_registered_or_schema`（扫描 `tstdx/` + `scripts/` 全部 .py 的 `TSTDX_*` 字面量，`tests/` 刻意排除——那里的 `TSTDX_CORE_TIMOUT` 一类是负例夹具）。变异验证：删掉登记项 ⇒ 4 条判据同时红（含「设置它就能用」那条真实回归），塞进一个未登记的读取点 ⇒ 派生判据点名报红 |
| F-70 | P3（对外口径类，F-67 的补集：死路径判据只认「路径」，不认裸类名） | **四份对标/审计文档把已删除的 `UnifiedQuoteAPI` 门面写成今天的对外接口，而两条既有判据都看不见它**（第 44 步取证，2026-09-19）。① 事实：`hasattr(tstdx, "UnifiedQuoteAPI")` 为 False （该类随 v16 Phase 2 门面删除，`docs/ARCHITECTURE.md:88-91` 自己写明了改名动因）。② 现在时点名的活文档 4 份：`docs/efinance_parity_gap_analysis.md:8,26,155`（「新增门面方法：`UnifiedQuoteAPI` 上新增 21 个」「`tstdx/facade/api.py` —— 新增 21 个 `UnifiedQuoteAPI` 方法」「全部存在且可实例化」）、`docs/niuniu_coverage_audit.md:4,81,116,128,154,157`（把本库自称「统一门面 `UnifiedQuoteAPI`」，并以「用 tstdx 的 `UnifiedQuoteAPI` 替换 niuniu 9 个适配器」作为写给下游的**待办目标**）、`docs/astock_toolkit_parity.md:24`「本次新增接口（5 个 `UnifiedQuoteAPI` 方法）」、`docs/tiantian_fund_extensions.md:13,56,158`（「18 个 `UnifiedQuoteAPI` 门面方法」「门面接线校验：`UnifiedQuoteAPI` 与 `WebQuoteSession` 均具备全部 18 个方法」——最后一句是校验指令，照做即 `AttributeError`）。③ 同族第二格在 `docs/tdx_status.md` §五 末段：它把 `route="web"/"local"` 写成今天的入参契约并称「仍报 `ValueError`」，实测 `Client.quotes(..., route="web")` 是构造期 `TypeError`（`Client.quotes` 形参只有 `symbols`/`provider`/`policy`/`currentness`），**本步已就地改写**为 pre-v17 → v17 对照并补上 `provider` 写错名的真实下场（E1010 + `known_providers`）。**为什么两条判据都看不见**：死路径判据（第 42 步）只解析反引号里的**路径**形状（点号 `tstdx.a.b.C` 或斜杠 `facade/api.py`），裸类名两者都不是；幻影名判据（第 44 步）只在**错误小节**（标题含 错误/异常/症状/排查/失败/Error/Exception）里点名，而这 4 份文档的命中在「结论速览」「文件清单」「校验步骤」。合起来是一条明确缺口：**活文档里的非错误类裸名没有任何判据** | **本轮只登记（2026-09-19，第 44 步），待用户裁决**：三条路径——(a) 把这 4 份按日期成稿的对标/审计报告整体移入 `docs/archive/`（它们记录的是 2026-09-09 那一轮的对标结论，本就属历史语境，而 `docs/archive/` 在全部事实判据的豁免名单里，移动即自洽）；(b) 逐份改写成 v17 口径（门面名换成 `Client` 方法名、把「校验指令」删掉），代价是重写 4 份文档的结论表；(c) 加第三条判据——把幻影名门禁的扫描面从「错误小节」扩到全部小节，允许集用全仓 `ClassDef` 名单。**为什么不自行拍板**：(a) 改变文档的可达路径，(c) 会把散文里的第三方名（`niuniu`、`TiantianFundApi`）与已豁免的 endpoint 名全部拉进射程，误报面需单独一轮取证；本步授权范围是 F-68 的错误面 |
| F-71 | P2（对外契约类：一条被门禁保活的第二数据入口，F-29/F-56 旁路口径的最后一格） | **`tstdx.web` 仍然是一条公开、被文档指向、并且由架构判据**真实求值**保活的数据入口，而 §0.1 与 README 写着「`Client` 唯一业务入口」**（第 48 步取证，2026-09-19）。① 根包 docstring 第三段 Quick start（`tstdx/__init__.py:60-63`）就是 `from tstdx.web import get_quotes; get_quotes([...], source="tencent")`，而 F-31 的判据 `test_dunder_docstring_quickstart_examples_construct` 会在禁网下把这行的构造真实求值——**这条入口不是散文，是被门禁钉住的公开形状**。② 它不在顶层公开面里：`tstdx.__all__`、惰性表 `_LAZY`、`hasattr(tstdx, "WebQuoteClient")` 三处皆无（`tests/runtime/test_root_public_surface.py:30-45` 反向钉住），只能经 `from tstdx.web import …` 抵达；`tests/architecture/test_official_runtime_no_fallback.py` 的 `OFFICIAL_RUNTIME` 判据禁止内核 import 它——这三件事合起来把它的定位写成"链外但公开可用"。③ `WebQuoteClient.quotes()`（`tstdx/web/__init__.py:413-443`）是**隐式有序换源**：按 `web.enabled_sources`（缺省回落 `DEFAULT_FALLBACK_ORDER`）逐源 `try`，任何异常（连 `AttributeError`/`KeyError` 这类非 `TdxError` 也算，见 `:426-435`）记进 `self.errors` 后继续下一家，全挂才抛 `AllSourcesExhausted`；`klines()` 同形（`:445-461`）。④ 同一个模块 docstring 自相矛盾：`:4-7` 写「内核不做自动换源：这里列出的源只有在请求显式指向它时才会被访问；跨源容错必须由调用方给出 `FallbackPolicy`」，`:15-19` 立刻把这条有序换源摆进 Quick start，只补一句"顺序由调用方配置，属调用侧策略"——配置一次生效与每次请求显式给策略是两种不同的显式性。包 docstring 判据只扫 `tstdx/__init__.py` 一层，`tstdx/web/__init__.py` 这段话目前**没有任何判据在看**。⑤ 对外文档把它写成选型建议：`docs/migration/easyquotation.md:58`「两者是独立入口，按需选用」、`:67`「多源互备用 `WebQuoteClient`，内核跨源用显式 `FallbackPolicy`」、`docs/migration/README.md:13` 的迁移映射、`docs/configuration.md:92-96` 的 `[web]` 段（自称 legacy 缺省源顺序）、`docs/troubleshooting.md:48`（自称 legacy Web 入口）。⑥ **本步之后，内核到 web 的取数跳全部是单源构造点**：`tstdx/runtime/executor.py:604`、`:613` 的 `create_source`，`:635-675` 三家历史 K 线源类（`SinaHistoryKlineSource`/`EastmoneyHistoryKlineSource`/`BaiduSource`）的直接构造，以及迁移能力跳 `tstdx/runtime/executor.py:273-276` 的 `WebQuoteSession(源名)`——会话的 `_c` 只 `create_source(self.source_name)`（`tstdx/web/session.py:124-129`），`session.py` 全文与 11 个 `_session_*.py` mixin 对 `WebQuoteClient`/`DEFAULT_FALLBACK_ORDER`/`enabled_sources` **零引用**。有序降级的构造点在发行代码里只剩两处，且都在 `tstdx/web/__init__.py` 内部：`:498`（`get_quotes` 的缺省分支）与 `:506`（`get_kline`，它写死 `sources=[KLINE]`，是单源而非换源）。同一轮顺带量出一条**假事实**：`tstdx/web/session.py:168` 自称会话「底层复用 :mod:`tstdx.web` 的零依赖适配器与降级逻辑」，而按上面这张构造点普查，会话按构造就是单源、永不触降级链——这句话与本格的 ③ 所描述的降级形状**只对得上 `WebQuoteClient`，对不上会话**，(b)/(c) 任一路径都要一并改写它，而它目前同样没有任何判据在看。**本步已做（不改对外面）**：内核的 web 一跳不再借道这条公开便利函数——`_web_quotes` 现在与兄弟跳同形地直接 `create_source(plan.provider, timeout=…)`（语义与旧路径的指定源分支等价：`get_quotes` 那一支就是 `create_source` + `fetch` + `finally close` 三行（`tstdx/web/__init__.py:492-497`），省略的 `headers`/`cookie` 在 `create_source` 里的缺省正是它当时传进去的 `None`，而 `max_retries`/`rate_limit` 两条路径都从未转发；`plan.provider` 经 `resolve_provider` 恒为已注册非空名（`tstdx/query.py:314-317`），旧代码的 `if source:` 分支因此恒被走到），并新增行为判据 `test_kernels_web_hop_uses_exactly_the_source_the_plan_named`（Provider 名单由 `DIRECT_BINDINGS` 现推，覆盖 4 家：`baidu`/`eastmoney`/`sina`/`tencent`）。取证见 §1.5 第 48 步 | **本轮只登记（2026-09-19，第 48 步），待用户裁决**：三条路径——(a) **收口为唯一入口**：删除 `WebQuoteClient` 与 `get_quotes`/`get_kline`/`get_rates` 的公开入口语义（`create_source`、`sources` 注册表与 `WebQuoteSession` 作为 Provider 适配器留下），根包 Quick start 第二段换成 `Client(default_provider="tencent")`，`docs/migration/` 两份文档与 `[web].enabled_sources` 配置段随之失去读者（与 F-16/F-20 的"配置面即执行面契约"一起处置）；代价是对已发布 API 的破坏性变更，且 web 侧两处 `AllSourcesExhausted` 抛点（`tstdx/web/__init__.py:437` 与 `tstdx/web/__init__.py:458`）消失——实测内核 `tstdx/runtime/orchestration.py:110`（显式 `FallbackPolicy` 用尽）仍是该类的真实抛点，故 (a) 不会把它变成 F-44 族幻影叶子。(b) **承认双入口**：把"唯一业务入口"改写成带例外的口径（`Client` 唯一**内核**入口，`tstdx.web` 是明示的 kernel-free 便利面），并把包 docstring 判据从根包扩到 `tstdx/web/__init__.py`，让 ④ 那段"不做自动换源"要么改口径要么当场红；代价是 v16/v17 那条决策点在文档里要开一条书面例外。(c) **留入口、去掉隐式换源**：`WebQuoteClient` 保留但缺省单源，多源必须由调用方在请求里点名（不再读 `web.enabled_sources`、不再回落 `DEFAULT_FALLBACK_ORDER`），Quick start 第二段与 ④ 的口径当场自洽；代价是 `[web]` 段两个键的语义变化，F-20（同一段的探活/源顺序默认值）要一并复测。**为什么不自行拍板**：本步授权是内核那一跳的单源不变量（纯内部形状，对外零变化），而 (a)/(b)/(c) 动的都是对外可见的入口清单与选型建议——与 F-65/F-66/F-68 同格，先登记事实与代价再裁决。判据可复算：上面每格的行号、`__all__` 名单与那条邻域失明变异（M1）都是当场读数 |
| F-72 | P0（对外结果语义类：一条被 3598 项离线判据整体照不到的静默失败，F-45 strict 通道的空转格） | **`quotes` 把「全部主机失败」写成「成功且没有数据」**（第 49 步复评轮实测，2026-09-20）。① 事实：把 `socket.connect` 与 `getaddrinfo` 全部钉死后调 `Client.quotes`（`symbols` 单只、`provider` 写 tdx），发生 16 次连接失败，函数**照常返回** `data=[]`、`ResultMeta.warnings=()`、`degraded=None`；同一条链上的 `bars`/`snapshot`/`security_count` 在同样禁网下抛 `AllHostsUnreachable [E2040]`，`security_list` 抛 `CommandOffline`——七格里只有 `quotes` 这一格把传输层整体失败吃下去。② 落点：`tstdx/client/_mixin.py:326-364` 的 `_t_quotes` 对每只符号单独捕获 `TdxError` 装进 `errors`，调用方没传 `_collect` 时收尾走 `self._set_last_errors(errors)` 后 `return _emit(out, as_format)`；而内核 `_tdx_quotes`（`tstdx/runtime/executor.py:503-505`）正是那个不传 `_collect` 的调用方，失败细节留在 `client.last_errors` 这个实例侧信道里，**内核与 `QueryResult` 都不读它**。③ 旁证：CLI 早就知道要绕过它——`tstdx/cli/runtime_commands.py:847-856` 在打印「0 条」之前专门 `getattr(c, last_errors)` 才拿得到原因；同一个内核，库面与 CLI 面对失败原因的可见性不一致。④ 后果射程：HTTP `/v13/quotes`、WS `quotes`、MCP `get_quotes` 三个服务面在整体断网时都是 200 加空结果，与「该代码不存在」完全同形，`Client.bars` 那套 `strict` 判据（F-45）在这里无物可严。⑤ 为什么全绿：离线全量用假客户端，不存在「所有主机同时失败」的场景；F-52/F-65/F-66 一族判据都是「声明面 ↔ 执行面」对账，不看失败投递方向 | **本轮只登记（2026-09-20，第 49 步复评轮），待用户决策**：三条路径——(a) 全部符号失败即抛 `AllHostsUnreachable`，与其余六格同语义；(b) 逐只失败写进 `ResultMeta.warnings`，于是 `strict=True` 立刻可拒收空成功；(c) 维持现状、只在文档写明 `quotes` 是「整体失败也返回空」的容错面。默认建议 (a)+(b) 组合。取证：`Temp/review50/probe_quotes.log` 与 `probe_core7.log`。方案与决策点见 `docs/REFACTOR_PLAN_V18_REVIEW.md` §4.2 与 §5 D1 |
| F-73 | P1（活文档假指令，F-67 死路径判据的小写点号盲区） | **活文档让人去调一个已经不存在的对象上的方法**（第 49 步复评轮，2026-09-20）：`docs/troubleshooting.md:97` 写「查看各源最近失败原因：`client.router.last_errors()`」，而全仓 `tstdx/` 里 `.router` 属性**零命中**（router 三件套随 Phase 3B 物理删除，见 §0.2 的 F-2 行）。为什么两条判据都看不见：死路径判据只认反引号里的 `.py` 路径形状，幻影名判据只扫 CamelCase 类名，而 `obj.attr.method()` 这种小写点号调用两者都不是——与 F-70 同族，换了一格形状 | **本轮只登记（2026-09-20，第 49 步复评轮），待用户决策**：(a) 改写成今天真实存在的观察点，并把死路径判据扩到反引号内的小写点号形状；(b) 只改这一行、判据不动；(c) 删掉这一行。默认 (a)——不扩判据则同类指令还会再长出来。见 `docs/REFACTOR_PLAN_V18_REVIEW.md` §5 D2 |
| F-74 | P1（链外遗留资产，F-18/F-65/F-68 同族的第四格） | **`tstdx/deprecation.py` 206 行退役机制在包内零消费者**（第 49 步复评轮实测，2026-09-20）：全仓 `tstdx/` 里对该模块的 import、装饰器与两个类名的命中数为 **0**（只有该文件自己），它也不在 `tstdx.__all__` 的 45 个名字里，唯一的引用者是 `tests/test_deprecation.py` 与两处架构测试。它服务的门面层已在 v16 Phase 2 删除（F-70 行），因此这是一座为已经不存在的桥留下的支架 | **本轮只登记（2026-09-20，第 49 步复评轮），待用户决策**：(a) 物理删除加防回潮 unimportable 守卫（与 F-18/F-65/F-68 同法）；(b) 接进某个真实退役流程；(c) 移入 `tstdx/tools/` 当工程脚本。默认 (a)。见 `docs/REFACTOR_PLAN_V18_REVIEW.md` §5 D3 |
| F-75 | P2（能力声称的最后一格形状问题，F-37 (c) 与 F-66 (c) 的续集） | **三个恒定抛错的能力仍占着对外端点形状**（第 49 步复评轮，2026-09-20）：`minute`（`0x0537`）与 `trades`（`0x0FC5`）在 tdx 上抛 `NotImplementedFeature`、`security_list`（`0x044D`）抛 `CommandOffline`，禁网实测七格里这三格永远不给数（`minute`/`trades` 另有 Web Provider 迁移绑定，`security_list` 全仓只有 tdx 一条绑定），但 HTTP 10 路由、WS 10 方法、MCP 9 工具里这三格照样是可调入口。文档口径已写明（`docs/api/interfaces.md` 的 37/38/41/172-176 行），缺的是**响应侧分类**：调用方现在要靠读文档预知，而不是从返回里区分「本仓结构化拦截」与「Provider 故障」 | **本轮只登记（2026-09-20，第 49 步复评轮），待用户决策**：(a) 三面保留、响应里显式分类；(b) 从三面摘除这 3 格（破坏性变更，与 F-66 (c) 已拍的「不改面」相反）；(c) 维持现状。默认 (a)。见 `docs/REFACTOR_PLAN_V18_REVIEW.md` §3 与 §5 D4 |
| F-76 | P2（可发现性与命名空间收敛的取舍，属登记不属缺陷） | **167 个目录迁移能力靠 `__getattr__` 动态出现**（第 49 步复评轮实测，2026-09-20）：`Client` 类体公开名 15 个，`dir()` 在实例上也只见这 15 个加 3 个属性（`runtime`、`orchestrator`、`stream_planner`），`fund_rank` 这类名字只有真去 `getattr` 才存在（`probe_surface.log`）；参数经 `Client.call` 收进 `options` 的 args/kwargs JSON 袋，类型检查器与 IDE 补全都看不见 | **本轮只登记（2026-09-20，第 49 步复评轮），维持现状待用户确认**：(a) 不动，「发现面只给名字」的口径由第 47 步判据继续管；(b) 为 167 个能力生成显式函数桩，把 `Client` 从 15 个名字撑到 180 个以上；(c) 收窄动态面。默认 (a)：(b) 与 v17「根级与入口命名空间收敛」的方向直接相反。见 `docs/REFACTOR_PLAN_V18_REVIEW.md` §5 D5 |

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
| 根级白名单 ≤10 | ~~偏差 2（曾落地 11 项）~~ **已收口**（2026-09-18）：`client_api.py` 移入 `client/api.py`，白名单回到原案 10 项，公开面 `from tstdx import Client` 不变。**2026-09-19 第 26 步再超限：10 → 11**（新增 `tstdx/diagnostics.py`）。理由：告警发射口必须同时被 `domain/`、`web/`、`client/`、`runtime/` 四层引用，放进任何一个子包都会让低层反向依赖上层（`domain/calendar.py → runtime/…`），而根级正是既有的跨层原语位（`errors.py`/`result.py`/`query.py`）。**上限本身没有门禁把守**（`ROOT_WHITELIST` 是枚举集合，不是数值），故这条超限按偏差如实登记，不静默改口径 |

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
   第 42 步把这条判据扩成同时认**斜杠**形式（`facade/api.py`、`security/` 那种文件名形状）的死路径，
   豁免只剩「同一逻辑块内写明删除史」与「代码运行期自建目录」两条，F-67 的执行记录见 §1.5 条目 42。
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
   ✅ **冒烟本体已于第 16 步执行**（2026-09-19，周六）：七格真实网络/服务面 6 PASS / 1 FAIL、
   wheel 安装冒烟 `SMOKE_RC=0`，逐格判据见 §1 第 16 步、§4 该格已勾 `[x]`。
   ⚠️ 本项仍未执行的只剩两件：**tag `v1.1.0-dev.1`**（远端可见，须用户明确确认后启动）与
   **一次工作日盘中复跑**（第 16 步落在休市日，`stream` 格与实时读数不能算被它证明过）。
   把"再跑一次须授权"（现行约束）读成"从未跑过"（历史事实）是第 35 步清偿的 F-61。
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
     清单重写为 17 条（第 45 步撤销 `providers.http` 那条豁免后实测 16 条，见本表 F-18 行），逐条给出可核验证据（文档路径 + 具体测试文件 + 为何生产链路不
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

22. ✅ **`options` 袋不再是 `max_age` 的备用入口：`allow_partial` 退场，袋级门禁补上判据的补集
    （2026-09-19，见 §0.3 F-46）**：第 20 步的门禁以 `dataclasses.fields(QuerySpec)` 为分母，
    而 v13 恰好把 `allow_partial` / `allow_stale` 从一等字段**降级成袋里的字符串键**——判据
    的补集里住着同一个缺陷。本步把它清掉，并把门禁从"字段"扩到"键"。

    - **删除前先确认它是幻影**：全仓对 `allow_partial` 只有"仅支持 quotes"一条校验，
      `tstdx/runtime/`、`tstdx/client/`、`tstdx/integration/` 零读取；`BatchResult.partial`
      在 `tstdx/batch.py:52-79` 必须与 `errors` 严格一致，即 partial 是**结果事实**，
      不是可放行的策略；`Client.quotes_batch()` 也不接受该参数。故"在 quotes 上设它 = 什么
      也不改变"，与 `max_age` 同族，按 clean-break 口径直接物理删除（形参 + 属性 + 校验）。
    - **袋里的键从此二选一**：新增 `tstdx.query.REJECTED_OPTIONS`（`allow_stale` /
      `allow_partial`，各带一句为何无对象可作用），`normalized()` 逐键当场 `ValidationError`；
      被测 `test_every_rejected_option_key_fails_loudly_instead_of_being_ignored` 以
      `sorted(REJECTED_OPTIONS)` 为分母参数化，新增被拒键不写理由即无覆盖、写了理由就必须被抛。
    - **门禁覆盖两处写入点**：`build()` 内部的 ergonomic 折叠，和生产代码里
      `QuerySpec.build(..., options={...})` 的字面量注入（`client/api.py` 的泛化调用面走的就是
      这条路，其 `args`/`kwargs` 与 kernel 的 `market` 都能被执行面读到，故当前差集为空）；
      判据是"写入点键集 − `REJECTED_OPTIONS` − 执行面实际读取键集 = ∅"，外加"每个 `allow_*`
      形参必须落进袋里"。两条扫描各带零命中自曝断言。
    - **首轮先红在门禁自己身上（第二次同形事故）**：`folded | injected - set(REJECTED) -
      executed` 因 `|` 优先级低于 `-` 而让 `build()` 折叠的键绕开拒绝集，`allow_stale` 被误报
      为幻影开关——加括号后 GREEN。第 20 步是 `dict.fromkeys` 共享集合让字段门禁失明，本步是
      优先级让袋级门禁过严：**两轮都是新门禁先于被测代码暴露自己的缺陷**，这正是"判据必须先
      被证明会咬人"的理由。
    - **变异验证 6 条（每条 RC 取自命令自身）**：注入未折叠的 `allow_noop` 形参 →
      `build() 的 allow_* 形参没有落进袋里（收下即丢）：['allow_noop']`；`build()` 内折叠幽灵键
      → `构造期折叠了无人消费的 option 键（幻影开关）：['allow_noop']`；在 `client/api.py` 的
      调用点注入 `phantom_key` → 同形报错并指名 `['phantom_key']`；把消费扫描改瞎 →
      `执行面一个 option 键都没读到，说明消费扫描自身失效了`；把调用点扫描改瞎 →
      `生产代码里一个 QuerySpec.build 调用点都没找到，说明调用点扫描自身失效`；未变异对照
      `RC=0`。
    - **文档不再手抄清单**：`README.md` 与 `docs/providers/README.md` 原先各自点名
      "`allow_stale` 恒被拒绝"，现改为**按 `REJECTED_OPTIONS` 引用**——被拒键增减时文档不必
      跟着改，也就不可能过期（F-39/F-42 的口径）。
    - **复测（同一轮日志；HEAD `02b38d9` + 本步 5 个文件的孤立 worktree）**：整仓离线
      `-m "not network"` junit **3347 tests / 0 failures / 0 errors / 5 skipped**、
      `ISO_FULL_RC=0`、145.6s，`--cov=tstdx` **80.59%**（日志明写 `Required test coverage of
      77.0% reached`，阈值 77 未下调）。条数可核对：第 21 步合树 3344 − 删除的
      `test_allow_partial_is_limited_to_quotes` + 参数化 2 条 + 构造形参 1 条 + 袋级门禁 1 条
      = **3347**。5 条 skip 全部是 pyarrow/duckdb 依赖缺席模拟（第 21 步同仓曾测得 7 条，
      本步未新增或删除任何 skip 标记）。同树 originality `--strict`（189 · Suspicious 0）、
      `spec_audit --json --strict`（`coverage_pct: 100.0`）、`golden_audit --gate
      --require-markets`、reachability `--strict`（无未登记孤儿 ✓）、`contract_audit --ci`
      （**63 契约 · 155 capability**，PENDING 逐行 184 条与主树完全一致）、docs links
      （82 文件）、`ruff check`、`ruff format --check`（457 files）、`mypy tstdx/`（CI 参数）
      **全部 RC=0**。
    - **为什么本步必须开孤立 worktree**：测量期间并行会话正在改
      `tstdx/runtime/executor.py` 与 `tstdx/catalog/capability.py`，主树整仓同轮实测
      `FINAL_FULL_RC=1`、3 条红同一根因
      `TypeError: DirectProviderExecutor._tdx_client() missing 1 required positional
      argument: 'timeout'`（`test_kernel_config_wiring` 1 条 + `test_local_day` 2 条），
      与本步零交集，故未代为修改；权威数字取孤立树（第 18/19/20 步同法）。
    - **本步判据的已知边界（如实登记）**：袋级门禁只看**字面量**键——以变量或 `**kwargs`
      注入的键不在静态差集里。这条边界是有意的：`REJECTED_OPTIONS` 的检查发生在
      `normalized()`，对任何来源的键一律生效，所以"被拒的键"不依赖字面量可见性；剩下的
      风险只有"塞进一个既不被拒也没人读的键"，那需要调用方自己构造，且其后果与本步删除前
      的 `allow_partial` 相同（无效果），不再新增生产侧幻影。
    - **第 23 步对该边界的修正（写在原条目之外的同一处）**：探针实测显示这条边界比当时的
      措辞更宽——`Client.execute(QuerySpec.build(..., options={'max_age': 0}))` 是公开路径，
      且 `REJECTED_OPTIONS` 只在取值为真时命中（`allow_stale=False` 一样能入袋）。本步把
      "不被执行面读取"整体变成构造期错误（`EXECUTED_OPTIONS` 白名单），故上述"剩下的风险"
      已经不存在；字面量局限仍在，但它现在只影响门禁的**告警覆盖面**，不影响运行期拒绝。

23. ✅ **入参面收口：三张服务面的"已声明字段"钉住，`options` 袋改为 fail-closed 白名单
    （2026-09-19，见 §0.3 F-46 追记与 F-47 登记）**：F-43/F-46/F-27/F-28 已经分别给
    `QuerySpec` 字段、袋里键、CLI 选项立过"收下就必须被消费"的判据，唯一没有对应物的是
    **对外服务面的签名**——路由形参与 JSON Schema 属性同样是"看起来生效"的入口。本步补上
    这三条门禁，并顺手把袋的判据从"名单内的键要有理由"升级为"袋里只许有被执行面读取的键"。

    - **三条新门禁**（`tests/runtime/test_migrated_surfaces_v13.py`，与三面既有委托回归同处
      一个文件）：① `test_http_route_parameters_all_reach_the_execution_path`——AST 取带
      `@app.get/@app.post` 装饰器的函数（实测 **10** 条，下限 8 条自曝），每个形参必须出现在
      同一函数体的 `ast.Load` 名字里，否则报"查询参数没有进入执行路径（幻影开关）"；
      ② `test_mcp_input_schema_and_handler_agree_in_both_directions`——9 个 `ToolSpec` 的
      `inputSchema.properties` 与其 handler 从 `args` 袋读出的字面量键**双向相等**（多一个是
      幻影开关，少一个是未声明输入）；③ `test_ws_declared_methods_are_all_dispatched`——
      `RuntimeJsonRpcHandler.METHODS`（**10** 项）与 `_dispatch` 里的分派分支双向相等。
      三条各带"扫到 0 个即门禁自身失效"的断言（路由 <8、工具 <5、分派分支 <5、schema 属性
      为空都直接报"扫描自身失效"），沿用第 20/22 步确立的自我校验形状。
    - **`trades` 教给扫描器的一课（第三条判据首轮先红在自己身上）**：WS 的分派里
      `snapshot`/`minute`/`trades` 共用一个 `if method in {"snapshot", "minute", "trades"}`
      分支（`runtime_ws.py:191`），只认 `method == "字面量"` 的扫描把 `trades` 报成"声明却未
      分派"——**这是假红，但恰好证明判据真的在比对两侧**。补上 `ast.Set`/`Tuple`/`List`
      比较项后 GREEN；变异 `G3b` 把该分支的集合比较识别整段关掉，判据立刻重新指名
      `['trades']`，把这条形状钉成可回归的判据。
    - **袋改为 fail-closed 白名单（`tstdx/query.py`）**：新增 `EXECUTED_OPTIONS`
      （`args` / `kwargs` / `market`，即执行面真实读取的三处），`normalized()` 在
      `REJECTED_OPTIONS` 之后核对其余键，凡不被执行面读取一律 `ValidationError`（context 带
      `unknown_options` 与名单本身）。这一步让 F-46 条目里"袋里的键从此没有第三种"那句
      **第一次真正成立**——`options={'max_age': 0}` 与 `options={'allow_stale': False}`
      （假值绕过了策略键专属理由）现在都在构造面当场被拒，而不是被 `Client.execute` 带进
      执行面静默忽略。白名单是一份**刻意留下的抄本**，由袋级门禁的第三条断言把它钉回真实
      读取点：名单多一个键报"袋白名单与执行面实际读取点脱节：名单多 ['max_age']"，少一个键
      报"名单漏 ['market']"，两个方向都红，抄本因此不可能悄悄过期。
    - **配套测试**：`test_unknown_option_key_is_rejected_even_when_falsy`（三种形状：陌生键、
      假值策略键、`None` 值键）、`test_every_executed_option_key_still_compiles`（以
      `sorted(EXECUTED_OPTIONS)` 为分母，白名单新增键而测试没配取值即报"缺探针取值"）；
      `test_options_order_does_not_change_query_identity` 原先用 `{"a":1,"b":2}` 这类任意键
      演示指纹顺序无关，改为袋内真实键——**判据不该靠违规输入才成立**（同 F-45 那条把缺陷
      写成契约的旧测试同理）。
    - **变异验证 9 条（每条 RC 取自命令自身，`MUTBAG_RC=0` / `mut_wire` 同轮 5 条 RC 全中）**：
      袋侧 M1 删掉白名单检查 → `DID NOT RAISE ValidationError`；M2 名单加执行面不读的键 →
      "名单多 ['max_age']"；M3 名单删掉 `market` → "名单漏 ['market']"（同轮接受面测试也红，
      真实入参开始被拒）；M4 让策略键的专属理由永不触发 → 参数化测试报"实际消息是通用白名单
      消息"，顺带量到一个事实：**白名单已经覆盖"拒绝"这个动作本身，被覆盖的只是"为什么拒绝"
      这句话**；wire 侧 G1 给 `quotes()` 加不使用形参 → `['unused_probe']`；G2 给
      `query_capability` 的 schema 加 `max_age` 属性 → "声明却无人读取 ['max_age']"；
      G3 在 `METHODS` 里声明不存在的 `history` → "声明却未分派 ['history']"；G3b 见上；
      未变异对照 `RC=0`。
    - **复测（同一轮日志；HEAD `ac49328` + 本步 9 个文件的孤立 worktree `wt_wire`，因并行会话
      正在改 `tstdx/runtime/executor.py`、`tstdx/catalog/capability.py`、
      `tests/sink/test_local_day.py` 与 `tests/runtime/test_kernel_config_wiring.py`）**：
      整仓离线 `-m "not network"` junit **3354 tests / 0 failures / 0 errors / 5 skipped**、
      `ISO2_FULL_RC=0`、161.6s，`--cov=tstdx` **80.59%**（日志明写
      `Required test coverage of 77.0% reached. Total coverage: 80.59%`，阈值 77 未下调）。
      条数可核对：第 22 步 3347 + wire 门禁 3 + 袋侧新测试 4 = **3354**。本步在同树先跑过
      一轮只含 wire 门禁的测量（**3350 / 0 / 0 / 5**、80.58%），随后加袋白名单再跑到 3354，
      两次都绿，权威数字取后一次。同树 originality `--strict`（`Total: 189 Suspicious: 0`）、
      `spec_audit --json --strict`（`coverage_pct: 100.0`）、`golden_audit --gate
      --require-markets`、reachability `--strict`、`contract_audit --ci`（PENDING 仍是既有的
      Typed Query 契约缺口，本步未新增）、docs links（82 文件）、`mypy tstdx/`（CI 参数）、
      `ruff check tstdx/ tests/ scripts/`、`ruff format --check tstdx/ tests/ scripts/`
      （457 files already formatted）**全部 RC=0**。
      **一处测量口径纠正**：本轮先用 `ruff format --check .` 量到 RC=1，7 个"未格式化"文件
      全在 `docs/**.md`（`ruff` 只在被显式指到路径时才格式化 markdown 代码块），而 CI 与实际
      门禁口径是 `tstdx/ tests/ scripts/`（`.github/workflows/ci.yml:37`）；按 CI 参数复跑
      `RC=0`。教训与 F-21 同形：**判据的失败必须先在门禁真实定义的范围内复现，才允许登记**。
      主树同轮 subsets 复跑只有一条红，且属并行会话在途文件
      （`test_kernel_config_wiring.py::test_default_deadline_leaves_the_configured_timeout_intact`
      断言 `timeout == 5.0` 而实得 `4.9999891`，是剩余时间取整的形状），本步未代为修改。
    - **F-47 登记为待裁决**：三面（HTTP 查询串与 body、WS `params`、MCP `arguments`）对
      **未声明**请求字段仍一律静默忽略——探针实测见 §0.3 F-47。与 F-44 同格，属对外契约的
      破坏性变更（今天返回 200 的请求会开始返回 4xx），故本轮只钉事实与三条候选路径
      (a)/(b)/(c)，不代为拍板；袋侧因已有 F-46 的同类先例（clean break 口径）而当场清偿。

24. ✅ **`deadline_ms` 不再是折进对象就完事的幻影旋钮：预算第一次约束每一跳；分页也
    只剩一份实现（2026-09-19，见 §0.3 F-48/F-49）**：第 20 步删掉 `max_age` 时给字段
    立了"必须被消费"的判据，但该判据把**规划器自读**算成消费，于是同一个缺陷换了一个
    更深的位置活下来——`deadline_ms` 被折进 `plan.budget`，`ExecutionBudget` 的两个方法
    从此零调用点。本步同时补上 F-45 那一族的漏网：`security_list_all` 的影子分页。

    - **取数口只有一个**：`DirectProviderExecutor._hop_timeout(plan)` 先 `ensure_remaining`
      再 `min(self.timeout, budget.remaining_s())`；`_migrated_capability` 一次取值 `hop`
      分发给 6 类后端，7 个 `_tdx_*` 直调执行器经 `_tdx_client(timeout)`，5 个 Web 直调
      执行器各取一次，`_composed_call` 的签名从此强制要求 `timeout`。
    - **不静默改变默认行为**：默认 `deadline_ms=5000` 与默认 `[core] timeout=5.0` 同值，
      `min()` 在默认路径上是恒等操作，留一条 `== 5.0` 的断言把这个"不变"也钉住（**该断言从未通过——取小的另一侧是单调时钟剩余量；判据已改为 `4.9 < hop <= 5.0`，本条记录与本步复测数字的互斥见 §0.3 F-53 ①**）；
      预算耗尽时的 `ReadTimeout` 发生在**构造客户端之前**，断言里连"零个客户端实例"
      都要核。
    - **判据形状的两处教训**：① 字段侧改为"读的人必须在执行面"（跳过 `query.py`），
      `spec → plan.budget` 的折叠关系由 `QueryPlan(...)` 构造处 AST 推导，不抄名单；
      ② 逐函数断言追不上新增直调执行器——首轮变异 M3（`_web_quotes` 退回 `self.timeout`）
      在只测 `_tencent_bars` 时活了，补一条"全文件只允许 `_hop_timeout` 读 `self.timeout`"
      的结构判据后，M3 与另一条新造的 M8（TDX 跳绕过预算）双双变红。继第 20/22 步之后，
      这是同类门禁**第三轮**先红在自己身上。
    - **分页收口**：`security_list_all` 改绑 `TdxClient.export_security_list`，composed 面
      那段无页数上限、空首页静默的 `while True` 物理删除，`_validate_composed` 的两张参数
      名单同步失去该条目；反向门禁断言 `_composed_call` 函数体内**没有任何 `ast.While`**——
      命令名清单不是判据，形状才是。
    - **变异验证 8 条全部 RED 且各自指名**（M1 `min()` 退化成 `self.timeout`、M2 删掉
      预检查、M3 Web 跳绕过预算、M8 TDX 跳绕过预算、M4 绑定改回单页 `security_list`、
      M5 游标循环插回 composed、M6 豁免表漏掉 `currentness`、M7 身份读取函数改名使豁免
      前提失效），harness 结束后三个被改文件 `md5sum -c` 全部 OK——**测量过程零残留**。
    - **本步抓到并修的是自己**：主树首轮整仓 2 条红，根因
      `TypeError: lambda() takes 0 positional arguments but 1 was given`——
      `tests/sink/test_local_day.py` 的 `_tdx_client` 替身签名比生产窄（F-28 已给同类
      立过判据："测试替身比生产接口更窄"正是幻影参数的成因）。改的是替身签名并加一句
      理由，**未放宽任何断言**。
    - **复测（同一轮日志；HEAD `ac49328` + 本步 4 个文件的孤立 worktree）**：基线同树实测
      `3347 tests / 0 failures / 0 errors / 7 skipped`（`BASE_RC=0`），加本步文件后
      junit **3356 tests / 0 failures / 0 errors / 7 skipped**、`ISO_FULL_RC=0`，条数可核对：
      `3347 + 9`（`test_kernel_config_wiring.py` 由 6 项扩到 15 项）。`--cov=tstdx`
      **80.62%**（日志明写 `Required test coverage of 77.0% reached`，阈值 77 未下调，
      本步未新增或删除任何 skip 标记）。同树 originality `--strict`（`Total: 189 Suspicious: 0`）、
      `spec_audit --json --strict`（`coverage_pct: 100.0`）、`golden_audit --gate
      --require-markets`、reachability `--strict`（无未登记孤儿 ✓）、`contract_audit --ci`
      （**63 契约 · 155 capability**，与第 17/19/20/22 步逐项相同）、docs links（82 文件）、
      `ruff check`、`ruff format --check`（427 files）、`mypy tstdx/`（CI 参数）**全部 RC=0**。
      主树同轮另测得 `3359 / 0 / 0 / 7`、80.63%——多出的 3 项来自并行会话在途的第 23 步
      门禁，与本步零交集，故权威数字取孤立树（第 18/19/20/22 步同法）。
    - **账本与本步代码不在同一提交**：代码与测试 4 个文件已作为 `524c687` 单独提交；
      本条与 §0.3 F-48/F-49 落在并行会话正在编辑的同一份账本文件上，属第 20 步已登记的
      共享账本风险——若本提交带上了对方在途的账本行，史实以对方提交为准。
    - **登记未办**（本步不动，等裁决或等文件腾出）：`ExecutionBudget` docstring 的
      "shared by the whole logical query" 对 `policy` 扇出不成立（每个 symbol 重新
      `compile()` 即重新计时），该文件正被并行会话编辑；F-45 尾条的两处（`QueryResult`
      warnings 通道、`Client.bars()` 的 `strict`）与 F-44/F-47/F-18 同批待裁决。
      （前一项已在第 25 步随 F-50 一并改写，后两处仍在等裁决。）

25. ✅ **幻影判据反过来量计划自己：plan 上的无人读取副本、预算里从未驱动过的尝试记账、
    注册表里那个与真相矛盾的 60，一并清掉（2026-09-19，见 §0.3 F-50/F-52）**：第 20 步钉
    "字段必须被消费"、第 24 步钉"读的人必须在执行面"，两步的分母都只在**入参**那一侧。本步
    把同一把尺子搬到**计划**这一侧，一次量出三处：`QueryPlan` 的四个字段（plan 之外读取点 0）、
    `ExecutionBudget` 的尝试记账（`begin_attempt` 全包读取 0 次）、`ChannelSpec.batch_limits`
    → `batch_limit_for()` → `plan.batch_limit` 的整条死链（唯一读者是写入它的规划器）。

    - **删除而非接线，且给出可核验的理由**：`batch_limit` 若"接成执行期校验"，就是把 quotes
      分片从真正生效的 `_QUOTES_SNAPSHOT_BATCH = 80` 静默改成注册表里的 60——一次无人要求的
      行为变更。数字留在执行它的地方，注册表从此不声称配额；`ExecutionBudget` 只留墙钟，
      docstring 顺带改正口径（一次 `execute()` 而非"整个逻辑查询"，关闭 F-48 尾条 ⑥）。
    - **判据按形状、且能自曝失明**：`test_every_query_plan_field_is_read_by_the_execution_face`
      的分母取 `dataclasses.fields(QueryPlan)` 而非名单；
      `test_execution_budget_has_no_unexercised_member` 要求每个公开实例方法经由
      `budget`/`plan.budget` 被执行面读到，构造子 `from_deadline_ms` 的例外**自身也要核验前提**
      （它一旦不再是类方法即红）。两条都带"扫描零命中 ⇒ 门禁自己瞎了"的断言，M4 变异实测该
      断言真的会说话。
    - **测试侧的"抄本断言"一并改判**：`test_query_contracts.py` 三处 live/local/batch 断言改读
      `PROVIDERS…channel(...)` 真相源，`test_query_planner.py` 的 deadline 断言从
      `plan.deadline_ms == 1000` 改为比 `plan.budget.deadline_ns` 的 8 秒差值与
      `remaining_s()` 次序；注册表侧原两条维持死面的用例改写成注册表真正执行的那条规则
      （`periods` 只属于 bars channel，此前**零覆盖**）+ 位置式构造兼容断言。**未放宽任何断言、
      未新增或删除任何 skip。**
    - **变异验证 4 条全部 RED 且各自指名**（M1 把 `deadline_ms` 副本装回 plan、M2 把
      `begin_attempt()` 装回预算且只在 `query.py` 内调用——正是 F-50 的真实形状、M3 让注册表
      重新声称 `batch_limits` 并把"字段不存在"那条断言削弱成永真（`repr` 扫描仍然指名）、
      M4 把读取扫描改瞎（自曝失明断言命中）），harness 结束后被改文件全部还原，测量零残留。
    - **复测改在提交树本身做，不在拼装树上做**（这条口径变更由并行会话在 `9127d78` 与
      `15bfb61` 里完成，本文件只在第 26 步把已提交的 `CHANGELOG.md` 口径同步进来，不重复测量）：
      先按第 18/19/20/22 步的旧法在"HEAD `288e62f` + 本步 7 个文件"的拼装 worktree 量得采集
      3373 → 3375，随后把本步代码提交为 `9fbece0` 并**从提交树另开 worktree 复跑**——提交内容
      经 hunk 过滤后与拼装树并不逐字节相同（`test_kernel_config_wiring.py` 的那条每跳超时断言
      在拼装树里仍是 `== 5.0` 的旧写法）。提交树的第一条读数就是**一条红**：
      `test_every_documented_cli_example_parses` 报本文件 `exit 2`，由 `9127d78` 单行改写清偿。
      清零后的同一提交树：采集 **3375** 项、`-m "not network"` 选中 **3365** 项、**0 failed /
      0 errors / 7 skipped**、139s、`--cov=tstdx` **80.64%**（`Required test coverage of 77.0%
      reached`，阈值 77 未下调，未增删任何 skip 标记）。**两套 junit 口径不可混读**：`tests=`
      计的是采集总数（3375 = 选中 3365 + 被 `-m` deselect 的 10 条网络用例）。第 26 步在
      `9fbece0` 的干净 worktree 单跑该门禁复现到那 1 项红（RC=1，offender 唯一），与这条口径
      一致。
    - **一条记录缺陷的账本行自己臆造了一个读取面**：F-52 行的 (a) 路径把自省面写成了 CLI parser
      里并不存在的一个子命令（真实自省面是 HTTP 的 `/v13/capabilities` 与 WS 的
      `runtime.capabilities`，两处都已逐 provider 回显 channel→capability 映射；CLI 侧只有
      `capabilities` 一张表，没有 Provider 表）。判据没错、错在作者。第 26 步踩到的是同一道
      门禁的另一种形状——把那条命令原样引在句子里，门禁不认识"这是史实引用"，照抓不误（见
      §1 第 26 步 ④）。最终口径与 F-39/F-46 的既有做法一致：文档不手抄幻影，改用可核验的自然
      语言指代。
    - **其余门禁在同一提交树上逐个复跑**：`ruff check` / `format --check` / `mypy tstdx/` / originality `--strict`
      （`Total: 189 Suspicious: 0`）/ reachability `--strict`（188 模块 · 171 可达 · 17 豁免，
      `无未登记孤儿 ✓`）/ `contract_audit --ci`（**63 契约 · 155 capability**，与第
      17/19/20/22/24 步逐项相同）/ `spec_audit --json --strict`（44 specs · 100.0）/
      `golden_audit --gate --require-markets` / docs links（82 文件）/ `tests/adversarial` /
      `tests/test_bridges.py` 在同一提交树上逐个复跑，数字见本轮日志（不改抄）。
    - **一次测量自伤，如实登记**：第一次跑提交树离线全量时给子进程传了一个臆造的环境变量
      `TSTDX_OFFLINE=1`，于是 **86 条**测试红成同一个 `ConfigError: [E1000] 无法识别环境变量
      TSTDX_OFFLINE：段名须属于 ['core', 'hosts', 'rate_limit', 'web', 'security']`。Phase 6
      的 fail-closed 配置装载器行为正确，红的是测量者——记下来是因为这类"整片同因红"极易被
      误读成一次真实的回归，而判别只需读一条错误消息。
    - **测量口径如实登记**：主树同轮实测 1 条红
      （`test_every_executed_option_key_still_compiles[strict]`），根因是并行会话在途的
      `EXECUTED_OPTIONS` 扩了 `strict` 而尚未补探针取值，与本步零交集；本步的 `tstdx/query.py`
      与该会话的改动同文件，故提交前把对方那一格 hunk 逐出本步补丁（`git diff` 按 hunk 过滤后
      在孤立树复验），对方在途内容原样留在主树工作区，未代为提交、未改写。
    - **F-52 登记为待裁决**：同一把尺子量到 `ProviderSpec.display_name`/`role`（三面读取点 0）
      与 `Provenance.provider_timestamp`（从未被传入非默认值、不进 wire 显式键）。两者都是公开
      dataclass 形状，按 F-44/F-47 口径只钉事实与 (a)/(b)/(c) 三条路径，不代为拍板。

26. ✅ **结果侧的瑕疵第一次有载体：一条发射口、一份元数据、内核里一个 `strict`；顺带接通
    解码层那 9 处从未被读过的静默修正（2026-09-19，见 §0.3 F-51/F-53）**：第 21 步给分页立了
    判据，却把判据的**去向**留在半路——`warnings.warn` 只到调用方进程的 stderr，`QueryResult`
    没有任何字段承载它，三张服务面在 wire 上读不出"这次结果带瑕疵"；`Client.bars()` 也没有
    `strict` 形参。本步把这两处（F-45 尾条）与同一轮实测新抓到的解码层断链（`ParseResult.warnings`
    在真实执行路径上零读取点，9 个发射点的静默修正从产生起就无人可见）一次性接通。

    - **发射口只有一个**：新增 `tstdx/diagnostics.py`——`record_warning(code, message, *,
      stacklevel=2, stderr=True)`、12 个 `WarningCode`、`ResultWarning.to_dict()`、
      `contextvars` 收集器 `warning_sink()`。全仓结果侧告警改由它发射，裸 `warnings.warn` 只剩
      三处豁免（发射口自身、`deprecation.py`、执行器把告警换成 `TruncatedDataError` 的那处）；
      收集器是 ContextVar 而非模块全局，**并发服务面共用一个进程时不串台**（A 的瑕疵不会记到
      B 的结果上，这条有专门的线程隔离测试）。
    - **默认行为逐字节不变**：stderr 文案与 `stacklevel` 指向保持原语义（`record_warning` 里
      `+2` 补偿转发帧）、返回数据不变、`strict` 默认 `False`、没有收集器时不抛不吞（CLI 与
      import 期告警的既成行为原样），wire 只**新增** `meta.warnings` 一个键。`stderr=False`
      是给"自己已经去过重"的发射点（复权缺前收盘价、日历未覆盖年份）留的口子：去重是对终端
      的礼貌，不该决定某一次结果要不要声明这个缺陷。
    - **`strict` 归内核单点**：`execute()` 在发起任何 I/O 之前读一次 `options["strict"]`，非
      bool 当场 `ValidationError`，任何瑕疵即 `TruncatedDataError`——判据与 Provider 无关，
      不会再出现"每个适配器各认一次"的幻影开关（F-43/F-46 同族）。该键同时进
      `EXECUTED_OPTIONS`，第 23 步的袋门禁因此自动覆盖它。
    - **解码侧接上**：`_t_bars` 每一页把 `result.warnings` 以 `WarningCode.DECODE_CAVEAT`
      记进通道——"声明 5 条实收 3 条"这类判断从此既在 stderr 也在结果里。
    - **四条新门禁全部按形状判据**：裸 `warnings.warn` 白名单**正反两向**（新增发射点即红、
      豁免过期也即红）；`WarningCode` 每个成员必须有发射点（分母取枚举，不抄名单）；
      `_t_bars` 必须**同时**读 `.warnings` 与调用 `record_warning`（只留一侧即红）；
      `docs/api/interfaces.md` 的 Client 方法表逐行 ⇔ 真实签名双向对账（AST 取形参，本次实测
      只有 `bars` 一格漂移，说明这条门禁此前根本不存在）。
    - **变异验证 10 条全部 RED 且各自指名**（出口漏 `warnings` 键、执行器收下却不装进结果、
      `strict` 读而不用、`strict` 不校验类型、`stderr=False` 仍唠叨、收集器不收货、类别校验被删、
      解码循环被删、声明表多一个无人发射的类别、豁免名单塞入不存在的文件），对照 D 还原后
      RC=0，`MUT_RC 0`；harness 结束后逐文件与主树比对，唯一漂移是
      `integration/serialization.py` 的行尾（`core.autocrlf=true`，索引侧归一为 LF，非内容差），
      复跑该树 `ruff format --check`（460 files）与 42 条相关测试仍全绿。
    - **复测（同一轮日志；孤立 worktree = HEAD `a944964` + 本步 25 个文件，逐文件字节校验）**：
      本步的基线跟着 HEAD 挪过两次（`15bfb61`→`463b9ae`→`a944964`，三个提交都出自并行会话），
      前两组读数随基线作废；下面这组才是与本步提交树同基的那一次：同一提交的纯净树以完全相同
      的参数跑，junit **3367 tests / 0 failures / 0 errors / 5 skipped**、`--cov=tstdx` **80.72%**；
      加上本步 25 个文件后为 **3390 / 0 / 0 / 5 skipped**、`PYTEST_RC=0`、覆盖率 **80.76%**（`Required test coverage of
      77.0% reached`，阈值 77 未下调，未增删任何 skip 标记：两棵树的 5 条 skip 逐名相同，都是
      parquet / duckdb 的可选依赖门）。条数可核对：`3367 + 23`（18 条接线 + 3 条告警通道门禁 +
      1 条 Client 方法表对账 + 1 条 `EXECUTED_OPTIONS` 新探针取值）= **3390**；`-m "not network"`
      下 junit 的 `tests=` 计的是**选中**项，与本文件第 25 步那条"采集数与选中数不可混读"的
      口径一致。同一文本前后两轮跑到 132.7s / 137.5s，计时只作现场参考，不当契约数字写进账本。同树 9 道 CI 门禁逐个复跑 **全部 RC=0**：originality `--strict`（`Total: 190
      Original: 190 Suspicious: 0`，190 = `a944964` 树的 189 + 本步新增的 `diagnostics.py`）、
      `spec_audit --json --strict`（`coverage_pct: 100.0`）、`golden_audit --gate
      --require-markets`、reachability `--strict`（`无未登记孤儿 ✓`）、`contract_audit --ci`
      （**63 契约 · 155 capability**，PENDING 仍是既有的 Typed Query 缺口，本步未新增）、docs
      links（82 文件）、`mypy tstdx/`（CI 参数，`All checks passed!`）、`ruff check`、
      `ruff format --check`（`460 files already formatted`）。
    - **本步抓到并修的是自己（四处）**：① 批量转换器把 `UserWarning,` 留在了形参位上 →
      11 条 web 用例 `TypeError`；② 一个 `SimpleNamespace` 的 meta 替身窄于生产 `ResultMeta`
      → 14 条 CLI 用例红，改判为由 `dataclasses.fields(ResultMeta)` 派生（F-27/F-28 同形）；
      ③ 自己新写的测试撞上 `SIM117`×2 与两处 `ruff format`；④ **自己新写的账本行在同一条
      门禁上多加了一条 offender**——F-53 引用那条假子命令时把 CLI 前缀写进了行内代码，而
      `test_every_documented_cli_example_parses` 抽取时不看语境，把「我在描述一个缺陷」读成
      「我在推荐一个命令」。同一轮两份日志可核对：带着自己那行时门禁列出两条同文 offender，
      改写为不带前缀的写法后只剩 HEAD 的一条。第 20/22/24/25 步之后，这是同类门禁**第五轮**
      先红在自己身上。**①②只在跑全量时才现形**——此前一轮 74 条聚焦子集全绿并不能说明
      什么，"聚焦子集不是发布测量"这条本步再次自证。
    - **他人遗留：本步只补齐自己那一半，不把别人的清偿记到自己账上（F-53）**：
      `test_kernel_config_wiring.py` 里那条对单调时钟剩余量求浮点相等的断言（自 `524c687` 起
      从未通过，实测 `4.9999834 == 5.0`）由本步改为 `4.9 < hop <= 5.0` 并写明理由——代码由并行
      会话带进 `9fbece0`，账本由本步补齐。HEAD `9fbece0` 上那条 CLI 示例红**不属于本步**：本轮
      在 `9fbece0` 的干净 worktree 复跑确认它是 1 项红，在当前 HEAD `15bfb61` 上同一测试 RC=0，
      清偿它的是 `9127d78`（单行改写）与 `15bfb61`（撤回挂在拼装树上的复测数字）；本步起初把
      这条红记成自己清零，现按复算改回归属。`docs/api/interfaces.md` 里从 `5c50487` 起就语法
      不通的 `options={,` 修为 `options={}`（这处是本步改的），并如实登记"围栏 python 块不在
      任何门禁的解析范围内"这条盲区。
    - **本步最后一次改账本时自己砍了自己一刀，如实登记**：批量替换脚本用各步共用的条目前缀
      （`- **复测（同一轮日志；HEAD`）当锚点，而第 19/20/22/23/24 步各有同形条目，于是第一次
      运行把第 26 步往前累计的 **254 行**吞成了一段。恢复靠的是本步为测量而留的孤立 worktree
      副本（还原后逐字节比对一致，`git diff --stat` 回到预期的 160 增 / 11 删）。教训写在这里
      是为了下次不再用共用形状当锚点：改共享账本必须锚在**只此一处**的独有内容上（本轮独有
      的数字、提交号），并先断言命中数为 1。
    - **根级白名单再超限，按偏差登记而非静默改口径**：`tstdx/*.py` 从原案上限 10 变为 **11**。
      告警发射口必须同时被 `domain/`、`web/`、`client/`、`runtime/` 引用，放进任何子包都会让
      低层反向依赖上层，故落在根级跨层原语位（`errors.py`/`result.py` 旁）。**上限本身没有
      数值门禁**（`ROOT_WHITELIST` 是枚举集合），因此这一步既改白名单，也把"超限"写进 Phase 3C 的
      实际映射表与 §4 总体验收清单，不假装它没发生。
    - **登记未办（本步不动，等裁决）**：`Client.typed()` 返回 `TypedQueryResult(data,
      capability)`，既不带 provenance 也不带 warnings——接进 Typed Query 面要改 63 张契约的
      返回形状（F-51 尾条 (a)）；`strict` 是否开放为 HTTP/WS/MCP 请求参数属 F-47 的裁决；
      通道只覆盖"结果侧瑕疵"，异常路径归 `errors` 那一半（F-44）。
    - **共享树上此刻有他人在途改动，本步按文件归属切开**：第 25 步合树之后，并行会话正在按
      F-52 的 (b) 路径动 `tstdx/providers/__init__.py`、`tests/providers/test_registry.py` 与
      `docs/providers/README.md`（`display_name`/`role` 的物理删除，本步提交时仍未收口）。这
      三个文件既不进本步提交，也不进本步的测量树——孤立 worktree 取 HEAD + 本步自己的文件后
      逐文件字节校验，否则"本步全绿"会混进别人尚未落地完的形状；第 24/25 步登记的共享树风险
      由此复现，上一版本这一步写的"主树 diff 逐文件都是本步自己的改动"在复测当时已经不成立。
      另一条交接：第 27 步因本步正在改写 `tstdx/result.py` 而把 `Provenance.provider_timestamp`
      的删除推后——本步合树后该文件不再被占用，那一半的裁决位仍在 §0.3 F-52 行，本步不代拍板。


30. ✅ **出处面上最后一个没人读的字段：`Provenance` 里那个从未被填过的"源时间戳"（2026-09-19，
    清偿 §0.3 F-52 剩下的那一半，按 (b) 物理删除；编号接续执行序列——第 27/28/29 步的收口写在
    §0.3 与 CHANGELOG，未在 §1 单开条目，跳号不是漏项）**：同一把尺子（第 27/28 步那把）量到
    `Provenance.provider_timestamp`（HEAD `dab85b5` 的 `tstdx/result.py:57`）上是三重沉默——
    AST 扫 `tstdx/` 全部 189 个模块对它的读取点 **0**；全仓没有任何调用方给 `Provenance.direct()`
    传过非默认值，即没有任何 Provider 上报过源侧时刻；`integration/serialization.py` 按显式键
    构造 wire，键集合里根本没有它。一个永不为非 `None` 又永不被读的字段，唯一效果是让"直连结果
    的出处比本地观察更丰富"这句话只在 `help()` 与 ADR 里成立。
    - **删而不接（本行的 (b) 路径）**：接线要 9 个解码器开始上报源时间戳，那是新增的对外承诺而
      它们今天一个都拿不出这个数据；`observed_at_ns` 承载的才是唯一真实可知的时刻（本地观察
      时刻）。字段连同 `direct()` 的同名形参一并物理删除，不留 `= None` 兼容位——与 F-40/F-50
      /F-54 同口径的 clean break。删除后 `Provenance` 剩 8 个字段，逐字段读取点实测 `provider` 2、
      `channel` 2、`capability` 2、`kind` 2、`observed_at_ns` 1、`cache_tier` 1、`requested_provider` 1、
      `fallback` 1，全部有人按它行动。
    - **删除不留后门（判据是双向的）**：`test_deleted_provenance_field_is_not_a_back_door` 同时
      钉两件事——显式关键字构造 `Provenance` 当场 `TypeError` 且消息含字段名（不是静默收下再丢掉），
      `Provenance.direct()` 的签名里不存在这个形参（唯一的公开构造入口）。
    - **尺子收成一把，判据自己也不该有抄件**：第 27/28 步把读取点扫描写成
      `tests/providers/test_registry.py` 的私有 `_unread_fields`，本步给结果面复用同一把尺子时
      把它搬进 `tests/support/field_readers.py`（全仓唯一实现，`test_registry.py` 改为 import），
      避免第三份实现；owner 名单仍逐类人工核对，不塞进公共实现的默认值——第 28 步登记的
      "普查不成立"边界正是为此起的。防盲保险沿用三条：`fields` 非空、`scanned > 30`、`reads` 非空。
    - **新门禁 `tests/architecture/test_result_shape_gates.py`（5 条测试）**：`Provenance` 与
      `ResultMeta` 各一条读取点判据、各一条顺序敏感形状清单，加那条后门判据；owner 名不放 `self`
      （类内 `__post_init__` 的自检不算字段被兑现的证据，且 `self.capability` 在别的类里另有含义）。
    - **变异验证（4 项，跑在 `dab85b5` + 本步 4 文件的孤立 worktree，逐项复原后逐字节校验一致，
      主树全程未被写过生产文件）**：**M1** 把 `provider_timestamp` 以默认值装回类定义 → RC=1 并
      点名 `['provider_timestamp']`；**M2** 把 owner 集合换成不可能命中的 `zzz_unbound_name` → RC=1
      报的是"扫描一条都没命中，判据自身失效"，不是"字段没人读"；**M3** 给 `Provenance` 新增一个
      没人读的 `notes: str = ""` → RC=1 点名该字段；**M4** 给 `ResultMeta` 同样加一个没人读的
      `notes` → 读取点判据**假绿**、形状清单 RC=1 红。CONTROL 与还原后 RC=0。
    - **M4 是本步唯一测出来的判据缺陷，如实登记而不是抹掉**：`tstdx/cli/runtime_commands.py:406`
      里的 `result.notes` 属于探针结果对象而非 `ResultMeta`，AST 层面同名字段无法区分所有者——
      这是第 28 步"普查不成立"那条边界的第三个现场（前两个：`self.` 根、实例未绑定具名变量）。
      出路不是把尺子改宽，而是补第二层判据：形状清单对字段顺序敏感，回长一个字段必须先改动
      清单，改动就落到纸面上。加清单后 M4 由绿转红，两层判据的分工写在模块 docstring 里。
    - **第一轮门禁抓到本步自己的一处缺陷**：新测试文件的 import 段少了第三方与首方之间的空行，
      `ruff check` 当场 rc=1；补空行后重跑，本轮引用的每个数字都来自修正后的那一轮。
    - **本步不动的**：F-52 行只把状态改判为"已清偿（按 (b)）"并保留三条路径原文，(a)/(c) 未采纳；
      若日后要让 Provider 上报源时间戳，属新增对外承诺，需要新的发现编号而不是本行回潮。
    - **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `dab85b5` + 本步 4 文件，与本步
      提交树逐文件同内容）**：基线取干净 `dab85b5` 的同轮实测 junit **3391 tests / 0 failures /
      0 errors / 5 skipped**（157.1s）、`--cov=tstdx` **80.77%**；本步树 junit **3396 / 0 / 0 / 5**
      （148.2s）、`FINAL_RC=0`、**80.77%**，对账 `3391 + 5`（本步 5 条新门禁）= **3396**，
      阈值 77 未下调。同树 9 道门禁全部 RC=0（`GATES_RC=0`）：originality（Total 190 / Suspicious 0）、
      reachability（189 模块 / 172 可达 / 17 白名单）、`contract_audit --ci`（63 契约 · 155 capability）、
      `spec_audit --strict`（coverage 100.0%）、golden 审计、docs links（82 文件）、mypy（RC=0）、
      `ruff check`、`ruff format --check`（462 文件）。**口径边界如实登记**：上表的门禁与全量数字
      取自本步 4 个代码文件的树（148.2s）；两处账本 .md 写入后，在**与提交树逐文件同内容的 6 文件
      树**上重跑离线全量与 docs links，读作 **3396 tests / 0 failures / 0 errors / 5 skipped**、
      `FINAL_RC=0`、143.8s、`--cov=tstdx` **80.77%**、docs links RC=0——与上一轮同条数同百分比，
      只差墙钟，即本节数字属于它所声称的那棵树（F-53 口径）。
    - **共享树隔离**：本步只提交 `tstdx/result.py`、`tests/providers/test_registry.py`、
      `tests/architecture/test_result_shape_gates.py`、`tests/support/field_readers.py`，加本文件的
      §0.3 F-52 一行与 §1 第 30 步一条，以及 `CHANGELOG.md` 一处。§0.3 F-52 行只改状态格与行尾
      （两半都是删除路径落地后的改判），第 27 步那半的记录与 (a)/(c) 两条未采纳路径原文保留；
      F-54/F-55/F-56 三行、第 28/29 步的 CHANGELOG 记账、`docs/configuration.md` 与
      `uv.lock` 均未被写入本步提交。

31. ✅ **最后一条旁路数据命令收进内核：CLI `stream` 不再自己造流（2026-09-19，清偿 §0.3 F-56，
    按默认 (a)；编号如实登记——本步起草时是"第 30 步"，并行会话在那之前把 F-52 provenance 半边
    落成 `60f6be9` 并占用第 30 步，故本步顺延为 31，两处账本的编号一起改）**：`cmd_stream` 曾
    `from ..streaming import QuoteStream` 并以 `_transport_kwargs(args)` 自建对象，绕开唯一业务入口
    `Client` 与唯一流式契约 `StreamPlanner`；`QuoteStream._get_runtime()` 再惰性 new 出第二个
    `UnifiedRuntime`。副作用是两面不同答案：CLI 的 `--provider` 是自由字符串、全路径无流式白名单，
    于是 CLI 承诺"注册表支持 `quotes` 轮询的 Provider 都能 stream"，而库面对同一个 Provider 抛
    `ValidationError`。
    - **收口形状**：`with Client(**_client_kwargs(args)) as client: client.stream(symbols, provider=…,
      interval=…, diff_only=…, max_queue=…, on_quote=…, on_error=…)`，随后仍由 CLI `start()`/`stop()`
      并打印计数；零数据 → exit 1 的判据逐字不变。连接参数由 `_transport_kwargs` 换回
      `_client_kwargs`，即第 8 步（F-27）那条"CLI 只转达用户显式说过的，其余交给内核读配置面"。
      两面从此同答案：非法 provider 得到 exit 2 + 与库面同一句话。
    - **顺带补上 `--host`**：该子命令原先自己写 `--provider` 而完全没有 `--host`。旁路时代声明了也
      无人消费（`_transport_kwargs` 那条通道本就不进内核），因此这条缺失一直没被 F-28 的选项消费
      审计抓到；改走 `Client` 后由 `_provider_args()` 与 `snapshot`/`minute`/`trades` 同形声明。
    - **守卫**：`test_service_faces_never_build_a_stream_themselves` 与 F-29 的 web 守卫共用
      `_service_face_imports()`（AST 扫 CLI + HTTP/WS/MCP 的全部 import 边，函数体内的 import 同样算，
      相对导入解析为绝对模块名），禁 `tstdx.streaming` / `tstdx.stream_contract`；零命中即自曝两条
      门禁同时失明。`test_stream_forwards_provider_and_connection_args` 从"monkeypatch
      `tstdx.streaming.QuoteStream` 钉住旁路形状"改写为捕获 `Client` 构造参数与 `Client.stream`
      实参（含 `on_quote` 回调确实转达、`with` 退出即关客户端）；新增
      `test_stream_command_refuses_a_provider_the_stream_contract_refuses` 用真实 `Client` 跑
      `main(...)` 并核对信封文案；`tests/unit/test_cli_semantics.py` 两条 stream 用例的替身同样从
      `QuoteStream` 换到 `Client`。
    - **本步的变异装置先量到自己**：M2 首轮 `assert 0 == 2`——那条走真实 `Client` 的离线用例在回归
      现场真的把 tdx 流起来并收到了一帧。按 `test_dunder_docstring_quickstart_examples_construct`
      的既有做法拦 `socket.getaddrinfo` / `socket.create_connection` 后，同一条变异改报
      `assert 1 == 2`（拒绝仍成立、egress 不再发生），判据同时收紧到信封文案，任何别的 exit 2 都不算通过。
    - **让一条既有宣称第一次为真**：`docs/api/interfaces.md` §3 与 `docs/api/README.md` 自第 15 步起
      就写着"数据命令全部委托同一个 `Client`，6 个传输/诊断命令除外"，而 `stream` 当时是第 7 条旁路；
      §5 更是把轮询基类当流式订阅门面来写、通篇不提 `StatefulQuoteStream`。本步把两处口径与
      `tstdx/streaming/__init__.py` 的 docstring（原文声称基类"已从公开/核心面移除"，而它在
      `__all__` 里且是 CLI 的生产路径）一并归位。
    - **变异验证 3 条全部 RC=1 且各自指名**（CONTROL 与还原后 RC=0）：**M1** 在 `cmd_stream` 里加回
      `from ..streaming import QuoteStream` → 新守卫红，报 `tstdx\cli\runtime_commands.py: import
      tstdx.streaming`；**M2** 删掉 `provider=args.provider` → 三条同时红：转发用例 `KeyError:
      'provider'`、拒绝用例 `assert 1 == 2`、F-28 的选项消费审计报 `stream: --provider
      (dest=provider)`（转发与消费两条判据各看一半，互补而非重复）；**M3** 把 `--host` 撤回成
      `--provider` 独写 → 转发用例在 `parse_args` 处 `SystemExit: 2`。
    - **本步不动的**：`QuoteStream` / `AsyncQuoteStream` 保留在 `__all__` 与包内（它们是 `Stateful*`
      的轮询基类，删除会把生命周期实现一起搬回来）；(b) 的"把 tdx-only 判据下沉到 `QuoteStream`"
      与 (c) 的"只收紧门禁不改行为"未采纳。F-47（`strict` 是否开放为请求参数）、F-44（异常路径的
      瑕疵通道）、F-37、F-18 仍等裁决，本步不代拍板。
    - **共享树隔离**：本步提交 7 个代码/测试/文档文件（`tstdx/cli/runtime_commands.py`、
      `tstdx/cli/parser.py`、`tstdx/streaming/__init__.py`、
      `tests/architecture/test_cli_connection_contract.py`、`tests/unit/test_cli_semantics.py`、
      `docs/api/interfaces.md`、`README.md`）加 `CHANGELOG.md` 与本文件的 §0.3 F-56 行、§1 第 31 步。
      测量树取 `60f6be9` + 本步文件：F-52 那一步（`60f6be9`）与本步改动的文件集合除两处账本外不相交，
      逐文件校验后拷贝，未把他人在途的 `docs/configuration.md`、`uv.lock` 写进本步。
    - **复测（同一轮日志；孤立 worktree = `60f6be9` + 本步 9 文件）**：离线全量 junit
      **3398 / 0 failures / 0 errors / 7 skipped**、`SUITE_RC=0`、167.3s、`--cov=tstdx`
      **80.70%**（阈值 77 未下调）；对账 `60f6be9` 的 3396 ＋ 本步 2 条新用例 = 3398。同树 13 道
      门禁全部 RC=0（含 `ruff format --check` 432 文件、reachability 189/172/17、`contract_audit
      --ci` 63 契约 · 155 capability、`spec_audit` 100.0%、docs links 82 文件、mypy 0 error）。
      变异 CONTROL/RESTORED RC=0，M1/M2/M3 各自 RC=1。数字取自只差本条文字的那棵树，写入后在同一
      提交树重跑 `tests/architecture` 与 docs links，两道 RC=0。
32. ✅ **出处词表里那两种不可能出现的出处：`ProvenanceKind` 的幻影成员与三条没人按它行动的判定属性（2026-09-19，清偿 §0.3 F-57，按 (b) 物理删除；编号接续执行序列）**：第 30 步把 `Provenance` 的字段逐个量完后，`kind` 那一格把问题往下推了一层——字段都有人读，但读到的值只可能来自一个成员。
    - **编号竞争如实登记**：本步代码先按 `60f6be9` 的基线写好并跑过一轮测量，期间并发会话把 F-56（CLI `stream` 收进 `Client`）作为**第 31 步**提交为 `0d7fbe1`，于是本步序号让到 **32**，并在 `0d7fbe1` 之上**整轮重测**（变异也重跑）。第一轮那批数字（`60f6be9` 基线 3396、本步树 3398）不写进任何账本，也不与本步提交树对应——它属于它所声称的那棵更早的树（F-53 口径）。
    - **实测的三重沉默（与第 30 步同形）**：① `tstdx/` 189 个模块里 `kind` 的唯一赋值是 `Provenance.direct()` 写死的 `ProvenanceKind.DIRECT`，`REPLAY`/`SYNTHETIC` 零构造点；② 这两条成员的全部引用就是它们自己的判定属性 `replay`/`synthetic`（`tstdx/result.py:95,99`），即"造不出却教你判断"；③ 三条属性对 `tstdx/` 生产代码读取点 **0**，全仓唯一读取是 `tests/runtime/test_query_contracts.py:195` 的一条断言——第 29 步为 `StreamPlan` 登记过的"抄本断言"同款现场。`tstdx/tools/golden_audit.py` 里满屏的 `ORIGIN_SYNTHETIC`/`.real`/`.synthetic` 是该工具自有 `Coverage` 类的同名属性（第 28 步"普查不成立"边界的又一现场），逐处人工核对后排除。
    - **删而不接（本行的 (b) 路径）**：(a) 若要"用上" `REPLAY`，就得让公开结果面生产一种真实 Provider 读取永远给不出的出处；本仓的回放数据停在解码层断言字节，从不进入运行期 `QueryResult`，不取。(c) 保留成员挂"将来用"注释是本家族逐次删除的形状。落地：枚举只留 `DIRECT`，`real`/`replay`/`synthetic` 三条属性全删，出处由 `kind` 字段自己说明；`test_query_contracts.py` 那条断言随属性一起删除，规则本身仍由 `assert direct.kind is ProvenanceKind.DIRECT` 加退役词汇（`cached`/`cache_hit`/`direct_fetch`）循环钉住。`Provenance` 类体净减 11 条语句（全量 `TOTAL` 语句数 基线 22566 → 本步 22555，净减 11）。
    - **wire 口径如实收窄**：`integration/serialization.py:41` 发射 `provenance.kind.value`，所以删除成员等于让 `kind` 在 HTTP/MCP 上只能取 `"direct"`——这与 v17"零缓存、直连"的对外承诺一致，不是新增限制：此前 `replay`/`synthetic` 也从来没有出现在任何真实响应里。`cache_tier` 仍在 wire 上恒为 `null`（第 30 步口径）。
    - **尺子补出成员维度，顺带收掉第三份抄件**：`tests/support/field_readers.py` 增 `members_referenced(owner, *, skip="")`——扫 `tstdx/` 找 `<owner>.<UPPER>` 具名引用，返回 `(扫过的模块数, 命中的成员名)`。本步用它做生产者普查；同时把 `tests/architecture/test_caveat_channel_gates.py` 里那条 `WarningCode` 发射点判据自带的私有 AST 遍历改成调用它（那是同一把尺子的第三份实现，"每量一次就抄一份"正是这族要避免的东西）。owner 名单仍由调用方逐类手工核对，公共实现不内置默认值。
    - **两条新门禁**：`test_every_declared_provenance_kind_has_a_producer` 的分母取 `{item.name for item in ProvenanceKind}`（F-43：清单会过期，枚举不会），分子取扫描结果，双向差集——`declared - produced` 非空红（幻影成员），`produced - declared` 非空红（引用不存在的成员），另加 `declared == {"DIRECT"}` 这条形状锁；`test_provenance_exposes_no_judgement_property` 要求 `vars(Provenance)` 里 property 恒空，判定翻译层回长即红。防盲三条照旧：`scanned > 30`、引用集合非空、声明集合非空。
    - **变异验证（4 项，跑在 `0d7fbe1` + 本步 5 文件的孤立 worktree，逐项复原并逐字节比对主树哈希）**：**M1** 把 `REPLAY = "replay"` 装回枚举 → 红，点名 `['REPLAY']`；**M2** 让 `real` 属性回长 → `test_provenance_exposes_no_judgement_property` 红；**M3** 让尺子失明（owner 名换成不可能存在的 `NoSuchKindEnum`）→ 红，且报的是"扫描自身失效"自检而不是"零幻影"；**M4** 生产者躲开扫描——`Provenance.direct()` 改成按值构造 `ProvenanceKind("direct")` → 红，`DIRECT` 也落入"声明了却没有具名生产点"。BASELINE 与逐项还原后均 RC=0，`MUT_RC 0`。M4 的边界如实登记：**按值构造本身不被禁止**，禁止的是"没有任何具名引用还自称该成员可生产"——真要引入新出处，得写得出 `ProvenanceKind.NEW` 的构造点。
    - **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `0d7fbe1` + 本步 5 个代码文件，与本提交树逐文件同内容）**：基线取干净 `0d7fbe1` 同轮实测 junit **3398 tests / 0 failures / 0 errors / 5 skipped**、139.7s、`--cov=tstdx` **80.77%**；本步树 junit **3400 tests / 0 failures / 0 errors / 5 skipped**、143.2s、`FINAL_RC=0`、`--cov=tstdx` **80.77%**，对账 `0d7fbe1` 的 3398 ＋ 本步 2 条新门禁 = 3400，阈值 77 未下调。同树 9 道主门禁全部 rc=0（`GATES_RC=0`）：：originality（Total 190 / Original 190 / Suspicious 0 / External imports 17）、`spec_audit --strict`（44 spec、coverage 100.0%）、golden 审计（530 样本：real 60 / synthetic 470 / unknown 0）、reachability（189 模块 / 172 可达 / 17 白名单豁免）、`contract_audit --ci`（63 Typed Query 契约 · 155 capability）、docs links（82 文件）、`ruff format --check`（462 files already formatted）、mypy（RC=0，无输出）、`ruff check`（All checks passed!）。
    - **口径边界**：上表的门禁与全量数字取自**只含本步 5 个代码文件**的那棵树；两处账本 .md 写入后，在**与提交树逐文件相同**的树上重跑，读作 **3400 tests / 0 failures / 0 errors / 5 skipped**（141.4s）、`--cov=tstdx` **80.77%**、`full rc=0`、docs links `rc=0`——本节数字因此属于它所声称的那棵树（F-53 口径）；写进这一句本身只动 .md，不再另起全量轮，只在同树复跑 docs links（`rc=0`）。另注：第 31 步那轮在同一内容的树上记 7 条 skipped，本轮两侧都是 5 条（全部落在 parquet/duckdb 缺依赖的用例上）——条件跳过数随环境而变，故本步对账只用同轮两侧的数，不跨轮拼。
    - **共享树隔离**：本步只提交 `tstdx/result.py`、`tests/support/field_readers.py`、`tests/architecture/test_result_shape_gates.py`、`tests/architecture/test_caveat_channel_gates.py`、`tests/runtime/test_query_contracts.py` 五个代码文件，加本文件的 §0.3 F-57 一行与 §1 第 32 步一条、`CHANGELOG.md` 一处。并发会话的第 31 步全部产物（`0d7fbe1`：`tstdx/cli`、`tstdx/streaming`、`tests/architecture/test_cli_connection_contract.py`、`tests/unit/test_cli_semantics.py`、`docs/api/interfaces.md`、`README.md`，以及 F-56 行的改判与 §1 第 31 条、CHANGELOG 第 31 步块）逐字节未动；`docs/configuration.md` 的 autocrlf stat 噪声与 `uv.lock` 未纳入本步提交。


33. ✅ **方案文档的"现状判定"停在 Phase 3 之前的时态（2026-09-19，清偿 §0.3 F-58；本步不动
    生产代码）**：为回答"核心功能是否全部实现、主体链路是否贯通"而复读本文档时实测——§0.1 的两行
    ❌ 与 §0.2 的八行"处理对象"都还是三、四个 Phase 之前的口径，其中引用的四个模块路径
    （`runtime/gateway.py`、`executor_registry.py`、`provider/router.py`、
    `executor_bindings.py`）与那个根级 `client_api` 都已从磁盘消失。
    - **编号竞争如实登记**：本步的 §0.1/§0.2 重写与新门禁先按 `0d7fbe1`（第 31 步）基线写好
      并测过一轮，期间并发会话把 F-57（`ProvenanceKind` 的幻影成员）作为**第 32 步**提交为
      `85cc43e`，于是本步序号让到 **33**，并在 `85cc43e` 之上整轮重测（三条变异也重跑）。第一轮
      那批数字不写进任何账本——它属于它所声称的那棵更早的树（F-53 口径）。
    - **这是 F-23/F-34/F-35 同族的第四处**：文档门禁只校验反引号里的 `tstdx.x.y` 点号路径与
      README 数字，**带斜杠的文件路径与表格的时态不在射程内**；被当作事实源的文档自己失真时，
      所有下游读者（包括本轮提问的人）都会照字面接受。
    - **改法**：§0.1 重写为五行现在时（内核主链 / 四个服务面 / 流式面 / 配置面 / 已删除的旧
      接缝），两行 ❌ 改判"已整层物理删除，因此不再可能是断链"并指向防回潮守卫；§0.2 增列
      「现状」逐行补裁决与日期。历史陈述与证据原文保留——本轮只回答"这条还开着吗"；§0.2 标题补为
      「Phase 3–5 处理对象；第 33 步起逐行现状见最后一列」，让只看标题的人也读得到判决。
    - **新门禁 `tests/architecture/test_plan_status_gates.py`**：路径存在性（§0.1）、裁决＋日期
      （§0.2）、两节标题仍在（防解析空转）、以及"§0.1 ≥4 行且 ≥5 条路径 / §0.2 ≥6 行"的防盲
      断言。防盲那半不是装饰：M3 改名后三条判据全部报"门禁自身失效"而不是绿。
    - **本步先红在自己写的判据上两次**：① 裁决正则用子串匹配，F-5 行里"不再依赖**已删除**的信封
      线"这句历史叙述替当前裁决打了勾，M2 逃逸（RC=0）→ 收紧为必须以 `**裁决**` 开头；② 补
      裁决时少写一个 `|`，八行的判决并进了「证据」格、列数与五列表头不符 → 修回并把列数写进
      断言。两条都在变异复跑里才现形，是"新门禁第一个被测出的是它自己"这条重复教训的第六次。
    - **复测（同一轮日志；孤立 worktree = `85cc43e` + 本步 3 文件）**：离线全量 junit
      **3404 / 0 failures / 0 errors / 7 skipped**、`SUITE_RC=0`、143.3s、`--cov=tstdx` **80.69%**
      （阈值 77 未下调；本步零改生产代码，构成与第 32 步的 80.77% 同量级）；对账其在提交树上的
      3400 ＋ 本步 4 条新门禁 = 3404。7 条 skipped 全部落在 `tests/output/test_sinks_dispatch.py`
      的 parquet/duckdb 缺依赖用例。同树 13 道门禁全部 RC=0（`ruff format --check` 433 文件、
      reachability 189/172/17、`contract_audit --ci` 63 契约 · 155 capability · 92 PENDING、
      `spec_audit` 100.0%、docs links 82 文件、mypy 0 error、originality Total 190 / Suspicious 0）。
      变异 CONTROL/RESTORED RC=0，M1/M2/M3 各自 RC=1 且逐条指名。数字取自只差本条文字与三处散文
      订正的那棵树，写入后在同一提交树重跑 `tests/architecture` 与 docs links，两道 RC=0。

34. ✅ **握手帧 3 的产品标识块：一处未验证断言决定了默认出站字节，K 线整族因此恒 0 根（2026-09-19，清偿 §0.3
    F-59，并给出 F-37 K 线那一半的归因）**：本步是 F-37 取证的正题——周六上午按第 16 步留下的待办重跑实时冒烟，
    结论却是"主站没问题、请求体没问题、**握手有问题**"。
    - **取证方法**（一次性脚本，三条对真实主站只读、两条离线复算，均不改代码；日志 `s34_confirm.log` /
      `s34_probe_frames.log` / `s34_probe_blob.log`，离线的两条见本条内引注）：① 逐位 A/B——同主机、同一条新建连接、同一个逐字节照抄
      golden 的 `0x052D` 请求体，只换握手帧 3 的 30 字节：`d5d0c9cc…00000002`（自采集样本，含 GBK 券商名）⇒
      `payload=2B rows=0 warns=1`，30 个零字节 ⇒ `payload=180B rows=10 warns=0`，且
      `first_dt='2026-09-07 15:00' close=9.23`。live 日志只记告警**条数**，那一条的原文由离线复算给出：
      把观测到的 16 字节帧头 + 载荷 `2003` 喂 `parse_response_header` → `dispatch(category=4)`，得
      `method=0x052d ok=True tier=L1 name=SECURITY_BARS rows=0` 与
      `count 失真已钳制：声明 800 条，按剩余字节 16B/条 只能容纳 0 条`（脚本 `s34_declared.py`，
      日志 `s34_declared.log`）。7 台可达主机各 2 轮全同向，
      golden 采集主机 218.75.126.9 再交替 3 轮（累计 5 次），**7/7 无一例外**；第 8 台 119.147.212.81 两轮均连接超时。
      ② 内容形状矩阵——`A`*30 / `0xFF`*30 与全零同样拿回 180B/10 根（3/3 主机），只有那个样本块回空桩；把全零块尾
      4 字节换成样本块的 `00000002` 则 3/3 主机握手中途 `ConnectionClosed`。③ 缺帧分布——只发帧 1+2 时 7/7 主机回数据，
      一帧不发时 6/7 读超时（60.191.117.167 例外）。
    - **本步推翻了本仓两处自己写过的"✅ 实测"**：其一"服务端不校验帧 3 内容，只校验长度"（它的证据是一个从未存在
      过的测试文件与一个从未存在过的 golden 目录，见 §0.3 F-59 ①），其二"三帧都得发"（帧 3 实际不是取数的必要条件，
      此前从未被测）。**这两句都曾写在同一份 docstring 里，而全仓对握手字节零测试**——所以纠正它们的方式不是再写
      一句更强的话，而是补上线路形状断言。
    - **修复（默认值 + 删除 + 文档 + 门禁四件）**：`_PRODUCT_ID_BLOB = b"\x00" * 30`；`OPAQUE_BLOB_BYTES` /
      `opaque_blob()` / `build_setup_frame3()` 三个零调用点符号物理删除（HEAD `git grep` 除自身文件外命中 0）；
      模块 docstring 与 `PROTOCOL_SPEC/7709/0x000D_HANDSHAKE.yaml` 两处假声明改写为本步可复算口径；新增
      `tests/protocol/test_handshake_frames.py` 9 项。**刻意不做**的三件：不改"不发帧 3"（那是新增一项对外协议声称，
      且今天的三帧形状与真实客户端同形）、不猜服务端为何回空桩（机制未知，只登记行为）、不动 0x000F/0x0010 的解析器
      （第 34 步实测证明其错位与握手无关，属另一格，需用户裁决）。
    - **端到端复验（无 monkeypatch，走唯一业务入口）**：`Client().bars(…, strict=True)` × {sh600000, sz000001,
      sz300750} × 8 个周期名 = **24/24 各 10 根**、`kind=direct`、`tier=None`、`warns=0`；`count=320` ⇒ 320 根；
      `BAD=0`、`CONFIRM_RC=0`。同一轮另以 ④ 号脚本复核 0x000F / 0x0010（`s34_probe_finance.log`，
      `FIN_RC=0`）：新默认握手下两条仍解出错位字段，故 F-37 只闭合了 K 线那一半。
    - **顺带登记的一条反向缺陷（F-60，本步不静默修）**：第 21 步给空首页立的告警写着"服务端声明 0 条记录"，而本轮
      实测的那个 2 字节空桩声明的是 **800 条**（`2003` 小端 = `0x0320`）。把该桩原样注入传输层、其余一律走生产链路
      （`TdxClient(pool=fake).bars(...)`，解码器/分页模板/告警通道都是真代码，脚本 `s34_stubpair.py`）实测得到 **2 条互相否证的告警**：
      `decode_caveat` 报"count 失真已钳制：声明 800 条，按剩余字节 16B/条 只能容纳 0 条"，`bars_empty_first_page`
      报"服务端声明 0 条记录"。⇒ 第 26 步 F-51 的接线是好的，坏的是那句写死的数字（`_mixin.py:254-258`，两处注释
      `:226-227`、`:252-253` 同错）；而 `tests/client/test_v5_pagination.py` 用 `_bars_payload(0)` 造了一个"真声明
      0"的假桩，所以这一对在 3413 项测试里永远不出现。修复形状与判据改造见 §0.3 F-60，本步不动代码。
    - **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `1be93ea` + 本步 5 个文件）**：基线取干净 `1be93ea`
      同轮实测 junit **3404 tests / 0 failures / 0 errors / 5 skipped**、142.1s、`--cov=tstdx` **80.77%**；本步树
      junit **3413 tests / 0 failures / 0 errors / 5 skipped**、135.3s、`--cov=tstdx` **80.82%**、
      `SUITE_EXIT=0`（`junit_s34_final3.xml` / `full_s34_final3.log`，与提交树逐文件 cmp 相同），
      对账 `3404 + 本步 9 条线路形状守卫 = 3413`，阈值 77 未下调。同树 9 道主门禁全部 rc=0（`GATES_RC=0`，`gates_s34b.log`）：originality（Total 190 / Original 190 / Suspicious 0 / External imports 17）、`spec_audit --strict`（coverage 100.0%）、golden 审计（`[GATE] all L1 verified commands have real samples (OK)`、`0x052D real category coverage: [0…11]`）、reachability（189 模块 / 172 可达 / 17 白名单豁免，无未登记孤儿）、`contract_audit --ci`（63 Typed Query 契约 · 155 capability）、docs links（82 文件）、mypy（rc=0，无输出）、`ruff check`（All checks passed!）、`ruff format --check`（464 files already formatted）；`tests/architecture` 9 个文件共 200 项 rc=0。

35. ✅ **把跑过的冒烟写成"尚未执行"：现网口径与 §1/§4 记录对齐（2026-09-19，清偿 §0.3
    F-61/F-62；本步不动生产代码）**：第 34 步落地后为回答"主体链路是否全部贯通"再读 §0.1，
    撞上 F-58 的镜像病——那张表现在把**做过**的事写成没做。

    - **四处现在时面同错**：§0.1 结论第一条边界写"真机冒烟尚未执行……需用户授权"；本文 Phase 5
      计划项 3 仍挂"⚠️ 仍未执行"；README 路线图行写"仍待：…… + 真实网络 smoke + tag"；README
      「下一阶段」把已经跑通的 wheel 安装冒烟与三面 live 各一发列为计划。而 §1 第 16 步逐格记着
      七格真实网络/服务面冒烟 6 PASS / 1 FAIL 与 `SMOKE_RC=0`，§4 验收清单该格也已勾 `[x]`。
      本轮向用户复述现状时确实按"从未在现网验证过"读过一次，并把 F-37 读成"冒烟还没跑"——错的
      是文档，代价不是保守而是失真。
    - **与 F-58 的分工**：第 33 步的判据核对"§0.1 引用的文件路径存在"与"§0.2 每行有裁决"，
      边界子句里的**动作声称**（跑没跑、接没接、验没验）不在射程内。本步不为此再造一条专用扫描
      （动作声称的形态太自由，写不出不像自证的判据），改立一条**结构性**判据：§0.1 结论里每个
      带圈编号的边界子句必须点一个 §0.3 真实存在的 F 号，且其中至少一个仍是开放裁决。凭印象的
      说法从此必须挂账，账还必须是活账。
    - **新判据 `test_open_boundaries_cite_an_open_finding`** 的三种失败各有消息：一个 F 号都不点
      →`没有 F 号：…`；点的号在 §0.3 里不存在 →`§0.3 里没有这些行：[…]`；只点已清偿的账 →
      `只点到已清偿的账：[…]`。分母是 §0.3 的账本行本身（本步在提交基线上实测 **52 行**，开放裁决
      **7 条**：F-18/F-20/F-37/F-38/F-44/F-47/F-63），防盲自检沿用两条（结论段解析不出编号子句即判
      门禁自身失效）。
    - **判据上线即红在自己身上（本族第四次、第五次）**：第四次——新加的"裁决格必须读得出来"自检
      当场报出 `['F-43', 'F-46']`，表格切格按 `|` 裸切，而账本里行内竖线有两种真实形态：转义的
      `tests/…\|scripts/…`（F-24）与反引号里的正则字面量 `` `a|b` ``（F-43、F-46）；被劈成碎片的
      裁决格没有任何裁决词，于是这三行在原判据下**永远读成"已清偿"**——一条挂着的账会这样从分母上
      安静消失。改法是 `_split_row()`：反引号内与转义后的 `|` 不作分隔符。第五次——把树让到并发会话
      的 `b5a54f0` 之后重跑，同一条自检立刻报出 `['F-63']`：那一行的裁决写作 `**本步只登记，不改**`，
      而我上一轮刚把"本轮只登记"当成开放词收进词表，**同义词差一个字就当没看见**。词表补入
      "本步只登记"，docstring 同步列全八词。
    - **F-62 是本步自己撞上的第六次，而且这一次改错的是我**：本步原把"账本里 12 处复测行写
      `Windows+py3.13`"判为假标签并批量订正成 `py3.12`，依据只有 `.venv/pyvenv.cfg` 与本机覆盖率头。
      并发会话的第 36 步自陈其整轮测量用的是**另一个解释器**（`python -V` → 3.13.x，非仓内 `.venv`），
      且同一棵 `f60c3b5` 基线在他们那边 junit 5 skipped / 80.82%、在仓内 `.venv` 这边 7 skipped / 80.75%
      ——两套数字都是真的，只是来自两个环境。我那 12 处改写因此把**别人正确的实测**涂成了假的，
      性质与 F-59"证据指向不存在的文件"同类，只是方向是反的。已全部撤回（12 处恢复原文），并立
      下本步起的新口径：**复测行必须写测量者实际用的解释器与所在环境**，不替别人的历史记录改标签。
      详见 §0.3 F-62。
    - **编号竞争第三次踩同一坑，这次撞在提交那一刻**：本步原记"第 34 步 / F-59"，测量期间被握手
      那轮（`f60c3b5`）占号，遂改判第 35 步 / F-61 并在 `f60c3b5` 上全量重测；落盘提交时 CAS 又撞上
      并发会话的第 36 步（`b5a54f0`）刚刚合入，`update-ref` 按预期拒绝，于是整步再让基线、在新提交上
      三方合并并重测第二轮。两次让号都没动别人的行，第 36 步也明确"跳过 F-61/F-62 用 F-63，不复述
      也不改它们的行"。
    - **复测（本机 Windows+py3.12，即仓内 `.venv`；同一轮日志；孤立 worktree = `b5a54f0` + 本步 4 个
      文件）**：基线取干净 `b5a54f0`（同一轮紧接本步之前实测，`wt_f62_base`）junit **3415 tests /
      0 failures / 0 errors / 7 skipped**、`--cov=tstdx` **80.76%**（22559 语句 / miss 3745 / partial
      1019）；本步树 junit **3416 / 0 / 0 / 7**、`--cov=tstdx` **80.77%**（语句分母 22559 未变、miss
      3745→3743）、`SUITE_RC=0`。对账 `3415 + 本步 1 条边界账判据 = 3416`，`tests/architecture` 201 项
      rc=0（干净基线上为 200 项），阈值 77 未下调。同树 13 道门禁全部 rc=0（`f62g_chain.log`）：
      `ruff check`（All checks passed!）、`ruff format --check`、mypy（rc=0，无输出）、originality
      （Total 190 / Original 190 / Suspicious 0 / External imports 17）、reachability（189 模块 / 172
      可达 / 17 白名单豁免，无未登记孤儿）、`contract_audit --ci`、`spec_audit --strict`、golden 审计
      （`[GATE] all L1 verified commands have real samples (OK)`）、adversarial、bridges、
      streaming+runtime、benchmark smoke、docs links（各条读数见当轮 `f62g_*.log`）。变异 6 条全部
      RC=1 且各自指名（CONTROL 与逐项还原 RC=0，`f62g_mut.log` / `f62g_mutdetail.log`）。**落盘前后两轮
      （runA/runB）逐格相同**，本节数字取落盘后的那一轮。
    - **同一台机器上的两个解释器，两套都是真数字**：本轮在仓内 `.venv`（cpython-3.12.13）测得基线
      7 skipped（4 项缺 pyarrow、3 项缺 duckdb）/ 80.76%，并发会话第 36 步在同一棵 `f60c3b5` 上用自己的
      3.13.x 解释器记 5 skipped / 80.82%。两条记录都保留原文，本步不改写他人记录，只把基线取成自己
      这一轮的数字，并按 F-62 的新规把环境写进复测行。

36. ✅ **空首页告警按服务端当次声明数分家：清偿 F-60（2026-09-19）**
    - **本步量的是第 34 步自己留下的那句话**：F-59 把握手产品标识块修好后，同一条链路上留下了自相矛盾的
      一对告警——线路实测的 `0x052D` 空桩声明 800 条（`2003` 小端 = `0x0320`），解码侧照实报
      「count 失真已钳制：声明 800 条」，而 `BARS_EMPTY_FIRST_PAGE` 却说「服务端声明 0 条记录」。判据方向
      没错，数字是写死的：`_mixin.py:254-258` 那句字符串把「声明 0」与「声明 N 却回 0 个记录字节」这两件
      处置完全不同的事压成一件，而区分它们恰是这条告警存在的唯一理由。
    - **改法三件**：① `registry.py:253-256` 把 `state["declared_count"]` 随 `ParseResult.meta` 暴露
      （内部形状，不动对外契约），`_mixin.py:199`/`:228-232` 分页侧接住它；② `:258-266` 文案按声明数三分支
      （N>0 空桩 / 0 无此周期历史 / 读不到头＝声明数未知），strict 的 `context` 加 `"declared"` 键
      （`:274-279`），两处旧注释（原 `:226-227`、`:252-253`）、`diagnostics.py:56-57` 与 `docs/errors.md:25-26`
      同批改口径；③ 空桩 fake 从「合成的真声明 0」换成线路实测的 `2003`（新 `_StubPayloadPool`），
      判据相应从「恰 1 条告警」改为「2 条且两条都含 800、第二条不得出现『声明 0 条』」，真声明 0 那条
      补文案断言——原 fake 与被修代码犯了同一个错，这是它能带着 3413 项全绿活过 F-60 的原因。
    - **变异证据（`mutate_s36.log`，四发各自 rc=1）**：M1 退回写死 0 的单句 → 只红
      `test_real_stub_warns_with_the_declared_count`（新判据确实咬住文案，不是靠条数）；M2 撤掉 `registry.py`
      那处暴露（4 行） → 红三条（含既有 `test_empty_first_page_warns`：证明「声明 0」这句现在也是从线路上读的，
      不再是常量）；M3 把「声明为 0」那一支改成永不成立 → 只红 `test_empty_first_page_warns`（两支真的分家）；
      M4 从 strict `context` 删 `"declared"` → 只红 `test_real_stub_strict_context_carries_the_declared_count`。
    - **顺带登记不静默修（F-63）**：查 `DECODE_CAVEAT` 发射点时量到两件事——`_mixin.py` 的 15 个 dispatch
      点里只有 bars 那处读 `result.warnings`，其余 14 处只取 `result.rows`，把解码层判断整族丢弃（第 26 步
      F-51 的「接线」只覆盖了一条命令）；`SECURITY_LIST_EMPTY_FIRST_PAGE` 的文案与 F-60 同法写死「声明 0 条」，
      而 0x044D 登记为 `STATUS_OFFLINE`、`_guard_offline` 在 `_req` 里 fail-fast，实测走 `TdxClient` 直接
      `CommandOffline`，故它只在把 `security_list` 整个 monkeypatch 掉的测试里才发射。两条都写进 §0.3 F-63，
      本步不动代码（②需用户裁决）。
    - **编号竞争**：并发会话在 `wt_f61` 里以「第 35 步」写着 §0.1 边界口径（F-61）与解释器标签（F-62）的
      账本清偿，测量时仍未提交。本步按第 33 步同法让号，取 **36**，并跳过 F-61/F-62 用 F-63，不改它们的行。
    - **复测（本机 Windows+py3.13.14，同一轮日志；孤立 worktree = `f60c3b5` + 本步全部 7 个文件，与提交树
      逐文件 `cmp` 相同）**：
      基线取干净 `f60c3b5` 同轮实测 junit **3413 tests / 0 failures / 0 errors / 5 skipped**、143.3s、
      `--cov=tstdx` **80.82%**（与第 34 步记的同一棵树逐格相同）；本步树 junit **3415 tests / 0 failures /
      0 errors / 5 skipped**、135.9s、`--cov=tstdx` **80.83%**（`junit_s36_final.xml` / `full_s36_final.log`；
      账本落盘前的 `runA`/`runB` 两轮逐格相同），
      对账 `3413 + 本步 2 条空桩守卫 = 3415`，阈值 77 未下调。同树 9 道主门禁全部 rc=0（`gates_s36_final.log`）：
      originality（Total 190 / Original 190 / Suspicious 0 / External imports 17）、`spec_audit --strict`
      （coverage 100.0%）、golden 审计（`[GATE] all L1 verified commands have real samples (OK)`、
      `0x052D real category coverage: [0…11]`）、reachability（189 模块 / 172 可达 / 17 白名单豁免，
      无未登记孤儿）、`contract_audit --ci`（63 Typed Query 契约 · 155 capability）、docs links（82 文件）、
      mypy（rc=0，无输出）、`ruff check`（All checks passed!）、`ruff format --check`（464 files already
      formatted）；`tests/architecture` 200 项 rc=0。解释器版本按本步实测写：`python -V` → 3.13.14
      （`.workbuddy/binaries/python/versions/3.13.12`），非仓内 `.venv` 的 3.12.13。
      该轮之后只剩两处纯文字订正（`一个自相矛盾`→`自相矛盾的一对`、`那三行`→`那处暴露（4 行）`），
      落盘后在同一提交树上复验 docs links（82 文件）与 `tests/architecture` 200 项均 rc=0，
      其余 5 个代码/测试文件与该轮逐字节 `cmp` 相同。

37. ✅ **解码层的判断只有 bars 一条命令能上 wire：清偿 F-63①（2026-09-19）**
    - **本步量的是第 26 步 F-51 那句"已经接线"**：`_mixin.py` 有 15 处 `_client_pkg.dispatch(...)`
      （本步最终树 `:256`、`:348`、`:370`、`:383`、`:394`、`:412`、`:450`、`:514`、`:658`、`:699`、
      `:760`、`:770`、`:804`、`:816`、`:858`），而全仓只有 bars 分页那一处读 `result.warnings`。解码层
      的产出侧一点没缩水：`guarded_count` 在 8 个解析器模块里有 43 个调用点会写「count 失真已钳制」，
      §2-17 写「记录截断：声明 N 条，实收 M 条」，`dispatch` 写「L1 解析失败 → 降级」与「L2 置信度不足
      → 回落 L3 原始透传」。这些判断进了袋，袋没人读，wire 上就是静默——用户拿到一份被截断、被降级、
      字段映射可能已经错位的行，且没有任何一条告警告诉他。
    - **改法是一个口，不是补十四处**：新增 `_forward_decode_caveats(result, label)`（`:104-118`）逐条
      `record_warning(WarningCode.DECODE_CAVEAT, …)` 后原样返回 `ParseResult`，15 个分派点全部经过它。
      bars 的原 inline 循环删除、文案逐字未动（`bars({symbol!r}) 分页解码：…`），故第 36 步那条
      「两条告警都含 800」的空桩守卫继续成立，且现在它与转发口本体互为证据。真正放大覆盖面的是通用口
      `_t_request_result`（`:450`）：`security_list` 之外的 `trade_today` / `block_*` / `goods_*` /
      `ex_*` / F10 目录等十几个公共方法共用这一个分派口，接一次十几条面同时看得见解码判断。
    - **归属行号是实测的，不是猜的**：`_caller_stacklevel()`（`:121-136`）沿当前调用栈找"离开 tstdx 的
      第一帧"。固定常量 `_TPL_WARN_STACKLEVEL = 4`（`:90`）只对单跳成立——模板之间用 `_op_call` 互调，
      `block_list` → `request` → `request_result` 实测三跳，常量形状把告警落在 `_mixin.py:169`（库里），
      等于把缺陷指给一个没做错的人。异步侧的诚实边界：归属帧落在 `asyncio` 里（不在 tstdx 栈内），本步
      对异步只钉「通道 + 标签」两条，不钉行号。
    - **判据（3 条结构门禁 + 5 条行为守卫）**：结构侧见 §0.3 F-63 裁决格所述三条。旧的 bars 专属门禁
      （断言 `_t_bars` 本体自己读 `.warnings` 并 `record_warning`）被这次重构合法打破，删掉换成通用判据
      ——不是放宽，是分母从 1 变 15。行为守卫 `tests/client/test_decode_caveat_wiring.py` 只挑**可达**
      形状：`0x120F` 走 `request` 通用口（断言整句文案逐字相等）、`0x000F` 走 `capital_changes`（同一条
      payload 同时产出「钳制」与「降级」两句，是 F-37 的取证口）、异步同命令一条、干净页静默一条、
      归属行号一条。0x0537 / 0x0FC5 / 0x0FB4 / goods / ex / mac 的 bars+quote 在本步实测为 fail-closed
      （`NotImplementedFeature` / `CommandOffline`），拿它们做守卫只会测到门、测不到接线。
    - **变异证据（`mutate_s37.log`，四发各自 rc=1，各红自己那条判据）**：M1 撤掉 `request` 口的转发
      （回到 F-63① 的原始形状）→ 红 4 条（分派点门禁 + 标签门禁 + `request` 口行为守卫 + 归属守卫）；
      M2 让转发口空转（只 `return result`）→ 红 7 条，其中 `test_every_declared_warning_code_has_an_emit_site`
      证明 `DECODE_CAVEAT` 又变回虚设类别，`test_real_stub_warns_with_the_declared_count` 证明 bars 现在
      确实与其余 14 点同口；M3 `stacklevel` 退回常量 → 只红归属那条，实测误报落点 `_mixin.py:169`；
      M4 把 `ex_bars` 的标签前缀抄成 `goods_bars` → 只红标签门禁（复制粘贴的归属错位有主）。
    - **自己的代码先被自己的守卫抓了一次**：`_caller_stacklevel()` 初版把循环写成"先移动到 `f_back` 再
      计数"，计数因此多出一帧，`block_list` 的告警落到 `_pytest/python.py:167`；33 项里只有归属那条红。
      同时首轮的 `mypy` 报 `_mixin.py:135` 的 `FrameType | None` 赋值不兼容（rc=1），改为让变量类型稳定
      的循环形状而不是加标注。两处都不是放宽判据，是照判据修实现。
    - **复测（本机 Windows+py3.13.14，同一轮日志；孤立 worktree：基线 = 干净 `abef5e1`，本步树 =
      `abef5e1` + 本步 3 个文件，与提交树逐文件 `cmp` 相同）**：基线 junit **3416 tests / 0 failures /
      0 errors / 5 skipped**、142.1s、`--cov=tstdx` **80.83%**；本步树 junit **3423 tests / 0 failures /
      0 errors / 5 skipped**、135.6s、`--cov=tstdx` **80.85%**（`junit_s37step.xml` / `full_s37step.log`）。
      对账 `3416 + 5 条新守卫 + 3 条新门禁 − 1 条被替换的 bars 专属门禁 = 3423`，阈值 77 未下调。同轮
      9 道主门禁本步树与基线各全部 rc=0（`gates_s37step.log`）：originality（Total 190 / Original 190 /
      Suspicious 0 / External imports 17）、`spec_audit --strict`（coverage 100.0%）、golden 审计
      （`[GATE] all L1 verified commands have real samples (OK)`、`0x052D real category coverage: [0…11]`）、
      reachability（189 模块 / 172 可达 / 17 白名单豁免）、`contract_audit --ci`（63 Typed Query 契约 ·
      155 capability）、docs links（82 文件）、mypy（rc=0，无输出）、`ruff check`（All checks passed!）、
      `ruff format --check`（465 files already formatted）；`tests/architecture` 203 项 rc=0（基线同轮
      201 项 rc=0）。该轮之后只落下本节账本与 `CHANGELOG.md` 两处纯文字改动，提交树上复验
      docs links（82 文件）与 `tests/architecture` 203 项均 rc=0，3 个代码/测试文件与该测量树逐字节
      `cmp` 相同。工作树里那 1 项 `test_spec_coverage` 失败来自未跟踪的
      `PROTOCOL_SPEC/_sniffer/quotation/**/DRAFT.yaml`（本步不提交它们），孤立基线树同轮 0 失败 ⇒ 非代码
      回归。解释器版本按本步实测写：`python -V` → 3.13.14（`.workbuddy/binaries/python/versions/3.13.12`），
      非仓内 `.venv` 的 3.12.13。
    - **未做**：F-63②（`SECURITY_LIST_EMPTY_FIRST_PAGE` 的永不可达发射点与写死数字）等用户裁决 `security_list`
      面的去留后再动；本步没碰那条文案，也没碰 `_t_export_security_list`。

38. ✅ **命令账本四个没人读的字段，其中一个是全账本唯一的描述：清偿 F-64（2026-09-19）**
    - **本步量的是账本自身**：`Command` 登记 10 个字段，干净 `bb201b1` 上 AST 扫 `tstdx/` 189 个模块，
      `request_fields` 0 处、`aliases` 0 处、`spec_file` 1 处而那处属 `spec_audit.AuditResult`
      （`tstdx/tools/spec_audit.py:475`，另一个类的同名属性）——三个纯登记。第四个是 `summary`：
      85 条命令逐条写着中文描述（`summary` 为空 0 条），全包读它 0 处（3 处同名命中全属
      `tstdx/domain/records.py` 的 `NewsRecord`/`ResearchRecord`/`SearchRecord`）。但它与前三个不同判：
      账本 85 条只有 39 条在 `PROTOCOL_SPEC/` 有 YAML（该目录 44 个 yaml、42 个命令号），另外 46 条的
      语义说明**只活在 `summary` 这一行**，而客户端唯一把命令说给用户听的地方只报了名字——
      一个字段既能删（零读取）又不该删（唯一描述），说明缺的是接线不是删除。
    - **还有一条只剩「写」那半边的生成线**：`tstdx/tools/codegen.py:generate_command_entry` 从 YAML 的
      `request.fields` 推出 `request_fields=(...)` 写进 `_c(...)`，账本因此躺着 13 处该实参。删字段不删
      生成线，下一次真跑 `python -m tstdx.tools.codegen --write` 就是 TypeError，而 codegen 只在人想
      采新命令时才跑——这类"只有另一半会坏"的裂缝，测试全绿也照不出来。
    - **改法三件**：① `Command` 10 → 7 字段，13 处实参与生成器 7 行推导同删；② `summary` 接线：
      `_guard_offline` 两条文案由「0x07E5（BLOCK_QUOTES）」变为「0x07E5（BLOCK_QUOTES：板块行情
      （2026-09 三主站实测无响应，client 方法保留待参数校正））」，`CommandOffline` 与
      `NotImplementedFeature` 的 `context` 各加 `"summary"` 键；③ 判据四条 + 一条分母自曝
      （`tests/unit/test_commands.py::TestLedgerFieldShape`、
      `tests/client/test_offline_failfast.py::TestBlockedCommandsSpeakTheirOwnLedgerLine`、
      `tests/unit/test_tools_semantics.py::TestCodegenLedgerShape`）。
    - **判据上线即红在「没红」上（本族第六次，第一次由变异台当场揭穿）**：M1 把 `summary` 从文案里摘掉、
      `context` 留着，第一轮 **RC=0**。原因是 `TdxError.__str__`（`tstdx/errors.py:135-140`）把 `context`
      的前六个键拼进字符串，于是 `summary in str(exc)` 被机读侧单独满足——"人读的那句话"其实从没被断言过。
      判据改读 `ei.value.message` 后 M1 红 10 项；反向的 M6（只摘 `context["summary"]`）红 8 项。
      前五红都是"上线即红"，这一个是"上线即绿而其实是假的"，比红更难防：它只在有人去变异时才现形。
    - **变异 6 发各自 rc=1**（`s38b_mut.log`）：M1 摘文案 → 红 10；M2 往 `Command` 塞回 `request_fields` →
      红形状锁 + codegen 往返；M3 清空 `0x07E5` 的描述 → 红 85 条普查 + 它自己的到达判据；M4 生成器重写
      已删字段 → 红往返 + 反回潜；M5 豁免集吞掉全部 9 条下线命令 → 红分母自曝 + 既有 fail-fast 用例；
      M6 摘 `context` 机读键 → 红 8。六发恢复后工作树只剩本步六个文件。
    - **顺带**：同文件那条测试的名字在说谎——`test_exactly_seven_offline_commands` 断言的是 9（账本自
      2026-09-06 起下线 9 条），改名 `test_exactly_nine_offline_commands`，数字与名字同真。
    - **登记不静默修（F-65）**：同一把尺子量账本函数侧，`stats()`/`get_command_by_name()`/
      `unknown_command_ids()`/`by_family()` 零生产调用点，其中两个挂在 `tstdx/protocol/__init__.py` 的
      `__all__` 上，而 `docs/archive/OPTIMIZATION_PLAN.md:32` 把 `unknown_command_ids` 说成"保留为别名"
      （被别名掉的 `unknown_commands()` 早已不在模块里）。删公开查询函数是对外契约收窄，与 F-44/F-47
      同族，本步不动，等裁决（三条路径已写进 §0.3 该行）。
    - **复测（本机 Windows+py3.12，仓内 `.venv` 为 cpython-3.12.13；孤立 worktree `wt_s38b` = 干净
      `bb201b1` + 本步 6 个文件，与测量树逐文件 `cmp` 相同）**：基线（同轮 `wt_s38bbase`）junit
      **3423 tests / 0 failures / 0 errors / 7 skipped**、`--cov=tstdx` **80.79%**；本步树 junit
      **3438 / 0 / 0 / 7**、`--cov=tstdx` **80.84%**、`SUITE_RC=0`。对账
      `3423 + 2 形状/普查 + 11 到达判据（8 offline + 2 block + 1 分母）+ 2 生成器往返 = 3438`，
      阈值 77 未下调。同轮链上 15 项全部 rc=0（`s38b_chain.log`，逐条读数见 `s38b_*.log`）：
      `ruff check`（All checks passed!）、`ruff format --check`（435 files，按 Makefile 的路径集）、
      mypy（rc=0，无输出）、originality（Total 190 / Original 190 / Suspicious 0 / External imports 17）、
      reachability（189 模块 / 172 可达 / 17 白名单豁免，无未登记孤儿）、`contract_audit --ci`
      （63 Typed Query 契约 · 155 capability · 92 个 capability 无契约，184 行 PENDING）、
      `spec_audit --strict`（coverage 100.0%）、
      golden 审计（`[GATE] all L1 verified commands have real samples (OK)`）、adversarial、bridges、
      streaming+runtime、benchmark smoke、docs links（82 文件）、`tests/architecture`、
      `tests/protocol`+`tests/unit`（本步新增的那一项）。
    - **编号与让号**：本步六个文件先在 `abef5e1` 上写完并跑过一轮（基线 3416 / 80.77% → 本步树
      3431 / 80.82%，`s38_chain.log`，+15 项与后一轮逐格吻合）；期间并发会话把第 37 步（F-63①）落成
      `bb201b1`，于是整步让基线：把六个文件的 diff `git apply` 到 `bb201b1` 的新孤立树（六个文件与旧树
      逐字节 `cmp` 相同），基线与本步读数全部改取新一轮，不拼接旧基线数字。F-64/F-65 与「第 38 步」
      在落笔前对**工作树**grep 过（并发会话的 py3.13 那轮记的是 3423 / 5 skipped / 80.85%，与本轮
      3423 / 7 skipped / 80.79% 是同一棵树的两套环境读数，都保留原文）。

39. ✅ **发不出去的命令，在调用方读得到的每一面写明「已下线」：执行 F-63② 与 F-37 (c) 裁决（2026-09-19）**
    - **裁决是这一步的全部授权范围**（2026-09-19 向用户采集四条，本步只用其中两条）：F-63② →
      「保留，只把『已下线』写清」；F-37 → 「下调能力声称 + 推迟 tag」。于是本步零改协议字节、零改解析器、
      零删除任何公开面，改的全是"说法"这一层——而说法这一层正是 F-23/F-31/F-34/F-35 反复出事的那一层。
    - **一条推导链取代五份手抄名单**：账本 `STATUS_OFFLINE`（9 条）∪ `core._UNVERIFIED_STRUCTURED_BLOCK`
      （2 条）− `core._OFFLINE_FALLBACK_OK`（1 条放行）→ trampoline 模板（含 `_op_call` 传递闭包，故自己
      不写 `CMD[...]` 的 `export_security_list` 也被判出）→ 内核 `_CORE_BINDINGS` 的 tdx 直绑能力
      （7 个里 3 个死的）→ MCP 处理器 → 两份 markdown 表。五张面共用同一个推导，同一轮实测的分母是：
      模板面 9 个、业务入口面 3 个（`Client.minute`/`security_list`/`trades`）、Provider 文档死能力 3 行
      （另有 `finance`/`capital_changes` 两行归 F-37 那条判据）、MCP 死工具 3 个、接口表 47 行可解析 /
      23 行能推到方法或能力。
    - **落笔**：`tstdx/client/_mixin.py` 9 个模板 docstring（6 条 offline + 2 条 inferred 拦截 +
      `quotes_snapshot` 的**反向**口径「本方法仍可用，代价是每次批量尝试按已知失败处理，真实数据走
      逐只 `0x0530` 回退」）、`Client.security_list`/`minute`/`trades` 各点名它总是抛的那个异常、
      `mcp/_tools_spec.py` 三条描述（账本 offline 写 `offline`、inferred 拦截写 `unavailable`，两类失败
      不混写）、`docs/providers/tdx.md` 能力清单 5 行 + 口径来源段、`docs/api/interfaces.md` 13 行 +
      表前说明。README 三行按 (c) 降级：7709 覆盖率行不再列分时/逐笔、发布硬化行记着裁决与 tag
      `v1.1.0-dev.1` 继续推迟、Live Smoke 行不再声称该族字段级 live 正确。
    - **F-37 那半边按 (c) 落地的形状**：`_t_capital_changes`/`_t_finance_info` 各挂 `.. warning::`，
      把 2026-09-19 实测到的字段错位（`market` 读到 ASCII `'0'`、`code` 带 `\x01`）写成对外口径
      「条数可用、字段语义不保证」，并写明修它要新的真机布局判据、不按猜测改解析器。
    - **新门禁** `tests/architecture/test_offline_capability_honesty.py`（8 项）：模板面、入口面、
      Provider 文档面、MCP 面、接口表面各一条，加"措辞必须复述账本对 `offline` 的定义"、
      "F-37 两处降级不许被写回"、"永不可达的残留告警必须仍然只有一个发射点"。分母一律现推
      （本文件不抄命令号也不抄能力名，F-42 口径），每条带自检下限（模板 ≥9、入口面 ≥3、MCP 死工具 ≥3、
      接口表 ≥20 行且 ≥15 行可推导），Provider 文档面**两侧都判**：死能力没批注红，通的能力被写成受限也红。
    - **变异取证 13 发**（脚本 `step39/mutate.py`，逐发"应用→跑门禁→按字节还原"）：12 发各自只红自己那条；
      M9 把 `_op_call("security_list", …)` 换成运行期同值的 `F"security_list"`（AST 常量消失而行为不变），
      模板面与接口表面**同时**红——这两格读同一条传递闭包，红两格正是"同源推导"的证据而不是判据过宽。
      CONTROL 与逐项还原后 `baseline: all green` / `restore check: all green`。
    - **变异揭开的一处自身缺陷**：MCP 判据原写 `"offline" in description`，而 `CommandOffline` 这个类名
      自带 `offline` 子串 ⇒ 只提异常类名也能把断言喂饱。改成 `re.search(r"\boffline\b", …)` 后 M5 才真的红。
      与第 38 步 M1「机读侧替人读侧作证」同形，是本族第七次新判据上线即绿而绿是假的。
    - **登记不擅自改（F-66，本轮新行）**：还有第六张面，且它是唯一机器可读的那张——
      `GET /v13/capabilities` 把 tdx/quotation 的 16 个能力名平铺成裸字符串清单、没有任何状态字段，
      其中 8 个发不出去（`auction`/`volume_price` 在同一轮探针里当场 `CommandOffline`）。本步门禁以
      「方法名 = 能力名」为键，注册表那套 channel 词汇是第三套命名（`auction` vs `_t_auction_snapshot`），
      故这两条落在五张面之外。改发现面的形状属对外契约，三条路径写进 §0.3 F-66，与 F-44/F-47 一并裁决。
    - **顺带把两条已裁决却仍写着"需裁决"的行改对**：§0.3 F-44 头部改为「裁决已给出（选 (a) 接线），
      待第 41 步执行」、F-47 改为「裁决已给出（选 (a) 三面 fail-closed），待第 40 步执行」——用户已经答过
      的问题在账本上仍挂着"待答"，就是 F-58/F-61 那一族"把做过的写成没做"，只是这次落在裁决格上。
      F-63 整行转为已清偿、F-37 转为部分清偿（§0.1 ①② 两个边界子句仍点到它，判据不破）。
    - **复测（本机 Windows + 外部解释器 cpython-3.13.12，非仓内 `.venv`；孤立 worktree `wt_s39q` =
      干净 `4dd2af9` + 本步 6 个改动文件 + 新门禁，基线 `wt_s39qb` = 同 HEAD，同一轮）**：基线 junit
      **3438 tests / 0 failures / 0 errors / 5 skipped**、`--cov=tstdx` **80.91%**；本步树 junit
      **3446 / 0 / 0 / 5**、**80.91%**、RC=0，对账 `3438 + 8（新门禁 8 项）= 3446`，阈值 77 未下调。
      覆盖率 TOTAL 行与基线逐格相同（22555 stmts / 3712 缺 / 6040 branch / 1020 缺 branch）——本步在
      `tstdx/` 里没新增任何可执行语句，改的只有 docstring 与 markdown。**同一轮先前那次跑的是 80.90%**，
      差的 0.01 个百分点整格落在 `tstdx/transport/pool.py`（缺 113→111、缺 branch 37→36）：本步没碰那个
      文件，那是 transport 测试的运行间抖动，两轮读数都留在这里，而不是只留与基线相等的那个。同轮 9 主门禁两树全部 rc=0：originality（190 文件 / Suspicious 0 /
      External imports 17）、`spec_audit --json --strict`（coverage 100.0%）、golden 审计
      （`[GATE] all L1 verified commands have real samples (OK)`）、reachability `--strict`（无未登记孤儿 ✓）、
      `contract_audit --ci`（rc=0，92 项 PENDING 照旧不阻断）、docs links（82 文件）、mypy（CI 参数、无输出）、
      `ruff check`（All checks passed!）、`ruff format --check`（本步树 466 files / 基线 465 files already
      formatted）。`step39.log` 与 `baseline39.log` 是这一轮的两份原始读数；方案与 CHANGELOG 落笔之后，
      把两者复制进同一棵 `wt_s39q`（九个文件与工作树内容相同，唯一差别是 `tstdx/client/_mixin.py` 的行尾：工作树是 LF、该树 checkout 成 CRLF，规范化后同一个 blob）再跑一轮同样的 9 门禁 +
      全量离线：`s39final.log` 九项 rc=0、`s39final.suite.log` 即上面那组 junit 与覆盖率读数。
    - **本步未动**：CLI 帮助（`_guard_offline` 的抛错消息自第 38 步起已带账本 summary，调用当场就说清了）、
      HTTP 路由的 OpenAPI（未声明字段那半边属 F-47，第 40 步）、`0x000F`/`0x0010` 解析器（无判据不猜）。

40. ✅ **三张 wire 面对未声明的请求字段当场拒绝：清偿 F-47（用户裁决 (a)）（2026-09-19）**
    - **裁决就是这一步的授权范围**（2026-09-19 四条裁决里的 F-47 一条）：选 (a) 三面 fail-closed——
      HTTP 查询串与 body、WS `params`、MCP `arguments` 统一按白名单核对，未知键即拒，并让 MCP schema 的
      `additionalProperties: false` 真正被执行。(c) 的"GET 查询串维持宽容"未采纳，因此这一步是一次
      **对外请求契约的收紧**：此前返回 200 的请求开始返回 422 / `-32602`，属破坏性变更，CHANGELOG 与
      `docs/api/interfaces.md` 都按破坏性口径写明。
    - **拒绝口只有一条**：新增 `tstdx/integration/wire_fields.py`——`undeclared_fields()`（差集按字典序，
      同一份输入永远同一个报错）+ `reject_undeclared()`（抛 `ValidationError`，人读句点名未知键，机读侧
      `context.unknown_fields` / `context.declared_fields`，`phase=wire_validation`）。四面各自接这一条。
    - **白名单来源四面各不相同，但都是「声明本身」而不是抄件**：HTTP 查询串 = FastAPI 路由签名
      （`route.dependant.query_params` 运行时现取，因此结构上不存在第二份名单可过期）、
      HTTP body = `QUERY_BODY_FIELDS`（5 键）、WS `params` = `WS_PARAMS_FIELDS`（按 `METHODS` 逐方法，
      10 项含两个空集）、MCP `arguments` = 该工具的 `inputSchema.properties`，同时 9 张 schema 补上
      `additionalProperties: false`——只写声明不执行，就是 F-44 那格"文档承诺一个不会发生的行为"的
      入参面版本。
    - **实现先造出一次全站故障，被既有契约测试当场抓住**：接上依赖后**每一条**路由都返回 422，人读的
      那句只剩 `request validation failed`（`test_http_query_and_capability_discovery_delegate_to_client`
      第一时间红了）。根因用独立的两变体探针钉死（`step40/probe_annotation.py`，读数在同一份
      `probe_annotation.log`）：A 变体（模块带 `from __future__ import annotations` + `fastapi` 在工厂函数内
      import）→ `422 {"detail":[{"type":"missing","loc":["query","request"],"msg":"Field required"}]}`，
      B 变体（同一份代码只去掉 future import）→ `200 {"ok":"1"}`。PEP 563 下 FastAPI 拿到的只是字符串注解，
      而 `Request` 不在模块 globals 里，于是把依赖的那个形参当成必填查询参数。改法是删掉该文件的
      future import，并在原地写下为什么这个文件不许再加它。**这条约束隐形到一次"顺手统一导入风格"
      的改动就能让全站再挂一次。**
    - **两张 JSON-RPC 面的人读位置并不对称（实测，不是设计假设）**：同一次拒绝，WS 把理由写在
      `error.data.message`（`error.message` 按规范是通用的 `invalid params`），MCP 把它平铺进
      `error.message`。判据因此各按自己那面真实公开的读法断言——照抄一条，另一面就永远绿。
    - **新门禁** `tests/runtime/test_wire_declared_fields.py`（**46 项** = 6 条推导 + 40 条行为）。
      推导类：WS 表 ↔ `_dispatch`+`_policy` 的读取点、body 名单 ↔ `payload.get(...)` 两个方向求差；
      每张 MCP schema 必须写着拒绝；四面必须真的调用那唯一拒绝口；两份名单在全包只许有一个定义点
      （F-42 抄件口径）。每条带自检下限（WS 方法 ≥8、HTTP 路由 ≥10、MCP 工具 ≥5、扫描模块 ≥150）。
      行为类：逐路由 / 逐方法 / 逐工具真打——带满已声明字段必须通，多一个 `max_age` 必须被拒，且人读句
      与机读 `unknown_fields` 两侧都点名（第 38 步口径，不许机读侧替人读侧作证）。
    - **变异取证 13 发**（`step40/mutate.py`，逐发"应用 → 跑 46 项 → 按字节还原"；判据是"红掉的测试基名
      集合与预期完全相等"，既不许越界打到别的判据，也不许声称抓住却根本没红；参数化用例的红数另计）：
      M1 表里多一个未分派的方法 → 1 红；M2 声明一个分派代码从不读的键 → 2 红（推导 + 该方法的用例：
      没有测试样本的新键自己也会红，正是"幻影声明"的现场）；M3 表漏一个真读的键 → 1 红；
      M4 body 名单加幻影键 → 1 红；M5 schema 收回拒绝声明 → 1 红；M6 名单出现第二份定义点 → 1 红；
      M7 查询串闸永远放行 → 10 红；M8 查询串声明清空（把已声明字段也当未知）→ 7 红（9 条 GET 里两条
      本就无查询参数，没有可误伤的字段）；M9 body 闸永远放行 → 1 红；M10 WS 闸永远放行 → 10 红；
      M11 MCP 闸永远放行 → 9 红；M12 只把机读侧 `unknown_fields` 空掉（人读句仍然点名）→ 30 红，
      即全部读机读侧的行为用例；M13 整段撤掉 WS 的接线 → 11 红（结构判据 + 该面 10 个方法，两格读同
      一处接线，红两格是同源的证据而不是判据过宽）。`baseline: 46 用例, 红 无` / `restore check: 红 无`；
      一次 `ruff format` 换行之后重跑 13 发，红集合逐发相同。
    - **落笔**：`docs/api/interfaces.md` §3 服务面开头加口径段（三张 wire 面一致、白名单来源逐面列出、
      拒绝落点 422 / `-32602`、以及破坏性变更与 `_=…` 缓存穿透参数同样被拒的提示）。同轮实测：仓内
      没有任何内部代码或文档示例请求这些路由（`docs/` 里只有路由清单本身），所以没有一处示例因本步
      变成错的。
    - **复测（本机 Windows + 外部解释器 cpython-3.13.12，非仓内 `.venv`；孤立 worktree `wt_s40step` =
      干净 `8fe0d0e` + 本步 6 个代码/测试文件，基线 `wt_s40base` = 同一 HEAD，同一轮）**：基线 junit
      **3446 tests / 0 failures / 0 errors / 5 skipped**、`--cov=tstdx` **80.91%**
      （TOTAL 22555 stmts / 3712 缺 / 6040 branch / 1020 缺分支）；本步树 junit **3492 / 0 / 0 / 5**、
      **81.21%**、RC=0，对账 `3446 + 46（新门禁 46 项）= 3492`，阈值 77 未下调。多出的 0.30 个百分点
      不只是新模块自己绿：TOTAL 从 `22555 / 3712 缺` 到 `22580 / 3654 缺`——新增 25 条语句的同时把
      **58 条原本没执行到的服务面语句跑到了**，逐路由 / 逐方法 / 逐工具那 40 条行为用例是第一批把三张面
      的每条分支都真打一遍的请求。同轮 9 主门禁两树全部 rc=0（originality 191 文件 / Suspicious 0 /
      External imports 17、`spec_audit --json --strict` coverage 100.0%、golden `[GATE] … (OK)`、
      reachability 无未登记孤儿、`contract_audit --ci`、docs links 82 文件、mypy 无输出、
      `ruff check` All checks passed!、`ruff format --check` 468 files already formatted）。
      `s40step.log` / `s40base.log` 与 `s40step.suite.log` / `s40base.suite.log` 是这一轮的四份原始读数。方案与 CHANGELOG 落笔之后，把 9 个文件（6 个代码/测试 + `docs/api/interfaces.md` + 本方案 + CHANGELOG）整份复制进同一棵 `wt_s40step` 再跑同样的 9 门禁 + 全量离线：`s40final.log` 九项 rc=0 且逐行读数与 `s40step.log` 相同（originality 191 文件 / Suspicious 0、docs links 82 文件、`ruff format --check` 468 files already formatted），`s40final.suite.log` junit **3492 / 0 / 0 / 5**、**81.21%**、RC=0——这一轮读的树与提交内容同一份。**两次同树读数的 TOTAL 差一格**：缺语句 3654→3653、缺分支 1023→1022，整格落在 `tstdx/protocol/generic.py`（87%→88%），本步没碰那个文件；百分比读数两次相同，两处读数都留在这里而不是只留相等的那个。提交前对最终内容再跑第三轮（同一棵 `wt_s40step`）：`s40commit.suite.log` junit **3492 / 0 / 0 / 5**、**81.21%**、TOTAL 22580 语句 / 3654 缺 / 6042 分支 / 1023 缺分支——TOTAL 那一格又回到第一轮 `s40step.suite.log` 的 3654/1023，所以第二轮的 3653/1022 是 `tstdx/protocol/generic.py` 自己那批测试的抖动而不是本步改出来的；三轮的百分比读数一致，三轮都在这一格里，不留「只取相等的那两次」。同一轮的 9 主门禁见 `s40commit.log`（九项 rc=0）；补完本条之后同一棵树再跑一遍门禁，`s40commit2.log` 仍九项 rc=0——两份日志之间只差这条 bullet 的文字，9 项里唯一读文档的 `check_docs_links` 在两树上同为 `docs link check OK (82 files)`，而那份全量离线读数与提交内容差的也只有文档文字。
    - **顺手量出、但登记不代拍板（F-67，本轮新行）**：同一轮读 `docs/errors.md` §四「上层边界约定」时量出两处死路径——`facade/api.py`（`tstdx/facade/` 不存在）与 `integration/http_server.py`（真身是 `runtime_http.py`），第一条 bullet 还承诺了 `ApiResponse{success=False, ...}` 与 `route_errors` 这套已不存在的形状（`hasattr(tstdx, "ApiResponse")` False、`route_errors` 与 `W11` 在 `tstdx/` 内零命中）。事实型文档门禁看不见它：判据只认反引号里的**点号**模块路径，这三处写的是**斜杠**形式。同轮全扫 7 份活文档，只有这一份含死路径。**第一次取证本身就是错的**——按文件名尾串匹配把 `tstdx/client/api.py` 也算成命中，虚报了 README 与 ARCHITECTURE 两份活文档，改成整串匹配才对上事实（两份读数 `step40/probe_f67.log` 与 `probe_f67_precise.log` 都留档）。改一份对外文档的边界章节属 Phase 4「文档统一」，且扩门禁要单独一轮取证复测，因此不混进这次入参契约的提交，三条路径写进 §0.3 F-67。
    - **同时改对一处已过期字样**：§0.3 F-66 行原文写着「与 F-44/F-47 同批裁决」，而 F-47 已在本步清偿 ⇒ 改为「与 F-44 同批裁决（同格里曾挂着的 F-47 已由第 40 步清偿）」。
    - **本步未动**：`strict` 仍然不是请求参数（F-51 尾条 (b) 的口径没变，只是从"静默忽略"变成"当场 422"）；
      CLI 面本就有 argparse 的白名单，不在本步范围；发现面缺状态字段是 F-66，等裁决不代拍板。

41. ✅ **F-44 (a) 落笔：`currentness` 从声明口径变成运行期判据，"文档承诺 ⇒ 有站点"上门禁（2026-09-19）**
    - **裁决的边界先量清楚再动**：(a) 那一格写着两件事——"Provider 失败路径统一包成 `SourceUnavailable`"
      与"为 `currentness` 落一个可判据的运行期校验器"。第二轮取证（`step41/probe_reach.log`、
      `probe_reach2.log`、`probe_channels.log`）把两件事分成了两种下场：后者有落点，前者没有。
      没有落点的证据：`DIRECT_BINDINGS` 251 条与注册表三元组一一对应，2100 个 (provider, capability)
      组合经 `QueryPlanner.compile` 全部或编译成功、或以 `ValidationError`（"provider 'x' 不支持
      capability 'y'"）结束，**到达执行器"没有可执行 Direct Provider binding"分支的组合数是 0**；
      而把 Provider 真实失败（`ConnectionFailed`/`AllHostsUnreachable`/`WebSourceError`）统一包成
      `SourceUnavailable` 要推翻 `docs/providers/README.md` §12 自己那条"Provider-specific `TdxError`
      必须原样保留"的保护——那是另一次对外契约决定，不在本次"接线"授权里。
    - **接线的形状**：新增 `tstdx/runtime/freshness.py::verify_currentness(plan, *, strict)`，
      在 `DirectProviderExecutor.execute()` 里**先于任何 Provider I/O** 调用（`with warning_sink()`
      块内第一行，因此非严格面的瑕疵也进得了本次结果的 `meta.warnings`）。判据只读两件事：
      `PROVIDERS.get(provider).channel(id).local`（注册表事实）与 `_parse_currentness(plan.spec.currentness)`。
      `auto`/`historical` 不要求证据；`live`/`business` 要求"当期"，而本地文件 channel 给不出
      "文件已覆盖当期"的判据 ⇒ 无法证明。三面分工写进 `docs/providers/tdx.md` §5：
      规划期 422（`live` 打非 live channel，输入本身不可满足）/ 运行期 503（`strict=True` 且口径无法证明）/
      非严格 200 + `currentness_unproven` 瑕疵（新增 `WarningCode` 成员，发射口仍是
      `tstdx/diagnostics.py:record_warning` 一处）。
    - **为什么不做"未来日期"判据**（本步否决的一条看似更聪明的规则）：行内时间戳在本仓至少有三种写法
      （`parsers/*.py` 的 `f"{dt:08d}"` 8 位无分隔、`_std7709_bars.py` 的 `"YYYY-MM-DD HH:MM"`、
      web 适配器的 `"YYYYMMDD"`），全部是**无时区的源本地（CST）字面量**，而 `domain/models.py:11`
      却声明"时间 UTC 内部存储"；`Provenance.observed_at_ns` 是 `time.time_ns()` 的 UTC 墙钟。
      拿 CST 字面量跟 UTC 墙钟比符号，主机时区不在 +8 时会每天误报约 8 小时。要做这条判据得先解决
      行内时间戳的时区归属（另一格，未登记为本步范围），所以本步只落"注册表可判据"的半边。
    - **新门禁 `tests/architecture/test_error_promises.py`（4 项）**：判据是"对外文档以反引号点名的错误类
      ⇒ 该类的子树里必须有真实站点（raise / `raise <变量>` 回溯 / `on_error(...)` 投递）"。
      分母由扫描现推（文档集 46 份、点名 45 类），不抄清单；5 个未接线叶子进 `PROMISE_EXEMPTIONS`，
      每条豁免必须挂一个仍在台账里的 F 号，且**只看类自身站点**——一旦有人接线就必须撤销豁免，
      否则门禁自己报"已过期"（不给自己留一张没人读的表）。抽象基类不进豁免表：子树计数已让它们自然通过。
    - **变异 8 发全部被抓住**（`step41/mutations.log`，每发"改坏 → 跑指定测试 → 断言红 → 立刻还原"）：
      M1 规则收窄成只认 `live`（`business` 逃逸）→ 红 `test_non_strict_shape_rides_the_result_as_a_caveat`；
      M2 删掉执行器里的 `verify_currentness` 调用 → 同格红；M3 `strict` 面失效（不抛）→ 红
      `test_local_bars_with_business_fails_before_any_disk_io`；M4 非 `strict` 面把瑕疵改成别的类别 → 红；
      M5 从豁免表撤销 `UnknownCommand` → 红 `test_every_documented_error_class_is_wired_or_exempt`；
      M6 把已接线的 `TdxError` 塞进豁免表 → 红 `test_exemptions_never_outlive_their_condition`；
      M7 让文档点名扫描扫不到对外文档 → 红自检 `test_the_scan_itself_finds_the_tree_the_docs_promise`；
      M8 把 `raise <变量>` 从站点判据里剔除 → 红承诺门禁（`AntiSpiderBlocked`/`WebRateLimited` 被误报）。
      `变异数 8，未被抓到 0`，三个被改文件事后逐一做残留检查均为 OK。
    - **文档面（本步的第二半，把不兑现的承诺改成实话）**：`docs/providers/tdx.md` §5 改写为"运行期只有
      一处 `currentness` 判据 + `age`/`received_at` 在本仓没有字段也没有读写方（全仓 grep 0 命中）"，
      §7 的错误清单删掉 `CapabilityUnsupported`/`DataIntegrityError` 两个从未存在的名字并写明
      `SourceUnavailable` 无抛点；`docs/providers/README.md` §11 的 `historical_closed`/`current_series`
      换成 `currentness` 四值口径，§12 从"`SourceUnavailable == selected Provider unavailable`"改成
      "尚未接线的分类占位 + 选定 Provider 无法满足请求时用户实际拿到的是哪四类"；
      `docs/errors.md` 新增"一之二、树里存在但运行期永不发生的类"表（5 行，各配为什么不会发生）、
      `fallback_to_offline`/`fallback_to_web` 两行的消费方从"sources 路由"改为"**无消费方**"，
      头部"44 个类"改为指向 `tstdx.errors.__all__` 并记下漂移；`docs/tdx_status.md` 三处 P13-A 的
      `SourceUnavailable` 承诺改写为 v17 实况（门面已删除、offline 走 `CommandOffline`），
      并在 §五 表格上加"v17 状态"横幅说明该矩阵整体随门面下线。
    - **登记不代拍板（F-68，本轮新行）**：5 个占位叶子怎么处置（全删 / 全留 / 只留 E7050）是对外错误面
      的决定，且分歧点正好落在 ADR-013 承诺过的稳定资产上；三条路径写进 §0.3 F-68。
      同一行还登记了本步顺手量出的四处"文档独有幻影名"与 `docs/errors.md` 的类数漂移。
    - **判据自身的第一版是错的，两处**：站点普查最初只认字面 `raise X(`，把 `last_exc = AntiSpiderBlocked(...)`
      + `raise last_exc` 这条已接线的重试环虚报成幽灵（`step41/probe_orphan.log` vs `probe_sites.log`）；
      豁免表的自洁判据最初按子树计数，于是三个抽象基类被自己判成"已接线却仍挂豁免"——事实是它们
      **不该在豁免表里**（子树计数已让它们通过承诺门禁）。两次都按 F-67 的口径把错读数留档。
    - **复测（孤立 worktree `wt_s41step` = 干净 `b52a122` + 本步 9 个文件，基线 `wt_s41base` = 同一 HEAD，
      同一轮；解释器 cpython-3.13.12，非仓内 `.venv`）**：基线 junit **3492 / 0 失败 / 0 错误 / 5 跳过**、
      149.270s、**81.21%**（`step41/s41base.suite.log`）；本步树 junit **3509 / 0 / 0 / 5**、159.392s、
      **81.23%**（`step41/s41step.suite.log`），`3492 + 13（判据行为与矩阵）+ 4（承诺门禁）= 3509` 对得上，
      阈值 77 未下调。覆盖率 TOTAL 行 `22580 stmts / 3654 缺 / 6042 分支 / 1023 缺分支` →
      `22606 / 3654 / 6050 / 1023`：新增 26 条语句、8 条分支全部被执行到（缺语句与缺分支两格一格没动）。
      9 主门禁两树全部 rc=0（`step41/s41base.log`、`step41/s41step.log`）：originality 191→192 模块
      （新模块带 license 头）、ruff format 468→471 文件（3 个新文件）、docs link 两侧同为 82 files，
      spec_audit 100.0%、golden_audit/reachability/contract_audit/mypy/ruff check 五项读数与基线逐格相同。
    - **落笔后对提交内容所在的同一棵树再跑两轮（`wt_s41step`，dirty=11）**：`step41/s41commit.log` 九项
      rc=0 + `s41commit.suite.log` junit 3509 / 0 / 0 / 5、144.433s、**81.24%**、TOTAL
      `22606 stmts / 3653 缺 / 6050 分支 / 1022 缺分支`；补完下面两条文字后同一棵树再跑
      `s41commit2.log`（九项 rc=0）与 `s41commit2.suite.log` —— junit 3509 / 0 / 0 / 5、148.118s、
      **81.23%**、TOTAL 回到 `22606 / 3654 / 6050 / 1023`，与本步首轮 `s41step` 的读数逐格相同。
      两轮之间那 0.01 个百分点整格在 `tstdx/protocol/generic.py`（逐文件覆盖率表 diff 只有这一行：
      缺语句 20↔21、部分分支 9↔10），本步没碰那个文件——与第 40 步记录的是同一格运行间抖动，两个读数都留档。
    - **复测之后又改掉三处自己的读数（同一轮内、提交前）**：① CHANGELOG 初稿把对外行为写成
      "`live`/`business` 打在本地 vipdoc channel 上"，而实测 `live` 打本地 channel 在规划期就以
      `ValidationError`/422 结束、**到不了**运行期判据，可达形状只有 `business`（HTTP 面默认口径）一种——
      规则里的两个 mode 在用户侧只落地一个，措辞按可达形状重写；② 初稿写"其余 21 个远程 channel"，21 是
      `live=True` 的 channel 数，注册表总 channel 数是 56、非本地 55 个，两个口径混用了；③ `FreshnessViolation`
      的 HTTP 状态第一力量成 500——探针把 `http_status_for()` 当成"吃 `code` 字符串"的映射函数用
      （`http_status_for("E4060")` 落进 `isinstance` 失败分支才返回 500），按真实签名喂异常实例才是类声明的
      503。三处都在落笔后、提交前改掉，错读数一并留档（F-67 的口径）。
    - **本步未动**：`SourceUnavailable` 等 5 个叶子（F-68 等裁决）；`age`/`received_at` 类的年龄阈值
      （没有字段、没有生产者，造判据等于造需求）；非 live 的 Web channel 在 `business` 口径下的
      可证明性（注册表没有这个事实，硬判会把 HTTP 默认口径 `business` 全线变成 503）；
      行内时间戳的时区归属（`domain/models.py` 的 UTC 声明与解析器的 CST 字面量不一致，另案）。

42. ✅ **事实文档的斜杠死路径上门禁：清偿 F-67 (a)，Phase 4「文档统一」的第一格（2026-09-19）**
    - **本步零生产代码**：动的是 `docs/errors.md` §四、`docs/ARCHITECTURE.md` §3 的一行，以及判据
      `tests/architecture/test_doc_code_consistency.py`；`--cov=tstdx` 只看包，因此覆盖率 TOTAL 与基线
      逐格相同（两边都是 `22606 stmts / 3654 缺 / 6050 分支 / 1023 缺分支`、81.23%）。这不是"没测到"，
      而是本步确实没往 `tstdx/` 里放东西。
    - **改写前先量"这一节错在哪一层"**：§四 原文三条 bullet 里两条点名磁盘上不存在的模块
      （`facade/api.py`、`integration/http_server.py`），第一条还承诺了整套不存在的形状
      （`ApiResponse{success=False, ...}` 与 `context["route_errors"]` 聚合，W11）。今天的事实是：
      `tstdx/client/api.py` 全文**没有一处 `except`**（`Client` 在 56 行、`AsyncClient` 在 427 行，
      异常原样上抛），越过信任边界的错误只有一个形状（`tstdx/error_envelope.py::to_error_envelope`），
      HTTP 面真身是 `runtime_http.py` 且状态码取自 `envelope.http_status`，CLI 走 stderr 一行 JSON
      信封并以 **2**（领域错）/ **1**（非领域错）/ **130**（Ctrl-C）退出。五条逐条实测后写进新 §四，
      并保留"此前写的是什么、为什么那是已删除的旧形状"的说明——不静默抹掉史。
    - **顺带量出第二处假事实**：`docs/ARCHITECTURE.md` §3 分层表把 `security/` 一层标成「活」，
      而 `tstdx/security/` 早在 `fcf8e92`（撤回一项过期的安全承诺）里连同其唯一文件一起删除。
      该行删掉，另在表下补一段说明：包内没有独立安全目录，越界时的凭据处理由 `error_envelope.py`
      的按关键字过滤承担，安全口径指向根 `SECURITY.md`（第一稿曾额外声称"凭据一律走环境变量注入"
      并链向不存在的 `docs/security.md`，两处都在落笔前删掉/改指，否则会撞 G6 链接门禁）。
      补的说明里「删除前也只有一个 docstring 与 `__all__ = []`」这一格不是转述——按
      `git show fcf8e92^:tstdx/security/__init__.py` 读出来核对，与根 `SECURITY.md` 的记法一致
      （那个模块的 docstring 还预告了 `capture.py`/`origin.py` 两个从未存在的文件）。
    - **判据扩形（(a) 的第二半）**：新增斜杠形式判据，与原点号判据并存互不干扰——
      扫 25 份事实文档（原 8 份 + `docs/providers/` 全部 + `docs/tdx_status.md`），取反引号里
      含斜杠、且以 `/` 或文件后缀收尾的 token，要求它在**仓库根 / `tstdx/` / `docs/`** 任一处落位。
      三个根是取证逼出来的：文档对同一物件有三种写法（`docs/providers/tdx.md` 全写、
      `quickstart.md` 指 `docs/quickstart.md`、`transport/pool.py` 指 `tstdx/transport/pool.py`），
      只按仓库根匹配会虚报 37 处（见下面的变异读数），而**虚报比漏报更难查**（F-44/F-67 同一口径）。
    - **豁免只有两条，且都窄**：① 同一**逻辑块**（段落 / 列表项 / 表格行）内写明删除史
      （`删除`/`移除`/`清理`/`不再`/`下线`/`已消灭`/`退役`/`历史`/`曾经`/`retract`）；
      块粒度是判据的全部效力所在——整份文档当一块等于没有判据。② 该目录由代码在运行期自建，
      判据是推导而非名单：同一个 .py 文件里既调用 `mkdir`、又把这个名字写成路径分量
      （`codegen.py` 的 `"generated_draft" / "_generated.py"`）。当前基线读数：**0 违约 / 18 个豁免
      token**，18 个逐个看过出处，全部落在"删除史叙述"或运行期草稿区两类，无一处是现时口径的假事实。
    - **扩判据前先全量取证**（`step42/probe_slash_paths.log`）：活文档里斜杠形式的路径引用共
      **828 处**，磁盘上不存在的有 **85 个不同 token**；绝大多数在 `docs/archive/`、`docs/adr/`、
      各版本方案史里（刻意的历史语境，事实门禁按 `EXCLUDED_PARTS` 不参与）。收窄到 25 份事实文档后，
      真需要改的只有 **3 处**（§四 的两处 + ARCHITECTURE 的 `security/` 一处），其余全是删除史叙述。
      探针第一版还把 `output://`、`parquet://`、`mypy tstdx/`、`docs/providers/<provider>.md`、
      `~/.tstdx/config.toml` 当成路径报了出来（9 处噪声），因此判据加了形状排除：含空格、`://`、
      `<`、`>`、以 `~`/绝对路径起头的都不是"仓库里的一个路径"。
    - **自检与被自检**：另加 `test_the_slash_ruler_itself_sees_the_deleted_layers`，硬要求判据**真的
      看见过** `execution/`、`provider/`、`tstdx/facade/` 这三层已删除目录（且豁免名单 ≥5 项）——
      一套只会说"没问题"的判据与没有判据等价。**变异 9 例、UNEXPECTED 0**（`step42/mutations2.log`）：
      控制组绿；3 个植入的现时口径死路径（表格行、跨段删除史、`tstdx/security/`）全部被抓；
      把整份文档当一块 ⇒ 跨段那条**逃逸**（证明块粒度承重）；豁免退回"目录名在源码里出现过就算"
      ⇒ `tstdx/security/` 被 `/v13/security/count` 这条路由字符串**白白赦免**（证明 `mkdir`+路径分量
      判据承重，这正是本步第一版的实际错法）；只认仓库根 ⇒ 误报 37 处；摘掉形状排除 ⇒ 5 处垃圾
      进名单；把扫描面缩到一份文档 ⇒ **自检报警**（证明自检耦合在扫描面上，不是空转）。
    - **复测（孤立 worktree，同一轮；解释器 cpython-3.13.12，非仓内 `.venv`）**：基线
      `wt_s42base` = 干净 `39a1b30`（dirty=0），junit **3509 / 0 失败 / 0 错误 / 5 跳过**、157.414s、
      **81.23%**（`step42/s42base.suite.log`）；本步树 `wt_s42step` = 同一 HEAD + 本步 3 个文件
      （dirty=3），junit **3511 / 0 / 0 / 5**、155.107s、**81.23%**（`step42/s42step.suite.log`），
      `3509 + 2`（斜杠判据 + 自检）对得上，阈值 77 未下调。9 主门禁两树全部 rc=0
      （`step42/s42base.log`、`step42/s42step.log`），且逐项读数逐行相同：originality 192/192、
      spec_audit 100.0%、G6 docs link 82 files（新增的 `../SECURITY.md` 相对链接可解析）、
      ruff check「All checks passed!」、ruff format 471 files——本步没有新增文件，故两侧同数。
    - **落笔后再跑一轮提交内容所在的同一棵树（`wt_s42step`，dirty=5：判据 + 2 份文档 + 本台账 +
      CHANGELOG）**：`step42/s42commit.log` 九项 rc=0，`s42commit.suite.log` junit **3511 / 0 / 0 / 5**、
      145.696s、**81.24%**、TOTAL `22606 / 3653 / 6050 / 1022`；补完安全口径那一格的措辞后同一棵树
      再跑 `s42commit2.log`（九项 rc=0）与 `s42commit2.suite.log`——junit **3511 / 0 / 0 / 5**、
      145.601s、**81.23%**、TOTAL 回到 `22606 / 3654 / 6050 / 1023`，与本步首轮 `s42step` 逐格相同。
      两轮之间那 0.01 个百分点仍整格落在 `tstdx/protocol/generic.py`（缺语句 20↔21、部分分支 9↔10，
      逐文件表 diff 只有这一行）——与第 41 步记录的是同一格运行间抖动，四个读数里三个给 21/10，
      而本步没碰那个文件。
    - **第三次跑同一棵树收口（`step42/s42final.log`、`s42final.suite.log`，内容即提交本身）**：
      九项门禁 rc=0，junit **3511 / 0 / 0 / 5**、146.851s、**81.23%**、TOTAL
      `22606 / 3654 / 6050 / 1023`，与 `s42base`/`s42step`/`s42commit2` 逐格相同。
      本条与 CHANGELOG 的复测口径落定后，同一棵树再跑最后一轮（`step42/s42ship.log`、
      `s42ship.suite.log`）：九项门禁 rc=0、junit **3511 / 0 / 0 / 5**、146.489s、**81.23%**、
      TOTAL `22606 / 3654 / 6050 / 1023`，逐格不变。六轮读数里只有 `s42commit` 那一轮把
      `tstdx/protocol/generic.py` 记成 20/9，其余五轮都是 21/10。
    - **本步未动**：点号判据的扫描面（扩到 `docs/providers/` 与 `tdx_status.md` 只加在斜杠判据上，
      避免一次改动顺带改写别的口径）；`docs/archive/`、`docs/adr/`、各版本方案史里的死路径
      （按设计豁免）；F-67 登记时提过的全仓范围命中（`facade/api.py` 24 份 md、
      `integration/http_server.py` 9 份）绝大多数就在这些归档里，不参与事实检查；
      门禁只判"路径在不在磁盘上"，不判"这行话术是否夸大"——后者仍是逐本人读。
43. ✅ **配置面文档的五类抄本上机器对账门禁，当场量出「照指引做就自毁」的 F-69（2026-09-19）**

    - **本步问的是「文档写的每个数字，代码真的认账吗」**：`docs/configuration.md` 是配置面唯一的用户
      口径——一张表列全 5 段 17 键，每格写着默认值、取值范围与读取方；§4 定下 `TSTDX_<SECTION>_<KEY>`
      命名规则与一张「专用 runtime 变量」表；§5 逐格承诺「这种写法会以什么消息报错」。第 43 步之前，
      这一整面声称里只有「配置段数 = 5」一个数字被既有门禁读过。新模块
      `tests/architecture/test_config_doc_contract.py`（18 个用例）把其余五类逐类钉回运行期事实：
      ① 段与键**双向**对账（schema 有的必须都记录，记录的不许是幻影）；② 默认值对上运行期
      `DEFAULT_CONFIG` 的实际取值（读实例，不猜 dataclass 元数据形状）；③ 取值范围改成**行为化**判据
      ——文档写的边界必须真被 `validate()` 接受、越界一档必须真被拒（`_numeric_bounds()` 的边界取自文档、
      整/浮步长取自 schema，`rate_limit` 那格写在表外的 prose 也被读到）；④ 注册表成员声称
      （`default_provider`/`web.enabled_sources`/`web.rate_limit`）以 `PROVIDERS` 与 `KNOWN_SOURCES` 两个
      真相源逐个试正反例，且「文档点名的行」与「判据认识的行」必须是同一份集合；⑤ §5 的 8 条
      fail-closed 场景**就地触发**，异常类名与文档写出的消息片段逐个命中，场景表双向对账（文档新增
      不认识的红、删掉已认识的红）；⑥ 环境变量：命名规则对**全部** 17 键逐个成立，§4 表与 loader
      登记表双向相等。
    - **测下来这份文档今天全部为真**（这条要写清，它决定本步的性质）：17 键的默认值、6 个数值键的
      边界、8 条报错消息片段、7 个「已删除段仍 fail closed」、命名规则 17/17——逐格对上，没有一格需要
      改文档措辞。所以第 43 步的主体是**防回潮**而不是纠错，唯一量出的错在环境变量命名空间那一格。
      这一点与 F-67 不同：那一格是文档真的写错了，本步六类判据里有五类是给「今天对、明天会漂」的抄本
      上闸（F-13 的死配置段、F-16 的「写了 TOML 不生效」、F-20 的幻影键 `hosts.heartbeat_cmd`、F-43 的
      幻影开关 `allow_partial` 都是这一族反复出错的那几类）。
    - **量出的真实缺陷（F-69，登记见 §0.3）**：`TSTDX_WENCAI_COOKIE` 被 `tstdx/web/wencai.py:77` 读、
      被它自己的错误消息叫用户设置，却没登记进 `loader._RUNTIME_ENV_KEYS` ⇒ 照指引做完 `Client()` 抛
      `ConfigError [E1000]`。基线复现与修后对照（干净 `7a3bae7` 树 vs 本步树，同一解释器同一命令）：
      基线 `ConfigError [E1000] 无法识别环境变量 TSTDX_WENCAI_COOKIE：段名须属于 ['core', 'hosts', 
      'rate_limit', 'web', 'security']`，修后 `Client OK`。修法取 (a)：登记该变量、§4 表补一行、把测试
      夹具用的 `TSTDX_GOLDEN_SYNTHETIC` 改名 `GOLDEN_INCLUDE_SYNTHETIC`（旧名全仓零引用，契约零变化），
      并把判据从「两份抄本互相印证」升级成「**代码从环境读的每个 `TSTDX_*` 都必须有归属**」——扫描
      `tstdx/` 与 `scripts/` 全部 .py 字面量，只放行已登记与 schema 形两类；`tests/` 排除（负例夹具是
      刻意的错拼）。前缀示意的写法（`TSTDX_RATE_LIMIT_*`）由 `_TRUNCATED_NAME` 滤掉，否则 loader 自己的
      docstring 会被判成未登记变量。
    - **顺手撤销一条已经失效的豁免**：`tests/runtime/test_kernel_config_wiring.py` 的 `_FIELD_EXEMPTIONS`
      还写着 `currentness`「运行期校验器尚不存在（未裁决，见 §0.3 F-44）」——第 41 步已落
      `tstdx/runtime/freshness.py:40` 直接读 `plan.spec.currentness`，那句话成了假事实而豁免本身也不再
      需要。取撤销而非改措辞：该字段自此受幻影旋钮判据管（M12 证明撤销有牙）。
    - **变异 12 发全部被点名抓出、两条 CONTROL 绿**（`step43/mut.py`）：M1 幻影键行、M2 删键行、
      M3 过期默认值、M4 放宽范围、M5 改报错消息措辞、M6 删 §5 场景行、M7 段数抄本、M8 取消登记
      `TSTDX_WENCAI_COOKIE`、M9 塞入未登记的环境读取、M10 删 §4 表一行、M11 改 `docs/api/interfaces.md`
      的 WS 方法名、M12 摘掉 `currentness` 的执行面读取点。逐发要求 rc=1 **且**指名该条判据红 **且**消息
      里点着缺陷，无一发溜过；首轮有两发因判据自身缺陷而读数失真，见下一条。
    - **变异轮逼出两条判据自身的缺陷**（这是它的全部价值所在——落笔时它们看起来都是好的）：
      ① M1 第一轮报出 4 条红，其中 `test_membership_claims_match_the_registries` 说的是「成员声称与
      判据对不上账」——幻影键让一条与本判据无关的声称也报了误导性结论。修法是让成员声称判据只量真实
      存在的键（`_schema_keys()`），幻影由双向判据专门报；M1 收敛到 3 条红且每条说自己那句。同一过滤
      补进 `_numeric_bounds()`/`_keys_of_type()`/nan-inf 目标选取，否则一处文档笔误会炸出三条
      `AttributeError` 而不是三条断言。② M6 让自检用例以 `KeyError: '段内未知字段'` 崩溃——「判据自身
      失效」应当是一句人话，不是一次异常栈，已改为显式断言。两条都是**只在变异下才现形**的形状缺陷，
      正向跑 18 个用例全绿时看不见（与 F-67/F-44 的「探针多报与判据漏报同一族」同形）。
    - **同轮把 `docs/api/interfaces.md` 的两种新形状纳入既有门禁**（`test_doc_code_consistency.py`
      +45 行）：WS 方法名单的第二种写法（顿号分隔的散文清单，不是 `docs/api/README.md` 那种
      括号清单）逐个对 `runtime_ws._dispatch` 认账，MCP「N 工具」的数对 `TOOLS` 注册表认账。一个名字
      两种抄法就有一条判据的漏网形状——M11 正是这条判据的现场证据。
    - **复测（隔离工作树，同一解释器 py3.13.12、`-m 'not network'`、`--cov=tstdx`）**：基线树 `7a3bae7`
      junit 3511 / 0 失败 / 0 错误 / 5 跳过、
      157.874s、覆盖率 **81.23%**（TOTAL 22606 / 3654 / 6050 / 1023）——与第 42 步 ship 读数
      逐格相同；本步树 junit 3531 / 0 / 0 / 5、
      150.179s、覆盖率 **81.26%**（TOTAL 22606 / 3648 / 6050 / 1020）。净增 20 个用例、
      覆盖率+0.03pp（本步只加测试与文档，未动 `tstdx/` 除 loader 登记表一行）。两棵树 `rc` 均为 0，
      `ruff check`、`ruff format --check` 与 `mypy tstdx/`（CI 同参数，191 模块）均 0 问题；阈值 77
      一次都没动。
    - **本步未动**：`docs/configuration.md` §1 的数据流图与 §6 编程接口示例（人读口径，没有逐格抄本可
      钉）；§4 的「值解析顺序 JSON→bool→int→float→逗号列表→字符串」六档只在命名规则判据里被间接用到
      （`"1"` 走整数档），未按文档措辞逐档探针——那属 `parse_env_value` 的行为测试，`tests/config/` 已有
      覆盖但不是从文档推导；`tests/` 目录不参与 `TSTDX_*` 对账；`docs/archive/`、`docs/adr/` 里的旧环境
      变量口径按历史语境豁免。
    - **ship 轮（提交树复测，同一解释器与工作树参数）**：`3582469` 落到 main 后另起隔离工作树 `wt_s43commit` 再量一遍，`head=3582469 dirty=0`。九项确定性门禁 **G1–G9 全部 rc=0**（originality 192/192 全 MIT、spec_audit `coverage_pct 100.0`、golden_audit「all L1 verified commands have real samples (OK)」+ 既有 `suspect_short` WARN 1 条、reachability 无未登记孤儿、contract_audit、docs links 82 files、mypy、ruff check、ruff format 472 files already formatted）；离线全量 **junit 3531 / 0 失败 / 0 错误 / 5 跳过**、163.516s、覆盖率 **81.26%**（TOTAL 22606 / 3648 / 6050 / 1020，`Required 77.0% reached`），与本步树逐格相同——提交内容与被测内容一致，无「只在干净树上绿」的差额。
44. ✅ **执行 F-68 裁决 (a)：删掉 4 个从不兑现的错误叶子，把「文档承诺」门禁改成双向，并当场修掉投递站点判据的漏形（2026-09-19）

    - **本步问的是「错误树里那些文档承诺用户会拿到、运行期却永不发生的叶子，兑现吗」**。裁决 (a)「全删 5 个
      叶子」的落点是 4 个：`UnknownCommand`(E3030) / `ChecksumMismatch`(E3050) / `SourceUnavailable`(E7050) /
      `CompatibilityWarning` 连同 `__all__` 条目、E 段声明与 docstring 一并物理删除（`tstdx/errors.py` −35 行）。
      删除后的树是 40 个 `TdxError` 子类 / 40 个唯一 code / E1–E9 九域仍在（`__all__` 45 个名字 = 40 类 +
      `TdxError` + `RetryAdvice` + `RETRY_ADVICE` + `advice_for` + `http_status_for`）。
    - **裁决的前提被自己的判据证伪了 1/5，这一条要用户复核**：第 41 步那轮站点普查只认字面 `raise X(`，
      本步把 `on_error(...)` / `warn(...)` / `emit(...)` / `_emit(...)` 四类**投递**前缀纳入判据后重测，
      `BackpressureOverflow`(E6030) 当场有真实站点——`tstdx/streaming/base.py:185-186` 在队列溢出时把
      `sub.on_error(BackpressureOverflow(...))` 交给订阅方，且 context 带 `dropped_total`/`max_queue`。
      删掉它就不是「删一个从不兑现的叶子」而是删一条对外承诺，与裁决意图相反，故**保留并新增行为测试**
      （`tests/streaming/test_backpressure_delivery.py`：溢出确实投递到 `on_error` 且 `code == "E6030"`）。
      同一轮把「第 5 个名额」的取证否掉过程写进 `docs/errors.md` §一之二，判据里另留一条 canary
      （`assert "BackpressureOverflow" in wired`）——投递前缀判据若被改坏，红的是这条断言而不是又一次静默漏报。
    - **文档侧不是删名字，是按实测行为改写**（每处都先探针量过再落笔，`step44/probe_*.py`）：
      `docs/providers/README.md` §12 的 `SourceUnavailable`「规范语义」段撤销（v17 没有统一的「Provider 不可用」类，
      `docs/providers/tdx.md` §7 的 `CapabilityUnsupported`/`DataIntegrityError` 换成实测真实形状：E1010 /
      `IntegrityViolation` E3042 / `TruncatedDataError` E4050）；`docs/troubleshooting.md` 的 E3030 小节改写为
      `[E3035] CommandOffline`；baidu/boc/iwencai/eastmoney/sina/tencent 六份 Provider 文档的错误段统一改成
      `ValidationError`(E1010) + `WebSourceError`(E7xxx) 家族口径，其中两处是**假承诺**：`FreshnessViolation`
      按 `tstdx/runtime/freshness.py` 的判据只在 `ChannelSpec.local` 的 channel 上触发，纯 HTTP 的 baidu 严格
      模式不会因新鲜度报错；iwencai 写的「认证错误」在本仓根本没有独立类。`docs/migration/easy_tdx.md` 的
      「未知 category 发 `CompatibilityWarning` 并回退 `"day"`」整句作废——`normalize_bar_period` 对未知值原样
      透传，回退由所选 Provider 的 `supported_periods` 以 422 拒绝，不存在「告警后回退」。ADR-013 §11 那句
      「`SourceUnavailable` 是稳定公共资产」不抹史：原文保留，追加一条带日期与裁决号的作废修订。
    - **新判据是双向的**（`tests/architecture/test_error_promises.py`，+315 行）：正向=「活文档错误小节点名的
      树内类必须有真实站点」；反向=「活文档错误小节以反引号点名的 CamelCase 名必须存在于允许集
      （builtins ∪ 全仓 `tstdx/` 的 `ClassDef` 名 ∪ 错误树）」。反向这一半是本步新写的，因为删除动作只会让
      正向判据「少报」而永不报警——文档留着已删类的名字时它一句都不说。实测口径：38 份活文档、错误小节里
      47 个 CamelCase 候选、41 个是真实树内类、**0 个幻影名**、`PROMISE_EXEMPTIONS` 清空。历史语境文档按前缀
      豁免（`docs/REFACTOR*` / `CHANGELOG*` / `docs/archive*` / `docs/adr*`），另留一处小节级豁免
      （`docs/tiantian_fund_extensions.md` §六 的东方财富移动端 endpoint 名）并配「豁免仍需要吗」的自洁判据。
      判据的允许集刻意不是「名字以 Error/Exception 结尾」：那样会把 `CommandOffline`、`FreshnessViolation`
      这类真类挡在射程外。
    - **退役要有唯一登记处**：新增 `docs/errors.md` §一之二，它是全仓唯一允许点名「不存在的错误类」的地方，
      并配 `test_register_lists_only_names_that_are_really_gone` 双向钉住——登记表里的名字若哪天回到树里就红
      （防止这张表变成第二份承诺），表本身读不出来也红（防止判据靠正则空转）。
    - **README 的「40+ 异常类」下界宣称撤销并改成反向判据**（`test_doc_code_consistency.py`）：类数快照在删类
      那一刻必然变谎，而本步正是删类，故把 `_FLOOR_CLAIMS` 的 `README.md 异常类` 一行连同其真相源 `_error_classes()`
      一起删掉——**那个真相源本身也是错的**：它数的是 `errors.py` 全部顶层 `ClassDef`，把 `RetryAdvice` 这个
      dataclass 算成「异常类」。替换为反向判据 `test_no_live_doc_states_an_error_class_count`（任何活文档写回
      `NN+ 异常类` / `NN 个错误类` 即红，方案与台账文档除外）+ 扫描面金丝雀（`README.md` 必须在集里且 ≥30 份，
      实测 39 份）——否则「没有文档可读」会让判据假绿。同轮把 `docs/troubleshooting.md` 的「85 命令账本」
      数值声称补回（改写 E3030 小节时被连带删掉，导致既有精确数字门禁报「门禁失效」）。
    - **本步把自己写的三张 taxonomy 表也钉回了事实**（`tests/errors/test_taxonomy.py`）：`EXPECTED_HTTP_STATUS`
      此前**没有任何测试读取**——一张看起来像契约、实际无人执行的清单，正是 F-68 同一族的「虚报」；现在按
      「偏离默认 500 的类」双向核对（改过状态码的类必须进表、继承 500 的不得进表、值必须逐格相等）。
      `EXPECTED_PARENTS` 改成必须覆盖每个类的直接基类。两条新判据当场量出既有表的 8 处漂移：`CommandOffline`
      /`FreshnessViolation`/`TruncatedDataError` 三个第 41 步接线的真类在四张表里全部缺席，`AdjustError` 缺继承行，
      `WebSourceError` 改了 502 却没进 http 表。新增 `test_the_table_is_the_module_surface` 把表与 `tstdx.errors.__all__`
      双向对账（多删少删都红）。
    - **顺手清掉同族的一条假事实**：`docs/tdx_status.md` §五 末段把 `route="web"/"local"` 写成今天的入参契约并称
      「仍报 `ValueError`」——实测 `Client.quotes(..., route="web")` 是构造期 `TypeError`（`Client.quotes` 形参只有
      `symbols`/`provider`/`policy`/`currentness`）。改写为 pre-v17 → v17 四条对照，并补上今天真实的失败形状
      （`provider` 写错名 → `ValidationError` E1010/422，context 带 `known_providers` 与所写 id）。
    - **登记 F-70**：4 份对标/审计文档（`docs/efinance_parity_gap_analysis.md`、`docs/niuniu_coverage_audit.md`、
      `docs/astock_toolkit_parity.md`、`docs/tiantian_fund_extensions.md`）仍以现在时把已删除的 `UnifiedQuoteAPI`
      门面写成今天的对外接口，其中一处还是给下游的「校验指令」；死路径判据只认路径形状、幻影名判据只扫错误
      小节，**活文档里的非错误类裸名当前没有任何判据**。三条路径（移入 archive / 逐份改写 / 扩判据到全小节）
      列在 §0.3 F-70，不代拍板。
    - **变异取证 14 发全部点名**（`step44/mutation1.log` 8 项 + `step44/mutation2.log` 6 项，逐发注入→跑指定节点→
      断言它红→字节级还原并复核）：活文档塞回幻影名、登记表混进在世类名、撤销 endpoint 名豁免、空转豁免、
      摘掉 `on_error` 投递前缀（canary 抓到了）、继承表删一行、http 表期望值写错、改了码的类漏进表、表里塞进
      继承 500 的类、活文档写回「40+ 异常类」、把金丝雀抬到不可满足。首轮反向判据还量出两处自身缺陷并当场修：
      `_live_docs()` 用「路径 ∉ 前缀名单」判历史文档（精确匹配 ⇒ ADR 与 archive 全部漏豁免而误报），
      以及两处笔误让判据以 `AttributeError`/语法错崩溃而不是给人读的断言消息。
    - **复测（同一轮，解释器 py3.13.12、`-m 'not network'`、`--cov=tstdx`、阈值 77 未动）**：主树 junit
      **3585 / 1 失败 / 0 错误 / 5 跳过**、143.206s、**81.27%**（TOTAL 22595 / 3645 / 6050 / 1019）；那唯一一条红
      `tests/test_spec_coverage.py::test_audit_covers_every_non_probe_yaml` 与**本步无关**——并行会话在
      `PROTOCOL_SPEC/_sniffer/quotation/{000f,0200,2d00}/DRAFT.yaml` 放了三个未跟踪的自动探测草案，
      该判据硬要求草案清单只有一项。本步没有触碰 `PROTOCOL_SPEC/`，也未把这些文件带进提交。
      隔离工作树复测消掉这一干扰：基线树 `wt_s44base` = 干净 `9ba2385`，junit **3531 / 0 / 0 / 5**、150.694s、
      **81.26%**（TOTAL 22606 / 3648 / 6050 / 1020）；本步树 `wt_s44step` = 同一 HEAD + 本步 23 个文件（dirty=23），
      junit **3585 / 0 / 0 / 5**、150.702s、**81.27%**，`3531 + 54 = 3585` 可核对（净增：反向门禁与退役登记、
      taxonomy 三张表的双向核对参数化、背压投递行为测试），`rc` 均为 0。九项确定性门禁在两棵树**逐格相同**且
      全部 rc=0：originality 192/192 全 MIT、Suspicious 0，spec_audit `coverage_pct 100.0`，golden_audit「all L1
      verified commands have real samples (OK)」+ 既有 `suspect_short` WARN 1 条，reachability 191 模块 / 174 可达 /
      17 白名单「无未登记孤儿」，contract_audit，docs links 82 files，mypy，ruff check「All checks passed!」，
      ruff format 473 files（+1 = 本步新增的投递测试）。
    - **ship 轮（提交树复测，同一解释器与工作树参数）**：`1e4340b` 落到 main 后另起隔离工作树 `wt_s44ship`
      再量一遍，`head=1e4340b dirty=0`。九项确定性门禁 **G1–G9 全部 rc=0**，且本份门禁日志与 `wt_s44step`
      那份**逐字节相同**（只在末尾多记一行本脚本自加的 rc=0 计数），即提交内容与被测内容一致；离线全量
      junit **3585 / 0 失败 / 0 错误 / 5 跳过**、150.17s、覆盖率 **81.27%**（TOTAL 22595 / 3645 / 6050 / 1019，
      `Required test coverage of 77.0% reached`，阈值未下调），与本步树逐格相同——没有「只在干净树上绿」
      或「只在脏树上绿」的差额。
    - **本步未动**：F-70 那 4 份文档与「非错误类裸名」判据的扩形；`docs/api/interfaces.md` 与 `docs/cookbook/`
      里指向 `tstdx.web.WebQuoteClient` 的历史入口口径（第 41 步起就在待裁决清单里）；`tests/errors/test_taxonomy.py`
      的 `E_RANGE` 无需扩判据（实测本就全覆盖）；ADR-013 正文原文（只加修订不抹史）；`v1.1.0-dev.1` 标签仍未打。

45. ✅ **执行 F-18 裁决 (b)：把一件造好并测过、却没有任何生产调用点的安全资产物理删除，顺手抓到被删文件在三份清单里的四种过期抄本（2026-09-19）**

    - **裁决与射程**：用户对 F-18 拍板 **(b) 连同测试删除**（原话记于 §0.3 F-18 行），(a) 接线与 (c) 维持
      豁免均未采纳。本步只做这一件：删除 `tstdx/providers/http.py`（360 行）与它唯一的消费者
      `tests/providers/test_http_boundary.py`，撤销 `scripts/_reach_allow.txt` 的豁免记录，改写 README 两处
      对外口径。**零生产调用点不是印象**：全仓 grep `providers.http` / `ProviderBoundHttpClient` /
      `host_allowed` / `PROVIDER_HTTP_HOST_SUFFIXES`，`tstdx/` 内除模块自身外命中 **0**，`scripts/`、`ops/`、
      `benches/`、`.github/`、`pyproject.toml` 同样 0，唯一命中是那一份测试文件（取证脚本与逐条输出见
      `step45/mutations.py` 的 M5：`importlib.util.find_spec` 现为 `None`，`tstdx.providers` 包内也无
      `http` 字样残留）。
    - **"11 项离线测试全覆盖"这一格登记了四个错数，本步逐个量出**（都在同一行 F-18 里）：① 该文件
      `--collect-only` 实为 **10 项**；② 守卫主机表实为 **7 个键**（`tencent/sina/eastmoney/baidu/jsl/boc/iwencai`），
      而 registry 的 11 个 Provider id 里 `builtin/derived/local_vipdoc/tdx` 这 4 个本就不经这条 HTTP 守卫，
      故 (a) 的缺口不是"补到 11"；③ "全覆盖"在覆盖率意义上不成立——第 44 步基线日志里
      `tstdx\providers\http.py` 是 **177 语句 / 64 未命中 / 60 分支 / 13 部分 = 60%**，低于全仓均值，
      这也正是删掉它之后整仓覆盖率**上升**的原因；④ (a) 写的接线点 `build_client(provider=…)` 根本没有
      `provider` 形参（`tstdx/web/_base_http.py:498` 实签 `build_client(prefer_httpx=True, default_headers=None)`），
      真要接得先把 Provider 身份送进 web 传输层构造面——改动面比登记的大一格。
      教训与 F-42/F-68 同族并已并入既有判据：**登记时的数字就是下一个过期点**，本行只保留原文＋实测并记。
    - **删一个文件让三份手抄清单同时暴露，其中一份是门禁自己**：`tests/architecture/test_official_runtime_no_fallback.py`
      把 `tstdx/providers/http.py` 抄在 `OFFICIAL_RUNTIME` 清单里，文件消失后三条判据以
      `FileNotFoundError` 崩在 `read_text`——读者会读成"环境坏了"而不是"清单过期了"。该清单是人工 curated
      的子集（删前 20 项 / 删后 19 项，全仓 190 模块），不能 glob 推导，因此补
      `test_official_runtime_inventory_points_at_real_files`：死路径以人读消息逐个报名，另加"清单 ≥15 项"
      金丝雀，防止覆盖面缩水成空转假绿。另两处过期抄本按历史口径保留（README 路线图行与 CHANGELOG
      既成条目各自就地改写为已删除语境；`docs/REFACTOR_PLAN_V17_CLOSURE.md` §0.2 F-8 与 §0.3 F-22 的
      "17 条豁免"是当时读数，现值 16 记在本行与本步的 F-18 行）。
    - **对外口径改写而不是抹掉名字**：README 特性表原本写着"守卫已实现但尚未接入 web 链路"（一句把
      死资产写成活的承诺），路线图「可达性收口」行原本把它列为"仍待裁决的一项"；两处统一为"HTTP 传输层
      不做 Provider 主机白名单，单源边界由调用方自证"，并保留内核那条真实约束（选定的 Provider 不会被
      悄悄换成别家）。README 里该模块路径写成斜杠形——点号形 `tstdx.providers.http` 会被
      `test_backticked_tstdx_paths_are_importable` 当场拒（该判据**不给点号路径退役豁免**，因为点号形就是
      用户会照抄的 import 语句）；这是本步真实撞出来的一条，记下来免得后人当成判据过严。
    - **变异取证 7 发全部逐发红数**（`step45/mutations.log`，每条注入→跑指定节点→断言红→字节级还原，
      末发用 sha256 复核还原）：M5 删除可证性、M1 删模块留豁免记录 → `audit_reachability --strict` 以
      `[dead]` 红、M2 清单塞回死路径 → 新自检以人读消息红（同一次注入下 M2b 证明三条旧判据仍是
      `FileNotFoundError`，即缺自检时的真实形状）、M3 清单缩水到 3 项 → 金丝雀红、M4 README 用点号死路径 →
      文档判据红并指名。首轮 M2 曾误判为 FAIL：新测试的 docstring 里出现了 `FileNotFoundError` 这个词，
      pytest 连 docstring 一起打印，判据"输出里不该有 FileNotFoundError"因此永远不成立——改成匹配
      `FileNotFoundError: [Errno 2]` 后才测到真形状（**判据自身缺陷，与被测对象无关**，与第 43 步"两处笔误让
      判据以 AttributeError 崩掉"同一族）。
    - **复测（同一轮，解释器 py3.13.12、`-m 'not network'`、`--cov=tstdx`、阈值 77 未动）**：基线 = 第 44 步
      提交树工作树 `wt_s44ship`（干净 `bd5e5c7`）junit **3585 / 0 / 0 / 5**、81.27%；本步树
      `wt_s45step`（同一 HEAD + 本步 7 个文件，dirty=7）junit **3576 / 0 失败 / 0 错误 / 5 跳过**、
      149.83s、覆盖率 **81.45%**（TOTAL 22418 / 3581 / 5990 / 1006）。`3585 − 10 + 1 = 3576` 可核对
      （删掉 10 项守卫测试、净增 1 项清单自检）。九项确定性门禁 **G1–G9 全部 rc=0**：originality
      `Total: 191 / Original: 191 / License OK: 191 / Suspicious: 0`（−1 = 被删模块）、spec_audit
      `coverage_pct 100.0` 与 `total_specs 44` 不变、golden_audit `total 530: real 60 / synthetic 470`、
      reachability `190 模块 / 174 可达 / 16 豁免` 且 `[ALLOW-DEFECT]` 0 条、contract_audit、docs links
      82 files、mypy、ruff check「All checks passed!」、ruff format **471 files**（473 − 2）。
    - **ship 轮（提交树复测，同一解释器与工作树参数，不引用步骤轮读数）**：`05acd91` 落到 main 后另起
      隔离工作树 `wt_s45ship`（实测 `head=05acd91`、`git status --porcelain` 0 行）把九项门禁与离线全量
      重跑一遍：`s45ship.gates.log` 与 `s45step.gates.log` **逐字节相同**（两份 sha256 均为
      `fe75c5720579a2f05c3595e824f4842bb3038bbe46e3b6625ba5dee3eb1a0b81`），`rc=0` 计数 9/9；离线全量
      junit **3576 / 0 失败 / 0 错误 / 5 跳过**（另 10 项被 `-m 'not network'`  deselected）、150.16s、
      `SUITE_RC=0`、覆盖率 **81.45%**（TOTAL 22418 / 3581 / 5990 / 1006，
      `Required test coverage of 77.0% reached`，阈值未下调）——与本步树逐格相同：删掉一条链外守卫这件事
      在干净提交树与被测工作树上给出同一个数，没有「只在脏树上绿」的差额。
    - **本步未动**：F-65 裁决 (b)（账本函数面）、F-66 裁决 (c)（能力发现面只给名字不给状态）、F-70
      的 4 份对标文档；`docs/archive/OPTIMIZATION_PLAN.md:32` 那句"`unknown_command_ids` 保留为别名"属
      归档史，留给 F-65 一并处置；SECURITY.md 从未提及该守卫，故无需改；`v1.1.0-dev.1` 标签仍未打。

46. ✅ **执行 F-65 裁决 (b)：账本函数面收口——删掉两个没人读的聚合器，把剩下的五个名字逐个钉成"有名单、有口径、有用例"（2026-09-19）**

    - **裁决与射程**：用户拍板 **(b)**（原话记于 §0.2 F-65 行）——保留 `by_family`/`unknown_command_ids`
      为公开查询面并补文档与用例，删 `stats()`/`get_command_by_name()`；(a) 四个全删、(c) 只删两孤儿均未采纳。
      删除前先按 HEAD `e47538d` 复量（不引用第 38 步的旧读数）：`stats()` 全仓唯一读者是它自己的测试
      （`tests/unit/test_commands.py:104` 那一句 `st = stats()`），`get_command_by_name()` 除定义行与
      `commands.__all__` 行外**零命中**（且从未从 `tstdx.protocol` 再导出）。
    - **删的不是"两个函数"而是"两份名单说不清同一件事"**：HEAD 上 `commands.__all__` 里没有 `stats`，
      包面却 `from .commands import stats` 并把它挂进自己的 `__all__` ——两面各说各话，读者按包面写的
      `from tstdx.protocol import stats` 确实能用，所以这条假承诺没有暴露面。这一形状被直接做成判据
      `test_package_face_declares_nothing_the_module_denies`（包面宣称的账本名字必须是模块面也声明的）。
    - **本步量出的事实（全部现算，不抄）**：账本 85 行 / 5 协议族（`quotation 39 / ex_quotation 17 /
      mac_quotation 16 / goods 11 / f10 2`）；状态 `online` 74 / `offline` 9 / `degraded` 2；语义未经 golden
      校正 默认族 30 条、全账本 76 条（反向即默认族 verified 恰好 9 条，与既有测试同数）；名字全局唯一
      （85 个名字互不重复 ⇒ `cmd()` 不需要族上下文的前提成立）；`get_command(cmd(name), family)` 与被删的
      按名线性扫描对 85 行**逐行等值**——被删能力的归宿是证据不是断言。
    - **裁决没覆盖的第三个孤儿 `cmd()`，本步如实登记而不是顺手删**：HEAD 实测 `tstdx/` 内对它的调用点为 **0**
      （消费者只有测试）。(b) 的删除名单是点名的两个，本步不擅自扩大对外契约的收窄范围：改为**保留 +
      补口径 + 补用例**，并交新门禁"每个公开名必须有测试真的从账本模块 import 并裸名调用"持续把住——
      只 import 不调用不算读者。同一条尺子顺手量到 `by_status` 也**零生产调用点**（读它的是三份测试），
      故它的文档口径改成实话：发包前的 fail-fast 读的是**单行的** `status`（`_guard_offline` 走
      `get_command`），本函数只负责"按状态列全部行"。
    - **登记文本的三处失真按实测改写**（F-65 行与本表同源处都改）：① "by_family 只被 `by_status`/
      `unknown_command_ids` 调用"——`by_status` 实际直接遍历 `COMMANDS`，`by_family` 的生产读取点只有
      `unknown_command_ids` 一处；② `unknown_command_ids` 返回 `list[Command]` 而非裸命令号（名字极易误读，
      docstring 里给了取号写法）；③ `by_status` 的 docstring 与 `docs/api/interfaces.md` 那行原写"客户端
      fail-fast 就以它为依据"，实测不成立。归档那句"保留作别名"从未成立（初始提交 `f73ef61` 的
      `tstdx/protocol/commands.py` 里 `unknown_command_ids` 与两个被删函数同时存在、也没有 `unknown_commands`），
      真实同名物是 `tstdx/transport/sniff.py` 的 `ProtocolSniffer.unknown_commands()`（探测期观察到的未登记号），
      归档原文不抹、就地加修订注记。
    - **新门禁 `tests/architecture/test_ledger_public_surface.py`（6 项）**：函数面形状双向锁死（模块公开
      函数 ⇄ `__all__`，且类名不计入函数面——`Family` 按 `callable()` 判会被误算）、包面不宣称模块面否认的
      名 + 裁决保留的两个公开面不得从包上消失、文档表名单与声明名单双向相等、文档 5 处规模字符串全部由
      运行期重算后**逐字回查原文**（第 19 步 F-42 与第 45 步的同一课：抄一次就失真，所以不许抄）、
      每个公开名有用例真的调用、`stats`/`get_command_by_name` 不得回潜（模块面与包面各查一次）。
    - **顺带修掉本表自己的三处过期读数**（第 45 步登记、本步落地）：§0.2 F-8 行的"17 条可达性豁免（含
      `providers.http`）"、§0.3 F-22 处置段的"清单重写为 17 条"与本表 F-23 行"标注 `tstdx.providers.http`
      已实现但未接入"——三处都按第 45 步撤销该豁免后的实测 **16 条**（本步同参数复算：`190 模块 / 174 可达 /
      16 豁免 / 记录缺陷 0`）改写并指回 F-18 行，历史数字保留但标明"只对当时读数负责"。
    - **变异取证 5 发，逐发指名目标判据**（`step46/mutations2.log`，最终树重跑）：M1 以 HEAD 原形状让 `stats`
      回潜（同时补 import 与包面 `__all__`）⇒ 包面判据与"不得回潜"两条同时红；M2 文档状态分布 `offline` 9→10
      ⇒ 重算判据红；M3 文档表塞一个函数面没有的名 ⇒ 名单对账红；M4 把用例的调用改成别名 import ⇒
      "必须被真的调用"红；M5 账本里长出一个未声明的公开函数 ⇒ 形状锁红。还原证明：四个被注入文件的
      sha256 与注入前同值（`4acfb42ea2f3a25d` / `91d8b496a7c5ffbc` / `e3836a0509b206a3` / `7821a69ad482123b`），
      行数与 EOL 未变（`commands.py` 390 行全 CRLF、`interfaces.md` 425、`test_commands.py` 210、
      `__init__.py` 69）。首版脚本曾在锚点匹配失败时把半成品留在树上（`read_text` 把 CRLF 归一成 LF、
      锚点却按 `\r\n` 拼），本版改为 `newline=""` 读写 + `try/finally` 字节还原，教训记在这里。
    - **复测（同一轮，解释器 py3.13.12、`-m 'not network'`、`--cov=tstdx`、阈值 77 未动）**：基线 = 第 45 步
      ship 树 `wt_s45ship`（干净 `05acd91`，`git status --porcelain` 实测 0 行，`tests/unit/test_commands.py`
      collect 17 项）；本步树 `wt_s46step`（HEAD `e47538d` + 本步 9 个文件）九项确定性门禁 **G1–G9 全部 rc=0**
      （`s46step.gates.log`）：originality `Total: 191 / Original: 191 / Suspicious: 0`、spec_audit
      `total_specs 44 / coverage_pct 100.0`、golden_audit `total 530: real 60 / synthetic 470 / unknown 0`、
      reachability `190 模块 / 174 可达 / 16 豁免` 且"无未登记孤儿 ✓"、contract_audit `63 个 Typed Query 契约 /
      155 个注册业务 capability`、docs links `82 files`、mypy、ruff check「All checks passed!」、ruff format
      `472 files already formatted`（471 + 本步新增门禁文件）。离线全量 junit **3593 / 0 失败 / 0 错误 / 5 跳过**、
      151.959s、`SUITE_RC=0`、覆盖率 **81.50%**（TOTAL 22403 / 3569 未命中 / 5982 分支 / 1006 部分，
      `Required test coverage of 77.0% reached`）——与基线差额可核对：`3576 − 17 + 28 + 6 = 3593`
      （`test_commands.py` 由 17 项增至 28 项、净增 6 项门禁）。
    - **ship 轮（提交树复测，同一解释器与工作树参数，不引用步骤轮读数）**：`24f1b1e` 落到 main 后另起隔离
      工作树 `wt_s46ship`（实测 `head=24f1b1e`、`git status --porcelain` 0 行）把九项门禁与离线全量重跑一遍：
      `s46ship.gates.log` 与 `s46step.gates.log` **逐字节相同**（两份 sha256 均为
      `1ed719240f9827a3174cf0d719be78f1f62173b577f64183562e8703d682ac27`），`rc=0` 计数 9/9；离线全量
      junit **3593 / 0 失败 / 0 错误 / 5 跳过**、151.709s、`SUITE_RC=0`、覆盖率 **81.50%**
      （TOTAL 22403 / 3569 / 5982 / 1006，`Required test coverage of 77.0% reached`，阈值未下调）——与本步树
      逐格相同（唯一差额是两条日志的耗时 151.959s / 151.709s，同一台机器同一批测试的正常抖动）。
      提交树比步骤树多出的内容只有第 46 步这一整条账目（步骤树那份拷贝早于本段文字），而读账目的门禁恰好是
      `tests/architecture/`（本表路径与 F 号引用的存在性判据），它在两边同绿——记账不改判定，这条是本步的自检。
    - **本步未动**：F-66 裁决 (c)（能力发现面只给名字不给状态）、F-70 的 4 份对标文档与"非错误类裸名"判据
      扩形；`tstdx.protocol` 包面与模块面 `__all__` 的不对称只登记不拉平（收窄包面是另一次对外决定）；
      `v1.1.0-dev.1` 标签仍未打。

47. ✅ **执行 F-66 裁决 (c)：能力发现面「只有名字、没有可用性」——推导补上注册表那一跳，文档口径改由门禁现算回查（2026-09-19）**

    - **裁决与射程**：用户拍板 **(c)**（原话记于 §0.3 F-66 行）——**不改面、只补判据**：把第 39 步的门禁扩到
      注册表词汇，并在 `docs/api/*` 写明"发现面只声明名字、不声明可用性"。(a) 给发现面加状态字段、(b) 收窄
      名单两条路径**均未采纳**，因此本步对三处出口（`Client.capabilities()` / `GET /v13/capabilities` /
      WS `runtime.capabilities`）的形状**零改动**——不加键、不删名、不改排序，一条判据专门把这个"不动"钉住。
    - **本步量出的事实（全部现算，`step47/probe_ledger_facts.log`）**：发现面 172 个名字，构成是可复算的
      恒等式 `172 = 7 个内核直绑能力 ∪ 167 个 catalog 迁移能力`，且与注册表（`11 Provider × 56 channel`）
      里出现过的能力名集合**逐字相等**（三处出口同源，`Client.capabilities()` 是那一份名单的唯一来源）。
      其中经 tdx 命令账本的只有 **25 个名字 = 7 内核直绑 + 18 个 tdx 客户端族 catalog 绑定**，其余 **147 个**
      走 web 会话 / web adapter / channel adapter / composed 四类后端——本步的门禁因此**只对 25 个名字的可
      用性发言**，这句话在文档与判据里同时写着，不拿 172 当作"172 条都能发"。注册表名与实现方法不同名的
      绑定有 10 条（`auction`→`auction_snapshot`、`f10_catalog`→`catalog`、`mac_quotes`→`mac_quote` …），
      这一套第三命名正是第 39 步词汇的盲区。
    - **8 个名字一调即失败，两类死法不混写**：6 个名字踩在账本判 `offline` 的 **5 条命令**上（`auction`
      `0x056A` / `volume_price` `0x051A` / `block_quotes` `0x07E5` / `minute_history` `0x0FB4` /
      `security_list` 与 `security_list_all` 同踩 `0x044D`），抛 `CommandOffline`；2 个名字（`minute`
      `0x0537`、`trades` `0x0FC5`）是 request/parser 仍为 inferred 的结构化拦截，抛 `NotImplementedFeature`。
      **"6 个名字"不等于"6 条命令"**——本步第一版草稿就把它写成"6 条命令被账本判 offline"，按名字与按号两种
      口径自查后改写，现在这句话由判据现算（名字数与命令集合分别推）。出路列也不许想象：每个死名字的备选
      Provider 必须与 `PROVIDERS` 里"声明了同一能力名的 Provider"求差为空，`minute`→baidu/eastmoney/tencent、
      `trades`→baidu/tencent 是从注册表读出来的，其余六个除 tdx 外无人声明，格子里就写「无」。
    - **唯一的解析盲区按名字登记、并要求它保持无害**：`f10` 的发现名经 `f10_client` 后端实现，但执行器按
      **capability** 而不是 `meta.method` 选方法（`download` / `catalog`），所以"实现方法名"这一跳对它失明。
      判据把 `unresolved == {"f10"}` 钉成名单，同时追加一条自毁式断言：**F10 族账本里出现任何 offline/拦截
      命令即红**——登记过的盲区只在它确实判不到东西时才无害。
    - **推导补的那一跳与其增量（`step47/probe_hop_delta.log`）**：注册表能力名 → `catalog` 绑定 → 实现方法 →
      `_t_*` 模板 → 命令号。同一套代码里把这一跳摘掉即退回第 39 步：账本可达名字 **7 → 25**、判死的名字
      **3 → 8**（新增 `auction` / `block_quotes` / `minute_history` / `security_list_all` / `volume_price`），
      而旧的 7 个内核直绑名判定结果一字未变——这一跳只扩覆盖、不重判。M10 变异（摘掉那一跳）实测把死名字
      表判据打红，证明它是承重的而不是装饰。
    - **新门禁 4 项（`tests/architecture/test_offline_capability_honesty.py` 8 → 12 项）**：① 每个发布名归进
      恰好一格（内核直绑 / 账本可达 / 非账本后端），归不进去或落进"没登记过的后端"即红——绑定表长出第六种
      后端不许被静默算成免检；② 出口形状锁：HTTP 与 WS 的返回字面量里出现 `status`/`available`/
      `availability`/`offline`/`degraded`/`verified` 任一键即红，同时要求文档小节写着「没有可用性」与「不承诺」；
      ③ 死名字表四字段（名字集合、命令号、实现方法、出路 Provider）两侧对账——漏一行等于把发不出去的名字
      平铺成可用能力，多一行等于把通的名字写坏；④ 文档 7 处计数（172、`tuple[str, ...]` 的 172、恒等式、
      11 × 56、8/6、25 = 7 + 18、其余 147）+ README 的三元组全部由运行期重算后**回查原文**，另判 §3 的
      HTTP 与 WS 两个小节必须互指这一口径。
    - **抓到自己踩了邻居的门禁**：新增小节插在「Client 方法表」与「UnifiedRuntime」之间，而
      `tests/architecture/test_doc_code_consistency.py` 的 Client 表窗口原本以「切到 `### UnifiedRuntime`」为界，
      于是发现面表的能力名被当成方法名，全量跑报「表里多出 `auction`/`block_quotes`/`minute_history`/
      `security_list_all`/`volume_price`」。窗口改为收在本节之内（切到下一个 `###` 标题），判据强度不降：
      名单双向相等与逐行参数名差集照旧，另补 M11/M12 两发变异证明收窄后的窗口仍抓得住漏行与假形参。
      这一格与第 40/42 步"新文档小节踩到既有解析窗口"同形，教训是**加小节前先查谁在按标题切这份文件**。
    - **取证过程里同时中过两个"假绿"（记在这里因为它们是同一类错误）**：① 判据侧——M7（把 Client 行里的
      「没有可用性」删掉）首版**没红**，因为该单元格末尾的交叉引用「见下节『能力发现面：只有名字，没有
      可用性』」本身含有被 substring 命中的那句话；改成先把交叉引用剥掉再判，口径必须写在这一行自己身上。
      ② 脚本侧——变异脚本用 `'RED' in outcome` 判命中，而字符串 `NOT-RED` 里就含 `RED`，于是第一版把这一发
      打成了 PASS。改为 `endswith("=RED")` 后重跑，12 发全部命中目标判据。一条只看"有没有红字"的取证脚本
      与一条只做 substring 匹配的判据，同轮各绿了一次——与第 41 步 F-68 的"探针多报/判据漏报"是同一课。
    - **一轮纪律偏离如实留档**：本步第一次全量跑漏掉了 `-m 'not network'`，让 10 项 `network` 标记的实时冒烟
      真的出了网（那份 junit 是 3607 项 / 6 跳过 / 81.67%）。它不作为门禁证据，也不写进任何文档口径；按
      既有 protocol 重跑的离线读数（下两条）才是本步的数。**离线口径是这套台账的唯一比较基准。**
    - **复测（同一轮，解释器 py3.13.12、`-m 'not network'`、`--cov=tstdx`、阈值 77 未动）**：基线 = 第 46 步
      ship 树 `wt_s46ship`（干净 `24f1b1e`）junit **3593 / 0 失败 / 0 错误 / 5 跳过**、81.50%；本步树
      `wt_s47step`（HEAD `4411f00` + 本步 4 个文件）九项确定性门禁 **G1–G9 全部 rc=0**（`s47step.gates.log`）：
      originality `Total: 191 / Original: 191 / Suspicious: 0`、spec_audit `total_specs 44 / coverage_pct 100.0`、
      golden_audit「all L1 verified commands have real samples (OK)」、reachability「无未登记孤儿 ✓」、
      contract_audit、docs links `82 files`、mypy、ruff check「All checks passed!」、ruff format
      `472 files already formatted`（本步只改既有文件，未新增文件）。离线全量 junit
      **3597 / 0 失败 / 0 错误 / 5 跳过**、145.097s、`SUITE_RC=0`、覆盖率 **81.50%**
      （TOTAL 22403 / 3569 未命中 / 5982 分支 / 1006 部分，`Required test coverage of 77.0% reached`）——
      与基线差额可核对：`3593 + 4 = 3597`，恰为本步新增的四项判据，覆盖率与基线同值。
    - **ship 轮（提交树复测，同一解释器与工作树参数，不引用步骤轮读数）**：`eadb0c3` 落到 main 后另起隔离
      工作树 `wt_s47ship`（`git status --porcelain` 实测 0 行）把九项门禁与离线全量重跑一遍：
      `s47ship.gates.log` 与 `s47step.gates.log` **逐字节相同**（两份 sha256 均为
      `1ed719240f9827a3174cf0d719be78f1f62173b577f64183562e8703d682ac27`），`rc=0` 计数 9/9；离线全量
      junit **3597 / 0 失败 / 0 错误 / 5 跳过**、150.994s、`SUITE_RC=0`、覆盖率 **81.50%**
      （TOTAL 22403 / 3569 / 5982 / 1006，阈值未下调）——与本步树逐格相同，唯一差额是两条日志的耗时
      （145.097s / 150.994s，同一台机器同一批测试的正常抖动）。提交树比步骤树多的只有本条账目，而读这条
      账目的判据（F 号与小节引用的存在性）在两边同绿。
    - **本步未动**：F-70 的 4 份对标文档与"非错误类裸名"判据扩形（仍待裁决）；(a) 加状态、(b) 收窄名单两条
      路径未采纳、也未预研；`docs/providers/tdx.md` 的口径由第 39 步的五张面判据继续管，本步不重复批注；
      F-15 的 CI 覆盖率重钉、F-20 的 heartbeat 默认值、F-37 剩余两项仍分别卡在 CI 环境与实时复跑授权；
      `v1.1.0-dev.1` 标签仍未打。

48. ✅ **内核的 web 一跳不再借道对外便利入口：一条字面门禁看不见的隐式换源，改用行为判据钉住，并把它该不该存在登记为 F-71（2026-09-19）**

    - **授权与射程**：本轮是零缓存口径下的内部加固，射程只有两格——`_web_quotes` 这一跳的取数源必须
      **唯一**且**等于 `plan.provider`**，并且这件事要由**行为**判据而不是字面 token 来钉。对外面（`tstdx.web`
      该不该继续是公开入口）不在授权内：本步对 `tstdx/web/__init__.py` 的公开函数、`__all__`、模块 docstring
      与 `docs/migration/` 一字未改，那一问登记成 §0.3 的 F-71 等裁决。
    - **盲区是当场量出来的（`step48/mutations.py` + `step48/m1_neighbors.log`）**：这一跳此前写的是
      `get_quotes(list(plan.spec.symbols), source=plan.provider, timeout=self._hop_timeout(plan))`，即复用对外
      的便捷函数。而本文件那三条"内核不许碰有序降级"的判据读的全是形状（`FORBIDDEN_NAMES` 的 import 名单、
      `FORBIDDEN_CALLS = ("WebQuoteClient(", "web_session(")`，以及
      `test_official_runtime_has_no_cross_provider_fallback_literal` 里那组降级流水线字面量）——`get_quotes`
      只在 `source` 为假值时才 new `WebQuoteClient`（`tstdx/web/__init__.py:492-499`），所以内核借道时一个字面量
      都不触发。实测把 `source=plan.provider,` 这一行删掉（等价于将来有人在跳里省掉点名），整条
      `tests/architecture` + `tests/runtime` + `tests/web` 离线邻域**只有新写的行为判据一处红**，三条既有判据
      与其余全部判据一字不变地绿，而内核那一跳已经落到"按 `web.enabled_sources` 逐源试、把非 `TdxError`
      异常也记进 `self.errors` 后继续下一家"的路径上。这是"直连既定数据源"口径的最后一格假绿。
    - **改法（`tstdx/runtime/executor.py:598-610`）**：与兄弟跳 `_tencent_bars`（`:612`）同形——
      `create_source(plan.provider, timeout=self._hop_timeout(plan))` + `src.fetch([normalize_symbol(s) …])` +
      `finally src.close()`。语义与旧路径的指定源分支等价，三条理由：`get_quotes` 那一支本就是同样三行，
      省略掉的 `headers`/`cookie` 在 `create_source`（`tstdx/web/__init__.py:345-353`）里的缺省正是它当时传进去的
      `None`、`max_retries`/`rate_limit` 两边都不转发；`plan.provider` 经 `resolve_provider` 恒为已注册非空名
      （`tstdx/query.py:314-317`），故旧代码的 `if source:` 分支恒被走到。对外行为零变化，变的只是内核不再拿
      "公开默认值恰好不出错"当作自己不换源的前提。
    - **同轮做了一次全仓构造点普查（写进 §0.3 F-71 ⑥）**：本步之后，内核到 web 的取数跳**全部**是单源构造点
      （`create_source` 两处、三家历史 K 线源类直构造、迁移能力跳的 `WebQuoteSession(源名)`，其 `_c` 只
      `create_source(self.source_name)`，`tstdx/web/session.py` 全文对 `WebQuoteClient`/`DEFAULT_FALLBACK_ORDER`/
      `enabled_sources` 零引用）；有序降级的构造点在发行代码里只剩 `tstdx/web/__init__.py:498` 与 `:506` 两处，
      都在缺省分支里。普查顺带抓出一条假事实：`tstdx/web/session.py:168` 自称会话「底层复用 `tstdx.web` 的
      零依赖适配器与降级逻辑」，而它按构造就是单源、永不触降级链（`tstdx/web/` 下引用那三个符号的文件只有
      `__init__.py`、`sources.py` 的定义处与 `adapters_fund.py:27` 的一句"不参与行情降级链"注释，11 个
      `_session_*.py`  mixin 全部零引用）——那段话描述的降级形状只属于 `WebQuoteClient`，对不上会话。本步不改它
      （属对外文档面），随 F-71 一起处置。
    - **新判据（`tests/architecture/test_official_runtime_no_fallback.py` 11 → 12 项）**：
      `test_kernels_web_hop_uses_exactly_the_source_the_plan_named` 把 `tstdx.web.WebQuoteClient` 换成"一被构造
      就抛断言"的桩、把 `create_source` 换成记录器，再从 `DIRECT_BINDINGS` **现推**派发 `_web_quotes` 的 Provider
      名单（当前 4 家：`baidu`/`eastmoney`/`sina`/`tencent`），逐家 compile→跑跳→要求
      `hops == [(provider, 归一化后的符号) …]`。三个设计点：名单是推导的不是手抄（F-42 口径）、派生分母塌成空
      即自曝、符号必须**原样**送达。
    - **同轮换了一把尺子（F-48 的超时判据跟着搬家）**：`test_web_route_also_gets_the_bounded_timeout`
      （`tests/runtime/test_kernel_config_wiring.py`）原先靠 patch `get_quotes` 来读这一跳的超时，调用点一改它就
      失去读者。现在它只 patch `create_source`，一次跑 `_tencent_bars` + `_web_quotes` 两跳，要求两次构造都拿到
      `0 < timeout <= 0.25`——M5（这一跳不取预算、写死 `30.0`）实测由**这条**判据报红而不是新判据，两条尺子各管
      各的格、不互相顶。
    - **5 发变异读数（`step48/mutations2.log`，逐发字节还原 + sha256 对账）**：M1 改回借道 `get_quotes` 且不点名
      源 ⇒ 新判据红、超时判据红、旧字面门禁绿；M2 源名写死成 `tencent` ⇒ 新判据红、旧绿；M4 把派生分母的过滤
      条件改成不存在的执行器名 ⇒ 新判据点名"覆盖面塌成了空转"；M5 不取预算 ⇒ 超时判据红、新判据绿；
      M3 丢掉执行面的 `normalize_symbol` ⇒ **三处全绿**，这是本判据的一条真实边界而非假绿：符号在进入内核前已由
      规划器归一化（`step48/probe_plan.py` 实测 `plan.spec.symbols == ('sh600519', 'sh600519')`，输入里的裸码
      `600519` 在 compile 阶段就被补成 `sh600519`），执行面那一层是为与旧路径等价而保留的冗余第二遍。判据里保留
      符号断言（钉"参数原样送达"），但本轮不假装它钉得住归一化——改法与文档都不许把它算作本步的覆盖面。
      另：同一判据在改道前的树上先跑过一轮 4 发（`step48/mutations.log`，`ALL-RED`：丢源、源传 `None`、源名
      字面量、空覆盖），那一轮的 M1 邻域读数就是上面那条"整片只有它红"。
    - **复测（同一轮，解释器 py3.13.12、`-m 'not network'`、`--cov=tstdx`、阈值 77 未动）**：基线 = 第 47 步
      ship 树 `wt_s47ship` junit **3597 / 0 失败 / 0 错误 / 5 跳过**、81.50%；本步树 `wt_s48step`（HEAD `cb5c1c6`
      + 本步 5 个文件）九项确定性门禁 **G1–G9 全部 rc=0**（`s48step.gates.log`）：originality
      `Total: 191 / Original: 191 / Suspicious: 0`、spec_audit `total_specs 44 / coverage_pct 100.0`、
      golden_audit「all L1 verified commands have real samples (OK)」、reachability「无未登记孤儿 ✓」、
      contract_audit、docs links `82 files`、mypy、ruff check「All checks passed!」、ruff format
      `472 files already formatted`（本步只改既有文件，未新增文件）。离线全量 junit
      **3598 / 0 失败 / 0 错误 / 5 跳过**、150.664s、`SUITE_RC=0`、覆盖率 **81.50%**
      （TOTAL 22406 / 3569 未命中 / 5982 分支 / 1006 部分，`Required test coverage of 77.0% reached`）——
      差额可核对：`3597 + 1 = 3598` 恰是那一项新判据；TOTAL 的 +3 全部落在 `tstdx/runtime/executor.py`
      （321 → 324），而该文件的未命中数 131 一字未变（新增三条语句都被执行到），覆盖率与基线同值。
      本轮 gates 日志与第 47 步那份 **sha256 逐字节相同**（`1ed719240f9827a3174cf0d719be78f1f62173b577f64183562e8703d682ac27`）——
      这句话不是"什么都没改"的证据，九项门禁没有一项读测试计数，记在这里是免得后来人把它当成锚点误读。
    - **ship 轮（提交树复测，同一解释器与工作树参数，不引用步骤轮读数）**：`93934d1` 落到 main 后另起隔离
      工作树 `wt_s48ship`（`git status --porcelain` 实测 0 行）把九项门禁与离线全量重跑一遍：
      `s48ship.gates.log` 与 `s48step.gates.log` **逐字节相同**（两份 sha256 均为
      `1ed719240f9827a3174cf0d719be78f1f62173b577f64183562e8703d682ac27`），`rc=0` 计数 9/9；离线全量
      junit **3598 / 0 失败 / 0 错误 / 5 跳过**、151.968s、`SUITE_RC=0`、覆盖率 **81.50%**
      （TOTAL 22406 / 3569 / 5982 / 1006，阈值未下调；`tstdx/runtime/executor.py` 两边同为 324 条语句 /
      131 条未命中）——与本步树逐格相同，唯一差额是两条日志的耗时（150.664s / 151.968s，同一台机器同一批
      测试的正常抖动）。提交树比步骤树多的只有本条账目与 F-71 ⑥ 那次构造点普查的批注，而读这条账目的判据
      （F 号与小节引用的存在性、死路径对账）在两边同绿。
    - **本步未动**：F-71 的三条路径 (a) 收口删入口 / (b) 承认双入口 / (c) 留入口去掉隐式换源一字未执行，
      `tstdx/web/__init__.py` 那段自相矛盾的 docstring（`:4-7` 说"不做自动换源"、`:15-19` 立刻摆进 Quick start）
      仍然没有任何判据在看；F-70 的 4 份对标文档与"非错误类裸名"判据扩形仍待裁决；F-37 剩余两项 + 依赖它的
      F-38、F-20 的 heartbeat 默认值、F-15 的 CI 覆盖率重钉仍分别卡在实时复跑授权与 CI 环境；
      `v1.1.0-dev.1` 标签仍未打。

49. ✅ **全盘复评轮：主链重新走查一遍，量出五笔新账 F-72…F-76，并另立 v18 复评方案文档（本轮不改代码）（2026-09-20）**

    - **本轮授权与射程**：用户要求「重新梳理主要功能与架构、指出不合理处、判定核心功能与主链是否贯通、
      制定方案存到 docs」。据此**只读取、只测量、只登记**——`tstdx/` 与既有文档一字未动，产出两件：新文档
      `docs/REFACTOR_PLAN_V18_REVIEW.md` 与本节五笔账。已裁决未执行的两步（F-70 (b)、F-71 (c)）不重复登记，
      只在 v18 文档 §4.1 排进执行次序。
    - **基线在同一轮里重新量了一遍**（隔离工作树 `wt_s50review`，detached @ `616d065`，解释器 py3.13.12）：
      九项确定性门禁 **G1–G9 全部 rc=0**（originality `Total: 191 / Original: 191 / Suspicious: 0`、
      spec_audit `total_specs 44 / coverage_pct 100.0`、golden_audit OK、reachability「无未登记孤儿 ✓」、
      contract_audit、docs links `82 files`、mypy、ruff check、ruff format `472 files already formatted`，
      `Temp/review50/gates.log`）；离线全量 junit **3598 / 0 失败 / 0 错误 / 5 跳过**、170.416s、
      `SUITE_RC=0`、覆盖率 **81.50%**（TOTAL 22406 / 3569 / 5982 / 1006，阈值 77 未动）。规模与表面读数：
      11 Provider × 56 channel、`DIRECT_BINDINGS` 251、内核直绑三元组 17、发现面 172 个名字（7 直绑 ∪ 165
      迁移）、`tstdx.web.__all__` 69、`WebQuoteSession` 公开方法 140、`Client` 类体公开名 15
      （`probe_counts.log`）。构造开销实测 `Client()` 5.3 ms、`UnifiedRuntime()` 0.6 ms、`audit_runtime()`
      0.2 ms——每次构造都跑的注册表审计不是热点（`probe_surface.log`）。
    - **主链判定：贯通，但「贯通」不等于「每格都给得出数」**。把 `socket.connect` 与 `getaddrinfo` 全部钉死
      后逐个调那 7 个内核直绑便捷方法（`probe_core7.log`、`probe_quotes.log`）：`bars`/`snapshot`/
      `security_count` 抛 `AllHostsUnreachable`、`security_list` 抛 `CommandOffline`、`minute`/`trades` 抛
      `NotImplementedFeature`（这两格与 `security_list` 的「已下线」口径第 39/47 步已写进文档），
      `quotes` 是唯一一格在 16 次连接全失败之后**照常返回** `data=[]` 且 `warnings=()`。
    - **F-72（本轮最重要的一笔）**：`_t_quotes` 逐只捕获 `TdxError` 塞进 `errors`，不传 `_collect` 的调用方
      收尾时把它写进 `client.last_errors` 实例侧信道后照常返回（`tstdx/client/_mixin.py:326-364`），而内核
      `_tdx_quotes`（`tstdx/runtime/executor.py:503-505`）正是那个不传 `_collect` 的调用方。于是 HTTP
      `/v13/quotes`、WS `quotes`、MCP `get_quotes` 在整体断网时看到的是 200 加空结果，与「该代码不存在」
      同形；F-45 的 `strict` 读的就是 `warnings`，这里无物可严。旁证是 CLI 早就自建了绕行：
      `tstdx/cli/runtime_commands.py:847-856` 在打印「0 条」前专门去读 `last_errors`。离线全量 3598 项
      全绿照不到它——假客户端不会让所有主机同时失败，而 F-52/F-65/F-66 一族判据都只做「声明面 ↔ 执行面」
      对账，不看失败投递方向。
    - **另外四笔**：F-73 活文档 `docs/troubleshooting.md:97` 指令去调已删除对象的 `client.router.last_errors()`
      （全仓 `.router` 零命中），而小写点号调用形状既不是死路径判据认的 `.py`、也不是幻影名判据认的
      CamelCase；F-74 `tstdx/deprecation.py` 206 行退役机制在包内**零消费者**、不在 `tstdx.__all__` 的 45 个
      名字里，只被测试钉着；F-75 三个恒定抛错的能力仍占着三面入口，缺的是响应侧「结构化拦截 vs Provider
      故障」的分类而不是文档口径；F-76 167 个能力靠 `__getattr__` 动态出现，`dir()` 与类型检查器都看不见。
    - **v18 文档给每条留了编号决策点与默认值**（§5 的 D1–D6），其中 D6 是次序问题：先把已有裁决的第 50/51 步
      （F-70 (b)、F-71 (c)）做完，还是先做 P0 的 F-72——默认前者，因为 F-72 的判据要复用 F-71 收口后的
      会话面。**未新增任何门禁**：五笔账的判据扩形都挂在各自决策之后，不在裁决前预建。
    - **本步未动**：`tstdx/` 一字未改；既有文档一字未改；F-70/F-71 的执行、F-37 余条与实时复跑、F-20、
      F-15 的 CI 覆盖率重钉、`v1.1.0-dev.1` 标签，全部照旧挂着。

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
   **2026-09-19 第 43 步追记**：这份文档的五类抄本（键清单 / 默认值 / 取值范围 / fail-closed 消息 /
   环境变量规则）已从「人读」升级为与 `schema.py` + `loader.py` 的**机器双向对账**，判据在
   `tests/architecture/test_config_doc_contract.py`；同一轮把 `TSTDX_*` 的判据从「两份抄本互相印证」扩成
   「代码读取 ⇒ 必须有归属」，当场量出并清偿 F-69（`TSTDX_WENCAI_COOKIE` 未登记，照本库指引设置它反而
   让 `Client()` fail closed）。抄本从此改一处不联动即红。
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
- [x] 根级 `.py` ≤ 10（**实测 11**：第 26 步新增唯一告警发射口 `diagnostics.py`，见本条追记）；
      `execution/` DAG 与 `provider/` v14 适配器目录已不存在
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
- [ ] 发布 `v1.1.0-dev.1` tag：⏳ **仍待用户明确确认**。本行原本还挂着"另外四项待裁决"
      （F-37 / F-18 / F-44 / F-47），到第 47 步为止那四项的**裁决已全部下达并执行**：F-47 按 (a) 由第 40 步
      清偿、F-44 的 (a) 由第 41 步落地（其剩余叶子按 F-68 由第 44 步删掉 4 个零站点者）、F-18 按 (b) 由第 45
      步物理删除链外守卫、F-37 按 (c) 由第 39 步下调能力声称——**登记史不抹**，逐条读数见 §1.5 对应步骤。
      今天真正还卡在 tag 之前的是这些：**F-37 的剩余半边**（`0x000F`/`0x0010` 的解码布局仍未闭合，且工作日
      盘中复跑——stream 0 帧与字段错位两项——需要实时复跑的授权）、**随它一起挂着的 F-38**（F-37 未落地前
      补 live 断言等于把每日 live job 钉成固定红）、**F-20**（改默认探活码是真实网络行为变化，须真机验证）、
      **F-15 的覆盖率重钉**（阈值只准在 CI 环境的 exact-head 读数上调整，本机读数不算）、**F-70**
      （4 份对标/审计文档的现时态口径，三条路径仍待裁决）、**F-71**（`tstdx.web` 作为第二条公开数据入口，
      与"唯一业务入口"的口径正面冲突，三条路径待裁决——它不卡主链验证，但打 tag 会把这条矛盾固化进发布说明）。
