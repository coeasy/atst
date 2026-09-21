# tstdx v18 结构收口与对外契约重设方案

> **文档状态**：**执行中**——§5 的 V18-A3、V18-B1、V18-B4 已落地（第 1 轮，提交 `94b97d6`，
> 读数见 §9），B5 与 G1 前半截经实测作废/已满足（更正就地写在 §5）；第 2 轮把"证据本身"
> 变成被门禁的对象（提交 `d1bd446`，读数见 §11），第 3 轮把"文档承诺 ↔ 代码兑现"这条轴上的
> 幻影旋钮与手抄计数改成行为与判据（提交 `0a4a232`，读数见 §12，并撤回 C2(a)/D5、新登记 G5）；
> 第 4 轮把同一根轴补到命令行面（`--flag` 注册了必须有人读），并当场反掉自己上一轮写下的
> 过度承诺（提交 `7fe7e54`，读数见 §13）；第 5 轮把它补到机器对机器的声明面（HTTP 路由形参与
> MCP `inputSchema` 属性声明了必须有人读），新尺子第一件事就抓到一条教人吃 `ModuleNotFoundError`
> 的部署命令（提交 `788e533`，读数见 §14）；第 6 轮把同一把尺子量到**判据自己的台账**上
> （V18-C4：可达性豁免理由点名的公共 API 名必须真在模块里），当场修掉台账里 5 处过期写法——
> 点到不存在的符号 2 处、"等 N 个"这类核不了的计数 3 处（提交 `b4c3bc8`，读数见 §15）；
> 第 7 轮把同一把尺子量到**判据自己赖以放行的那份例外名单**上（G2 按 D10(b) 落地：注册表覆盖
> 改由三个派生来源对账，"既无契约也不在任何派发面"从 PENDING 升为 ERROR），撤掉一份 17 个名字的
> 手抄豁免名单与一条 63 项的死名单，并就地更正 §2 的诊断——所谓"92 项缺契约"里有 92 项其实
> 全在通用派发面上，真·无声明形状的是 0 项（代码 `a98755d`+`e26300e`+`c7db30b`，读数见 §16）；
> 第 8 轮收掉 C4 的后半截（V18-D3）：包内零消费者的 206 行退役机制 `tstdx/deprecation.py` 物理删除，
> 防回潮不新建判据、由四道既有尺子分头盯住（根级白名单 / 可达性 `[ORPHAN]` / 事实文档可解析 /
> 换形后的贯通第 16 项），并顺手把治理文档教的 `@deprecated` 与审计索引教的死 make 目标改成实话
> （代码 `f92d827` + 文档 `cb92bc0`，读数见 §17）；
> 其余阶段仍待按 §6 的 D7–D12 裁决推进。
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
| 契约对账 `contract_audit` | **63 个 Typed 契约 / 155 个注册业务 capability / 92 项 PENDING / 0 ERROR**，RC=0（PENDING 不阻断）。⚠️ 该读数已被第 7 轮更正为**口径产物**：92 项全部在通用派发面上，真缺口 0 项，见 §16 | `scripts/contract_audit.py` |
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

**核心功能全部实现：否。** 缺口逐格都可量化，不是感觉（第 3 轮又量出一格，故不写死份数）：

| # | 缺口 | 实测证据 | 严重度 |
|---|---|---|---|
| G1 | **三格能力在任何 Provider 上都不可能给数**，但仍占着 HTTP/WS/MCP 三面入口 | `minute`(0x0537)/`trades`(0x0FC5) 在 `tstdx/client/core.py:288,314,326` 恒定 `raise NotImplementedFeature`；`security_list`(0x044D) 在 `core.py:300` 恒定 `raise CommandOffline` | P1（对外承诺形状） |
| G2 | **typed 面只覆盖 40%**：155 个业务 capability 里 92 个无契约，`contract_audit` 判 PENDING 且 RC=0 不阻断。<br>**更正（第 7 轮实测，见 §16）**：这一格诊断错了一半——92 个"无契约"**全部**在通用派发面上（迁移网关或内核专属执行体），真·无任何声明形状的是 **0 个**。所以"缺 92 份契约"不是功能缺口而是口径产物；已按 D10(b) 把判据换成派生三来源 + 规模下限，缺形状改 ERROR 阻断 | 本轮 `scripts/contract_audit.py`：`63 契约 / 155 capability / WARN: 92 个待办`；更正读数：`172 注册 / 63 专属契约 / 172 在派发面 / 0 无形状` | P1（功能账，非缺陷）→ **口径账，已改** |
| G3 | **`0x000F` 资本变动 / `0x0010` 财务在新握手下仍解错**，F-37 余条未裁决；本轮离线日志里就能看到 `[E3040] 股权信息记录数异常: count=300, body=30, size=0` 的解码告警 | 隔离树测试日志 `v18rev_test.log` 的 warnings summary 段（`tests/client/test_decode_caveat_wiring.py` 现场复现） | P0（数据正确性） |
| G4 | **7709 数据面从未在交易时段被验证过**：CI 的 live-smoke 是 `cron '0 1 * * *'` UTC = 北京 09:00，注释自己写着 "before A-share trading"（9:30 开盘），host-audit 是周三 09:00 UTC = 北京 17:00（收盘后） | `.github/workflows/live-smoke.yml:9`、`.github/workflows/host-audit.yml:5` | P1（验证盲区，F-38 的真正根因） |
| G5 | **`fund_estimate` 是 G1 的同族、Provider 侧的那一格**（第 3 轮登记、第 4 轮按代码更正口径）：能力面仍声明它，而实现只在"站点回 404/页面未找到 HTML"这一实测形状上抛 `SourceDeprecated`——它**仍会先发一次真实请求**，端点复活就会重新返回 dict。调用方要读源码才知道这一格现网不给数 | `tstdx/web/sources.py:449` 与 `tstdx/providers/__init__.py:570` 仍列该能力，`tstdx/cli/runtime_commands.py:681` 仍可从 CLI 抵达，实现链是 `tstdx/web/_session_baidu.py:108` → `tstdx/web/adapters_fund.py:188`（该函数先 `_request_text`，命中 404 页才抛，其余响应走 `_parse_jsonp` 返回 dict） | P2（对外承诺形状，与 F-66/F-75 同批裁决） |

**结论口径建议统一成这句**（写进 README 与 F-37 裁决记录，避免每轮重新解释）：
> 链是通的，声明与执行是闭合的；G1/G5 是"实现了但选择不给数/不给类型"的登记账，
> G2 经第 7 轮更正后是**口径账**（已改判据，无待补契约），
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
除自身外 0 命中，也不在 `__all__` 45 个名字里，≈F-74；**第 8 轮已按 D3(a) 删除，见 §17**）；`profile/`(1 262) `output/`(305)
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
(a) ~~维持 `__getattr__`，但把 `capabilities()` 升级为带签名/必填项的结构化发现面（推荐，破坏性小）~~
　**第 3 轮撤回**：F-66 已由用户拍板 (c)「不改面、只补判据」，第 47 步更把"发现面不许长出状态字段"钉成门禁，
　(a) 恰是那条判据判红的形状——重做已裁决格属越权，见 §12 撤回段；
(b) 生成显式方法桩（+167 个 `def`，与 v17 命名空间收敛方向相反）；
(c) 缩小动态面，只暴露 typed 契约覆盖的能力。
C3 **`tstdx/client/` 分包**（R-3）：内核侧入口留 `tstdx/client/`，协议族客户端迁 `tstdx/tdx/`（或
`tstdx/protocol/client/`），并在 README/门禁里**正面表达**双身份："TDX 协议库可独立使用；
经内核的调用一律走 `Client`"——把现在这句话只写在散文里的状态变成结构（新路径 + unimportable 守卫
+ 服务面只 import `Client`）。
C4 **豁免类别加价值判据**（R-7）：`_reach_allow.txt` 每条豁免必须写"谁在链外使用它"（真实调用方：
用户可见 API 名 + 消费它的 docs/cookbook 篇 + 测试文件），三类缺一即 `[thin]` 红；
`deprecation.py` 按 D3 处置（默认删除 + 防回潮守卫）。
> **第 6 轮落地口径更正**：前半截按**派生判据**而非"三类缺一"的形状判据落地——
> "三类缺一即 `[thin]`"要求先定义"哪一段算 API 名 / 哪一段算 docs 篇"，那正是 §4 第二类
> （形状枚举，换个写法就复活）要删的东西。实际写进 CI 的是：反引号点名的符号必须在被豁免模块的
> 静态命名空间里（`[dead-claim]`），全清单点名总数低于 30 即判据自报失明（`[blind-claims]`）；
> "消费它的文件"那一维早就由 `[dead-pointer]`/`[weak-pointer]` 盯住（路径存在且真触达），不重复建。
> 读数与 5 处过期写法的修正见 §15；`deprecation.py` 的 D3 处置在第 8 轮按默认 (a) 落地，见 §17。

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
| D10 | 92 项 typed 契约缺口（G2） | (a) 按业务族分 3 批补齐（约 90 份契约） (b) 承认 typed 面只覆盖核心族，把口径与判据一起改 (c) 维持 PENDING 不阻断 | **(b)**：(a) 是为覆盖率买代码，(c) 是把已知缺口藏成噪声。**第 7 轮已按 (b) 落地**（派生三来源 + 下限 + 缺形状 ERROR，见 §16）；"要不要为核心族之外补专属契约"从此是产品选择，不再是门禁里的 92 行噪声 |
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
| `wt_v18b2step/gates_v18b2.log` | 第 2 轮步骤轮（`0a13d9f` + 本步 5 文件）九门禁逐条 RC 与摘要行 |
| `wt_v18b2step/fulltest_v18b2.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b2step/mutate_v18_round2.py` + `.log` | 第 2 轮 4 条反证变异（N1–N4）：每条 `rc=1` 且 `restored=True` |
| `wt_v18b2ship/gates_ship.log` | **提交树**（`d1bd446`，`dirty=0`）九门禁复测 |
| `wt_v18b2ship/fulltest_ship.log` + `.xml` | 提交树离线全量与 junit 计数；克隆普查读数与步骤轮逐字相同 |
| `wt_v18b3step/gates_v18b3.log` | 第 3 轮步骤轮（`66a7b13` + 本步 13 文件）九门禁逐条 RC 与摘要行 |
| `wt_v18b3step/fulltest_v18b3.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b3step/mutate_v18b3.py` + `.log` | 第 3 轮 7 条反证变异（M1–M7）：每条 `rc=1`、`turned_red=True` 且 `restored=True` |
| `wt_v18b3ship/gates_ship.log` | **提交树**（`0a4a232`，`dirty=0`）九门禁复测 |
| `wt_v18b3ship/fulltest_ship.log` + `.xml` | 提交树离线全量与 junit 计数；与步骤轮逐格相同 |
| `wt_v18b4step/gates_v18b4.log` | 第 4 轮步骤轮（`10a4488` + 本步 2 文件）九门禁逐条 RC 与摘要行 |
| `wt_v18b4step/fulltest_v18b4.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b4step/mutate_v18b4.py` + `.log` | 第 4 轮 5 条反证变异（K1–K5）：每条 `rc=1`、`turned_red=True` 且 `restored=True` |
| `wt_v18b4step/probe_never_returns.py`、`probe_rerun.log` | 第 4 轮四个判据候选（A/B/C/D）+ CLI 严格口径的量法与实测计数；"量过但不立"的凭据 |
| `wt_v18b4ship/gates_ship.log` | **提交树**（`7fe7e54`，`dirty=0`）九门禁复测 |
| `wt_v18b4ship/fulltest_ship.log` + `.xml` | 提交树离线全量与 junit 计数；与步骤轮逐格相同 |
| `wt_v18b5step/run_gates.sh`、`gates_v18b5.log` | 第 5 轮步骤轮（`9384796` + 本步 3 文件）环境与九门禁逐条 RC 与摘要行 |
| `wt_v18b5step/fulltest_v18b5.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b5step/fulltest_v18b5_rerun.log` | **同一棵树、同一批文件的第三次全量**：用于把 −0.01pp 钉成计时抖动而非退化（§14 末段） |
| `wt_v18b5step/mutate_v18b5.py` + `.log` | 第 5 轮 7 条反证变异（L1–L7）：每条 `turned_red=True` 且 `restored=True` |
| `wt_v18b5step/probe_{g,h,i,j}.py`、`probe_readings.log`、`probe_readings_step.log` | 判据五取证 + 三个"量过但不立"的候选（H1/H2/I）计数；前者在 main 工作树跑、后者在步骤树内重跑 |
| `wt_v18b5ship/run_gates.sh`、`gates_ship.log` | **提交树**（`788e533`，被跟踪文件 0 改动）九门禁复测 |
| `wt_v18b5ship/fulltest_ship.log` + `.xml` | 提交树离线全量与 junit 计数；与步骤轮逐格相同 |
| `wt_v18b6step/run_gates.sh`、`gates_v18b6.log` | 第 6 轮步骤轮（`7420d9d` + 本步 3 文件）环境与九门禁逐条 RC 与摘要行 |
| `wt_v18b6step/fulltest_v18b6.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b6step/mutate_v18b6.py` + `.log` | 第 6 轮 7 条反证变异（C1–C7）：每条 `rc=1`、`turned_red=True` 且 `restored=True`；C7 走的是 `--strict` 那条路 |
| `wt_v18b6step/probe_{k,l,m}.py`、`probe_readings_step.log` | 豁免台账的第三种口径探针（M）+ 配置面两把"量过不立"的候选（K/L）在步骤树内的读数 |
| `wt_v18b6ship/run_gates.sh`、`gates_ship.log` | **提交树**（`b4c3bc8`，被跟踪文件 0 改动）九门禁复测 |
| `wt_v18b6ship/fulltest_ship.log` + `.xml` | 提交树离线全量与 junit 计数；与步骤轮的唯一差异是 `transport/pool.py` 一行（§15 ship 段） |
| `wt_v18b7step/run_gates.sh`、`gates_v18b7.log` | 第 7 轮步骤轮（`27e1014` + 本步 3 文件）环境与十次调用逐条 RC 与摘要行；`contract_audit` 一格按 CI 用 `--ci` 跑（第 6 轮是裸脚本，本轮更严） |
| `wt_v18b7step/fulltest_v18b7.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数 |
| `wt_v18b7step/mutate_v18b7.py` + `.log` | 第 7 轮 10 格反证（V1/V2 ＝ 旧口径为什么哑；M1–M8 ＝ 每格新判据各自红一次）；harness `rc=0`，每格跑完按 md5 还原 |
| `wt_v18b7step/_base_contract_audit.py` | 提交前版本（`27e1014`）的审计脚本快照——V1/V2 两格的取证对象，只在步骤树内，不入库 |
| `wt_v18b7step/probe_n_typed_coverage.py`、`probe_readings_step.log` | 三个派生来源与旧 92 格 PENDING 的口径分解（§16 第一张表的来源） |
| `wt_v18b7ship/run_gates.sh`、`gates_ship.log` | **提交树**（`e26300e`，被跟踪文件 0 改动）十次调用复测 |
| `wt_v18b7ship/fulltest_ship.log` + `.xml` | 提交树离线全量与 junit 计数；与步骤轮唯一差异仍是 `transport/pool.py` 一行，方向与 §15 相反 |
| `wt_v18b8step/run_gates.sh`、`gates_v18b8.log` | 第 8 轮步骤轮（`0bf2994` + 本步 11 文件）环境与十次调用逐条 RC 与摘要行 |
| `wt_v18b8step/fulltest_v18b8.log` + `.xml` | 步骤轮离线全量（带 `--cov`）与 junit 计数。同一棵树本步跑过两次：第一次 3 519 miss / 997 partial / **81.70%**（`pool.py`、`ratelimit.py` 两行各抖 2/1），日志被第二次同名覆盖、只余 §17 的数字；第二次（最终内容）3 523 / 999 / **81.68%**，即这份存档 |
| `wt_v18b8step/mutate_v18b8.py` + `.log` | 第 8 轮 9 格反证（C0 对照 + P1–P8 回潮形状）：每格至少一道门禁红、每格按字节还原；harness `rc=0` |
| `wt_v18b8step/_base_deprecation.py`、`_base_test_deprecation.py` | 提交前版本（`0bf2994`）的两个被删文件快照——P1/P2/P8 三格的取证对象，只在步骤树内，不入库 |
| `wt_v18b8ship/run_gates.sh`、`gates_ship8.log` | **提交树**（`cb92bc0`，被跟踪文件 0 改动）十次调用复测 |
| `wt_v18b8ship/fulltest_ship8.log` + `.xml` | 提交树离线全量与 junit 计数；与步骤轮的 `tstdx/` 覆盖表逐行相同 |

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

**ship 轮（提交树复测，同一解释器与工作树参数）**：`d1bd446` 落到 main 后另起隔离工作树
`wt_v18b2ship` 再量一遍，`head=d1bd446 dirty=0`。九项确定性门禁 **G1–G9 全部 rc=0**
（`gates_ship.log`：originality `Total: 191 / Original: 191 / Suspicious: 0`、spec_audit
`coverage_pct 100.0`、golden_audit `[GATE] all L1 verified commands have real samples (OK)`
+ 既有 `suspect_short` WARN 1 条、reachability `无未登记孤儿 ✓`、contract_audit、
docs links 83 files、mypy 0 行、ruff check `All checks passed!`、ruff format 443 files）；
离线全量 junit **3617 / 0 失败 / 0 错误 / 7 跳过**、153.8s、覆盖率 **81.50%**
（TOTAL 22 433 / 3 578 / 5 994 / 1 000，`Required 77.0% reached`）。
克隆普查读数在两棵树**逐字相同**（54 组 / 314 份额外副本 / 最大一组 24 份 @ `0x52d`），
提交内容与被测内容一致。两轮覆盖率 81.49% / 81.50%：`stmt` 与 `branch` 两格完全相同
（22 433 / 5 994），差在 `miss` 3 580→3 578、`partial` 1 001→1 000——**这 2 格的来路本轮没有归因**，
只登记为读数微动，不用于任何阈值或趋势判断；阈值 77 未动。

---

## 12. 执行记录（续）

### 第 3 轮｜声明出来的旋钮必须真的被拧动（提交 `0a4a232`，13 个文件）

§10/§11 打掉的是"引用不存在的证据"，本轮打掉的是同一族里剩下的两个形状：
**文档承诺了入参与行为，代码根本不调**（幻影旋钮），以及**文档写了数字，代码换了它不会红**（手抄计数）。
两类各自补了一格可判红门禁，同时把三处幻影旋钮改成真行为。

| 改动 | 内容 |
|---|---|
| `tstdx/transport/sniff.py` | `known()` / `unknown_commands()` 此前把判定域写死成单一族 `Family.STANDARD`（实测其值 `"quotation"`），而 `families` 是构造函数文档承诺的入参：一条登记在 `mac_quotation`/`goods`/`f10` 的命令号被读成"未知"，`export_drafts` 再拿错的账本给它生成草稿。现在两处都按 `self.families` 取域（显式 `family=` 仍可收窄），`attach(families=…)` 也真正改写作用域——`attach` 原文自己写着"仅影响未知命令判定、实际记录仍按 cmd_id 存"，等于承认旋钮只拧了一半 |
| `tstdx/web/_session_market.py` | ① `shared_http()` 自称"线程安全惰性单例"却做无锁 check-then-append（它的兄弟 `shared_bucket` 在 `_base_http.py:101` 是持锁的），并发首建会造出多个 `HttpClient`，多出来的连同各自 keep-alive 连接池一起泄漏（进程级列表，永不关闭）；整段"取或建"移进 `_SHARED_HTTP_LOCK`。② 删掉 `quotes()` 上从未被函数体读取的 `prefix: bool = True` 形参（仓内与 docs 零调用点，属纯幻影入参） |
| `tstdx/web/{global_market,session,__init__,limits,_session_baidu,adapters_fund}.py` | 手抄数字改成"点名事实源"或当场改对：外盘品种 14→**13**（`GLOBAL_CODES` 真值，且与 `_session_market.globals` 原有口径对齐）、`web/__init__` 的"其余 18 个 Source/会话子模块"删除（真实是 `_LAZY` 67 键 / 22 个目标模块——写成新数字同样会烂，故改为指向 `_LAZY`）、`session` 的"7 个零依赖适配器"改为指向 `_ADAPTER_SPECS`；`all_market` 的 `max_pages=None` 不再承诺"拉到底"（缺省 `DEFAULT_MAX_PAGES`=100 页，实测 `adapters.py:86,197,451`）；`TENCENT_KLINE_MAX` 上方"分段请求（尚未封装，见 v5 PG8）"改成 C9 已落地路径 `fetch_bars_paged(…, paging=True)`；`fund_estimate` 的 Returns 段改成"端点已下线，本方法永不返回"+`Raises SourceDeprecated`（**这句本身是过度承诺，第 4 轮按代码更正，见 §13**），`FundSource` 类文档不再把它列为能力 |
| `scripts/audit_reachability.py` | 两类新缺陷，与孤儿同权重在 `--strict` 下失败：`[weak-pointer]`——豁免理由点名的 `.py` 文件必须真的**触达**它豁免的模块（直接 import、沿图走父包导出这一跳、或按点号全名提及），"某某测试覆盖它"从此必须真的覆盖它；`[dead-seed]`——`SEEDS` 里改了名的条目不再被 BFS 的 `if s in modules` 静默丢弃。图构建抽成 `_build_graph()` 供判据复用 |
| `tests/architecture/test_declared_knobs.py`（新） | 三把尺子，每把都带**下限 +  planted 正控**：① numpydoc `Parameters` 里承诺的入参必须被函数体读取（全量现扫 **130** 个带参数文档的函数，修复后 `offenders=[]`；本轮抓到并修掉的真实幻影 2 处 = `attach(families=…)` 与 `quotes(prefix=…)`）；② 自称"线程安全"且动到进程级 `_SHARED_*` 的函数必须拿得出锁证据（全量 `defects=[]`）；③ 写成"共 N 个（见 :data:`X`）"的计数在导入期现算回查，**模块导不进来时报缺陷而不是跳过**——"无法复核"不许读成绿 |
| `tests/transport/test_sniffer_passive.py`（新） | 被动采集族 10 格行为判据：默认判定域、显式 `family=` 收窄、`unknown_commands` 随作用域、`attach` 的记录/幂等/空转三条、环形缓冲与上限、两个零值入参 `ValueError` |
| `scripts/_reach_allow.txt` + `tests/architecture/test_reachability_allowlist.py` | 新格当场抓到一条真实假证据：`tstdx.transport.sniff` 的豁免理由点名 `tests/protocol/test_sniffer_loop.py`，那个文件覆盖的是 `tstdx.protocol.generic` 的**同名不同族**观察器——被豁免模块当时**零测试覆盖**。改为点名本轮新写的 `tests/transport/test_sniffer_passive.py`；另补 6 格契约用例覆盖 `[weak-pointer]` 与 `[dead-seed]`（含正反两向 + 真实清单全量复核） |

**步骤轮**（`wt_v18b3step`，detached @ `66a7b13` + 本步 13 文件，md5 与工作树逐格相同）：
九项确定性门禁 **G1–G9 全部 rc=0**（`gates_v18b3.log`）；离线全量 `PYTEST_RC=0`、
junit **3643 项 / 0 失败 / 0 错误 / 7 跳过**、171.7s；覆盖率 **81.73%**
（TOTAL 22 440 stmt / 3 524 miss / 5 996 branch / 1 002 partial），`Required 77.0% reached`，
**阈值未动**。判据规模 3617 → **3643（+26）**，无删除（新增 26 = 10 被动采集 + 10 声明旋钮 + 6 白名单新格）。
环境标签同上两轮：`.venv` cpython-3.12.13、`WIN-PM`。

**反证证据**（`wt_v18b3step/mutate_v18b3.log`，7 条全部 `rc=1`、`turned_red=True` 且 `restored=True`）：

| 变异 | 动了什么 | 结果 |
|---|---|---|
| M1 | `[weak-pointer]` 的触达判定换成恒真（规则变瞎） | 反证用例红（真实清单用例仍绿 ⇒ 报的是形状不是噪声） |
| M2 | `[dead-seed]` 的比对集合清空 | 过期种子用例红 |
| M3 | 所有函数一律按"桩"豁免（幻影入参抓不到） | planted 正控红，而全量普查仍绿 ⇒ 证明"零缺陷"是看出来的，不是没看 |
| M4 | 锁证据不再检索 | 无锁单例正控红 |
| M5 | 摘掉 `shared_http` 的持锁区间 | 两条同时红：锁尺子弹出该文件，**并发行为用例报 `assert 4 == 1`**（首建造出 4 个 client） |
| M6 | 计数声明导不进来时静默放过 | "无法复核必须是缺陷"用例红 |
| M7 | 判定域退回写死 `"quotation"`（= 修复前语义） | 被动采集 4 条用例同时红 |

**本轮的一条自我反证**（记下来是因为它正是本轮主题的形状）：M5 第一次跑时并发用例**没有红**。
判据当时写的是"四个调用者拿到同一个对象"，而无锁实现里四个调用者都读 `_SHARED_HTTP[0]`，
身份检查天然通过——泄漏的是被 append 到 1..3 的那三个。断言改成**构造次数** `len(built) == 1`
之后 M5 才当场红。一条"看起来在测并发"的判据实测什么都没盯，与 §11 那条"指针只查存在"同族。

**撤回与登记**：
- §5 的 **C2(a)/D5 推荐项撤回**：F-66 已由用户拍板 (c)「不改面、只补判据」，第 47 步并把
  "发现面不许长出状态字段"钉成门禁；(a) 要加的正是那类字段，重做已裁决格属越权。C2 的 (b)/(c)
  两条同样动对外面，维持"待裁决"不变。
- **新登记 G5（§2）**：`fund_estimate` 是 G1 的同族、Provider 侧那一格——`web/sources.py:449`
  与 `providers/__init__.py:570` 仍声明该能力、`cli/runtime_commands.py:681` 仍可达，实现则
  在"站点回 404 页"这一实测形状上抛 `SourceDeprecated`（**第 3 轮此处写作"恒抛"、并把会话
  docstring 写成"永不返回"，两者都是过度承诺，第 4 轮按代码更正，见 §13**）。本轮只把文档改成
  更接近实话的说法，**没动声明面**（动它属对外契约，与 F-66/F-75 同批裁决）。

**ship 轮（提交树复测，同一解释器与工作树参数）**：`0a4a232` 落到 main 后另起隔离工作树
`wt_v18b3ship` 再量一遍，`head=0a4a232 dirty=0`。九项确定性门禁 **G1–G9 全部 rc=0**
（`gates_ship.log`：originality `Total: 191 / Original: 191 / Suspicious: 0`、spec_audit
`coverage_pct 100.0`、golden_audit `[GATE] all L1 verified commands have real samples (OK)`
+ 既有 `suspect_short` WARN 1 条（`0x537`）、reachability `模块总数: 190 / 可达: 174 / 白名单豁免: 16`
且 `无未登记孤儿 ✓`（两条新格零缺陷）、contract_audit、docs links 83 files、mypy 0 行、
ruff check `All checks passed!`、ruff format 445 files）；离线全量 junit
**3643 / 0 失败 / 0 错误 / 7 跳过**、179.8s、覆盖率 **81.73%**
（TOTAL 22 440 / 3 524 / 5 996 / 1 002，`Required 77.0% reached`）——与被测步骤轮**逐格相同**，
提交内容与被测内容一致。

**本轮明确未做**：C1/C2/C3、D1–D4、E1–E4、F2（本机无 `gh` 且 `api.github.com` 被限流，
CI 读数无法在同轮隔离树复现，故不写任何覆盖率再钉）、F3 行情时段 live 任务、F4/G3
（仓内样本锁不出 `0x000F`/`0x0010` 语义，见 §8）、G2 的 D10 裁决；
CHANGELOG `[Unreleased]` 的两条破坏性登记（`quotes` 失败语义、移除 `quotes(prefix=)` 形参）
因并行会话该文件仍未提交而押后。

---

## 13. 执行记录（续）

### 第 4 轮｜命令行面不许有"注册了没人读的开关"，并当场反掉上一轮写下的过度承诺（提交 `7fe7e54`，2 个文件）

第 3 轮的轴是"文档承诺 ↔ 代码兑现"，落点是**函数入参、锁、计数**三种形状。本轮把同一根轴
补到最后一种受众——**命令行用户**：`argparse` 收下并印进 `--help` 的每一个 `--flag`，处理链路
必须真的读它的 `dest`，否则用户以为拧了开关而运行时什么都没发生。

同时用本轮新写的探针回头复核第 3 轮自己的文档改动。**抓到的正是我**：为了改掉 `fund_estimate`
的假 Returns 段，我在第 3 轮把它写成"端点已下线，本方法永不返回"，而实现
（`tstdx/web/_session_baidu.py:108` → `tstdx/web/adapters_fund.py:188`）是**先发一次真实请求**、
只在命中"404 / 页面未找到 HTML"这一形状时才抛 `SourceDeprecated`，其余响应照样 `_parse_jsonp`
返回 dict。"永不返回"与 §2 G5 原来那句"恒抛"是同一种病：**把 2026 年的实测结果写成了结构性质**。
本轮把这两处口径改成实话（代码 docstring + §2 G5 + §12 就地标注，改动见提交与该两节）。

| 改动 | 内容 |
|---|---|
| `tests/architecture/test_declared_knobs.py`（新判据四：CLI 面） | `_dests_of()` 从 CLI 源文件的 `add_argument(...)` 现算 dest→flags 表（`dest=` 优先，否则取长选项名 dashes→underscores），`_reads_of()` 只认**两种严格读取形状**：`<像命名空间的名字>.<dest>` 属性访问、`getattr(<像命名空间的名字>, "<dest>")`。命名空间宿主必须叫 `args/ns/namespace/parsed/opts` 或以 `args` 结尾，因此**注册语句本身不可能自证为已读**。全量普查 `test_every_registered_cli_flag_is_read_by_the_runtime` 带**下限 `len(dests) >= 40`**（尺子解析不出东西时必须自己先红，不许把"扫不到"读成绿），planted 正控 `test_the_cli_ruler_sees_a_planted_unread_flag` 保证规则不是恒真 |
| `tstdx/web/_session_baidu.py::fund_estimate` | docstring 从"永不返回"改成实测形状：一句话交代端点已下线并指向 `fund_nav_history`、明示**仍会发出一次真实请求**、`Raises SourceDeprecated`（410 语义，按 `FundSource` 记录的实测形状）与 `Raises WebSourceError`（不可达/超时等非下线形状）分开，末尾留一句"解析层随端点一起留着，接口复活本方法会重新返回 dict——『现网只抛』不是它的结构性质"。**没动声明面**（G5 与 F-66/F-75 同批裁决） |

**四个"量过、按数字不立"的判据候选**（探针与计数见 `wt_v18b4step/probe_rerun.log`，本轮同轮在
main 工作树上重跑；不立的判据不留代码，只留这段读数）：

| 候选 | 实测 | 不立的理由 |
|---|---|---|
| A：能力恒不可用（函数体任何路径都只抛）却仍挂在声明面 | `hits=0` | 这一格形状当前**零实例**（`fund_estimate` 按代码看并不属于它）。为零样本建尺子只能靠 planted 正控撑着，收益不抵一条恒真空判据的维护成本；G5 已在 §2 登记成人读得懂的账 |
| B：docstring 同句写"尚未/未实现"且点名符号，而符号已存在 | `hits=2`，**2/2 误报** | 两条都判错：`tstdx/domain/calendar.py:18` 说 `TradingCalendar.update_from_web` "尚未实现，调用即抛 `NotImplementedError`"——该函数在 `calendar.py:335` 确实**只有**一句 `raise NotImplementedError`，文档是实话；`tstdx/web/_session_efinance.py:6` 的"尚未被 tstdx 覆盖"讲的是能力覆盖面而非某个符号。"符号存在"≠"能力已实现"，这条谓词根本盯不住它想盯的东西 |
| C：散文式"永不返回/恒抛"↔ 控制流是否真的无返回路径 | 修复前 `claims=1 unfounded=1`，修复后 `claims=0 unfounded=0` | 全包活样本只有 1 个，而且**就是我上一轮亲手写的那句**；更糟的是**它对否定是瞎的**——把"『永不返回』不是它的结构性质"这句**更正**写回 docstring，token 扫描仍把它读成"声称永不返回"（K5 复现的就是这个形状）。散文里的 token 匹配做不成判据：要么换成控制流分析（为零样本付这个代价不值），要么留下 K5 那种自证式噪声 |
| D：numpydoc `Raises` 段点名的异常是否在该函数可达 raise 集合里 | 全包**只有 2 个**函数带 `Raises` 段；D1 严格口径 `hits=2`，D2 全包范围 `hits=0` | 那 2 条命中全在 `fund_estimate`，且两条都是**真承诺**（`raise` 站点在单跳之外的 `FundSource.fetch_estimate` 里，严格口径把真话读成缺陷）。为 2 格样本、首跑就 100% 误报的判据入门禁，等于给判据集加一条必须写豁免的形状 |

**步骤轮**（`wt_v18b4step`，detached @ `10a4488` + 本步 2 文件，两文件 md5 与 main 工作树逐格相同：
`test_declared_knobs.py` = `4e2817f5…`、`_session_baidu.py` = `a2b7fc45…`）：九项确定性门禁
**G1–G9 全部 rc=0**（`gates_v18b4.log`：originality `Total: 191 / Original: 191 / License OK: 191 /
Header OK: 191 / Suspicious: 0 / External imports: 17`、spec_audit `"coverage_pct": 100.0`、
golden_audit `[GATE] all L1 verified commands have real samples (OK)` + 既有 `suspect_short` WARN 1 条
（`0x537 4B<12B x3`）、reachability `模块总数: 190 / 可达: 174 / 白名单豁免: 16` 且 `无未登记孤儿 ✓`、
contract_audit rc=0、docs links `83 files`、mypy 0 行、ruff check `All checks passed!`、
ruff format `445 files already formatted`）；离线全量进度走到 `[100%]`、junit
**3645 项 / 0 失败 / 0 错误 / 7 跳过**、159.742s；覆盖率 **81.73%**
（TOTAL 22 440 stmt / 3 523 miss / 5 996 branch / 1 001 partial），`Required test coverage of 77.0%
reached`，**阈值未动**。判据规模 3643 → **3645（+2）**，无删除。环境标签同前三轮：
`.venv` cpython-3.12.13、`WIN-PM`。

**反证证据**（`wt_v18b4step/mutate_v18b4.log`，5 条全部 `rc=1`、`turned_red=True` 且 `restored=True`）：

| 变异 | 动了什么 | 结果 |
|---|---|---|
| K1 | `_dests_of` 解析不出任何 dest（尺子失明） | 下限与正控**同时**红——证明 `>= 40` 那条兜底真的在兜 |
| K2 | 命名空间读取集恒空（所有 flag 都被误判成没人读） | 全量普查与正控同时红 |
| K3 | 改成"文本里搜到名字就算读取" | **只有正控红、全量普查仍绿** ⇒ 严格 AST 口径是必要的：宽松口径下注册语句会自证为已读 |
| K4 | 真实 CLI 里把 `tstdx/cli/parser.py:207` 的 `--port` dest 改名（制造无人读取的 flag） | 全量普查红 ⇒ 判据对真实面有效，不只是对 planted 样本有效 |
| K5 | 把第 3 轮那句"本方法永不返回"写回去 | 探针 C `claims=1 unfounded=1` ⇒ 本轮的口径更正确实盯住了它要盯的东西 |

**ship 轮（提交树复测，同一解释器与工作树参数）**：`7fe7e54` 落到 main 后另起隔离工作树
`wt_v18b4ship` 再量一遍，`head=7fe7e54 dirty=0`。九项确定性门禁 **G1–G9 全部 rc=0**，摘要行与
步骤轮逐字相同（`gates_ship.log`：`Total: 191 / Suspicious: 0`、`coverage_pct 100.0`、
`模块总数: 190 / 可达: 174 / 白名单豁免: 16 / 无未登记孤儿 ✓`、docs links 83 files、mypy 0 行、
ruff check `All checks passed!`、ruff format 445 files）；离线全量 junit
**3645 / 0 失败 / 0 错误 / 7 跳过**、159.689s、覆盖率 **81.73%**
（TOTAL 22 440 / 3 523 / 5 996 / 1 001，`Required 77.0% reached`）——与被测步骤轮**逐格相同**。
两棵树的 `tstdx/`、`tests/`、`scripts/` 逐文件比对（`diff -r --strip-trailing-cr`）除 `__pycache__`
外**内容一致**，仅工作树检出为 CRLF、提交树为 LF：提交内容与被测内容一致。

**本轮明确未做**：候选 A/B/C/D 四把尺子（读数见上表，量过而不立）；§2 G5 的**声明面**改动
（`sources.py:449` / `providers/__init__.py:570` 是否摘掉 `fund_estimate` 属对外契约，与 F-66/F-75
同批裁决）；C1/C2/C3、D1–D4、E1–E4、F2、F3、F4/G3、G2 的 D10 裁决；CHANGELOG `[Unreleased]`
三条登记（`quotes` 失败语义、移除 `quotes(prefix=)` 形参、`Sniffer` 判定域随 `families` 收窄）
仍因并行会话该文件未提交而押后。

---

## 14. 执行记录（续）

### 第 5 轮｜机器对机器的声明面也不许有"传了没人读"的字段；文档教的命令必须跑得起来（提交 `788e533`，3 个文件）

同一根轴的第三种受众。第 3 轮量**函数入参**（docstring `Parameters` ↔ 函数体），第 4 轮量**命令行用户**
（`--flag` ↔ `args.<dest>`）。本轮量**机器对机器的声明面**：HTTP 路由的函数签名与 MCP 工具的
`inputSchema.properties` 是对外公开的白名单——FastAPI 把路由形参公开成查询串参数，
`wire_fields`/MCP schema 又拿同一份声明当拒绝白名单，于是"加进声明却无人读"的字段能一路绿灯穿过闸口，
调用方那边变成"我传了，它没理我"。比 CLI 那一格更隐蔽：`--help` 至少还在手边，wire 面上它看起来就是
一个生效的开关。

| 改动 | 内容 |
|---|---|
| `tests/architecture/test_declared_knobs.py`（新判据五：wire 面） | HTTP 侧 `_http_scan()` 从 `runtime_http.py` 的 AST 现算 `app.get/post/put/delete` 装饰过的 handler 形参表（`self/cls` 除外），`_body_reads()` 只认函数体里 **Load 语境**的名字；MCP 侧从 `_tools_spec.TOOLS` 的 `inputSchema.properties` 现算声明属性，读取集 `_dict_keys()` 只认 `<请求体字典>.get("K")` / `<body-dict>["K"]` 两种形状，字典名必须叫 `args/params/payload/data/options`——**声明自身的字面量不是这两种形状，注册无法自证为被读取**（沿用判据四的口径）。两面共用谓词 `_wire_unread()`；三张下限 `HTTP total >= 20`、`len(TOOLS) >= 5` 且 `MCP total >= 20`；planted 正控 `test_the_wire_ruler_sees_planted_unread_fields` 用一段含"注册了没人读"形状的假路由源码，同时验正反两向 |
| `tests/architecture/test_doc_code_consistency.py`（新判据：文档命令形状） | `_CMD_TARGET` 抽 `python [-3.x] -m …` / `uvicorn …` 里点名的 `tstdx.x.y` 与可选 `:attr`，`_IMPORT_TARGET` 抽 `from/import tstdx.x`；`_target_offense()` 真的 `importlib.import_module()` 并对 `:attr` 做 `hasattr`——判据落在"这条命令敲下去起不起来"，不是"字符串像不像个模块"。作用域沿用同文件的 `chain_docs()`（活文档），下限 `checked >= 60`；正控三格：planted 死模块红、写对的 `runtime_http:create_runtime_app` 绿、模块在而对象名抄错红 |
| `docs/FAQ.md` 部署段 + 监控段 | 见下段 |

**与 F-47 的分工**（避免被读成重复建设）：`tests/runtime/test_wire_declared_fields.py` 管**反方向**——
请求里出现未声明的字段必须当场被 `reject_undeclared` 拒掉；本判据管**声明了的字段有没有真的被读**。
前者是运行期行为、后者是静态 AST，两面合起来才是那句"wire 面上每个字段要么被读走、要么被拒"。

**新尺子第一件事抓到的唯一真实缺陷**：`docs/FAQ.md` 部署段教用户
`python -m uvicorn tstdx.integration.http_server:create_app --factory`，而 `tstdx/integration/http_server.py`
早已被物理删除（真身是 `runtime_http.create_runtime_app`）——照抄文档的人第一步就吃 `ModuleNotFoundError`；
101 处命令/导入形状里唯一的一处。同段另两处也按代码钉实：镜像 CMD 是 `--help`，起服务要么走
`tstdx serve --bind 0.0.0.0 --port 8000`（`cli/parser.py:206-209` 确有这两个 flag），要么把 app 交给 uvicorn
（需 `tstdx[server]`，`pyproject.toml:45` 确有该 extra）；监控段原来那句 `GET /api/v1/system/metrics`
在网关里根本不存在（全树 grep `system/metrics` 现已 0 命中），改成代码里真有的 `PrometheusExporter.serve()`
——`host="127.0.0.1", port=9090` 缺省、暴露 `GET /metrics`（`prometheus_exporter.py:166`）。
**第 4 轮的教训在这一格起了作用**：我一度把 `OtelExporter` 写成"缺省端点 `http://localhost:4318/v1/metrics`"，
读到 `__init__` 签名是 `endpoint: str | None = None`（**没有**缺省值）才收回，改成"`endpoint` 须显式给出，
例如 …"。

**三个"量过、按数字不立"的判据候选**（步骤树内重跑，读数见 `wt_v18b5step/probe_readings_step.log`）：

| 候选 | 实测 | 不立的理由 |
|---|---|---|
| H1：文档点名的 wire 查询键必须存在于声明面 | 声明并集 13 个路由形参 / 17 个 wire 键（并集口径，与判据五的逐路由 25 格不同），文档命中 1 个未知键：`max_age`，来自并行会话**在途未提交**的台账 `docs/REFACTOR_PLAN_V17_CLOSURE.md`（F-43 正是在记这个旋钮的账） | 唯一命中是对方会话写给自己的缺陷描述，不是"文档教错"。要立它就得把别人的活文档纳入我的判据域，并解决"叙述语境 vs 指令语境"的区分——这条区分本轮没有可推导的口径 |
| H2：文档点名的 `--flag` 必须已注册 | CLI 声明面 70 个 dest/flag 写法，文档命中 38 个未注册 | 38 个几乎全是**别的工具的**旗标（`--cov`、`--rm`、`--stat`、`--ignore-missing-imports`、`--strip-trailing-cr`）与归档计划里的设想命令（`--regen-all`、`--require-hashes`）。立它等于维护一份"哪些命令行不属于 tstdx"的豁免名单——正是第 4 轮否掉的那种形状 |
| I：文档里所有反引号点号引用必须可解析 | 39 份活文档、点号引用 214 次 / 去重 121 个，19 次解析不出（分布在 9 份文档） | 19 处**全是误报**：`tstdx.git`（URL 尾巴）、`tstdx.toml`（文件名）、迁移/发布说明里的**否定句**（"不再有 `tstdx.compat`"）——第 4 轮探针 C 那条"散文里对否定是瞎的"在这里原形重现。更能说明问题的是：本轮真正的缺陷（`http_server:create_app`）**不在**这 19 处里，反引号口径看不见命令，命令口径才看得见 |

**步骤轮**（`wt_v18b5step`，detached @ `9384796` + 本步 3 文件，三文件 md5 与 main 工作树逐格相同：
`test_declared_knobs.py` = `f08ef8f4…`、`test_doc_code_consistency.py` = `0cf2c0e9…`、`FAQ.md` = `b707432e…`）：
环境探针 + 九项确定性门禁 G1–G9 共 10 次调用**全部 rc=0**（`gates_v18b5.log`：originality `Total: 191 /
Original: 191 / License OK: 191 / Header OK: 191 / Suspicious: 0 / External imports: 17`、spec_audit
`"coverage_pct": 100.0`、golden_audit `[GATE] all L1 verified commands have real samples (OK)` + 既有
`suspect_short` WARN 1 条（`0x537 4B<12B x3`）、reachability `模块总数: 190 / 可达: 174 / 白名单豁免: 16`
且 `无未登记孤儿 ✓`、contract_audit `63 个 Typed Query 契约` + 92 待办 rc=0、docs links `83 files`、mypy 0 行、
ruff check `All checks passed!`、ruff format `445 files already formatted`）。判据五自身读数：**HTTP 10 张路由 /
25 个形参，unread=0；MCP 9 个工具 / 29 个声明属性，unread=0；正控（塞一个不读的声明属性）被抓住 True**。
离线全量进度走到 `[100%]`、junit **3650 项 / 0 失败 / 0 错误 / 7 跳过**、160.286s；覆盖率 **81.72%**
（TOTAL 22 440 stmt / 3 525 miss / 5 996 branch / 1 002 partial），`Required test coverage of 77.0% reached`，
**阈值未动**。判据规模 3645 → **3650（+5）**，无删除。环境标签与前四轮相同：`.venv` cpython-3.12.13、`WIN-PM`。

**反证证据**（`wt_v18b5step/mutate_v18b5.log`，7 条全部 `rc=1`、`turned_red=True` 且 `restored=True`）：

| 变异 | 动了什么 | 结果 |
|---|---|---|
| L1 | HTTP 路由解析不出任何 handler（尺子失明） | 普查与正控**同时**红 ⇒ `total >= 20` 那条兜底真的在兜 |
| L2 | MCP handler 读取集恒空（所有声明属性都被误判成没人读） | MCP 普查红 |
| L3 | 共用谓词 `_wire_unread` 恒不报 | **只有正控红、两张普查仍绿** ⇒ 正控是有分量的那一半：没有它，"unread=0"读不出任何区别 |
| L4 | 真实 `runtime_http.py` 里加一个没人读的路由形参 | HTTP 普查红 ⇒ 判据对真实面有效，不只是对 planted 样本有效 |
| L5 | 真实 `_tools_spec.py` 声明一个 handler 不读的属性 | MCP 普查红 |
| L6 | 文档命令形状扫不到东西 | 普查与正控同时红（`checked >= 60` 兜底） |
| L7 | 把本轮修掉的死命令（`http_server:create_app`）原样写回 FAQ | 文档命令判据红 ⇒ 新格确实盯住了它要盯的那一处 |

**ship 轮（提交树复测，同一解释器与工作树参数）**：`788e533` 落到 main 后另起隔离工作树 `wt_v18b5ship`
再量一遍，`head=788e533`、被跟踪文件 0 处改动（树里只剩本轮取证脚本与日志自己）。10 次调用**全部 rc=0**，
摘要行与步骤轮逐字相同（`gates_ship.log`：`Total: 191 / Suspicious: 0`、`coverage_pct 100.0`、
`模块总数: 190 / 可达: 174 / 白名单豁免: 16 / 无未登记孤儿 ✓`、`63 个 Typed Query 契约`、docs links 83 files、
mypy 0 行、ruff check `All checks passed!`、ruff format 445 files）；离线全量 junit
**3650 / 0 失败 / 0 错误 / 7 跳过**、166.899s、覆盖率 **81.72%**（TOTAL 22 440 / 3 525 / 5 996 / 1 002，
`Required 77.0% reached`）——与被测步骤轮**逐格相同**。两棵树的 `tstdx/`、`tests/`、`scripts/` 逐文件比对
（`diff -r --strip-trailing-cr`）除 `__pycache__` 外**内容一致**，仅工作树检出为 CRLF、提交树为 LF：
提交内容与被测内容一致。

**−0.01pp 的来历（量清楚了才写进这份文档）**：上一轮 81.73% → 本轮 81.72%，而本轮**没碰任何运行时代码**
（改动全在 `tests/architecture/` 与 `docs/FAQ.md`，`--cov=tstdx` 的总语句数四次运行恒为 22 440，分母没动）。
逐文件比对：两轮之间的差异集中在 `tstdx/transport/pool.py`（607 stmt，未覆盖 111→113、partial branch 36→37）。
为了把它钉成"计时抖动"而不是"本轮造成的退化"，同一棵步骤树、同一批文件又跑了第三次全量
（`fulltest_v18b5_rerun.log`，`PYTEST_RC=0`）：`pool.py` 这次稳定在 113/37，动的换成了
`tstdx/protocol/generic.py`（20/9 → 21/10），TOTAL 随之 3 525/1 002 → 3 526/1 003。也就是说这两个并发/计时
相关文件的未覆盖行集**逐次运行本身就会抖 ±1～2 行**，"两次独立读数相同"不能作为零退化的证据。按实测口径
写结论：**本轮 −0.01pp 落在计时抖动行上，与本轮改动无因果**；覆盖率真值仍以 CI（ubuntu + py3.11、同一提交树）
为准，本轮不据此重钉基线。

**本轮明确未做**：候选 H1/H2/I 三把尺子（读数见上表，量过而不立）；`chain_docs()` 之外的文档（归档计划、
并行会话在途台账）不纳入任何命令/引用判据；`docs/FAQ.md` 其余段落未逐条复核（本轮只动部署与监控两段）；
§2 G5 的**声明面**改动（与 F-66/F-75 同批裁决）；C1/C2/C3、D1–D4、E1–E4、F2、F3、F4/G3、G2 的 D10 裁决；
CHANGELOG `[Unreleased]` 三条登记仍因并行会话该文件未提交而押后。

## 15. 执行记录（续）

### 第 6 轮｜轴的第四次延伸：门禁自己的台账也在"声明了就得兑现"之列（提交 `b4c3bc8`，3 个文件）—— V18-C4 前半

前四轮把这根轴量在**产品**身上：第 3 轮函数入参、第 4 轮 `--flag`、第 5 轮 HTTP 形参与 MCP
`inputSchema`。本轮把它量到**判据自己的账本**上——C4 要的就是这一格。
`scripts/_reach_allow.txt` 的 16 条豁免记录是 reachability `--strict` 那句
"190 模块 / 174 可达 / 16 豁免 / 无未登记孤儿"的全部依据；`--strict` 此前已经钉住记录指向的模块存在、
不过期、理由够长、理由里的仓内路径存在且**真的触达**被豁免模块（`[dead-pointer]`/`[no-pointer]`/`[weak-pointer]`）。
**没钉的只剩理由里那些符号名**：每条理由都在说"这个模块的公共入口是 `X`/`Y`"，而这些名字一格都不核。
它比代码过期得慢、也比代码隐蔽：类拆并或改名之后，路径还在、理由够长、指针也还触达，
白名单其余各类缺陷全绿，
而照着台账那句话去 `from tstdx.catalog.provider_contract import CapabilityContract` 的人拿不到任何东西——
本轮实测这个名字**在本仓今天的代码里根本不存在**（`grep -E "(^|[^A-Za-z_])CapabilityContract"` 在 `tstdx/` 下
只命中 `ProviderCapabilityContract` 这个真名的子串）。

| 改动 | 内容 |
|---|---|
| `scripts/audit_reachability.py`（新增两类缺陷，接入 `main()`） | `_SYMBOL_CLAIM` 抽理由里**反引号点名**的裸标识符（含 `/` `.` 或以 `.py`/`.md` 结尾的不算，指针那几格已经管它们）；`_module_namespace()` 从被豁免模块文件的 AST 现算静态可见的顶层名字：定义、赋值名、`import`/`from` 别名（含 `as`）、`if`/`try` 分支里的条件与兜底导入、以及 `__all__` 的字面量条目；`_claim_defects()` 逐格比对，报 `[dead-claim]`，并在**全清单点名的符号总数 < `MIN_SYMBOL_CLAIMS`（30）** 时另报 `[blind-claims]`——记号被批量擦掉时判据自己喊红，不许把"没人声明"读成"没缺陷" |
| `scripts/_reach_allow.txt`（16 条，其中 15 条带点名） | 49 格公共 API 名全部加反引号（可核口径），并修掉 5 处过期写法（下表）；文件头补一段"记号约定"，说明反引号＝"本模块导出什么"、散文＝"它在讲什么"，两者混成一个口径会误报 |
| `tests/architecture/test_reachability_allowlist.py`（+5 格，22 格全绿） | 真实清单普查（含 `total >= 40` 证据面下限）＋ 四格 planted：点名的符号不存在→`[dead-claim]`、`from .impl import X` 再导出算命名空间、`__all__` 条目算合法声明（`__getattr__` 动态给名那一族不会误报）、同一份记录在 `min_claims=0` 下绿而下限默认值红→**下限红的是规模不是真假** |

**修掉的 5 处过期写法**（分布在 4 行、5 条记录：点名到不存在的符号 2 处 + "等 N 个"这类核不了的计数 3 处；
都在本轮之前就已与代码不符，`git show 7420d9d:scripts/_reach_allow.txt` 可回查）：

| 记录 | 原写法 | 现写法（实测） |
|---|---|---|
| `tstdx.catalog.provider_contract` | `ProviderIdentity`/**`CapabilityContract`** | `ProviderIdentity`/`ProviderCapabilityContract`/`ProviderExecutionContract`；消费方从"三个文件"改成点名的两份真实测试，并补 `test_namespace_layout.py` 钉迁移后位置 |
| `tstdx.profile.detect` | `detect_profile`/`BUILTIN_PROFILES` | `detect`/`BUILTIN_PROFILES`，并写明探测入口 `tstdx.profile.detect_profile` 的真身在 `tstdx/reader/profile.py:670`、由包 `__init__` 再导出 |
| `tstdx.trade.errors` | "tests/trade/ **两个文件**断言其语义" | 点名 `tests/trade/test_frames.py` 与 `tests/trade/test_simulator.py`（指针口径要求可核文件名，"两个文件"核不了） |
| `tstdx.charset` / `tstdx.profile` | "… 等 11 项" / "… 等 14 个测试文件引用" | 改成"完整清单见 charset 包 `__init__` 的 `__all__`" / 只留可核的那一份测试——**"等 N 个"这类手抄计数正是第 3 轮 R-10 的口径**，本轮在自己台账上把它清掉 |

**为什么是"反引号 + 静态 AST"而不是"括号里的名字 + `importlib` 真导入"**：另一把探针（`probe_m.py`）按后一种口径
在 pre-fix 清单上能认出 14/16 条记录、43 个名字，并 flag 出 3 条记录 5 个"不存在"的名字——其中
`tstdx.charset.encoding` 那条点名的 `GB18030`/`GBK`/`Big5` **3/3 全是误报**（那句散文讲的是"优先级表里含哪些
编码名"，不是"本模块导出什么"，`hasattr` 必然为假），另外 2 个才是本轮真修的 `detect_profile` 与
`CapabilityContract`。两种口径因此**互盲**：修完之后同一把括号探针只剩 1 条记录 2 个名字（16 条里的 15 条它再也看不见），
而反引号口径在 pre-fix 清单上同样什么都抽不出来。选反引号是因为它能把"导出什么"与"在讲什么"分开——
这正是括号口径做不到的那件事。改成静态命名空间还有一条硬理由：CI（ubuntu + py3.11）不装
`[server]`/`output` 的 extras，`importlib.import_module("tstdx.output")` 在那里直接抛，基于导入的尺子在 CI 上是瞎的
（本地能跑是因为工作树装了全套 extras，这正是 §14 记下的环境标签不可混用问题）。

**两把"量过、按数字不立"的配置面候选**（步骤树内读数，`wt_v18b6step/probe_readings_step.log`）：

| 候选 | 实测 | 不立的理由 |
|---|---|---|
| K：`tstdx/config/schema.py` 声明的字段必须在包内被读到 | 声明面 6 段 / 22 字段；K1 严格（schema 外的受体属性读取）、K2（+字符串键）、K3（含 schema 自身）**三档 unread 全为 0** | 读取集是**按名字**在全包属性名并集里查的，`timeout` 这种名字分不清 `cfg.timeout` 与 `sock.timeout` ⇒ "0 未读"是口径必然，不是事实断言。零发现＋零分量的判据就是 §4 第二类要删的形状 |
| L：把配置对象按"段 → 字段"的链式读取来核 | 5 段 / 17 字段，包内属性链只读到 8 对；`rate_limit` 5/5、`web` 4/4 报"未见" ⇒ **9 格全部误报** | 误报来源是两种真实写法，都能指到行：整段对象原样传出去（`transport/pool.py:93` 把 `cfg.rate_limit` 交给 `SessionRateLimiter.from_config`，字段在形参名上读，`ratelimit.py:285-290`），以及先落到别名（`web/__init__.py:391` 的 `cfg.enabled_sources`）。要修就得维护"哪些受体算同一个对象"的跨函数别名图——那是第 4 轮否掉的形状，不是派生判据 |

**步骤轮**（`wt_v18b6step`，detached @ `7420d9d` + 本步 3 文件；`audit_reachability.py` md5 `65eccf6e…`、
`_reach_allow.txt` `c9e00ad9…`、`test_reachability_allowlist.py` `b7e662be…`，与 main 工作树逐格相同）：
环境探针 + 九项确定性门禁 G1–G9 共 10 次调用**全部 rc=0**（`gates_v18b6.log`：`3.12.13 … [MSC v.1944 64 bit]`、
originality `Total: 191 / Original: 191 / License OK: 191 / Header OK: 191 / Suspicious: 0 / External imports: 17`、
spec_audit `"coverage_pct": 100.0`、golden_audit `[GATE] all L1 verified commands have real samples (OK)` + 既有
`suspect_short` WARN 1 条（`0x537 4B<12B x3`）、**reachability `模块总数: 190 可达: 174 白名单豁免: 16` +
`无未登记孤儿 ✓`（新增两类缺陷在同一格里，0 条）**、contract_audit `63 个 Typed Query 契约` + 92 待办 rc=0、
docs links `83 files`、mypy 0 行、ruff check `All checks passed!`、ruff format `445 files already formatted`）。
新判据自身读数：**16 条记录 / 15 条带点名 / 49 格符号 / `[dead-claim]` 0 条 / 下限 30**。
离线全量进度走到 `[100%]`、junit **3655 项 / 0 失败 / 0 错误 / 7 跳过**、154.899s；覆盖率 **81.73%**
（TOTAL 22 440 stmt / 3 523 miss / 5 996 branch / 1 001 partial），`Required test coverage of 77.0% reached`，
**阈值未动**。判据规模 3650 → **3655（+5）**，无删除。环境标签与前五轮相同：`.venv` cpython-3.12.13、`WIN-PM`。

**反证证据**（`wt_v18b6step/mutate_v18b6.log`，7 条全部 `rc=1`、`turned_red=True` 且 `restored=True`）：

| 变异 | 动了什么 | 结果 |
|---|---|---|
| C1 | 命名空间解析恒空（所有点名都算不存在） | 真实清单普查红 ⇒ 尺子坏在自己身上时也会喊 |
| C2 | 反引号记号解析不出来（判据失明） | 普查红 ⇒ `MIN_SYMBOL_CLAIMS` 那条下限真的在兜 |
| C3 | 下限抹成 0 | planted 的"薄点名"格红 ⇒ 下限与符号真假是两条独立的红 |
| C4 | 真实清单里把一个点名的公共 API 改成不存在的名字 | 普查红 |
| C5 | 把本轮刚改对的那格退回改错前的形状（`CapabilityContract`） | 普查红 ⇒ **本轮修的那一格现在有了门禁**，改回去就红 |
| C6 | 改**代码**：把被点名的契约类连它的 `__all__` 条目一起改名，清单不动 | 普查红 ⇒ 判据读的是当前模块，不是清单自证 |
| C7 | CI 路径：同一格缺陷交给 `--strict` | `rc=1` + `[ALLOW-DEFECT] [dead-claim] tstdx.profile.presets：…` ⇒ 门禁与测试是同一套口径 |

**C6 的来历值得记一句**：最初只改 `class ProviderIdentity` 那一行时它是**绿的**——`__all__` 里那串
`"ProviderIdentity",` 还留着，按命名空间口径这一格仍然"有这个名字"。这不是判据漏了本轮要的账
（"清单说它有、代码里没有"这一格已经成立），而是它管不到"改了实现忘了改 `__all__`"——那属 F-4 的
命名空间白名单与 contract_audit 的域，不该由豁免台账代管。变异因此加宽成两处改动，并把这句盲区写在这里而不是偷偷改掉判据。

**ship 轮（提交树复测，同一解释器与工作树参数）**：`b4c3bc8` 落到 main 后另起隔离工作树 `wt_v18b6ship`
再量一遍，`head=b4c3bc8`、被跟踪文件 **0 处改动**。10 次调用**全部 rc=0**，摘要行与步骤轮逐字相同
（`gates_ship.log`：`Total: 191 / Suspicious: 0`、`coverage_pct 100.0`、`模块总数: 190 / 可达: 174 / 白名单豁免: 16 /
无未登记孤儿 ✓`、`63 个 Typed Query 契约`、docs links 83 files、mypy 0 行、ruff check `All checks passed!`、
ruff format 445 files）；离线全量 junit **3655 / 0 失败 / 0 错误 / 7 跳过**、160.737s、覆盖率 **81.72%**
（TOTAL 22 440 / 3 525 / 5 996 / 1 002，`Required 77.0% reached`）。两棵树的 `scripts/`、`tests/architecture/`
逐文件比对（`diff -r --strip-trailing-cr`）**内容一致**（仅工作树检出 CRLF、提交树 LF）。
两轮之间 0.01pp 的差**只有 `tstdx/transport/pool.py` 一行**：步骤轮 111 miss/36 partial，提交轮 113/37，
TOTAL miss 3 523→3 525、partial 1 001→1 002——这正是 §14 末段第三次全量亲自量到的那个计时抖动文件
（同树连跑就会在 ±1～2 行之间跳）。本轮改动全在 `scripts/` 与 `tests/architecture/`，`tstdx/` 一行未动，
分母恒为 22 440。结论按 §14 已确立的口径写：**该差值落在 pool 的抖动行上，与本轮改动无因果**，
覆盖率真值仍以 CI 为准，本轮不据此重钉基线。

**本轮明确未做**：C4 的后半截——`tstdx/deprecation.py` 按 D3 的处置（默认删除 + 防回潮守卫）当时没做，
本轮只把它的台账格子说清楚（**第 8 轮已落地，见 §17**）；配置面 K/L 两把尺子（读数见上表，量过而不立）；`tstdx.trade` 那条记录
仍是"包级理由 + 零反引号点名"（下限有余量：49 ≫ 30，所以它绿得有依据，但也确实没被符号口径盯住）；
`_module_namespace()` 不核"名字在但形状/语义与理由描述不符"（比如理由说是类、实为函数）；
§2 G5 的声明面改动（与 F-66/F-75 同批裁决）；C1/C2/C3、D1–D4、E1–E4、F2、F3、F4/G3、G2 的 D10 裁决；
CHANGELOG `[Unreleased]` 三条登记仍因并行会话该文件未提交而押后。

---

## 16. 执行记录（续）

### 第 7 轮｜轴的第五次延伸：判据自己赖以放行的那份名单也得能派生（代码 `a98755d` + `e26300e` + `c7db30b`，3 个文件 + README 两行）—— G2 按 D10(b) 落地

前四轮量的是"声明出来的东西有没有被读"，第 6 轮量的是"台账点名的符号在不在"，这一轮把尺子
转向**判据的输入**：`contract_audit` 的 Registry 覆盖一格拿注册表去比一份手抄豁免名单，
名单说谁免检谁就免检。这条轴上的危险方向和前面相反——不是"承诺了没做"，而是"尺子的刻度本身
是抄来的"，所以它越绿越没人怀疑。

**先更正 §2 的诊断**（就地改在 G2 那一行）。取证轮写下"155 个业务 capability 里 92 个无契约
⇒ typed 面只覆盖 40%"，D10 因此给了三选一。把三个来源分开量之后（`wt_v18b7step/probe_readings_step.log`）：

| 口径 | 实测 | 说明 |
|---|---|---|
| 注册 capability（PROVIDERS 全集） | **172** | 派生自注册表 |
| 走通用迁移网关 | **167** | `MIGRATED_CAPABILITIES`，由 `MIGRATED_BINDINGS` 派生 |
| 走内核专属执行体 | **7** | `bars` `minute` `quotes` `security_count` `security_list` `snapshot` `trades` |
| 专属执行体且不在迁移表（真协议原语） | **5** | `bars` `quotes` `security_count` `security_list` `snapshot` |
| 有专属 Typed Query 契约 | **63**（契约类 63 个） | 且 63 个**全部**同时也在派发面上 |
| 三者之外（无任何声明形状） | **0** | 这一格才是缺口，本轮起按 ERROR 阻断 |
| 旧口径那 92 条 PENDING | **92 / 92 全在派发面上** | 没有一条是"实现不了"或"没人接线" |
| 手抄豁免名单 `_INTERNAL_CAPABILITIES` | **17 项 = 12 项与迁移表逐字重复 + 5 项原语** | 12 项重复=纯抄；5 项原语可由 `executor_name` 现算 |

也就是说："还差 92 份契约"既不是功能缺口也不是缺陷，而是**分母被读错**的产物——`registered - typed`
里混进了 109 个设计上从来就不需要专属契约的能力。而那份 17 项名单里 12 项与迁移表重复的部分
是真正的危险：把某个名字的迁移绑定摘掉，审计不会有任何反应（M5 就是照这一格设计的）。

**改了什么**（4 个文件、三笔提交，合计 `216 insertions / 316 deletions`）：

| 文件 | 动作 |
|---|---|
| `scripts/contract_audit.py` | 删掉三条手抄/死代码：`_DOMAIN_TYPED`（63 项名单，全文件只有一处定义、零引用）、`_INTERNAL_CAPABILITIES`（17 项）、`_iter_domain_capabilities()`。新增三个派生入口 `registered_capabilities()` / `gateway_capabilities()` / `typed_capabilities()` 与三格规模下限 `MIN_REGISTERED=150`、`MIN_TYPED_COVERAGE=50`、`MIN_GATEWAY_COVERAGE=150`。`audit_registry_coverage()` 从"注册表有、契约没有 ⇒ PENDING"改成"**既无契约也不在任何派发面 ⇒ ERROR**"，并加三格"来源解析不出来就红"的自检。报告头现算现印形状来源，docstring 里的计数全部撤掉（规则 4 那句"当前为 0 项"也是抄本，一并删） |
| `tests/v14/test_contract_automation.py` | 5 条 → 11 条，且**判据只有一份实现**：同一个"哪些算具体契约"的遍历原本在本文件里抄了 4 份，其中 2 份各带一份手抄 kwargs 名单（`{"index_code": "000300"}` 这类），契约类一改构造函数副本会各自静默少算；现在统一走 `ca` fixture 复用脚本口径。新增：派生形状覆盖、三格下限、"注册表塌成 1 个必须红"、"契约面塌成 1 个必须红"、"遮掉派发面缺口必须现形"、"派发面确实来自生产表" |
| `tests/architecture/test_doc_code_consistency.py` | `test_contract_audit_docstring_numbers_match_the_audit` 从"抄的数字对不对"换成"**有没有再抄**"（出现 `N 个/N 项` 即红）+ 三格下限自检。射程是**每一段 docstring**：本轮一开始只扫模块那一段，而函数说明里还留着 `17 个名字／12 个重复／9 个领域基类` 三个数——前者是旧名单的历史规模（该记在有取证日志可查的本文件里），后者是当前口径的抄本，两处都从代码散文里删掉。另删 `_business_capabilities()`（它调的正是被删的 `_iter_domain_capabilities`）。这是判据**换形**不是删除：职责由新形态继续承担，覆盖面从 155 的手抄口径变成 172 的全注册表 |
| `README.md`（两行） | 该脚本的说明原来写着"契约待补项按 PENDING 报告"——本轮之后规则 1 不再产 PENDING，改成"注册能力缺声明形状即 ERROR 阻断；Domain Record 待映射项按 PENDING 报告" |

**与并行会话台账的一句话**（不改他们的文件，只在此登记）：F-25 的裁决句写着"**没有**把 PENDING
升级为阻断——那需要一次性补 92 份契约"。本轮实测这个前提是假的：升级**不需要补任何契约**，
当前 0 项无形状，新 ERROR 今天零命中。该句的"缺口尺寸记在本行"随之失效，请台账所有者按 §16
这张表改写。

**步骤轮读数**（`wt_v18b7step` @ `27e1014` + 本步 3 文件；复制后与主树逐字节相同：原始 md5
`18e37195…` / `4a093c2a…` / `ab9c17c0…`）：环境探针 + CI 规范调用的门禁共 **10 次调用全部 rc=0**
（`gates_v18b7.log`：`3.12.13 … [MSC v.1944 64 bit]`、originality `Total: 191 / Original: 191 /
License OK: 191 / Header OK: 191 / Suspicious: 0 / External imports: 17`、spec_audit `"coverage_pct": 100.0`、
golden_audit `[GATE] all L1 verified commands have real samples (OK)` + 既有 `suspect_short` WARN、
**reachability `模块总数: 190 可达: 174 白名单豁免: 16` + `无未登记孤儿 ✓`**、
**contract_audit `63 个 Typed Query 契约 / 172 个注册 capability` + `形状来源：63 个有专属契约 /
172 个在通用派发面 / 0 个无任何声明形状` + `PASS: …` rc=0，PENDING 行数 0（旧报告 184 行 = 92×2）**、
docs links `83 files`、mypy 0 行、ruff check、ruff format）。
离线全量 junit **3661 项 / 0 失败 / 0 错误 / 7 跳过**、160.572s；覆盖率 **81.72%**
（TOTAL 22 440 stmt / 3 525 miss / 5 996 branch / 1 002 partial），`Required 77.0% reached`，
**阈值未动**。判据规模 3655 → **3661（+6，全部在 `tests/v14`：5 → 11）**，零删除、零 skip。
本轮改动全在 `scripts/` 与 `tests/`，`tstdx/` 一行未动，分母恒为 22 440。
注：这一轮的 `contract_audit` 一格改用 `--ci` 调用（第 6 轮跑的是裸脚本），是收紧不是放松。

**反证证据**（`wt_v18b7step/mutate_v18b7.log`，harness `rc=0`；10 格全部按预期变色，每格跑完即按 md5 还原）：

| 格 | 动了什么 | 结果 |
|---|---|---|
| V1 | 不改动：拿**提交前版本**的脚本（`_base_contract_audit.py`）跑同一棵树 | `rc=0`、PENDING **92 格**（打印 184 行）⇒ 老门禁的原始形状 |
| V2 | 老口径下往注册表塞一个"无契约也无派发面"的能力 | ERROR 集合为空，只落一条 PENDING ⇒ 老尺子对**真缺口**也哑 |
| M1 | 把缺口计算摘成 `uncovered = []` | `tests/v14` 红（`test_masking_the_dispatch_face_exposes_the_gap`）|
| M2 | `MIN_REGISTERED` 150 → 0 | `test_a_collapsed_registry_source_is_reported` 红 ⇒ 下限被单独盯住，不是摆设 |
| M3 | `MIN_TYPED_COVERAGE` 50 → 0 | `test_a_collapsed_typed_source_is_reported` 红 |
| M4 | `if got < floor:` → `if False:`（整段自检 disable） | 两格下限判据同时红 |
| M5 | 派发面遮成只含网关（摘掉"专属执行体"那一支） | `test_coverage_sources_agree_with_production_tables` 红 ⇒ 手抄名单能藏的事，派生口径藏不住 |
| M6 | 在脚本 docstring 里再抄一个"92 个待补" | `test_contract_audit_docstring_numbers_match_the_audit` 红 |
| M7 | 脚本里重新出现手抄基类名字符串 | `test_typed_query_denominator_is_not_a_hand_copied_list` 红 |
| M8 | 往 `gateway_capabilities()` 的 docstring 里塞一句"旧名单 17 个名字的规模" | `test_contract_audit_docstring_numbers_match_the_audit` 红 ⇒ 判据射程含函数级散文，不只模块那一段 |

**M8 的来历要记一句**：本轮新写的规则在脚本 docstring 里留下一句"散文（含本 docstring）不抄任何
计数"，而同一个文件的**函数** docstring 里当时还写着 `17 个名字／12 个重复／9 个领域基类`——
判据最初只扫模块那一段，于是这句话自己就是它所禁止的东西。是复核 §16 那张表时对着文件读出来
的，不是门禁报的。修法是把范围换成"AST 里每一段 docstring"（M8 盯住这一格），并把三处数字从
代码里删掉：旧名单规模属历史证据，留在本文件这张带日志出处的表里；基类个数是当前口径的抄本，
删掉后由 `_typed_domain_base_names()` 现算。

**M3 那一格值得单独记**：把契约来源遮成只剩 1 个时，`registered - typed - gateway` 仍然是**空**
（派发面盖住一切），也就是说"缺口=0"这个读数是彻底假的——只有规模下限认得出来。这正是本轮
给三个来源各配一条下限的全部理由，也是它比"数一数报多少人"更强的地方。

**ship 轮（提交树复测）**：三笔代码提交 `a98755d`（规则与判据）+ `e26300e`（把说明里的副本数
改成实测的 4）+ `c7db30b`（把散文判据的射程扩到每一段 docstring + README 两行）落到 main 后，
另起 `wt_v18b7ship` @ `c7db30b`，被跟踪文件 **0 处改动**，10 次调用**全部 rc=0**，摘要行与步骤轮
逐字相同（`gates_ship.log`：`Total: 191 / Suspicious: 0`、spec_audit `"coverage_pct": 100.0`、
`模块总数: 190 / 可达: 174 / 白名单豁免: 16 / 无未登记孤儿 ✓`、`形状来源：63 / 172 / 0`、
`PASS: 172 个注册 capability 全部落在声明形状之内`、docs links 83 files）。离线全量 junit
**3661 / 0 / 0 / 7**、159.363s、覆盖率 **81.72%**（TOTAL 22 440 / 3 525 / 5 996 / 1 002）。
**本轮两棵树的 `tstdx/` 覆盖表逐行相同**（`diff` 空输出），连 §14、§15 各见过一次的那行
`transport/pool.py` 这次也同为 113 miss / 37 partial——它仍是抖动行，只是这一轮两棵树抖到了同一格，
所以本轮没有需要归因的差值；改动全在 `scripts/` 与 `tests/`，`tstdx/` 一行未动，分母恒为 22 440，
覆盖率真值仍以 CI 为准，本轮不重钉基线。提交内容与实测内容按 md5 双向核对：步骤树原始 md5 与主树
逐字节相同，去掉 CR 后与 `c7db30b` 提交版逐文件相同（`18e59495…` / `1810d535…` / `ab9c17c0…`）。

**本轮明确未做**：`gateway_capabilities()` 的两支（迁移网关 / 专属执行体）在"是否需要专属契约"这一格
是**并集**关系，因此把某条能力从一支错标到另一支不会改变 172，本判据看不见这种分类漂移——
新加的对账测试只盯"派生集合与生产表一致"三条包含式，同样不区分两支，这一点写在这里而不是偷偷改掉判据；
覆盖仍是 **capability 名字级**，不核参数级（有契约但契约入参与实际 args 不符不在射程内，那是
`spec_audit` 与 F-4 命名空间白名单的域）；规则 4（Domain Record 映射）仍是 PENDING 级——
它与本轮改掉的 PENDING 不同类，"要不要给某能力建结果模型"确实是可押后的产品选择，当前 0 命中；
D10 的"核心族之外要不要补专属契约"从此变成纯产品决策，本方案不为它开账；
A1/A2/A4（README 半截）、C1/C2/C3、C4 后半（`deprecation.py` 的 D3 处置，**第 8 轮已落地，见 §17**）、D1–D4、E1–E4、
F2、F3、F4/G3、§2 G5 的声明面改动；CHANGELOG `[Unreleased]` 仍因并行会话该文件未提交而押后。

## 17. 执行记录（续）

### 第 8 轮｜轴的第六次延伸：登记越齐全，死机制越像活的（代码 `f92d827` + `cb92bc0`，11 个文件 `39 insertions / 354 deletions`）—— C4 后半按 D3 默认 (a) 落地

前七轮的轴都是同一个方向：**声明了就得兑现**（旋钮、入参、字段、符号、名单）。这一轮把轴翻过来——
`tstdx/deprecation.py` 在磁盘上、在四处登记（根级白名单、可达性豁免、裸告警豁免、公共 API 表）里
全都活着，登记越齐全，门禁看它就越健康；而它服务的对象一个都没有。这是那条轴的**反向失效模式**：
"被登记" 不等于 "被使用"，一份越写越全的豁免台账完全可以给零消费者的代码发持续供养费。
D3 给的默认处置是物理删除 + 防回潮守卫，本轮按 (a) 落地。

**取证**（本轮现测，`git grep -i deprecat 0bf2994 -- tstdx/`）：该模块 206 行、`__all__` 五项
（`DeprecationPolicy` / `deprecated` / `DeprecationInfo` / `get_deprecation_count` /
`reset_deprecation_count`）外加一个不进 `__all__` 的 `DeprecationError`，
包内**除自身外零引用**——上面那次全库 grep 的每一条命中都是另一条路径上的 `SourceDeprecated`
或 `_check_deprecated`，没有一处 `from .deprecation import`。真正在跑的生命周期信号是
`tstdx/errors.py:471` 的 `SourceDeprecated`，由 `tstdx/web/_base_core.py:349`、
`tstdx/web/_base_em.py:136`、`tstdx/web/adapters.py:156,209,216,225` 发射，
加 `tstdx/diagnostics.WarningCode` 的告警信封。所以删掉它不是"扔掉一个还没人用的正确机制"，
而是**同一件事的第二套写法且没人走**；`tests/test_deprecation.py` 那 9 项（`--collect-only` 实测，
台账里写的 13 项是抄本）全部只在给自己写的模块计数。

**改了什么**（11 个文件、两笔提交）：

| 文件 | 动作 |
|---|---|
| `tstdx/deprecation.py` | 整文件删除（206 行） |
| `tests/test_deprecation.py` | 整文件删除（130 行 / 9 项）。"删测试要有凭据"这一格本轮的凭据是文件本身：9 项断言的对象全部来自开头那一行 `from tstdx.deprecation import …`，包内其它代码零涉及；P1/P2 两格另证没有任何一条**别的**判据以"这个模块存在"为条件 |
| `tests/test_bridges.py` | #16 **换形**而不是取消：从"弃用策略从 deprecate 到 remove 至少 2 个 minor"改成"包内不留自建退役机制"，三面各一条断言——磁盘上没有这个文件、`importlib.import_module` 抛 `ImportError`、根包公共面（`__all__` 与属性）不重新导出那三个名字。套件仍为 24 项，三份写着"24 项"的文档因此不用改 |
| `tests/architecture/test_namespace_layout.py` | 根级白名单去掉 `deprecation.py`（11 → 10；该判据是**集合相等**，多一项少一项都红） |
| `tests/architecture/test_caveat_channel_gates.py` | 撤回裸告警豁免，`BARE_WARN_ALLOWED` 只剩 `CHANNEL` 一项。讽刺的是逼着撤回它的正是第 6 轮那条"豁免不能过期"的判据：模块没了，豁免就成了它自己禁止的 stale exemption |
| `scripts/_reach_allow.txt` | 删掉该模块的豁免记录（16 → 15 条；反引号符号声明 51 → 48 处，仍高于 `MIN_SYMBOL_CLAIMS = 30`） |
| `docs/api/README.md` | 删掉公共 API 表那一行——第 6 轮那道"免费守卫" `test_backticked_tstdx_paths_are_importable` 正盯这张表，写回去就是红 |
| `README.md` / `docs/ARCHITECTURE.md` | 目录树一行去掉该文件名；"根级白名单 11 项" → 10 项（后者是被 `_root_modules()` 现算钉住的事实数字，不是可自由改的文案） |
| `AUDIT_AND_BRIDGES.md` | 验收项 16 改写为新语义；两处 `make audit-bridges` → `make test-bridges`（pickaxe：`f73ef61` 加入该目标时命令就带着 `\|\| echo "⚠ bridges test 未就绪"`，失败被吞成成功；`340624c` 已删除该目标，文档还在教人跑它） |
| `GOVERNANCE.md` §4.2 | 弃用策略改为直接 `warnings.warn(..., DeprecationWarning, stacklevel=2)`；不建包内装饰器；`warnings.deprecated`（3.13）在 `requires-python = ">=3.10"` 下不可用，这一句写进政策而不是留给下个人重新发现 |

**为什么这一轮不新增判据**：删除之后"它回来"只有四种形状，每种都已被现存的尺子单独盯住——
本轮的工作因此是**证明那四把尺子真会咬**，而不是再刻第五把。

| 回潮形状 | 由谁认得 |
|---|---|
| 模块文件回到包根（登记一概不动） | `test_root_namespace_matches_whitelist`（集合相等）＋ 可达性审计 `[ORPHAN]`（它不在豁免里了）＋ #16 |
| 模块回来且四处登记全部补回 | 只有 #16（见 P2 那一格） |
| 只在公共 API 表写回一行 | `test_backticked_tstdx_paths_are_importable` |
| 只在豁免台账写回一行 | 可达性审计的 `[ALLOW-DEFECT] [dead]`（第 6 轮的战果） |
| 只把裸告警豁免写回 | `test_bare_warn_calls_stay_inside_the_channel_and_one_justified_site` |
| 只把根包 `__all__` 写回 | #16 第三面 |
| 把删掉的 9 项测试整包搬回来 | pytest 收集期 `ModuleNotFoundError`（rc=2） |

**规模账（−9 与"覆盖不降"证明）**：判据 3661 → **3652**，差值 9 项全在被删的
`tests/test_deprecation.py`；#16 换形 1 条、新增 0、被删判据 0（没有任何一条判据以"这个模块存在"
为条件，P1/P2 两格就是查这一点的）、新增 skip 0、阈值 77.0% 未动。删测试文件必须配覆盖证明，
本轮是**逐行等式**而非"大概没降"：与 `wt_v18b7ship` 的 `tstdx/` 覆盖表按（文件名 + 四个计数列）
归一后 `diff`，唯一差异是 `tstdx\deprecation.py 77 2 14 3 95%` 整行消失，其余 **140 行逐字节相同**；
TOTAL 差值 22 440−22 363 = 77 stmt、3 525−3 523 = 2 miss、5 996−5 982 = 14 branch、
1 002−999 = 3 partial，恰好就是那一行。**81.72% → 81.68% 不是回潮**：删掉一个 95% 的、
高于均值的文件，加权结果必然往下挪，而"留存代码的覆盖"两个方向都没变差。基线仍只由
CI（ubuntu + py3.11）重钉，本机数字不作依据。

**步骤轮读数**（`wt_v18b8step` @ `0bf2994` + 本步 11 文件，复制后与主树逐字节相同）：环境探针 +
CI 规范调用的门禁共 **10 次调用全部 rc=0**（`gates_v18b8.log`：`3.12.13 … [MSC v.1944 64 bit]`、
originality `Total: 190 / Original: 190 / License OK: 190 / Header OK: 190 / Suspicious: 0 /
External imports: 17`（上轮 191，正好少一个被删文件）、spec_audit `"coverage_pct": 100.0`、
golden_audit `[GATE] all L1 verified commands have real samples (OK)` + 既有 `suspect_short` WARN、
**reachability `模块总数: 189 可达: 174 白名单豁免: 15` + `无未登记孤儿 ✓`**（上轮 190 / 174 / 16）、
**contract_audit `形状来源：63 个有专属契约 / 172 个在通用派发面 / 0 个无任何声明形状` +
`PASS: 172 个注册 capability 全部落在声明形状之内`**、docs links `83 files`、mypy 0 行、
ruff check `All checks passed!`、ruff format `443 files already formatted`）。
离线全量 junit **3652 项 / 0 失败 / 0 错误 / 7 跳过**、163.943s；覆盖率 **81.68%**
（TOTAL 22 363 stmt / 3 523 miss / 5 982 branch / 999 partial），`Required test coverage of 77.0%
reached`。
两处门禁先于文档发现漂移，都是本轮改动的必然后果：`test_fact_doc_numbers_match_their_truth_source
[docs/ARCHITECTURE.md-根级模块白名单]`（11 → 10）与 `test_readme_tree_lists_existing_paths`（README 树）。
另两处不粉饰：新写的 #16 第一次 `ruff format --check` 是红的；同一棵树本步跑过两次全量，
第一次 **3 519 miss / 997 partial / 81.70%**（差值全在 `transport/pool.py`、`transport/ratelimit.py`
两行已知抖动行），日志被第二次同名覆盖，只余本节这两个数字——引用的最终读数是第二次（改动已完成）。

**反证证据**（`wt_v18b8step/mutate_v18b8.log`，harness `rc=0`；9 格，每格跑完按字节还原）：

| 格 | 动了什么 | 结果 |
|---|---|---|
| C0 | 不改动（对照） | 4 道相关门禁全绿 ⇒ 下面每一格的红都不是恒报出来的 |
| P1 | 只把模块文件放回包根（四处登记不动） | 变红 **4/5**：白名单集合、#16、裸告警豁免、可达性 `[ORPHAN] tstdx.deprecation`；docs 那格按设计仍绿（没写回表） |
| P2 | 模块回来 + 四处登记全部补回（一个"复活后处处自洽"的死机制） | 只有 #16 红 ⇒ 泛化的四把尺子都会点头，防回潮靠的是那三条专门断言，这一点记在这里而不是补一把泛尺子 |
| P3 | 只把公共 API 表那一行写回去 | `test_backticked_tstdx_paths_are_importable` 红 |
| P4 | 只把可达性豁免记录写回去 | `audit_reachability --strict` 红：`[ALLOW-DEFECT] [dead] tstdx.deprecation：模块不存在，豁免记录已失效（应删除该行）` |
| P5 | 只把裸告警豁免写回去 | `test_bare_warn_calls_stay_inside_the_channel_and_one_justified_site` 红 |
| P6 | 只把根级白名单写回去 | `test_root_namespace_matches_whitelist` 红 |
| P7 | 根包公共面重新导出 `deprecated` | #16 第三面红 |
| P8 | 把删掉的 9 项测试整包搬回来（模块不回来） | 收集期 `ModuleNotFoundError: No module named 'tstdx.deprecation'`，rc=2 |

**harness 自己的两格缺陷值得记一句**（因为它对所有后续取证脚本都成立）：主树工作副本是 CRLF，
`Path.read_text()/write_text()` 会把换行归一化，于是"按 md5 还原"必然假失败、而"锚点字符串"
必须带 `\r\n` 才命中；第一版正是栽在这里。第二版把快照改成 `read_bytes()`、还原改成
`write_bytes()`、植入改成从 `_base_*.py` 字节拷贝，并对同一文件的二次编辑复用首份快照，才拿到 `rc=0`。

**ship 轮（提交树复测）**：`f92d827`（删除 + 三面守卫 + 四处登记）与 `cb92bc0`（两份政策/验收文档）
落到 main 后，另起 `wt_v18b8ship` @ `cb92bc0`，被跟踪文件 **0 处改动**，10 次调用**全部 rc=0**，
摘要行与步骤轮逐字相同（`gates_ship8.log`：`Total: 190 / Suspicious: 0`、`"coverage_pct": 100.0`、
`模块总数: 189 / 可达: 174 / 白名单豁免: 15`、`形状来源：63 / 172 / 0` + `PASS: 172 …`、
docs links 83 files、ruff format 443 files）。离线全量 junit **3652 / 0 / 0 / 7**、171.100s、
覆盖率 **81.68%**（TOTAL 22 363 / 3 523 / 5 982 / 999）。**两棵树的 `tstdx/` 覆盖表逐行相同**
（归一后 `diff` 空输出，140 行），本轮不需要归因差值。

**本轮明确未做**：P2 那一格说明"复活且处处自洽"只有 #16 认得，而 #16 是**手抄的三条断言**
（一个文件名、一个模块名、三个公共名字），本轮没有、也不打算把它泛化成"任何被删根级模块都不得回来"
——那是白名单与可达性两把尺子的职责，泛化会造出第五把重复尺子；`warnings.deprecated` 的启用等
`requires-python` 抬到 ≥3.13 再说，本轮已把这条限制写进 GOVERNANCE；`tests/web/` 里带
"deprecation" 字样的两项测的是 `SourceDeprecated` 的下线检测，与被删机制无关，不动；
A1/A2/A4（README 半截）、C1/C2/C3、D1–D4、E1–E4、F2、F3、F4/G3、§2 G5 的声明面改动；
其余 15 条"公共 API 名义资产"豁免（`trade/`、`profile/`、`output/`、`charset/`）仍等 R-7 的
类别价值标准——本轮只删了一处**没有任何判据替它说话**的资产，那 15 条各有判据在盯（第 6 轮的
符号账），不能由本轮外推到"它们也该删"；CHANGELOG `[Unreleased]` 仍因并行会话该文件未提交而押后。
