# tstdx V19 架构复核与重构方案（第 27 轮起草）

> **定位**：本文是从第 27 轮起的**方案与决策账**。`docs/REFACTOR_PLAN_V18_RESTRUCTURE.md`（4 232 行、
> 26 轮执行记录）**不再追加**——它已经是 16 个判据文件的解析输入，继续往里写字等于让方案本身
> 变成判据的脆弱面。V18 台账保留为历史证据，本文只写"下一步做什么、按什么口径判定做完"。
>
> **口径**：本文每个数字都是 2026-09-26 在 `07477e8` 工作树上**现场读出**的（读法写在 §0），
> 没有一个来自记忆或上一轮台账。凡是本轮没能证实的怀疑，一律进 §5 的"更正/不采信"清单，
> 不写成结论。

## 0. 读数怎么复跑

| 读数 | 口径 |
|---|---|
| 规模（文件数 / 行数） | `find <dir> -name '*.py' \| xargs wc -l`，排除 `__pycache__` |
| capability / Provider / 子命令 / 路由 / MCP 工具数 | 现读 `tests/architecture/test_doc_code_consistency.py::_actual_facts()` |
| 协议账本行数、族分布、状态分布 | `tstdx.protocol.commands.COMMANDS` 现场计数 |
| 执行绑定条数 | `len(tstdx.runtime.executor.DIRECT_BINDINGS)` |
| Web 源数 | `len(tstdx.web.sources.KNOWN_SOURCES)` |
| CLI 子命令清单 | 「CLI 的 `--help` 里那 31 项 choices」现列 |
| 错误码唯一性 | 遍历 `tstdx.errors` 全部 `TdxError` 子类，按 `code` 聚合 |
| 门禁分母 | `tests/support/gate_inventory.py`（`gates_targets()` / `ci_job_keys()` / `ci_check_cells()`），名单唯一解析处 |

本轮实测值：`tstdx/` **190 文件 / 59 996 行**；`tests/` **275 文件 / 57 730 行**（判据:产品 = **0.96 : 1**）；
`tests/architecture/` **33 文件 / 14 074 行 / 346 个测试函数**；其中把 markdown 当输入的判据
**16 文件 / 7 457 行 / 170 个测试函数**，被扫的活文档面 = `active_docs()` 的 **38 个文件**。
协议账本 **85 行**（族：quotation 39 / ex_quotation 17 / mac_quotation 16 / goods 11 / f10 2；
实测状态：online 74 / offline 9 / degraded 2），`DIRECT_BINDINGS` **251** 条，
capability **172**，Provider **11**，Web 源 **31**，CLI 子命令 **31**，HTTP 路由 **10**，MCP 工具 **9**，
异常类 **41** 个 / 码 **40** 个。

## 1. 主要功能：六张面

1. **库面**：`tstdx/__init__.py` 懒加载 `__all__`，业务唯一入口 `tstdx.client.api.Client` / `AsyncClient`。
2. **CLI 面**：31 子命令 / 36 叶（`version…quotes-snapshot`），`tstdx/cli/parser.py` 是名单唯一来源。
3. **HTTP 面**：10 条 `/v13/*` 路由（`tstdx/integration/runtime_http.py`），`create_runtime_app(client=None)`。
4. **WS 面**：10 个 JSON-RPC 方法（`tstdx/integration/runtime_ws.py`），`serve_runtime_ws(handler=None)`。
5. **MCP 面**：9 个工具（`tstdx/integration/mcp/`），`MCPServer(client=None)`。
6. **数据源面**：TDX 协议（85 命令账本）+ Web 31 源 + 本地 `reader/` + `derived` 聚合。

三张服务面的 `Client` 所有权是同一口径（不传则工厂自造、按 `owns_client` / `owns_handler` /
`_owns_client` 归零），该表由 `test_service_plane_client_ownership_table_matches_the_code` 钉住。

## 2. 架构与唯一主链（本轮逐跳实测）

```
四张服务面（CLI / HTTP / WS / MCP）—— 只翻译，不执行
      ▼
Client / AsyncClient（client/api.py:255 起，__getattr__ 只为已登记 capability 造包装）
      ▼
QueryPlanner.compile（query.py:612）→ QuerySpec.normalized（symbols 归一、选项拒绝表、
      provider 必填、单通道一致性）→ ExecutionBudget（query.py:82，deadline 由 ensure_remaining 读）
      ▼
UnifiedRuntime（runtime/kernel.py，零缓存）
      ▼
DirectProviderExecutor（runtime/executor.py:45，DIRECT_BINDINGS 三元组精确绑定，251 条）
      ▼
providers 注册表（Provider×Channel×Capability 单一事实源，11 个 Provider）
  ├─ tdx      → protocol/registry.dispatch（L1 精确解析 → L2 通用 → L3 原始透传）
  │             → codec/framing（magic 与头长单一来源）→ transport/pool（令牌桶 / 退避≤8 s / 熔断 / 代际）
  ├─ 四家 web   → web/（31 个 SourceSpec）
  ├─ local_vipdoc → reader/
  └─ derived   → 显式聚合
      ▼
QueryResult + meta.provenance + meta.warnings（诊断出口 diagnostics 发射器，contextvar sink）
      ▼
跨源：仅限显式 FallbackPolicy → runtime/orchestration.py（唯一通道），耗尽抛 AllSourcesExhausted
流式：StreamSpec/StreamPlanner → streaming/（退订 API 面向调用方，由调用方持有订阅）
```

主链判定：**贯通**。四张服务面都落到同一个 `Client`；不存在静默跨源降级（回退只在显式策略里，
且带 `provenance.fallback` 与 `attempts`）；启动三方对账 `audit_runtime()` 本轮现场可跑并返回报告。

## 3. 核心功能是否全部实现：判定 + 四条边界

主体功能已实现。以下四条是"声明在、但用户拿不到"的边界，它们是本方案的输入而不是猜测：

- **B-1 传输调优面只有 6/17 可达**：`ConnectionPool.__init__`（`transport/pool.py:241-265`）收 17 个参数，
  `pool_settings_from_config`（同文件 `:71-94`）只翻译 6 个（`slots_per_host`/`timeout`/`heartbeat_interval`/
  `max_retries`/`rate_limit`/`use_tls`）。其余 **9 个**（`connect_timeout`、`idle_timeout`、`keepalive`、
  `heartbeat_cmd`、`handshake`、`handshake_strict`、`spec`、`on_host_down`、`speedtest_threshold`）在
  `tstdx/` 生产树里**零构造点**，`docs/configuration.md:59-109` 的键清单也不含它们 ⇒ 用户无论怎么写
  配置文件都改不到。而 `docs/ARCHITECTURE.md:44-47` 把"配置面即执行面契约"写成核心不变量。
- **B-2 两张传输链的熔断状态机各写一份（本轮已按 D-4 钉住一致性）**：同步 `transport/pool.py:557-602`
  与异步 `transport/async_.py:633-675` 都实现了"OPEN 冷却 → HALF_OPEN 单探针 → 归还令牌不伪造健康"的
  同一套语义，本轮逐行比对**语义仍一致**，但只共享 `retry_backoff_delay` 一个函数。
  第 25/26 轮那类修复（G38 退避封顶、F-92/F-94 熔断复位）都要人肉打两遍——这是漂移的入口。
  本步（A4）的处置：`tests/transport/test_pool_circuit_parity.py` 用脚本时钟把同一串事件喂两张链，
  逐项比对运行期健康轨迹（熔断态 / 探测令牌 / 三类计数 / 加权值 / 错误类型 / OPEN 距今几个冷却），
  并单独钉住四个常数同源。**实现仍未合并**（D-4：合并要重写两张池，风险直接进传输主链）。
- **B-3 门禁分母三套不同源（本轮已按 D-5 收口）**：起草本文时引用过的"17 道门禁"是 V18 台账连用
  5 轮的**本机 scratch runner 的步数**（`gates26_round.py:46-62`，其中 ruff 算两次）；`make gates`
  是 **11 个目标**；CI 一次运行展开 **16 个 check 格**（11 个作业里 `test` 按矩阵占 6 格）。
  ⇒ 三个数没有一个由判据拥有，"绿"的可核对性取决于读者手上那份 runner。
  本步（A3）的处置：唯一解析处 `tests/support/gate_inventory.py` + 两条判据
  （`test_local_gate_contract.py::test_make_gates_and_ci_jobs_are_the_same_check_set`、
  `::test_the_documented_gate_denominators_are_computed_not_copied`）。自本步起
  **发布口径只有 `make gates` 的 11 个目标**，且它必须与 CI 的 11 个作业按键一一对应；
  runner 的步数只在记录实测时称作"本轮 runner 的 N 步"，不再称"全套门禁"。
- **B-4 两张 CLI 叶子是恒失败面**：`list` 与 `blocks` 走直连传输层（`cli/runtime_commands.py:6-12` 自述
  绕开内核），其命令 `0x044D SECURITY_LIST` / `0x07E5 BLOCK_QUOTES` 实测 `offline`，本轮现场跑
  两次都在发包前 fail-fast（`E3035`，退出码 2）。文档已经诚实写明"不服务"，所以这不是失真，
  而是**产品决策问题**：要不要继续把两条只能报错的命令放在出厂面上。

## 4. 不合理清单（分级，附证据）

### P1（会让用户按文档做事却拿不到行为）

| 编号 | 事实 | 证据 |
|---|---|---|
| P1-A | B-1 的 9 个不可达传输参数 → **本轮 A1 已建参数可达性账**（`test_pool_knob_reachability.py`），文档侧口径见 `docs/configuration.md` 的「传输池参数：哪些**不**经配置面」 | `transport/pool.py:71-94`（6 键）vs `:241-265`（17 参）；`docs/configuration.md:59-109` |
| P1-B | B-2 的两份熔断状态机无一致性判据 → **本轮 A4 已建对拍判据**（行为一致，实现仍不合并） | `pool.py:557-602` ↔ `async_.py:633-675`；`tests/transport/test_pool_circuit_parity.py` |
| P1-C | B-3 的三套门禁分母，台账口径无判据 → **本轮 A3 已建判据**（11 目标升为唯一发布口径） | `Makefile:101`、`tests/support/gate_inventory.py`、`test_local_gate_contract.py` 末两条、runner `gates26_round.py:46-62` |

### P2（结构债：读起来像事实，实际没人对账）

| 编号 | 事实 | 证据 |
|---|---|---|
| P2-A | 心跳主张（V17 账本 F-20）→ **第 28 轮已清偿**。原主张：账本 `0x0004 HEARTBEAT` 标 `verified=True` 而全仓无发送方；真心跳 `DEFAULT_HEARTBEAT_CMD = 0x0002` 在标准族根本没有登记行（`0x0002` 只作 F10 族的 `F10_TEXT` 存在），`get_parser(0x0002,'quotation')` 与 `get_parser(0x0004,'quotation')` 现读均为 `None`；spec 的免解析器豁免由"响应体为空"这句话买来。**真机实测（2026-09-26，标准族默认主站池 10 台：7 台可达、3 台 TCP 超时；逐台空体请求，7 台答得完全一致）**：`0x0004` 回 10 字节 `0000000000003c283501`（回显 method+seq，末 4 字节按小端 u32 读为日期形状整数 `20260924`，布局未锁定）；`0x0002` 回 50 字节、含 GBK 文本「上交所公告」；备用号 `0x0015` 回 127–136 字节压缩帧。⇒ 三句旧散文：spec 的"响应体同样为空"是**假的**；`transport/base.py` 的"服务端对未知命令通常回一个短帧，故 0x0002 是刻意选的中性探测码"也是**假的**（0x0002 有响应、50 字节、且与 F10 族那条文本命令同形）；只有"该命令目前无发送方"是**真的**。处置按默认决定 D-30：**不翻转** `DEFAULT_HEARTBEAT_CMD`（两条都答，换码是零收益的真实网络行为变化）、**不**把 0x0002 塞进标准族账本（语义未核对，且 85 行分母无端扩大）。动的是主张面：spec 补真机 `measured` 块 + 显式 `response.parse: false`，**豁免依据从"响应看起来为空"换成"spec 主动认领不解析"**（`spec_audit.is_payloadless` 现在读这个键），`transport/base.py` 的注释换成实测事实，`docs/configuration.md` 的 `heartbeat_cmd` 行同步，`0x000D` 一并补声明（其散文早已写明响应读取后丢弃）。判据：`tests/protocol/test_heartbeat_claim_evidence.py`（探活码单一真相源 / 线上码 ≠ 账本 HEARTBEAT / `verified` 由实测块兑现）与 `tests/test_spec_coverage.py`（豁免两条向核对、`measured` 自洽） | 探针原始日志与变异读数**本会话重取过一次**：第一遍的 `scratch_v19a28/f20/heartbeat_probe.log`、`f20/tests_run1.out` 与 `Temp/audit_spec28_mutation.out` 在 2026-09-26 18:01 随 `scratch_v19a28/` 一起被外部清掉（本会话未执行任何删除），真机字节本身已进仓在 `PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml` 与 `0x000D_HANDSHAKE.yaml` 的 `measured` 块里。变异正控按同口径重跑（`Temp/audit_spec28_mutation_re.out`）：基线 `rc=0 / Control frames: 2 / Coverage: 100.0%`；摘掉 `0x000D` 的 `parse: false` ⇒ **rc=1**、`Control frames: 1`、`Coverage: 97.7%`、点名 `Uncovered: 0x000D handshake`；摘掉 `0x0004` 的同一行 ⇒ 同形状红并点名 `0x0004 heartbeat`；两次都按 sha256 还原并复核。判据那一半未受影响：`tests/protocol/test_heartbeat_claim_evidence.py` 与 `tests/test_spec_coverage.py` 常驻在仓 |
| P2-B | 死属性：`Command.port`（`protocol/commands.py:84-91`）**生产树零读者**——族→端口的真源在 `transport/hosts.py`；`Command.hex` 只有 `tests/unit/test_commands.py:46` 一个读者。F-65 曾用同一口径删掉 `stats()`/`get_command_by_name()`，但那次只清函数、没清 dataclass 成员。**本轮 A2 已删，并把成员面锁进 `test_ledger_public_surface.py`；真相源换见 §5-6** | 本轮全仓 `.port` 归属核对（其余命中都在 HostEntry/Slot/SpeedtestResult 上） |
| P2-C | 判据层自指：16 个判据 / 7 457 行解析 markdown；同一知识 2–3 份手抄名单（WS 方法 ×3：`test_face_exposure_projection.py:155-164` vs `runtime_ws.py:30-41` vs `test_wire_declared_fields.py:247`；CLI 命令 ×3；周期 ×4；Web 源 ×3；capability 数在 `test_doc_code_consistency.py:820/827/835` 钉三次）⇒ 改一句散文要重开三个门禁 | 本轮 `wc -l` + 逐处 file:line |
| P2-D | 只被测试/工具/豁免名单消费的生产目录：`charset/` 468 行、`output/` 305 行、`transport/sniff.py` 417 行、`profile/` 1 014 行（`feedback/`、`observability/` **不是**——它们有真实生产消费者，本轮核对过）；`docs/ARCHITECTURE.md:53` 仍称 charset「独立完备」，而快照日期是 2026-09-19（`docs/ARCHITECTURE.md:3`），早于最近若干轮改动 | `scripts/_reach_allow.txt`、`audit_reachability.py` 现跑「无未登记孤儿 ✓」 |
| P2-E | **spec 的 `context:` 整块没有机器读取点**（第 28 轮第 1 遍登记，F-20 的余波）。实测三条：① 44 份非探测 spec **全部**带 `context:` 块；按"每行开头的结构键"扫出的键去重 **31 个**（其中 3 个是多行标量内部被 `Xxx:` 形状误收的碎片，见日志），**14 个**在 `tstdx/` + `scripts/` + `tests/` 全文里一次都没出现过（`applicable_families`/`buy_requires_funds`/`category_variants`/`datetime_format`/`diff_base`/`header_layout`/`is_backup_command`/`known_limitation`/`lot_size`/`max_batch`/`password_scheme`/`requires_handshake`/`single_symbol_only`/`single_value`），另有三个写在 flow 一行的键（`interval_seconds`/`max_missed`/`timeout_seconds`）逐词 grep 同样零命中；② AST 扫 `tstdx/**` 与 `scripts/**` 里对 `*spec*` 变量的下标与 `.get()` 取值，机器实际读取的键只有 **8 个**（`spec_id`/`name`/`family`/`status`/`description`/`response`/`golden_samples`/`_file`），`codegen.py` 与 `spec_audit.py` 都不碰 `context`；③ 这条不是纸面洁癖——`0x0004` 那一格的 `max_missed: 3` 与 `timeout_seconds: 10` 读起来像已实现的政策，而本包既没有漏拍计数器也没有心跳专用超时（探测异常交给池的熔断/故障转移），同一格 notes 还把间隔指向 `transport/base.py`，真源其实是 `[core] heartbeat_interval`。**本轮只改说谎的那一格**（`PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml` 的 `context.notes` 现在逐条指向有读取点的旋钮），其余 43 份原样保留、不宣称已修 | 现扫 `Temp/p2e_census28_re.out`（44/44 带 context、31 键、14 键零命中；8 个机器读取键逐键点名）。**这条账本会话重取过一次**：第一遍的读数原本写成"25 键 / 16 零命中"，其扫描脚本 `context_census28.py` 与两份 `.out` 在 2026-09-26 18:01 随 `scratch_v19a28/` 一起被外部清掉（本会话未执行任何删除）；按"数字必须同轮可复跑"的口径，旧读数就地作废，新读数的**取键规则写进了脚本注释**（行首结构键，冒号后的散文不算）——两次读数差在规则而不在事实：机器读取键那一格 8 个名字逐字相同。留给后续轮次的两条路，都要先有判据：(a) 给 `context` 一个读取点（把 SCHEMA.md 声明的机器读取键 ⇄ AST 现扫钉成两向相等）；(b) 按 D3 口径在 SCHEMA.md 明写"纯描述、不据以实现"，并把带行为语义的数字搬进注释 |
| P2-F | **`AsyncQuoteStream.stop()` 的停机等待没有任何尺子在量**（第 28 轮第 2 遍登记，判据七的自曝范围）。现读三件事：① `tstdx/streaming/base.py:510-518` 的收尾是 `await asyncio.shield(task)`，而 `tests/architecture/test_resource_lifecycle_gates.py` 的第七格只覆盖闭合名单 `_SHUTDOWN_FUNCS`（`close`/`__aexit__`/`_drop`/`_sweep_idle`/`_cleanup_committed_close`/`_await_cleanup_before_cancellation` 六个名字，`stop` 不在其中）——也就是说这条停机路径**在判据七的射程之外**，判据七的 docstring 里那句"`AsyncQuoteStream.stop()` 的 shield 不在射程内"就是这条登记的落点，不在这里偷偷放行；② 被 shield 的任务此刻很可能正卡在 `:546` 的 `await asyncio.to_thread(self._get_runtime().quotes, ...)` 上，`to_thread` 不可取消，所以 shield 等的不是"一个 await 边界"而是"一次同步取数跑完"；③ 它**有上界但不是小上界**：那次取数由池的 socket 超时与逐台故障转移兜住，之后 `_run` 在循环顶部读到 `_stop` 才退出，中间最坏还要睡一次 `ReconnectPolicy.next_delay()`——`base.py:448` 现读 `cap=30.0`。⇒ 结论：这不是死循环、也不是无界等待，但 `stop()` 在故障转移场景下可以阻塞到"一次取数超时 × 台数 + 30 秒"。**本轮只登记不修**（改法已想清楚：把 `stop` 加进 `_SHUTDOWN_FUNCS`，再要么给 shield 套 `wait_for`、要么在登记表里逐字写明上界，两者都要先有真机或桩件测出的那个上界，不能凭感觉填一个数） | `tstdx/streaming/base.py:510-518`（shield）、`:539-547`（不可取消的 `to_thread` 取数）、`:448`（`ReconnectPolicy(base=1.0, cap=30.0)`）；`tests/architecture/test_resource_lifecycle_gates.py:721-730`（名单闭合、含"名单必须对得上包里真实异步函数"的自证）、`:33-37`（本条射程声明）。**⇒ 第 29 轮已清偿**：按"两者都要有真机或桩件测出的上界"的要求走的是**第二支**——① `stop` 进入 `_SHUTDOWN_FUNCS`，`UNBOUNDED_SHUTDOWN_AWAITS` 新增 `tstdx/streaming/base.py::AsyncQuoteStream.stop` 一条，并把上界逐刻度写在登记行里（socket 超时 × 候选主站数 + `ReconnectPolicy.next_delay()` 的 `cap=30.0`）；② 收尾本身从"`suppress(CancelledError)` 吞掉调用方取消"改成与 `_await_cleanup_before_cancellation` 同口径的**先排空、再原样抛回取消**（worker 仍活着时不再清句柄、不再关它正在用的 owned runtime）；③ `AsyncStatefulQuoteStream.stop` 加 `try/finally` 让取消传播时状态机照常进 CLOSED，并删掉 `_run` finally 里"任务在自己的 finally 里读自己的 `done()`"这个不可满足的死分支；④ 判据：`test_loop_termination_gates` 的 `TERMINATION_EXEMPTIONS` 收录 `stop`，`tests/runtime/test_stream_state.py` 新增收尾次序（`poll`→`poll_done`→`closed`）与"取消不被吞 + 终止态幂等"两条行为判据 |

### P3（已登记、待业主裁决，本轮不重复发明）

- **G39**：`RetryAdvice.max_retries` 声明了但没有执行者读它（第 25 轮登记）→ **第 28 轮已清偿**，
  按默认决定 D-28 走"删而不接"：字段、`to_dict()` 键、16 处构造实参、反馈载荷那一格全部物理
  删除，`RetryAdvice` 现为六个字段。口径与判据见 `docs/errors.md` §二 与
  `tests/architecture/test_advice_field_actors.py`（该文件同轮补了盲区：文档表行 ⇄ 字段名单
  两向相等，删一格必须同时删一行）。
- **G32**：CLI 真机口径"点名"≠"跑过"（第 23 轮登记，判据⑨ 只补了前一半）。
- **G43**（第 28 轮登记，**唯一的发布阻断项**）：`make gates` 的 `test` 目标在候选树与主树同轮
  各红一次，红点是 `test_new_pointers_must_be_lawful_additions_not_pre_declared_exemptions`
  报 `['wt_v18b26step']`。现扫事实：`P:/github_public/tstdx_wt_v18b26step` 已不在盘上（2026-09-26
  18:01 前后随另一批现场一起消失，本会话未执行删除），而 V18 台账有 **4 处**以现时语气引用它
  （`docs/REFACTOR_PLAN_V18_RESTRUCTURE.md:151/4128/4185/4187`），四处都没有「已回收」字样。
  判据只给两条出路：那棵树在盘上，或就地按「已回收 + 解得开的锚」登记。**本轮两条都不做**：
  前一条要么造一棵空目录冒充测量现场（假绿，G14），后一条要改的是并行会话拥有的文件。
  ⇒ 归属清楚：这是 V18 台账那一轮自己的收尾账，处置动作 = 由该台账的所有者在那 4 格里补登记
  （或恢复现场）。本轮把它写成 §9 教训 2 与这条 G43，**并在收口报告里按"发布条件未达成"报**，
  不用"其余 11 步都绿"把它糊过去。
  **⇒ 第 30 轮复测：阻塞已解除，但不是本轮解除的。** 处置动作落在并行会话那批提交里
  （`docs/REFACTOR_PLAN_V18_RESTRUCTURE.md` 的引用格现在写着「该候选树已于本轮收口后回收
  （本机已无），留存锚 = 提交 `07477e8`」），本轮只是把它复测到绿：候选树 `test` 步
  **4064 passed / 0 failed**、主树同轮单跑 `tests/architecture/test_evidence_pointers.py`
  **7 passed, 2 skipped**（`Temp/gates30_candidate.log` 与本轮控制台）。本条从"发布阻断"
  降级为"已清偿待归档"，`make gates` 的 11 个目标本轮全绿（见 §10 的 30-D 行）。
- **trade 面**：内核零消费者（本轮确证：`tstdx/**` 里除 `tstdx/trade/` 自己以外无 importer；
  `scripts/_reach_allow.txt:24-30` 登记 7 条；`tests/trade/` 6 文件 976 行消费它）。
  `docs/ARCHITECTURE.md:60` 已把它写成"唯一剩余待裁定项"。它与协议账本的 id 重叠是**跨族**、
  由 `(family, cmd)` 键消解，并且已有 `tests/test_spec_coverage.py:141-160` 专门对账——所以
  这是"要不要做成产品"的决策，不是断链。

## 5. 本轮更正 / 不采信（写进方案，防止误传进文档）

1. **「`errors.py` 有 10 个码被两个类共用」——不成立**。现读结果：41 个 `TdxError` 子类 / 40 个码，
   唯一同码是 `E9000`（基类 `TdxError` 与 `InternalError`），属基类默认值，不是缺陷。
2. **「`_ERROR_CODE_MAP` 那张表把 typed 面与非 typed 面映射到不同码」——不成立**：全仓没有这个符号。
3. **「`ResultMeta.source` 写了没人读」——不成立**：`runtime/executor.py:300` 读它选 Web 会话，
   `tools/golden_audit.py:125` 用它分级 origin。
4. **「facade 那批 12 份转发的 `__getattr__`」——现状不成立**：F-11 已把 `web/facade.py` 改成
   `web/session.py` 与 `_session_*.py`，本轮按该名册 grep 为零命中。
5. **不采信"异步池 `_rebuild` 无锁"这条**：本轮没能在两个池文件里找到该符号，若后续要提，先补证据。
6. **A2 的副作用要写进方案，不能只写在提交信息里**：`Command.port` 在 `tstdx/` 生产树里零读取点，
   却不是"没人读"——`tests/architecture/test_doc_code_consistency.py::_family_port` 用它当 README
   协议覆盖矩阵端口列的真相源。删它必须同时换真相源，否则文档判据当场红（本轮实测：删后
   `686 passed` 之前先失败在这条上）。新真相源取 `transport/hosts.py::POOL_BY_FAMILY`——那才是
   连接真的拿去建连的那份端口；族内端口不唯一时判据自曝。
7. **A1 的文档口径先写大了，收口时改回来**：`docs/configuration.md` 那句原本写"名单与**由谁给值**
   由判据与本表双向核对"，而 `test_pool_knob_reachability.py` 双向核对的只有第一列参数名，
   "由谁给值"一列只有 `family` 那格被现读（从 `TdxClient.__init__` 的默认值）。判据不量的格子
   不能让文档说它量——现已改成逐格写明"哪一格有尺子、哪一格只是理由"。
8. **候选树建在哪一层，决定证据尺看得见什么**（本轮第一遍的假红）：
   `tests/architecture/test_evidence_pointers.py` 的"新增树名必须本机存在"是从**正在量的那棵树**
   的位置出发判断的（仓内 `wt_*` ∪ 同级 `tstdx_wt_*`，并且故意排掉自己）。本轮第一版候选树建在
   `.../Local/Temp/wt_v19a27step`，于是它看不见 `P:/github_public/` 那一层的第 25/26 轮现场，
   第 26 轮台账里那条 `tstdx_wt_v18b26step` 指针被读成"新增且不在盘上"而报错——**代码没变，
   换的只是测量树的位置**。第 21 轮起的约定（候选树落在 `P:/github_public/tstdx_wt_*`）因此不是
   风格问题：它是这条判据的可见性前提。本轮按约定重建到 `tstdx_wt_v19a27step` 后同一判据转绿，
   未改判据一个字（那文件此刻还躺着并行会话的未提交改动，本轮不动它）。留给台账所有者的后续项：
   该判据的失败信息里应当报出"本机可见的证据树来自哪个父目录"，否则下一轮还会误读成产品缺陷。

## 6. 编号默认决策（业主未逐项回答，按默认执行；每条写推翻成本）

| 编号 | 默认 | 推翻成本 |
|---|---|---|
| D-1 | 方案落在本文档（新建 V19），V18 台账封存不再追加 | 低：把 §7 各阶段改写回 V18 第 27 轮即可，但要重跑 15 个文档判据 |
| D-2 | **不动 `Config` schema**（不新增第 6 段），B-1 走"诚实登记 + 判据盯住差集"，而不是扩配置面 | 中：若改为接线 9 个键，需同时改 `configuration.md` 键账与 `test_config_doc_contract.py` 的段数/键数口径 |
| D-3 | 不接线（配置键）而是先建**参数可达性账**：`__init__` 现读参数集 = 配置可达 ∪ 文档点名 ∪ 内部固定，三者之差必须为空 | 低：判据独立成文件，删掉即可 |
| D-4 | B-2 先做**行为对拍判据**（同一串事件序列喂两张链，要求熔断轨迹相同），**不合并实现**；只有对拍抓出真实分歧才动代码 | 中：合并成一份共享状态机要重写两张池，风险直接进传输主链 |
| D-5 | B-3：把"发布口径"统一到 `make gates` 的 11 目标（唯一被 `test_local_gate_contract.py` 钉住的那套），scratch runner 步数只在台账里称作"本轮 runner 的 N 步"，不再称"全套门禁" | 低：纯口径改写 |
| D-6 | B-4 的 `list`/`blocks` **保留**（文档已诚实），但在 CLI 参考表里加"当前不服务"列并让判据盯住它；真正的替代数据路径（本地 vipdoc / Web 源）在文档里点名 | 低：若决定删除，按 D3 口径删，不留别名 |
| D-7 | `charset`/`output`/`sniff`/`profile` **不删**（有公开 API 承诺或文档承诺），改的是 `docs/ARCHITECTURE.md` 的分层表口径 | 中：删除会撤 `__all__` 承诺，属 clean-break，需单列一轮 |
| D-8 | 版本号继续 `1.1.0`（`v1.1.0` 标签不动），改动收口后重建安装包到新 dist 目录（G23） | 低 |

## 7. 阶段计划

### 阶段 A（第 27 轮，本轮）：把三条"没人对账的账"变成判据

| 步 | 动作 | 完成判据 | 状态 |
|---|---|---|---|
| A1 | P1-A/D-2/D-3：新增 `tests/architecture/test_pool_knob_reachability.py`——现读 `ConnectionPool.__init__` 的 17 个参数，与 `pool_settings_from_config` 的 6 个键、`docs/configuration.md` 新小节点名的 11 个参数三方对账（每个参数恰好一类归宿、两集合不相交、点名参数不得是 schema 字段），`family` 那格另从 `TdxClient.__init__` 现读 | 判据绿且造两次正控（凭空加一个参数名 / 抹掉文档里那一格）各红一次 | ✓ 双正控各红一次后已还原 |
| A2 | P2-B/D3 口径：删 `Command.port`；把 `test_ledger_public_surface.py` 的成员账从"函数名单"扩到 property 面（`DECLARED_COMMAND_MEMBERS = {"hex"}`） | 删除后架构门禁全绿；把 `Command.port` 加回去必须让判据红（正控） | ✓ 正控红一次后已还原；**副作用见 §5-6**：端口真相源改为内置主站池 |
| A3 | P1-C/D-5：台账与文档口径统一为 `make gates` 的 11 目标；本轮起 runner 步数只作"本轮 N 步"表述 | 相关文档判据绿，且 11/11/16 三个数由 `tests/support/gate_inventory.py` 现算 | ✓ 加一个未镜像的 CI 作业 ⇒ 两条判据同时红（正控），workflow 已还原 |
| A4 | P1-B/D-4：建 sync↔async 熔断行为对拍判据（脚本化事件序列，不比时间） | 若对拍抓到分歧→登记 F 号并按 D3/改判流程处置；无分歧则判据本身钉住一致性 | ✓ 现读**无分歧**（两池轨迹逐项相等），因此不动代码；正控=让异步池归还令牌时顺手清零加权值 ⇒ 对拍在第 15 步报出分叉，随后已还原 |
| A5 | 收口：候选树 runner（按新口径报"本轮 N 步"）+ 离线全量 + 重建安装包 + 装包六面复测；本轮记录写进本文 §8 | 全部 `rc=0`，同轮日志名进账 | ✓ `STEPS=12 bad=0`（`gates27_round3.log`，离线全量 **4008 passed / 9 skipped / 15 deselected**、覆盖率 **82.82%**）+ 装包六面 **28 项 OK / 0 失败**（`install27.log` `BAD=0`）。前两遍的 3 格红全是环境而不是代码，见 §5-8 与 §8 的 A5 行 |

### 阶段 B（后续轮）：判据层瘦身（P2-C）

- 目标：把 4 族手抄名单收成"一处真相 + 派生"，并给"文档即判据输入"设规模上限（现值 16 文件 / 7 457 行）。
- 第一步只动最危险的 WS 方法名单（3 份 → 1 份），因为 `runtime_ws.py:30-41` 本来就是它的事实源。
- 不删判据，只删判据里的**手抄副本**；每删一份都要先证明派生口径能红（正控）。

### 阶段 C：分层表与现实对齐（P2-D）

- `docs/ARCHITECTURE.md` 快照日期 2026-09-19 已过期，需按本轮现读重写 §3 分层表的"状态"列，
  并给"仅测试/工具消费者"一个新口径词（不与"活"混用）。
- `trade` 与 `sniff` 的处置留给业主（P3），本方案不替它们决定删除。

### 阶段 D：清偿已登记项

- F-20（P2-A 心跳主张）与 G39 已在第 28 轮按各自登记时的验收口径收口（判据见 §4 P2-A 行与
  §7 G39 那一格）；**G32**（CLI 真机口径"点名"≠"跑过"）仍开，收口前不得宣称发布就绪度提升。

## 8. 第 27 轮执行记录

> 读数环境：本机主工作树（HEAD `07477e8`，工作树里同时躺着两个并行会话的未提交内容），
> 解释器 uv CPython 3.12，`PYTHONPATH` 注入主树 `.venv/Lib/site-packages`，`PYTHONIOENCODING=utf-8`。
> pytest 一律带 `-o "addopts=--strict-markers --import-mode=importlib" --no-cov -p no:cacheprovider -q`
> （`-qq` 会把末行统计一起吞掉，本轮不用）。下面每个数字都是本会话同轮跑出来的，没有一条来自记忆。
>
> A5 那一行的读数来自**另一套现场**：候选树 `P:/github_public/tstdx_wt_v19a27step`（`git worktree add
> --detach` 自 HEAD，再由 `sync27_candidate.py` 覆盖 dirty 名单并镜像删除；`tstdx.__file__` 现场确认
> 落在候选树），`PYTHONPATH` 换成 `主树 .venv/Lib/site-packages;候选树`，ruff / mypy 走
> `.venv/Scripts/*.exe`（`python -m ruff` 在 uv 解释器旁找不到 `ruff.exe`，第一遍两格 lint 就是这么
> 红的）；构建与装包在 `P:/github_public/scratch_v19a27/`（`dist27_pass1/` + `venv27/` +
> `probe27/`），日志名一律带 `27`。候选树必须落在 `P:/github_public/` 这一层，理由见 §5-8。

| 步 | 判据 | 同轮读数 | 正控（改前必须红） |
|---|---|---|---|
| A1 | `tests/architecture/test_pool_knob_reachability.py`：17 参三方对账、文档点名不得是 schema 键、`family` 从 `TdxClient.__init__` 现读、三型人工差集 | 与 `test_config_doc_contract.py`+`test_ledger_public_surface.py` 同跑 **28 passed**；两次正控还原后 **22 passed in 0.68s**；§5-7 那条文档口径改完后再跑 `test_pool_knob_reachability.py`+`test_config_doc_contract.py` **22 passed in 0.90s** | ① 给 `ConnectionPool.__init__` 加 `planted_knob` ⇒ **1 failed**，报告点名 `planted_knob` 并附"规模不符"；② 抹掉 `docs/configuration.md` 的 `idle_timeout` 行 ⇒ **2 failed, 2 passed** |
| A2 | 删 `Command.port`；`test_ledger_public_surface.py` 新增成员面判据（`DECLARED_COMMAND_MEMBERS = {"hex"}`） | `test_ledger_public_surface.py`+`test_doc_code_consistency.py` **144 passed in 7.89s**；`tests/architecture`+`tests/compatibility` **686 passed in 90.11s** | 把 `port` property 加回 `Command` ⇒ **1 failed, 6 passed**（`{'hex','port'} != {'hex'}`），已还原 |
| A3 | `tests/support/gate_inventory.py` + `test_local_gate_contract.py` 末两条（11 目标 ↔ 11 CI 作业，四个分母现算） | 该文件 **11 passed in 0.12s**；现算值 gates=**11**、CI 作业=**11**、`test` 矩阵=**6**、check 格=**16** | 往 `ci.yml` 塞一个本地链路未镜像的作业 ⇒ **2 failed, 9 passed**；workflow 已从备份还原，`git diff` 对该文件干净 |
| A4 | `tests/transport/test_pool_circuit_parity.py`：脚本时钟喂同一串事件、逐项比运行期健康轨迹、四常数同源 | **3 passed in 0.16s**；`tests/transport` 全目录 **413 passed in 22.81s**；现读结论：**两张池语义无分歧**，因此按 D-4 不动实现 | 让异步池 `_release_probe_token` 顺手清零加权值 ⇒ 对拍在第 15 步报出分叉（`8.5` ↔ `0.0`）；已还原，`async_.py` 里该赋值只剩 `_mark_success` 一处 |
| A5 | 候选树 runner（`make gates` 的 11 目标 = 12 步命令）+ 离线全量 + 重建安装包 + 装包六面复测 | `tstdx_wt_v19a27step`（HEAD `07477e8` 快照 + 本树 109 个改动文件覆盖 + 1 处同步删除，`tstdx.__file__` 确认落在候选树）跑三遍：`gates27_round1.log` **`STEPS=12 bad=3`** → `gates27_round2.log` **`bad=0`** → `gates27_round3.log` **`STEPS=12 bad=0`**（末遍按 Makefile 原样用 `-v`，前两遍为压输出用了 `-q`，而 `-q` 下 pytest 末行统计不进日志）。离线全量：`4008 passed, 9 skipped, 15 deselected, 23 warnings in 276.44s`，覆盖率 **82.82%**（阈值 77 已达、未下调）；`test-bridges` **24 passed**；`audit-originality` **191/191**；`audit-reachability` **无未登记孤儿**；`audit-docs` **95 files OK**；ruff `check` **All checks passed** + `format --check` **474 files already formatted**；mypy 无输出。主树同轮复核（§5-7 口径修正之后）：`tests/architecture`+`tests/compatibility` **688 passed in 74.45s**（`main27_archcompat.log`）。构建（`install27.log`）：`[build] rc=0`，现场 `仓库根: P:\github_public\tstdx_wt_v19a27step`、`[校验] canonical typed distribution ✓（version=1.1.0，runtime_files=191）`、`[冒烟] 通过 ✓`，产物进**新建**的 `scratch_v19a27/dist27_pass1/`（G23：不覆盖任何已记录的产物），指纹 wheel 775 863 B `sha256: 020df2b8eef2c0041f9d1646abb1e6d5b48d42446cac20aae9c4875becc8d632`、sdist 1 741 339 B `sha256: d72394386f7d8a4318fa7d47cc6ed4db5e0e7293a0133ed6b3035b3abaaa5e1f`。装包：`uv venv --python 3.12 --seed` → `venv27`（`rc=0`），`pip install wheel[server,web,metrics]` `rc=0`，`pip check` = `No broken requirements found.`。六面探针 `install27_sixface_installed.out` **`rc=0`、28 项 OK / 0 失败**、末行 **`六面贯通复查：全部通过`**；第 `[0]` 行是现场身份（`import tstdx` → `venv27\Lib\site-packages\tstdx\__init__.py`），本轮三格落点在**装好的包**上复现：`ConnectionPool` 17 参 / 配置面 6 键、`Command` 成员面只剩 `hex` 且 `port` 不在、`POOL_BY_FAMILY` 每族端口唯一、四个熔断常数在两池同值；F-117/F-118 两根指标在真正渲染出的 Prometheus 文本里 `0.0 -> 1.0`，F-117 另有一格证明它在驱动线程里也走通（`calls=3`）；六面规模 CLI 31（叶子 36）/ HTTP `/v13` 10 / WS 10 / MCP 9 / web 31 | 两遍假红各是一次环境教训、都不靠改判据通过：① 首遍两步 lint `rc=1` 是 `python -m ruff` 在 uv 解释器旁找 `ruff.exe` 找不到（`FileNotFoundError`），换 `.venv/Scripts/ruff.exe` 后 `rc=0`；② 首遍 `test` 步唯一失败是 `test_new_pointers_must_be_lawful_additions…` 报 `['wt_v18b26step']` 不在盘上——同一判据在主树同轮是绿的，差异只来自候选树建在 `Temp` 而看不见 `P:/github_public/` 那一层证据树，按第 21 轮起的约定把候选树挪到 `tstdx_wt_v19a27step` 后转绿（该判据文件此刻在工作树里带着并行会话第 26 轮的未提交改动，本轮一个字没动它），详见 §5-8 |

A1/A2 各留下一条"删了才知道有人读"的教训，都写进了正文：A2 的 `Command.port` 生产树零读者、
却同时是文档矩阵端口列的真相源（§5-6）；A1 的口径里 `docs/configuration.md` 那张表如果被谁
后来精简掉一格，判据会当场红——这是有意的，但那张表最初写的"名单与由谁给值都由判据双向核对"
说大了（尺子只双向核对第一列参数名，"由谁给值"只有 `family` 那格被现读），§5-7 记的就是这次
自我更正：**文档描述判据时，只能描述判据真的做了的那部分**。

A5 留下的是本轮最贵的一条：一套全绿的门禁读数里，"红"可以完全来自**测量现场的位置**而不是
代码（§5-8）。两遍假红都没有靠改判据或改台账声明绕过，改的是自己的现场布法——这与 G14
"宁跳不假绿"是同一件事的另一面：也不许把环境造成的红记成产品的红。

## 9. 第 28 轮执行记录

> 读数环境：与 §8 同一套（本机主工作树 + 候选树 `P:/github_public/tstdx_wt_v19a28step`，
> uv CPython 3.12，`PYTHONPATH` 注入主树 `.venv/Lib/site-packages` 与候选树，ruff 走
> `.venv/Scripts/ruff.exe`，`PYTHONIOENCODING=utf-8`）。**两处与 §8 不同**，都是本轮现学的：
> ① 日志一律落 `C:/Users/Administrator/AppData/Local/Temp/`——本轮原先落在
> `P:/github_public/scratch_v19a28/{pass1,pass2,pass3,f20}/` 的那批读数连同目录本身在
> 2026-09-26 18:01 前后从本机消失（同一时刻没的还有 `scratch_v19a27/` 与三棵 `tstdx_wt_*`
> 候选树；本轮收口时再 `ls -d P:/github_public/tstdx_wt_*`，那一层**只剩本轮自己这一棵**），
> 本会话没有执行过任何删除动作，因此不再把证据押在那一层；
> ② 变异台账每格先把原件落盘备份再打补丁（见本节末第三条教训）。
> 本节每个数字都出自本会话同轮跑出的日志，没有一条来自记忆或上一轮台账。

| 步 | 判据 | 同轮读数 | 正控（改前必须红） |
|---|---|---|---|
| 28-A | 第 1 遍（断链与孤儿）：G39 按 D-28"删而不接"清偿 ⇒ `tests/architecture/test_advice_field_actors.py` 把字段名单 ⇄ `docs/errors.md` §二 那张表钉成两向相等；F-20（§4 P2-A）心跳主张清偿 ⇒ `tests/protocol/test_heartbeat_claim_evidence.py`（探活码单一真相源 / 线上码 ≠ 账本 HEARTBEAT / `verified` 由实测块兑现）+ `tests/test_spec_coverage.py` 的免解析器豁免改读 `response.parse: false`；P2-E（spec `context:` 无机器读取点）只登记 + 改掉说谎那一格 | `Temp/audit_spec28_mutation_re.out`：基线 `rc=0`、`Control frames: 2`、`Coverage: 100.0%`。`Temp/p2e_census28_re.out`：非探测 spec **44/44** 带 `context:`、末级键去重 **31** 个、其中 **14** 个在 `tstdx/`+`scripts/`+`tests/` 全文零命中、机器实际读取键 **8** 个。G39 那一半随候选树全套门禁复绿（见 28-D 行的 `test` 步） | 摘掉 `PROTOCOL_SPEC/7709/0x000D_HANDSHAKE.yaml` 的 `parse: false` ⇒ **rc=1**、`Control frames: 1`、`Coverage: 97.7%`、点名 `Uncovered: 0x000D handshake`；对 `0x0004_HEARTBEAT.yaml` 做同一刀 ⇒ 同形状红并点名 `0x0004 heartbeat`；两格都按 sha256 还原（`还原=ok`）后基线复绿 `rc=0`。**这两格是重取的**：第一遍的脚本与 `.out` 随 18:01 那次清理一起没了，旧读数（"25 键 / 16 零命中"）在 §4 P2-E 里就地作废 |
| 28-B | 第 2 遍（死循环与无界等待）：`tests/architecture/test_resource_lifecycle_gates.py` 加**第七格**——异步停机路径上每个 `await` 要么自带截止期、要么就地写明等得起，`UNBOUNDED_SHUTDOWN_AWAITS` 登记表与整包现扫**双向**一致；配套 `tests/transport/test_async_close_deadline.py` 四格钉住新上界 `CLOSE_WAIT_SECONDS = 1.0`（`tstdx/transport/async_.py:249`） | 三文件基线 **151 passed in 11.24s**（`Temp/mutate28_ledger.out`）；第七格现扫停机路径 await 计数 `checked` 非空、名单六个动词全在包里解得开 | M6 把 `await asyncio.wait_for(writer.wait_closed(), timeout=CLOSE_WAIT_SECONDS)` 退回裸 `wait_closed()` ⇒ **1 failed in 4.47s**，还原后 sha 与原件一致。**这一格的射程是收窄过的**：整文件跑 M6 时 `pool.close()` 那格不是失败而是**永远不回来**（那格的职责正是"总关停量被上界收住"，把上界摘掉就没有上界），所以 M6 只跑 `-k stalled`——挂死不能当红记 |
| 28-C | 第 3 遍（六面贯通）：WS 承载面 `serve_runtime_ws` 端到端五格（`tests/integration/test_runtime_ws_server_transport.py`：往返、handler 跑在工作线程、非规范路径 1008、通知不回帧、自建 handler 才登记 `server.tstdx_handler`）；`docs/ARCHITECTURE.md` 分层表点名的包内 `*.py` 必须真在装好的包里、名册行必须解得开（`test_doc_code_consistency.py` 的 roster 格）；恒失败面 `tstdx/integration/runtime_tasks.py` 按 D3 物理删除 | 该测试文件 **6 passed in 1.58s**（`Temp/diag28_files.py` 的分文件读数）；roster 两格与三面线名册在候选树门禁里同轮复绿（见 28-D 行） | 同一份 `Temp/mutate28_ledger.out`：**M1**（分层表把一个真实模块名换成幻影）`1 failed`；**M2**（把 `freshness.py` 抹掉）`1 failed`；**M3**（WS 方法散文里改坏一个方法名）`1 failed`；**M4**（MCP 工具数 9→8）`1 failed`；**M5**（`docs/api/README.md` 的 HTTP 面少写一条路由）`1 failed`；**M7**（`to_thread` 改回协程内直调）**2 failed**（线程形状格 + 慢请求不饿循环格同时红）；**M8**（非规范路径不再 1008 关闭）`1 failed`；**M9**（通知也回帧）`1 failed`；**M10**（不登记 `server.tstdx_handler`）`1 failed`。`CASES=10 BAD=0`，全部还原 ok，跑完再取一次基线仍是 `151 passed` |
| 28-D | 收口：候选树把 `make gates` 的 11 个目标（本会话脚本展开成 12 步，`lint` 两条）全跑一遍；从同一棵候选树构建安装包、装进干净 venv 跑六面探针；最后重扫规模 | **门禁**（`Temp/gates28_round3.log`，18:44–18:50）：末行 `STEPS=12 bad=1`。唯一 `rc≠0` 是 `test`：`1 failed, 4041 passed, 9 skipped, 15 deselected, 23 warnings in 302.48s`，失败格 `tests/architecture/test_evidence_pointers.py::test_new_pointers_must_be_lawful_additions_not_pre_declared_exemptions` ⇒ §4 的 **G43**，因在测量现场不在代码。覆盖率行现取（`Temp/gates28_step_test.out`）：`Required test coverage of 77.0% reached. Total coverage: 82.93%`，阈值与 `--strict` 口径一字未动。其余十个目标全绿：`All checks passed!` / `478 files already formatted` / mypy 空输出 / bridges `24 passed` / golden 与 spec 与 adversarial 与 reachability（`无未登记孤儿 ✓`）/ originality `Total: 191 Original: 191 Suspicious: 0` / benchmark smoke `OK: kline, market, vipdoc` / `docs link check OK (95 files)`。**装包**（`scratch_v19a28/probe28/install28.log`）：六步全 `rc=0`、末行 `BAD=0`；构件在 `dist28_pass1/` —— wheel `tstdx-1.1.0-py3-none-any.whl` 777,728 B、sha256 `adf041b1e5f12f16a6a53bd76decb388116299c0aaa8a9e4ffdfbe8d4598e969`，sdist `tstdx-1.1.0.tar.gz` 1,762,510 B、sha256 `df18fee0a3edd107d0a4b7ad5896bd5c61ed543192d0ba8fdb0cbff9bf86c8ef`；装进 `venv28` 后 `pip check` = `No broken requirements found.`，六面探针（`install28_sixface_installed.out`）**46 条 `OK`**、七段面全覆盖（库面 / CLI / HTTP / WS / WS 承载 / MCP / web），末行「六面贯通复查：全部通过」。**规模**（主树现量）：`tstdx/` 190 个 `.py` / 60,030 行，`tests/` 282 / 59,340 行。**最后一次核对**（本行写完之后再同步一次候选树，`sync28_candidate.py` 报 `copied=124 removed=0 skipped=1`，然后跑文档侧四格，`Temp/gates28_docs_final.log` `STEPS=4 bad=1`）：证据尺 `1 failed, 6 passed, 2 skipped in 0.44s`——那一格红的内容仍是 `assert not {'wt_v18b26step'}`，说明本节自己写下的 G43 / P2-F / 28-D 三段文字**没有新增任何红**；`test_doc_code_consistency.py` **141 passed in 7.03s**；本轮新落的三份判据文件（G41 第七格 + 异步关停上界 + WS 承载面）合跑 **17 passed in 10.61s**，四份文件在最终字节上再合跑一次是 **158 passed in 17.44s**（`Temp/docs28_final_counts.out`，141+17=158 与分跑对得上）；`check_docs_links.py` 报 `docs link check OK (95 files)`。附带一条读数口径：这几格的统计行只在 `-v` 下才进日志——`pyproject.toml:103` 的 `addopts` 自带 `-q`，命令行再给一个 `-q` 就是 `-qq`，末行统计会被静默抹掉，只剩 rc=0（Bash 里表现为整段输出为空）。**用户文档补一句**：28-B 那条停机上界此前只活在代码注释与本轮台账里，`docs/api/README.md` 传输层段现在写明"`AsyncTcpConnection.close()` 最多等 1 秒对端确认，超时或报错直接丢弃该传输"——补完之后把候选树再同步一次、整个 `tests/architecture/` 重跑：**1 failed, 566 passed, 2 skipped in 87.76s**（`Temp/docs28_arch_final.out`），那一格失败仍且仍是 G43。装包也按 G23 重来一遍进**新**目录 `dist28_pass2/`：新 wheel 与 pass1 **同尺寸同 sha256**（777,728 B / `adf041b1…4598e969`，日志里 `wheel_pass2==pass1: True` 就是这一格的自证，因为文档不进 wheel），sdist 则换成 1,762,639 B / `68680010f36b7905e127e12ff1ff7bb726a8d25e533e1ad1858c0c53d0561fc2`（文档与判据进 sdist）；新 wheel 重装进 `venv28` 后六面探针再跑一遍，仍然 **46 条 `OK` / `BAD=0` / 「六面贯通复查：全部通过」**（`install28_pass2.log`）。也就是说：台账自己引用的那些行号、文件名与面数，是在它写完之后的那份字节上被读过的 | 这一格不引入新判据，所以它的正控是前两行那 12 格变异（`Temp/mutate28_ledger.out` `CASES=10 BAD=0` + 28-A 那两刀）再加一条**自我更正**：V18 台账在 `docs/REFACTOR_PLAN_V18_RESTRUCTURE.md:144` 与 `:4227` 仍写 G41 是「六条形状判据 / 700 行」，而文件此刻 `wc -l` = **799 行、七条**（第七格由本轮 28-B 加）。那份归并行会话，本轮一个字不动，只把差异写在这里，并记下口径：**引用别人台账里的行数/条数声明之前必须先 `wc -l` 现量**。另有一条环境事实进本行：主树与候选树的 `tstdx/` 用 `diff -r` 比有 **28 个文件"不同"**，`tr -d '\r'` 后逐格 sha 相同 ⇒ git 检出把 LF 换成 CRLF，字节级 `diff -r` 不是内容核对（与 §9 末第三条教训同一个根）。**第 2 遍当时那一趟是 2 红，其日志被同名脚本覆盖，本轮不引它的数字**——在盘上的两份（`gates28_round1.log` 18:17 / `gates28_round3.log` 18:50）都是 `STEPS=12 bad=1`，逐字只差三处计时 |

三条教训，都落在正文而不是提交信息里：

1. **测量现场是可丢的东西，台账不能跟着它说谎**。18:01 那次清理抹掉的不是"几个日志文件"，
   而是 §4 两格正引用的证据。本轮的处理顺序是：先重取（`*_re.out` 两份），再把引用的措辞改成
   事实（`PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml` 里那句"探针日志落在本机 scratch"现在写明
   那份日志已随目录消失、`measured` 块就是唯一落点），最后才换落点。没走"把指针抹掉让判据闭嘴"
   那条路，也没有把旧数字留着不管——G42 的尺子量的是指针，量不到指针后面的字节。
2. **同名的红可以有不同的因**。候选树那一格报 `['wt_v18b26step']`，与第 27 轮 §5-8 那条**长得
   一样**：但那次是候选树建错层看不见现场，这次是现场本身没了（主树同轮复现同一格红，且
   `P:/github_public/tstdx_wt_v18b26step` 已不在盘上）。唯一合法的出路是在 V18 台账里那四处
   引用格上写「已回收 + 解得开的锚」——那份文件归并行会话，本轮一个字不动，改按 §4 之外的
   G43 登记。**发布判定因此要说白：`make gates` 的 11 个目标里 `test` 这一目标此刻是红的，
   发布条件未达成**，而红不在产品代码里。
3. **变异台账中途被杀，会在产品树上留下变异**（本轮实测踩过）：第一版脚本把原件只放在内存里，
   外层进程在 M6 期间被杀掉后，`tstdx/transport/async_.py` 带着被摘掉的 `wait_for` 又活了十几
   分钟——期间候选树全绿、主树同一格红并且**挂死**。修法已进脚本：每格先写落盘备份（路径 +
   原件 sha + 原件字节）、还原成功后删除，开机自检先把残留备份放回去再开始量；`Temp/
   mutate28_ledger.out` 上一版日志的第一行就是这条自检抓到的那份残留。附带一条环境事实：
   `tstdx/integration/runtime_ws_server.py` 在本机检出是 **CRLF** 而 `async_.py` 与文档是 LF，
   多行锚点必须按目标文件自己的行尾重写，否则命中 0 次、整格被静默跳过（M8/M9/M10 第一遍
   就是这么漏掉的）。

## 10. 第 30 轮执行记录

> 读数环境：候选树 `P:/github_public/tstdx_wt_v19a30step`（`git worktree add --detach` 自
> HEAD `715bb79`，再由本会话把这一轮的 9 条改动逐文件覆盖进去、并镜像那处根级删除；覆盖之后
> 候选树的 `git status --porcelain` 与主树**逐条同名**，这是本轮唯一可信的"读的是同一份字节"
> 自证）。日志一律落 `C:/Users/Administrator/AppData/Local/Temp/`、名字带 `30`。§8/§9 那三条
> 口径照用（候选树必须在 `P:/github_public/` 这一层、ruff 走主树 `.venv/Scripts/ruff.exe`、
> `test`/`test-bridges` 用 `-v`）。**本轮新学两条**，写在节末教训 1/2：变异脚本必须按字节读写，
> `Path.replace()` 不是 `str.replace`。本节每个数字都出自本会话同轮落在盘上的日志。

| 步 | 判据 | 同轮读数 | 正控（改前必须红） |
|---|---|---|---|
| 30-A | 第 1 遍（历史归档 + 发行身份）：`AUDIT_AND_BRIDGES.md` 是**唯一**可安全归档的历史文档（现扫：0 条 markdown 链接、只被冻结文件里的散文提到、链接判据不受影响），已 `mv` 进 `docs/archive/` 并按归档阅读规则加顶部归档说明；四本大台账（V17 收口 / V18 评审 / V18 重构 / V20 债）被门禁按**路径**钉住，移动即红 ⇒ 判定不动。普查里露出的不变量成为新判据 `tests/architecture/test_release_identity_and_snapshot_docs.py`（**G44**）四格：`pyproject.toml` 的 `version` ⇄ `tstdx.__version__` 相等、现版本必须有 `docs/releases/v<version>.md` 且其首个标题含同一版本号、活文档门禁射程外的每份根级 `*.md` 必须在前 12 行内自带史料标记、`docs/archive/README.md` 门禁口径表点名的判据文件与常量必须现读解得开 | 该文件 5 格（四判据 + 一格自我更正）随候选树 `test` 步进全量（见 30-D 行的 4064）；发行身份三处现读同一号码 `1.1.0`，`docs/releases/` 在盘上是 `v1.0.0.md` + `v1.1.0.md` | `Temp/mut30g44.out`：**8 格全红、`CELLS=8 bad=0`、每格还原后 sha 与原件一致**，并逐格记下是哪一格接住的 —— M1/M2（两处版本手抄各自分叉）⇒ `test_declared_package_version_is_the_same_number_in_two_places`；M3（删发布页）/M4（标题不含版本号）⇒ `test_declared_version_has_a_reader_facing_release_page`；M5（`DESIGN.md` 头里两处史料标记一起删）/M6（新造一份无标记的根级文档）⇒ `test_root_docs_outside_the_gate_face_declare_their_status`；M7（归档表指向不存在的常量）/M8（把 `archive` 从 `EXCLUDED_PARTS` 摘掉）⇒ `test_archive_readme_contract_names_judges_that_exist`。**M5 第一遍是绿的**：它只替换了第 3 行那句"不是现行方案"，而第 8 行还有"本文按原文留存"——同一判据有两个锚，只打掉一个不算打掉；这一格现在必须两处一起删才算数（与 §9 教训 3 同族：无效变异比没有变异更坏，它会让下一个人以为量过） |
| 30-B | 第 2 遍（断链）：修掉一条**六类判据全都看不见**的库面断链。`QuerySpec` 有两条入参约定——原始载荷（`options["args"]/["kwargs"]`）与语义字段（`symbols`/`period`/`count`/`start`/`adjustment`）——而 migrated capability 的 `_call_payload` 只读前者，两个键都不在时交出去的是 `([], {})`：于是"文档承诺、门面有方法、registry 有绑定、实现有签名"四样齐了的 `client.minute("000001", provider="tencent")` 在**最后一跳**把参数全丢掉。修复落在接缝而不是五个调用点：`tstdx/catalog/capability.py` 新增 `implementation_for()`（一个 binding → 一个实现 callable；`validate_call` 里那八段手抄分支同时换成读它），`tstdx/runtime/executor.py` 新增 `_semantic_call_payload()`（语义字段 → 实现自己的关键字形参，`symbols→symbol` 是唯一一处改名）+ 两条诚实闸（批量代码打到单只实现 ⇒ 拒；语义字段在该实现上没有落脚点 ⇒ 拒，即"幻影旋钮"的反方向）。新判据 `tests/architecture/test_semantic_payload_bridge.py`（**G45**）10 格，其名单不是手抄可达清单：`_semantic_cells()` 从 `audit_direct_bindings()` 筛出真的走 `_migrated_capability` 的三元组，再要求它经 `QueryPlanner` 真编译到同一 key | 受影响面实测恰好 **5 格**（`minute`×{tencent, eastmoney, baidu} + `trades`×{tencent, baidu}），第一格钉这个分母不许缩。真机三源：`tencent` 通，`baidu`/`eastmoney` 撞上游反爬（`AntiSpiderBlocked`/`WebSourceError`），那是外部事实不是本地断链。离线全量（主树，改完后跑）：**4044 passed, 9 skipped, 15 deselected in 187.62s**（`tests/integration` 不在内）⇒ `validate_call` 那八段的收敛没有回归 | `Temp/mut30b_g45.out`（本轮重取）：**M1–M6 全红** —— 语义字段表去掉 `symbols`、改名表指向实现不收的名字、给实现加一个必填形参、把幻影旋钮闸放宽成静默丢字段、让批量代码静默取第一只、让 `implementation_for` 不再认识 `direct_adapter`（宿主类手抄回潮的形状）；每格还原后 sha 一致 |
| 30-C | 第 3 遍（六面贯通 + 用户文档）：把 30-B 修好的那条链在六张面各跑一遍，并把它**此前在文档里就是错的**那些口径改对 | 改动落点：`docs/api/interfaces.md`（§2 `Client` 表里 `minute`/`trades` 两行点名可用源、688 行那条真机口径整句重写、两条可粘贴示例换成 `tstdx minute sh600519 --provider tencent` / `trades sh600519 --provider tencent`）、`docs/cookbook/README.md`（N6 选型表把 tdx 标成"声明但**已下线**"，并补一段"一格 = 一次 HTTP = 一只代码、批量绝不静默降级、Web 源上 `count`/`start` 没有落脚点会 `ValidationError`、上游反爬抛错误类而不是空数组"）、`tstdx/client/api.py`（`minute` docstring 同步，三个错误类名现读自 `tstdx/errors.py`）。两条被改写的示例按原样在**装好的 wheel** 上真跑（`Temp/cli30c_examples.out`）：`rc=0 / 41,681 B`、`rc=0 / 9,477 B`。安装/使用文档的口径普查：README 与 `docs/quickstart.md` 已经写明"本包不在 PyPI 上"与两条真正可用的安装路径，extras 名单与 `pyproject.toml` 同一套（该一致性由第 23 轮的 extras 判据钉着）——剩下的"证明它们真装得动"就是 30-D 那 11 格 | 这一遍不引入新判据，它的正控就是 30-A/30-B 那两本变异台账（合计 14 格全红）+ 30-D 在装好的包上对同一条链的复跑 |
| 30-D | 收口：候选树把 `make gates` 的 11 个目标（本会话脚本展开成 12 步，`lint` 两条）全跑；从**同一棵**候选树构建安装包（G23：全新 `dist30_pass1/`），逐条按文档口径真装进各自全新的 venv，再在装好的包上跑六面；最后现量规模与文档指针 | **门禁**（`Temp/gates30_candidate.log`，12:15–12:20）：末行 **`STEPS=12 bad=0`** —— 11 个目标**本轮全绿**，§4 的 G43 已不在阻塞位。逐步读数：`All checks passed!` / `479 files already formatted` / mypy 空输出 / **`4064 passed, 9 skipped, 15 deselected, 25 warnings in 294.12s`** 且覆盖率行现取 `Required test coverage of 77.0% reached. Total coverage: 83.58%`（阈值 77 一字未动）/ bridges `24 passed in 0.70s` / golden（`0x537 4B<12B x3` 那条 WARN 与第 28 轮同形）/ spec `rc=0` / adversarial `rc=0` / reachability `无未登记孤儿 ✓`（两格 `[allow]` 是登记过的 `trade.simulator` 与 `transport.sniff`）/ originality `Total: 190 Original: 190 Suspicious: 0 External imports: 17` / benchmark `OK: kline, market, vipdoc` / `docs link check OK (96 files)`（比第 28 轮的 95 多一份，正是本轮新落进 `docs/archive/` 的那本）。**构件**（`Temp/build30_pass1.out`，现场 `仓库根: P:\github_public\tstdx_wt_v19a30step`、PEP 517 隔离、`[校验] canonical typed distribution ✓ version=1.1.0, runtime_files=190`、`[冒烟] 通过 ✓`、`twine check` 过）：wheel `tstdx-1.1.0-py3-none-any.whl` **774,739 B** sha256 `6367ade280b3525c5f43e1c93051e2f3753292b8d042d3a1b6bf7fee7293d1fc`、sdist `tstdx-1.1.0.tar.gz` **1,774,929 B** sha256 `db8a5949695d55eeb1e2f3fccdac8f74821f890fd4cbdc6e05306c1833b38ec4`。**装包六面**（`Temp/inst30_faces.out`，**`CHECKS=11 bad=0`**）：身份格现读 `import tstdx` → `Temp\venv30\Lib\site-packages\tstdx\__init__.py`、`tstdx.__version__` ⇄ `importlib.metadata` 同为 `1.1.0`；README 那条 `pip install "tstdx[all] @ file:///…whl"` 与 quickstart 那条 `pip install -e ".[dev]"` 各自 `rc=0`，且可编辑安装**没在候选树留下第 10 条改动**（跑完仍是那 9 条）；库面 `minute=267 / trades=70 / call(minute)=267 / execute(QuerySpec)=267 / async minute=267 / quotes(tdx native)=1`，`trades(count=50)` 在库面是 `ValidationError`；CLI 面 `--help` 2,132 B、`minute 000001 --provider tencent` 38,730 B、`trades …` 8,934 B，三条 `rc=0`；HTTP 面两条 `200`（`267` / `70`）、`count=50` 是 `422 E1010`；WS 面 `minute ok n=267`、`trades ok n=70`；MCP 面 `tools=9`、`get_minute_today n=267`。**规模**（主树现量）：`tstdx/` **189 个 `.py` / 59,559 行**、`tests/` **284 / 59,955 行**；两个 190 与 189 不是矛盾——`scripts/build_package.py:337` 的 `runtime_files` 与 originality 的 `Total` 都把 `tstdx/py.typed` 记作运行期成员，189 `.py` + 1 marker = 190。**终字节复测**（本节写完之后再跑一遍，读的是已落盘的字节）：`Temp/gates30_final.log`（12:44–12:49）末行仍 **`STEPS=12 bad=0`**，全量格 `4064 passed, 9 skipped, 15 deselected, 25 warnings in 298.58s (0:04:58)` + `Total coverage: 83.58%`、`479 files already formatted`、bridges `24 passed in 0.67s`、`docs link check OK (96 files)`——与 12:15 那次只在秒数上不同（294.12 → 298.58，同一棵树、同一套字节）。同轮从**同一棵**候选树重建成全新 `Temp/dist30_pass2/`（`Temp/build30_pass2.out`：`[校验] canonical typed distribution ✓ version=1.1.0, runtime_files=190`、`[冒烟] 通过 ✓`、`twine check` 过），两份构件 sha256 与 `dist30_pass1` **逐字节相同**。这不是"没重构建"：现读 sdist 成员清单，`docs/` 只有 **7** 项（`docs/{adr,api,archive,cookbook,migration,providers}/README.md` + `docs/releases/v1.0.0.md`），本规划案不进任何构件 ⇒ 台账正文的字改动不会动到安装包指纹。再把 `dist30_pass2` 的 wheel 装进全新 `Temp/venv30pass2` 复跑六面（`Temp/inst30_pass2.out`，**`CHECKS=9 bad=0`**；比 30-A/C 那 11 格少两格，是因为这次把"装包命令本身"移出探针、换成一格**候选树脏名集合 == 主树脏名集合**，两边各 10 条、差集为空）：导入落在 `venv30pass2\Lib\site-packages\tstdx\__init__.py`、`tstdx.__version__` ⇄ `importlib.metadata` 同为 `1.1.0`；库面 `minute=267 / trades=70 / call(minute)=267 / execute(QuerySpec)=267 / async minute=267 / quotes(tdx native)=1`，`trades(count=50)` 仍 `ValidationError`；CLI `--help 2132 B` / `minute 38730 B` / `trades 8934 B` 三条 `rc=0`；HTTP `200 267` / `200 70` / `count=50 → 422 E1010`；WS `minute ok n=267` / `trades ok n=70`；MCP `tools=9` / `get_minute_today n=267`——**这一整串数字与 pass1 那次全等** | 本行不新增判据；它的正控是本轮在两棵树上各跑一遍的那 14 格变异，加一条**就地复测**：把 30-B 的语义载荷桥按 M4/M5 的形状"放宽"一次，G45 与全量都拦得住（主树全量 4044 → 候选树 4064，多出的 20 条正是 G44 的 5 + G45 的 10 + 并行会话那 5 条）。**最后一次核对**（读的是台账自己的字节）：文档侧五判据在候选树单跑（`Temp/docs30_close.out`）= `collected 178 items` / **`178 passed in 7.34s`**，射程是 `test_doc_code_consistency.py` + `test_doc_code_examples.py` + `test_config_doc_contract.py` + 本轮的 G44/G45 两本；链接判据（`Temp/docs30_links.out`）= `docs link check OK (96 files)`。这两格在本段落盘并同步进候选树之后又原样复跑了一次，读数不变（改的是散文、不是任何一条被点名的代码事实） |

### 10-1. 文档行号指针订正（本轮现量，不改写别人那一轮的正文）

读者面（`README.md`、`docs/api/*`、`docs/cookbook/*`、`docs/ARCHITECTURE.md`、
`docs/FAQ.md`、`docs/quickstart.md`、`docs/troubleshooting.md`、`docs/errors.md`）里的
`xxx.py:NNN` 形状行号引用 = **0 处**（`Temp/cites30.py` 现扫，19 份现行面文档、13 处裸基名
不计），所以这类漂移不会外泄到用户读到的页面上。本规划案自己（V19）有 16 处解得开的行号
引用，其中 4 处已漂到别处，逐条给出真值；**§2/§4/§9 是别人那一轮写下的正文，本轮一个字不改，
只在这里订正**（同一手法见 §5 与 §9 末的"引用别人台账前先 `wc -l` 现量"）：

| 引用位置 | 它想指的东西 | 本轮现量真值 |
|---|---|---|
| §2 `runtime/executor.py:45` | `DirectProviderExecutor` | 类在 **`:246`**（HEAD `715bb79` 上就已是 `:174`，`:45` 早漂了；本轮 30-B 在同文件加语义载荷桥，把它推到 `:246`） |
| §4 P1-A `transport/pool.py:71-94`（6 键） | `pool_settings_from_config` 的六键 | 函数在 **`:76`**、六个键在 **`:88-93`**（现读名单 = `slots_per_host` / `timeout` / `heartbeat_interval` / `max_retries` / `rate_limiter` / `use_tls`，**"6 键"这个主张本身仍成立**） |
| §4 P1-A `transport/pool.py:241-265`（17 参） | `ConnectionPool.__init__` | 签名在 **`:246`**，`inspect.signature` 现读 **17 个参数**（不含 `self`），主张成立、指针漂了 5 行 |
| §9 28-B `tstdx/transport/async_.py:249` | `CLOSE_WAIT_SECONDS = 1.0` | 常量在 **`:99`**，唯一使用点 `await asyncio.wait_for(writer.wait_closed(), timeout=CLOSE_WAIT_SECONDS)` 在 **`:274`**；`:249` 的现值是 `_connect` 里的一句 `return`。该文件自 `68389b5` 起未再改动 ⇒ 这一格从写下那天起就指错 |

一条口径随之立起来：**写现状的正文可以带行号，带行号就必须能被现读复核**——本轮没有为此
新建判据，因为"指到空行/超出文件行数"可判、"指到的那一行是不是它说的那件事"不可判，
一套只能判前一半的尺子会给人"行号引用已被看着"的错觉（G40 的"不许有没人按它行动的名册"
同一族）。要真治这一类，方向是**符号锚**（`tstdx.runtime.executor.DirectProviderExecutor`）
而不是更细的行号尺；本轮先把话写在这里，登记为开放项（见 §10-2）。

### 10-2. 本轮之后仍开着的事

- **P2-A / P2-E / G32 / B-4 / V20 §5 判据 2**（执行器 ≤420 行 vs 现值）：一字未动，归属与
  处置口径沿用 §4。本轮给 `tstdx/runtime/executor.py` 加了语义载荷桥，这条"该文件太大"的
  账因此**更长了一截**（+72 行），不遮掩。
- **行号锚 vs 符号锚**：上面 10-1 的四处是本轮量到的全部；如果以后要把它做成尺子，
  射程只能是"指针必须解得开且不指到空行"，并且必须先解决裸基名（`pool.py:557` 这种从模块
  上下文写的写法，13 处）到底算不算合法引用。
- **发行身份的下一步**：G44 现在钉的是"三处手抄必须相等"，还没钉"版本要不要往前推"。
  `1.1.0` 这个数字在 web 子包换 import 路径（`68389b5`）之后被复用，而 CHANGELOG 的
  `[Unreleased]` 段落里 1.2.0–1.4.0 那把梯子还挂着——**这是所有者的发布决策，不是断链**，
  本轮按 D 系列默认什么都不改。

三条教训：

1. **变异脚本自己就是一种写操作**：本轮第一版把原件按文本读进内存、改完再按文本写回，
   于是仓库的 CRLF 被归一成 LF。内容 diff 是空的、`git diff` 只留一句
   `LF will be replaced by CRLF`，可 `git status` 会把两份**本轮根本没打算碰**的文件报成脏——
   对"由用户决定暂存什么"的口径，这就是往他们的清单里塞噪声。修法已进脚本：二进制读、
   二进制写、逐字节 `sha` 比对，跑完再 `git status --porcelain` 数一遍条数（本轮跑完仍是
   那 9 条）。这与 §9 教训 3 是同一族：**测量工具的副作用必须被工具自己核对**。
2. **`Path.replace()` 不是 `str.replace`**：`p.replace("\\", "/")` 在 `pathlib` 里是"改名"，
   报的是 `TypeError: takes 2 positional arguments but 3 were given`；本轮的尺子第一遍就死在
   这一格。要字符串替换请写 `p.as_posix()` 或 `str(p).replace(...)`。
3. **"文档承诺、代码齐备"仍然可以是断链**：30-B 那条链上，registry 有绑定、门面有方法、
   实现有签名、文档有例子，六类判据全绿了 20 多轮——因为参数是在执行器最后一跳被丢掉的，
   而那一跳两侧各自都有"自己的判据"（`validate_call` 量的是显式入参，可达性判据量的是
   三元组能不能路由）。**判据要量接缝，不是量接缝两侧**。G45 就是这条教训的形状化落点。

## 11. 第 31 轮执行记录

> 读数环境：候选树 `P:/github_public/tstdx_wt_v19a31step`（`git worktree add --detach` 自
> HEAD `715bb79`，再由本会话把本轮改动逐文件覆盖进去；两棵树的 `git status --porcelain`
> **43 条同名**，`diff` 差集为空，见 `Temp/r31/dirty_main.txt` 与 `dirty_cand.txt`）。
> §8/§9/§10 那三条口径照用（候选树必须在 `P:/github_public/` 这一层、ruff 走主树
> `.venv/Scripts/ruff.exe`、`test`/`test-bridges` 用 `-v`）。**本轮新学两条**写在节末教训 1/2：
> 收口探针自己的口径也要有正控，`@contextmanager` 的记账必须放 `finally`。日志一律落
> `Temp/r31/`、名字带 `31`（31-B 那四份取证早于建目录，落在 `Temp/` 根、名字带 `g47`，
> 本节按盘上真名引用）。**本节每个数字都出自本会话落在盘上的日志。**

| 步 | 判据 | 同轮读数 | 正控（改前必须红） |
|---|---|---|---|
| 31-A | 第 1 遍（断链 + 没人行动的声明）：三处"声明与行动分叉"，全部落在**执行器与目录的接缝**上。① `DirectProviderExecutor` 的 `f10_client` / `ex_client` / `goods_client` / `mac_client` 四条分支构造的是 `TdxClient` 的**子类**，却只传 `timeout=hop`，于是调用方钉住的主站列表与 `[hosts] slots_per_host` / `[core] max_retries` / `[core] heartbeat_interval` / `[rate_limit]` / `[security] use_tls` 在这四条路上当场蒸发（同一个 `Client` 实例、同一份配置，行情主链路生效）；修复后家族构造只有一处 `_pool_client()`。② `MigratedCapabilityBinding.factory` 字段注释写着"空值表示执行器走自己的兜底逻辑"，而执行器收到空值是**报错**（G40 同族的过期散文）。③ `hk_quotes` 的 Provider 注入规则在 `validate_call` 与执行器**各抄一遍**，执行器那份还多一个 `provider in {sina, tencent}` 守卫——两处口径已经不同；现在校验与执行都读 `tstdx.catalog.capability.call_kwargs_for()` | 新尺子 `tests/architecture/test_client_family_transport.py`（**G46**）在候选树 **104 格全绿**（`Temp/r31/mut31d_all.out` 的 baseline 行），分母不是手抄：家族集合从绑定表现扫，`test_client_family_transport.py` 的判据 1–3 逐族量"构造处收到了什么"。`tests/architecture/test_dispatch_targets.py`（G27）加两件（⑦ factory 的三条承诺、⑧ 注入规则唯一处）→ 该文件 **12 格全绿**（同一 baseline 行）。翻译层那把第 27 轮的尺子 `test_pool_knob_reachability.py` 同时复跑 **4 格全绿** | `Temp/r31/mut31d_all.out`（本轮把散在三个脚本里的编号并成**一本**：19 条、候选树落锚、按字节回滚复核 sha256，末行 **`CASES=19 BAD=0`**，每条 `restored=True`）：**M1** 主站位置参换 `None` → G46 `19 failed, 85 passed`、翻译层 4 绿；**M2** f10 分支绕开构造处自己 new → `5 failed, 99 passed`（该族两格读数 + 两格旋钮 + 一格形状，名字逐格可点，见该文件 docstring）；**M3** ex/goods/mac 同形 → `15 failed, 89 passed`；**M4** 构造处收缩成只剩 `timeout` → `19 failed, 85 passed` 且翻译层仍 4 绿（两把尺子射程不重叠）；**M5**（对照条）翻译层删一个键 → **G46 `104 passed` 保持绿**、红的是翻译层（`2 failed, 2 passed`）：这条要求"本尺子不许红"，否则两把尺子的功劳记串；**M6** 家族集合派生永远为空（尺子自身失明）→ `2 failed, 7 passed, 5 skipped`，分母闭合格红；**D1** 执行器再抄一遍注入规则 → dispatch `1 failed, 11 passed`；**D2** 目录层抽掉注入规则 → `2 failed, 10 passed`；**D3** factory 指向不存在的类 → `1 failed, 11 passed`；**D4** 非 `web_adapter` 的行也开始带 factory → `1 failed, 11 passed` |
| 31-B | 第 2 遍（无界等待 / 资源生命周期）：四处"同一份契约两张面一张生效一张不生效"（B-3 族），逐条**先量后改**。**B1**：`ConnectionPool.__init__` 自己起心跳线程，而 `AsyncConnectionPool` 把起跑写在 `start_heartbeat()` 里、只有 `__aenter__` 调它——`AsyncTdxClient` 家族走 `open()`，**shipped 路径上从来没有那条循环**，`idle_timeout` / `heartbeat_interval` 在异步面是幻影旋钮；起跑点搬到池第一次握住真 socket 的地方（`_get_conn_locked`），`async with` 那道门删掉（没有连接就没有可回收的东西，留着只会多出第二个起跑点）。**B2**：`AsyncQuoteStream` 的四处等待是裸 `await asyncio.sleep(...)`，停机信号 `_stop` 是 `threading.Event`（异步侧没法 await），而 `interval>0` 就合法 ⇒ 一次 `interval=3600` 的订阅把 `stop()` 拖成 3600 秒，`stop()` 里那圈 `asyncio.shield` 还把调用方的取消吞掉；新增 `streaming/base.py::_sleep_or_stop`，四条腿全走它，形状对齐同步侧的 `Event.wait`。**B3**：同步 `TcpConnection._recv_exact` 只在每次 recv **之前** `settimeout(self.timeout)`，那是**空档**超时（每 1 字节重新武装），一个逐字节吐数据的对端永远撞不到它，而 32768 是单帧上限、帧内字节数由**对端**写；进循环前算一次 `deadline`、每轮按剩余预算收紧、见底即 `close()` 抛 `ReadTimeout`，三条出口各自把 socket 超时还回去。**B4**：执行器 `tdx_client` 分支用 `with self._tdx_client(hop)`，`__enter__` 落在 `try` 之外 ⇒ 异常路径上构造出来的池和心跳线程没人收尾；新增唯一保护区 `DirectProviderExecutor._client_session()`（构造留在 `with` 表达式里，保护区只管释放） | 取证四份：`Temp/g47_before.out` → `ARMED after a request: False`、`conn reclaimed by sweeper: False`、`HUNG: stop() outlived a 15s wall guard (caller's 1s timeout never honoured)`、`declared timeout=0.4s -> got 24 bytes after 1.166s`、无限滴流那遍 `HUNG: _recv_exact never returned`；`Temp/g47_before2.out` → 修复前后中间态 `ReadTimeout after 0.398s` 与池面 `pool.request -> AllHostsUnreachable after 0.412s`；`Temp/g47_after.out` → `ARMED after a request: True`、`conn reclaimed by sweeper: True`、`stop() returned after 0.031s wall / 0.000s cpu` + `worker task still alive: False` + `owned handle cleared: True`；池面对照 `AllHostsUnreachable after 0.781s/0.786s`（修复前，声明 0.4s ⇒ 预算被越过约 2 倍）。新判据三本：`tests/transport/test_async_sweeper_arm.py`（**G47**，含"起跑调用点全包按函数名现扫、每个起跑函数恰好一处且住在池自己的类里"）、`tests/streaming/test_async_stop_wake.py`（**G49**）、`tests/transport/test_recv_wall_clock_deadline.py`（**G48**，五格：切断跟着声明值走、无限滴流自己收口、两条确定分支各自还回预算、池面生效、正常往返没被误伤、结构上墙钟读数只算一次且算在循环之前）。B4 的形状判据并进了 G46 的第 3 件事（每格用完恰好显式 `close()` 一次，正常路径与抛错路径各量一遍） | B1 的判据自带桩格（"把旧门种回去"当场红）；B4 = 电池 **M7**：`tdx_client` 分支回到 `with self._tdx_client(hop) as client:` → **G46 `22 failed, 82 passed`**（该族 10 格 `released_exactly_once` + 10 格 `raising_still_releases` + 形状格 + 全包射程格一起红——一次改坏把四把尺子全撞翻）。B2/B3 的改前红就是上面 `g47_before.out` 的 `HUNG` 与"预算被越过"两行原文（离线假服务器，不依赖上游） |
| 31-C | 第 3 遍（六面贯通 + 用户/接口文档）：把 G41 的"谁造谁关"口径在**每一张面**上复查，并修掉本轮之前六类判据都看不见的四处。**C2**：`AsyncQuoteStream` **没有** `__aenter__`/`__aexit__`，而它的同步孪生有 ⇒ `async with client.stream(...)` 当场 `AttributeError`，接口文档"AsyncClient 是同名异步镜像"那句在这一格两个面给了不同答案（第 31 轮之前全仓没有一条判据碰过这两对 dunder，同步那半边同样是"声明了没人量"）。**C3**：B4 那次只收了迁移分支三条，核心链路还留着 **9 处** `with self._tdx_client(self._hop_timeout(plan)) as client:` ⇒ 同一份文件对同一个风险给两种答案，全部收进 `_client_session`（执行器现扫 **13 处交接**）。**C4**：按"构造出口 / 保护区"的名字在**整个 `tstdx/`** 的 AST 上重扫，同一形状在 CLI 面还剩 **6 处**（`probe` / `blocks` / `list` / `quotes-snapshot` 四处 `with TdxClient(**conn)`、`goods` / `f10` 两处 `with get_client(...)`）；新增 `tstdx/cli/_common.py::family_client()`，同执行器那道形同协议（只管释放，构造留在 `with` 表达式里——把 kind 传进保护区里再查注册表，返回类型就塌成 `Any`，工厂那五道 `@overload` 等于白设）。**同一遍还露出 CLI 的第二条断链**：`_transport_kwargs` 只手抄 `hosts` 与 `timeout` 两键，其余五个 TOML 键在六支直连命令上蒸发（与 31-A 的 G46 是同一条断链换了张面），而 `pool_settings_from_config` 自己的 docstring 那时就写着"任何手工建池的调用方都走这里"——那句话在 CLI 面上是假的；现在它调的就是内核那一份翻译。**C5**：B1 真的武装起来的心跳/回收线程把两条既有判据撞翻——`test_async_transport_coverage.py` 的 sleep 替身立刻返回、永不被调度，`asyncio.run` 收尾时既切不断它也回不了事件循环（实测整格挂死在 `_heartbeat_loop`），`test_retry_backoff_cap.py` 则把心跳那一轮 `sleep(75.0)` 记成了退避梯子的一格。**C6**：两张池把"这个槽位还是不是当代"的检查写成可跳过（`generation: int \| None = None`，`None` 即放行），而每个调用点手里都握着 `slot.generation` ⇒ 静默放行是默认路径。**C7**：`make type-check` 在装了 `.[all,dev]` 的环境里被 numpy 2.5 的 PEP 695 stub 崩掉（整道门禁以 `[syntax]` 死掉）——CI 的 type-check 作业只装 `.[dev]`，所以这个坑从没在 CI 露过面；`pyproject.toml` 的 `[tool.mypy]` 加 `no_site_packages = true`（写在这一处而不是 Makefile/CI 命令行，是为了让三个入口读同一个口径）。**文档面**：`Client.close()` 那句"释放内核连接"是**过头主张**（实测 `Client()` 之后进程线程数增量 0、内置执行器根本没有 `close()`），改成如实口径并写明它**不**关你传进来的 `runtime`、**不**停止已 `start()` 的 `stream()`；`get_client("std")` 与 `get_client("async")` 两条**照抄即 `ValueError`** 的示例活着使用接口文档里（判据全在调用链上，参数槽不在射程内）；HTTP 面 lifespan 的收尾写在 `yield` 之后而不是 `finally` 里 | 新判据四本 + 两处扩尺：`tests/streaming/test_stream_context_parity.py`（**G50**/G40，8 格）、`tests/runtime/test_runtime_http_client_release.py`（**G51**，3 格：自建客户端关恰好一次、借来的不关、生命周期体抛错仍关）、`tests/runtime/test_close_chain_ownership.py`（G41/G40，**7 格**，把"这条释放链在默认内核上是空转"钉成读数）、`tests/architecture/test_doc_code_consistency.py` 加"文档里出现的 `get_client` kind 集合 ⇄ `_CLIENT_REGISTRY` 键集合**双向相等**"（哨兵字典比键集合，`rate_limiter` 用身份比而不是比属性）。G46 加第 4、5 件事 → 该文件在收口时是 **104 格**；`test_cli_connection_contract.py` 的连接参数格从"断 CLI 手抄的两键"换成"断键集合 == 内核那一份翻译 + `hosts`"，并加六支直连命令的哨兵格。文档落点：`docs/api/interfaces.md`（异步池起跑时刻、`get_client` 五 kind 真值表、`close()` 到底释放什么、`deadline_ms` 不是硬取消、六支直连命令的连接参数与保护区口径）、`docs/cookbook/README.md`、`docs/configuration.md`（`with Client(config=…)` 那句）、`tstdx/client/api.py` 的 `minute` docstring。回归面：`Temp/r31/pass3_aexit.log` **490 passed in 22.43s**、`Temp/r31/aexit_gates.log` **87 passed in 11.05s**、`Temp/r31/pass3_closechain.log` / `pass3_clientsession.log`（执行器与门面全绿）| C2 = 电池 **M12/M13**：去掉 `__aexit__` 的 `await self.stop()` → `2 failed, 6 passed`（`test_the_async_stream_starts_on_enter_and_stops_on_exit` + `test_a_raising_block_still_stops_the_async_stream`）；`__aenter__` 改成 `return self` → `1 failed, 7 passed`（"进块"这件事只剩形状）。C3 = **M15**：核心链路任意一处回到 `with self._tdx_client(...)` → 只红两格（`test_every_client_handoff_sits_behind_the_guarded_session` + 全包射程格，实测报出 `runtime/executor.py:630`），其余 **102 格仍绿**。C4 = **M16**：CLI `blocks` 分支回到 `with get_client(...)` → 只红全包判据 `test_no_family_client_is_handed_to_a_with_anywhere_in_the_package`（实测报出 `cli/runtime_commands.py:751`），其余 **103 格仍绿**：换面即换尺子，这条量的是射程。G41 那四格 = **M9a/M9b/M10/M11** → `2 failed, 5 passed` / `1 failed, 6 passed` / `1 failed, 6 passed` / `1 failed, 6 passed`，红名逐条点名见 `test_close_chain_ownership.py` docstring。G51 的改前红 = `Temp/g47_http_before.out`：`test_the_release_happens_even_when_the_lifespan_body_raises` **1 failed, 2 passed**，`assert 0 == 1 where 0 = <..._SpyClient object>.closes`。C5 没有产品侧变异可种（改的是测试替身），它的正控是"把替身改回立刻返回 → 整格挂死"这一条本轮实测过 |
| 31-D | 收口：候选树跑 `make gates` 的 11 个目标（本会话脚本展开成 12 步，`lint` 两条）；把本轮散在三处的变异编号并成一本电池并跑满；从**同一棵**候选树构建安装包（G23：全新 `Temp/dist31_pass1/`），逐条按文档口径真装进全新 venv，再在装好的包上跑六张面 + 两条保护区；最后现读两棵树的脏名集合 | **门禁**（`Temp/r31/gates31_candidate.log`，18:42:47–18:48:24）末行 **`STEPS=12 bad=0`**、`dirty-after-gates=43`。逐步：`All checks passed!` / `486 files already formatted` / mypy 空输出 / **`4219 passed, 6 skipped, 15 deselected, 26 warnings in 312.43s (0:05:12)`** 且覆盖率行现取 `TOTAL 22392 3060 6070 1004 84%`（阈值 77 一字未动）/ bridges `24 passed in 0.70s` / golden `[GATE] all L1 verified commands have real samples (OK)` / spec `rc=0` / adversarial `rc=0` / reachability `无未登记孤儿 ✓` / originality `Total: 190  Original: 190  License OK: 190  Header OK: 190  Suspicious: 0  External imports: 17` / benchmark `benchmark smoke OK: kline, market, vipdoc` / `docs link check OK (96 files)`。相对第 30 轮：**4064 → 4219（+155）**、格式文件数 479 → 486、覆盖率 83.58% → 84%。**构件**（`Temp/r31/build31_pass1.out`，18:52，现场 `仓库根: P:\github_public\tstdx_wt_v19a31step`）：`[校验] canonical typed distribution ✓ (tstdx-1.1.0-py3-none-any.whl, tstdx-1.1.0.tar.gz, version=1.1.0, runtime_files=190)`、`[冒烟] 通过 ✓`、`[环境] twine 7.0.0 ✓` + `twine check` 过；wheel **781,111 B** sha256 `c64fd5f11267ab2d0db9cb2172a86f176ab36d058305f3be14794be3ccd0c2cf`、sdist **1,820,210 B** sha256 `308c523a4b999b1cc8be978c524d7f44f6dfa171501519c5c048c0c96f9f4c47`（两枚 sha 由 `sha256sum` 现读，不是抄构建日志的行序）。**装包六面**（`Temp/r31/inst31_faces.out`，19:07，**`CHECKS=20 bad=0`**）：`uv venv` 全新 `venv31w` → README 那条 `pip install "tstdx[all] @ file:///…whl"` `rc=0`（Resolved 27 packages）→ 导入落在 `venv31w\Lib\site-packages\tstdx\__init__.py`、`tstdx.__version__` ⇄ `importlib.metadata` 同为 `1.1.0`；库面 `minute=267 / trades=70 / call(minute)=267 / execute(QuerySpec)=267 / quotes(tdx native)=1 / async minute=267`、`trades(count=)` 仍是 `ValidationError`；**CLI 保护区在装好的包上**：块体炸掉之后 `"pool_closed": true`；CLI `--help 2132 B` / `minute 38730 B` / `trades 8934 B` 三条 `rc=0`；HTTP `/v13/minute/… 200 267`、`/v13/trades/… 200 70`、`count=50 -> 422 E1010`；WS `minute ok n=267` / `trades ok n=70`；MCP `tools=9 get_minute_today n=267`；流式面 `sync_running_on_enter / sync_closed_on_exit / sync_thread_gone / async_running_on_enter / async_closed_on_exit` 五真；quickstart 那条 `pip install -e ".[dev]"` `rc=0` 且**没在候选树多出改动**（前=43 后=43 差集为空）。**终字节复测**（本节写完之后再跑一遍，读的是已落盘的字节）：门禁 `Temp/r31/gates31_final.log`（19:52:38–19:58:28）末行仍 **`STEPS=12 bad=0`**、`dirty-after-gates=43`，全量格 `4219 passed, 6 skipped, 15 deselected, 26 warnings in 326.99s (0:05:26)` + `TOTAL 22392 3060 6070 1004 84%`、`486 files already formatted`、bridges `24 passed in 0.73s`、`docs link check OK (96 files)`——与 18:42 那次只在秒数上不同（312.43 → 326.99），覆盖率的六列读数一字不差。**构件**（`Temp/r31/build31_pass2.out`，20:03，现场 `仓库根: P:\github_public\tstdx_wt_v19a31step`）：`[校验] canonical typed distribution ✓ (tstdx-1.1.0-py3-none-any.whl, tstdx-1.1.0.tar.gz, version=1.1.0, runtime_files=190)`、`[冒烟] 通过 ✓`、`twine check` 过。两份构件与 `dist31_pass1` **分开比**：wheel `781,111 B` / sha256 `c64fd5f11267ab2d0db9cb2172a86f176ab36d058305f3be14794be3ccd0c2cf` **逐字节相同**；sdist `1,820,210 → 1,821,099 B`（+889 B）、sha256 `308c523a… → b6ffd238456b32684596be921106233169cc94f597f0e535e4e781634067d716` **不同**。差异不是猜的：把两份 sdist 各自解开 `diff -rq`，成员差异恰好 **6 个文件、全部在 `tests/`**（`architecture/test_client_family_transport.py`、`runtime/test_close_chain_ownership.py`、`runtime/test_runtime_http_client_release.py`、`streaming/test_async_stop_wake.py`、`streaming/test_stream_context_parity.py`、`transport/test_recv_wall_clock_deadline.py`），即 §11-2 那本台账给四把尺子改号 + 拆开 G47 撞号之后同步进去的标题行；`tstdx/`、`docs/`、`pyproject.toml` 一条差异都没有（现读成员清单：`tests` 1941、`tstdx` 190、`PROTOCOL_SPEC` 69、`docs` 7、根文件 7，合计 2214）。再把 pass2 的 wheel 解开做三向 `diff -rq`（忽略 `__pycache__`）：wheel 的 `tstdx/` ↔ sdist 的 `tstdx/` ↔ 候选树的 `tstdx/` 两边都是 **0 行差异**，wheel 内 189 个 `.py`。装包六面**没有**再重装一遍全新 venv 复跑：pass2 的 wheel 与 pass1 是同一枚字节，重装量到的是同一件事，那 20 格 + 补的 5 格已经跑在它身上。这段"终字节"叙述落盘之后，19:52 那 12 步里唯一还可能被纯散文动到的两格又在候选树单独跑了一遍：`check_docs_links.py` `rc=0` + `docs link check OK (96 files)`，文档侧 architecture 判据（`test_doc_code_consistency.py` + `test_release_identity_and_snapshot_docs.py`）`rc=0`、点阵 72+72+4 全过 | 本行不新增判据。它的正控是三样：① 上面那本 19 条电池（`CASES=19 BAD=0`，含 M5 那条要求"本尺子保持绿"的对照条）；② **收口探针自己也被复测了一遍**——第一遍 `inst31_faces.py` 的"六支直连命令"里有两格打的是**不存在的命令形状**（`hosts speedtest`、`list --market sh`），它们在 argparse 就退场，而「`rc in (0,2)`」把这种用法错误和"走完保护区后的 rc=2"混为一谈，于是那两格是**零覆盖**；补格脚本 `Temp/r31/inst31_cli_fix.py` 先把两个旧形状原样复现（`rc=2 bytes=0` 且输出里有 `usage`），再按 `tstdx/cli/parser.py` 现读的入参重打：`tstdx list sh --count 100` → `rc=2 stdout=0B merged=263B 0.4s`（E3035：`0x044D SECURITY_LIST` 已登记下线，客户端发包前 fail-fast）、`tstdx probe 0x052D --timeout 5` → `rc=1 stdout=1339B 38.8s`（`rc=1` 是"探测没成"的既定出口，第 23 轮台账同一口径），最后把 `family_client` 换成 `finally` 记账的 spy 现量**四支**直连命令（`list` / `probe` / `blocks` / `quotes-snapshot`）：每支 `region_entries: 1` 且退出保护区后 `pool_closed: true`（`blocks` `rc=2`、E3035 `0x07E5 BLOCK_QUOTES`；`quotes-snapshot 000001` `rc=0`）⇒ `CHECKS=5 bad=0`；③ 构建环境这一遍**换了**：主 `.venv` 已不含 `build`（第一遍以 rc=1 `[环境] 缺少 build` 收口失败，见 18:52 之前的那次尝试），于是新建专用 `Temp/venv31build`（build + twine 7.0.0）跑构建器，**没有**动共享环境——顺带记一条：本会话那层包装脚本末尾的 `echo`/`tail` 把 rc 吃掉了，后台任务的"exit code 0"不等于工具的 rc |

### 11-1. 登记不修（每条带实测口径与推翻成本）

本轮量到、但**不修**的东西。判断标准沿用 §4 的分级：改它需要业主裁决（语义会变）、或者它是
上游/外部事实（不是本地断链）、或者修它要新写一把尺子而本轮射程已满。

| # | 事实（本轮现读） | 为什么不修 |
|---|---|---|
| R-1 | `deadline_ms` 不是硬取消：它约束的是**每一跳建立时**的超时上界，一跳已发出后只能等它自己的 socket 超时回来，而 `[core] max_retries` 的重试在传输池内不再重读预算 ⇒ 总墙钟上界是"deadline + 最后一跳的容忍" | 要改成硬取消就得在池里持有跨重试的预算并在每次发包前 `wait_for`，那是语义变更（现在的错误类是 `ReadTimeout`，改完可能是 `DeadlineExceeded`）。本轮做的是**把口径写进 `docs/api/interfaces.md`** 而不是改行为 |
| R-2 | 限流器在 `strict=False`（配置缺省）时走**无界**阻塞：`pool.py:665 / 854 / 980` 与 `async_.py:833` 四处同步点调 `limiter.acquire()`，而 `ratelimit.py:250` 那一行把它翻成 `bucket.acquire(tokens, blocking=not self.strict, timeout=0.0 if self.strict else None)` ⇒ `strict=False` 时 `timeout=None`，令牌长期不回来时声明的 `timeout` 与 `deadline_ms` 都管不住这一等（异步孪生另有 `while not try_acquire(): await asyncio.sleep(0.05)` 的轮询路径，同样无界） | 改默认值会让"按配置节拍排队"的既有调用方当场拿到异常；这是策略选择。真实缺口是**没有一条判据量过"限流耗尽时多久把控制权还给调用方"**，登记为下一步的尺子候选 |
| R-3 | CLI 的 `blocks` / `list` 两支命令打到的是**已登记下线**的命令（`0x07E5` / `0x044D`，fail-fast E3035），而 §3 的 CLI 表把它们列在"直连传输层"里、读起来像可用命令；`--count` 在这两支上没有上界守卫，唯一的收口是"服务端返回短页就 break" | 错误消息本身是诚实且可行动的（带 `cmd=` / `name=` / 建议改查 `PROTOCOL_SPEC`），所以这不是断链而是**文档标注不足**；给 §3 表加一列"当前必然失败"是排版活，与 `test_cli_reference_table.py` 的分母口径要一起动，本轮排不进 |
| R-4 | `_cmd_quotes_snapshot`（`cli/runtime_commands.py:867-876`）在 `c.quotes_snapshot(args.symbols)` 之后读 `getattr(c, "last_errors", [])`——但 `TdxClient.quotes_snapshot`（`client/sync.py:546`）根本不写 `last_errors`，只有并发批量那条路（`sync.py:434/458`）写 ⇒ "全部失败"那行的 detail 在这支命令上恒为 `"未知原因"`，而 `getattr` 的缺省把这个静默再包一层 | 修法有两个方向（改走会收集错误的方法，或让 `_t_quotes_snapshot` 自己按只收集），语义不同；本轮先把事实钉在这里，并把"默认值把静默包起来"这一形状登记为 G40 家族的下一格 |
| R-5 | `Client.close()` 这条链在**默认内核**上是空转（构造 `Client()` 后线程增量 0、内置执行器没有 `close()`），而且它**不**停止由本客户端 `stream()` 出来并已 `start()` 的流——那条 worker 线程归持有流的人 | 本轮做的是把文档的过头主张改成如实口径 + 用 `test_close_chain_ownership.py` 7 格钉住"谁关、关什么、关不到什么"。要不要让 `close()` 连带停流是**契约变更**（会让"关门面继续用流"的写法失效），交业主 |
| R-6 | 31-C6 之后，`_slot_is_current` / `_mark_failure` / `_mark_success` / `_release_probe_token` 的 `generation` 形参在**每个调用点都传 `slot.generation`** ⇒ 它现在是一个恒等于槽位自身字段的参数，"必填"这件事由类型系统与两张池的 AST 尺子共同保证 | 下一步该问的是"这个参数还要不要存在"（收掉它，四个方法直接读 `slot.generation`，判据的分母同时收缩）。本轮不动：刚把静默放行改成必填，同一轮里又把它删掉会让 M/D 台账的因果读不出来 |
| R-7 | `baidu` / `eastmoney` 两个 Web 源在真机上撞上游反爬（`AntiTradeBlocked` / `WebSourceError`）；本机 7709 主站在收口这一遍多支直连命令上不可达（`f10` 那格报"所有主站均不可达"） | 外部事实，不是本地断链。第 30 轮已把"上游反爬抛错误类而不是空数组"写进 cookbook 选型表；本轮的装包探针因此把六面里数据依赖上游的格子按"错误可读 + rc 在上界内"判，而不是按"必须取到数据"判 |

### 11-2. 编号台账（本轮新登记 + 一次改号）

* **新登记**：**G46** 家族客户端的构造与释放（`tests/architecture/test_client_family_transport.py`，
  5 件事 / 104 格，射程是整个 `tstdx/`）；**G47** 异步池心跳与空闲回收者的起跑点
  （`tests/transport/test_async_sweeper_arm.py`）；**G48** 同步 `_recv_exact` 的墙钟读预算
  （`tests/transport/test_recv_wall_clock_deadline.py`）；**G49** 异步流每条睡眠腿可被 `stop()`
  叫醒（`tests/streaming/test_async_stop_wake.py`）；**G50** 流式订阅上下文协议两面同形
  （`tests/streaming/test_stream_context_parity.py`，与 G40 同格）；**G51** HTTP 服务面自建
  `Client` 的释放含异常路径（`tests/runtime/test_runtime_http_client_release.py`）。
  沿用登记：**G41/G40**（`tests/runtime/test_close_chain_ownership.py` 把"释放链"这一格从
  散文变成读数）、**G27** 的两件新增（⑦⑧）、第 30 轮的 **G44/G45** 一字未动。
* **一次改号（撞号的代价：同一个编号在两处指两件事，读者无法复核任何一处）**：
  本轮中途有四本判据的标题都写着 G47、两本写着 G46 ⇒ 除"异步池回收者起跑"那本保留
  **G47**（`tstdx/client/async_.py:101` 与 `tstdx/transport/async_.py:1391` 两处生产注释、
  以及 `test_async_transport_coverage.py:804` 的引用都指它，一起读得通）之外，另三本改成
  **G48 / G49 / G51**、流式上下文那本从 `G46/G40` 改成 **`G50/G40`**。
  变异编号同理：分派表那四条本轮早前用 `M7`–`M10` 命名，与家族尺子的 `M7` 和释放链的
  `M9`/`M10` 撞号 ⇒ 并入电池时改叫 **D1–D4**（`Temp/mut31d_all.py` 的脚本头就写着这次改号）。
  本轮之后**唯一编号**的口径：一个 M/D 号 = 一条变异，一个 G 号 = 一把尺子。

### 11-3. 本轮之后仍开着的事

- **R-2 的那把尺子**（限流耗尽时多久把控制权还给调用方）与 **R-6 的参数收缩**是本轮量出来、
  下一步最该做的两格。
- **P2-A / P2-E / G32 / B-4 / V20 §5 判据 2**（执行器 ≤420 行 vs 现值）：一字未动，归属与
  处置口径沿用 §4。本轮又给 `tstdx/runtime/executor.py` 加了 `_client_session` 保护区与
  家族构造处收敛（`git diff --numstat` 现值 **137 增 / 36 删**，文件 **710 → 811 行**），
  这条"该文件太大"的账因此**继续变长**，不遮掩。
- **`--count` 上界（R-3）** 与 **§3 表里"当前必然失败"的标注**：同一支笔的活，等 §3 表下一轮
  重排时一起做。
- **发行身份**：`1.1.0` 本轮一字未动（G44 钉的是"三处手抄相等"，不是"版本要往前推"），
  CHANGELOG `[Unreleased]` 里 1.2.0–1.4.0 那把梯子仍在等业主裁决（§10-2 同条）。

四条教训：

1. **收口探针自己也需要正控**：那两格"六支直连命令"打的是不存在的命令形状，`rc=2` 恰好
   落在允许集合里，于是**零覆盖被记成了全覆盖**。判据如果量的是退出码，它就分不清"argparse
   挡在门口"和"走完保护区之后失败"。修法不是多加几支命令，而是改量**进没进保护区、出来时
   关没关**（补格脚本现读 `region_entries` 与 `pool_closed`）。这与 §10 教训 1 同族：
   **测量工具的口径必须由工具自己核对**。
2. **`@contextmanager` 的记账要放 `finally`**：第一版 spy 把记录写在 `with` 之后，块体抛错时
   `@contextmanager` 会在 `yield` 处重抛，那段记录根本不执行 ⇒ `list` 那格（E3035 从保护区里
   炸出来）被记成"这支命令没进保护区"。**一条探针缺陷长得就像一条产品缺陷**；本轮它是靠
   "读数与已知事实矛盾"才被抓出来的。
3. **同轮并号比没号更坏**：四个 G47 与两个 M7 分别指两件事，读者无法复核任何一处，
   台账里"见 §11"的指针全部悬空。本轮把三本变异脚本并成一本（19 条）并一次性改号
   （§11-2）。以后写判据的顺序反过来：**先查号有没有被占，再落标题行**。
4. **"安装包指纹"是两个数，不是一个**：sdist 带 `tests/`（1941 个成员），wheel 只带 `tstdx/`
   （189 个 `.py`）。于是"只改测试标题行"这一遍是 wheel 逐字节相同、sdist 差 889 B——如果
   只比一句"构件 sha 相同/不同"，就会得出"改动进了 runtime"或"复测没真跑"两种错话。终字节
   复测比指纹要**分开比，并把差异落到成员清单**（本轮 `diff -rq` 两份解开的 sdist：6 个文件、
   全在 `tests/`，`tstdx/` 零差异）。


