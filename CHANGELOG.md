# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed（v17 Phase 5 第 44 步 —— 错误树上 4 个「文档承诺、运行期永不发生」的叶子：F-68 裁决 (a) 的执行；**BREAKING**）

- **删掉 4 个从未兑现的公开错误类**：`UnknownCommand`(E3030)、`ChecksumMismatch`(E3050)、
  `SourceUnavailable`(E7050)、`CompatibilityWarning`。判据是同一把尺子两遍：全仓 AST 扫 `raise`、
  `raise <变量>` 回溯与 `on_error(...)`/`warn(...)`/`emit(...)` 投递三类站点，加上 38 份活文档的点名普查。
  它们既没有抛点也没有投递站点，却被对外文档写成「你会拿到这个异常」——`docs/providers/README.md` §12
  甚至规定各 Provider 统一以 `SourceUnavailable` 表达「选定的 Provider 不可用」。
- **写破坏性变更的理由与替代**：今天这些位置上的真实下场是——Provider 不可用 = 传输层原异常
  （`ConnectionFailed` E2010 / `AllHostsUnreachable` E2040 / Web 面的 `WebSourceError` E7xxx 家族），
  命令已下线 = `CommandOffline` E3035，未知/低置信样本由离线工具链归档而非运行期异常，兼容性判定
  一直是 `CompatibilityError` E8000 而不曾发过警告。已按 F-68 裁决 (a) 接受「用户写了 `except
  SourceUnavailable` 会失效」这一代价；`docs/errors.md` §一之二 是全仓唯一的退役登记表。
- **裁决里的第 5 个叶子被取证否掉，没有删**：`BackpressureOverflow`(E6030) 在补上「投递站点」判据后
  实测有真实站点（`tstdx/streaming/base.py:185-186` 队列溢出时 `sub.on_error(BackpressureOverflow(...))`，
  context 带 `dropped_total`/`max_queue`）。删它等于删一条对外承诺，与裁决意图相反，故保留并新增行为测试
  `tests/streaming/test_backpressure_delivery.py`。
- **对外文档按实测行为改写，不是把名字抹掉**：6 份 Provider 文档的错误段统一为 `ValidationError`(E1010) +
  `WebSourceError`(E7xxx) 口径，其中两处是假承诺——`FreshnessViolation` 只在读本地文件的 channel 上触发，
  纯 HTTP 的 baidu 严格模式不会因新鲜度报错；iwencai 的「认证错误」在本仓根本没有独立类。
  `docs/migration/easy_tdx.md` 的「未知 category 发 `CompatibilityWarning` 并回退 `"day"`」整句作废
  （未知 period 由所选 Provider 的 `supported_periods` 以 422 拒绝，不存在告警后回退）。
  `docs/tdx_status.md` 的 `route="web"/"local"` 「仍报 `ValueError`」也是假事实——v17 的 `Client.quotes()`
  没有 `route` 形参，实测是构造期 `TypeError`；改写为 pre-v17 → v17 对照并补上真实失败形状。
  ADR-013 §11 的原文不抹史：保留并追加带日期与裁决号的作废修订。
- **承诺门禁改成双向，并把自己写的表钉回事实**：`tests/architecture/test_error_promises.py` 新增反向判据
  「活文档错误小节点名的 CamelCase 名字必须存在于代码里」与退役登记判据；`tests/errors/test_taxonomy.py` 里
  此前**没有任何测试读取**的 `EXPECTED_HTTP_STATUS` 现按「偏离默认 500 的类」双向核对，`EXPECTED_PARENTS`
  必须覆盖每个类的直接基类，表与 `tstdx.errors.__all__` 双向对账。三条新判据当场量出既有表的 8 处漂移
  （`CommandOffline`/`FreshnessViolation`/`TruncatedDataError` 三个真类在四张表里全部缺席等）。
- **README 不再声明异常类个数**：`40+ 异常类` 这类快照会在下一次删类时静默变谎，故撤下其下界宣称，并换成
  反向判据（任何活文档写回 `NN+ 异常类` 即红）+ 扫描面金丝雀。顺带删掉那条宣称的旧真相源，它数的是
  `errors.py` 全部顶层 `ClassDef`，把 `RetryAdvice` 这个 dataclass 也算成了「异常类」。
- **取证与复测**：14 发变异逐发点名（含把 `on_error` 投递前缀摘掉时金丝雀确实报警），每发注入→断言该判据红→
  字节级还原；隔离工作树同一轮复测（py3.13.12、`-m 'not network'`、`--cov=tstdx`、阈值 77 未动）：基线 `9ba2385`
  junit 3531 / 0 失败 / 5 跳过、81.26%，本步树 junit 3585 / 0 / 5、81.27%（净增 54 个用例）；九项确定性门禁
  两树逐格相同且全部 rc=0（originality 192/192、spec_audit 100.0%、reachability 无未登记孤儿、docs links 82 files、
  mypy、ruff check、ruff format 473 files）。

### Fixed（v17 Phase 4 第 43 步 —— 「照本库自己的指引做，就会把自己弄坏」的开关：F-69，同时给配置面文档上机器对账门禁）

- **`TSTDX_WENCAI_COOKIE` 此前是一个自毁开关**：`tstdx/web/wencai.py` 读它取 i问财 cookie，同文件的错误消息直接叫用户「设置 `TSTDX_WENCAI_COOKIE`」——可它没登记进 `config/loader.py` 的 `_RUNTIME_ENV_KEYS`。`TSTDX_` 是 strict 环境扫描的**保留命名空间**，未登记的名字一律按拼写错误 fail closed，所以用户照本库指引做完，下一条 `Client()` 就抛 `ConfigError [E1000] 无法识别环境变量 TSTDX_WENCAI_COOKIE`：不是「这个变量不生效」，而是整条配置链起不来。基线（`7a3bae7` 干净树）实测复现、修复后同一命令构造成功。本轮登记该变量、在 `docs/configuration.md` §4 的 runtime 变量表补一行，并写明「该前缀为保留命名空间，测试与工具不得占用」。
- **同一族缺陷的第二格在测试中**：`tests/unit/test_golden.py` 的合成样本开关原名 `TSTDX_GOLDEN_SYNTHETIC`——一个仅用于回放基线的测试夹具占了运行时保留前缀，任何设了它的会话会让 5 个配置贯通测试红。改名 `GOLDEN_INCLUDE_SYNTHETIC`（全仓 grep 证明旧名除那两行外零引用，无 CI / Makefile / 文档依赖 ⇒ 对外契约零变化）。
- **判据从「两份抄本互相印证」升级为「代码读取 ⇒ 必须有归属」**：§4 的表与 loader 的登记表此前只要一起漏同一个名字就完全自洽。新增 `tests/architecture/test_config_doc_contract.py`（18 个用例）扫描 `tstdx/` 与 `scripts/` 全部 .py 里的 `TSTDX_*` 字面量，每一个都必须已登记或本身是 schema 形 `TSTDX_<SECTION>_<KEY>`；`tests/` 刻意排除（那里的错拼是负例夹具）。
- **`docs/configuration.md` 的五类抄本一并钉回运行期事实**：段与键**双向**对账（幻影键与漏记的键各自报红）、默认值对上运行期 `DEFAULT_CONFIG`、取值范围改成行为化判据（文档写的边界必须真被 `validate()` 接受、越界一档必须真被拒）、注册表成员声称以 `PROVIDERS`/`KNOWN_SOURCES` 试正反例、§5 的 8 条 fail-closed 场景就地触发并逐个命中文档写出的异常类名与消息片段、§4 命名规则对全部 17 键逐个成立。**测下来这份文档今天全部为真**，需要改的只有上面那一条环境变量——本步的主体是防回潮。
- **`docs/api/interfaces.md` 的 WS 方法名单第二种写法纳入既有门禁**：`docs/api/README.md` 写的是括号清单，interfaces 写的是顿号分隔的散文清单，此前只有前者对分派器认账。同一轮把 MCP「N 工具」的数钉回 `TOOLS` 注册表。一个名字两种抄法就有一条判据的漏网形状。
- **顺手撤销一条已失效的豁免**：`tests/runtime/test_kernel_config_wiring.py` 还以「运行期校验器尚不存在」豁免 `QuerySpec.currentness`，而第 41 步的 `tstdx/runtime/freshness.py` 已直接读它 ⇒ 撤销豁免，该字段自此受幻影旋钮判据管。
- **12 发变异全部被点名抓出**（含取消登记该变量、塞入未登记的环境读取、改一格默认值、放宽一格范围、改一条报错措辞、删一行文档），并在过程中逼出判据自身的两条形状缺陷（幻影键会让成员声称判据报出误导性结论；§5 场景被删时自检用例以 `KeyError` 崩溃而不是一句人话），两条都已修。
- 隔离工作树复测（`-m 'not network'`，同一解释器）：基线 junit 3511 / 0 失败 / 5 跳过、81.23%，本步 junit 3531 / 0 / 5、81.26%；`ruff check`、`ruff format --check`、`mypy tstdx/`（CI 同参数）均 0 问题，覆盖率阈值 77 未动。

### Fixed（v17 Phase 4 第 42 步 —— 事实文档里的**斜杠死路径**上门禁：F-67 (a) 的清偿，Phase 4「文档统一」的第一格）

- **这一格改的是两份对外文档里的假事实，不是代码**：`docs/errors.md` §四「上层边界约定」把两个
  磁盘上不存在的模块（`facade/api.py`、`integration/http_server.py`）写成今天的边界，第一条 bullet
  还承诺了 `ApiResponse{success=False, ...}` 与 `context["route_errors"]` 聚合整套已随 v12 门面删除的
  形状；`docs/ARCHITECTURE.md` §3 的分层表把已随 `fcf8e92` 删除的 `tstdx/security/` 标成「活」。
  §四 整节按实测重写为五条边界事实：`tstdx/client/api.py` 全文没有一处 `except`（异常原样上抛）、
  越过信任边界的错误只有 `ErrorEnvelope` 一个形状、HTTP 面真身 `runtime_http.py` 取
  `envelope.http_status`、WS 与 MCP 的人读位置不对称、CLI 向 stderr 打一行 JSON 信封并以
  2/1/130 退出；旧文写错的每一处都留了「此前写的是什么」的说明，不静默抹史。表下补的安全口径
  第一稿多声称了一句「凭据一律走环境变量注入」并链向根本不存在的 `docs/security.md`，
  两处都在落笔前删掉/改指根 `SECURITY.md`。
- **事实型文档门禁为什么一路放行**：判据只认反引号里的**点号**模块路径（`tstdx.a.b.C` 那种形状），
  而这几处写的是**斜杠**文件名——形状上就不进判据。本节把同一批文档按斜杠形式再扫一遍，并按 F-67
  登记时的要求先做单独一轮全量取证（`step42/probe_slash_paths.log`）：活文档里含斜杠的路径引用共
  **828 处**、磁盘上不存在的 **85 个不同 token**，收窄到 25 份事实文档后真需要改的只有 **3 处**
  （§四 两处 + ARCHITECTURE 一处），其余落在 `docs/archive/`、`docs/adr/` 与各版本方案史的刻意
  历史语境里，按设计不参与事实检查。
- **判据的两条豁免都很窄**：① 同一**逻辑块**（段落 / 列表项 / 表格行）内写明删除史才赦免——块粒度
  是它的全部效力所在，整份文档当一块等于没有判据；② 该目录由代码在运行期自建，判据是推导不是名单
  （同一个 .py 文件里既调用 `mkdir`、又把这个名字写成路径分量）。落点回退按仓库根 / `tstdx/` /
  `docs/` 三个根各试一次，只认仓库根会**虚报 37 处**（文档对同一物件有三种写法）。基线读数：
  **0 违约 / 18 个豁免 token**，18 个逐个核过出处，无一处是现时口径的假事实。
- **自检与被自检**：另加一条测试，硬要求这套判据**真的看见过** `execution/`、`provider/`、
  `tstdx/facade/` 三层已删除目录——一套只会说「没问题」的判据与没有判据等价。**变异 9 例、
  UNEXPECTED 0**（`step42/mutations2.log`）：控制组绿；3 个植入的现时口径死路径（表格行断言、
  跨段删除史、`tstdx/security/`）全部被抓；把整份文档当一块 ⇒ 跨段那条**逃逸**；豁免退回
  「目录名在源码里出现过就算」⇒ `tstdx/security/` 被 `/v13/security/count` 这条路由字符串
  **白白赦免**（这正是本步第一版的实际错法）；只认仓库根 ⇒ 误报 37 处；摘掉形状排除 ⇒
  `output://`、`mypy tstdx/`、`docs/providers/<provider>.md` 等 5 处垃圾进名单；扫描面缩到一份
  文档 ⇒ 自检当场报警。
- **复测（孤立 worktree，同一轮，解释器 cpython-3.13.12）**：基线 `wt_s42base` = 干净 `39a1b30`，
  junit **3509 / 0 失败 / 0 错误 / 5 跳过**、157.414s、**81.23%**（`step42/s42base.suite.log`）；
  本步树 `wt_s42step` = 同一 HEAD + 本步 3 个文件（dirty=3），junit **3511 / 0 / 0 / 5**、155.107s、
  **81.23%**（`step42/s42step.suite.log`），`3509 + 2`（斜杠判据 + 自检）对得上，阈值 77 未下调；
  覆盖率 TOTAL 与基线逐格相同（`22606 / 3654 / 6050 / 1023`），因为 `--cov=tstdx` 只看包而本步
  零生产代码。9 主门禁两树全部 rc=0 且逐项读数逐行相同（originality 192/192、spec_audit 100.0%、
  docs link 82 files、ruff format 471 files）。
  提交内容所在的同一棵树另跑三轮（`s42commit`/`s42commit2`/`s42final`，每轮之间只动文档措辞），
  九项门禁同样全部 rc=0、junit 3511 / 0 / 0 / 5；三轮里只有 `s42commit` 把覆盖率记成 **81.24%**，
  差异整格在 `tstdx/protocol/generic.py`（缺语句 20↔21、部分分支 9↔10），与第 41 步记录的是
  同一格运行间抖动。

 —— `currentness` 从声明口径变成运行期判据，"文档点名的错误类 ⇒ 代码里真有站点"上门禁：F-44 裁决 (a) 的执行）

- **对外行为变化（本步唯一一处，且只有一个可达形状）**：本地 vipdoc channel 被要求
  `currentness='business'`（HTTP 面默认口径）时，`strict=True` 从"安静地返回一份无法证明新鲜度的本地文件"
  改为运行期 `FreshnessViolation`（E4060，HTTP 面 503）；非严格时结果照样返回，但携带
  `currentness_unproven` 瑕疵。`live` 打本地 channel **到不了**这条判据——规划期先以 `ValidationError`/422
  拒掉（本步未改），所以规则里的两个 mode 在用户侧只落地一个。`auto`/`historical` 与其余 55 个非本地 channel
  的行为一字未动。
- **落笔前先量裁决的边界**：(a) 那一格里写着两件事——"Provider 失败路径统一包成 `SourceUnavailable`"与
  "为 `currentness` 落一个可判据的运行期校验器"。第二轮取证把两件事分成了两种下场：后者有落点并已接线，
  前者没有。没有落点的证据：`DIRECT_BINDINGS` 251 条与注册表三元组一一对应，2100 个 (provider, capability)
  组合经 `QueryPlanner.compile` 全部或编译成功、或以 `ValidationError` 结束，**能到达执行器"没有可执行
  Direct Provider binding"分支的组合数是 0**；而把 Provider 真实失败统一包成 `SourceUnavailable` 要推翻
  `docs/providers/README.md` §12 自己那条"Provider-specific `TdxError` 必须原样保留"的保护——那是另一次
  对外契约决定，不在"接线"授权里，登记为 F-68 待裁决。
- **校验器的形状**：新增 `tstdx/runtime/freshness.py::verify_currentness(plan, *, strict)`，在
  `DirectProviderExecutor.execute()` 里**先于任何 Provider I/O** 调用（也在 `warning_sink()` 块内，所以
  非严格面的瑕疵进得了本次结果的 `meta.warnings`）。判据只读两件事：`ChannelSpec.local`（注册表事实）与
  `_parse_currentness`。`auto`/`historical` 不要求证据；`live`/`business` 要求"当期"，而本地文件 channel
  给不出"文件已覆盖当期"的判据 ⇒ 无法证明。三面分工：规划期 422（`live` 打非 live channel，输入本身不
  可满足）/ 运行期 503（`strict` 且口径无法证明）/ 非严格 200 + 瑕疵（发射口仍只有
  `tstdx/diagnostics.py::record_warning` 一处）。
- **否决了一条看似更聪明的规则**：不做"行内时间戳晚于今天 ⇒ 假数据"判据。行内时间戳在本仓至少有三种
  写法（8 位无分隔、`YYYY-MM-DD HH:MM`、`YYYYMMDD`），全部是无时区的源本地 CST 字面量，而
  `Provenance.observed_at_ns` 是 UTC 墙钟——拿 CST 字面量跟 UTC 墙钟比符号，主机时区不在 +8 时每天误报约
  8 小时。先解决时区归属，本步只落"注册表可判据"的半边。
- **新门禁 4 项 + 判据测试 13 项**（`tests/architecture/test_error_promises.py`、
  `tests/runtime/test_freshness_verifier.py`）：门禁的分母由扫描现推（82 份 md 里 46 份对外文档、被反引号
  点名的 45 个错误类），判据是"文档点名 ⇒ 该类子树里有真实站点（`raise` / `raise <变量>` 作用域回溯 /
  `on_error(...)` 投递）"；5 个未接线叶子进豁免表，每条必须挂一个仍在台账里的 F 号，且撤销只看类自身站点
  ——接线后不撤表，门禁自己报过期。幻影异常与幻影开关（F-43/F-46）同族：用户按文档写 `except`，那段代码
  永远不会执行。行为侧 13 项含 channel×mode 全矩阵与"注册表事实自校验"，并断言同一开关的两面一致
  （抛出的类与落进 `warnings` 的瑕疵同现同灭）。
- **8 发变异逐发被抓住**（规则收窄成只认 `live` / 删掉执行器调用 / `strict` 面失效 / 非 `strict` 面静默 /
  豁免表漏一类 / 已接线类仍挂豁免 / 文档点名扫描失明 / `raise <变量>` 判据退化）：`变异数 8，未被抓到 0`，
  三个被改文件事后逐一做残留检查均为 OK。
- **文档面（把不兑现的承诺改成实话）**：`docs/providers/tdx.md` §5 写明运行期只有一处 `currentness` 判据、
  `age`/`received_at` 在本仓既无字段也无读写方（全仓 grep 0 命中），§7 删掉 `CapabilityUnsupported` /
  `DataIntegrityError` 两个从未存在的类名并写明 `SourceUnavailable` 无抛点；`docs/providers/README.md`
  §11 的 `historical_closed`/`current_series` 换成 `CurrentnessMode` 四值口径，§12 从"`SourceUnavailable ==
  selected Provider unavailable`"改成"登记占位 + 选定 Provider 无法满足请求时用户实际拿到的是哪四类"；
  `docs/errors.md` 新增"树里存在但运行期永不发生的类"表（5 行各配为什么不会发生）、
  `fallback_to_offline`/`fallback_to_web` 的消费方从"sources 路由"改为"无消费方"、头部类数改为指向源头
  并记下 v8 的 44 与当前 46 个类的口径差；`docs/tdx_status.md` 三处 P13-A 的 `SourceUnavailable` 承诺改写
  为 v17 实况，并在兜底矩阵上加"v17 状态"横幅。
- **判据自身的第一版错过两处，两处都留档**：站点普查最初只认字面 `raise X(`，把
  `last_exc = AntiSpiderBlocked(...)` + `raise last_exc` 这条已接线的重试环虚报成幽灵（多报 3 类）；豁免表
  的自洁判据最初按子树计数，于是三个抽象基类被自己判成"已接线却仍挂豁免"——事实是它们不该在豁免表里。
- **孤立 worktree 同轮复测（本机 Windows + 外部解释器 cpython-3.13.12，非仓内 `.venv`）**：基线（干净
  `b52a122`）junit 3492 / 0 / 0 / 5、149.270s、**81.21%**；本步树 junit **3509 / 0 / 0 / 5**、159.392s、
  **81.23%**、RC=0，`3492 + 13 + 4 = 3509` 对得上，阈值 77 未下调。覆盖率 TOTAL 行
  `22580 stmts / 3654 缺 / 6042 分支 / 1023 缺分支` → `22606 / 3654 / 6050 / 1023`：新增 26 条语句、
  8 条分支全部被执行到（缺语句与缺分支两格一格没动）。9 主门禁两树全部 rc=0（originality 191→192 模块、
  ruff format 468→471 文件、docs link 两侧同为 82 files，其余六项读数与基线逐格相同）。
- **落笔后对提交内容所在的同一棵树再跑两轮**：九项门禁两次全部 rc=0；junit 两次同为 **3509 / 0 / 0 / 5**，
  覆盖率一次 **81.24%**（TOTAL 3653 缺语句 / 1022 缺分支）、一次 **81.23%**（3654 / 1023，与首轮相等）。
  0.01 个百分点的差整格在 `tstdx/protocol/generic.py`（逐文件覆盖率表 diff 只有这一行，本步没碰那个文件），
  与上一步记录的是同一格运行间抖动——两个读数都记下，而不是只留相等的那个。

### Fixed（v17 Phase 5 第 40 步 —— 三张 wire 面对未声明的请求字段当场拒绝：F-47 裁决 (a) 的执行）

- **裁决即边界**：F-47 选 (a) 三面 fail-closed——HTTP 查询串与 body、WS `params`、MCP `arguments`
  统一按白名单核对，未知键即拒，并让 MCP schema 的 `additionalProperties: false` 真正被执行。
  (c) 的「GET 查询串维持宽容」未采纳，所以这是一次**对外请求契约的收紧**：此前返回 200 的请求
  现在开始返回 422 / `-32602`，属破坏性变更；`docs/api/interfaces.md` §3 就地写明口径，连客户端
  爱加的缓存穿透参数 `_=…` 同样会被拒这一条一起写。
- **一条拒绝口 + 四份「声明本身」**：新增 `tstdx/integration/wire_fields.py`（`reject_undeclared`
  把人读的那句话与机读侧的 `unknown_fields` 一起给出）。HTTP 查询串的白名单就是路由签名
  （`route.dependant.query_params` 运行时现取，结构上没有第二份名单可过期）；body 与 WS `params`
  用本模块的两份名单；MCP 用该工具自己的 `inputSchema.properties`，9 张 schema 同时补上
  `additionalProperties: false`——只声明不执行等于承诺一个不会发生的行为。
- **实现先造出一次全站故障**：接上依赖后每条路由都 422。根因用两变体探针钉死——该文件顶部的
  `from __future__ import annotations` 让 FastAPI 解析不出工厂内局部 import 的 `Request` 注解，
  于是把依赖形参当成必填查询参数。删除该 future import 并在原地记因：这条约束隐形到一次
  「顺手统一导入风格」就能让全站再挂一次。
- **两张 JSON-RPC 面的人读位置并不对称（实测）**：WS 把理由放在 `error.data.message`
  （`error.message` 是通用的 `invalid params`），MCP 平铺进 `error.message`；判据各按本面真实
  公开的读法断言，照抄一条就会有一面永远绿。
- **新门禁 46 项**（`tests/runtime/test_wire_declared_fields.py`：6 条推导 + 40 条行为）：名单与
  分派读取点双向求差、每张 schema 必须写着拒绝、四面必须调用唯一拒绝口、名单在全包只有一个定义点；
  行为侧逐路由 / 逐方法 / 逐工具真打，带满已声明字段必须通、多一个 `max_age` 必须两侧点名被拒。
  13 发变异逐发「红掉的测试集合与预期完全相等」（无越界、无漏抓），红数依次
  1/2/1/1/1/1/10/7/1/10/9/30/11；`baseline` 与 `restore check` 两侧都是「红 无」。
- **孤立 worktree 同轮复测（本机 Windows + 外部解释器 cpython-3.13.12，非仓内 `.venv`）**：基线
  （干净 `8fe0d0e`）junit 3446 / 0 / 0 / 5、**80.91%**；本步树 junit **3492 / 0 / 0 / 5**、
  **81.21%**、RC=0，`3446 + 46 = 3492` 对得上，阈值 77 未下调。覆盖率 +0.30 个百分点不只是新模块
  自己绿：TOTAL `22555 stmts / 3712 缺` → `22580 / 3654 缺`，新增 25 条语句的同时把 58 条原本没执行到
  的服务面语句跑到了；9 主门禁两树全部 rc=0。
- **落笔后对同一棵树再跑一轮（这一轮读的树就是提交内容）**：`s40final.log` 九项 rc=0、`s40final.suite.log` junit **3492 / 0 / 0 / 5**、**81.21%**、RC=0。两次同树读数的 TOTAL 差一格（缺语句 3654→3653、缺分支 1023→1022，整格在 `tstdx/protocol/generic.py` 87%→88%，本步没碰那个文件），百分比读数相同——两处读数都记下。
- **提交树再跑第三轮，把那一格抖动归了因**：`s40commit.suite.log` junit **3492 / 0 / 0 / 5**、**81.21%**、TOTAL 3654 缺语句 / 1023 缺分支——回到第一轮的读数，说明第二轮那一格是 `tstdx/protocol/generic.py` 自身测试的抖动，不是本步改出来的。同一轮 `s40commit.log` 九项 rc=0，补完这段文字后同一棵树再跑的 `s40commit2.log` 仍九项 rc=0（两份日志只差这条文档文字）。三轮都记。
- **登记 F-67，不顺手改**：`docs/errors.md` §四 把 `facade/api.py` 与 `integration/http_server.py` 两个已不存在的模块写成今天的边界，还承诺了 `ApiResponse{success=False, ...}` 这套零命中的形状；事实型文档门禁看不见，因为判据只认反引号里的**点号**路径而这儿写的是**斜杠**形式。同轮全扫 7 份活文档只有这一份含死路径；第一次取证自己虚报了两份（按尾串匹配），改整串匹配才对上。属 Phase 4「文档统一」，三条路径与两份读数写进账本。


### Fixed（v17 Phase 5 第 39 步 —— 发不出去的命令，在调用方读得到的每一面写明「已下线」：F-63② 与 F-37 (c) 裁决的执行）

- **授权的边界就是这一步的边界**：用户裁决 F-63② 取「保留，只把『已下线』写清」、F-37 取
  「下调能力声称 + 推迟 tag」。零改协议字节、零改解析器、零删除公开面，改的全是"说法"层。
- **一条推导链取代五份手抄名单**：账本 `STATUS_OFFLINE`（9 条）∪ inferred 拦截集（2 条）− 放行集（1 条）
  → trampoline 模板（含 `_op_call` 传递闭包）→ 内核 tdx 直绑能力 → MCP 处理器 → 两份 markdown 表。
  同轮实测分母：模板面 9 个、业务入口面 3 个、MCP 死工具 3 个、接口表 47 行可解析 / 23 行可推导。
  口径相反的那格单独写清：`0x054C` 虽登记 offline，却是唯一被 `_OFFLINE_FALLBACK_OK` 放行的命令，
  所以 `_t_quotes_snapshot` 说的是「本方法仍可用，真实数据走逐只 `0x0530` 回退」——给它套 fail-fast
  套话同样是假话。
- **F-37 的降级落在四处调用方可读面**：`_t_capital_changes`/`_t_finance_info` 各挂 `.. warning::`、
  Provider 文档两行同口径、README 三行改写，统一说法是「条数可用、字段语义不保证」；
  tag `v1.1.0-dev.1` 继续推迟；解析器一个字节没动。
- **新门禁 8 项**（`tests/architecture/test_offline_capability_honesty.py`），分母全现推、每条带自检下限、
  Provider 文档面两侧都判；13 发变异里 12 发各自只红自己那条，第 13 发（把 `_op_call` 的常量参数换成同值
  f-string，行为不变而 AST 失明）同时红两格——两格读同一条闭包，这正是"同源"的证据。
- **变异揭出自己的假绿**：MCP 判据原写 `"offline" in description`，而 `CommandOffline` 这个类名自带该子串，
  只提异常类名就能喂饱断言；改成 `\boffline\b` 词边界后 M5 才真的红（第 38 步 M1 的同形故事）。
- **登记为 F-66，不代拍板**：`GET /v13/capabilities` 是第六张面、也是唯一机器可读的那张——16 个
  tdx/quotation 能力名平铺成裸字符串、无状态字段，其中 8 个发不出去（`auction`/`volume_price` 探针当场
  `CommandOffline`）。改发现面形状属对外契约，三条路径与判据一并写进 `docs/REFACTOR_PLAN_V17_CLOSURE.md`。
- **孤立 worktree 同轮复测（本机 Windows + 外部解释器 cpython-3.13.12，非仓内 `.venv`）**：基线
  （干净 `4dd2af9`）junit 3438 / 0 失败 / 5 跳过、80.91%；本步树 junit **3446 / 0 / 0 / 5**、**80.91%**、
  RC=0，`3438 + 8 = 3446` 对得上，阈值 77 未下调；覆盖率 TOTAL 行与基线逐格相同（本步在 `tstdx/`
  里没新增可执行语句）。**同轮先前那次读到的是 80.90%**，差的 0.01 个百分点整格在
  `tstdx/transport/pool.py`（缺 113→111、缺分支 37→36），本步没碰那个文件，是 transport 测试的
  运行间抖动，两轮都记下而不是只留相等的那个；9 主门禁两树全部 rc=0（originality 190/190、
  `spec_audit` coverage 100.0%、golden `[GATE] … (OK)`、reachability 无未登记孤儿、`contract_audit --ci`、
  docs links 82 文件、mypy 无输出、`ruff check` 干净、`ruff format --check` 466 / 465 文件）。

### Fixed（v17 Phase 5 第 38 步 —— 命令账本四个没人读的字段，其中一个还是全账本唯一的描述：F-64 的清偿）

- **量的是账本自己**：`Command` 登记 10 个字段，干净 `bb201b1` 上 AST 扫 `tstdx/` 189 个模块，
  `request_fields` 0 处、`aliases` 0 处、`spec_file` 1 处而那处属 `spec_audit.AuditResult`（同名不同物）。
  第四个是 `summary`——85 条命令逐条写着中文描述，全包读它 0 处（3 处同名命中属 `domain/records.py` 的
  `NewsRecord`/`ResearchRecord`/`SearchRecord`）。但它跟前三个不同判：85 条里只有 39 条在 `PROTOCOL_SPEC/`
  有 YAML（该目录 44 个 yaml、42 个命令号），另外 **46 条**的语义说明只活在 `summary` 这一行，而客户端
  唯一把命令说给用户听的地方（`_guard_offline` 的两条 fail-fast）只报了名字。
- **三件改动**：① `Command` 10 → 7 字段，13 处 `request_fields=(...)` 实参与 `codegen.py` 里那 7 行推导
  同删（生成器只剩"写"的半边时，删字段会让下一次真跑 `--write` 直接 TypeError）；② `summary` 接线：
  报错文案变成「0x07E5（BLOCK_QUOTES：板块行情（2026-09 三主站实测无响应，client 方法保留待参数校正））」，
  `context` 加 `"summary"` 键；③ 判据四条 + 分母自曝一条：形状锁、85 条普查、按账本自身分母
  （8 条被拦 offline + 2 条 inferred-block）逐个钉「文案里有这句话」与「context 里有这个键」、
  生成那一行喂回 `_c` 求值。
- **判据上线即绿，而绿是假的**：M1 把 `summary` 从文案里摘掉、`context` 留着 → 第一轮 **RC=0**。
  `TdxError.__str__`（`tstdx/errors.py:135-140`）把 `context` 前六个键拼进字符串，`summary in str(exc)`
  被机读侧单独满足，"人读的那句话"从没被断言过。改读 `ei.value.message` 后 M1 红 10 项，反向的 M6
  （只摘 `context["summary"]`）红 8 项。本族前五次都是新判据上线即红，这次是上线即**绿**——
  只有真去做变异才现形。六发变异最终各自 rc=1（`s38b_mut.log`）。
- **孤立 worktree 同轮复测（本机 Windows+py3.12，仓内 `.venv` cpython-3.12.13）**：基线（干净 `bb201b1`）
  junit 3423 / 0 失败 / 7 跳过、80.79%；本步树 junit **3438 / 0 / 0 / 7**、**80.84%**，
  对账 `3423 + 2 形状普查 + 11 到达判据 + 2 生成器往返 = 3438`，阈值 77 未下调；链上 15 项全部 rc=0
  （originality 190/190、reachability 189/172/17、`spec_audit` coverage 100.0%、golden `[GATE] … (OK)`、
  docs links 82 文件、mypy 无输出、`ruff check` / `format --check` 435 文件）。六个文件先在 `abef5e1` 上
  跑过一轮（3416 → 3431、80.77% → 80.82%），并发会话把第 37 步落成 `bb201b1` 后整步让基线重测，
  不拼接两轮数字。
- **顺手改掉一句谎**：`test_exactly_seven_offline_commands` 断言的是 9（账本自 2026-09-06 起下线 9 条），
  改名 `test_exactly_nine_offline_commands`。
- **登记不静默修（F-65，待用户裁决）**：账本函数侧同一批孤儿——`stats()`/`get_command_by_name()`/
  `unknown_command_ids()`/`by_family()` 零生产调用点，其中两个挂在 `tstdx/protocol/__init__.py` 的
  `__all__` 上；`docs/archive/OPTIMIZATION_PLAN.md:32` 还把 `unknown_command_ids` 写成"保留为别名"，
  而被别名掉的 `unknown_commands()` 早已不在模块里。删公开查询面是对外契约收窄（F-44/F-47 同族），
  本步只把 `Command` 的字段面收口，未动这四个函数。

### Fixed（v17 Phase 5 第 37 步 —— 解码层的判断只有 bars 一条命令能上 wire：F-63① 的清偿）

- **第 26 步 F-51 的"已经接线"只覆盖了一条命令**：`tstdx/client/_mixin.py` 里有 15 处
  `_client_pkg.dispatch(...)`，而只有 bars 分页那一处读 `result.warnings`，其余 14 处只取 `result.rows`
  就把袋丢掉。产出侧从没缩水：`guarded_count` 在 8 个解析器模块有 43 个调用点会写「count 失真已钳制」，
  还有「记录截断：声明 N 实收 M」「L1 解析失败→降级」「L2 置信度不足→回落 L3 原始透传」。用户因此会拿到
  一份被截断、被降级、字段映射可能已错位的行，而 wire 上一条告警都没有——除了 `bars`。
- **修的是一个口，不是补十四处**：新增唯一的转发口 `_forward_decode_caveats(result, label)`（读
  `result.warnings` → `record_warning(DECODE_CAVEAT, …)`，原样返回 `ParseResult`），15 个分派点全部过它
  一次；bars 的旧 inline 循环删除但文案逐字未动，第 36 步的空桩守卫照旧成立。放大覆盖面的是通用口
  `_t_request_result`：`trade_today` / `block_*` / `goods_*` / `ex_*` / F10 目录等十几个公共方法共用它，
  接一次十几条面同时看得见解码判断。
- **归属行号按调用栈实测**：`_caller_stacklevel()` 沿栈找"离开 tstdx 的第一帧"。写死常量只对单跳成立，
  而 `block_list` → `request` → `request_result` 实测三跳，常量把告警记在 `_mixin.py:169`（库里）——
  归属错位的告警等于把缺陷指给一个没做错的人。异步侧只钉「通道 + 标签」，行号落在 `asyncio` 里，不假装修。
- **判据三条 + 守卫五条，变异四发各自 rc=1**：结构门禁钉住"含 `dispatch` 的函数必须调用转发口"
  （`scanned ≥ 15` 防扫描自身失效）、"每条文案以发起它的公共方法名开头"（复制粘贴错位即红）、"转发口
  本体必须真的 `record_warning(DECODE_CAVEAT)`"（空转即红）；行为守卫挑可达命令（`0x120F` 走通用口、
  `0x000F` 同步与异步各一条、干净页静默、归属行号）。M1 撤 `request` 口转发 → 红 4；M2 让转发口空转 →
  红 7（含 `DECODE_CAVEAT` 重新虚设、bars 那条旧守卫）；M3 stacklevel 退回常量 → 只红归属那条；
  M4 把 `ex_bars` 标签抄成 `goods_bars` → 只红标签门禁。
- **孤立 worktree 同轮复测**：基线（干净 `abef5e1`）3416 项 / 0 失败 / 5 跳过、80.83%；本步树 3423 项 /
  0 失败 / 5 跳过、80.85%（对账 `3416 + 5 守卫 + 3 门禁 − 1 被替换的 bars 专属门禁`），阈值 77 未下调；
  9 道主门禁两侧全部 rc=0（含 mypy 与 `ruff check` / `format --check` 465 文件），`tests/architecture`
  203 项 rc=0。
- **仍未做（等用户裁决）**：F-63②——`SECURITY_LIST_EMPTY_FIRST_PAGE` 的发射点在真实客户端路径上不可达
  （0x044D 登记为 `STATUS_OFFLINE`，`_guard_offline` 在 `_req` 里 fail-fast），而它的文案与 F-60 同法写死
  「声明 0 条」。要先定 `security_list` 面是「已下线，删 capability 与 CLI/HTTP/MCP 三个入口」还是
  「保留但明确只走替代命令」；本步没碰那条文案。

### Fixed（v17 Phase 5 第 36 步 —— 空首页告警替服务端编数字：F-60 的清偿，顺带量到 F-51 只接了一条命令）

- **本步量的是第 34 步自己留下的那句话**：F-59 修好握手帧 3 的产品标识块之后，同一条链路上留下一对互相否证的
  告警。线路实测的 `0x052D` 空桩声明 **800 条**（载荷 `2003` 小端 = `0x0320`），解码侧照实说
  「count 失真已钳制：声明 800 条，按剩余字节 16B/条 只能容纳 0 条」，而 `BARS_EMPTY_FIRST_PAGE` 说的是
  「服务端声明 **0** 条记录」——后者是 `tstdx/client/_mixin.py` 里一句写死的字符串，把「声明 0」与「声明 N
  却回 0 个记录字节」压成同一件事，而这两件事的处置完全不同（前者是该标的没有这段历史，后者是服务端回了个
  不携带任何记录字节、却仍声明 800 条的桩），区分它们恰是这条告警存在的唯一理由。判据方向对，数字是编的。
- **为什么 3413 项测试全绿也看不见它**：`tests/client/test_v5_pagination.py` 造空页用的是
  `_bars_payload(0)`，即载荷 `0000`＝**真的声明 0 条**，fake 与被测代码犯了同一个错，两条断言因此彼此自洽；
  该测试还把告警条数钉成「恰 1 条」，等于把「同一个结果上两条互证」这个真实形状排除在射程外。本步把空桩 fake
  换成线路实测的 `2003`（新增 `_StubPayloadPool`），判据随之改为「2 条且两条都含 800、第二条不得出现
  『声明 0 条』」，并给真声明 0 那条补上文案断言——两支各有主。
- **改法三件**：① `tstdx/protocol/registry.py` 把 `state["declared_count"]` 随 `ParseResult.meta` 暴露
  （走 `meta` 的内部形状接线，不引入对外契约）；② `_mixin.py` 的空首页文案改为按声明数三分支——`N>0` 说
  「声明 N 条却一个记录字节都没回，这是空桩，不是该标的没有历史」，`0` 说「声明 0 条：该标的无此周期历史或
  主站对这条命令只回空桩」，读不到计数头则说「声明数未知」，**不拿未知冒充 0**（那正是本条原罪的镜像），
  `strict` 的 `TruncatedDataError.context` 加 `"declared"` 键让机读侧与文案同数；③ 两处旧注释、
  `tstdx/diagnostics.py` 的 `WarningCode` 注释与 `docs/errors.md` 的易混对照同批改写。
- **变异四发各自 rc=1**：M1 退回写死 0 → 只红新空桩守卫（证明它咬的是文案不是条数）；M2 撤掉 `meta` 暴露 →
  红三条，含既有那条，说明「声明 0」这句现在也是从线路上读的；M3 让「声明为 0」那一支永不成立 → 只红
  真声明 0 那条（两支确实分家）；M4 删 `context` 的 `"declared"` → 只红 strict 那条。
- **顺带登记不静默修（F-63）**：查 `DECODE_CAVEAT` 发射点时量到 15 个 dispatch 调用点里只有 bars 那一处读
  `result.warnings`，其余 14 处只取 `result.rows`，把解码层判断整族丢弃——第 26 步 F-51 的「接线」只覆盖了
  一条命令，而 `guarded_count` 在 8 个解析器模块里有 43 个调用点会产出这种判断；同时 `SECURITY_LIST_EMPTY_FIRST_PAGE`
  的文案与 F-60 同法写死「声明 0 条」，而 0x044D 登记为 `STATUS_OFFLINE`、`_guard_offline` 在 `_req` 里
  fail-fast（实测 `TdxClient(pool=fake).security_list(0, 0)` 直接 `CommandOffline`），于是它只在把
  `security_list` 整个 monkeypatch 掉的测试里才发射——那格绿是一个假桩的绿。两条都写进 §0.3 F-63，本步不动
  代码，其中②需用户裁决（要么承认这条面已下线并删 capability 与三个入口，要么保留并明确只走替代命令）。
- **编号竞争**：并发会话在 `wt_f61` 以「第 35 步」写 F-61/F-62 的账本清偿，本步测量时仍未提交；按第 33 步同法
  让号取 **36**，新发现跳过 F-61/F-62 用 **F-63**，不复述也不改它们的行。
- **复测（本机 Windows+py3.13.14，同一轮日志；孤立 worktree = `f60c3b5` + 本步全部 7 个文件，与提交树逐文件
  `cmp` 相同）**：基线取
  干净 `f60c3b5` 同轮实测 junit **3413 / 0 failures / 0 errors / 5 skipped**、143.3s、覆盖率 **80.82%**
  （与第 34 步记的同一棵树逐格相同）；本步树 junit **3415 / 0 / 0 / 5**、135.9s、覆盖率 **80.83%**
  （`junit_s36_final.xml` / `full_s36_final.log`；先于账本落盘的 `runA`/`runB` 两轮逐格相同），对账
  `3413 + 2 条空桩守卫 = 3415`，阈值 77 未下调。同树 9 道主门禁全部 rc=0（originality Total 190 / Suspicious 0、
  `spec_audit --strict` coverage 100.0%、golden `[GATE] all L1 verified commands have real samples (OK)`、
  reachability 189/172/17 无未登记孤儿、`contract_audit --ci` 63 契约 · 155 capability、docs links 82 文件、
  mypy 无输出、`ruff check` All checks passed!、`ruff format --check` 464 files already formatted），
  `tests/architecture` 200 项 rc=0。

### Fixed（v17 Phase 5 第 35 步 —— 把跑过的冒烟写成"尚未执行"：现网口径与 §1/§4 记录对齐，F-61/F-62）

- **F-58 的镜像病，方向相反**：§0.1「主链路贯通状态」的结论第一条边界写着"真机冒烟尚未执行……
  需用户授权"，而 §1 第 16 步逐格记着七格真实网络/服务面冒烟已经跑过（6 PASS / 1 FAIL）与 wheel
  安装冒烟 `SMOKE_RC=0`，§4 验收清单该格也已勾 `[x]`。同一句话在另外三张现在时面上重复出现：
  本文 Phase 5 计划项 3 仍挂"⚠️ 仍未执行"、README 路线图行写"仍待：…… + 真实网络 smoke + tag"、
  README「下一阶段」把已跑通的 wheel 冒烟与三面 live 各一发仍列为计划。
- **后果不是保守而是失真**：读者会认为主链从未在现网验证过，并把 F-37 读成"冒烟还没跑"而不是
  "跑过；K 线那一格已在第 34 步归因为本端握手字节并修好，余下是 `0x000F`/`0x0010` 字段错位待
  裁决"——本轮向用户复述现状时确实这样读过一次，错的是文档。
- **成因**：第 33 步重写 §0.1 时把"再跑一次须授权"（现行约束）与"从未跑过"（历史事实）揉进同
  一个短句。凭印象写的边界既不挂账、也没有任何判据核对，于是全绿。
- **改法**：§0.1 结论第一条改为"现网证据只到一次性冒烟为止"，就地标注第 16 步读数与第 34 步对
  `rows=0` 那一格的归因，并把"仍缺"精确到两件事（一次工作日盘中复跑、F-37 余条的处置裁决），
  "再跑一次真实网络仍须用户授权"作为现行约束保留；第二条缺口清单换成有账可查的四项（F-37 余条、
  F-38、F-25 的 PENDING 面、F-18）。Phase 5 计划项 3 与 README 三处同批改口径。
- **覆盖率一格重钉**：README 路线图里"离线整仓覆盖率 78.80%"是 Phase 5 第 4 步的本机读数，此后
  整仓删码已把它推高；改为本步同轮日志的实测值并标明"本机 Windows+py3.12、CI 数字仍待重钉"。
- **门禁补第三条判据** `test_open_boundaries_cite_an_open_finding`：§0.1 结论里每个带圈编号的
  边界子句必须点一个 §0.3 真实存在的 F 号，且其中至少一个是开放裁决（未清偿/部分清偿/部分处理/
  本轮只登记/本步只登记/待用户决策/维持现状/未处理）。一个都不点＝给凭印象的说法发通行证；只点已清偿的账＝把做完的事写成
  待办；点了账本里没有的号＝幻影引用。三种失败各有自己的消息。分母是 §0.3 的账本行本身，本步在
  提交基线 `b5a54f0` 上实测 52 行、开放裁决 7 条（F-18/F-20/F-37/F-38/F-44/F-47/F-63）。
- **判据上线即红在自己身上（本族第四、第五、六次）**：第四次——新加的"裁决格必须读得出来"自检
  当场报出 `['F-43', 'F-46']`。表格切格原先按 `|` 裸切，而账本里行内竖线有两种真实形态——转义的
  `tests/…\|scripts/…`（F-24）与反引号里的正则字面量 `` `a|b` ``（F-43、F-46）——被劈成碎片的裁决格
  里没有任何裁决词，这三行在原判据下**永远读成"已清偿"**，一条挂着的账就这样从分母上安静消失。
  改法是新增 `_split_row()`：反引号内与转义后的 `|` 不作分隔符。同批把第 34 步 F-60 用的
  "本轮只登记"补进开放裁决词表（它确实是活账，此前 `_is_open` 把它读成已清偿）。第五次——把树让到
  并发会话的 `b5a54f0` 之后重跑，同一条自检立刻报出 `['F-63']`：那行裁决写作 `**本步只登记，不改**`，
  而我上一轮刚把"本轮只登记"收进词表，同义词差一个字就当没看见，词表因此补入"本步只登记"。
  第六次就是下面这条 F-62，改错的是我自己。
- **变异 6 条全部 RC=1 且逐条指名**（CONTROL 与还原后 RC=0）：**M1** §0.1 里把现存路径换成已删
  模块 → `§0.1 引用了磁盘上不存在的模块：['tstdx/runtime/gateway.py']`；**M2** §0.2 抹掉一行裁决 →
  `§0.2 这些行没有当前裁决（或缺日期/提交号）：['F-5']`；**M3** §0.1 标题改名 → 三条判据同时报
  "门禁自身失效"；**M4** 把 F-61 原病装回（边界子句不挂任何账）→ `没有 F 号：现网证据只到一次性
  冒烟为止，不是"从未上过现网"：…`；**M5** 边界子句改点已清偿的 F-31 →
  `只点到已清偿的账：['F-59', 'F-31']`（该子句原有的 F-59 已被第 34 步判为已清偿，于是两条引用
  全是死账）；**M6** 点账本里不存在的 F-99 → `§0.3 里没有这些行：['F-99']`。
- **F-62 是本步自己撞上的第六次，而且这一次改错的是我**：本步原把"账本 12 处复测行写着
  `Windows+py3.13`"判为假标签并批量订正成 `py3.12`，依据只有 `.venv/pyvenv.cfg`（`cpython-3.12.13`）、
  `%APPDATA%\uv\python` 目录清单与本机覆盖率头。撤回依据是同机对照：同一棵 `f60c3b5` 基线，并发会话
  第 36 步记 5 skipped / 80.82%，仓内 `.venv` 这轮记 7 skipped / 80.75%——同一棵树、两个环境、两套
  数字都真，"这台机器上从未存在过 3.13"因此不成立。**批量改写的后果不是排版错误，而是把他人的正确
  实测涂成假的**，性质与 F-59"证据指向不存在的文件"同类，只是方向相反。12 处已全部恢复原文，改立新
  口径：**复测行必须写测量者当轮实际使用的解释器与所在环境，历史条目的标签由其作者维护，别的会话不得
  批量改写**；仍**不为它加门禁**——环境标签是每条记录各自的事实，硬钉判据只会造出随环境漂移变红的假
  门禁。详见 §0.3 F-62。
- **本步不动生产代码**：只改两处账本、README 与那条文档门禁测试；未执行任何真实网络请求（再跑
  一次仍须用户授权），因此运行期行为与覆盖率构成不变。
- **编号竞争第三次踩同一坑，这次撞在提交那一刻**：本步原记"第 34 步 / F-59"，测量期间被并行会话的
  握手那轮（`f60c3b5`）占号，改判第 35 步 / F-61 后在其上全量重测；落盘提交时 CAS 又撞上并发会话的
  第 36 步（`b5a54f0`）刚刚合入，`update-ref` 按预期拒绝，于是整步再让基线、三方合并并在 `b5a54f0`
  上重测第二轮。两次让号都没动别人的行，第 36 步也明确"跳过 F-61/F-62 用 F-63"。
- **复测（本机 Windows+py3.12，即仓内 `.venv`；同一轮日志，孤立 worktree = `b5a54f0` + 本步 4 个
  文件）**：干净基线 `b5a54f0` 同轮 junit **3415 tests / 0 failures / 0 errors / 7 skipped**、覆盖率
  **80.76%**；本步树 junit **3416 / 0 / 0 / 7**（对账 `+1 条边界账判据`）、覆盖率 **80.77%**（语句分母
  22559 未变、miss 3745→3743）、`SUITE_RC=0`、阈值 77 未下调；`tests/architecture` 201 项 rc=0（基线
  200 项），13 道门禁全部 rc=0，变异 6 条全部 RC=1。详见 §1 第 35 步复测行。

### Fixed（v17 Phase 5 第 34 步 —— 握手帧 3 的产品标识块：一句未验证的断言让 K 线整族恒 0 根，F-59/F-37）

- **现象与归因**：F-37 记的"7 台可达主站对 `0x052D` 只回 2 字节空桩（K 线恒 0 根），而请求体与 2026-08-31 拿到
  180 字节真样本时逐字节相同"——本轮证明**它既不是服务端行为也不是时段效应，而是本端握手帧 3 的 30 字节产品标识块**。
  同主机、同一条新建连接、请求体照抄 golden，只换这 30 字节：重放自采集样本（含 GBK 券商名）⇒ `payload=2B rows=0`，
  换成 30 个零字节 ⇒ `payload=180B rows=10`（首根 `2026-09-07 15:00`、`close=9.23`）。7 台可达主机各 2 轮全同向，
  golden 采集主机再交替 3 轮（累计 5 次），7/7 无一例外。
- **根因是两处"✅ 实测"从未被实测**：`tstdx/protocol/handshake.py` 写着"服务端不校验内容，只校验长度"，证据点名的
  `tests/integration/test_handshake.py`（及 `test_opaque_blob_tolerance`）与 `tests/golden/7709/_handshake/`
  **在仓库任何一次提交里都没有出现过**（`git log --all --diff-filter=A` 空）；同一文件另一句"三帧都得发"同样从未被测
  ——只发帧 1+2 时 7/7 主机照样回数据。而全仓对握手字节的测试数为 **0**（本步之前 `setup_frames` 在 `tests/` 里只命中
  `.pyc`）。默认值照那句假断言选了"原样重放抓包样本"，于是整族 K 线在真实主站上永远是空的。
- **改法四件**：① 默认块改为 30 个零字节（`_PRODUCT_ID_BLOB`，附长度断言）；② 三个零调用点符号 `OPAQUE_BLOB_BYTES` /
  `opaque_blob()` / `build_setup_frame3()` 物理删除，`__all__` 收缩为 3 项（clean break，不留别名）；③ 模块 docstring
  与 `PROTOCOL_SPEC/7709/0x000D_HANDSHAKE.yaml` 改写为本轮可复算口径，并显式点名那两处假证据；④ 新增
  `tests/protocol/test_handshake_frames.py` 9 项离线线路形状守卫（默认零块、帧 3 `pkg_len == 32` 与两处长度字段和
  `method=0x0FDB` 逐个对齐、帧 1/2 步骤号未动、覆盖只替换帧 3 body（STANDARD/MAC）、长度非 30 即 `ValueError`、
  EXTENDED/F10/GOODS 仍返回空元组）。
- **本轮另测出的三条边界，都写进 docstring 而不是留给下一个人踩**：`b"A"*30` 与 `b"\xff"*30` 同样拿回 180B/10 根，
  所以关键不是"零"，而是别把抓包样本当默认值重放；全零块把末 4 字节换成样本块的 `00000002` 会让 3/3 主机在握手中途
  直接断开连接 ⇒ 这块字节被结构化解析，"opaque"这个命名本身就在替一个错误模型说话；完全不发握手帧时 6/7 主机对
  `0x052D` 读超时，但 60.191.117.167 回了数据，且 218.75.126.9 在相隔两分钟的两轮里一次回数据、一次读超时——按事实
  登记为**不稳定**，不写成契约。
- **端到端复验（走唯一业务入口、无任何 monkeypatch）**：`Client().bars(…, strict=True)` × {sh600000, sz000001,
  sz300750} × {day, week, month, 1min, 5min, 15min, 30min, 60min} = **24/24 各 10 根**，每条都 `kind=direct`、
  `cache_tier=None`、`warnings=0`；`count=320` ⇒ 320 根；`BAD=0`。**F-37 因此只闭合了 K 线那一半**：0x000F
  `capital_changes` 与 0x0010 `finance_info` 在新默认握手下仍解出错位字段（首行 `market=48` / `code='000\x01'`，
  0x0010 首行 `market=0`、`code=''`、`values` 全 0），而 0x0010 的 live 载荷长度在两台主机上都与 golden 同长
  14302 字节——错位属解码布局一侧，且这两条命令的 golden 从不校验解析值；需用户裁决，本步不擅自改解析器。
- **顺带登记不静默修（F-60）**：第 21 步给空首页立的告警写着"服务端声明 0 条记录"，而本轮的 2 字节空桩声明的恰恰是
  800 条（`2003` 小端 = `0x0320`）。把那个桩原样注入传输层、其余走生产链路实测，同一个结果发出 **2 条互相否证的
  告警**：`decode_caveat` 说"声明 800 条，按剩余字节 16B/条 只能容纳 0 条"，`bars_empty_first_page` 说"服务端声明
  0 条记录"。第 26 步 F-51 把解码侧真话接进 wire 的那条链是好的，坏的是 `tstdx/client/_mixin.py:254-258` 那句写死
  的数字（`:226-227`、`:252-253` 两处注释同错）；而 `tests/client/test_v5_pagination.py` 的 fake 用
  `_bars_payload(0)` 造了个"真声明 0"的假桩，于是这一对在测试里永不出现——本步只登记，修复形状见 §0.3 F-60。
- **为什么取全零而不是"干脆不发帧 3"**：两条在本轮实测都拿得到数据；取全零保留的是与真实客户端同形的三帧会话形状，
  `setup_frames()` 的帧数契约、两池握手计数与既有测试形状都不动，而"不发帧 3"要新增一项对外协议声称，不属本步。
  **未解释的**：服务端为什么对那 30 个字节回空桩而不是报错——机制未知，本轮只登记可复算的行为。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `1be93ea` + 本步 5 个文件，与主树提交前内容逐文件相同）**：
  基线取干净 `1be93ea` 同轮实测 junit **3404 tests / 0 failures / 0 errors / 5 skipped**、142.1s、`--cov=tstdx`
  **80.77%**；本步树 junit **3413 tests / 0 failures / 0 errors / 5 skipped**、135.3s、
  `--cov=tstdx` **80.82%**、`SUITE_EXIT=0`（对账：`3404 + 本步 9 条线路形状守卫 = 3413`，阈值 77 未下调）。同树 9 道主门禁全部 rc=0（`GATES_RC=0`，`gates_s34b.log`）：originality（Total 190 / Original 190 / Suspicious 0 / External imports 17）、`spec_audit --strict`（coverage 100.0%）、golden 审计（`[GATE] all L1 verified commands have real samples (OK)`、`0x052D real category coverage: [0…11]`）、reachability（189 模块 / 172 可达 / 17 白名单豁免，无未登记孤儿）、`contract_audit --ci`（63 Typed Query 契约 · 155 capability）、docs links（82 文件）、mypy（rc=0，无输出）、`ruff check`（All checks passed!）、`ruff format --check`（464 files already formatted）；`tests/architecture` 9 个文件共 200 项 rc=0。

### Fixed（v17 Phase 5 第 33 步 —— 方案文档的「现状判定」与磁盘同真，F-58）

- **本步量的是方案文档自己**：`docs/REFACTOR_PLAN_V17_CLOSURE.md` §0.1「主链路贯通状态」一直
  停在 Phase 3 之前的时态——两行 ❌ 把 v14 编排信封与 registry 三件套写成**现行断链**，而它们
  的证据列点名的 `runtime/gateway.py`、`executor_registry.py`、`provider/router.py`、
  `executor_bindings.py` 早已在 Phase 3A/3B 整层物理删除、磁盘上不存在；服务面那行还写着
  "全部 import `client_api.Client`"，那个根级模块也在 Phase 3C 并入 `tstdx/client/`。§0.2
  的八行"遗留不合理点"统一挂在"Phase 3–5 处理对象"下，其中七行在文档别处已记为清偿，却没有
  一行把判决写回表格——于是"还有哪些不合理、链路是否贯通"这个问题按字面读会得到"还有八条待办
  加两条断链"的答案。**编号竞争**：本步产物先按 `0d7fbe1` 写好并测过一轮，期间并发会话把 F-57
  作为第 32 步提交（`85cc43e`），故本步序号让到 **33** 并在其之上整轮重测（变异也重跑）。
- **为什么既有活文档门禁抓不到**：`test_doc_code_consistency.py` 校验的是反引号里的
  `tstdx.x.y` **点号**路径与 README 数字；表格里的**带斜杠文件路径**与**时态**都不在射程内。
  这是 F-23/F-34/F-35 同族的第四处，位置在被当作事实源的文档本身。
- **§0.1 重写为五行现在时**：内核主链 / 四个服务面 / 流式面 / 配置面 / 已删除的旧接缝。原来那两
  行 ❌ 改判为"✅ 已整层物理删除，因此不再可能是断链"并指向 16 项 `unimportable` 防回潮守卫；
  结论行就地写清两处"实现"的边界（真机冒烟未跑、F-37 的 2 字节桩、92 项 PENDING 契约、
  F-18 的链外守卫），不把未做的说成做了。
- **§0.2 增列「现状」**：八行逐条补 `**已清偿**（2026-09-19，…）` 并指向清偿它的那一步/那一
  Phase，历史问题陈述与证据列原文保留——改的是"这条现在还开着吗"，不是重写历史；§0.2 的
  标题也从「Phase 3–5 处理对象」补为「…；第 33 步起逐行现状见最后一列」，否则只看标题仍会
  读成八条待办。
- **新门禁 `tests/architecture/test_plan_status_gates.py`（4 条测试）**：① §0.1 表体里每个反引号
  文件路径（可带 `:行号`）必须存在于磁盘，先试仓库相对路径再试 `tstdx/` 下同名路径；② §0.2 每行
  的最后一格必须以加粗裁决开头（已清偿/已修/部分处理/待用户决策/维持现状）并含日期或提交号；
  ③④ 两节标题仍在（改名即红），外加"§0.1 ≥4 行且 ≥5 条路径、§0.2 ≥6 行"的防盲断言——解析不
  出行判门禁自身失效，不静默通过。
- **判据被自己的变异修硬了一次**：M2 首轮 **RC=0 逃逸**。原因是裁决正则放宽成子串匹配，而 F-5
  那行的判决文字里含"不再依赖**已删除**的信封线"——一句历史叙述替当前裁决打了勾。收紧为"必须以
  `**裁决**` 开头"后 M2 转红。同轮还发现自己把八行的判决并进了「证据」格（少写一个 `|`，表格
  列数与表头不符），一并修回五列。
- **变异验证 3 条全部 RC=1 且各自指名**（CONTROL 与还原后 RC=0）：**M1** 把 §0.1 里现存的
  `tstdx/runtime/executor.py` 换成已删除的 `tstdx/runtime/gateway.py` → 红并点名该路径；
  **M2** 抹掉 F-5 行的加粗裁决 → 红并点名 `['F-5']`；**M3** 把 §0.1 标题改名 → 三条判据同时红，
  报的是"解析不出节，门禁自身失效"而不是"没问题"。
- **本步不动生产代码**：`tstdx/` 零改动，因此运行期行为、契约与覆盖率构成都不变；新增的是 4 条
  门禁测试。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `85cc43e` + 本步 3 文件）**：离线全量
  junit **3404 tests / 0 failures / 0 errors / 7 skipped**、`SUITE_RC=0`、143.3s；`--cov=tstdx`
  **80.69%**（阈值 77 未下调）。对账上一步（第 32 步）在其提交树上读到的 **3400** ＋ 本步 4 条新
  门禁 = **3404**；百分比与上一步的 80.77% 同量级，因为本步零改生产代码、覆盖率构成不变。7 条
  skipped 逐条取自同轮 junit：全部落在 `tests/output/test_sinks_dispatch.py` 的 parquet/duckdb
  缺依赖用例上。同树 13 道门禁全部 RC=0：`ruff check`、`ruff format --check`（433 文件）、`mypy`
  （0 error）、originality（Total 190 / Suspicious 0）、reachability（189 模块 / 172 可达 / 17
  白名单、`无未登记孤儿 ✓`）、`contract_audit --ci`（63 契约 · 155 capability · 92 项 PENDING 不
  阻断）、`spec_audit --strict`（`coverage_pct: 100.0`）、golden 审计、adversarial、bridges、
  `tests/streaming + tests/runtime`、benchmark smoke、docs links（82 文件）。变异 CONTROL 与还原后
  RC=0，M1/M2/M3 各自 RC=1 并逐条指名（M1 点 `tstdx/runtime/gateway.py`、M2 点 `['F-5']`、M3 报
  "门禁自身失效"）。**口径边界如实登记**：上面的数字取自只差本条复测文字与三处账本散文订正（§1
  序号让位后的 `33.`、§0.2 标题与编号竞争说明、两处换行）的那棵树；写入后在**同一提交树**重跑
  `tests/architecture`（含本步新门禁与既有文档门禁）与 `check_docs_links.py`，两道均 RC=0。

### Removed（v17 Phase 5 第 32 步 —— 出处词表里那两种不可能出现的出处，F-57；**BREAKING**）

- **`ProvenanceKind` 只剩 `DIRECT`**：`REPLAY`/`SYNTHETIC` 两条成员被物理删除。它们在 `tstdx/` 全部
  189 个模块里的唯一引用是它们自己的判定属性——没有任何代码能造出这两种出处，却有代码在教调用方
  如何判断它。零缓存内核只有一条构造路径（`Provenance.direct()` 写死 `ProvenanceKind.DIRECT`），词表比
  运行期宽，就是给"将来的层"留门。
- **`Provenance` 上的三条判定属性 `real` / `replay` / `synthetic` 一并删除**：出处由 `kind` 字段本身说明；
  三条属性对 `tstdx/` 生产代码的读取点为 **0**，全仓唯一读取是 `tests/runtime/test_query_contracts.py` 里
  的一条断言，它验证的是规则的抄本而非规则本身。该断言随之删除，同族规则仍由
  `assert direct.kind is ProvenanceKind.DIRECT` 与退役词汇（`cached`/`cache_hit`/`direct_fetch`）循环钉住。
- **wire 侧词汇如实收窄**：`integration/serialization.py` 发射 `provenance.kind.value`，删除成员即 `kind`
  在 HTTP/MCP 响应上只能取 `"direct"`——这不是新增限制，此前 `replay`/`synthetic` 也从没出现在任何真实
  响应里；`cache_tier` 仍恒为 `null`（第 30 步口径不变）。
- **新增门禁（`tests/architecture/test_result_shape_gates.py`，两条）**：
  `test_every_declared_provenance_kind_has_a_producer` 以枚举自身为分母（F-43 口径：清单会过期，枚举不会），
  以 AST 扫 `tstdx/` 得到的 `ProvenanceKind.<MEMBER>` 具名引用为分子，双向差集——声明了没人生产红、引用了
  不存在的成员同样红；`test_provenance_exposes_no_judgement_property` 要求 `Provenance` 类体里 property 恒为
  空集，判定翻译层一旦回长即红。防盲三条沿用：`scanned > 30`、引用集合非空、声明集合非空。
- **尺子补出成员维度，并收掉它的第三份抄件**：`tests/support/field_readers.py` 新增
  `members_referenced(owner, *, skip="")`；`tests/architecture/test_caveat_channel_gates.py` 的 `WarningCode`
  发射点判据改为调用它，删去自带的那份私有 AST 遍历（同一把尺子抄第三次即本族要删之物）。
- **变异验证（4 项，逐项复原后逐字节比对主树哈希）**：M1 把 `REPLAY` 装回枚举 → 红并点名 `['REPLAY']`；
  M2 让 `real` 属性回长 → 红；M3 让尺子失明（扫一个不存在的 owner 名）→ 红，报的是"扫描自身失效"自检
  而非"零幻影"；M4 生产者躲开扫描（`Provenance.direct()` 改为按值构造 `ProvenanceKind("direct")`）→ 红。
  BASELINE 与还原后 RC=0，`MUT_RC 0`。
- **未采纳路径如实登记**：(a) 让 golden 回放生产 `REPLAY` 出处＝在公开结果面上引入一种真实 Provider 读取
  永远给不出的出处，与 v17 反向；(c) 保留成员挂"将来会用到"注释＝F-50/F-52/F-54/F-55 逐次删除的形状。
  `docs/adr/ADR-013`、`CONTRIBUTING.md`、PR 模板里"replay/synthetic 不得冒充实时数据"是**禁令**而非存在性
  声称，删成员让它更强，故原文保留。
- **编号说明**：本步代码先按 `60f6be9` 写好并测过一轮，期间并发会话把 F-56 作为第 31 步提交（`0d7fbe1`），
  故序号让到 32 并在新基线上整轮重测；第一轮数字不入账。
- **实测**：基线（干净 `0d7fbe1`，同轮）junit **3398 tests / 0 failures / 0 errors / 5 skipped**、139.7s、`--cov=tstdx` **80.77%**；本步树 junit
  **3400 tests / 0 failures / 0 errors / 5 skipped**、143.2s、`FINAL_RC=0`、`--cov=tstdx` **80.77%**（对账 3398 ＋ 本步 2 条新门禁 = 3400，
  阈值 77 未下调），同树 9 道主门禁全部 rc=0（`GATES_RC=0`），CI 另三道作业（bridges / adversarial /
  benchmark smoke）在同一棵树上单独复跑：bridges RC=0、adversarial_matrix RC=0、benchmark_smoke RC=0。

### Changed（v17 Phase 5 第 31 步 —— CLI `stream` 收回 `Client` 面，F-56；**BREAKING**）

- **`tstdx stream` 从此经 `Client.stream` 起流**：`cmd_stream`（`tstdx/cli/runtime_commands.py`）
  不再 `from ..streaming import QuoteStream`，改为
  `with Client(**_client_kwargs(args)) as client: client.stream(symbols, provider=…, interval=…,
  diff_only=…, max_queue=…, on_quote=…, on_error=…)`，随后 `start()` / `stop()` 与打印计数留在
  CLI 一侧。退出码判据（零数据 → exit 1）逐字不变；连接参数由 `_transport_kwargs` 换回
  `_client_kwargs`，即"只转达用户显式说过的，其余交给内核读配置面"。
- **两面同答案**（这是本步的实际缺陷）：`--provider` 此前是自由字符串，旁路把它当
  `default_provider` 透传，`QuoteStream._get_runtime()` 再惰性 new 出第二个 `UnifiedRuntime`，
  于是 CLI 承诺"注册表支持 `quotes` 轮询的 Provider 都能 stream"（`PROVIDERS.supports("tencent",
  "quotes")` 为真），而库面 `Client.stream(provider="tencent")` 抛 `ValidationError`。现在
  `tstdx stream --provider tencent` 得到 exit 2 + 错误信封，与库面同一句话。
- **顺带补上 `stream` 缺的 `--host`**：该命令原先自己声明 `--provider` 而完全没有 `--host`，
  是 Tier-A 数据命令里唯一不能钉主站的一条；旁路时代即便声明也无人消费。现由 `_provider_args()`
  统一声明，与 `snapshot`/`minute`/`trades` 同形。
- **守卫**：新增结构性门禁 `test_service_faces_never_build_a_stream_themselves`——与 F-29 那条
  共用 `_service_face_imports()`（AST 扫 CLI + HTTP/WS/MCP 全部 import 边，函数体内的 import 同样
  算），任何解析到 `tstdx.streaming` / `tstdx.stream_contract` 的边即为红，扫描零命中时
  "两条门禁同时失明"自曝。`test_stream_forwards_provider_and_connection_args` 从"monkeypatch
  `tstdx.streaming.QuoteStream` 钉住旁路形状"改写为捕获 `Client` 构造参数与 `Client.stream` 实参
  （含 `on_quote` 回调确实被转达、`with` 退出即关客户端）；新增
  `test_stream_command_refuses_a_provider_the_stream_contract_refuses` 用**真实** `Client`
  跑 `main(...)` 并核对信封文案（禁网两拦，见下条）；`tests/unit/test_cli_semantics.py` 两条
  stream 用例的替身同样从 `QuoteStream` 换到 `Client`。
- **本步的变异装置先量到自己**：`--provider` 一旦不转发，那条走真实 `Client` 的回归用例会
  真的把 tdx 流起来——首次测量时它 `assert 0 == 2`，即离线用例在回归现场触了网。现按
  `test_dunder_docstring_quickstart_examples_construct` 的既有做法拦
  `socket.getaddrinfo` / `socket.create_connection`，同一条变异改报 `assert 1 == 2`（拒绝仍成立、
  egress 不再发生）。判据同时收紧到信封文案，任何别的 exit 2 都不算通过。
- **口径归位**：`tstdx/streaming/__init__.py` 的 docstring 不再声称 `QuoteStream`/`AsyncQuoteStream`
  "已从公开/核心面移除"（它们既在 `__all__` 里、又正是 `Stateful*` 的轮询基类），改写为"唯一入口是
  `Client.stream`，基类不得由服务面直接构造"；`docs/api/interfaces.md` §5 由"QuoteStream /
  AsyncQuoteStream 两个门面"重写为三层（`Client.stream` 唯一入口 → `Stateful*` 生命周期对象 →
  轮询基类 + engine 组件），§3 服务面口径补上第二条守卫名并显式写明"含 `stream`"；README 数据源层
  的"流式订阅"行同批指向 `Client.stream`。
- **本步让一条既有宣称第一次为真**：`docs/api/interfaces.md` §3 与 `docs/api/README.md` 自第 15 步
  起就写着"数据命令全部委托同一个 `Client`，6 个传输/诊断命令除外"，而 `stream` 当时仍是第 7 条
  旁路——该此前无人把守、也从未成立过。
- **变异验证 3 条全部 RC=1 且各自指名**（CONTROL 与还原后 RC=0）：**M1** 在 `cmd_stream` 里加回
  `from ..streaming import QuoteStream` → 新守卫红，报
  `tstdx\cli\runtime_commands.py: import tstdx.streaming`；**M2** 删掉 `provider=args.provider` →
  三条同时红：转发用例 `KeyError: 'provider'`、拒绝用例 `assert 1 == 2`、F-28 的选项消费审计报
  `stream: --provider (dest=provider)`（两条判据各看一半，转发与消费互补而非重复）；**M3** 把
  `--host` 声明撤回 `--provider` 独写 → 转发用例在 `parse_args` 处 `SystemExit: 2`。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `60f6be9` + 本步 9 文件，与本步提交树
  逐文件同内容）**：离线全量 junit **3398 tests / 0 failures / 0 errors / 7 skipped**、`SUITE_RC=0`、
  167.3s；`--cov=tstdx` **80.70%**（`Required test coverage of 77.0% reached`，阈值 77 未下调）。
  对账上一步（F-52，`60f6be9`）的同轮 **3396**：本步净增 2 条＝新守卫
  `test_service_faces_never_build_a_stream_themselves` ＋ 新回归
  `test_stream_command_refuses_a_provider_the_stream_contract_refuses`。同树 13 道门禁全部 RC=0：
  `ruff check`、`ruff format --check`（432 文件）、`mypy`（0 error）、originality（Total 190 /
  Suspicious 0）、reachability（189 模块 / 172 可达 / 17 白名单、`无未登记孤儿 ✓`）、
  `contract_audit --ci`（63 契约 · 155 capability，PENDING 仍是既有的 Typed Query 缺口，本步未新增）、
  `spec_audit --strict`（`coverage_pct: 100.0`）、golden 审计、adversarial、bridges、
  `tests/streaming + tests/runtime`、benchmark smoke、docs links（82 文件）。变异复跑在同一棵树上：
  CONTROL 与 RESTORED RC=0，M1/M2/M3 各自 RC=1 且逐条指名（见上三条）。
  **口径边界如实登记**：上面的数字取自 9 个文件里 7 个代码/文档文件与本步两份账本正文都已落地的树，
  只差本条复测文字本身；写入后在同一提交树重跑 `tests/architecture`（25 条连接契约＋全部活文档
  判据）与 `check_docs_links.py`，两道均 RC=0，即本条所述与它所声称的那棵树互不矛盾。

### Removed（v17 Phase 5 第 30 步 —— 出处里那个从未被填过的源时间戳，F-52 provenance 半边；**BREAKING**）

- **`Provenance` 少了一个公开字段**：`provider_timestamp`。`tstdx/result.py:57` 上它写着
  `provider_timestamp: str | None = None`，`Provenance.direct()` 把同名形参原样透传，而三重沉默
  同时成立：AST 扫 `tstdx/` 全部 189 个模块读取点 **0**、没有任何调用方给 `direct()` 传过非默认值
  （即没有 Provider 上报过源侧时刻）、`integration/serialization.py` 按显式键构造 wire 而键集合里
  没有它。按 clean break 物理删除字段与形参，不留 `= None` 兼容位；`Provenance` 现为
  `provider / channel / capability / kind / observed_at_ns / cache_tier / requested_provider / fallback`
  八个字段，逐字段读取点实测 2、2、2、2、1、1、1、1。
- **为什么不接线**：接线要求 9 个解码器开始上报"源侧时刻"——那是新增的对外承诺，而它们今天一个
  都拿不出这个数据；`observed_at_ns` 承载的才是唯一真实可知的时刻（本地观察时刻）。让这个字段
  继续躺着，等于对外声称直连结果的出处比观察本身更丰富。
- **删除由判据双向钉住**：新增 `test_deleted_provenance_field_is_not_a_back_door`——显式关键字
  构造 `Provenance` 当场 `TypeError` 且消息含字段名（不静默收下再丢），`Provenance.direct()` 的
  签名里也不存在该形参。
- **新门禁 `tests/architecture/test_result_shape_gates.py`（5 条）**：`Provenance` 与 `ResultMeta`
  各一条读取点判据、各一条顺序敏感形状清单，加那条后门判据。owner 名逐类人工核对
  （`Provenance` 认 `provenance/p/prov`，`ResultMeta` 认 `meta/result`），**不放 `self`**——类内
  `__post_init__` 的自检不算字段被人兑现的证据。防盲保险沿用第 27/28 步三条：`fields` 非空、
  `scanned > 30`、`reads` 非空。
- **尺子收成一把**：第 27/28 步的读取点扫描写在 `tests/providers/test_registry.py` 的私有
  `_unread_fields` 里，本步搬进 `tests/support/field_readers.py` 作为全仓唯一实现，`test_registry.py`
  改为 import——判据本身也不该有第二份抄件（F-49/F-50 那条口径用到测试侧）。
- **四条变异（孤立 worktree = `dab85b5` + 本步 4 文件，逐项复原后逐字节校验一致）**：**M1** 把
  `provider_timestamp` 以默认值装回类定义 → RC=1 点名 `['provider_timestamp']`；**M2** 把 owner
  集合换成 `zzz_unbound_name` → RC=1 报"扫描一条都没命中，判据自身失效"；**M3** 给 `Provenance`
  新增一个没人读的 `notes: str = ""` → RC=1 点名该字段；**M4** 给 `ResultMeta` 同样加一个没人读的
  `notes` → 读取点判据**假绿**、形状清单红。CONTROL 与还原后 RC=0。
- **M4 是本步测出来的判据边界，写在这里而不是抹掉**：`tstdx/cli/runtime_commands.py:406` 的
  `result.notes` 属于探针结果对象、不是 `ResultMeta`，AST 层面同名字段分不出所有者——第 28 步
  登记的"普查不成立"边界的第三个现场（前两个：`self.` 根、实例未绑定具名变量）。修法是补第二层
  判据（形状清单对字段顺序敏感，回长字段必须先改清单），而不是把尺子改宽。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `dab85b5` + 本步 4 文件，与本步提交树
  逐文件同内容）**：基线为干净 `dab85b5` 同轮实测 junit **3391 tests / 0 failures / 0 errors /
  5 skipped**、`--cov=tstdx` **80.77%**；本步树 junit **3396 / 0 / 0 / 5**、`FINAL_RC=0`、148.2s、
  **80.77%**（`Required test coverage of 77.0% reached`，阈值 77 未下调），对账 `3391 + 5`
  （本步 5 条新门禁）= **3396**。同树 9 道门禁全部 RC=0：`ruff check`、`ruff format --check`
  （462 文件）、mypy（RC=0）、originality（Total 190 / Suspicious 0）、reachability
  （189 模块 / 172 可达 / 17 白名单）、`contract_audit --ci`（63 契约 · 155 capability）、
  `spec_audit --strict`（coverage 100.0%）、golden 审计、docs links（82 文件）。**第一轮门禁抓到
  本步自己的缺陷**：新测试文件 import 段少一个空行，`ruff check` 当场 rc=1，补空行后重跑，上方
  每个数字都来自修正后的那一轮。共享树隔离：只提交 `tstdx/result.py`、
  `tests/providers/test_registry.py`、`tests/architecture/test_result_shape_gates.py`、
  `tests/support/field_readers.py` 与本文件、`docs/REFACTOR_PLAN_V17_CLOSURE.md` 的两处账本；
  §0.3 F-52 行就地改判为"已清偿（按 (b)，两半分别第 27/30 步）"并保留三条路径原文。
  **口径边界**：上表数字取自本步 4 个代码文件的树（148.2s）；账本两处 .md 写入后，在与提交树
  逐文件同内容的 6 文件树上重跑离线全量与 docs links，读作 **3396 tests / 0 failures /
  0 errors / 5 skipped**、`FINAL_RC=0`、143.8s、覆盖率 **80.77%**、docs links RC=0——同条数同
  百分比，只差墙钟（F-53 口径：数字要属于它所声称的那棵树）。

### Removed（v17 Phase 5 第 29 步 —— 流计划上两条只写不读的记录，F-55；**BREAKING**）

- **`StreamPlan` 少了两个字段**：`capability`、`channel`。`StreamPlanner.compile()`
  （`tstdx/stream_contract.py:65`）先对 `capability != "quotes"`、`channel != "quotation"` 逐条
  fail-closed，再把两个结论原样抄进 plan；而 AST 扫 `tstdx/` 全部 189 个模块，对
  `plan.capability`/`plan.channel` 的读取点是 **0**。`StreamPlan` 的唯一消费方是 `Client.stream`
  （`tstdx/client/api.py:388`）与 `AsyncClient.stream`（`tstdx/client/api.py:512`），两者读走的只有
  `symbols / provider / interval / diff_only / max_queue`——恰好是交给 worker 的实参。
  `runtime/executor.py`、`tstdx/result.py`、`runtime/identity.py` 里的 `plan.provider`/`plan.channel`/
  `plan.spec.capability` 全部属于 `QueryPlan`（同名不同物，逐处人工核对）。全仓对 `plan.channel`
  唯一的"读取"是一条测试断言——它验的是规则的抄本，不是规则；`plan.capability` 连抄本读取都没有。
- **为什么不接线而是删**：接线要把 `StatefulQuoteStream` 改成收 plan 而非具名参数，等于让公开的
  `subscribe(symbols, interval=…, diff_only=…, max_queue=…)` 变成一个内部类型的投影；而今天只有
  `provider=tdx` + `channel=quotation` 能通过编译，把这两个值再传一次不改变任何一次请求的成败。
  编译期两条 `ValidationError` 原样保留，删掉的只是抄件。那条抄本断言改成行为断言：
  `StreamSpec.build("sh600519", channel="quote")` 当场 `ValidationError`。
- **门禁换了更强的判据**：新增 `test_stream_plan_carries_only_the_fields_the_client_reads`——
  不再只问"每个字段有没有读者"，而是要求 `dataclasses.fields(StreamPlan)` 与 AST 扫
  `tstdx/client/api.py` 得到的 `plan.*` 读取集合**逐字相等**：新增字段没接线是红，消费者读到幻影
  字段也是红。防盲保险两处：`plan_fields` 非空、`api.py` 至少要读到一条 `plan.*`。变异验证：
  **M1** 把 `capability` 以默认值加回 `StreamPlan` → RC=1 并点名 `['capability']`；**M2** 把扫描的
  owner 名换成不可能命中的 `no_such_plan_var` → RC=1 报"api.py 里读不到任何 plan.*，判据自身失效"；
  CONTROL 与还原后 RC=0。
- **顺带暴露、本步不动（登记为 §0.3 F-56）**：为找 `StreamPlan` 的消费方而排查流式入口时发现，
  CLI `stream` 是最后一条不经 `Client` 的数据命令——它自建 `QuoteStream`，因此完全跳过
  `StreamPlanner` 的 tdx-only fail-closed。删抄件不掩盖它：判据本来就在编译期执行，只是此前
  plan 上那份副本让它看起来像"计划面知道 channel"。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `82f9a99` + 本步 4 文件，与本步提交树
  逐文件同内容）**：离线全量 junit **3391 tests / 0 failures / 0 errors / 7 skipped**、
  `SUITE_RC=0`、142.7s；`--cov=tstdx` **80.69%**（`Required test coverage of 77.0% reached`，
  阈值 77 未下调）。同树 12 道门禁全部 RC=0：`ruff check`、`ruff format --check`（430 文件）、
  `mypy`（0 error）、originality（Total 190 / Suspicious 0）、reachability（189 模块 / 172 可达 /
  17 白名单）、`contract_audit --ci`（63 契约 · 155 capability）、`spec_audit --strict`
  （44/44、100.0%）、golden 审计、adversarial、bridges、benchmark smoke、docs links（82 文件）。
  共享树隔离：只提交 `tstdx/stream_contract.py`、`tests/runtime/test_v13_architecture_alignment.py`、
  本文件与 `docs/REFACTOR_PLAN_V17_CLOSURE.md` 的 F-55/F-56 两行；本步测量期间并行会话把第 26 步
  落成了 `82f9a99`（25 文件），提交基因此从 `a944964` 换到它，两者都不含我方未提交的改动。
  **口径边界如实登记**：`142.7s` 与 `80.69%` 取自上面那一轮全量日志（上一轮同树读作 150.2s / 80.70%，
  差在测量本身而非树），把它们写进本行是提交树与该轮日志之间唯一的差异；写入后在同一提交树另跑
  `check_docs_links.py` 与 `pytest tests/architecture -q`（覆盖活文档↔代码判据），两道均 RC=0。

### Removed（v17 Phase 5 第 28 步 —— 注册表里第二套没人执行的市场词汇，F-54；**BREAKING**）

- **`ChannelSpec` 少了两个公开字段**：`markets`、`notes`。23 个 channel 逐个写着
  `markets=("cn_a", "hk", "us")` 这类字符串，而 `tstdx/`+`scripts/`+`tests/` 三面对它的读取点
  是 **0**；这套词汇（`cn_a`/`cn_bse`/`hk`/`us`/`future`/`commodity`/`option`/`bond`/`fx`）在代码里
  没有任何一处与 `tstdx.domain.symbol.Market` 对上。市场正确性实际由 `Symbol.tdx_market`
  （`tstdx/domain/symbol.py:122`：HK/US 抛 `SymbolError`、`provider_switch_allowed=False`）承担——
  注册表有 `require()` 管 capability、`require_period()` 管 period，唯独市场没有执行位。
  `notes` 同理：零生产读取，唯一"读到"它的是第 25 步自己钉位置形状的那条测试；它写的那句
  "本地 vipdoc 不替代在线 TDX"在 `docs/providers/tdx.md:92` 本来就有。`ChannelSpec` 现为
  `id / capabilities / live / local / periods`，`live`/`local`/`periods` 逐个实测有读取者
  （`query.py` 的 currentness 判定、`catalog/provider_bindings.py` 的绑定核对、`require_period`）。
- **为什么不接线而是删**：接线要先造一个 `Market → 字符串 token` 的映射，再与 `tdx_market`
  形成同一件事的两套实现——第 24 步刚按 F-49 清掉过一个"第二实现"，而且它会把今天能成的请求
  变成报错（对外契约变更，无实测收益）。市场与定位的说明留在 `docs/providers/*.md`：§8 模板
  第 2 条与 §9"Registry 目标"同步改写，§9 另加一条前言说明那一节是 v12 目标结构而非当前代码形状
  （清单里 `auth/rate policy`、`production status` 这类条目从来没有代码落点）。
- **门禁推广**：`test_every_provider_spec_field_has_a_reader` 与新的
  `test_every_channel_spec_field_has_a_reader` 共用同一个 `_unread_fields()` 尺子（分母取自
  `dataclasses.fields`，读取点由 AST 扫 `tstdx/` 全部模块、owner 名人工核对），三把防盲保险不变；
  ChannelSpec 额外钉死形状 `fields == {id, capabilities, live, local, periods}`，位置式构造测试
  改写为新形状并断言 `markets`/`notes`/`batch_limits` 作关键字传入当场 `TypeError`。
  变异验证：**M1** 把 `markets` 以默认值加回 → 两条测试红，判据点名 `markets`；**M2** 把 owner
  集合换成不可能命中的名字 → 红并报"ChannelSpec 字段读取扫描一条都没命中"。
- **测量方法的一条边界（登记，不改）**：曾想把这把尺子推广成 168 个 dataclass 的无人值守普查，
  两条独立证据判它不成立——它把第 27 步刚测过有 14/6/3 个读取点的 `ProviderSpec.id/channels/default`
  判成零读，也把 `CoreConfig.*` 判成零读而 `tstdx/runtime/kernel.py:66` 真在读 `cfg.core.timeout`。
  成因：实例未绑定到具名变量（注册表里是元组成员）与 `self.` 根的属性链。所以本账本里每个
  "零读取"结论都是逐类人工核对 owner 之后写下的，普查脚本不进门禁。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `463b9ae` + 本步 5 文件，与本步提交树逐文件同内容）**：
  离线全量 junit **3367 tests / 0 failures / 0 errors / 7 skipped**、`SUITE_RC=0`、140.1s；
  `--cov=tstdx` **80.65%**（`Required test coverage of 77.0% reached`，阈值 77 未下调）。
  同树 12 道门禁全部 RC=0：`ruff check`、`ruff format --check`、`mypy`（0 error）、originality
  （Total 189 / Suspicious 0）、reachability（188 模块 / 171 可达 / 17 白名单）、
  `contract_audit --ci`（63 契约 · 155 capability）、`spec_audit --strict`（44/44、100.0%）、
  golden 审计、adversarial、bridges、benchmark smoke、docs links（82 文件）。
  共享树隔离：只提交 `tstdx/providers/__init__.py`、`tests/providers/test_registry.py`、
  `docs/providers/README.md` 与本文件；并行会话的第 26 步在途改动（`tstdx/diagnostics.py`、
  `result.py`、`executor.py` 等 22 文件）全部留在工作区未动。

### Removed（v17 Phase 5 第 27 步 —— 注册表里两个没人读取的字段，F-52 注册表半边；**BREAKING**）

- **`ProviderSpec` 少了两个公开字段**：`display_name`、`role`。11 个 Provider 各自写着
  `display_name="Tencent Finance"` 与 `role="auxiliary_live"`，而全包对它们的读取点是 **0**——
  与第 25 步的 `QueryPlan.deadline_ms` 同族：一张可被外部 introspect 的注册表里躺着没人兑现的
  声称。按 clean break 口径物理删除，不留 alias/兼容 property（与 F-40/F-50 同口径）；`ProviderSpec`
  现在只剩 `id / channels / default` 三个执行面真会读到的字段（`tstdx/providers/__init__.py` −24 行）。
- **人类可读的名称与定位改由文档承载**：`docs/providers/README.md` §8 的模板第 1 条从
  "Provider ID / display name / role"改为"Provider ID，名称与定位写在正文"。`docs/adr/ADR-013`
  的 `ProviderSpec` 示例**不动**——它连同 `markets=`/`auth_policy=`/`production=` 这些从未存在的
  字段一起被 `tests/architecture/test_doc_code_consistency.py` 判为历史语境快照（`docs/adr/` 在
  `EXCLUDED_PARTS` 里），不参与活文档门禁；把它当现状改是误读，把它当现状删是篡改。
- **判据是结构式的，不是逐条断言**：新增 `test_every_provider_spec_field_has_a_reader`——字段
  分母取自 `dataclasses.fields(ProviderSpec)` 本身，读取点由 AST 扫 `tstdx/` 全部模块的属性访问
  （owner 名过滤）得出，任何字段失去读者即当场变红，新增字段无需改测试。判据自带三把防盲保险：
  `assert fields`（dataclass 空了即自失效）、`assert scanned > 30`（遍历没覆盖到模块即红）、
  `assert reads`（一条读取都没命中说明过滤器写错了）。实测删除后读取点数：`id` 14、`channels` 6、
  `default` 3，`display_name`/`role` 各 0。
- **三条变异证明门禁不是摆设**：M1' 把两个字段以默认值加回类定义 → RC=1 并点名
  `['display_name', 'role']`；M2 把 owner 集合换成不可能命中的名字 → RC=1 报"字段读取扫描一条
  都没命中"；M3 把扫描根目录指向空处 → RC=1 报"只扫到 0 个模块"。CONTROL（只删不改）RC=0，
  harness 零残留。
- **`Provenance.provider_timestamp` 本轮不动**（F-52 的 provenance 半边）：它同样零读取、同样
  不进 wire（`integration/serialization.py` 按显式键构造），但 `tstdx/result.py` 此刻正被并行
  会话按第 26 步改写，同一文件两把刀只会制造假冲突，故把删除推到那一步合树之后；裁决位仍挂在
  方案 §0.3 的 F-52 行。
- **测量自伤一条，如实登记**：本步第一次孤立复测（12:58）里 `ruff format --check` 是 **RC=1**，
  原因是我追加的测试块以 LF 落盘而该文件在工作区是 CRLF（`core.autocrlf=true`、无
  `.gitattributes`），`ruff` 按 `line-ending=auto` 认首个行尾为 CRLF。修复是 `ruff format` 该文件
  一类的行尾归一，不涉及任何语义变更；下方复测数字来自归一之后的重跑。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = `15bfb61` + 本步 3 文件）**：
  离线全量 junit **3366 tests / 0 failures / 0 errors / 7 skipped**、`SUITE_RC=0`、130.1s；
  `--cov=tstdx` **80.64%**（`Required test coverage of 77.0% reached`，阈值 77 未下调）。
  同树 12 道门禁全部 RC=0：`ruff check`、`ruff format --check`、`mypy`（0 error）、originality
  `Total: 189 / Suspicious: 0`、reachability `188 模块 / 171 可达 / 17 白名单`、`contract_audit --ci`
  （63 契约 · 155 capability）、`spec_audit --strict`（44/44、100.0%）、golden 审计、
  adversarial 与 bridges、benchmark smoke、docs links（82 文件）。
  工作区隔离：只提交 `tstdx/providers/__init__.py`、`tests/providers/test_registry.py`、
  `docs/providers/README.md` 与本文件，按 index 级 blob 暂存切出本步内容；并行会话的第 26 步
  在途改动与其 `docs/REFACTOR_PLAN_V17_CLOSURE.md` 改写全部留在工作区未动。

### Added（v17 Phase 5 第 26 步 —— 结果侧数据瑕疵通道：一条发射口、`meta.warnings`、内核 `strict`，F-45 尾条 + F-51）

- **`tstdx/diagnostics.py` 是数据瑕疵的唯一发射口**：`record_warning(code, message, *,
  stacklevel=2, stderr=True)` 把同一条事实送到两处——本次查询的收集器（`warning_sink()`，
  `contextvars` 实现，线程/任务隔离）与既有的进程 `UserWarning`。12 个 `WarningCode` 声明为
  枚举，"新增类别却没发射点"和"发射点没声明类别"都由架构门禁双向把守；全仓裸 `warnings.warn`
  只剩发射口自身、`deprecation.py` 与执行器把告警换成异常的那处，共 3 个白名单豁免。
- **`QueryResult.meta.warnings` 是新载体**：`ResultMeta` 增 `warnings: tuple[ResultWarning, ...] = ()`，
  执行器在返回前装入本次收集到的瑕疵；HTTP/WS/MCP 三面共用的序列化把它逐条写进
  `meta.warnings`（**additive 键**）。空元组从此是"这次结果干净"的证据，而不是沉默——第 21 步
  那条"首页即空响应"的告警，此前只能从服务端 stderr 读到。
- **`Client.bars(..., strict=True)` 是公开入口**：`strict` 由内核在发起任何 I/O 之前集中读取
  （`options["strict"]`，非 bool 即 `ValidationError`，键已入 `EXECUTED_OPTIONS` 白名单），本次
  结果携带任何瑕疵即抛 `TruncatedDataError`。判据与 Provider 无关，不再有第二个"每源各认一次"
  的开关。默认 `False`，`strict=False` 的路径与第 21 步以来逐字节相同。
- **解码器的静默修正第一次到达调用方（F-51 的 ②半边）**：`ParseResult.warnings` 此前在执行
  路径上零读取点（9 个发射点：count 失真钳制、记录不完整即停、正文截断、L2→L3 回落），现在
  `bars` 分页把每一页该判断以 `WarningCode.DECODE_CAVEAT` 记进通道。
- **默认行为不变清单**：stderr 文案与 `stacklevel` 指向、返回的数据、`strict` 默认值、无收集器
  时"不抛不吞"的既成语义全部保持；wire 只多一个键。`stderr=False` 供已自带去重的发射点使用
  （复权缺前收盘价、日历未覆盖年份）——去重是对终端的礼貌，不该决定某一次结果要不要声明缺陷。
- **4 条新门禁 + 21 项接线断言 + 10 条变异全部按形状判据**：裸 `warn` 白名单正反两向、`WarningCode` 枚举 ⇔
  发射点、`_t_bars` 同时读 `.warnings` 与发射、`docs/api/interfaces.md` 的 Client 方法表逐行
  与真实签名双向对账（实测只有 `bars` 一格漂移）。10 条变异各自指名、对照全绿、harness 零残留。
- **归属更正（F-53）**：`test_kernel_config_wiring.py` 里那条对单调时钟剩余量求浮点相等的断言
  （自 `524c687` 起从未通过）改为 `4.9 < hop <= 5.0` 并写明判据——代码由并行会话带进 `9fbece0`，
  账本由本步补齐。HEAD `9fbece0` 上那条 CLI 示例红**不由本步清偿**：`9127d78` 已把不存在的子
  命令从行内示例位改写、`15bfb61` 撤回了第 25 步挂在拼装树上的复测数字；本步起初把它记成自己
  清零，现按两棵干净 worktree 的复跑改回归属（`9fbece0` 该门禁 1 项红，`15bfb61` 同一测试
  RC=0）。`docs/api/interfaces.md` 一处自 `5c50487` 起语法不通的 python 示例由本步修正。
- **已知边界（登记，不改）**：`Client.typed()` 的返回 `TypedQueryResult(data, capability)` 既无
  provenance 也无 warnings（接进去要改 63 张契约形状，等裁决）；`strict` 未开放为 HTTP/WS/MCP
  请求参数（属 F-47）；通道不覆盖异常路径（属 F-44）。
- **根级 `.py` 白名单 10 → 11**（新增 `diagnostics.py`）：见 `docs/ARCHITECTURE.md` §4、README
  结构树与验收清单的同步改写。
- **复测（本机 Windows+py3.13，同一轮日志；孤立 worktree = HEAD `a944964` + 本步 25 个文件）**：
  基线跟着 HEAD 挪过两次（`15bfb61`→`463b9ae`→`a944964`，三个提交都出自并行会话），只记与本步
  提交树同基的那组：同一提交的纯净树以相同参数跑基线 `3367 tests / 0 failures / 0 errors /
  5 skipped`、80.72%；加本步文件后 junit **3390 / 0 / 0 / 5 skipped**、`PYTEST_RC=0`，
  `--cov=tstdx` **80.76%**（`Required test coverage of 77.0% reached`，阈值 77 未下调，未增删任何 skip 标记，
  5 条 skip 与基线逐名相同）。条数：`3367 + 23`（18 接线 + 3 通道门禁 + 1 方法表对账 + 1 袋
  探针）。同树 9 道 CI 门禁全部 RC=0（originality `Total: 190 / Suspicious: 0`、`contract_audit
  --ci` 63 契约 · 155 capability、docs links 82 文件、`ruff format --check` 460 files、`mypy` 0）。

### Removed（v17 Phase 5 第 25 步 —— 计划面上的无人读取副本与"执行次数预算"，F-50；**BREAKING**）

- **`QueryPlan` 少了四个字段**：`deadline_ms`、`batch_limit`、`live_channel`、`local_channel`。
  `compile()` 逐行写入它们，而 plan 之外的读取点是 **0**——`deadline_ms` 在全包内 4 次命中
  全部落在 `query.py` 自己体内，其余三个字段连一次属性读取都没有。第 24 步把 `deadline_ms`
  接进 `plan.budget` 之后，这份副本只是第二个真相源。`plan.budget` 从此是 deadline 的唯一载体。
- **`ExecutionBudget` 只剩墙钟**：`max_attempts`、`attempts`、`begin_attempt()` 物理删除——
  它们在 `tstdx/` 内零调用点（`begin_attempt` 全包读取 0 次）。第 24 步点亮的是
  `remaining_s` / `ensure_remaining` 那半边，"执行次数预算"仍是一张带着 `threading.Lock` 的
  空支票。对象 docstring 同步改写为"一次 `execute()` 的墙钟上界"，并写明重试归传输池
  （`[core] max_retries`）与 fallback 策略所辖（关闭 F-48 尾条 ⑥）；外部读者为零的
  `remaining_ns()` 一并删除。
- **注册表不再声称批量上限**：`ChannelSpec.batch_limits`、`batch_limit_for()` 与 tdx quotation
  channel 上的 `{"quotes": 60}` 整链删除。该链唯一读者就是写入它的规划器（`batch_limit_for`
  全包命中 **1** 次），而它声称的 60 与真正生效的分片上限 `_QUOTES_SNAPSHOT_BATCH = 80`
  （`tstdx/client/_mixin.py:75`）**直接矛盾**。取删除而非"接成执行期校验"：后者会静默把
  quotes 分片从 80 改成 60，本步不改任何默认行为。
- **迁移口径**：`ChannelSpec` 构造不再接受 `batch_limits=`（其余字段位置不变，`notes` 仍是
  第 6 个位置参数，有断言钉住）；`QueryPlan` 的构造点全仓仅规划器一处；deadline 请读
  `plan.budget.deadline_ns`，quotes 批量上限请读 `_QUOTES_SNAPSHOT_BATCH`。
  `docs/api/interfaces.md` 的字段清单已同步。
- **两条计划面结构门禁 + 一条注册表反向门禁**：`test_every_query_plan_field_is_read_by_the_execution_face`
  （第 20 步字段门禁的对偶，分母取 `dataclasses.fields(QueryPlan)`、读取扫描跳过 `query.py`）、
  `test_execution_budget_has_no_unexercised_member`（每个公开实例方法必须经由
  `budget`/`plan.budget` 被执行面读到；构造子 `from_deadline_ms` 因"它就是折叠那一步"而例外，
  且例外前提自身要核验）、`test_the_registry_declares_no_batch_quota`（字段不存在 + `repr`
  不含 `batch_limit` + `build()` 签名无该形参 + 真正生效的 `_QUOTES_SNAPSHOT_BATCH == 80`）。
  原先维持死面的两条 `batch_limits` 用例改写成注册表**真正执行**的那条规则（`periods` 只属于
  bars channel）；三处"对着抄本断言"的测试改为对着真相源断言（live/local 读
  `PROVIDERS…channel(...)`，deadline 读 `plan.budget`）。
- **变异验证 4 条全部 RED 且各自指名**：把 `deadline_ms` 副本装回 plan → `['deadline_ms']`；
  把 `begin_attempt()` 装回预算且只在 `query.py` 内部调用它（F-50 当时的真实形状）→
  `['begin_attempt']`；注册表重新声称 `batch_limits` 并把"字段不存在"那条断言削弱成永真 →
  `repr` 扫描仍指名；把读取扫描改瞎 → 自曝"计划面读取扫描一条都没命中，说明它自身失效了"。
  harness 结束后被改文件全部还原，测量过程零残留。
- **顺带实测、登记待裁决（F-52）**：`ProviderSpec.display_name`/`role` 与
  `Provenance.provider_timestamp` 同样零读取点（`tstdx/`+`scripts/`+`tests/` 三面对前两者的
  读取均为 0；后者从未被传入非默认值，也不在序列化面的显式键里）。因涉及两个公开 dataclass
  的形状，按 F-44/F-47 口径只钉事实、不代为拍板。
- **复测以提交树为准，不以拼装树为准**：本步代码提交为 `9fbece0` 后从该提交单开 worktree
  复跑，第一条读数就是**一条红**——`test_every_documented_cli_example_parses` 报本文件
  `exit 2`：F-52 行把"接进自省面"的 (a) 路径写成了一个 `providers` 子命令的行内示例，而 CLI
  parser 里根本没有它（只有 `capabilities`）。一条登记"某字段无人读取"的账本行，自己臆造了
  一个读取面，并被专为防这类事而上线的门禁抓住——`9127d78` 单行改写清零。清零后同一提交树
  实测：**采集 3375 项**（基线 `288e62f` 的 3373 + 本步 2 条计划面门禁，注册表侧 3 换 3），
  `-m "not network"` 选中 **3365** 项、**0 failed / 0 errors / 7 skipped**、139s，
  `--cov=tstdx` **80.64%**（日志明写 `Required test coverage of 77.0% reached`，阈值 77 未
  下调，未新增或删除任何 skip 标记）。**口径更正（F-53 同族）**：本条此前写的是"HEAD + 本步
  7 个文件"拼装树上的 `3375 tests / 0 failures / 8 skipped`——那棵树的 junit `tests=` 计的是
  **采集总数**（含 10 条被 `-m` deselect 的网络用例），且该树与本步提交内容并不逐字节相同
  （每跳超时那条断言在拼装树里仍是旧写法），两个原因叠加使绝对数字不可复用，故以提交树读数为准。
- **同树门禁逐个 RC=0**：`ruff check`、`ruff format --check`（427 files）、`mypy tstdx/`
  （CI 参数）、originality `--strict`（`Total: 189 Suspicious: 0`）、reachability `--strict`
  （188 模块 / 171 可达 / 17 白名单豁免，`无未登记孤儿 ✓`）、`contract_audit --ci`
  （**63 契约 · 155 capability**，与第 17/19/20/22/24 步逐项相同）、`spec_audit --json --strict`
  （44 specs · `coverage_pct: 100.0`）、`golden_audit --gate --require-markets
  --require-kline-categories 0,4,9 --require-payloads`、docs links（82 文件）、
  `tests/adversarial` + `tests/test_bridges.py`（28 项）。
- **一次测量自伤，如实登记**：第一次跑该树离线全量时给子进程传了一个臆造的环境变量
  `TSTDX_OFFLINE=1`，Phase 6 的 fail-closed 配置装载器当场拒绝未知 `TSTDX_*`，于是 **86 条**
  测试红成同一个 `ConfigError`。判据正确、测量者错误；记下是因为"整片同因红"很容易被读成真实
  回归，而判别只需读一条错误消息。
- 测量期间主树另有并行会话在途（`client/api.py`、`result.py`、`integration/serialization.py`、
  `executor.py` 的 warnings 通道与 `strict` 面），同轮主树实测 1 条红
  （`test_every_executed_option_key_still_compiles[strict]`：`白名单新增了 strict，但这里没有
  对应的探针取值`），属对方在途的白名单扩展，与本步零交集，未代为提交、未改写。

### Changed（v17 Phase 5 第 24 步 —— `deadline_ms` 第一次真的约束执行面，F-48）

- **查询总预算从"折进对象就完事"变成每一跳的超时上界**：`DirectProviderExecutor` 新增
  唯一取数口 `_hop_timeout(plan)` = `min(配置 timeout, plan.budget.remaining_s())`，并在
  取数前跑 `ensure_remaining("provider_request")`。它分发到 TDX 套接字（`_tdx_client(timeout)`
  改为收形参，7 个 `_tdx_*` 直调执行器与 composed 原始读全部经它）、`WebQuoteSession`、
  `F10Client`、ex/goods/mac 族客户端、`direct_adapter`、`_web_adapter_call`、`_composed_call`
  （签名从此强制要求 `timeout`）与 5 个 Web 直调执行器。此前 `ExecutionBudget` 的
  `ensure_remaining()` / `remaining_s()` 在 `tstdx/` 内**零调用点**：调用方给 250ms 预算，
  传输层照样拿 5 秒（配置调到 30 秒时更糟）。与已删除的 `max_age` 同形，只是这次连"新鲜度"
  的托词都没有。
- **默认路径可证明未变**：默认 `deadline_ms=5000` 与默认 `[core] timeout=5.0` 同值，
  `min()` 在默认配置下是恒等操作，并有断言钉住（`timeout == 5.0`）。行为变化只发生在
  **显式设置过 `deadline_ms` 的调用方**身上——此前该参数没有任何效果。
- **预算耗尽改为 fail-fast**：剩余预算为 0 时在构造客户端之前抛
  `ReadTimeout("查询总 deadline 已耗尽")`，离线回归连"零个客户端实例"都断言。
- **两条新判据**：① 字段侧把第 20 步的"必须被消费"收紧为"读的人必须在执行面"
  （`tstdx/query.py` 自身的读取不算），`spec → plan.budget` 的折叠链由 `QueryPlan(...)`
  构造处的 AST 推导而非手抄名单；② 结构性守卫——`executor.py` 里 `self.timeout` 只允许
  被 `_hop_timeout` 读一次，任何新直调执行器绕过预算当场红。首轮变异正是从"逐函数写断言"
  的缝里活下来的（`_web_quotes` 退回裸配置值时无人变红），补结构判据后同一变异双红。
- **一处测试替身纠正**：`tests/sink/test_local_day.py` 的 `_tdx_client` 替身签名比生产窄
  （零参 lambda），随形参新增改为收 `_timeout`；改的是替身，未放宽任何断言。

### Fixed（v17 Phase 5 第 24 步 —— 分页只剩一份实现，F-49）

- **`security_list_all` 不再自带第二份游标分页**：composed 面那段 `while True` + `start`
  游标循环**没有页数上限、空首页静默返回 `[]`**——正是第 21 步（F-45）刚给
  `TdxClient.export_security_list` 修掉的两个缺陷，只是这份影子实现永远走不到那条判据。
  该能力现绑到真相源 `("tdx_client", "export_security_list")`，`_validate_composed` 的
  `required`/`allowed` 两张表同步删除该条目。实测口径如实：`0x044D` 目前是
  `STATUS_OFFLINE`，`_guard_offline` 会先 fail-fast，故缺陷是形状而非当场的数据事故。
- **反向门禁按形状判定**：绑定 `(backend, method)` 必须仍是那一对，且 `_composed_call`
  函数体内不允许出现任何 `ast.While`——给另一条能力再写一份游标循环同样当场红。
- **变异验证 8 条全部 RED 且各自指名**（预算退化、预检查删除、Web 跳与 TDX 跳各绕过一次、
  绑定改回单页调用、游标循环插回、豁免表漏项、身份读取函数改名），harness 结束后被改文件
  `md5sum -c` 全部一致：测量过程零残留。复测数字见方案文档 §1 第 24 步。

### Added（v17 Phase 5 第 23 步 —— 三张服务面的"已声明入参"门禁，F-47 登记）

- **HTTP 路由形参必须进入执行路径**：`tests/runtime/test_migrated_surfaces_v13.py::
  test_http_route_parameters_all_reach_the_execution_path` 以 AST 取 10 条 `@app.get` /
  `@app.post` 路由，逐个形参核对它是否出现在同一函数体的读取名字里——一个收下却不用的路由
  形参就是门面版的 `max_age`（F-43）。同族判据此前已覆盖 CLI 选项（F-27/F-28）、`QuerySpec`
  一等字段（F-43）与 `options` 袋键（F-46），服务面签名是最后一处没有对应物的入口。
- **MCP `inputSchema` 与 handler 双向相等**：9 个 `ToolSpec` 声明的属性集合必须与其 handler
  从 `args` 袋实际读出的字面量键完全一致——多一个键是幻影开关，少一个键是未声明输入。
- **WS `METHODS` 与实际分派分支双向相等**：JSON-RPC 名单（10 项）里声明却不派发的方法、
  以及派发却未声明的方法都会当场让门禁红。判据认识 `method == "x"` 与
  `method in {"snapshot", "minute", "trades"}` 两种形状；三条门禁各带"扫到 0 个即门禁自身
  失效"的自我校验，且都用变异验证过会咬人（见下）。

### Changed（v17 Phase 5 第 23 步 —— `options` 袋改为 fail-closed 白名单；**BREAKING**）

- **`QuerySpec.options` 里不被执行面读取的键现在当场被拒**：新增
  `tstdx.query.EXECUTED_OPTIONS`（`args` / `kwargs` / `market`，即 `runtime/executor.py`
  真实读取的三处），`normalized()` 对名单之外的键抛 `ValidationError` 并在 context 里回显
  `unknown_options` 与白名单本身。这补上了 F-46 当时言过其实的一句：`REJECTED_OPTIONS` 只拦
  名单内的两个策略键、且只在取值为真时命中，因此
  `Client.execute(QuerySpec.build(..., options={"max_age": 0}))` 曾把已被删除的旋钮原样带进
  执行面静默忽略，`options={"allow_stale": False}` 同样能入袋。两者现在都红。此前只有
  `Client.bars(..., max_age=0)` 这类形参位置会 `TypeError`，袋侧没有对应防线。
- **白名单是一份被门禁钉住的抄本**：袋级门禁 `test_option_bag_keys_are_executed_or_rejected`
  加了第三条断言，把名单与 AST 扫出的真实读取点求差，双向都要为空——名单多一个键报
  "名单多 `['max_age']`"，少一个键报"名单漏 `['market']`"（F-42：能被钉住的抄本才是安全的）。
- **一处测试口径纠正**：`test_options_order_does_not_change_query_identity` 原先用
  `{"a": 1, "b": 2}` 这类任意键演示指纹顺序无关，现改为袋内真实键——判据不该依赖违规输入
  才成立。
- **变异验证 9 条全部按预期红/绿**：袋侧 4 条（删掉白名单检查 → `DID NOT RAISE`；名单加幽灵
  键 → "名单多"；名单删 `market` → "名单漏"；让策略键专属理由永不触发 → 报"实际消息是通用
  白名单消息"），wire 侧 4 条（给 `quotes()` 加不使用形参 → `['unused_probe']`；给
  `query_capability` 的 schema 加 `max_age` → "声明却无人读取"；在 `METHODS` 声明不存在的
  `history` → "声明却未分派"；关掉集合字面量识别 → 重新误报 `trades`），未变异对照 `RC=0`。
- **未清偿（需用户裁决，见 `docs/REFACTOR_PLAN_V17_CLOSURE.md` §0.3 F-47）**：HTTP 查询串与
  body、WS `params`、MCP `arguments` 对**未声明**的请求字段仍一律静默忽略——同一轮探针实测
  `GET /v13/bars/600519?max_age=0` 返回 200 且参数蒸发、WS 未知 params 不报错、9 张
  `inputSchema` 无一声明 `additionalProperties: false`。把它改成 fail-closed 会让今天返回
  200 的请求开始返回 4xx，属对外契约的破坏性变更，与 F-44 同格，故本轮不代为拍板。

### Removed（v17 Phase 5 第 22 步 —— `allow_partial` 幻影开关与 options 袋门禁，F-46；**BREAKING**）

- **`allow_partial` 不再是 `QuerySpec.build()` 的形参，也不再是 `QuerySpec` 的属性**：它此前
  被折进 `options` 袋、只在"capability 必须是 quotes"这条校验里出现，而执行面
  （`runtime/`、`client/`、`integration/`）没有一处读它——`BatchResult.partial` 由逐 symbol
  的三态结果推导，是**结果事实**而非可放行的策略。因此在 quotes 上设置它的实际效果是
  "什么也不改变"，在非 quotes 上设置它只是提前报错。`Client.quotes_batch()` 本来也不接受该
  参数，它只能经通用构造面到达。这与第 20 步退场的 `max_age` 同族：收下但无人读。
- **策略键统一进 `tstdx.query.REJECTED_OPTIONS`，命中即当场 `ValidationError` 并说明理由**
  （`allow_stale`：数据始终来自绑定的 Provider，过期容忍没有可作用的对象；`allow_partial`：
  partial 是 `BatchResult` 的结果事实）。袋里的键从此只有两种下场——被执行面消费，或者
  明确拒绝，不存在第三种。
- **新门禁 `test_option_bag_keys_are_executed_or_rejected`**：以 AST 取两处写入点的字面量键
  （`build()` 内部的 ergonomic 折叠 + 生产代码里 `QuerySpec.build(..., options={...})` 的
  显式注入），与执行面实际读取的键（`options.get("k")` / `options["k"]`）求差；差的集合
  必须为空，且 `build()` 的每个 `allow_*` 形参都必须落进袋里。两条自我校验（消费扫描零命中、
  调用点扫描零命中）各自指名"门禁自身失效"。文档不再手抄被拒键的清单，改为按
  `REJECTED_OPTIONS` 引用（F-39：抄本必过期）。
- **变异验证 6 条**：注入未折叠的 `allow_noop` 形参 → `['allow_noop']（收下即丢）`；
  在 `build()` 里折叠无人消费的键 → `幻影开关：['allow_noop']`；在生产调用点注入
  `phantom_key` → `幻影开关：['phantom_key']`；把消费扫描与调用点扫描分别改瞎 → 两条
  自曝失明断言各自命中；未变异对照 GREEN。首轮还抓到门禁自身的一个运算符优先级缺陷
  （`folded | injected - rejected` 让 `build()` 折叠的键绕开拒绝集），修好后 `allow_stale`
  不再被误报。
- **复测（同一轮日志；HEAD + 本步 5 个文件的孤立 worktree）**：整仓离线 `-m "not network"`
  junit **3347 tests / 0 failures / 0 errors / 5 skipped**、`ISO_FULL_RC=0`、`--cov=tstdx`
  **80.59%**（阈值 77 未下调；3344 − 1 条删除 + 4 条新增 = 3347）；originality `--strict`
  （189 · Suspicious 0）、`spec_audit --json --strict`、`golden_audit --gate --require-markets`、
  reachability `--strict`、`contract_audit --ci`（63 契约 · 155 capability）、docs links
  （82 文件）、`ruff check` 与 `format --check`（457 files）、`mypy tstdx/`（CI 参数）
  **全部 RC=0**。测量期间主树另有并行会话在途的 `runtime/executor.py`，同轮实测 3 条红
  （`DirectProviderExecutor._tdx_client() missing 1 required positional argument: 'timeout'`），
  与本步无关，故权威数字取孤立树。

### Fixed（v17 Phase 5 第 21 步 —— 首页空响应不得静默读成成功，F-45）

- **`bars()` 把"服务端声明 0 条"读成"历史已经耗尽"**：`tstdx/client/_mixin.py::_t_bars`
  的分页循环里，空页出口是一行 `break  # 空页：历史耗尽（正常终止）`——首页空与次页空
  共用同一个出口，而紧随其后的截断判据是 `len(bars) < count and drifted`，只在**锚点漂移**
  时才成立。于是主站对 `0x052D` 只回 2 字节 `count=0` 空桩时（F-37 实测到的现场），
  调用方拿到的是"请求成功、恰好 0 根"：不告警、不抛错、`strict=True` 也不抛。这正是
  F-37 在全绿的离线门禁里结构性隐形的机制面。判据现改为：**耗尽只会表现为短页或次页空**，
  首页空响应是另一件事（该标的无此周期历史，或主站对这个命令只回空桩）。
- **默认只加可观测性，不改数据**：首页空 → `UserWarning`（返回仍是空序列，与旧行为逐字节
  一致），`strict=True` → `TruncatedDataError`，与既有的漂移截断同级。
  `_t_export_security_list` 的 `0x044D` 同形缺陷（首页 0 条 ⇒ 读成"这个市场没有证券"，
  而 0/1 两个市场必有数千标的）一并接上同一判据。
- **一项测试改判**：`test_client_f1.py::test_empty_first_page_no_warning` 原用
  `warnings.simplefilter("error")` 断言"空首页不许留痕"——它把缺陷本身钉成了契约。现改为
  `test_empty_first_page_warns`，理由写进 docstring。新增离线回归 4 项（同步告警、
  同步 strict 抛错、异步镜像、次页空仍须静默——后者用 `simplefilter("error")` 反向钉住，
  保证这次改动没有把"正常耗尽"也变成噪声）。
- **口径文档同步**：`docs/errors.md` 的 `TruncatedDataError` 条目原先只说"网络响应数据被
  截断"，读者会以为只有漂移一个触发点；现列出 `_mixin.py` 的全部抛出点。刻意不写条数
  （F-42 的教训：数量抄本就是下一个过期点）。
- **变异验证 2 条各自 RED**：`empty_first_page = not seen` → `= False`、
  `if not out:` → `if False:`，对应测试各自行列失败——把判据退回静默路径必红。
- **复测（同一轮日志）**：整仓离线 `-m "not network"` junit **3344 tests / 0 failures /
  0 errors / 7 skipped**、`COV_FULL_RC=0`、`--cov=tstdx` **80.53%**（日志明写
  `Required test coverage of 77.0% reached`，阈值 77 未下调）；`mypy tstdx/`（CI 参数）、
  `audit_reachability --strict`（无未登记孤儿 ✓）、`check_originality --strict tstdx/`
  （189 · Suspicious 0）、`spec_audit --json --strict`、`golden_audit --gate --require-markets`、
  `contract_audit --ci`（63 契约 · 155 capability）、docs links（82 文件）、`ruff check` 与
  `format --check`（430 files）**全部 RC=0**。
- **如实登记两个未闭合点**：① `QueryResult` 没有 warnings 通道，所以这条告警只在调用方
  进程 stderr 上——HTTP/WS/MCP 三面的 wire 里看不见"本次结果为首页空桩"；② 唯一业务
  入口 `Client.bars()` 没有 `strict` 形参（`strict` 只存在于 `TdxClient.bars`），
  经内核绑定的路径拿不到"必须完整"的语义。两处都要改 `tstdx/client/api.py` 与
  `tstdx/result.py`，而这两个文件此刻正被并行会话编辑，本步未代为决定。

### Removed（v17 Phase 5 第 20 步 —— `max_age` 幻影旋钮与缓存口径散文，F-43；**BREAKING**）

- **`max_age` 从全部五张入口物理删除，不留别名**：`Client.bars()/quotes()/…` 的
  `max_age=` 形参、CLI 的 `--max-age`（3 个子命令）、HTTP/WS 的 `max_age` 请求字段、
  MCP inputSchema 的 `max_age` 属性与 `QuerySpec.max_age` 字段一并退场（9 个文件 50 行）。
  它此前**只进不出**：值被一路送进 `QueryPlan`，执行面没有任何一处读它，因此调用方设置
  它只会得到"已经生效"的错觉。直连路径上的合法口径只有两个——新鲜度**口径**
  `currentness`（`CurrentnessMode`）与执行**预算** `deadline_ms`，两者都参与 fingerprint。
  `allow_stale` 此前恒被拒绝，现在的拒绝文案说明**为何**无对象可作用。
- **`docs/providers/README.md` 的 `### bounded cache` 整节改写为 `### no result cache`**：
  原先 6 行在规定 cache key/fingerprint/provenance/cache-hit 语义，即一个已被物理删除的层；
  现在的契约是 `provenance.cache_tier` 恒为 `null`、Provider 不得自带"命中即跳过上游"、
  结果 provenance 必须与当前 `QueryPlan` 完全一致、freshness mode 由 Provider 如实声明。
  §13 CI 清单同步替换 `cache hit -> …` 一行。`README.md` 的"`allow_stale` 显式放行"与
  `docs/api/interfaces.md` 的 6 处 `max_age` 签名同批改正。
- **8 个生产文件的缓存口径散文纠正**（`QueryFingerprint` 的 "used by cache/single-flight
  layers"、`domain/period.py` 的 "and cache lookup"、`config/schema.py` 把"缓存/降级链"
  列为可配置面、`result.py`/`__init__.py`/`domain/symbol.py`/`protocol/handshake.py`/
  `runtime/kernel.py` 各一处）：读者照注释理解系统，注释指向不存在的层就是假事实。
- **两条新门禁把"零缓存"从标识符扩展到散文与字段**：`test_production_prose_never_claims_a_data_cache`
  扫 `tstdx/` 全部 docstring/注释里的缓存词根（10 条放行形状各自登记口径来源），
  `test_every_query_spec_field_is_consumed` 以 `dataclasses.fields(QuerySpec)` 为分母禁止
  幻影开关（豁免字段由反向核验守卫看住）。两条都带"扫描器零命中即自曝失明"的自我校验。
  变异验证 6 条全部 RC=1 且各自指名；其中幻影门禁的首轮变异先跑成 RC=0，暴露
  `dict.fromkeys(keys, set())` 共享集合使守卫失明，改推导式后才有牙齿。
- **顺带登记 F-44（待用户裁决）**：49 个错误类中 7 个叶子从未被 `raise`，其中
  `SourceUnavailable(E7050)` 与 `FreshnessViolation(E4060)` 被 Provider 文档明文承诺，
  而运行期既无包装站点也无 currentness 校验器。本轮只钉事实，未改对外错误契约。

### Fixed（v17 Phase 5 第 19 步 —— README"第二种写法"的数字与协议覆盖矩阵每族分布入门禁，F-42）

- **协议覆盖矩阵的每族分列是手抄本，5 行错 4 行**：矩阵原先只有一列"精确解析"，逐族写
  18 / 12 / 8 / 15 / 8——**和**恰好等于总解析器数 61，所以"61 精确解析器"的总数门禁一直绿，
  分列却全错。真相源（`PARSERS` 的 `(family, code)` 键）是 18 / 15 / 16 / 1 / 11；命令账本
  另有其数 39 / 17 / 16 / 2 / 11（和 = 85，此前被读成"每族命令数"）。矩阵现拆成
  "命令账本 / 精确解析器"两列，并把族键写进每行标签（`**MAC 专属**（\`mac_quotation\`）`），
  门禁因此不必在测试里另抄一份"显示名→族键"映射——文档自己声明它指哪一族。
- **商品语义的端口写反**：文档 7709，而 `protocol/commands.py::Command.port` 与主站池
  （`transport/hosts.py:351` 把 GOODS 池由 EXTENDED 池派生）都是 7727。新门禁
  `test_readme_protocol_matrix_matches_the_registries` 逐行比 `(端口, 命令数, 解析器数)`
  三元组，并要求族集合与注册表相等（新增协议族不写这行即红）。
- **同一事实的第二种写法长期在表外**：已钉"45+ HTTP 源"，未钉"web 45 源"这种裸抄本；
  `172 capability`、`11 Provider`、`CLI 31 子命令`、`10 端点`、`9 工具`、`5 套协议族`、
  `全 5 族`、`5 族客户端`、`61 × N 族`、`15 便捷方法`、`Client 15 方法`、`28 模块`、
  `6 源合并`、`251 条精确绑定`（框图/特性表/目录树三种措辞）此前没有一行被读过。本步补
  20 条精确宣称行 + 3 条下界行 + 10 个真相源 helper。README 的 `parsers(61 × 6 族)`
  （两处）与"17 模块"是同批过期抄本，改为真相源（5 族 / 28 模块）；"web 45 源"改为
  非数字写法"web 多源"，`docs/api/README.md` 的"11 源 × channel"改为"11 Provider ×
  channel"，让 Provider 数与 HTTP 源类不再共用"源"这个量词。
- **改写法不能静默逃逸**：每行的 `assert claimed` 使宣称串一旦改名或删除就报"门禁失效"
  而非悄悄少对一处（与 F-35 同形）。
- **变异验证 26 条全部 RED 且各自指名**：矩阵 4 例（解析器 18→17、MAC 命令 16→8、商品端口
  7727→7709、族键 `ex_quotation`→`extended` 报"族集合不符：矩阵 ['extended'] 多、
  ['ex_quotation'] 缺"）、下界 3 例（"45+ 源类"→"45 源类" 报"门禁失效"、"40+ 异常类"→"50+"
  报"实际只有 46 个"）、精确宣称 16 例（17 模块 vs 28、170/171 capability、CLI 30、
  HTTP 12 端点、MCP 12 工具、全 4 族、61 × 6 族、14 便捷方法、`Client` 16 方法、
  4 套协议族、5/7 源合并、85 命令账本（6 协议族）等）、绑定条数 3 例（250/240/252 vs 251）。
- **复测（同一轮日志）**：主树整仓离线 `-m "not network"` junit `3334 tests / 24 failures /
  0 errors / 7 skipped`、覆盖率 79.42%——**24 条红全部是同一个根因
  `TypeError: QuerySpec.build() got an unexpected keyword argument 'max_age'`**，来自并行会话
  在途的 `tstdx/query.py` 改动，本步未代为修改。为此按第 18 步的做法在 HEAD
  （`867f6d2`）+ 本步 3 个文件单开 worktree 复跑：junit **3337 tests / 0 failures / 0 errors /
  7 skipped**、`ISO_FULL_RC=0`、`--cov=tstdx` **80.51%**（日志明写
  `Required test coverage of 77.0% reached`，阈值 77 未下调）。计数关系可核对：
  `3334 + 3（本步新增的三条绑定宣称行）= 3337`。同一 worktree 内
  `check_originality --strict tstdx/`（189 文件、Suspicious 0）、`spec_audit --json --strict`
  （`coverage_pct: 100.0`）、`golden_audit --gate --require-markets`、
  `audit_reachability --strict`（无未登记孤儿 ✓）、`contract_audit --ci`
  （63 契约 / 155 capability）、docs links（82 文件）、`ruff check` 与 `format --check`
  （430 files）、`mypy tstdx/`（CI 参数）**全部 RC=0**。本步只动文档与门禁测试，
  未触碰 `tstdx/` 任何一行代码。

### Removed（v17 Phase 5 第 18 步 —— 缓存层删除后残留的"缓存形状"，F-40/F-41）

- **`tstdx.domain.finance` 的 `CapitalChangeCache` 整族物理删除**（不留别名）：
  `CapitalChangeCache` / `get_capital_change_cache` / `default_factor_cache_dir` /
  `ENV_FACTOR_CACHE_DIR`（`TSTDX_FACTOR_CACHE_DIR`）/ `DEFAULT_FACTOR_TTL_SECONDS` /
  `_FETCHED_AT_KEY` 及其 `__all__` 条目，`finance.py` 301→162 行。它是 Phase 2 删缓存层
  后留下的孤儿：自带 TTL + `~/.tstdx/factors` 落盘语义，docstring 明写命中即"跳过
  0x0010/gpcw 网络与解析"，却在 `tstdx/` 内**零调用方**——只有它自己的 8 项单测在维持
  "它活着"的假象。运行期"数据请求不需要缓存"由此从口径变成包内事实：不再有形状可以被
  一行 import 接回去。
- **`Provenance.cached(tier)` / `cache_hit` / `direct_fetch` 删除**（`result.py`
  161→148 行）：生产路径只经 `Provenance.direct()` 构造，tier 永不为非空，这三个成员同样
  零消费者，唯一引用是那项"自己造一个 tier 再断言能造出来"的自测。**`cache_tier` 字段
  刻意保留**——它是 CLI/HTTP/MCP 三面 wire 上恒为 `null` 的零缓存证据。
- **`result.py` 模块 docstring 改写**：原文描述 `cache_tier='l1'/'l2'` 取回模型并称其为
  "later cache-poisoning and freshness gates 的地基"，即包内文档在描述一个不存在的层。
- **PyPI 元数据不再宣称 `semantic caching`**（F-41）：`pyproject.toml` 的 `description`
  改为 "explicit Providers, direct Provider reads, Stateful streaming and canonical
  HTTP/WebSocket/MCP adapters"。对外最显眼的一句话此前是全仓最错的一句，而元数据文件
  从来不在事实门禁的扫描名单里。
- **三条门禁**：`test_package_defines_no_data_cache_layer`（AST 扫 `tstdx/**` 的
  `*Cache*` 类与 `get_*cache*` 函数；`functools.lru_cache` 这类纯函数记忆化明确排除，
  因为它不省掉任何一次网络请求）、`test_pypi_description_claims_no_caching`（描述含
  `cach` 即红）、`test_direct_provenance_carries_no_cache_tier`（`hasattr` 反向钉住三个
  已删词汇）。两条变异验证各自 RC=1 且指名（塞入 `class QuoteCache`、把
  `semantic caching` 写回描述）。
- **同一轮复测**：整仓离线 `-m "not network"` junit `3313 tests / 0 failures / 0 errors /
  5 skipped`、`FULL_RC=0`，`--cov=tstdx` **80.58%**（阈值 77 未下调）；`ruff check` /
  `format --check` 干净，`mypy`（CI 参数）对改动的两个模块 Success。

### Fixed（v17 Phase 5 第 17 步 —— 契约分母改结构判据、领域基类数入门禁，F-39）

- **"60+ Typed Query 契约"的分母不再由手抄名单决定**：`scripts/contract_audit.py::
  _all_typed_queries` 原先用一份 12 个名字的 `skip` 集合排除抽象基类，同一份名单在
  `tests/v14/test_contract_automation.py` 里又各自抄了 4 遍（全仓共 5 份副本）。实测这
  12 个类在四种构造路径（`cls()`、`_minimal_instance`、kwargs 阶梯、factory 映射）下
  **全部构造失败**——各站点自带的 `except Exception: continue` 早就把它们排除了，名单不
  决定读数，却决定读者的信任。危险方向是**漏更**：新增抽象基类若没同步这 5 份名单就会被
  算进契约数，`60+ 契约` 与 PyPI 规模口径同时虚增而审计全绿（F-19 是同一处的反方向缺陷：
  那次分母被读小）。现改为**纯结构判据**（可构造 + `capability` 为字符串），5 份名单全部
  删除。`contract_audit --ci` 实测回显 `63 个 Typed Query 契约 / 155 个注册业务
  capability`，与删名单前逐项相同 ⇒ 改判无损。
- **"11 领域基类"撤回为 10**：该宣称在 README（2 处）、`docs/api/README.md`、
  `docs/api/interfaces.md` 共 4 份抄本，而按任何自然定义都不成立——`tstdx/typed_query.py`
  里被其它契约直接继承的抽象 dataclass 是 10 个，含根 `CapabilityQuery` 才 11 个，根不是
  "领域"。判据（不含根）写进 `_typed_domain_base_names()` 的 docstring 后进入门禁；第 15
  步 F-35 那条"分母含糊所以刻意不钉"的口径就此撤销——含糊的不是事实，是没写判据。同批把
  README 两处"9 Domain Record 族"也纳入数字门禁（此前只在 api 文档钉过，README 抄本在
  盲区）。
- **审计脚本的自述进门禁 + 防回潮**：`test_contract_audit_docstring_numbers_match_the_audit`
  把 `_all_typed_queries` docstring 里写着的 155/63 钉回它自己算出的数（F-25 对齐了"它跑
  什么"，没对齐"它抄什么"）；`test_typed_query_denominator_is_not_a_hand_copied_list` 禁止
  名单回潮——那 12 个名字里任何一个以字符串字面量重新出现在审计脚本或 v14 套件里即为红。
- **顺带修掉该套件里一处测量装置缺陷**：`test_cli_script_exits_zero` 以 `text=True` 捕获
  子进程输出却不指定编码，Windows 下子进程的 GBK 输出使 `_readerthread` 抛
  `UnicodeDecodeError`（以 `PytestUnhandledThreadExceptionWarning` 长期滞留；且审计一旦
  真失败，失败分支的 `result.stdout[-800:]` 会因 `stdout is None` 二次崩掉诊断信息）。
  现固定 `PYTHONIOENCODING=utf-8` + `encoding="utf-8"`，warning 消失。
- **同一轮复测**：`tests/architecture/` + `tests/v14/` junit `211 tests / 0 failures /
  0 errors`、RC=0；`contract_audit --ci` RC=0；`ruff check` 与 `format --check` 对触及的
  3 个文件干净；整仓离线 `-m "not network"` junit `3313 tests / 0 failures / 0 errors /
  7 skipped`、`FULL_RC=0`，`--cov=tstdx` **80.52%**（日志明写 `Required test coverage of
  77.0% reached`，阈值 77 未下调）。**变异验证 8 条全部 RED 且各自指名**：塞回
  `skip = {"MarketDataQuery"}`、自述 155→150、63→62、三处领域基类各改 1、README 的
  Record 族 9→8。

### Fixed（v17 Phase 5 第 16 步 —— 发布冒烟引用已删除模块，F-36）

- **wheel 安装冒烟不再 import 已不存在的 `tstdx.facade`**：`scripts/build_package.py` 的
  安装探针里残留 `from tstdx.facade import UnifiedQuoteAPI`，而该模块已随 Phase 1b
  单内核化物理删除。这段 import 住在 `python -I -c "<字符串>"` 里，因此按文件路径对账的
  存在性守卫（F-24 补的那套）结构性扫不到它——`make build --smoke` 与 wheels job 的最后
  一步会 ImportError，发布链路固定为红。
- 探针改为断言**唯一业务入口的通用面在 wheel 内可用**：`from tstdx import Client` +
  `callable(Client.call)` / `callable(Client.typed)`，而不是删掉了事。
- **新增防回潮守卫** `tests/compatibility/test_local_smoke_hardening_contract.py::
  test_release_smoke_imports_only_symbols_that_still_exist`：正则抽出 `build_package.py`
  与 `.github/workflows/wheels.yml` 两处冒烟脚本里的全部 `tstdx` import，逐个过
  `importlib.util.find_spec` 与 `hasattr`，并断言解析结果非空以免守卫自身失明。
  变异验证：把那行 facade import 塞回探针即报
  `发布冒烟 import 了不存在的模块：['tstdx.facade']`。
- **本轮把该冒烟真正跑通**（全离线建临时 venv 装 canonical wheel）：`SMOKE_RC=0`、
  `tstdx --help` 与 `tstdx hosts audit --help` 均退出 0、`pip check` 回
  `No broken requirements found`；wheel 内零 `facade` 与零已解散的整方法侧车。
- 同一轮的真实网络/服务面冒烟另立出 **F-37（P0：7709 K 线在当下可达主站返回 2 字节
  空桩却被读成成功）** 与 **F-38（该类缺陷在现有门禁里零 live 判据）**，处置路径待裁决，
  详见 `docs/REFACTOR_PLAN_V17_CLOSURE.md` §0.3；本轮未改任何协议字节。

### Changed（v17 Phase 5 收口 —— 传输层"整方法桩层"解散，F-30）

- **连接池的 import 期整方法 monkey-patch 层物理删除**：`_pool_hardening`（461 行）、
  `_async_pool_hardening`（530 行）、`_async_close_hardening`（129 行）、
  `_pool_provenance_hardening`（327 行）四个侧车整体替换 `ConnectionPool` /
  `AsyncConnectionPool` 的方法，使 `pool.py`、`async_.py` 类体内的原实现成为
  **永不执行的死代码**（语句覆盖率 46.2% / 44.7%），而可达性门禁看不见这种遮蔽——
  被覆盖的原方法在静态导入图里依旧"可达"。
- 实现搬回拥有它的类体；主站代际发布的三条共享原语（`validate_host_updates` /
  `new_endpoint_entry` / `next_generation_host`）上收到 `tstdx/transport/hosts.py`，
  同步池与异步池共用一套规则，消掉"第二事实源"。
- **等价性是实证不是目测**：31 个搬迁成员逐个与 `git HEAD` 的侧车原文做归一化
  `ast.dump()` 比对（只归一 `pool`→`self`、`_impl.X`→`X`、`helper(self, …)`→
  `self.helper(…)`、重命名成员、docstring 与注解），差异 0。
- **防回潮反向钉住**：`tests/transport/test_public_pool_hardening_wiring.py` 从
  "断言实现住在 `_hardening` 侧车"改为"断言实现住在池模块 + `find_spec` 判侧车
  不存在"；本地冒烟与两条 wheel 契约同批改判，重新引入桩层即红。
- 保留的注册型侧车（`_pool_family_hardening.__init__`、`_ranking_hardening`、
  `_connection_contract_hardening`、`_host_selector_hardening`、client 层四件）
  不属本项：它们只做局部包装，不整方法替换。收敛它们登记为发布后独立 PR。
- **同一轮复测**：`PYTEST_RC=0`，整仓离线覆盖率 **80.60%**（阈值 77 未下调），
  `pool.py` 46.2%→**80%**、`async_.py` 44.7%→**89%**；`ruff check` 与
  `ruff format --check` 干净、`mypy`（CI 参数）188 文件 0 错、
  `audit_reachability.py --strict` RC=0（`188 模块 / 可达 171 / 豁免 17`）、
  docs link check 82 文件 OK。

### Changed（v17 Phase 6 —— 配置面接线与死面清偿，F-13/F-16）

- **`tstdx.toml` 从此真的生效**。`UnifiedRuntime` 成为配置面的唯一读者：`Client()` /
  `AsyncClient()` 缺省经 `tstdx.config.get_config()`（进程级惰性、6 源合并）取配置，
  `Client(config=...)` 与显式入参仍然优先。贯通的键：`core.default_provider` →
  `QueryPlanner`；`core.timeout / max_retries / heartbeat_interval`、
  `hosts.servers / slots_per_host`、`rate_limit.*`、`security.use_tls` →
  `DirectProviderExecutor → TdxClient → ConnectionPool`；`core.vipdoc_root` →
  `adjusted_bars` / `sync_daily` / `local_vipdoc`；`web.*` → `WebQuoteClient`。
- 配置 → 传输层参数只有**一个**翻译点：`tstdx.transport.pool.pool_settings_from_config()`。
  取代它的是被删除的第二读者 `ConnectionPool.from_config`。
- 进程级配置语义变更：`get_config()` 首次访问时按全部源解析一次（此前恒返回
  `DEFAULT_CONFIG`）；`reset_config()` 改为清空单例使下次重新读源（此前是把
  `DEFAULT_CONFIG` 塞回去）。
- 新增用户面文档 [docs/configuration.md](docs/configuration.md)（5 段全键清单 + 读取方 +
  取值范围 + fail-closed 语义 + 环境变量规则），并纳入事实型文档门禁；决策与取舍记录在
  [ADR-016](docs/adr/ADR-016-config-surface-covers-execution-only.md)。

### Changed（v17 Phase 5 第 2 步 —— 格式基线与 dev 工具版本）

- **`ruff format` 门禁由长期红转为基线绿**：一次纯格式提交重排仓内 **65** 个 `.py`
  文件（HEAD 既有 74，Phase 6 删改触及后净减 9）。语义不变已实证：对全部 65 个文件
  比较重排前后 `ast.dump()`，差异为零（`offenders: none`），且格式提交与功能提交分离，
  diff 可审。
- dev 依赖 `ruff`/`mypy` 从浮动区间（`ruff>=0.5`、`mypy>=1.10`）改为**精确钉版**
  （`ruff==0.15.2`、`mypy==2.3.1`，即本次实测通过全部门禁的版本）。理由：`ruff format`
  的重排结果与 `mypy` 的错误集都是版本敏感的，浮动区间会把格式/类型门禁变成版本漂移报告。
  安装 `dev` extras 的环境（CI 与本地钩子入口 `python -m ruff` / `python -m mypy`）因此
  解析到同一版本；升级需单独提交并同步重测门禁。

### Changed（v17 Phase 5 第 3 步 —— Web 会话层命名归位，F-11）

`tstdx/web/` 里仍以"门面（facade）"命名的模块，其名字指向的 `UnifiedQuoteAPI` 门面已随
v16 Phase 2 物理删除；这些模块的真实角色是 `WebQuoteSession` 的**按域方法分组**。全部改名，
clean-break——旧模块名不保留别名或再导出：

| 旧路径 | 新路径 |
| --- | --- |
| `tstdx/web/facade.py`（`tstdx.web.facade`） | `tstdx/web/session.py`（`tstdx.web.session`） |
| `tstdx/web/_facade_mixin_<域>.py`（11 个：`astock` / `baidu` / `efinance` / `fund_v2` / `fundamental` / `info` / `market` / `news` / `p1` / `p2` / `p3`） | `tstdx/web/_session_<域>.py`（后缀一一对应，不重命名域） |

- 符号名不变：`WebQuoteSession`、`web_session`、`_SessionBase` 及各 Mixin 类名保持原样；
  `from tstdx.web import WebQuoteSession` / `web_session` 的根面包入口也不变。
- 改的是**运行期字符串**，不只是 import：`tstdx/catalog/provider_bindings.py` 里
  `catalog` 通道的 `("tstdx.web.facade", "WebQuoteSession")` 绑定、
  `tstdx/web/__init__.py` 的 `_LAZY` 子模块名 `"facade"`，都是按名字解析的键。
- 文档面同步：`docs/api/README.md` 的 `tstdx.web.session.WebQuoteSession` 事实路径、
  4 份数据源审计文档的路径引用。已发布版本段（`[1.0.0]` 及更早）与 `docs/archive/` 的
  历史叙述保留当时名字。

### Removed（v17 Phase 5 第 4 步 —— 三项 strict 门禁的本地红清偿）

CI 上 originality / spec_audit / reachability 三项是硬门禁，本机实测皆红。逐项区分
**真缺陷**与**扫描器误报**后清偿，阈值一次都没有放宽：

- 删除 3 项零生产消费者的死面（clean-break，无别名）：`tstdx/observability/planned.py`
  （缓存 / singleflight 时代的指标名，唯一读者是它自己的测试）连同其测试文件、
  `tstdx/providers/adapter.py`（`ProviderAdapter` / `ProviderMetadata` ABC，全仓含测试
  零引用）、以及符号 `BatchSpec`（见下条）。
- **删除 `BatchSpec`**（`tstdx/batch.py` 里的 v13 批量请求信封）：它文档化的职责——
  「`UnifiedRuntime.execute_batch` 把一个 BatchSpec 展开成多个计划」——指向 v16 Phase 2
  已物理删除的方法，且生产代码无一处构造它（只有测试在证明它能构造）。批量展开的唯一
  实现路径是 `UnifiedRuntime.quotes_batch` 那条逐 symbol 直连循环，业务入口是
  `Client.quotes_batch`。同步移除：`tstdx.__all__` / `tstdx._LAZY` 条目（根面 46 → 45
  项）、`docs/api/interfaces.md` 的导入示例、README「下一阶段」里的占位行；符号进入
  `test_single_kernel_guards.py` 的 `DELETED_SYMBOLS`。`BatchItem` / `BatchResult` 的
  三态审计契约测试保留，并补上「重复 symbol 归一后只请求一次」这一原由 BatchSpec 测试
  代管的断言。
- **交易日历按接线收口，而非豁免**：`tstdx/domain/calendar.py` 原是孤儿，而
  `tstdx/tools/capture.py` 自带一份只认周末的交易时段副本——法定节假日会被误判成交易
  时段，从而错误中止本应放行的采集。法律自检改为消费 `TradingSession` 常量与
  `is_trading_day`：重复事实消除，日历经 `python -m` 入口自然生产可达 ⇒ 不进白名单。
- **`scripts/audit_reachability.py` 三处图修正**（都是漏报方向，不是放宽判定）：种子名单
  换成 v16 后的现行模块名（写死的 `http_server` / `ws_server` / `mcp_server` 会被
  `if s in modules` 静默丢弃，使整条服务面从图里消失）；`_LAZY` 边解析支持 `AnnAssign`
  与不带包前缀的子模块名字符串（F-11 后 `"facade"` → `"session"` 这类键此前看不见）；
  `tstdx.tools.*` 与 `tstdx.cli` 由「整包直接标可达」改为**作为种子参与 BFS**，入口独占
  的依赖不再被误判为孤儿。
- 白名单登记 2 项并附评审理由：`tstdx.catalog.provider_contract`（Provider 隔离契约，
  由 `tests/provider_isolation` 与命名空间守卫消费，内核不 import 是其零依赖设计的前提）、
  `tstdx.providers.http`（Provider 绑定 HTTP 主机边界守卫，离线测试全覆盖；v16 删除跨源
  路由层后无生产调用点，接线会改变 web 传输的失败语义，属安全面决策待确认）。
- `check_originality` 的两类红分别是：`tstdx/catalog/provider_contract.py` 与
  `tstdx/py.typed` 缺许可证头（补头），以及 `tstdx/batch.py` 一段散文被「derived from」
  模式误命中（改写为不含该措辞的等价表述）。全仓现为
  `Total: 194  Original: 194  License OK: 194  Header OK: 194  Suspicious: 0`。
- 顺带清掉三处幻影文档：`tstdx/batch.py` 模块 docstring 指向已删除的 `execute_batch`；
  `tstdx/domain/calendar.py` 声称「ratelimit 消费点在 transport」（实际无此消费方）并把
  `update_from_web` 写成可用的在线校准（该方法恒抛 `NotImplementedError`）；README 批量
  执行行宣称「并发走内核」，而内核批量路径是串行逐 symbol 直连。
- 复测（每条命令先重定向再取 RC，见下条）：originality `Total: 194 / Original: 194 /
  License OK: 194 / Header OK: 194 / Suspicious: 0` RC=0、reachability
  `模块总数: 193 / 可达: 174 / 白名单豁免: 19 / 无未登记孤儿` RC=0、spec-coverage
  `total 44 / in_ledger 44 / has_parser 42 / control_frames 2 / trade_plane 6 /
  coverage 100.0%` RC=0（**100% 阈值未动**）。格式与类型：`ruff check .` /
  `ruff format --check .`（433 文件）/ `mypy tstdx/`（CI 参数，193 源文件）均 RC=0，
  golden 门禁与 docs-code 一致性门禁 RC=0。
- 离线全量套件（WIP 文件入库后的整仓值）：**3227 passed / 7 skipped / 1 xpassed /
  0 failed，coverage 78.79%**，`PYTEST_RC=0`。测量路上读到的三个数都记在这儿，以免日后
  对不上：WIP 文件尚未跟踪时带 `--ignore` 的运行是 `3185 passed / 78.87%`；含它的运行是
  `15 failed / 78.61%`（那 12 项失败是并行会话的未完成测试，随后由 `4ae1e38` 修好入库、
  42 项全绿）；补装 `pandas` 之前是 `3 failed, 3182 passed / 78.20%`。
  最后这 3 项失败全部来自 `tests/reader/test_formats.py` 的 DataFrame 形状断言抛
  `DependencyMissingError [E1020]`，即本机缺 `[dataframe]` extra（CI 的 `.[dev]` 含它）。
  **处置是补环境而不是加 skip**——`uv pip install "pandas>=2.0"` 后该文件 41 项全绿；加
  `importorskip` 能当场抹掉这 3 项，但会把本机环境与 CI 永久分叉，也违反「不得为过 CI 增加
  跳过」的约束。
- 测量方法修正（本次红灯曾被读成绿灯的直接原因）：门禁命令接上管道后 `$?` 是管道末端的
  退出码，`cmd | tail; echo $?` 因此把三项 strict 红读成「已绿」。该约束连同正确写法已写入
  [CONTRIBUTING.md](CONTRIBUTING.md) 的门禁段。

### Changed（v17 Phase 5 第 5 步 —— 豁免清单本身成为被审计对象，F-22）

上一步把三项 strict 门禁改成绿灯之后，回头审这些绿灯读到的输入：`--strict` 绿只说明
**判定规则没被违反**，不说明规则吃的清单是真的。

- `scripts/audit_reachability.py` 新增**记录守卫**，四类缺陷与孤儿同权重使 `--strict`
  失败，并在输出里单列 `[ALLOW-DEFECT]`：
  - `[dead]`：豁免指向磁盘上不存在的模块——它已经不再豁免任何东西。
  - `[stale]`：豁免指向**已从入口可达**的模块。这类比 `[dead]` 更危险：留着它，将来
    这个模块真被断链时扫描器照样报绿，等于把未来的缺陷预付成绿灯。
  - `[thin]`：理由短于 `MIN_REASON_CHARS = 40`，说不出「谁消费它 + 为什么生产链路不
    import 它」的记录不可核验。
  - `[dup]`：同模块重复登记，后一条静默覆盖前一条。
- `tstdx.__main__` 由「豁免」改判为 `_entrypoints()` **种子**，与 `tstdx.cli` /
  `tstdx.tools.*` 同类：静态 import 图里永不出现对它的边，而 `python -m tstdx` 一定加载
  它——用豁免表达这种事入口，等于把工具口径缺陷记成产品决定。
- `scripts/_reach_allow.txt` 28 → **17** 条：删 9 条死记录（`tstdx.sinks`、`tstdx.native`、
  `tstdx.feedback*`、`tstdx.domain.adjust`、`tstdx.streaming.{engine,push}`、`tstdx.security`），
  修正 2 条**编造的理由**（`tstdx.charset`、`tstdx.deprecation` 写着「`_LAZY` 导出」，
  根包 `_LAZY` 实测 17 个值里没有它们），其余逐条附可核验证据（文档路径 + 具体测试文件）。
  TRADE 族的理由额外写明：`spec_audit` 是**按字符串模块名走 `importlib`** 解析这些模块的，
  AST 静态图看不见这种边，故其「不可达」是工具口径而非死代码。
- 删除 `pyproject.toml` 中同形状的死配置 2 处：mypy 的 `tstdx.native.*` 覆盖段、
  `keyring.*` 的 ignore-missing-imports 条目。mypy 自己的 `warn_unused_configs` 早已把
  前者报为 note，只是没人把 note 当缺陷处理；现在该 note 消失。
- 新增守卫回归 `tests/architecture/test_reachability_allowlist.py`（8 项：真实清单干净、
  `--strict` 在当前树上为 0、进程入口不靠豁免、四类缺陷各自都被抓住）。
- 复测：reachability `--strict` RC=0，`模块总数: 192 / 可达: 175 / 白名单豁免: 17 /
  记录缺陷 0`；`tests/architecture/` 88 passed；`mypy`（CI 参数）RC=0。判定强度未放宽。

### Removed（v17 Phase 5 第 5 步 —— 撤回一条已发布的安全假承诺，F-23）

- **删除空壳包 `tstdx/security/`**：其 `__init__.py` 内容是 `__all__: list[str] = []`，
  docstring 指向从未存在的 `tstdx/security/capture.py` 与 `origin.py`。它唯一的用途是给
  SECURITY.md 里那条自 **v10** 起就过时的历史叙述当证据。
- **SECURITY.md「凭据保护」改写**：原文宣称「tstdx 使用三级凭据存储：系统 keyring /
  环境变量 / 加密文件 `~/.tstdx/credentials.enc`」。`CredentialStore` 早在 **v10** 就按
  [ADR-007-010](docs/adr/ADR-007-010.md) 判定「全库零调用方、属
  过度工程」而物理删除。现改为「本库**不存储凭据**」+ 四条现状：行情链路不涉及凭据；
  交易侧是纯内存模拟器，`obfuscate_password` 是 clean-room 占位实现；真实券商凭据由调用方
  自管；错误上下文与反馈先脱敏后出口。
- **README 两处同步**：特性行的「凭据三级存储」换成可核验事实（`security.use_tls` 走
  `ssl.create_default_context()`、关键字脱敏、凭据不在本库职责内），并就地标注
  `tstdx.providers.http` 的主机边界守卫「已实现但尚未接入 web 链路」（与待决策的 F-18
  同一口径）；ASCII 概览框与结构树删去 `security/` 行，补上此前漏列的 `__main__.py`。
- **补门禁**：`tests/architecture/test_doc_code_consistency.py` 新增 3 项，把 README
  结构树条目与磁盘做双向对账——列出的路径必须存在，磁盘上的顶层包与顶层模块必须都列出。
  散文式能力承诺本质上不可机器判定，这一步只压缩它的隐身空间，不声称根治。

### Removed（v17 Phase 5 第 6 步 —— 删掉一条永远跑不起来的 PR 阻塞门禁，F-24）

本地按 CI 参数逐条复跑整条确定性门禁链（每条先重定向再取 RC），11 项绿灯里抓到一项
`RC=4`：`make gates` 的最后一步 `native-compat` 指向
`tests/compatibility/test_native_fallback_contract.py`，而该文件连同被测模块
`tstdx/native.py` 已在 `77bc2fe`（v16 Phase 2）物理删除。同一形状的引用还留在
`.github/workflows/native.yml` 的一个 `pull_request` 阻塞 job 里——它的「编译」一步用
`python -m compileall -q tstdx/native.py`，该命令对不存在的路径**打印 "Can't list" 却
退出 0**（本机实测），于是前一步静默假通过，红只在下一步的 pytest 上才显形。

- 删除 `native.yml` 与 `Makefile` 的 `native-compat` target 及 `gates` 依赖项。取
  「删除」而非「恢复测试」，因为被测能力本身已随 `tstdx.native` 退役，没有可恢复的对象。
- **删掉一条反向契约**：`test_ci_workflow_contracts.py` 原有断言「workflow 必须包含那个
  已删除的测试路径」。这类「必须包含某字符串」的守卫若不连带断言字符串所指存在，就会在
  删除之后变成阻止清理僵尸的护栏。
- 文档面同步：PR 模板去掉那条无人能诚实勾选的合并证据项；CONTRIBUTING 门禁清单去掉
  Native 一项；`.pre-commit-config.yaml` 注释同改。

### Fixed（v17 Phase 5 第 6 步 —— 门禁定义与文档口径对账）

- 新增三项存在性守卫，把「门禁自己指向不存在的文件」这类缺陷变成红灯而不是绿等：
  `.github/workflows/*.yml` 与 `Makefile` 里写死的 `tests/…`、`scripts/…`、`tstdx/…`
  `.py` 路径必须存在于磁盘；`gates:` 的每个前置 target 必须在 Makefile 里已定义。
- 新增 README 规模数字对账：`CI：N jobs` 与 `N 步确定性门禁` 必须分别等于 `ci.yml` 的
  job 数与 `gates` 的 target 数。原口径为「9 jobs」「六步门禁」，实际是 11 与 11。
- README 的 Ruff 行停在既成事实之前（「`ruff format --check` 仍有既存待重排文件」），
  现改为两项皆 0 错并标明钉版 `ruff==0.15.2`。
- 上述四项守卫逐一做过**变异验证**：分别往 ci.yml、Makefile 插入指向不存在文件的路径、
  给 `gates:` 加未定义 target、把 README 数字改回 9/12，四次皆 RC=1 且指名具体缺陷，
  随后原样还原（`git diff` 确认无残留）。守卫不亲测等于没有。
- 复测（同一条链、同样单独取 RC）：11 项门禁全 RC=0；离线全量套件
  **3238 passed / 7 skipped / 1 xpassed / 0 failed，coverage 78.80%**，`PYTEST_RC=0`，
  阈值 77 未动。

### Fixed（v17 Phase 5 第 6 步 —— `contract_audit` 的自述与行为对齐，F-25）

- `scripts/contract_audit.py` 的模块 docstring 宣称规则 1「每个业务 capability **必须**有
  Typed Query 契约」、`--ci`「任何缺口 exit 1」、退出码「0=全绿」；实际代码把"注册表有、
  契约无"降级为 `PENDING`，`run()` 只对 `ERROR` 计数。实测口径是 **155 个业务
  capability / 63 个有契约 ⇒ 92 项 PENDING 一律 exit 0**——自述比行为强，读文档的人会把
  "绿"理解成契约全覆盖。现逐条标注级别（规则 1、4 = PENDING，2、3、5 = ERROR）并写明
  `--ci` 的真实阻断条件。
- **没有**把 PENDING 升级为阻断：那要求一次性补 92 份契约，且会把已知待办伪装成既成事实。
  缺口尺寸记在重构方案里（F-25），不塞进门禁。
- README 的两处描述随之更正：它写的"契约↔注册表↔**绑定**三方对账"里，provider bindings
  根本不是这个工具的审计维度；现为 Registry/语义就绪/内核编译/Domain Record/往返五段。
  同批把里程碑表两行停在既成事实之前的状态行（Phase 4「门禁在建」、Phase 5「⏳」）改为
  实测结论，并保留两项真实待办（CI 侧覆盖率重钉、真实网络 smoke + tag）。

### Added（v17 Phase 5 第 7 步 —— 声明面 ↔ 执行面对账，F-26）

- **`audit_capability_bindings()` 新增第三面对账**：以 **Provider 注册表**逐 channel 声明的
  `(provider, channel, capability)` 三元组作为独立分母，与执行器绑定表做**双向**差集——
  声明了却没有执行路径 ⇒ `registry-declared … with no executor binding`；有执行路径却没在
  注册表登记 ⇒ `executor bindings outside the Provider registry`。报告新增
  `declared_bindings` 字段。该审计由 `runtime/audit.py` 在**每次构造
  `DirectProviderExecutor` 时**执行，所以断链在客户端构造期即失败，不需等到某次取数。
  该不变量此前只写在离线测试（`tests/runtime/test_v13_architecture_alignment.py`）里，
  即只保护"跑测试的人"；运行期判据中注册表这一维是缺位的。
- 守卫测试从"`report.* > 0`"（只剩 1 条绑定也绿）升级为真实不变量：注册表声明的 capability
  名集合必须等于 `Client.capabilities()` 对外承诺的集合；并用 monkeypatch 注入三处漂移
  （丢一条目录内绑定、丢一条目录外绑定、加一条幽灵绑定）逐条命中对应错误消息，证明双向
  各有牙。
- 当前实测：`migrated 229 ⊆ executable 251 = declared 251`，双向差集为空 ⇒ 主体链路在
  "声明 ↔ 执行"这一维闭合。这是对本轮"核心功能是否全部实现、主体链路是否全部贯通"的
  可复核回答；同时如实标注边界——**绑定存在 ≠ 运行期正确**，后者仍依赖 golden/adversarial
  门禁与尚未获准执行的真实网络冒烟。

### Fixed（v17 Phase 5 第 7 步 —— 审计分母的同源陷阱）

- 曾考虑用 `Client.capabilities()` 充当"对外承诺面"这一分母，**已否决并记录理由**：它本身
  由 `MIGRATED_CAPABILITIES` 推导，而后者与绑定表同源，用它对账等于左手查右手；且在
  `catalog` 内惰性 import 入口层会在构造执行器时反向拉起 `Client`。公共面对注册表的约束
  因此改由测试层承担（`test_registry_declaration_matches_public_surface`）。

### Changed（v17 Phase 5 第 8 步 —— CLI 连接参数与配置面对齐，F-27）

- **`tstdx --host` / `--timeout` 从此真的到达执行面**。改前 7 个行情命令（`quotes`/`bars`/
  `snapshot`/`minute`/`trades`/`security-count`/`security-list`）解析了 `--host` 却构造裸
  `Client()`，参数当场丢弃且命令照常返回数据（fail-open）；15 个命令的 `--timeout` 带
  `default=5.0` 字面值，与 `[core] timeout` 的默认值数值相同，因此只有在用户真去改
  `tstdx.toml` 时才暴露为"配置不生效"。现 CLI 只有两种合法姿态：**用户显式说过即转下去，
  未说过即交 `None` 让内核读配置**。
- `probe`/`blocks`/`list`/`quotes-snapshot`/`stream` 5 个"内核外自建传输客户端"的命令改为
  经 `_transport_kwargs` 解析 `[hosts] servers`（与内核 `runtime/kernel.py` 同一条优先级
  规则）；`goods`/`f10` 只统一 timeout，其 `hosts` 仍只认 `--host`——`[hosts] servers` 是
  7709 标准族条目，喂给 goods/F10 协议客户端是错的。
- `stream --provider` 此前解析后从不转发（非 tdx 静默按 tdx 跑），现已接线。
- `--timeout` 的字面默认值仅保留在 `hosts` 与 `server-test` 两个诊断命令上（它们要遍历
  候选主站池，本就不该吃 `[core] timeout`），其余 15 处改为 `None`。

### Added（v17 Phase 5 第 8 步 —— 防"幻影开关"与失效示例的两道门禁）

- `tests/architecture/test_cli_connection_contract.py`：用 fake `Client` 捕获真实构造参数，
  对每个走内核的命令断言"显式 `--host` 必达 / 未指定必为 `None`"；另有一条**结构性守卫**，
  凡 parser 声明了 `--host`/`--timeout` 且不在诊断白名单内的命令，其 handler 必须使用
  `_client_kwargs` / `_transport_kwargs` / `_transport_timeout` 之一 —— 对将来新增的命令
  同样生效。变异验证：把 `cmd_quotes` 退回裸 `Client()` 后三条断言同时命中。
- `tests/architecture/test_doc_code_consistency.py::test_every_documented_cli_example_parses`：
  活文档（围栏代码块 + 行内代码）里以 CLI 程序名开头的命令行逐条过真实 parser，含 `<>` /
  `[…]` 的用法语法行豁免。**上线即抓到 4 条本轮之前就失效的文档命令**（见下条 Fixed）。

### Fixed（v17 Phase 5 第 8 步 —— 文档里的 CLI 示例照抄即 exit 2）

- README：`serve` 的绑定参数写成了已改名的 `--host`（现为 `--bind`）；两处 `hosts audit
  --hosts-file …` 把组级选项放在子命令之后，parser 拒为 exit 2，改为
  `hosts --hosts-file … audit`。
- `docs/api/interfaces.md`：`hosts audit --family all` 中 `all` 不是合法取值（不写即全 5
  族）；`docs/api/README.md` 的 `margin` 命令示例缺必填位置参数。

### Fixed（v17 Phase 5 第 9 步 —— CLI 全量选项消费审计，F-28）

- **`tstdx stream --max-queue N` 此前是幻影开关**：parser 收下该值（默认 1024）却从不转给
  `QuoteStream.subscribe()`，于是库侧同名默认接管——与 F-27 同形（CLI 默认与库默认同为
  1024，只有主动调小背压上限以约束内存的用户会被静默忽略）。现补转发。
- 第 8 步的结构性守卫由"只查 `--host`/`--timeout`"推广为**与选项名无关**的全量消费审计：
  parser 声明的每个 dest 都必须出现在 handler 源码或四个点名的连接助手里。豁免面刻意收紧
  为逐个点名的助手（而非整个 `_common` 模块），否则任意一处 `args.x` 会给所有命令开绿灯。
- 单元测试中 stream 的假对象签名改为与真实 `subscribe` 一致（原假签名恰好缺 `max_queue`）：
  **测试替身比生产接口更窄**，正是这类幻影参数能长期存活的原因。

### Fixed（v17 Phase 5 第 10 步 —— CLI 最后两条旁路命令收口，F-29）

- **`tstdx changes` 与 `tstdx hot` 此前不走内核**：两个 handler 直接调用
  `WebQuoteSession.stock_changes()` / `WebQuoteSession.hot_rank()` 静态方法，绕开了
  CLI 承诺的唯一执行链。它们因此拿不到 `QueryResult` 信封与 `Provenance.direct` 指纹，
  不经过能力层的 `validate_call` 参数校验，也享受不到 F-27 刚接线的 `--host`/`--timeout`
  助手。两项能力本就有执行绑定（`web_session` backend），故本次**只删旁路、不改执行路径**：
  改为经 `Client` 调用同名 capability，离线对拍显示数据源收到的实参与返回行完全一致。
- **补两道守卫**（`tests/architecture/test_cli_connection_contract.py`）：一条逐命令断言确认
  两个命令以 capability 名调用 `Client`；另一条是**与命令名无关**的结构性扫描——用 AST 检查
  四个服务面（`tstdx/cli/`、`tstdx/integration/`）的全部 import，任何指向 `tstdx.web` 的边
  即为红。新增命令若复刻旁路，无需为它补测试就会被抓到。
- 两处单元测试的假 `WebQuoteSession` 换成 fake `Client`，与既有夹具同形。
- **变异验证**：把 `changes` 改回直接调用 ⇒ 两条守卫同时 exit 1（分别报出
  `import tstdx.web.session` 与 `recorder.kwargs is None`），还原后复绿。

### Fixed（v17 Phase 5 第 12 步 —— 包 docstring 纳入活文档门禁，F-31）

- **`Client(provider="tdx")` 这个写在包 docstring Quick start 里的例子照抄即 `TypeError`**：
  `Client(**runtime_kwargs)` 的键名以内核形参为准，正确写法是
  `Client(default_provider="tdx")`。**不加入参别名**（v17 clean-break 口径）。
- **`tstdx/__init__.py` 的「分层（自底向上）」图补齐到 24 个顶层包**（原先只有 14 个，
  缺的正是 v17 新增的服务面 `cli`/`integration` 与配置面 `config`/`catalog`），并逐条标注
  职责边界：服务面「只翻译不执行」、`catalog`「无执行」、`trade`「不接入内核」。
  README 结构树里 `cli/` 的「31 子命令，全部委托 Client」同步改为如实口径。
- **两条新门禁**（`tests/architecture/test_doc_code_consistency.py`）：分层图与磁盘顶层包
  **双向**对账；Quick start 的名字直调在禁网（`getaddrinfo` + `create_connection`）下真实
  求值，`TypeError`/`NameError`/`AttributeError`/`ImportError` 记为矛盾，因禁网或读文件
  而失败则说明入口与签名成立。此前文档门禁的三项检查只扫 markdown，包自己的门面无人对账。
- **变异验证**：删一行层 ⇒ 报缺失；插一行幽灵层 ⇒ 报幽灵；入参退回 `provider=` ⇒ 报
  `TypeError`，三条分别 RC=1。

### Fixed（v17 Phase 5 第 14 步 —— 事实文档的规模数字钉回真相源，F-34）

- **`docs/ARCHITECTURE.md` 里有两条"现状"是假的**：① 防回潮守卫条写
  `test_namespace_layout.py`「根级白名单 **11** 项」，实际是 **10**（`ROOT_WHITELIST` 与
  磁盘上的 `tstdx/*.py` 同数）；② F-15 门禁基线条仍在宣称「离线实测 76.14% … 低于 77
  阈值 ⇒ 门禁在本地为红」，而第 14 步同轮离线全量实测 **80.53%**、日志明写
  `Required test coverage of 77.0% reached`。**阈值 77 一次都没有下调**。
- **覆盖率陈述不再抄百分比**：F-15 那条改为判据口径（"离线全量已越过阈值 ⇒ 本地为绿"），
  逐轮实测数字统一记在 `docs/REFACTOR_PLAN_V17_CLOSURE.md` 的步骤日志里——一份声明"只描述
  代码现状"的文档抄一个每轮都会漂移的读数，就是在预约下一条失真；「CI 环境（ubuntu+py3.11）
  重钉仍需实测数字、本机 Windows 数字不作为依据」的口径原样保留。
- **数字门禁由"只读 README"扩展到事实文档全体**（`tests/architecture/test_doc_code_consistency.py`
  新增 9 项）：`test_fact_doc_numbers_match_their_truth_source` 把 README 与 ARCHITECTURE 的
  能力数、命令账本（`protocol.commands.COMMANDS`）、解析器数（`protocol.registry.PARSERS`）、
  配置段数（`dataclasses.fields(Config)`）、根级白名单（磁盘 `tstdx/*.py` 计数）逐个钉回运行期
  真相源；`test_documented_http_source_floor_still_holds` 把「45+ HTTP 源」按下界语义判定
  （实际 73 个 `*Source` 类：加源不必改文档，掉到宣称界下必须改）。
- **变异验证 10 条全部 RC=1 且各自指名**：172→167、85→84、61→62、5→6、10→11、删白名单宣称、
  README 85→86、README 61→60、下界 45→90、删下界宣称。

### Fixed（v17 Phase 5 第 15 步 —— 名单、代码注释数字与服务面口径一并钉回真相源，F-35）

- **`docs/api/interfaces.md` 承诺了一条不存在的纯度**：原文写"四个服务面全部委托同一个
  `Client`，不存在第二套执行路径"，而 CLI 另有 6 个传输/诊断命令（`probe`/`goods`/`f10`/
  `blocks`/`list`/`quotes-snapshot`）直连传输层客户端。现按事实改写为"数据命令全部委托
  `Client`，这 6 条属协议诊断面而非第二套能力执行路径"，并指名口径出处
  （`tstdx/cli/runtime_commands.py` 的模块 docstring）与既有守卫
  （`test_service_faces_never_import_the_web_layer`）；`docs/api/README.md` 的 CLI 行同步补
  "6 个传输/诊断命令除外"。**没有为凑口径给这 6 条硬造内核路径**——诊断命令的意义正是绕开
  能力语义看原始协议。
- **一个公开枚举的类数在 9 处写着错的值**：盘中异动 `CHANGE_TYPES` 有 **20** 项，
  `tests/web/test_hot_rank.py` 也早已断言 `len(et) == 20`，可 `docs/api/README.md` 与
  `tstdx/web/{fundflow,sources,_session_info}.py` 的 6 处 docstring/注释仍写"16 类"
  （枚举扩容时只动了测试那一侧）。数字全部改为 20，并新增
  `test_code_comments_about_change_types_match_the_enum` 扫 `tstdx/` 里所有含"异动"的行、
  把 `N 类` 钉回该字典——**生产代码的注释第一次进入事实门禁**。
- **数字表由 2 份文档铺满 6 份**：`_EXACT_CLAIMS` 10 行 → **22 行**，新增 `docs/api/README.md`
  （capability / Provider / 命令账本 / CLI 子命令 / MCP 工具 / WS 方法数 / Record 类数 /
  异动枚举数）、`docs/api/interfaces.md`（capability / HTTP 路由 / MCP 工具 / CLI 子命令 /
  Record 类数）、`docs/quickstart.md`、`docs/troubleshooting.md`、
  `docs/cookbook/06_custom_command.md` 的同一批事实；`_FLOOR_CLAIMS` 3 行按下界判定
  （`45+ HTTP 源` 实际 73、`60+ 契约` 实际 63，真相源取 `scripts/contract_audit.py` 的
  `_all_typed_queries()`）。
- **清单从"数个数"升级为"核名字"**：`test_documented_domain_record_names_match_the_module`
  比对 `tstdx.domain.records.__all__` 去掉 `Record` 词缀后的集合；
  `test_documented_ws_method_list_matches_the_dispatcher` 比对 `runtime_ws._dispatch` 的
  AST 提取结果（`method == "x"` 与 `method in {"a","b"}` 两种写法都认——只认前者会数出 9 个，
  把正确的文档判成错的）。文档写了分派器不认的方法名，用户照抄即 `-32601`。
- **刻意不钉的一项**：`docs/api/README.md` 的"11 领域基类"分母含糊（`domain` 下直接子类 10 个、
  去重后基类名 11 个），钉一个定义不清的事实只会制造下一条失真；已在计划里登记，待口径收敛。
- **变异验证 20 条全部 RC=1 且各自指名**：17 条文档侧（各数字 ±1、删清单宣称、清单里塞幽灵
  方法名 `runtime.ping` 与幽灵 Record 名 `Warrants`、契约下界 60→70），3 条枚举侧（两处生产
  注释 20→16、api/README 20→19）。**复测（同一轮日志）**：`tests/architecture/` 146 passed；
  离线全量 `-m "not network"` junit `3313 tests / 0 failures / 0 errors / 7 skipped`、RC=0；
  整仓 `--cov=tstdx` 80.54%（阈值 77 未下调）；`ruff check` + `format --check`（430 files）、
  `mypy`（CI 参数）、originality `--strict`（`Total: 189 Suspicious: 0`）、`spec_audit`、
  `golden_audit --gate --require-markets`、reachability `--strict`、docs links（82 文件）均 RC=0。

### Fixed

- **`tstdx.configure()` 此前调用即无效果**：它合并出 `Config` 后直接丢弃返回值，
  从不写回单例，`get_config()` 因此永远看不到覆盖。现改为
  `load_config(overrides=kwargs, set_global=True)` 并如实记录语义。
- **`[rate_limit]` 的所有取值曾被静默丢弃**：唯一的读者 `ConnectionPool.from_config`
  读的键名是 `rate_call_auction`/`rate_continuous`/…，而 `RateLimitConfig` 的字段名是
  `in_session`/`pre_post`/`closed` —— 两套名字从不重合，`getattr(..., 默认)` 于是把每个
  配置值都换成限流器自己的默认数字（其契约测试亦照幻影键名而写，故全绿从未暴露）。
  现字段名与 `SessionState` 一一对应，`SessionRateLimiter.from_config` 用直接属性访问，
  读不到的键名立即 `AttributeError`。
- **`WebQuoteClient` 不再吞掉配置错误**：其 `__init__` 曾以
  `try: … except Exception: pass` 包裹配置读取，配置解析失败即悄悄退回硬编码默认值；
  现直接 `get_config()`，fail-closed。
- 覆盖率门禁阈值收敛为单一事实源 `pyproject.toml [tool.coverage.report] fail_under`：
  删除 `Makefile` 与 `.github/workflows/ci.yml` 中重复的 `--cov-fail-under=77`
  （pytest-cov 读配置值，实测确认），并由两个门禁测试双向锁定不再出现副本。
  **阈值数值一次都没有下调**。

### Removed

- 配置面 12 段 → 5 段（`core` / `hosts` / `rate_limit` / `web` / `security`）。
  `cache`、`output`、`profile`、`sources`、`observability`、`compatibility`、`feedback`
  七个 dataclass 与其 `_SUBCONFIGS` 条目、`tstdx.config` 再导出一并物理删除
  （`tstdx/config/schema.py` −320/+45 行）。这些段在内核里零消费者，写了不改变任何
  行为，`[cache]` 更与"数据请求零缓存"直接冲突；现在写它们会命中
  `ValidationError: config 含未知配置段`，而不是被忽略。
- 删除 `ConnectionPool.from_config`、其硬化层 `tstdx/transport/_pool_factory_hardening.py`
  （76 行）与 2 个只测幻影键的契约测试；发布 wheel 冒烟改为断言唯一 seam
  `pool_settings_from_config` 存在且 `ConnectionPool.from_config` 不存在。
- 段内字段同步收缩：`HostsConfig` 留 `servers`/`slots_per_host`（删
  `auto_speedtest`/`ranking_file`/`max_hosts`/`speedtest_timeout`）、`WebConfig` 留
  `enabled_sources`/`timeout`/`max_retries`/`rate_limit`（删 `enabled`/`headers`/
  `normalize`）、`SecurityConfig` 只剩 `use_tls`（删 `credential_backend`/`user_agent`）。
  其中 `speedtest_timeout` 与 `web.*`/`security.*` 的被删字段在全仓**无任何读者**；
  `hosts` 的三个被删字段只有已消失的 `ConnectionPool.from_config` 读，而生产链从不调用它
  ⇒ 对真实链路同样是死键。`[security]` 现只剩一个真实开关。

### Fixed（v17 Phase 5 第 1 步 —— 类型门禁归零与死守卫）

- **修复 `Prober.only_offline_hours()` 的失效盘中守卫**：它比较
  `SessionState.IN_SESSION`，而 `SessionState` 从未定义该成员（真实成员为
  `call_auction/continuous/noon_break/closed`），任何未打桩的调用必抛
  `AttributeError`——即"盘中禁止探测主站"的保护一直是死代码，而所有测试都
  monkeypatch 掉这个方法，因此全绿从未暴露。现按 `state not in (CALL_AUCTION,
  CONTINUOUS)` 判定，并补 `tests/protocol/test_prober_offline_guard.py`
  逐时段回归（盘前/集合竞价/连续竞价/午休/盘后/周末 + `_guard_offline` 抛错路径）。
- `mypy tstdx/` 错误 **47 → 0**。除上述真实缺陷外，另有：`runtime/identity.py`
  以 `plan: object` 掩盖类型（改为 `QueryPlan`）、`golden_audit`/`speedtest`/
  `streaming.base`/`_pool_provenance_hardening` 的同名变量复用（局部重命名）、
  `config.schema` 的 `getattr` 循环补 `_Validatable` Protocol 锚点、
  `/v13/runtime/health` 不再伸手取 `executor._bindings` 私有属性而改读
  `DIRECT_BINDINGS` 事实源。11 个 `*_hardening.py` 的运行期打桩尾部逐行标
  `# type: ignore[method-assign|attr-defined]`（不整模块豁免；
  `--warn-unused-ignores` 保证标注失效即红）。
- 清理缓存时代残留：`runtime/identity.py` 的 `RuntimeCacheIdentity` 与
  `cache_identity_from_plan()` 生产代码零消费者，随 2 个只测自身的文件一并删除；
  `tstdx/sink/local_day.py`、`tstdx/domain/symbol.py`、`tstdx/web/efinance_*.py`、
  `tstdx/web/fin_report.py` 的 docstring 示例仍指向已物理删除的
  `tstdx.facade.UnifiedQuoteAPI`（照抄即 `ImportError`），改为 `Client` /
  `WebQuoteSession` 的真实签名。

### Added

> **口径标注（F-14）**：以下 P13/P14/P15 条目写于 `UnifiedQuoteAPI` 门面仍然存在
> 的时期，其中"门面暴露 X 个方法"是**当时的落地方式**，不是现行入口。该门面已随
> v16 Phase 2 物理删除，照抄条目里的 `UnifiedQuoteAPI.xxx()` 即 `ImportError`。
> 条目里的能力本身全部存活：它们注册在 `tstdx/catalog/capability.py`，现行唯一业务
> 入口是 `Client.call("<capability>", ...)` / `AsyncClient` 同名方法（运行期 172 个
> capability），底层数据源仍是条目点名的 `tstdx/web/*.py` 模块。门面→现行的对照见
> 上文 `### Changed（v17 Phase 4 —— 对外文档面对齐代码事实）` 的迁移表。条目里的
> `tstdx/web/_facade_mixin_*.py` 现已随 F-11 改名为 `tstdx/web/_session_*.py`。

- P14 数据源补全（ESG 评级 / 筹码分布）：新增 `tstdx/web/esg.py`（新浪 ESG 评级，
  覆盖 13 家机构聚合、季度历史、MSCI 全市场 5200+ 只、华证全市场 6300+ 只，
  含 E/S/G 三维度分项评分），新增 `tstdx/web/chip.py`（东财筹码分布，基于
  push2 资金流接口计算 accumulation_ratio 筹码集中度与 concentration_trend
  吸筹/派发趋势），新增 `tstdx/web/_facade_mixin_p1.py`（当时的挂载点）；这 5 个能力
  现经 catalog 可调用（入口 `Client.call(<capability>, ...)`）：`esg_rating` /
  `esg_history` / `esg_ratings_all` / `chip_distribution` / `chip_distributions`；新增
  `tests/web/test_p1_sources.py`（30 例全离线测试）。

- P13 数据源补全：新增 `tstdx/web/fin_report.py`（三大财务报表：资产负债表 / 利润表 /
  现金流量表，东财 datacenter-web `RPT_F10_FINANCE_GBALANCE/GINCOME/GCASHFLOW`，
  SECUCODE 过滤），新增 `tstdx/web/governance.py`（治理四报表：董监高持股
  `RPT_EXECUTIVE_HOLD_DETAILS` / 股东增减持 `RPT_SHARE_HOLDER_INCREASE` /
  公司概况 `RPT_F10_BASIC_ORGINFO` / 券商评级 `RPT_WEB_RESPREDICT`）；这 10 个能力
  现经 catalog 可调用（入口 `Client.call(<capability>, ...)`）：
  `balance_sheet` / `income_sheet` / `cash_flow` / `fin_report` /
  `executive_holds` / `shareholder_changes` / `org_profile` / `org_profiles` /
  `rating_forecast` / `rating_consensus`；新增
  `tests/web/test_fundamental_sources.py`（37 例全离线测试）。

- Web 源对标 `Micro-sheep/efinance` 全量补齐：新增 `tstdx/web/efinance_fund.py`
  （天天基金移动端 7 类基金扩展数据）、`tstdx/web/efinance_deriv.py`
  （东财 push2 期货/债券实时、快照、K 线、逐笔），这 21 个能力现经 catalog 可调用
  （入口 `Client.call(<capability>, ...)`）：
  `stock_base_info` / `stock_all_performance` / `stock_report_dates` / `ipo_review` /
  `fund_base_info` / `fund_manager` / `fund_holdings` / `fund_period_change` /
  `fund_asset_allocation` / `fund_industry_distribution` / `fund_public_dates` /
  `futures_base_info` / `futures_realtime` / `futures_kline` / `futures_trades` /
  `bond_realtime` / `bond_base_info` / `bond_kline` / `bond_history_bill` /
  `bond_today_bill` / `bond_trades`。
- 对标 `tiantianlaolao/astock-data-toolkit` 新增基本面衍生域：`tstdx/web/astock_toolkit.py`
  （东财 `RPT_SHAREBONUS_DET` / `RPT_VALUEASSESS_DET` / `RPT_CAPITAL_PARTICIPATION_DET` /
  `RPT_F10_FINANCE_MAIN`），暴露 `dividend_history` / `stock_valuation` /
  `holder_changes` / `financial_abstract` / `announcements`。
- 新增 `tstdx/web/news.py` 机构调研纪要源（东财数据中心 `RPT_ORG_SURVEY`），
  暴露 `research_visits`；补齐 niuniu 审计发现的资讯类硬缺口。
- 补齐 efinance 批量能力：`fund_base_info_multi`（`fund_base_info` 批量别名）与
  `bond_all_base_info`（`bond_base_info` 全市场别名）；并把 `_facade_mixin_info.py`
  已存在但当时未对外的 `free_holders` / `holder_num` 接入 catalog（`Client.call`）。
- 天天基金深度扩展（对标移动端全端点，补齐排行/快照/经理/公司/搜索 5 大子域）：
  新增共享工具 `tstdx/web/_mob_fund.py`（设备指纹 + 公共参数 + 多 host 容错 +
  `apply_fields` 字段归一化），新增三个源：`tstdx/web/fund_rank.py`
  （排行/实时快照替代已下线的 `fundgz`/净值/详情/评级/走势 7 方法）、
  `tstdx/web/fund_manager.py`（基金经理 JSON 版，含夏普/回撤/胜率/波动率打分卡，
  替代脆弱的 `fundf10` HTML 解析）、`tstdx/web/fund_company.py`
  （公司档案/旗下基金/规模变动/画像 + `fundts` 搜索），门面暴露
  `fund_rank` / `fund_snapshot` / `fund_nav_history_mob` / `fund_detail` /
  `fund_rating` / `fund_yield_curve` / `fund_rank_trend` / `fund_manager_list` /
  `fund_manager_profile` / `fund_manager_yield` / `fund_manager_eval` /
  `fund_manager_style` / `fund_companies` / `fund_company_archives` /
  `fund_company_funds` / `fund_company_scale` / `fund_company_base_info` /
  `fund_search` 共 18 个方法；新增 `docs/tiantian_fund_extensions.md`。
- 新增测试：`tests/web/test_efinance_fund.py`（7）、`tests/web/test_efinance_deriv.py`（9）、
  `tests/web/test_efinance_facade.py`（13）、`tests/web/test_astock_toolkit.py`（6）、
  `tests/web/test_news.py`（10）、`tests/web/test_fund_v2.py`（50），共 95 例全离线测试。
- 新增文档：`docs/efinance_parity_gap_analysis.md`、`docs/astock_toolkit_parity.md`、
  `docs/niuniu_coverage_audit.md`。

### Removed（v16 Phase 3A/3B —— 单一内核收口，clean-break 无别名）

执行面只剩一条链：`Client` / `AsyncClient` → `runtime.kernel.UnifiedRuntime`（零缓存）
→ `QueryPlanner.compile(QuerySpec)` → `DirectProviderExecutor`（`DIRECT_BINDINGS` 为唯一
三元组事实源）。下列 v14 信封层与其支撑 DAG **整体删除**，不提供向后兼容导入路径：

- 模块：`tstdx/runtime/{runtime,gateway,bootstrap,request,response,typed,stream,context}.py`、
  整个 `tstdx/execution/`（planner/graph/node/plan/semantic）、整个 `tstdx/provider/`
  （v14 router/base/tdx/web/local）、`tstdx/executor_bindings.py`、
  `tstdx/executor_binding_registry.py`、`tstdx/executor_registry.py`
- 符号：`Runtime`、`RuntimeGateway`、`QueryRequest`、`QueryResponse`、`create_runtime`、
  `request_from_typed`、`runtime_subscribe`、`StreamHandle`、`ExecutionPlanner`、
  `SemanticExecutionAdapter`、`ProviderRouter`、`resolve_executor`、`ExecutorBindingRegistry`
- 迁移表（旧 → 新）：
  `Runtime().execute(QueryRequest(cap, provider, options))` → `Client.execute(QuerySpec.build(cap, provider=..., options={"args": [...], "kwargs": {...}}))`；
  `RuntimeGateway().execute_typed(q)` / `request_from_typed(q)` → `Client.typed(q)`；
  `Runtime().subscribe(...)` → `Client.stream(...)`；`QueryResponse.records` → `QueryResult.data`
  （需要记录视图时用 `records_from_response(result)`）；
  `create_runtime(tdx=Fake())` 测试注入口 → `UnifiedRuntime(executor=FakeKernelExecutor())`
- 防回潮守卫：`tests/architecture/test_single_kernel_guards.py`（已删模块不可导入、
  符号不再出现、`tstdx.runtime.__all__` 仅导出内核、runtime 包不再 import 已删分层）

### Changed（v17 Phase 3C —— 根级命名空间归位）

`tstdx/` 根级平铺模块从 26 个收敛到 10 个（白名单仅留协议中立契约层：`__init__`、
`__main__`、`query`、`result`、`batch`、`typed_query`、`stream_contract`、`errors`、
`error_envelope`、`deprecation`）。纯移动、无合并、无兼容别名；导入方需按下表更新：

| 旧模块路径 | 新模块路径 |
| --- | --- |
| `tstdx.client_api` | `tstdx.client.api` |
| `tstdx.client_core` | `tstdx.client.core` |
| `tstdx.direct_provider` | `tstdx.runtime.executor` |
| `tstdx.orchestration` | `tstdx.runtime.orchestration` |
| `tstdx.runtime_audit` | `tstdx.runtime.audit` |
| `tstdx.runtime_identity` | `tstdx.runtime.identity` |
| `tstdx.runtime_provenance` | `tstdx.runtime.provenance` |
| `tstdx.capability_catalog` | `tstdx.catalog.capability` |
| `tstdx.capability_audit` | `tstdx.catalog.capability_audit` |
| `tstdx.provider_api` | `tstdx.catalog.provider_bindings` |
| `tstdx.provider_contract` | `tstdx.catalog.provider_contract` |
| `tstdx.provider_guard` | `tstdx.catalog.provider_guard` |
| `tstdx.provider_audit` | `tstdx.catalog.provider_audit` |

新包 `tstdx/catalog/` 承载"静态声明与一致性审计"（capability 目录与调用校验、Provider
channel→adapter 绑定表、Provider 隔离契约/守卫/审计），依赖方向单向 `runtime → catalog`。
顶层公开面 `tstdx.Client` / `tstdx.ProviderOrchestrator` / `tstdx.FallbackPolicy` 等**不变**
（`tstdx/__init__.py` 懒加载表已指向新路径）。守卫：`tests/architecture/test_namespace_layout.py`。

同批删除全仓零引用的孤儿模块 `tstdx/freshness.py`（427 行）、`tstdx/health.py`（256 行）与仅
被自身测试引用的 `tstdx/failure.py`（含 `tests/errors/test_failure_policy.py`）；一次性迁移脚本
`scripts/_v16_strip_use_cache.py` 一并移除。

### Changed（v16 Phase 3D —— typed 契约对齐内核真实签名）

`CapabilityQuery` 家族此前按信封时代的假想参数名建模，与 `DirectProviderExecutor`
转发的真实方法签名不符（调用即在规划期 fail-closed）。本轮以内核参数名为唯一事实源
重命名（clean-break，构造参数与校验消息同步）：

| Query | 旧字段 | 新字段 |
| --- | --- | --- |
| `F10Query` | `section` | `filename` |
| `WencaiQuery` | `question` | `query` |
| `ScreeningQuery` | `condition` | `query` |
| `SuggestQuery` | `keyword` | `key` |
| `IndexConstituentsQuery` | `index_code` | `index` |
| `BoardMemberQuery` | `board_id` | `node` |
| `FundHoldingsQuery` / `BondKlineQuery` / `BondTradesQuery` / `BondTodayBillQuery` / `BondHistoryBillQuery` | `symbol` | `code` |
| `FuturesKlineQuery` / `FuturesRealtimeQuery` / `FuturesTradesQuery` / `OptionSnapshotQuery` / `OptionsTrendsQuery` | `symbol` | `quote_id` |
| `BondBaseInfoQuery` / `BondRealtimeQuery` / `StockBaseInfoQuery` | `symbol` | `codes: tuple` |
| `AnnouncementsQuery` / `ConvertibleBondQuery` / `FundFlowQuery` / `GlobalQuotesQuery` | `symbol` | `symbols: tuple`（前者改继承 `BatchCapabilityQuery`） |
| `StockAllPerformanceQuery` | `symbol` | `report_date: str` |
| `IpoReviewQuery` / `FuturesBaseInfoQuery` / `OptionsListQuery` / `StockReportDatesQuery` | `symbol` | 无参（内核即无入参） |

契约→内核编译审计改由 `scripts/contract_audit.py::audit_typed_kernel_compilation` 承担
（`--ci` 门禁），`tests/v14/test_domain_typed_capabilities.py` 覆盖全部 50+ 领域契约的路由
与载荷断言。

### Changed（v17 Phase 4 —— 对外文档面对齐代码事实）

文档此前大量描述已删除的层，属于"文档说谎"级缺陷。本轮逐项核对并重写为当前事实：

| 位置 | 曾经的错误宣称 | 更正为 |
|---|---|---|
| `README.md` / `docs/quickstart.md` / `docs/FAQ.md` / `docs/cookbook/01` / `docs/migration/*` / `SECURITY.md` / `docs/api/interfaces.md` / `tstdx/observability/metrics.py` docstring | `from tstdx import TdxClient` | `from tstdx.client import TdxClient`（根面自 v15 起不再导出协议客户端）|
| `ops/smoke_30d.py` | 同上，**运行期 ImportError** | 改为 `tstdx.client` 导入 |
| `README.md` / `docs/api/*` | "32 CLI 子命令 / ~40 HTTP 接口 / 12 MCP 工具 / 167 capability" | 31 子命令 / 10 路由（`/v13/*`）/ 9 工具 / 172 capability / 11 Provider（逐项由门禁核对）|
| `docs/quickstart.md` §6、`docs/api/interfaces.md` §2–§3、`docs/cookbook/07` | `RuntimeGateway` / `create_runtime` / `QueryRequest` / `SemanticResultCache` / `execute_batch(requests)` | `Client` + `UnifiedRuntime` 单内核；批量为 `quotes_batch → BatchResult`，跨源为 `FallbackPolicy → OrchestratedResult.attempts` |
| `docs/api/interfaces.md` §3 服务面 | `integration.http_server`（42 端点）/ `ws_server` / `mcp_server`（12 工具）| `runtime_http.create_runtime_app`（10 路由）/ `runtime_ws_server.serve_runtime_ws`（10 方法）/ `integration.mcp.create_mcp_server`（9 工具）|
| `docs/cookbook/02_realtime_fallback.md` | `DataSourceRouter` 五级自动降级、`q.change_pct` 对象属性 | 默认永不换源 + 显式 `FallbackPolicy`；`QueryResult.data` 为 dict 列表 |
| `docs/cookbook/07_v14_runtime.md` | 整页 v14 信封运行时 | 重写并更名为 `07_single_kernel_queries.md`（溯源审计 / 批量三态 / 显式跨源 / typed / 流式 / `KernelExecutor` 假执行体）|
| `docs/api/README.md` | `tstdx.sources.router DataSourceRouter`、门面行 | `tstdx.providers` 注册表 + `catalog/*` + 统一内核层索引 |
| `README.md` / `docs/quickstart.md` / `docs/cookbook/01,05` / `docs/api/interfaces.md` §4 | 落地 URI `output://dataframe`、`parquet://x.parquet`、`duckdb://db?table=t`、`csv://` | 现行 sink 推断：`.csv/.parquet/.pq` 后缀 + `duckdb:<path>@<table>`，DataFrame 走 `to_dataframe()` 或 `fmt="dataframe"` |
| `docs/api/interfaces.md` §6 | `domain.records` 导出 `Bar/Quote/Level/FinanceInfo/...` | 9 类 `*Record`（`Bar/Quote/Level/CapitalChange` 属 `domain.models`）|
| `docs/api/interfaces.md` §6 | `from tstdx.domain.adjust import adjust_bars` | `AdjustEngine` / `to_adjusted` / `compute_factors` |
| `docs/cookbook/03_offline_vipdoc.md` | `read_lc1_file` / `read_lc5_file`（不存在）| `read_min_file(path, interval=1|5)` |
| `docs/tiantian_fund_extensions.md` / `docs/migration/easyquotation.md` | `from tstdx import tstdx  # UnifiedQuoteAPI`、`tstdx.facade.quote_api` | `Client.call(capability, **kwargs).data` |
| `docs/adr/README.md` ADR-004、`ADR-012/014/015` | 状态仍为 Accepted（描述 5 级降级 / 语义缓存 / 含缓存节点的执行链）| 标注 Superseded 并指向 ADR-013 与现行单内核链路（历史正文保留）|
| `docs/{quickstart,api/README,api/interfaces}.md` 交叉链接 | 指向已归档/相对路径错误的 `api/v14-runtime.md`、`api/README.md`、`ARCHITECTURE_AUDIT_v8.md` | 重指向归档位置或正确相对路径；`scripts/check_docs_links.py` 80 文件全绿 |

新增门禁 `tests/architecture/test_doc_code_consistency.py`（9 例）：活文档代码块里的
`from tstdx… import …` 必须可解析、事实型文档反引号里的 `tstdx.*` 路径必须可解析、
`tstdx.__all__` 与惰性导入表必须等集、README 宣称的 5 个数字必须等于运行期事实。

配套清理：

- `tstdx/__init__.py`：移除 7 个只存在于惰性表、既未列入 `__all__` 也无任何调用方的根级
  名字（`deprecated`、`DeprecationPolicy`、`FeedbackReporter`、`TelemetryCollector`、
  `UserStats`、`detect_encoding`、`decode_bytes`）。官方面回归单一事实源
  （`__all__` ⇔ `_LAZY`，46 项），需要时按真实模块路径导入。
- `tstdx/batch.py`：`BatchResult` 文档串不再引用已删除的 `planned_service` 契约。
- `tests/runtime/test_runtime_public_api_v12.py` → `test_root_public_surface.py`
  （文件名与被测契约一致；断言内容不变，注释去掉 `RuntimeGateway`）。
- 30 份被取代的历史方案文档归档至 `docs/archive/plans/`（含 `docs/api/v14-runtime.md`）。
- `CONTRIBUTING.md`：补 uv/Windows 本地门禁入口、"文档即门禁对象"、
  "契约先行必须自带实现"、"不做跨 PR 的先删后补"。

### Fixed

- 修复 `EastmoneyNoticeSource` / `EastmoneyResearchSource` 的 BASE 路径段丢失问题：
  `_get_json` 只拼接主机，`fetch_notices` / `fetch_reports` 此前把裸相对路径直接传给
  基类，导致真实请求打到 `https://reportapi.eastmoney.com?pageSize=...` 而 404。
  现改为传入 BASE 的路径段部分，主机只拼接一次。

## [1.0.0] - 2026-09-09

这是 tstdx 的首个正式稳定版，发布包同时提供 wheel 与源码包，支持 Python 3.10–3.13。

### Changed

- 明确 `ConnectionPool` / `AsyncConnectionPool` 的主站生命周期契约：后台测速只更新
  排名，真实请求健康只更新 live health；generation/lease 保护在飞请求，旧代完成结果
  不再污染新代主站状态。
- 统一同步/异步 half-open circuit 的单探测门禁、连接复用、空闲回收、心跳与
  `update_hosts` 热更新语义。

### Fixed

- 修复 `ConnectionPool` 主站生命周期竞态：后台测速、真实请求健康、half-open
  circuit、generation/lease、心跳、空闲回收与 `update_hosts` 连接复用现在互不
  覆盖；活动请求完成后，旧 generation 的结果不会污染新主站状态。
- 同步/异步连接池统一单探测 half-open 门禁，并区分测速 RTT 与 real-request
  health RTT；新增交错时序和并发回归测试。
- 修复 F10 客户端 0x06B9 文件分块响应解析：F10 facade 现在复用规范的文件下载解析器，
  空响应明确抛出 `DataError`。

### Release verification

- 全量 `pytest -q` 通过。
- `ruff check tstdx tests` 与 `python -m compileall -q tstdx tests` 通过。
- 发布脚本完成 wheel/sdist 构建、临时虚拟环境安装与 import 冒烟。
- 产物名称：`tstdx-1.0.0-py3-none-any.whl`、`tstdx-1.0.0.tar.gz`。

### Deprecation Timeline（P13-F：把 v1.4.0 Deprecated 章节的窗口期落到版本号）

- **v1.5.0（下一个 minor）**：`tstdx.native` 仍可用，行为不变；导入即发
  `UserWarning`（P14-D 强告警升级：默认可见，不再走 `DeprecationWarning`
  默认过滤规则）+ `logging.warning` 双通道；此版本为迁移**最后窗口**——
  所有下游需在 v1.5.0 之前切换到 `tstdx.codec` / `tstdx.io` 对应函数。
- **v1.6.0**：`tstdx.native` **正式删除**——模块文件移除，导入即
  `ImportError`（不再走纯 Python 回退）。同期移除 v1.4.0 Deprecated 章节
  中列出的兼容垫片；`pyproject.toml` 的 `[project.urls]` 若引用
  native 相关文档链接同步清理。见 P15-A 批次。

### Changed

- **P14 批次部分落地（2026-09-07）**：
  * **P14-D**：`tstdx/native.py` 弃用告警从 `DeprecationWarning` 升级为
    `UserWarning`（默认显示）+ `logging.warning` 双通道，v1.5.0 强告警。
    迁移指引文案显式指向 `decode_tdx_float / read_day_file /
    parse_kline_payload` 三个 Python 等价函数。
  * **P14-E**：`AsyncUnifiedQuoteAPI` docstring 显式声明「SourceUnavailable
    语义继承」——异步门面经 `asyncio.to_thread` 桥接同步门面实例，
    P13-A 的 CommandOffline/AllHostsUnreachable → SourceUnavailable 转换
    **自动继承**（异步层不重复实现以避免双写漂移）；
    `tests/facade/test_w11_w12_w13.py::TestP14EAsyncFacadeSourceUnavailable`
    3 个测试锁定契约（arun 透传 / aquery 折叠 / docstring 声明）。
  * **P14-F**：验证 `AsyncQuoteStream` 已在 M1 阶段完成 engine 组件接线
    （`ReconnectPolicy` + `BackpressureQueue` + 共享 `_resolve_payload`
    处理 GapUnfilledError + `Subscription._merger` DeltaMerger），
    `tests/streaming` 41 passed 全绿；文档状态更新为已闭环。
  * **P14-D2 覆盖率门禁对齐**：`pyproject.toml` `tool.coverage.report.
    fail_under` 从 75 提升到 77，与 CI `--cov-fail-under` 对齐，消除
    「门禁口径漂移」。同时新增 `[project.optional-dependencies].dev`
    extra（pytest / pytest-cov / pytest-asyncio / ruff / mypy / hatchling），
    开发者可 `pip install -e ".[dev]"` 获得与 CI 一致的本地体验。
  * **P14-A2 CLI hosts audit 子命令**：`tstdx hosts audit` 接入
    `scripts/audit_hosts.py`，用户无需调用脚本即可做 5 族巡检；
    参数与脚本一致（`--family / --timeout / --workers / --report /
    --ranking-file / --strict / --quiet / --no-save-ranking / --hosts-file`）。
  * **P14-A3 外部候选注入**：`audit_hosts.py` 新增
    `load_external_hosts(path)` 函数，支持三种格式：
    1. 纯文本（每行 `host:port [family] [name]`）；
    2. JSON list（`[{"host","port","family","name"}]`）；
    3. JSON dict（`{"quotation": [...], "ex_quotation": [...]}`）。
    family 支持官方常量与别名（quotation/standard/std/7709/extended/
    ex/7727/mac_quotation/mac/goods/f10）；错误格式抛 `ValueError`。
    社区贡献主站入口 P15-C 前置就绪。
  * `docs/POTENTIAL_ISSUES_AND_PLAN.md`：P14 批次状态表重构（🔧/⏳/✅
    三态），新增 P15 批次规划（Native 删除执行 / 性能基线 / 社区主站
    入口 / 覆盖率 77→80）。

- **P15 批次开始（2026-09-07）**：
  * **P15-D1 hosts_audit 单测补齐**：新增 `tests/test_hosts_audit.py`
    22 项测试，覆盖三条主链路——
    1. `TestCliHostsAudit`：CLI `hosts audit` 子命令注册与全量参数
       解析（`--family / --timeout / --host / --workers / --report /
       --ranking-file / --strict / --quiet / --no-save-ranking /
       --hosts-file`）；
    2. `TestLoadExternalHosts`：`load_external_hosts()` 三格式（纯文本 /
       JSON list / JSON dict）+ 家族别名（quotation/standard/std/7709/
       extended/ex/7727/mac_quotation/mac/goods/f10）归一 + 去重 +
       错误路径；
    3. `TestAuditFamilyWithExternalHosts`：`audit_family(additional_hosts=...)`
       去重注入 + baseline 回归（无 additional_hosts 时行为不变）。
    动态导入 `scripts/audit_hosts.py`（`sys.path.insert` + `importlib`），
    不依赖 CLI 主入口。

- **工程与文档（2026-09-07）**：
  * README.md 更新：版本 1.2.0 → 1.4.0，补 P13/P14/P15 特性说明
    （主站池治理 / 异步门面 SourceUnavailable 契约 / dev extra /
    CLI hosts audit 示例 / 巡检流程 / 文档导航新增
    POTENTIAL_ISSUES_AND_PLAN.md）。
  * `pyproject.toml` `[project.urls]` 修正到实际仓库
    （`coeasy/tstdx` → 原为 `tstdx/tstdx` 占位）——PyPI 元数据一致。
  * `.gitignore` 补充 `.workbuddy/` / `.test_tmp/` / `_cov.txt` /
    `base_orig_tmp.py`，杜绝本地会话产物与临时文件入库。
  * 项目首个 commit `f73ef61` 已推送至 `github.com:coeasy/tstdx` main
    分支（2061 files, 133,175 insertions）。

- **P12 数据源扩展与实测修复（真实环境验证）**：
  * **板块资金流排行**：新增 `sector_flow(board, sort, limit)`（session/facade
    双入口 + CLI `tstdx sector-flow [--board industry|concept|region]
    [--sort main_net]`）；实测行业/概念/地域真实取数通过（传媒主力净流入
    61.7 亿、SPD概念净占比 18.56%）。
  * **实测修复排行字段陷阱**：`EastmoneyRankSource.fetch_rows` 按资金流排序
    （main_net/main_ratio）时自动附带 f62/f184/f66/f72/f78/f84 字段——
    此前服务端排序正确但响应缺字段、行值静默为 None。
  * **TDX 扩展市场目录上提 facade**：`UnifiedQuoteAPI.ex_market_list() /
    ex_instruments(market)`（7727 品种目录）。**已知问题**：内置 7727
    扩展行情主站候选池 4 台全部超时（2026-09-06 实测），目录接口报
    `AllHostsUnreachable`；错误路径与提示正常，待更新主站池后可用。
  * **通用 datacenter 报表直查接口** `dc_query(report, symbol=..., filters=...)`
    + `dc_reports()` 白名单暴露（`WebQuoteSession`/`UnifiedQuoteAPI` 双入口）——
    白名单内任意报表直查（dividend 分红送配 `RPT_SHAREBONUS_DET` 已实测入列，
    2026-09-06 实测 performance 37 字段/holder_num/dividend 真实取数通过）；
    字段原样透传不做单位翻译（各报表语义差异大，避免误导），个股过滤键按报表
    自动映射（`_DC_SYMBOL_FILTER_KEYS`），无个股过滤键的报表（ipo）忽略 symbol。
  * **新增东财融资融券个股明细源** `web/adapters_margin.py`
    （datacenter `RPTA_WEB_RZRQ_GGMX`，2026-09-06 实测真实取数通过）——
    `EastmoneyMarginSource.fetch_margin`（DATE 倒序、days 截断、金额单位元、
    3/5/10 日差分字段入 `extra`）+ 源注册 `margin` + identity normalizer +
    `WebQuoteSession.margin` / `UnifiedQuoteAPI.margin` / CLI
    `tstdx margin <symbol> [--days N]` + 注册表一致性门禁扩项。
  * **实测发现并修复**：`fundgz.1234567.com.cn` 实时估值接口已下线
    （返回 404 HTML 页，官方 App 接口需设备签名鉴权）——
    `FundSource.fetch_estimate` 检测死接口后显式抛
    `SourceDeprecated`（保留 jsonp 解析层以备接口复活），
    `FUND` spec notes 同步；`fund_nav_history`/`fund_list` 实测仍可用。
  * **实测发现并修复**：`WebQuoteSession` 缺 context manager 协议
    （有 `close()` 无 `__enter__/__exit__`）——`_SessionBase` 补齐，
    `with web_session("sina") as s:` 可用。
  * **百度源扩展调研结论**（写入 `adapters_baidu.py` docstring）：指数行情
    不可行——`isIndex=true` 实测无效（000001 恒返回深市个股，前缀/1A0001/
    999999 均空结果，同码歧义无解）；smartbox 联想端点空响应；资金流
    `vapi/v1` 403。百度源现有能力（A 股 K线/分时/逐笔/五档）维持。
  * 新增 `tstdx/__main__.py`：`python -m tstdx` 与控制台脚本等价。
  * 新增测试：`tests/web/test_margin.py`（罐头解析/days 截断/空标的/注册表）
    + fund 死接口用例 + session CM 用例；真实取数复验通过。

- **收尾项（审计清单闭环）**：错误树梳理完成——44 类 E1-E9 九域体系经
  复核**无语义重叠对**（易混对按域区分并写入对照表）；新增
  [docs/errors.md](errors.md)（错误树速查 / RetryAdvice 字段契约与消费方 /
  扩展规则 / 上层边界约定），`errors.py` docstring 与 README 文档导航同步指向。

- **收尾项（审计清单闭环）**：错误树梳理完成——44 类 E1-E9 九域体系经
  复核**无语义重叠对**（易混对按域区分并写入对照表）；新增
  [docs/errors.md](errors.md)（错误树速查 / RetryAdvice 字段契约与消费方 /
  扩展规则 / 上层边界约定），`errors.py` docstring 与 README 文档导航同步指向。

- **v11 重构（P11 批次，全部落地）**：
  * **P11-0 lint 门禁清零**：ruff check 从 93 项清至 0（修复 P10-3 拆分遗留的
    `HttpResponse` F821 未定义名；10 处导入排序自动修复；3 个再导出门面模块
    （`cli/__init__`、`client/__init__`、`web/base`）以 per-file-ignores 声明
    F401 豁免；8 处真死导入逐一核对后删除；12 文件格式化对齐）。
  * **P11-1** `integration/mcp_server.py`（1062 行）拆为 `integration/mcp/`
    子包（`_common`/`_tools_impl`/`_tools_spec`/`_server`，依赖单向）+ 135 行
    兼容门面；23 工具 schema 逐字不变，logger 名保持，stdio 往返实测正常。
  * **P11-3** `protocol/parsers/std7709.py`（1216 行）拆为
    `_std7709_common`（常量/请求体构造）/`_std7709_quote`（0x044E/0x053E/0x0530）/
    `_std7709_bars`（0x052D/0x000F/0x0010）+ 107 行注册与再导出门面；
    6 个 L1 解析器注册路径与全部导入不变，golden/对抗门禁守护。
  * **P11-2 决议：transport 不拆**。`pool.py`（Slot/PoolStats + 单一
    ConnectionPool 类）与 `async_.py`（异步镜像域）各自职责单一、类内强耦合，
    无 web/base.py 式的多职责混杂，按 v10 方案「不强拆」条款保留原样。
  * 千行大文件降至 2 个（transport/async_ 831、transport/pool 800，均为
    单一职责域，保留）。

- **v10 重构（REFACTOR_PLAN_v10，全部落地）**：
  * **P10-1** 按 [ADR-007-010](adr/ADR-007-010.md) 删除 `CredentialStore`
    （三级凭据存储，526 行，全库零调用方）：模块/导出/测试/docs/白名单同步清理；
    `tstdx.security` 包保留并注明重新设计条件。
  * **P10-2** facade 路由壳拆分：W11/W12 路由选择与熔断基础设施收口至
    `facade/routing.py`（`RouteSelector`），`api.py` 1297→1129 行；
    实例状态名（`_route_fail_counts`/`_route_cooldown_until`）与全部公开面不变。
  * **P10-3** `web/base.py`（1229 行）四分：`_base_core`（生命周期+模板方法）/
    `_base_retry`（重试退避+失败双桶）/`_base_http`（HTTP 客户端+限流桶）/
    `_base_em`（`_EastmoneyJson` 主机池）；`web/base.py` 保留为组合 re-export
    门面，导入路径全兼容（`tests/seams` W5/W10 契约测试同步指向新模块）。
  * **P10-4** 文档清理：`sink/` docstring 改引 `tstdx.output`；README 特性表
    补 streaming 定位（ADR-011）。
  * **P10-5** route_parity 偶发失败定性：v9 期间代理并发改写 `web/__init__.py`
    时测试运行于半写状态所致（环境性，非测试缺陷）；复跑与终验均未复现，
    测试 fixture 已有隔离，不修改。
  * 千行大文件从 5 个降为 3 个（std7709 解析器/transport/mcp 明确列为 v11 候选）。

- **v9 重构（REFACTOR_PLAN_v9，决策已确认，全部落地）**：
  * **Q1 路由链合并**：`UnifiedQuoteAPI` 的 quotes/bars 各路由取数委托
    `DataSourceRouter` 单源调用（`order` 覆盖 + `default_empty_ok`/`adjust`/
    `start` 增量参数，向后兼容），消除双路由实现；W11 熔断壳/W12/W13 语义
    逐字保留；对拍基线 `tests/facade/test_route_parity.py`（11 例）+
    [ADR-012](adr/ADR-012-路由链合并口径对拍.md)。
  * **Q2 client 共享骨架**：33 组 sync/async 镜像方法一次性迁入
    `client/_mixin.py`（同步生成器 trampoline 模板，任意挂起点逐字等价）；
    sync.py 1035→643 行、async_.py 681→434 行，方法体去重约 -1100 行；
    奇偶门禁/monkeypatch 语义不变。
  * **Q4-1** web Source 层收尾：`web/_paginate.py` 共享分页拉取器
    （KlineSource/MinuteKlineSource 复用）；公告/研报源归入 `_EastmoneyJson`
    （主机池 failover 复用）。
  * **Q4-2** `tstdx/web/__init__` 惰性导入（PEP 562，55 符号 + `_ADAPTERS`
    惰性注册表）：首载子模块 24→3（-87.5%）。
  * **Q4-4** `tstdx/sinks` → `tstdx/output`（消除与 vipdoc 写回包 `tstdx.sink`
    的混淆）；旧名 shim 兼容一版（DeprecationWarning，v10 删除），16 处引用迁移。
  * **Q3/Q4-3** 异步门面紧凑设计文档化并冻结契约测试；[ADR-011](adr/ADR-011-streaming-native-处置决议.md)
    明确 streaming engine/push 与 native 定位（engine 实为 QuoteStream 内核，
    白名单注释修正）。
  * 全程行为与公开 API 兼容；全量 pytest / 对抗矩阵 / golden 门禁 / 严格
    可达性门禁通过。

### Added

- **v8 内部重构（REFACTOR_PLAN_v8，行为与公开 API 零变化）**：
  * 删除已废弃的 `tstdx.i18n` 兼容 shim（v7 起更名 `tstdx.charset` 并告警一版）；
    相关测试/文档/`_reach_allow.txt` 同步迁移至 `tstdx.charset`。
  * `facade/api.py`：新增 `_with` 资源作用域帮助方法，收口 21 处
    「构造客户端/Source → 调用 → close」薄委托模板（-85 行重复）。
  * `tstdx/cli.py`（1210 行）拆分为 `tstdx/cli/` 包：`_common`（表格输出/公共
    参数）+ `cmds_market` / `cmds_web` / `cmds_hosts`（按域命令）+ `parser`
    （argparse 装配）；`tstdx.cli:main` 控制台入口与 `from tstdx.cli import
    main/build_parser/_cmd_*` 导入路径全部不变。
  * `tstdx/client.py`（1715 行）拆分为 `tstdx/client/` 包：`sync.py`（TdxClient
    + 4 同步子类）/ `async_.py`（Async 镜像）/ `factory.py`（get_client）；
    37 个模块级符号逐一 re-export 对齐，`dispatch` 经包属性延迟解析保持
    monkeypatch 语义等价。
  * `tstdx/web/facade.py`（1336 行）拆为 `facade.py`（135 行）+ 3 个域 Mixin
    （`_facade_mixin_market/info/baidu`）；61 方法 AST 级 diff 为空，导入面不变。
  * `tstdx/trade/*`（交易协议模拟器）登记可达性白名单并在 README 标注定位
    （独立可选，不连真实券商）；`audit_reachability.py --strict` 恢复通过。
  * 新增 [docs/ARCHITECTURE_AUDIT_v8.md](ARCHITECTURE_AUDIT_v8.md)：功能完整性/
    链路贯通/不合理项审计结论与 v9 路线。
  * README 代码地图补 `sink/`（vipdoc `.day` 写回）与 `charset/` 条目，澄清
    `sink`/`sinks` 为职责不同的两个包。

### Added

- **P0-1 基金净值（G1，东财接口）**：新增 `tstdx/web/adapters_fund.py`
  `FundSource`（数据型源）——`fetch_nav_history`（api.fund.eastmoney.com/f10/lsjz，
  空净值→None 容错）、`fetch_estimate`（fundgz jsonp 解析）、`fetch_fund_list`
  （fundcode_search.js 全量 JS 数组解析，约 1.2 万条）；源注册 `fund`
  capabilities `fund_nav_history / fund_estimate / fund_list`（不参与行情降级）；
  `WebQuoteSession.fund_*` 便捷方法 + `UnifiedQuoteAPI.fund_nav_history /
  fund_estimate / fund_list` 门面 + CLI ``tstdx fund nav|estimate|list`` 子命令
  （`--page-size/--page-index` 翻页、`--json`）；补齐三方一致性门禁（`_ADAPTERS`
  注册 + `FundNormalizer` identity 显式登记 + `CAPABILITY_FACADE` 映射）；
  新增 `tests/web/test_fund.py` 9 例 + `tests/facade/test_fund_api.py` 5 例 +
  `tests/unit/test_cli_semantics.py` `TestP01FundSubcommand` 6 例（离线 mock）。

- **P0-2 指数成分股（G2，东财数据中心）**：新增 `tstdx/web/adapters_index.py`
  `EastmoneyIndexConstituentsSource`（继承 `EastmoneyDataCenterSource`）——
  `fetch_constituents` 经 datacenter `RPT_INDEX_TS_COMPONENT` 报表拉取，`TYPE`
  指数族过滤（沪深300/上证50/中证500/科创50/中证A50/中证A500/中证1000/深证50/
  深证100/北证50/上证180/中证A100/中证2000 共 13 族，2026-09 与中证官网 XLS
  交叉验证 5 族 jaccard=1.0）、单页 500、中证1000/中证2000 等大指数自动分页拉全量，
  `weight` 仅部分指数族提供；源注册 `index_cons` capability `index_constituents`
  （数据型源，不参与行情降级）；`WebQuoteSession.index_constituents` 便捷方法 +
  `UnifiedQuoteAPI.index_constituents` 门面 + CLI ``tstdx index constituents <code>``
  子命令（`--json`）；补齐三方一致性门禁（`_ADAPTERS` 注册 +
  `IndexConstituentsNormalizer` identity 显式登记 + `CAPABILITY_FACADE` 映射）；
  新增 `tests/web/test_index.py` 14 例 + `tests/facade/test_index_api.py` 2 例 +
  `tests/unit/test_cli_semantics.py` `TestP02IndexSubcommand` 3 例（离线 mock）。

- **B0 百度财经源收口**：`UnifiedQuoteAPI` 新增 `baidu_kline` / `baidu_minute`
  / `baidu_ticks` / `baidu_quote` 门面（转发 `WebQuoteSession` 便捷方法，
  try/finally 保证 close）；CLI 新增 ``tstdx baidu <symbol> [--kind kline|
  minute|ticks|quote]`` 子命令（`--period/--count/--end-time` K 线分页游标、
  `--limit` 逐笔、`--json`）；新增 `tests/facade/test_baidu_api.py` 6 例 +
  `tests/unit/test_cli_semantics.py` `TestB0BaiduSubcommand` 6 例（离线 mock）。

- **P2-1 TDX 交易协议探测**：新增可选模块 `tstdx/trade/`——`constants.py`
  （命令号/查询类别/价格类型/委托状态常量，生态公开约定，inferred 标注）+
  `errors.py`（`TradeError`/`TradeNotLoggedIn`/`TradeRejected`/
  `TradingUnavailable` 红线异常）+ `security.py`（口令 XOR 可逆混淆占位）+
  `frames.py`（8 字节帧头自洽编解码 + 登录/心跳/查询/委托/撤单 body）；
  `simulator.py` 纯内存模拟券商（资金/持仓/委托生命周期/冻结解冻）+
  `SimTransport` 协议回路；`client.py` `TradeClient`（登录/查询/委托/撤单/
  心跳 + `buy`/`sell` 助手）与 `SocketTransport`（**红线**：连接即抛
  `TradingUnavailable`，绝不连真实券商通道）；新增 `PROTOCOL_SPEC/TRADE/`
  6 份 draft spec（`0x0001_LOGIN` / `0x0002_HEARTBEAT` / `0x0003_LOGOUT` /
  `0x0100_QUERY` / `0x1000_SEND_ORDER` / `0x1001_CANCEL_ORDER`）；
  新增 `tests/trade/` 56 例（帧/口令/模拟器/客户端/红线）。

- **P1-2 运行时 bestip 测速热更新**：`ConnectionPool.update_hosts`
  （按新序重建主站池、复用既有连接、丢弃被移除主站，不重启不中断）+
  `AsyncConnectionPool.update_hosts` 异步镜像；`TdxClient.bestip()`
  （并行探测候选主站 RTT → 排序 → 热更新主站池）+ `AsyncTdxClient.bestip()`
  与 `open(bestip=True)` 触发；新增 `tests/client/test_bestip.py`。

- **P1-3 本地日线增量落盘**：`tstdx/sink/local_day.py` `LocalDaySink`——
  断点续传式把网络日 K 线增量写回 vipdoc `.day`（读末日期 → 向后回退
  多窗口拉取 → 已存在日期跳过，幂等；prev_close 链与 reader 语义一致；
  记录编码是 `DayBarReader` 解码的精确逆变换，写入后可直接回读）；
  `UnifiedQuoteAPI.sync_daily(symbols, root)` 批量门面；模块级
  `sync_daily` 便捷入口；新增 `tests/sink/test_local_day.py`。

- **N1 F10 栏目目录客户端方法**：`F10Client.catalog(symbol)`（0x0001，同步）+
  `AsyncF10Client.catalog`（异步镜像）——解析 F10 栏目目录输出
  `[{title, filename}]`；补齐 docstring 漂移（P1）；新增
  `tests/client/test_f10_client.py`（fake pool 注入离线用例）与
  `PROTOCOL_SPEC/F10/0x0001_F10_CATALOG.yaml`（spec\_audit 94.6%→94.7%）。

- **N1 F10 catalog 服务面接线**：`UnifiedQuoteAPI.f10_catalog`（tdx 路由校验）、
  HTTP `/f10/{symbol}/catalog`（fundamental 组 7→8，端点 42→43）、MCP
  `get_f10_catalog` 工具（12→13）、CLI `tstdx f10 <symbol> [--file <name>]`
  （缺省列栏目目录）；各层均带离线用例（facade 路由校验 / HTTP fake client /
  MCP stub / CLI 语义 / WS）。

- **P9 get\_client 显式拒绝**：`get_client` 对未知 `kind` 显式抛
  `ValueError`（不再静默回退 `TdxClient`），对齐 W12 显式拒绝风格。

- **N2 WS 方法面扩展（9→19）**：`JsonRpcHandler` 新增 `capital_changes`、
  `adjusted_bars`、`block_quotes`、`all_market`、`board_list`、`board_members`、
  `security_list`、`minute_klines`、`f10_download`、`f10_catalog` 共 10 个
  JSON-RPC 方法（跨源方法走 `UnifiedQuoteAPI`，其余复用客户端），同一错误映射；
  `tests/integration/test_ws_server_f4.py` 用 FakeClient/FakeFacade 覆盖。

- **M5 urllib 连接复用**：`web/base.py` 新增 `StdlibPooledClient`——基于
  `http.client.HTTPConnection` 的零依赖 keep-alive 连接池（空闲超时 + 全池
  懒回收 + 线程安全），未装 httpx 时自动提升复用；新增
  `tests/web/test_pooled_client.py`。

- **M7 响应体解码零拷贝**：`HttpResponse.json()` 直接 `json.loads(bytes)`
  跳过中间 str，大响应（全市场 / K 线）内存与耗时双降。

- **U5 easyquotation 腾讯全市场**：`TencentSource.fetch_all()` 全程单源
  （`getBoardRankList` 枚举代码 + `qt.gtimg.cn` 批量行情，单批失败不拖垮
  全市场）；`WebQuoteSession.all_market()` 与 `UnifiedQuoteAPI.all_market()`
  均支持 `"tencent"` 源（`node="hs_a"/"cyb"`）。

- **F1 除权除息数据链路**：`tstdx/domain/finance.py`（gpcw 语义字段序 +
  0x0010 语义映射 + `map_finance_values` + `to_capital_changes` 共享转换器）；
  `FinanceReader.read_indicators()` gpcw 语义化解析；`finance_info()` 0x0010
  带字段名输出；`UnifiedQuoteAPI.adjusted_bars()` 复权生产入口（原始 K 线 +
  除权事件 → `AdjustEngine` 前/后/定点复权）。

- **Q4 内存基准 + Q2 基准入 CI**：`benches/bench_memory.py` tracemalloc 三类
  场景峰值基线（全市场 5400 只 / 1000×320 K 线 / vipdoc 8 万条扫描），
  `--synthetic` 离线可跑 + `--live` 真实源 + `--json` 归档；CI 新增
  `benchmark-smoke` job；新增 `tests/unit/test_bench_memory.py`。

- **F3 WS 订阅协议文档化 + 断线补洞**：`ws_server.py` 文档化订阅/推送协议
  （`quote_update` 周期推送 + `quote_snapshot` 断线补洞）；`JsonRpcHandler.
  on_message()` 上报新增订阅、`push_snapshot()` 订阅/重连即推全量快照；
  `serve_ws` 传输层在新增订阅时立即推送快照，客户端无需等下一轮周期推送
  即可对齐断线窗口内漏推区间。

- **F2 板块数据在线化**：`MacClient.block_list`（0x120F 板块列表
  `<H count>+<8s name><H id>`）/`block_members`（0x1210 成分股 `<6s code>`）
  同步+异步方法；板块行情解析器 0x2000（`pct_change`/`price`）。

- **F4 7727 扩展市场覆盖**：`ExMarketClient.ex_market_count`（0x0100）/
  `ex_market_list`（0x0101）/`ex_instrument_count`（0x0102）/
  `ex_instrument_list`（0x0103）与 `GoodsClient.goods_count`（0x0200）/
  `goods_list`（0x0201）同步+异步方法。

- **7727/GOODS/MAC 协议 golden structure 文档**：`PROTOCOL_SPEC/` 新增
  7727（15）/GOODS（11）/MAC（3）共 29 份 `status: inferred` spec YAML；
  `tests/protocol/test_7727_goods_mac_coverage.py` 用合成载荷锁定
  EXTENDED 0x0100–0x010E、GOODS 0x0200–0x020A、MAC 板块三族解析器布局
  （P1c 0x0104 per-record absolute vs 0x0202/0x052D running base 显式区分，
  GBK ≤8B 槽位防字段漂移，28B 记录守卫）；`spec_audit` 覆盖率 75%→94.6%。

## \[1.4.0] - 2026-09-05

M1/M1b 架构决断落地（用户拍板）+ 低级批次 14 项具名全清 + L5 冒烟扩展。

### Added

- **M1 接线落地（用户决断）**：`QuoteStream` / `AsyncQuoteStream` 内核改由
  `streaming.engine` 组件构成——DeltaMerger（增量合并，替换自维护 `_diff`）、
  BackpressureQueue（背压，`max_queue` 参数自弃用状态转真实接线：溢出丢
  最旧 + `BackpressureOverflow` 派发；`0` 关闭直发）、ReconnectPolicy
  （异步版退避对齐同步版：指数 + 抖动 + 上限）。

- **E6 错误分类兑现真实抛点**：不可解析订阅符号 → `SubscriptionError`；
  标的连续 3 轮未见于响应 → `GapUnfilledError`（可观测信号，不自动补数）；
  背压溢出 → `BackpressureOverflow`。errors.py 处置注释同步更新。

### Deprecated

- **M1b 废弃决断（用户拍板）**：`tstdx.native` 自 v1.4.0 弃用（§29
  DeprecationPolicy，2 个 minor 窗口后 v1.6.0 删除）——Rust 扩展源码已移除、
  模块恒为纯 Python 回退透传无加速实质；导入即发 `DeprecationWarning`，
  窗口期行为不变。迁移：直接使用 `tstdx.codec` / `tstdx.io` 对应函数。

### Fixed（低级 14 项具名摘要）

- `commands.py` 族账本注释漂移（36→39）+ F10 族端口显式声明；
  `_looks_like_uint16_date` 裁决保留标注。

- `framing.py` zip==unzip 声明未压缩但长度矛盾时的 zlib 包装兜底。

- `mac.py` MacNews 正文截断 warn\_ctx 留痕。

- `web/normalize.py` KlineNormalizer 对齐解析层内联缩放为 identity
  （防 A 股量 ×10000 双重缩放）；测试同步更新。

- `security/credentials.py` list\_keys 遮蔽警示 + get\_source 诊断路径。

- `domain/symbol.py` 白名单移除 000010/000016（深市同名个股冲突，
  裸写归深市个股，指数需显式前缀）。

- `feedback/reporter.py` 移除股票代码脱敏步骤（公开市场标识非 PII，
  6 步流水线重编号）。

- `sinks/__init__.py` to\_csv 原子写（mkstemp + os.replace）。

- `integration/ws_server.py` 模板层唯一 TdxClient（防每连接独立连接池）；
  `http_server.py` 逗号列表 100 上限（quotes/snapshot/auction\_snapshot）。

- `tests/unit/test_golden.py` 回放 ids 含 case 层（market/标的维度可见）。

## \[1.3.0] - 2026-09-04

第三方深度复审修复批次（6 项高危 + 33 项中级全清 + mypy 清零 + socket 冒烟），
详见 `docs/POTENTIAL_ISSUES_AND_PLAN.md` §2.4 逐项状态表。

### Fixed（高危 6 项）

- **web/base.py + sources/**__init__.py（W#1/W#2）：`bars()` 本地路由 4 分支
  错位（date 列同时映射 open/close，行情口径全错）；`sources` 降级链
  东财→web 切换时 `adjust` 被静默丢弃（复权请求偷换为不复权）。

- **streaming/engine.py（T#1）**：`BackpressureQueue.put` 在锁内调用 on\_drop
  回调——订阅者异常/重入死锁。

- **transport/async\_.py（T#2）**：`iter_frames` 与请求并发时帧交叉（读循环
  未持连接锁，多路复用下响应帧串流）；同步池同型修复；尾部残流显式告警。

- **integration/http\_server.py（S#1）**：8 个端点签名与 `TdxClient` 方法断裂
  （`/file_download`/`/download` 双端点直崩，goods/ex/mac 系 market 参数
  未拼符号），fakes 镜像旧签名掩盖——契约测试用 `inspect.signature` 锁定。

- **protocol/generic.py + registry.py（P#1/P#8）**：Sniffer 归档零调用点
  （三处文档承诺落空）接线；秒级时间戳同秒覆盖 + DRAFT 并发竞写修复
  （毫秒 + 序号探测 + 模块锁 + `open("x")` 原子创建）。

### Fixed（中级 27 项摘要）

- **transport**：`request_multi` 部分数据 `payload=b""` 清空矛盾（保留前缀
  有效数据）；async 限流 3 缺口收口 `_acquire_rate()`；`PushChannel.read`
  断线升级 warning + `last_error` 属性，数据损坏不再伪装「无帧」。

- **streaming**：`QuoteChannel.tick` 裸码/带前缀双向索引；`poll_delay` 兑现
  ReconnectPolicy 指数退避（旧恒 0）；`QuoteStream.stop` 关 client + 防
  start 孤儿双跑。

- **client**：`quotes_snapshot` L3 透传行不再强转 Quote（同步/异步均回退
  逐只 0x0530）；`min_confidence` 兑现显式拒绝语义。

- **web**：sina 源显式拒复权 + 门面复权请求自动路由东财（旧静默给原始价）；
  东财单候选空结果换 host 复核；市值单位跨源统一为元（腾讯亿/新浪万元换算）；
  `SuggestSource` URL 编码；限流桶速率显式更新（不再首建者胜）；12 处裸
  get/post 补 `_check_deprecated` 前置；fundflow 未知周期显式报错。

- **codec/protocol**：`build_request` pkg\_len uint16 溢出抛 `FramingError`；
  `decode_response_body` 统一转 `DecompressError`（golden 回放不再裸抛
  zlib.error）；`parse_f10_text` 中间 NUL 不再截断正文；
  0x0010/0x053E tier 账实对齐（不再缺省自报 L1 满置信）；0x06B9 死分支
  清除；4 处 price\_scale 死语句清除；`ctx["_meta"]` 回传通道修复。

- **observability**：`Registry.snapshot` Histogram 按序列输出（statsd 消费端
  同步适配）；statsd 首推累计口径经裁决文档化保留。

- **integration**：4MB 护栏补 chunked 旁路（无 Content-Length + chunked → 413）；
  TaskStore 取消后完成不回写；`GET /tasks` 列表只回摘要。

- **config**：`merge_config` 改深合并（与 `with_overrides` 同语义，不再吞键）。

- **profile**：hint 先验 +0.1 加权不得救活数据证据不足的候选（原始 conf
  < 阈值 60% 时加权不生效）。

- **facade**：`ex_quotes` 多标的逐只循环（旧把 Sequence 透传单只必崩）；
  `EastmoneyHistoryKlineSource.BASE` 补齐（旧 build\_url AttributeError）。

### Changed

- **mypy 基线清零（L1b）**：95 错误 → 0（100 源文件）。根因修复：`bars`/
  `quotes`/`quotes_concurrent`/`get_client` @overload Literal 收窄、`Self`
  返回类型、Registry 泛型 register；动态元编程 5 处定向 ignore。

- **CI 递减门禁（L4）**：type-check job 加 `--warn-unused-ignores`，顺带清掉
  12 处失效 ignore。

- **ws\_server** `quotes_snapshot` 语义随 client 同步。

### Added

- **L5 socket 级冒烟**（`tests/integration/test_l5_smoke.py`）：HTTP 真实
  loopback（uvicorn 真端口）、WS 双客户端并发订阅推送、MCP stdio 行协议往返。

- 高危修复回归：`tests/protocol/test_sniffer_loop.py`（7）、
  `tests/streaming/test_engine_backpressure.py`、`tests/sources/`
  adjust 一致性、`tests/unit/test_http_server.py` 契约锁定、
  `tests/facade/test_bars_local_route.py`。

## \[1.2.0] - 2026-09-03

工业审计（`docs/INDUSTRIAL_OPTIMIZATION_PLAN.md` v2）全量修复批次 F0-F5 + G/H 长尾。

### Fixed

- **facade/async\_api.py（F0-1，实测级）**：`AsyncUnifiedQuoteAPI` 的 10 个桥接方法
  把同步门面的**实例方法当静态函数**经 `asyncio.to_thread` 调度，真实调用必
  `TypeError`（测试打桩 `staticmethod` 掩盖了缺陷）。重构为实例持有同步门面
  （`__init__(*, sync_api=None, **sync_kwargs)`，惰性创建），全部桥接走绑定方法；
  新增 `close()` 与 `with`/`async with` 上下文管理；`arun` 拒绝下划线方法名。
  回归纪律入测试：只允许打桩传输边界（假 `TdxClient`），禁止打桩被测方法本身
  （`tests/unit/test_fix_f0.py`，13 用例）。

- **web/base.py（F0-2，实测级）**：`HttpxClient` 补齐 `post()`（签名对齐
  `UrllibClient.post`，httpx 异常统一包装 `WebSourceError`）——按推荐
  `tstdx[web]` 安装（httpx 可用）时人气榜/CLI/MCP 全链 `NotImplementedError`
  必崩的断头路接通。

- **config/loader.py（F0-3，实测级）**：① `TSTDX_RATE_LIMIT_*` 段名按
  `Config._SUBCONFIGS` 最长前缀切分（唯一带下划线的段名此前永远无法表达）；
  ② bool 词表收紧为 `true/yes/on` 与 `false/no/off`；③ `config_from_env`
  未知段 `RuntimeWarning` + 跳过（此前任意未知 `TSTDX_*` 直接崩启动）。

- **security/credentials.py（§2-26）**：损坏凭据文件不再被 set() 静默以空字典
  覆盖重建——先改名 `credentials.enc.corrupt-<UTC>` 隔离原件备抢救，隔离失败
  放弃写；`_load_file` docstring 与实现契约对齐（损坏抛 `ConfigError`，
  降级决策权在各调用点）。回归 `tests/security/`（10 用例）。

### Changed（含契约变更）

- **config env 词义**：`TSTDX_*` 值为 `"1"/"0"` 时解析为 **int**（此前被 bool
  抢跑，`TSTDX_CORE_MAX_RETRIES=1` 报「必须是数值，收到 bool」）。确属缺陷修正；
  需要 bool 的用 `true/false`。

- **CredentialStore.set 写策略**：keyring 可用且写入成功时**不再**重复落 XOR
  混淆文件（弱文件恒持有全部密钥等价物与「优先 keyring」叙事冲突）。
  keyring 失败/缺失仍回退文件。读路径三级回退语义不变。

- **AsyncUnifiedQuoteAPI 构造**：新增 `sync_api` 注入参数与 `close()`/上下文
  管理；类级 `_SYNC` 共享实例移除（实例隔离）。

### Removed

- `tstdx/_async_bridge.py`（390 行）：`run()` 对常驻 loop 再 `run_until_complete`
  必抛 `RuntimeError` 且全库零消费——已坏孤儿，删除（§4 决议）。

- `tstdx/protocol/requests.py`：与 `client.py` 内联构包双源漂移（80 只上限等），
  全库含测试零 import——删除（§4 决议）。

- `tstdx/protocol/parsers/_generated.py`：注册链断裂幻影（`parsers/__init__`
  不导入、`tier="TIER_L1"` 字符串 bug）——删除；codegen 输出路径另行调整。

### Added

- **F5 审计门禁固化**：
  `tests/adversarial/test_full_matrix.py`——对抗 payload 全矩阵永久回归
  （9 畸形字节 × 全账本，strict-L1 原生异常逃逸=0 / 降级路径逃逸=0 /
  单条解析 <1s），含 canary 自证「门禁有牙齿」；
  `scripts/audit_reachability.py` + `scripts/_reach_allow.txt`——AST 模块可达性
  扫描（`_LAZY` 边 + 父包传播 + `python -m` 入口种子），孤儿须登记处置理由；
  `Makefile` 新增 `gates`（六步统一门禁序列：lint→format→全量→对抗→golden
  三旗标→可达性）与 `audit-reachability`/`audit-adversarial` 目标。

- **F1 流式与客户端（S 域）**：
  `streaming/__init__.py` C1 订阅符号经 `domain.symbol` 归一裸码查表/作 diff 键
  （`sh600519` 静默零数据终结；异步镜像同修）；C4 `subscribe` 对称加锁、
  流线程顶层 `except Exception` 兜底走 `on_error`+退避（线程无声死亡终结）、
  异步 `on_error` 套 suppress；**新发现并修复**：异步流对 `asyncio.Event.wait`
  传超时参数必 `TypeError`（协程一进空转分支即死）三处改 `asyncio.sleep`。
  `client.py` C3 `last_errors` `__init__` 显式声明+锁保护，`quotes_concurrent`
  错误收集调用局部化（并发错误确定性蒸发终结；`quotes()` 新增内部 `_collect`
  参数，公开签名不变——外部 monkeypatch `client.quotes` 的 fake 需吸收该 kwarg）；
  P1a `bars(index=True)` 经 dispatch ctx 直达解析器（指数 K 线 4 字节尾端到端
  消费，`facade/market.index_bars` 实装；布局仍 ⚠️ 待真机 golden 定标）；
  `quotes_snapshot` ≤80 只分片+失败回退逐只+日志计数（>255 struct.error 绕回退
  终结）；`export_security_list` 截断告警；`quotes()` 单只坏标的不中断整批；
  `_quote_body` 反转语义注释纠偏+market clamp。`push.py` 订阅体符号归一
  （`600519.SH`→`519.SH` 坏字节终结）、bj→0 对齐协议链、真实 Connection
  鸭子适配。`streaming/engine.py` `diff_only` 死分支修正为真 diff 语义并
  标注 experimental；`QuoteStream.max_queue` 弃用告警；三模块日志接线。
  新增回归 32 条（`tests/unit/test_client_f1.py` 等），S 域 90 项独立复验全绿。

- **F3 门面一致性（A 域）**：`facade/api.py` W11 `_try_routes` 聚合
  `route_errors` 进最终异常 context + 逐路由 warning + 实例级轻量熔断
  （连续失败 ≥3 次冷却 30s，auto 序跳过、显式路由仍试、`reset_circuit()`
  公开；空结果≠失败/ImportError 重抛/只捕 Exception 三红线保持）；
  W12 route 参数治理：minute/trades 接 tdx+web 双通路，15 个纯 tdx 能力
  方法显式传非 tdx 路由 → `ValueError`（全仓 grep 无破坏面），误导文案改
  「路由 X 在方法 Y 无对应实现，可用: …」；**W13 契约变更（口径根因修复）**：
  `bars()` 新增 `adjust` 参数透传 web 且默认口径由「web 强制 qfq」改为
  「原始价」，`start` 仅 tdx/local（显式 web → ValueError，auto → 剔除 +
  warning），价格口径不再随网络状态漂移。`response.py` df 列并集显式化 +
  from\_result 边界注释；三门面（binary/bridge/market）定位文档化 + 冒烟
  测试 19 条 + `binary.py` 死代码删除。新增回归 53 条，`tests/facade` 83 全绿。
  （复核例外：审计引导的 `market._option_symbol` 截尾声明为幻影引用，未虚构实现。）

- **F1 传输（T 域）**：`transport/base.py` C2 连接级租约（`TcpConnection.__init__`
  建 `RLock`，覆盖 connect/ping/request/异步镜像全生命周期；`Slot.busy` 死字段
  删除，杜绝假实现第三态）；`async_.py` C5 补 `asyncio.Lock` 请求串行化
  （**跨版本真缺陷**：3.10/3.11+ `wait_for` 超时不取消协程 → 双协程同 socket
  必串线；3.14 `Connection` 本身非线程安全，故跨版本都成立）+ C6 补 8 字节
  响应头 magic 校验（异步路径旧实现完全不校验）。`pool.py` T1 建连期异常分类
  （网络类异常 → `PoolConnectError` 供退避、编程错误不再被吞）+ T4 兑现注释
  承诺（`return_partial_on_failure`：保留已完成请求 + warning）+ tried\_hosts
  快照锁内拷贝 + 心跳 magic fail-fast + 埋点 `record_request`/`record_parse`/
  `record_reconnect`。`ratelimit.py` §2-9 三修（reset 锁 / `failures` 原子读 /
  时段档变更清令牌桶）+ 死 `TokenBucket` import 清理。`speedtest.py` 测量口径
  修复（旧实现测 recv 循环自身 CPU 非 RTT；`round*`→`round`；`ping_host` 默认
  端口 0 恒假 → 必失败）。`sniff.py` §2-13/24/25 三连（zip\_size==0 死循环 /
  解压失败 break / 16 个未知命令名 `msg_id:` 前缀）。新增回归 40+ 条，含
  16×650=10400 并发请求 0 串线压测（`-m slow` 门控）。

- **F2 协议（P 域）**：`protocol/registry.py` P1b/T2 dispatch **边界异常收口**
  （L1 解析器逃逸的原生异常统一包装 `ParseError` 保留 cause/context，fatal
  IntegrityViolation 仍直通；**实测修正：审计所引 7 类逃逸锚点已在早前批次
  消失，真实收口面为 9 payload×61 命令=610 组合实测 0 逃逸 + 永久测试固化**）

  - T3/P1b count 钳制（`codec count_guard` + `guarded_count`，声明数按剩余
    容量收敛 + 告警）+ L1 降级告警可观测（`DEGRADE_NOTICE`）。`std7709.py`
    CapitalChanges/FinanceInfo 尾记录 `len<size` break（旧实现裸 IndexError
    整批崩，测试前 1000×\[0xFF] payload 实测整批全 0 Bar）、P1d `build_realtime_quote_body`
    600000 doctest 笔误改正（golden 逐字节对拍锁定反转语义，⚠️ 家族复用面
    待真机）、P1a 指数 4 字节尾消费核实无误。`std7709_extra/goods/mac` 内层
    `except break` 改 fatal 感知 re-raise。`prober.py` §2 rate<=0 即抛 + probe\_range
    max\_count + 盘中改记 notes（可配 strict 抛）。`framing.py` iter\_frames 补
    max\_frame\_bytes 守卫 + ResponseFrame spec\_magic。`parsers/__init__.py`
    `__all__` 12→70 补全。P2 死代码三项：**`get_datetime_from_lc`** **删除从未使用
    的第二形参** **`minutes_from_midnight`（公共 API 契约变更，单参调用方零影响，
    双参调用转** **`TypeError`）**、`mac._count_records` 死模板删除、
    `MacHeartbeatParser` 恒真定性为设计（空载荷=可解析即活，恒真式清为显式
    `True` + docstring 依据注记）。新增回归累计 53 条。

- **F3 Web 源（W 域）**：`web/base.py` W3/W4 重试治理（`max_retries` 钳 0-5 +
  退避封顶 8s + 400/404 不重试 403/429/5xx 重试 + 传输/解析**双失败桶** +
  网络层异常纳入重试面）+ W5 令牌桶**进程级**（`shared_bucket` 注册表，修
  门面每调用新建源致令牌满血复活）+ C7 限流 `acquire` 超时抛 `WebRateLimited`
  （旧无超时死等）+ W10 `encoding` 类属性（gbk 默认，东财系 utf-8，修正新浪/
  腾讯 gbk 与东财混用）+ 结构性 `_EastmoneyJson` 单一实现（五处拷贝删除）。
  `fundflow.py` W1 po 排序方向 + W9 BSE 市场归属（f13=0 经 domain.symbol 判
  bj）；`adapters_ext.py` W6 分时 `len<4` 越界；`facade.py` W7 `longhu_rank`
  @staticmethod；`hot_rank.py` W8 空值防御；`corporate.py` W14 北交所 .BJ
  后缀 + report 参数化；`history.py` NaN/inf 过滤 + 周期/复权未知值**显式报错**
  （旧静默回退，含 hk/us 1min 误落日线数据缺陷）+ 翻页上限。`sources.py`
  「140 笔/页」→ 单一事实源指针。复核不成立 6 条（boards is\_member/normalize
  \_i/market\_stats zip/longhu super None/\_code\_to\_secid/wencai 守卫，均行号
  证据）。新增回归 62 条。

- **F4 服务面与可观测（V 域）**：`http_server.py` V1/V2 安全批——`SAFE_CLIENT_METHODS`
  24 方法白名单（`getattr(client,*)` 任意派发收口，破坏性 → 403 / 未知 → 400/404）+
  TaskStore max\_tasks=200 上限（活跃满 409 + 已完结按时间逐出 + 结果体

  > 1MB/>1000 行钳制）+ `_LazyClient` 类级存在性探测（`getattr(...,None)` 探测
  > 永真修复）+ POST body 4MB 中间件 + V6 错误面脱敏（原生异常 → E9001 无泄漏

  - logger.exception）+ 端点文档 42 实测清点。`ws_server.py` V4/V7——
    同步 handler 阻塞事件循环修复（`asyncio.to_thread`）+ path/max\_size 接线
    （现代/legacy websockets 双轨）+ 真推送 `quote_update`（每连接独立
    JsonRpcHandler 订阅集，30s 停推送改真实轮询推送）+ unsubscribe 可哈希校验 +
    asyncio.run 收敛；**SHA-1 项复核不成立**（握手由库内部完成，本包无 sha1 调用点）。
    `mcp_server.py` V6 三处 size/count 钳制 + 错误面脱敏 + stdio 异常日志；
    **route/force 透传与 \_write\_loop 死代码两项复核不成立**（grep 无此符号）。
    `metrics.py` V3 Histogram observe 累积语义修复（黄金序列 + histogram\_quantile
    手算对拍）+ 标签值转义 + 带锁快照贯穿导出器；`statsd` 增量快照/真解析标签/
    port=0 禁用/start\_pushing 真实线程；`observability start_exporter` 统一装配
    工厂。`feedback/` stats 环形缓冲死分支修复（`idx%max` 恒\<max 致 append 无界
    增长）+ telemetry 深拷贝 + 丢弃计数 + reporter 键名脱敏 + 混合类型排序防御。
    新增回归 66 条（含真回环 socket 收 quote\_update、1009 超限关闭）。

- **裁决（防重复误修）**：P 域上报的「0x0530 单票请求市场字节翻转真缺陷」经
  编排者独立实测**不成立**——真实 0x0530 走 `std7709.build_realtime_quote_body`
  （`quote_request_market`，SH→0/SZ→1），golden 逐字节对拍 600000/000651 全中；
  P 引用的 `build_security_quote_body` 全库不存在，`1-m` 实为 `_quote_body`
  服务 goods/ex/mac 三族（S 域已注明 ⚠️ 未经真机、与 quote\_request\_market
  同语义）。系并行读在途快照的误归属，0x0530 链无需改动。

- **存储/配置/CLI/工具域（G）**：`domain/symbol` 归一为单一事实源；`domain/calendar`
  交易日历惰性加载 + `RLock` 并发守护；`sinks` 分发表化 + 原子写统一 + `_TABLE_NAME_RE`
  预校验表名；`sources` 路由 `default_empty_ok` 语义收敛；`profile` 探测去重 +
  `_price_scale_sanity` 合理性守卫；`native` dict-only 签名 + `lot_factor` 语义对齐；
  `adjust` docstring 与防御性入参；`cli` 收敛至 19 子命令（新增 `probe`/`feedback`，
  退出码统一 TdxError→2 / KeyboardInterrupt→130）；`tools` 补 `_yaml_min.dump_yaml`
  （YAML 往返）、`golden_expand`、`check_originality --fix` 门控、`spec_audit`
  `coverage_summary(results)` 缓存化 + `_print_table`；`config` schema `_deep_merge`
  / `config_from_dict` 严格校验 + 移除未用枚举。新增 11 个测试文件 154 用例
  （含 10 条 YAML round-trip）。经核实反驳审计误报 4 项（`spec_audit._walk_python`
  等全库不存在的引用）。

### Documentation

- `docs/INDUSTRIAL_OPTIMIZATION_PLAN.md`：修复状态回填（逐项 ✅/⚠️/↩ 反驳）。

- `docs/api/README.md`、`docs/cookbook/06_custom_command.md`、`docs/adr/README.md`、
  `Makefile` 审计行：清除已删模块引用；Web 源规模口径修正为
  14 源模块 / 45 Source 类（按 grep 实数）。

- `tstdx/errors.py`：E6xxx 流式异常四类处置决议入注释（保留为预留公共错误
  分类；兑现背压/补数承诺的批次必须接线本分类）。

## \[1.1.0] - 2026-09-02

### Added

接口面吸收（对照 thsdk / levistock 公开能力面，洁净室实现，见 `docs/archive/ABSORPTION_ANALYSIS.md`）：

- **tstdx/facade/response.py**: 统一响应形态 `ApiResponse{success,error,data,extra,code}`

  - `__bool__` + `to_dict()` + 惰性 `.df`（pandas 可选）；`ok/err/from_result/wrap`
    工厂；`TdxError` 自动转失败响应（保留错误码与 context）。
    「成功但空」与请求失败严格区分。

- **UnifiedQuoteAPI.query(method, ...)**: 任意门面方法的统一响应化入口
  （永不抛异常边界）；HTTP 网关同步暴露 `POST /query`。

- **tstdx/web/wencai.py**: i问财自然语言选股（`WencaiSource`，注册名 `wencai`，
  capability `wencai`，1 req/s）。cookie 由调用方持有（参数或
  `TSTDX_WENCAI_COOKIE`），不依赖任何第三方 cookie 中继服务；
  返回 title+rows zip 后的 `list[dict]`。

- **search\_symbols(pattern, limit=, market=)**: 统一证券搜索（WebQuoteSession
  与 UnifiedQuoteAPI 双入口）：`suggest` 之上补齐 display 展示名与市场过滤。

- **corporate\_action(symbol)**: 权息资料语义别名（TDX 0x000F 股本变迁通道）。

- **index\_list()**: 常用指数离线目录（与 `index()` 行情互补）。

- **HTTP 网关新端点**: `GET /search`、`GET /index_list`、`GET /wencai`、
  `GET /corporate_action/{symbol}`、`POST /query`。

- **tests/**: `tests/facade/test_response.py`（21 用例）、
  `tests/web/test_wencai.py`（13 用例，全罐头）、
  `tests/web/test_search_symbols.py`（12 用例）；registry 一致性门禁扩展
  `wencai` capability ↔ facade 方法映射。

- **docs/archive/ABSORPTION\_ANALYSIS.md**: 上游接口吸收差距矩阵（能力对照 +
  洁净室红线 + 后续路线图）。

### Added（信任度批次：Golden origin 分级 + L1 真实样本门禁）

- **tstdx/tools/golden\_audit.py**: Golden 语料 origin 分级审计工具——
  样本按 meta.source 三级归一（real=self-captured / synthetic / unknown），
  输出分命令 real/syn 统计与维度覆盖（K 线类别、市场、代码数）。
  报告与门禁输出 ASCII 安全（Windows GBK 控制台兼容）。

- **L1 真实样本门禁**: 账本中 `tier=L1 且 verified=True` 的命令必须有
  ≥1 个 real 样本，`--gate` 缺失即退出码 1（CI 阻断）——
  「已宣布精确解析却没有真实主站样本」视同回归。
  追加开关：`--require-markets`（市场敏感命令须双市场 real 覆盖）、
  `--require-kline-categories`（K 线类别集合）。缺样本时给出
  `python -m tstdx.tools.capture --plan <kline|core|quotes>` 补录指引。

- **CI**: 新增 `golden-gate` job（`python -m tstdx.tools.golden_audit --gate`）；
  Makefile 新增 `audit-golden` 目标。

- **当前基线**: 500 样本（real 30 / synthetic 470 / unknown 0），
  L1 verified 命令 0x44E / 0x052D / 0x0530 / 0x000F 全部具备 real 样本，
  0x052D real 类别覆盖 0-11 全集；0x44E / 0x052D 的 parse\_ctx 市场维度
  缺失被软性标记（INFO，不阻断）。

- **tests/unit/test\_golden\_audit.py**: 24 用例（分级归一 / 聚合 / 缺口 /
  市场与类别门禁 / CLI 两路），门禁用真实账本 + 动态构造语料自适应验证。

### Added（接口扩展批次：个股所属板块 / IPO 日历 / 大单流向）

- **`stock_boards(symbol)`**: 个股所属板块（东财 push2 `slist` `spt=3` 接口，
  2026-09 实测验证）——行业/概念/地域全量 BK 板块（实测一只家电龙头 38 个），
  按板块涨跌幅降序；与 `em_board_members`（板块→成分）方向互补，
  返回 `code` 可回查成分形成板块网络遍历。
  `EastmoneyBoardSource.fetch_stock_boards`（复用 push2 主机池容灾）。

- **`ipo_calendar(apply_date=, page=, size=)`**: IPO 申购日历（东财
  datacenter-web `RPTA_APP_IPOAPPLY`，2026-09 实测验证）——申购/中签/缴款/
  上市日期、发行价与预测价、发行量、网上申购上限与顶格市值、行业 PE。
  未定价/未上市字段为 `None`（保留 null 语义，不填充 0）；
  `apply_date` 非空即「今日申购」视图。`EastmoneyIpoSource`
  （继承 datacenter 报表基类，`VALID_REPORTS["ipo"]` 注册）。

- **`big_order_flow(symbol)`**: 个股大单流向语义别名（单只标的的五档
  资金分档明细：主力/超大单/大单/中单/小单净流入与占比），
  与批量 `fund_flow` 同通道（东财 ulist）。

- **HTTP 网关新端点**: `GET /stock_boards/{symbol}`、`GET /ipo`、
  `GET /big_order_flow/{symbol}`。

- **registry 扩展**: `eastmoney` capability += `stock_boards`，
  `corporate` capability += `ipo`；一致性门禁同步
  （`fund_flow` 回归守卫 += `big_order_flow`）。

- **capture 元数据富化**: `_bars` ctx 补记 `market`/`code`，
  core 计划 0x000F/0x0010/0x044E/0x0537/0x0FC5 补记 `market`/`code`——
  后续补录的 Golden 样本可直接喂给 golden\_audit 的市场维度覆盖
  （消除 0x44E/0x052D 的 market\_gaps INFO 缺口）。

- **盘中异动**: push2ex `getStockChanges` 端点参数组合经 7 组实测均返回
  rc=102，无法离线确证——**本批不实现**（路线图标注「端点待重抓包验证」，
  不上线未经验证的接口）。

- **tests/web/test\_stock\_boards.py**: 17 用例（slist 罐头解析 / secid /
  null 语义 / 过滤器 URL / facade 双入口 / live 结构冒烟）。

### Added（补全批次：盘中异动 / 股吧人气榜 + Golden 实采补录）

- **`stock_changes(types=(), page=, size=)`**: 盘中异动池（东财 push2ex
  **`getAllStockChanges`**）。端点事实经前端公开 JS 提取 + 实测验证
  （此前 7 组探测失败系方法名与 `dpt` 参数错误：真实为
  `dpt=wzchanges` + `getAllStockChanges`，非 `getStockChanges`）。
  16 类异动枚举（火箭发射/大笔买入/60日新高等）随源暴露
  （`EastmoneyStockChangesSource.CHANGE_TYPES`）；`i` 指标串拆为
  `metrics: list[float]`（含义随类型不同，不强行归一）；
  非交易时段空列表为合法状态。注册名 `stock_changes`。

- **`hot_rank(page=, size=)`**: 股吧个股人气榜（东财 emappdata
  `stockrank/getAllCurrentList`，POST JSON，实测验证）。返回
  rank/symbol/code/market/rank\_change/his\_rank\_change；榜单仅含排名
  与代码，行情回查 `quotes`。`globalId` 本库生成（uuid4）。
  同花顺人气榜旧端点（dq.10jqka fuyao hot\_list）实测 404 已下线，
  不采用。注册名 `hot_rank`。

- **HTTP 网关新端点**: `GET /stock_changes`、`GET /hot_rank`。

- **registry 扩展**: `stock_changes` / `hot_rank` 源 + capability +
  normalizer + adapter 四方注册，一致性门禁同步。

- **Golden 实采补录**: 交易时段外运行 `capture --plan core/kline`（2026-09-02
  午休窗口，20/20 成功），语料 **real 30 → 50**：0x44E 双市场（mkt=\[0,1]）、
  0x052D 28 条实采（类别 0-11 全集 + 双市场）、0x000F/0x0010/0x0537/0x0FC5
  各 +1；`golden_audit --gate --require-markets` 最严格组合通过，
  market\_gaps 元数据缺口完全消除。回放类数值断言测试改为锚定设计罐头
  （`_pinned_sample`），不再随补录漂移。

- **tests/web/test\_hot\_rank.py**: 24 用例（异动罐头解析/枚举完整性/
  类型过滤与分页/POST 体形态/size 钳制/人气榜解析排序/live 冒烟）。

### 1.1.0 升级批次（docs/archive/OPTIMIZATION\_PLAN.md，合并两轮梳理的优化点）

- **批次 A（TDX 账本校准与门禁增强）**:

  - `facade/api.py` 注释命令号修正：`block_quotes` 0x02CF→**0x07E5**、
    `volume_price` 0x02EE→**0x051A**（与 client 实际请求一致）。

  - `commands.py`：`Command` 新增 **`status`** 字段（online/offline/degraded）

    - `by_status()` 助手；0x054C/0x053E/0x0450 实测下线入账；
      0x0010/0x0537/0x0FC5 升 `verified=True`（golden 实采支撑），
      0x0FB4/0x06B9 保持未验证（无实采样本不虚标）。

  - `golden_audit.py` 新增 **payload 有效性下限**（`MIN_PAYLOAD_BYTES`
    按命令单记录最小布局）与 `suspect_short` 分类；`--require-payloads`
    纳入硬门禁——「real 样本 ≠ 有效样本」盲区修复（0x537 空分时样本
    4B 已被标记警示）。

- **批次 B（发布卫生）**: `pyproject.toml` ruff 配置段迁移至
  `[tool.ruff.lint]`（废弃写法清理）；**lint 债务 417 项清零**
  （F401×133 / UP×112 / I001×53 / SIM105×28 / F841 / B905 / E741 /
  F821×9 真实引用缺失修复——含 `client._PREFIX_MARKET` 未定义的
  潜在 NameError、`requests.py` `__all__` 15 个幻影导出）；
  `ruff format` 全仓 137 文件归一。

- **批次 C（服务面 parity）**: CLI 新增 `changes` / `hot` 子命令；
  MCP 10→**12 工具**（`get_stock_changes` / `get_hot_rank`）；
  WS 新增 `stock_changes` JSON-RPC 方法；`docs/api` 全量更新
  （统一门面/异步门面/Web 源矩阵/集成服务面）。

- **批次 D（韧性与体验）**: `tstdx/tools/_console.py` 统一控制台
  UTF-8 出口（capture/spec\_audit/golden\_expand/check\_originality/
  golden\_audit 全部接入，GBK 控制台乱码修复）；
  **`AsyncUnifiedQuoteAPI`**（`facade/async_api.py`）：核心 10 方法
  `asyncio.to_thread` 桥接 + `arun()` 泛化通道 + `aquery()` 永不抛
  异常边界；版本 1.0.0 → **1.1.0**。

- **tests**: `tests/unit/test_commands.py`（账本校准 12 用例）、
  `tests/unit/test_upgrade_1_1_0.py`（C/D 批次 13 用例）；
  MCP 工具数断言同步 12。

### 1.1.0 批次 E 落地（docs/archive/OPTIMIZATION\_PLAN.md 长期队列可即启项）

- **E1 并发批采**: `TdxClient.quotes_concurrent`（线程池 + 连接池并行，
  保序 + 单只失败容错记 `last_errors`）、`AsyncTdxClient` 异步镜像、
  facade `quotes_concurrent`。

- **E3 符号库导出**: `export_security_list`（0x044D 分页遍历，短页停 +
  max\_pages 防御）、async 镜像、facade `security_list_all`。

- **E8 深市补录**: capture `core` 计划补 market=0 变体（0x000F/0x0010/
  0x0537/0x0FC5），午休窗口实采 10/10，**语料 real 50→60，
  四命令 mkt=\[0,1] 全覆盖**。

- **E5/E7 实测闭环**: capture 新增 `pool` 计划；0x07E5/0x051A/0x056A
  三主站实测无响应 → 账本 **offline**（client 方法保留待参数校正）；
  0x0FEB 断连（不识别 → offline）；0x051B 存活但空布局（degraded）；
  0x0537 沪市空布局根因独立（深市 619B 有效）→ degraded。

- **E10 bench 基线**: `benches/bench_web_parsers.py`（异动池 33.7 万
  rec/s / 人气榜 98.4 万 rec/s，5k 罐头 p50/p95）。

- **注释漂移三连修**: facade `security_list` 0x0514→0x044D、`finance`
  0x0223→0x0010、`capital_changes` 0x0A03→0x000F，加 docstring 防回归。

- **tests**: `tests/unit/test_batch_e.py`（13 用例）；
  账本 status 断言更新（offline 7 / degraded 2）。

## \[1.0.0] - 2026-08-31

GAP\_ANALYSIS\_v0 全部四档（A/B/C/D）实现完成的首个稳定版。

### Added

- **PROTOCOL\_SPEC/**: YAML spec system with 8 core command specs (handshake, heartbeat, kline, realtime\_quote, minute\_today, trade\_today, trade\_today\_alt, security\_count) + UNKNOWN/ 自动草案目录

- **tstdx/integration/**: HTTP 服务（FastAPI，6 组 32 端点 + 后台任务）、WebSocket JSON-RPC 2.0（bars/quotes/订阅/退避错误码）、MCP stdio 服务（10 工具）

- **tstdx/profile/**: 六步数据规格探测（证据链 + 置信度 + ProfileUndetectable）与 9 市场预设（SH/SZ/BJ A股、基金、债券、黄金、期货）

- **tstdx/protocol/prober.py**: 未知命令主动探测（1 req/s 限速、非交易时段门禁、spec DRAFT 归档）

- **tstdx/transport/sniff.py**: 被动嗅探（环形样本缓冲、未知命令发现、DRAFT YAML 导出）

- **tstdx/\_async\_bridge.py**: run\_sync 主线程/子线程/活动 loop 三态语义 + AsyncBridge 守护线程

- **tstdx/streaming/push.py**: 0x0547 PushChannel 原始字节推送

- **tstdx/tools/capture.py**: 合规强化（交易时段阻断、zlib/zstd 归档、hex dump、structure.md、schema v2 元数据）

- **tstdx/tools/golden\_expand.py**: Golden 语料合成扩充至 500 案例（幂等、SHA256 去重）

- **tstdx/tools/check\_originality.py**: AST 原创性门禁（许可头/拷贝指纹/外部导入审计）+ pre-commit 接入

- **tests/**: 11 类目测试矩阵（config/errors/web/observability/i18n/sinks/sources/streaming/protocol/client/compatibility）、24 项贯通 bridges 测试、流韧性测试、弃用策略测试、HTTP/WS/MCP 服务测试

- **benches/**: bench\_reader.py（记录数/秒、p50/p95）+ bench\_parser.py（黄金 payload 分派吞吐）

- **ORIGINALITY/AUDIT\_REPORT.md**: 合规审计报告（84/84 文件通过，0 疑似抄袭）

- **.github/workflows/wheels.yml**: 3 OS × 4 Python 发布矩阵（tag 触发 PyPI 发布）

- **docs/**: quickstart/FAQ/troubleshooting/migration×3/cookbook×6/ADR×10/API ref

### Changed

- **tstdx/web/adapters.py**: 归一化逻辑集中到 web/normalize.py（7 个 normalizer 注册制）

- **版本策略**: SemVer 正式版；弃用策略 = 2 个 minor 窗口（DeprecationPolicy.removal\_gap）

## \[0.4.0] - 2026-08-31

### Added

- **PROTOCOL\_SPEC/**: YAML spec system with 8 core command specs (handshake, heartbeat, kline, realtime\_quote, minute\_today, trade\_today, trade\_today\_alt, security\_count)

- **tstdx/web/normalize.py**: Centralized volume/amount normalization across 7 HTTP Web sources

- **tstdx/i18n/encoding.py**: Charset auto-detection (UTF-8/GBK/GB18030/Big5)

- **tstdx/tools/codegen.py**: YAML spec → parser codegen

- **tstdx/tools/spec\_audit.py**: Spec↔implementation coverage validation

- **tstdx/feedback/**: Feedback reporter, telemetry collector, user stats (opt-in, 7-step sanitization)

- **tstdx/security/credentials.py**: 3-tier credential storage (keyring → env → encrypted file)

- **tstdx/compat/easy\_tdx.py**: easy\_tdx compatibility shim

- **tstdx/compat/eltdx.py**: eltdx compatibility shim

- **tstdx/tools/check\_originality.py**: AST-based originality checker

- **ORIGINALITY/LICENSE\_ALLOWLIST.md**: License allowlist for compliance scanning

- **GOVERNANCE.md**: Project governance document

- **CODE\_OF\_CONDUCT.md**: Community code of conduct

- **SECURITY.md**: Security policy

- **CONTRIBUTING.md**: Contribution guidelines

- **.github/**: CI workflows (lint, type-check, test, originality, spec-coverage, bridges)

- **.github/ISSUE\_TEMPLATE/**: Bug report, feature request, docs, security advisory templates

- **.github/PULL\_REQUEST\_TEMPLATE.md**: PR template

- **Dockerfile**: Docker image for Python 3.11-slim

- **docker-compose.yml**: Docker Compose configuration

- **Makefile**: Development commands (install, test, lint, audit-bridges, etc.)

- **git-cliff.toml**: Changelog generation configuration

- **docs/adr/**: Architecture Decision Records (5 ADRs)

- **README.md**: Project documentation

### Changed

- **tstdx/web/adapters.py**: Updated to use centralized normalize module

### Fixed

- Various bug fixes and improvements

## \[0.1.0] - 2026-08-31

### Initial Release

- Core protocol parsing (60 parsers, 85 command ledger)

- Sync/Async dual API (TdxClient + AsyncTdxClient)

- Multi-protocol-family clients (Goods, ExMarket, MAC, F10)

- HTTP Web 7 Adapter + easyquotation shim

- 5-source fallback routing

- Streaming engine with reconnect/gap-fill/backpressure

- 3 data sinks (DataFrame, Parquet, DuckDB)

- 40+ error classes with RetryAdvice

- Config schema with 6-source merge

- Local vipdoc reader

- Zero-dep observability (Prometheus metrics)

- CLI with 12 subcommands

