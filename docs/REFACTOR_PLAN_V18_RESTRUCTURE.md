# tstdx v18 结构收口与对外契约重设方案

> **文档状态**：**执行中**——§5 的 V18-A3、V18-B1、V18-B4 已落地（第 1 轮，提交 `94b97d6`，
> 读数见 §9），B5 与 G1 前半截经实测作废/已满足（更正就地写在 §5）；其余阶段仍待按 §6 的 D7–D12 裁决推进。
> §0 的基线是**方案取证轮**的数，§9 是**执行轮**的数，两者环境标签相同（3.12.13）但不混用。
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
> **已落地（V18 第 1 轮，`94b97d6`）**：批量 `quotes` 的逐只失败不再静默吞掉——全败直接抛第一手异常
> （内核补 `context`），部分失败以新声明的 `WarningCode.QUOTES_PARTIAL_FAILURE` 随结果携带，
> `options["strict"]` 能把它升级成 `TruncatedDataError`。旧账 ≈F-72（并行会话登记，此处只引用不重复领号）。
> 替掉的是 `last_errors` 侧信道：夹具用 `_collect=None` 即判红，防止退回"错误装在另一个对象里"。
> 顺带把只读这个侧信道的一处 `warnings.warn` 消费者（`error_envelope`）里从未被写入的 `terminal` 分支删除，
> 并把查询级 deadline 的"不许同 Provider 重试"从建议升级为写进 `context` 的决策。
B2 **三面入参 fail-closed**（F-47 收口）：HTTP query/body、WS `params`、MCP `arguments` 未知键
一律拒（MCP 的 `additionalProperties: False` 已在 `_tools_spec.py` 声明 9 处，改为**强制**并被判据扫得到）。
> **作废更正（V18 第 2 轮复核）**：这条在本方案写就之前**已经整体落地**——提交 `b52a122`
> "refuse undeclared request fields on all three wire faces (F-47)"。当前磁盘事实：
> 拒绝口只有一处（`tstdx/integration/wire_fields.py:59` `reject_undeclared`），
> HTTP 查询串用路由签名当白名单（`runtime_http.py:41-52`）、HTTP body 与 WS `params` 用
> `QUERY_BODY_FIELDS`/`WS_PARAMS_FIELDS`（`wire_fields.py:34,40`）、MCP 用它自己的
> `inputSchema.properties`（`mcp/_server.py:228`，注释就写着"只写进 schema 而不执行＝承诺一个不会发生的行为"）。
> 判据也已存在：`tests/runtime/test_wire_declared_fields.py` 12 格，含"三面必须走同一个拒绝口"
> 与逐工具/逐方法的接受-拒绝行为表。**B2 从方案里删除，不是延后。**
B3 **G1 三格给结论**（按 D4）：三面保留则在响应里区分"本仓结构化拦截"与"Provider 故障"，
三面摘除则同时删 capability 注册 + CLI + 文档行。两种都比现状诚实，**不允许保留"入口在、永远抛错"**。
> **落地复核（V18 第 1 轮，`94b97d6`）**：这条的"前半截"其实早已成立，本项收敛为只做摘面决策。
> `CommandOffline`(`E3035`, `errors.py:304`) 与 `NotImplementedFeature`(`E9010`, `errors.py:503`) 各自带
> `http_status = 501`，经 `error_envelope.http_status_for()` 直达 `runtime_http.py:62` 的响应状态码，
> 且被 `tests/errors/test_taxonomy.py:163,176` 两格钉住——"本仓结构化拦截"与"Provider 故障(5xx/4xx)"
> 在 HTTP 面上已经是两个数。故 B3 剩余工作只有 D7 的那一问：这三格入口留还是摘。
B4 **R-2 审计转 fail-closed**：`audit_direct_bindings` 的 `warnings.warn` 改 raise；
`_is_unified_reachable` 里的 tencent/bars 特例下沉为注册表数据（`ChannelSpec` 加一个被读的字段，或改判据），
执行器里不留业务事实。
> **已落地（V18 第 1 轮，`94b97d6`）**：审计改为 `raise RuntimeError`（缺执行元数据的可达三元组不再只是
> 一条被 pytest 收走就完事的 warning）；可达性谓词改由注册表自己的 `channel.periods` 推导，
> 硬编码的 `tencent → {kline, minute_kline}` 影子规则删除——234 条迁移派生绑定逐条比对**零差异**，
> 即删除是行为保持的。新增 AST 判据 `test_binding_audit_holds_no_provider_business_facts`
> 钉住"这两段函数体里不许再出现任何 Provider 名或 capability 名"，并有正向对照（重新塞回特例即红）。
B5 **错误树对账**（F-44 另一半）：~~本轮实测 `SourceUnavailable` 仍零 raise，
`ProfileError/StreamError/GapUnfilledError/BackpressureOverflow/AntiSpiderBlocked/RetryAdvice` 六个叶子零 raise。~~
逐叶二选一：接线（有真实触发点）或删叶（同步 `docs/errors.md` 与 E 码表）。
> **作废更正（V18 第 1 轮，AST 逐文件计数）**：上面那行数字是抄来的旧账，实测**不成立**——
> `SourceUnavailable` 已从代码里整类消失（F-68 那一轮删的就是它）；
> `GapUnfilledError`(`streaming/base.py:156`)、`BackpressureOverflow`(`:186`)、
> `AntiSpiderBlocked`(`web/_base_core.py:141,244`) 各有真实构造点；
> `RetryAdvice` 构造 26 次；`ProfileError`/`StreamError` 零构造是因为它们是**基类**
> （`StreamError` 是前两叶的父类，`ProfileError` 的子类 `ProfileUndetectable` 在
> `profile/detect.py:596,739` 与 `reader/profile.py:396` 三处 raise）。
> 于是"41 个类里留 7 个永远不发生的叶子"这句判词随之撤回：**它骂的是一批不存在的叶子**。
> B5 剩下的唯一有效问题是 F-44 的另一半——`docs/errors.md`/E 码表与 `errors.py` 的 E 码是否逐格对上，
> 归入 V18-E 的"生成而非校验"一起做，不再单列。


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
| D8 | web 门面（140 方法 + `tstdx.web.__all__` 69）去留 | ~~(b) 收缩到内核实际绑定的 104 条能力~~ **(已撤回，见 §10)**：内核触达 140 里的 137 个方法，"收缩"买不到减行只会破坏绑定 | 剩下的真实选项：(a) 保留并单源化、(c) 只留 Provider 客户端删会话门面、或 (d) 保留门面但把 137 个方法与 Provider 的对应关系**做成表**（与 D1 的单位口径外移同一批）。默认改为**待重设**，不沿用 (b) |
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
| `wt_v18b1step/gates_v18b1.log` | 第 1 轮步骤轮九门禁逐条 RC 与摘要行 |
| `wt_v18b1step/fulltest_v18b1.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b1step/mutate_v18_round1.log` | 第 1 轮 8 条反证变异：每条 `rc=1` 且 `restored=True` |
| `wt_v18b1ship/gates_ship.log` | **提交树**（`94b97d6`，`dirty=0`）九门禁复测 |
| `wt_v18b1ship/fulltest_ship.log` + `.xml` + `collect_ship.txt` | 提交树离线全量、junit 与 `--collect-only` 规模 |

## 8. 本方案不做什么（避免被读成"又要一轮无限重构"）

- 不新增执行接缝、不复活缓存、不引入跨源自动降级（v16/v17 前置决策继续有效）。
- 不为让门禁变绿而删测试或降阈值（§4 的三条红线是删判据的前置条件）。
- 不重写 TDX 协议层：44 项规格 100% 覆盖、85 条命令账本、191 文件 originality 全绿，**它是这个项目最健康的一层**，重构预算不给它。
- 不猜协议字节：G3 的解法是本轮已入库的 `.bin` 样本 + 离线重放，猜与试都不如把样本读干净。
  **第 2 轮复核给这句加上边界**：仓内现有样本读不出这两条——0x000F 的三份实采给出互斥步长，
  0x0010 只能锁"每条 143 B"这一层结构、锁不出字段语义（详见 §10 G3 行）。因此 F4 的
  "有 `.bin` 就不必再猜"是**过度承诺**，真要锁必须新采集，而新采集要显式授权。

---

## 9. 执行记录

### 第 1 轮｜V18-B1 + V18-B4 + V18-A3 + R-9 的判据扩形（提交 `94b97d6`，17 个文件）

按 §5 的阶段序执行了 V18-B 的两格与 V18-A3，并按 §4 第二类的处置口径把"形状枚举型死引用扫描"
**改写**为按成员存在性判定（不是再加一条正则）。本轮不领 F 号也不领步骤号（D12 默认 (a)）；
v17 台账的登记等并行会话那两笔未提交改动落盘后一并做，`CHANGELOG` 的"旧→新"迁移行同一批写。

| 轮 | 树 | 九项确定性门禁 | 离线全量 | 覆盖率（阈值 77 未动） |
|---|---|---|---|---|
| 步骤轮 | `wt_v18b1step`（detached @ `f990ae6` + 本步 17 文件，与工作树 md5 逐格相同） | **G1–G9 全部 rc=0** | `PYTEST_RC=0`；junit 3611 / 0 失败 / 0 错误 / 7 跳过，119.3s | **81.46%**（TOTAL 22413 stmt / 3581 miss / 5984 branch / 1002 partial） |
| ship 轮（提交树复测） | `wt_v18b1ship`（detached @ `94b97d6`，`dirty=0`） | **G1–G9 全部 rc=0**（`gates_ship.log`，与步骤轮逐格同判据） | junit 3611 / 0 失败 / 0 错误 / 7 跳过，159.8s；`--collect-only` **3611 项 / 229 个含测试文件**（基线 3598/228 ⇒ 净 +13 项 / +1 文件，无删除） | **81.48%**（TOTAL 22413 / 3578 / 5984 / 1000） |

两轮同一环境标签：仓库 `.venv` = **cpython-3.12.13**、主机 `WIN-PM`。按 R-13，这个读数与并行会话
3.13.12 的读数**不可互换**，也**不能**用来重钉阈值（阈值重钉只认 CI ubuntu+py3.11 的 exact-head 读数）。

提交树九门禁的分项读数：originality `Total: 191 / Original: 191 / License OK: 191 / Header OK: 191 /
Suspicious: 0`；spec_audit `total_specs 44 / coverage_pct 100.0`；golden_audit
`[GATE] all L1 verified commands have real samples (OK)`（既有 `suspect_short` WARN 1 条：`0x537 4B<12B x3`）；
reachability `模块总数 190 / 可达 174 / 白名单豁免 16 / 无未登记孤儿 ✓`；contract_audit RC=0；
docs links `OK (83 files)`（基线 82，多的那一格是本方案文档自身入库）；mypy（CI 参数）0 行输出 RC=0；
ruff check `All checks passed!`；ruff format `443 files already formatted`。

**反证证据（§4 第三类：一条判据必须能指出"删掉哪一行生产代码它会红"）**——8 条变异全部 `rc=1`、
`restored=True`（`wt_v18b1step/mutate_v18_round1.log`）：

| 变异 | 删/改的生产代码 | 被谁抓红 |
|---|---|---|
| M1 | `_tdx_quotes` 全败不再 raise，改回返回空袋 | `test_tdx_quotes_failure.py` 两格（全败、单标的）**同时红** |
| M2 | 部分失败不再 `record_warning` | 部分失败携带 + `strict` 拒收两格红 |
| M3 | 绑定审计 `raise` 退回 `warnings.warn` | `test_binding_audit_fails_closed_instead_of_warning`（DID NOT RAISE） |
| M4 | 审计谓词里重新塞回 `tencent`/`bars` 字面量 | `test_binding_audit_holds_no_provider_business_facts` |
| M5 | 查询 deadline 不再写 `retry_same_provider: False` 决策 | `test_exhausted_query_deadline_is_never_advertised_as_retryable` |
| M6 | 文档重新引用 `client.router.last_errors_never_written()` | `test_backticked_call_chains_name_real_members` |
| M7 | docstring 重新引用 `:class:` 不存在符号 | `test_sphinx_roles_in_package_docstrings_resolve` |
| M8 | `per-file-ignores` 指向不存在的路径 | `test_ruff_per_file_ignores_still_point_at_existing_paths` |

M4/M8 还各带一条**正向对照自检**（`test_the_provider_fact_ruler_sees_a_reintroduced_special_case`、
以及成员链判据自带的 `scanned > 10` / `refs > 20` / 豁免清单非空三类"尺子必须真的看见过"断言），
用来防"判据在空转"这一族（F-26/F-27/F-28/F-63① 的老根）。

**顺带清掉的幻影引用**（新尺子上线当轮即抓出，全部实测不存在）：
`tstdx/web/sources.py` 的 `:mod:`tstdx.sources.router``（该模块早在 v16 前置决策里被路由删除取代）、
`tstdx/error_envelope.py` 的 `tstdx.failure.FailureDisposition`、`tstdx/web/fund_rank.py:38` 的
`EastmoneyIpoAuditSource`（真名 `EastmoneyIpoSource`）、`docs/migration/README.md` 的
`tstdx.facade.quote_api()`、`docs/troubleshooting.md` §3 的 `client.router.last_errors()` 与
"7 个 Web 源 / 触发限流后自动切换下一源 / 连续失败达阈值抛 `SourceDeprecated`"三处假事实。

**本轮明确未做**（是排期不是遗漏）：B2 三面入参 fail-closed、B3 的摘面决策（D7）、
A1/A2（= 并行会话步骤 50/51 的 F-70(b)/F-71(c)）、C/D/E/F 全部阶段、G2 的 D10(b) 口径改写。
§5 的 B5 与 G1 前半截经实测**作废/已满足**，更正记录就地写在 §5 对应条目下。

---

## 10. 前提复核对账（第 2 轮：把 §0/§3 的每个数字重新量一遍）

第 1 轮连中三笔"抄来自并行台账却与磁盘不符"的前提（B2/B5/G1 前半）。本轮遂对全部分诊断重做取证，
结论如下——**每条都注明"仍成立/前提已变/前提本就不成立"**，因为方案里错的那半比对的那半更贵：
执行者会照着错的去做，做完才发现买的是已经有的东西。

| 条目 | 复核结论 | 本轮实测 | 对方案的影响 |
|---|---|---|---|
| R-1 派发形状 | **仍成立，但机制描述错了** | `DIRECT_BINDINGS` 251 条 = 234 `_migrated_capability` + 17 专属（`Counter` 当场数）；但绑定表**由注册表三元组推导**，不是手抄清单 | "93.2% 走反射派发"照旧；"八张表手工同步"里内核侧那两张其实已经派生 |
| R-2 审计 fail-open | **已清偿** | `audit_direct_bindings` 现在 raise（`executor.py:110-137`）；`tstdx/runtime/` 剩下的 warn 只有两格且都是用户数据瑕疵（`freshness.py:78` 的 `CURRENTNESS_UNPROVEN`、`executor.py:533` 的 `QUOTES_PARTIAL_FAILURE`） | 第 1 轮已做，工程缺陷方向再无软失败 |
| R-3 `client/` 一名两职 | **成立，但行数抄错一个量级** | `core.py` 332 + `sync.py` 541 + `async_.py` 440 + `_mixin.py` 955 = **2 268 行**（原文的"955 行"只是 `_mixin.py` 一个文件）；"协议客户端可脱离内核"这一边界**没有任何守卫或文档判据**，只有 `tests/client/*` 事实上这么用 | C3 的诉求更强（边界连事实描述都没有），但预算要按 2 268 行算 |
| R-4 八张表 | **夸大：实际是四张外表面** | 注册表 `channel.capabilities` / typed 契约 / CLI parser / HTTP 路由 / WS `METHODS` / MCP `inputSchema` 六处仍手写；`DIRECT_BINDINGS` 与 capability catalog 已由注册表派生 | C1 的靶子从"八合一"收窄成"四面投影自一份契约"，工作量下降一半以上 |
| R-5/R-6 web 体量 | **docstring 数字低估；"改一处动多文件"不成立** | `web/` 17 915 行 / 51 文件 / 占包 30.3%；docstring **4 606 行 = 25.7%**（原文 4 270/23.8%），全包 docstring 占比 17.8% 作对照；实测改一个查询参数（东财 `fields`、新浪资金流 `page/num/sort`）只动 **1 处代码**，重复的是**单位口径的散文**（`sources.py:181-191`、`normalize.py:13`、`adapters.py:20`） | D1 从"消灭多文件同步"改写成"单位口径外移成表，散文改引用"；D2 的"表驱动重写 4 000–6 000 行"失去依据 |
| R-6 会话门面 140 方法 | **数字成立，结论反了** | `WebQuoteSession` 公开方法确为 140、`tstdx.web.__all__` 确为 69；但内核经 `MIGRATED_BINDINGS` 的 `backend="web_session"` 共 192 格，实际触达 **140 个方法里的 137 个**（只有 `close`/`minute`/`quotes` 不触） | **D8 默认 (b)"收缩到内核绑定的 104 条"作废**——按现证据门面几乎全量在用，收缩买不到减行，只会破坏绑定 |
| R-7 豁免面 | **已受门禁，只差指针核验** | `audit_reachability.py` 已强制 ≥40 字符理由、死记录、过期记录、重复条目；**未**校验理由里点名的消费者是否存在 | 本轮补 `[dead-pointer]`/`[no-pointer]` 两格（§11 第 2 轮）；C4 剩下的"每条豁免写清调用方"其实早就写了 |
| A1 / E2 README 数字 | **前提不成立：五个数字全对，且已有生成式判据** | 当场对账：`172` = `len(Client().capabilities())`、`251` = `len(DIRECT_BINDINGS)`、CLI `31`、HTTP `10`、MCP `9` —— 与 README:23/45/53 一致，零差异；`test_doc_code_consistency.py:467-480` 已经是"从运行期再生成一遍再比"的形状 | E2 从"要生成"降级为"把再生成的覆盖面从 README 数字扩到结构树与 `docs/` 散文数字"；DESIGN.md（161 548 B，无史料声明）与 CHANGELOG（263 627 B，无史料声明）那半仍成立 |
| G2 typed 缺口 | **仍成立** | `contract_audit`：63 契约 / 155 capability / 92 PENDING / 0 ERROR，RC=0 不阻断 | D10 的三选一仍待裁决 |
| G3 两把未锁的钥匙 | **0x0010 结构可锁、语义不可；0x000F 连结构都锁不上** | 0x0010 四份实采都是 `count=100` + body 14 300 B = **每条 143 B 整除**（4/4，跨两只标的两天）；0x000F 三份实采给出 9.000 / 54.820 / 164.569 三种互斥步长，且 26 333 B 那份里能数出 11 个不同股票代码，请求的 600000/000001 反而各出现 0 次 | §8"不猜协议字节"要补一句：**仓库里现有样本不足以锁定它们**，F4 的"有 `.bin` 就不必再猜"是过度承诺 |
| 语料库健康度（新增一格） | **§0 从没量过** | `tests/golden/` 530 份 `payload.bin`（60 real / 470 synthetic）里只有 **216 份不同字节**：54 组逐字节相同，克隆多出 **314 份**（最大一组 24 份同为 0x052D）；11 组成员横跨 real 与 synthetic，即"十几个不同代码的 `_syn_` 目录共用同一条实采字节" | 已在 `golden_audit` 里补成公开读数（只报不判，L1 门槛一字未动）；"我们有 530 份样本"这句话现在自带它的对照读法 |

> 附带清掉的一处假事实：`tstdx/web/session.py` 的会话 docstring 自称"底层复用 `tstdx.web` 的
> 零依赖适配器**与降级逻辑**"，而按并行台账 F-71 第 48 步自己的构造点普查，会话按构造就是单源
> （`_c` 只 `create_source(self.source_name)`，11 个 `_session_*.py` 对三个换源符号零引用）。
> 本轮把那句半真话改成与模块头一致的口径。它原本"没有任何判据在看"（F-71 ⑤ 语），
> 把判据扩到 `tstdx/web/__init__.py` 的 docstring 属于 F-71 (a)/(b)/(c) 裁决的一部分，留给那一格，不在此抢。

---

## 11. 执行记录（续）

### 第 2 轮｜把"证据本身"变成被门禁的对象（5 个文件）

§10 的复核没有停在"改文案"：复核对账暴露出的两类**不能被反驳的声明**各自补了一格可核验判据。

| 改动 | 内容 |
|---|---|
| `scripts/audit_reachability.py` | 豁免理由里的仓内路径指针变成被校验的声明：`[dead-pointer]`（点名的 tests/docs 已经不存在）与 `[no-pointer]`（整条理由举不出一个路径）都算记录缺陷，与孤儿同权重在 `--strict` 下失败。此前只查长度（≥40 字符），于是"tests/output_was_renamed/ 消费"这种句子可以一直绿着 |
| `tests/architecture/test_reachability_allowlist.py` | 两格新缺陷各自的反证用例 + 一格**自检**：真实清单的 16 条理由必须每条拿得出至少一个活指针（否则上面两判据可能在空转）。`LONG_REASON` 样本改为带真实路径，避免邻格被新规则顺带判红 |
| `tstdx/tools/golden_audit.py` | 新增 `payload_clones`（sha256 分组，`_payload_clones()`）与报告的 `[INFO] payload clones` 行。**只报不判**：L1 门槛、`suspect_short`、market/kline 追加门禁一字未动，克隆再多门禁照旧只看"有没有有效 real 样本" |
| `tests/unit/test_golden_audit.py` | `TestPayloadClones` 三格：同字节分组并按 origin 分堆、九份克隆不改门禁退出码、全异字节不成组 |
| `tstdx/web/session.py` | 会话 docstring 的"与降级逻辑"半真话删除，改为与模块头同口径（单源、源不可用即失败），并点名多源切换只在内核显式 `FallbackPolicy` 那一层 |

**步骤轮**（`wt_v18b2step`，detached @ `0a13d9f` + 本步 5 文件，md5 与工作树逐格相同）：
九项确定性门禁 **G1–G9 全部 rc=0**（`gates_v18b2.log`）；离线全量 `PYTEST_RC=0`、
junit **3617 项 / 0 失败 / 0 错误 / 7 跳过**、154.7s；覆盖率 **81.49%**
（TOTAL 22 433 stmt / 3 580 miss / 5 994 branch / 1 001 partial），`Required 77.0% reached`，
**阈值未动**。判据规模 3611 → **3617（+6）**，无删除。环境标签同第 1 轮：`.venv` cpython-3.12.13、`WIN-PM`。

**反证证据**（`wt_v18b2step/mutate_v18_round2.log`，4 条全部 `rc=1` 且 `restored=True`）：

| 变异 | 动了什么 | 结果 |
|---|---|---|
| N1 | 指针核验整体摘除 | 三条用例同时红（含 `[dead-pointer]`、`[no-pointer]` 两格与 dup 邻格——dup 用例正是靠"只有 dup 一条缺陷"来证明新规则没有乱判） |
| N2 | 真实清单里把 `tests/output/` 改写成不存在的目录名 | `test_real_allowlist_records_are_well_formed` 与 `--strict` 门禁用例双红：证明新规则**读的是当前磁盘**，不是写死的期望 |
| N3 | 克隆普查不再算摘要（digest 恒空） | 分组用例与"九份克隆不改门禁"用例双红 |
| N4 | 分组不再要求 ≥2 份（单份也算克隆） | 分组用例与"全异字节不成组"用例双红 |

**本轮明确未做**：C1/C2/C3、D1（按 §10 改写后的口径）、E1/E3/E4、F1–F5、G2 的 D10 裁决、
以及 F-70(b)/F-71(c)（并行会话名下）。D8 默认 (b) 依 §10 证据**撤回**，改为待用户重设。



