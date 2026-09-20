# tstdx v18 结构收口与对外契约重设方案

> **文档状态**：待用户裁决（§6 的 D7–D12 六个决策点；§5 的阶段可分批授权）。
> **授权尺度（本轮用户已答，2026-09-20）**：
> ① 重构尺度 = **含对外契约破坏**（允许改 capability 语义、三面入参/响应形状、门面定位）；
> ② 门禁尺度 = **可合并可删除**（覆盖率阈值 77 与 strict 旗标仍不降）；
> ③ 对外动作 = **push + 预发布 tag**；
> ④ 工作区产物 = **抓包样本入库 + 忽略本地工作树**（已随 `3028721` 落地）。
>
> **与 `docs/REFACTOR_PLAN_V18_REVIEW.md` 的分工**：那份是同一天并行会话的**复评轮**，
> 登记了 F-72…F-76 五笔新账并给出"本轮不改代码"的收口次序（其 §7 占用了第 50–55 步）。
> 本份不重复登记 F-72…F-76，也不与它争步骤号：本方案的条目一律用 **R-#** 自编号，
> 落盘执行时再按当时的台账空号领取 F 号与步骤号（领取前先 `grep` 台账工作树）。
> 一句话区分：它回答"还有哪些洞"，本文件回答"**架构哪里长歪了、按哪条破坏性路线扳回来**"。

---

## 0. 本轮实测基线（全部同一轮、同一隔离树产出，无一格来自记忆）

工作树：`%LOCALAPPDATA%\Temp\wt_v18review`（detached @ `616d065`，本方案取证时 HEAD）；
解释器：仓库 `.venv` = **cpython-3.12.13**（并行会话的 3.13.12 读数与本表不可互换）。

| 项 | 读数 | 取证方式 |
|---|---|---|
| 离线全量测试 | 进度走到 `[100%]`，**`PYTEST_RC=0`**；日志无 `N passed` 汇总行，故不引用该数 | `pytest tests/ -q -m "not network" --cov=tstdx` |
| 覆盖率 | **81.42%**（TOTAL 22406 stmt / 3588 miss / 5982 branch / 1004 partial），`Required test coverage of 77.0% reached`，**阈值未动**；49 个模块覆盖率已满 | 同上 |
| 离线判据规模 | `--collect-only` 实得 **3598 项 / 228 个含测试文件**；仓内 `def test_` **2432 个** | `pytest --collect-only` 逐文件计数求和 |
| ruff | `check` RC=0；`format --check` **442 files already formatted** RC=0 | 隔离树 |
| mypy（CI 参数） | **RC=0，输出 0 行** | 隔离树 |
| reachability `--strict` | **190 模块 / 174 可达 / 16 豁免 / 记录缺陷 0**，RC=0 | `scripts/audit_reachability.py` |
| originality | `Total: 191 / Original: 191 / License OK: 191 / Header OK: 191 / Suspicious: 0`，RC=0 | `tstdx.tools.check_originality` |
| spec_audit `--strict` | RC=0 | `tstdx.tools.spec_audit --json --strict` |
| docs links | 82 文件 OK | `scripts/check_docs_links.py` |
| 契约对账 `contract_audit` | **63 个 Typed 契约 / 155 个注册业务 capability / 92 项 PENDING / 0 ERROR**，RC=0（PENDING 不阻断） | `scripts/contract_audit.py` |
| 注册表面 | `Client().capabilities()` = **172**；`DIRECT_BINDINGS` = **251**；**11 Provider × 56 (provider,channel)** | 运行期内省 |
| 绑定的执行方式 | **234/251（93.2%）走同一条泛化派发 `_migrated_capability`，仅 17 条有专属执行器**；按 Provider 分：derived 114 / eastmoney 59 / sina 17 / tencent 15 / tdx 30 / builtin 2 / baidu 8 / iwencai 2 / boc 2 / jsl 1 / local_vipdoc 1 | `Counter(b.executor_name for b in DIRECT_BINDINGS)` |
| 代码规模 | `tstdx/` **59 073 行 / 190 py**；`tests/` **74 307 行**；`docs/` **22 284 行 / 83 篇**；`PROTOCOL_SPEC/` 5 182 行 | `wc -l` 求和 |
| 子包体量 | web **17 913 / 51 文件（占包 30.3%）** · transport 5 425 · protocol 5 066 · tools 4 798 · client 3 406 · 其余 16 包 ~14 000 | 同上 |
| web 层注释占比 | 17 913 行里 **4 270 行是 docstring（23.8%）**，`corporate.py` 227 行、`fundflow.py` 206 行 | AST 逐文件统计 |
| 架构判据 | `tests/architecture/` **13 文件 / 4 983 行 / 126 个 test 函数** | `wc -l` + `grep -c "def test_"` |
| 发布链路 | 本地 `main` 领先 `origin/main` **75 个提交**（本方案取证时）；无 tag | `git status -sb` / `git tag` |

**门禁现状判定：九项确定性门禁在本轮全部 rc=0，主链没有断。** 因此本方案不是"救火方案"，
而是"体量与形状方案"：链是通的，但通得靠约定，且为这条通链服务的注释、门面与判据已经长得
比被测物本身还大。

---

## 1. 项目主要功能（照磁盘上现存的东西重述，一份就够）

| 功能族 | 载体 | 实测规模 | 对外出口 |
|---|---|---|---|
| TDX 7709/7727/F10/MAC/goods 协议直连 | `tstdx/protocol/` + `codec/` + `transport/` + `client/{core,sync,async_}` | 12 887 行 / 85 条命令账本 / 10 个解析器模块 / 44 项 YAML 规格（非 draft） | `TdxClient` / `AsyncTdxClient`（README 明确"可脱离内核使用"） |
| 统一查询内核 | `runtime/` + `catalog/` + `query.py` | 2 908 行；`UnifiedRuntime.execute()` 4 行；17 条专属执行器 + 1 条泛化派发 | `Client.execute/typed/call/bars/quotes/…` |
| 多源 Web 抓取 | `web/`（7 源适配器 + 18 Mixin 会话 + 40+ 特性模块） | 17 913 行；`WebQuoteSession` 公开方法 140；`tstdx.web.__all__` 69 | 内核绑定（104 条 web Provider 绑定）**＋** 直连会话门面（第二条入口，F-71 裁决保留） |
| 流式 | `streaming/` + `stream_contract.py` | 1 711 行；tdx-only 判据单点 | `Client.stream` / CLI `stream` |
| 本地 vipdoc 读盘 | `reader/` + `sink/` + `profile/` | 2 884 行 | `local_vipdoc` provider 的 1 条绑定 + 公共 API |
| 服务面 | `cli/` `integration/{runtime_http,runtime_ws,mcp/}` | CLI 31 子命令 · HTTP 10 路由 · WS 10 方法 · MCP 9 工具 | 只翻译不执行（两条 AST 门禁 F-29/F-56） |
| 配置面 | `config/` | 719 行 / 5 段，每键有读者 | `tstdx.toml` + `TSTDX_*`，内核唯一读者 |
| 交易协议模拟 | `trade/` | 1 535 行，纯内存模拟器，**内核与 CLI 都不 import** | 实验性可选模块 |
| 工程面 | `tools/` + `scripts/` + `benches/` | 4 798 + 1 523 行；抓包/codegen/spec/golden/originality 审计 | 九项门禁 + 开发期工具 |

**一句话概括产品**：一个"通达信协议库 + Provider-first 行情运行时"，双身份并存且都写进了 README。
这个双身份是本方案 §3 的第一条结构性不合理来源。

---

## 2. 核心功能是否全部实现 / 主体链路是否全部贯通（分开回答，不要混）

**主链贯通：是。** 本轮独立复核到三处证据，不引用并行会话：
① `DIRECT_BINDINGS` 由注册表三元组推导（`runtime/executor.py:74-86`），`Client()` 构造期跑
`catalog/capability_audit.py` 的注册表↔绑定双向对账，172 个能力名与绑定面闭合；
② 251 条绑定全部有 `executor_name`，其中 17 条走专属实现、234 条走 `_migrated_capability`，
**没有任何一格指向不存在的执行体**；
③ 四个服务面对 `tstdx.web` / `tstdx.streaming` 的 import 边被 AST 门禁清零（本轮实测 RC=0）。

**核心功能全部实现：否。** 缺口是四格，且每一格都可量化，不是感觉：

| # | 缺口 | 实测证据 | 严重度 |
|---|---|---|---|
| G1 | **三格能力在任何 Provider 上都不可能给数**，但仍占着 HTTP/WS/MCP 三面入口 | `minute`(0x0537)/`trades`(0x0FC5) 在 `tstdx/client/core.py:288,314,326` 恒定 `raise NotImplementedFeature`；`security_list`(0x044D) 在 `core.py:300` 恒定 `raise CommandOffline` | P1（对外承诺形状） |
| G2 | **typed 面只覆盖 40%**：155 个业务 capability 里 92 个无契约，`contract_audit` 判 PENDING 且 RC=0 不阻断 | 本轮 `scripts/contract_audit.py`：`63 契约 / 155 capability / WARN: 92 个待办` | P1（功能账，非缺陷） |
| G3 | **`0x000F` 资本变动 / `0x0010` 财务在新握手下仍解错**，F-37 余条未裁决；本轮离线日志里就能看到 `[E3040] 股权信息记录数异常: count=300, body=30, size=0` 的解码告警 | 隔离树测试日志 `v18rev_test.log` 的 warnings summary 段（`tests/client/test_decode_caveat_wiring.py` 现场复现） | P0（数据正确性） |
| G4 | **7709 数据面从未在交易时段被验证过**：CI 的 live-smoke 是 `cron '0 1 * * *'` UTC = 北京 09:00，注释自己写着 "before A-share trading"（9:30 开盘），host-audit 是周三 09:00 UTC = 北京 17:00（收盘后） | `.github/workflows/live-smoke.yml:9`、`.github/workflows/host-audit.yml:5` | P1（验证盲区，F-38 的真正根因） |

**结论口径建议统一成这句**（写进 README 与 F-37 裁决记录，避免每轮重新解释）：
> 链是通的，声明与执行是闭合的；G1/G2 是"实现了但选择不给数/不给类型"的登记账，
> G3 是唯一一处"给了错数"的真缺陷，G4 是"我们其实还没在有效时段看过它"的验证空白。

---

## 3. 不合理点清单（R-#，按"为什么会长歪"归类，不按症状）

> 每条都给本轮实测证据；与并行会话已登记条目重叠的，标 `≈F-xx` 并注明**只引用不重复登记**。

### A 类：主链的"唯一性"靠约定，不靠类型

**R-1｜P1｜93.2% 的执行路径是同一条反射派发，主链实为"17 条代码 + 1 个约定"。**
234/251 条绑定落到 `_migrated_capability`，参数经 `QuerySpec.options` 的 args/kwargs 袋传递。
后果不抽象：可发现性（`dir()` 看不见 167 个能力，≈F-76）、类型检查（IDE 全盲）、
失败投递（≈F-72 的 `last_errors` 侧信道正是约定派发在失败方向上的产物）三头都吃亏。
证据：`runtime/executor.py:71,83-86`；`Counter(b.executor_name …)` = 234/17。

**R-2｜P1｜执行器审计自身 fail-open。**
`audit_direct_bindings()` 发现"统一可达却缺 migrated 元数据"时只 `warnings.warn`
（`runtime/executor.py:116-122`），而同文件的注册表对账是 raise。一软一硬，软的那条
在运行期永远不会被 CI 看见（warnings 被 pytest 收集但不失败）。
同处还有硬编码特例 `capability == "bars" and provider == "tencent"`（`:92`）——
审计逻辑里嵌了业务事实，是 F-27/F-49 那一族"影子规则"的现存样本。

**R-3｜P1｜`tstdx/client/` 一名两职，对外口径自相矛盾。**
同一个包里既有"唯一业务入口"（`api.py` 的 `Client`/`AsyncClient`），又有可脱离内核的
协议客户端（`core.py`/`sync.py`/`async_.py`/`_mixin.py` 955 行）。README §架构总览写
"唯一执行路径"，§快速开始第一格却教 `TdxClient` 直连。**这不是文档错，是产品双身份没有边界表达**：
门禁（F-29/F-56）只禁服务面 import web/streaming，不禁也不描述"谁可以绕过内核"。

**R-4｜P2｜八张表要手工保持同步，且每轮都在长新表。**
注册表 `channel.capabilities` · `DIRECT_BINDINGS` · capability catalog · typed 契约 ·
CLI parser · HTTP 路由 · WS `METHODS` · MCP `inputSchema` —— 八处事实源，
现有判据是"两两对账"的枚举式扫描（126 条架构判据里一大半在做这件事）。
证据：`tests/architecture/` 13 文件 4 983 行；F-25/F-27/F-28/F-66 的处置记录每一笔都在补一张新对照表。

### B 类：体量与形状的失衡

**R-5｜P1｜web 层占包体 30.3%，其中 23.8% 是 docstring，代码本身仍是手写命令式的。**
17 913 行 / 51 文件；`corporate.py` 1 126 行里 227 行是抓包来的接口事实（含"已验证可用报表名"
与"实测不可用，不要写进代码"这类**只对人有用**的表格）。这些事实散在 40+ 个特性模块的 docstring 里，
既不能给机器对账（`PROTOCOL_SPEC/` 只管 TDX 帧，44 项），也不能被测试消费。

**R-6｜P1｜会话门面是 18 个 Mixin 撑出的 140 个公开方法，与"单源精确取数"的裁决并存但形状未收。**
`web/session.py` 的 docstring 自己声明"本模块不是路由层，不会自动换源"，
而 F-71 裁决 (c) 的另一半（`WebQuoteClient` 缺省仍按 `[web] enabled_sources` 顺序换源、
`get_quotes` 不指名）尚未执行。形状上它是一个"每个源一套方法"的类——**新增一个源要动 4 处**：
适配器 + `_session_*.py` mixin + `sources.py` 注册 + catalog 绑定。

**R-7｜P2｜16 项可达性豁免把约 5 500 行"公共 API 名义资产"永久停在静态图外。**
`deprecation.py` 206 行在 `tstdx/` 包内**零引用**（本轮独立复核：`grep -rn deprecation --include=*.py tstdx/`
除自身外 0 命中，也不在 `__all__` 45 个名字里，≈F-74）；`profile/`(1 262) `output/`(305)
`charset/`(510) `trade/`(1 535) 各有豁免理由且理由可核验。**问题不在单个豁免，
在于"公共 API"这个豁免类别本身没有价值判据**：一个 206 行的机制可以凭"docs 里记着"长期存活。

**R-8｜P2｜测试代码 74 307 行 > 生产代码 59 073 行，比例 1.26:1。**
2 432 个 `def test_`；其中 126 条架构判据（4 983 行）不测行为，只测"清单是否抄对了"。
判据的边际保护价值在递减（见 §4）。

### C 类：判据的盲区是"形状枚举"，同类缺陷换形状就复活

**R-9｜P1｜死引用判据按形状分条，每漏一种形状就是一条新 F。**
现有三条各管一种形状：反引号里的 `.py` 路径（F-67）、CamelCase 类名、README 结构树行。
本轮实测出两个**新形状**，都不在射程内：
① 活文档 `docs/troubleshooting.md:97` 教用户调 `client.router.last_errors()`，
而 `.router` 在 `tstdx/` 里零命中（≈F-73，小写点号形状）；
② **生产代码 docstring** `tstdx/web/sources.py:11` 用 Sphinx 角色写着
`:mod:`tstdx.sources.router` 中统一路由`，指向 v16 已删除的模块，且是**现在时语气**——
它比 F-73 更糟：库作者读代码时被误导，而 `:mod:` 角色本身在 Sphinx 构建期会直接报错。
（这条是本方案新账，登记时领新 F 号。）

**R-10｜P2｜"清单抄录失真"是本轮台账里最大的单一缺陷来源，而判据继承了它。**
F-18 有**三处抄录错误**（测试数、Provider 表键数、函数签名），F-24 是守卫测试把已删路径钉成契约，
F-22 是豁免清单 28 条里 9 条死记录。根因一致：**curated 清单是第二份事实源**，
而 curated 清单无法 glob 推导（F-18 行自己承认这点）。继续加对账判据等于继续买同一张票。

### D 类：文档已成为与代码同量级的第二工程

**R-11｜P2｜22 284 行 docs + 262 KB CHANGELOG + 161 KB DESIGN + 2 604 行宽表台账。**
`docs/` 83 篇里 4 份 parity/audit 文档仍把已删门面写成今天的接口（F-70 裁决 (b) **未执行**）。
DESIGN.md 16 万字符属于"没人能确认它是否还活着"的体量——它不在任何一致性门禁的活文档集合里
（F-14 记录过 CHANGELOG 不在集合内，DESIGN 同理）。

**R-12｜P2｜活文档里的数字与结构靠"人工同步 + 事后门禁"维持，而门禁只能校验它已知的形状。**
README 的 172/251/31/10/9 五个数字今天都对（本轮逐个复算），但每个都要靠一条判据；
F-18/F-24/F-73 三代教训说明：**手抄一次、错一辈子的东西应当生成，不该被校验**。

### E 类：验证环境没有单一事实源

**R-13｜P1｜同一棵树在本机有两个解释器读数（3.12.13 与 3.13.12），跳过项与覆盖率都不同；而 F-15 的阈值重钉只认 CI（ubuntu+py3.11）的 exact-head 读数，CI 数字至今没有。**
本轮 81.42% 是 3.12.13 的数，并行会话同树读到 81.50%/5 跳过。**两个都是真的，都不能用来钉阈值**。
`fail_under = 77` 是历史值，长期低于实测（81%）——这不是缺陷，但是"阈值已失去信息量"。

**R-14｜P1｜发布链路从未 push 过：本地领先 75 提交。**
75 个提交里的门禁结论全部是本机取证，没有任何一条经过 CI 的 ubuntu/py3.11 复算。
G4（无盘中 live 判据）+ R-13（无 CI 读数）合起来意味着：**这个"Production/Stable"版本的
稳定性证据链目前只有单机强度。**

---

## 4. 关于判据的诚实评估（因为你授权了"可合并可删除"）

判据的总体保护价值不低（F-12/F-16/F-30/F-72 都是它抓的），但结构上有三类**边际价值趋零**：

| 类别 | 例子 | 为什么边际低 | 处置 |
|---|---|---|---|
| 抄录式对账 | `OFFICIAL_RUNTIME` 清单、README 结构树行、CLI 命令名清单 | 判据正确性依赖作者手抄准确；F-18 三处抄错、F-24 把死路径钉成契约 | **合并**为一个"派生自代码的单一声明表 + 差集即红"框架，逐份 curated 清单消灭 |
| 形状枚举型死引用扫描 | 反引号 `.py`、CamelCase 类名、小写点号（R-9 的①）、Sphinx `:mod:` 角色（R-9 的②） | 每换一种形状就要一条新判据，永远慢一步 | **改写**为一次符号存在性扫描：抽出任意形状的点号/角色引用 → import 期判定 → 未知即红（形状枚举退化为提取器规则列表，判据只剩一条） |
| 常量自对 | 断言 `x > 0`、断言清单 ≥15 项、假签名替身 | F-26/F-27/F-28/F-63① 都记录过"守卫形同虚设" | **删除或升级为变异可证伪**：一条判据必须能指出"删掉哪一行生产代码它会红"，写不出就删 |

**收缩尺度的验收红线**（用户授权"可删除"，但删测试必须有代价证明）：
删除/合并任一判据时，同一轮日志里必须留下 ① 该判据的变异证据（红一次并指名），
② 合并后框架对同一缺陷仍红的复测，③ 判据条数与覆盖面的差值清单。缺任一条即不批准。

---

## 5. 重构方案（六个阶段，可分批授权；每阶段末尾是验收判据，不是自评）

> 落地程序沿用仓内既有惯例：隔离工作树改动 → 字节相同搬回主树 → 提交 → 用**提交树**复测九门禁
> + 离线全量 → 台账记两轮数。**阈值只升不降，升只凭 CI exact-head 读数。**

### 阶段 V18-A｜先把已有裁决落地，把口径钉住（不改架构，1–2 轮）

| 项 | 动作 | 破坏性 |
|---|---|---|
A1 | 执行 F-70 (b)：4 份 parity/audit 文档按 v17 事实改写（反引号方法名逐份回查落点） | 无 |
A2 | 执行 F-71 (c)：`WebQuoteClient` 缺省单源、显式指名；顺带修 `web/session.py` 的假事实句 | 低（缺省换源行为消失＝行为变化） |
A3 | 修 R-9 的两处幻影：`docs/troubleshooting.md:97` 与 **`tstdx/web/sources.py:11` 的 `:mod:` 死引用**（新账） | 无 |
A4 | 把 §2 的"贯通≠全部实现"口径写进 README §核心特性 与 F-37 裁决行，四格缺口 G1–G4 各引一处证据 | 无 |

**验收**：九门禁 rc=0 不变；离线全量与覆盖率与基线同量级（记录读数）；新增一条判据能抓出
"生产 docstring 里的 `:mod:`/点号引用指向不存在符号"，并用 A3 的两个实例做变异证据。

### 阶段 V18-B｜对外契约重设（破坏性主体，2–4 轮）—— 对应 §2 的 G1/G3 与 R-1/R-2

B1 **`quotes` 整体失败语义**（按 D1 默认 = 全失败抛 `AllHostsUnreachable`、部分失败写 `warnings`）；
判据：禁网夹具下"全失败 ⇒ 要么抛、要么 `warnings` 非空"，`strict=True` 必须能拒收空成功。
B2 **三面入参 fail-closed**（F-47 收口）：HTTP query/body、WS `params`、MCP `arguments` 未知键
一律拒（MCP 的 `additionalProperties: False` 已在 `_tools_spec.py` 声明 9 处，改为**强制**并被判据扫得到）。
B3 **G1 三格给结论**（按 D4）：三面保留则在响应里区分"本仓结构化拦截"与"Provider 故障"，
三面摘除则同时删 capability 注册 + CLI + 文档行。两种都比现状诚实，**不允许保留"入口在、永远抛错"**。
B4 **R-2 审计转 fail-closed**：`audit_direct_bindings` 的 `warnings.warn` 改 raise；
`_is_unified_reachable` 里的 tencent/bars 特例下沉为注册表数据（`ChannelSpec` 加一个被读的字段，或改判据），
执行器里不留业务事实。
B5 **错误树对账**（F-44 另一半）：本轮实测 `SourceUnavailable` 仍零 raise，
`ProfileError/StreamError/GapUnfilledError/BackpressureOverflow/AntiSpiderBlocked/RetryAdvice` 六个叶子零 raise。
逐叶二选一：接线（有真实触发点）或删叶（同步 `docs/errors.md` 与 E 码表）。
**41 个类里留 7 个"永远不发生"的叶子不是分类学，是谎言。**

**验收**：三面的响应/入参形状有对账判据；每条破坏性变更在 CHANGELOG `[Unreleased]` 有"旧→新"迁移行；
变异证据齐（每条新判据各自红一次）。

### 阶段 V18-C｜派发显式化（架构核心，3–5 轮）—— 对应 R-1/R-3/R-4

C1 **一份声明表派生八张面**：把 `(provider, channel, capability) → 执行体 + 签名 + 三面暴露策略`
收敛为**单一数据源**（catalog 里的现有契约扩成可派生），CLI parser、HTTP 路由、WS `METHODS`、
MCP `inputSchema`、typed 契约**由它生成或校验为它的投影**。
目标：R-4 的八处手抄降到一处；§4 第一类判据整批消失。
C2 **`Client` 发现面收口**（按 D5）：三选一并落实——
(a) 维持 `__getattr__`，但把 `capabilities()` 升级为带签名/必填项的结构化发现面（推荐，破坏性小）；
(b) 生成显式方法桩（+167 个 `def`，与 v17 命名空间收敛方向相反）；
(c) 缩小动态面，只暴露 typed 契约覆盖的能力。
C3 **`tstdx/client/` 分包**（R-3）：内核侧入口留 `tstdx/client/`，协议族客户端迁 `tstdx/tdx/`（或
`tstdx/protocol/client/`），并在 README/门禁里**正面表达**双身份："TDX 协议库可独立使用；
经内核的调用一律走 `Client`"——把现在这句话只写在散文里的状态变成结构（新路径 + unimportable 守卫
+ 服务面只 import `Client`）。
C4 **豁免类别加价值判据**（R-7）：`_reach_allow.txt` 每条豁免必须写"谁在链外使用它"（真实调用方：
用户可见 API 名 + 消费它的 docs/cookbook 篇 + 测试文件），三类缺一即 `[thin]` 红；
`deprecation.py` 按 D3 处置（默认删除 + 防回潮守卫）。

**验收**：八张面与派生源的一致性由**一条**框架判据守；`tstdx/` 根级与 `client/` 包内命名空间由白名单
钉住（沿用 F-4 的机制）；删除的 curated 清单逐条列名并给"覆盖面不减"证明。

### 阶段 V18-D｜web 层收敛（体量最大，4–6 轮）—— 对应 R-5/R-6

D1 **接口事实外移**：4 270 行 docstring 里的源站事实（URL/参数/字段/失败形状）搬到
`PROTOCOL_SPEC/web/<provider>/<endpoint>.yaml`，与 TDX 帧规格同构（能被 `spec_audit` 式对账消费），
代码只留引用。目标：`web/` 的 docstring 占比从 23.8% 降到 <8%，且**机器可读**。
D2 **表驱动适配器**：把"URL + 参数拼装 + 字段映射"抽成声明式表 + 单一 fetch/normalize/parse 通路；
`adapters*.py`/`_session_*.py`/40+ 特性模块按端点表重写。
估可删 **4 000–6 000 行**（`corporate.py`+`fundflow.py`+`adapters.py` 三文件就 3 139 行）。
D3 **门面重设**（破坏性，按 D8）：`WebQuoteSession` 的 140 方法按"每源一套"重划为
每 Provider 一个显式小客户端，会话层只保留内核绑定实际调用的那 104 条能力对应的入口；
`efinance_*`/`astock_toolkit_*`/`tiantian_fund_*` 这类对标命名（ORIGINALITY 门禁 191 文件全绿，
出处标注合规）在收口后按能力名重命名，去掉"第三方库影子"。
D4 **单源边界**（承接 F-18 裁决 (b)）：`_base_http.py` 保持单源约定，文档明说"跨源边界由调用方自证"，
不再复活主机白名单。

**验收**：`web/` 行数下降幅度、每个端点的"改一处要动几个文件"计数（目标 1）、`spec_audit` 式
web 端点覆盖率、离线全量与覆盖率不降。

### 阶段 V18-E｜判据框架合并与工程卫生（2–3 轮）—— 对应 R-8/R-9/R-10/R-11/R-12

E1 落地 §4 的三处置：curated 清单→派生框架；形状枚举→单一符号存在性扫描；无变异证据的判据→删。
E2 文档面**生成而非校验**（R-12）：README 的 172/251/31/10/9、结构树、能力表由脚本从代码生成，
活文档门禁改为"重新生成后 diff 为空"（一次消掉一批抄录型判据）。
E3 冻结历史文档：DESIGN.md（161 KB）与 CHANGELOG 的既有版本段声明为**只读史料**并在文件头标注
"不反映当前实现，当前事实见 README + `Client().capabilities()`"；4 份 parity 文档在 A1 后并入
`docs/archive/` 或删除；`docs/REFACTOR_PLAN_V17_CLOSURE.md` 收口为"执行记录"并停止新增 F 号
（新账进 v18 台账）。
E4 卫生（已部分落地 `3028721`）：本地工作树目录不再污染 `git status`；
`.coverage`(1.4 MB)/`coverage.xml`(1 MB)/`__tmp_*.txt`/`sample_fail.log` 这类工作树残留纳入忽略或清出。

**验收**：判据条数与 `tests/` 行数的下降值（记录，不设 KPI——**为降而降会买到假绿**）；
文档 diff 生成物为空；DESIGN/CHANGELOG 的史料声明有门禁或至少在文件头可读到。

### 阶段 V18-F｜发布工程（必须真跑外部，2–3 轮 + 授权）—— 对应 G4/R-13/R-14

F1 **push**：本轮授权已给；把领先提交推上去，让 CI（ubuntu+py3.11）第一次复算这 76+ 个提交。
F2 **CI 读数重钉阈值**（F-15）：拿 CI exact-head 的覆盖率与跳过项重钉 `fail_under`，
阈值方向只允许"升到 CI 实测量级"或"保持"。
F3 **盘中 live 判据**（G4）：给 live-smoke 增加一个**交易时段内**的 job（开盘后，如 03:30 UTC=11:30 CST），
或把现有 cron 移到盘中并保留一个盘前格；同时补 F-38 的 7709 数据面 live 判据（今天完全空缺）。
F4 **G3 清偿**：用本轮入库的 `PROTOCOL_SPEC/_sniffer/quotation/{000f,0200,2d00}` 抓包样本**离线**重解
`0x000F`/`0x0010` 字段布局（有 `.bin` 就不必再猜，也不必等授权），产出正式 spec + golden 样本，
再由 F3 的盘中 job 复跑确认；完成后 F-37 才有裁决依据，tag 才有意义。
F5 **tag**：`v1.1.0-dev.1` —— 本轮授权已给。如实说明其含义：它标记的是"链闭合 + 九门禁绿 +
G1–G4 四格已知未清偿"的开发预发布点，不是发布候选；GitHub Release **不发布**
（`wheels.yml` 只在 `release: published` 上跑 trusted publishing，故打 tag 不会触发任何 PyPI 动作）。

**验收**：CI 全绿读数入库（带 run id）、盘中 live 判据首次产出、F-37 有 spec/golden 支撑的裁决。

### 建议执行次序

```
V18-A（1–2 轮，无破坏）
   └─→ V18-B（破坏性契约，先小面后大面）
          └─→ V18-C（派发显式化）  ←── 与 V18-D（web 收敛）可并行，但 C1 的派生表是 D3 的前置
                 └─→ V18-E（判据与文档工程化，C/D 落地后才有清单可删）
                        └─→ V18-F（发布工程；F1/F2/F3 其实可以提前，见下）
```

**唯一想建议偏离你授权顺序的地方**：V18-F 的 **F1（push）与 F2（CI 读数）建议立刻做，不等 A–E**。
理由：R-14 说这 76 个提交从未被 CI 看过；越晚推，一次性红得越难归因。
F5（tag）与 F3/F4 保持在后。

---

## 6. 决策点（D7–D12；不答即按默认执行）

| # | 问题 | 选项 | 默认 |
|---|---|---|---|
| D7 | V18-B 的破坏性幅度 | (甲) 只补失败语义、不动入口 (乙) 同时把 G1 三格从三面摘除 | **(甲)**：先让失败显形，摘面留到 C1 派生表上线后一起决策 |
| D8 | web 门面（140 方法 + `tstdx.web.__all__` 69）去留 | (a) 保留并单源化 (b) 收缩到内核实际绑定的 104 条能力 (c) 只留 Provider 客户端，删会话门面 | **(b)**：与 F-71 (c) 一致，且是 V18-D 减行的主要来源 |
| D9 | 协议库与运行时的产品身份 | (a) 一仓双身份、README 明说边界（现状+结构） (b) 拆两个发布单元（`tstdx-tdx` 协议库 / `tstdx` 运行时） | **(a)**：拆包成本高于收益，先用 C3 的分包把边界变成结构 |
| D10 | 92 项 typed 契约缺口（G2） | (a) 按业务族分 3 批补齐（约 90 份契约） (b) 承认 typed 面只覆盖核心族，把口径与判据一起改 (c) 维持 PENDING 不阻断 | **(b)**：(a) 是为覆盖率买代码，(c) 是把已知缺口藏成噪声 |
| D11 | 判据收缩的量化目标 | (a) 无目标，只按 §4 三类逐条处置 (b) 定"架构判据 126→≤60、`tests/` 行数 −20%" | **(a)**：定数字就会为凑数字删有价值的判据 |
| D12 | V18 是否沿用 F 号/步骤号 | (a) 本方案 R-# 自编号，落地时领号 (b) 立刻在 v17 台账为 R-9② 等新账开 F 号 | **(a)**：并行会话正在写台账，争号必撞 |

---

## 7. 本轮取证日志索引（全部本轮产生，可回查）

| 日志 | 内容 |
|---|---|
| `%TEMP%/v18rev_test.log` | 隔离树离线全量：`[100%]`、`PYTEST_RC=0`、覆盖率 81.42%、G3 的 E3040 解码告警原文 |
| `%TEMP%/v18rev_fast.log` | ruff check/format、spec_audit、reachability、originality、docs links 的 RC 与摘要行 |
| `%TEMP%/v18rev_mypy.log` | mypy CI 参数，0 行输出 / RC=0 |
| `%TEMP%/v18rev_contract.txt` | `contract_audit`：63 契约 / 155 capability / 92 PENDING / RC=0 |
| 工作树 `wt_v18review` | detached @ `616d065`；本方案取证用，未改动其内容 |

## 8. 本方案不做什么（避免被读成"又要一轮无限重构"）

- 不新增执行接缝、不复活缓存、不引入跨源自动降级（v16/v17 前置决策继续有效）。
- 不为让门禁变绿而删测试或降阈值（§4 的三条红线是删判据的前置条件）。
- 不重写 TDX 协议层：44 项规格 100% 覆盖、85 条命令账本、191 文件 originality 全绿，**它是这个项目最健康的一层**，重构预算不给它。
- 不猜协议字节：G3 的解法是本轮已入库的 `.bin` 样本 + 离线重放，猜与试都不如把样本读干净。
