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
| P2-F | **`AsyncQuoteStream.stop()` 的停机等待没有任何尺子在量**（第 28 轮第 2 遍登记，判据七的自曝范围）。现读三件事：① `tstdx/streaming/base.py:510-518` 的收尾是 `await asyncio.shield(task)`，而 `tests/architecture/test_resource_lifecycle_gates.py` 的第七格只覆盖闭合名单 `_SHUTDOWN_FUNCS`（`close`/`__aexit__`/`_drop`/`_sweep_idle`/`_cleanup_committed_close`/`_await_cleanup_before_cancellation` 六个名字，`stop` 不在其中）——也就是说这条停机路径**在判据七的射程之外**，判据七的 docstring 里那句"`AsyncQuoteStream.stop()` 的 shield 不在射程内"就是这条登记的落点，不在这里偷偷放行；② 被 shield 的任务此刻很可能正卡在 `:546` 的 `await asyncio.to_thread(self._get_runtime().quotes, ...)` 上，`to_thread` 不可取消，所以 shield 等的不是"一个 await 边界"而是"一次同步取数跑完"；③ 它**有上界但不是小上界**：那次取数由池的 socket 超时与逐台故障转移兜住，之后 `_run` 在循环顶部读到 `_stop` 才退出，中间最坏还要睡一次 `ReconnectPolicy.next_delay()`——`base.py:448` 现读 `cap=30.0`。⇒ 结论：这不是死循环、也不是无界等待，但 `stop()` 在故障转移场景下可以阻塞到"一次取数超时 × 台数 + 30 秒"。**本轮只登记不修**（改法已想清楚：把 `stop` 加进 `_SHUTDOWN_FUNCS`，再要么给 shield 套 `wait_for`、要么在登记表里逐字写明上界，两者都要先有真机或桩件测出的那个上界，不能凭感觉填一个数） | `tstdx/streaming/base.py:510-518`（shield）、`:539-547`（不可取消的 `to_thread` 取数）、`:448`（`ReconnectPolicy(base=1.0, cap=30.0)`）；`tests/architecture/test_resource_lifecycle_gates.py:721-730`（名单闭合、含"名单必须对得上包里真实异步函数"的自证）、`:33-37`（本条射程声明） |

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
