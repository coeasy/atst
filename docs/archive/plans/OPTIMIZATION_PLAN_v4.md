# tstdx 文档-代码一致性治理与工具链改进计划 v4

> 版本：v4 · 2026-09-04
> 基线：1.4.0（`pyproject.toml:7` / `tstdx/__init__.py:50`），工作副本 `P:\github_public\tstdx`
> 上一版：v3（对标竞品功能扩展；其批次前缀为 B0 / P0-x / P1-x / P2-x，其中 v3-B0、v3-P0-1、v3-P0-2、v3-P1-2、v3-P1-3、v3-P2-1 已收口，v3-P0-3、v3-P0-4、v3-P1-1、v3-P2-2、v3-P2-3 待实现）
> **本版范围声明**：v4 与 v3 **正交** —— v3 谈"加什么能力"，v4 只谈**文档陈述与代码事实的偏差、以及偏差背后的工具链缺陷**。不新增业务功能，不动单位/时区/降级契约（DESIGN 生命线）。

---

## 0. 立项依据与方法

### 0.1 问题来源

对 `P:\github_public\tstdx` 全量 Markdown **41 份**（仓库根 8 + `docs/` 28〔含 `archive/` 5〕 + `PROTOCOL_SPEC/` 2 + `ORIGINALITY/` 3）逐份通读，并对其中出现的**每一个量化陈述**在仓库内实跑复核（`spec_audit`、`pytest --collect-only`、AST/正则静态计数、`import` 实测）。

### 0.0 编号约定（先自证不与既有体系撞号）

本文档**不复用**仓库既有的任何批次前缀 —— 否则就犯了自己将在 ISS-10 批评的错（同一串字符承载多种语义）：

| 本文前缀 | 含义 | 与既有体系的关系 |
|---|---|---|
| `ISS-nn` | 问题编号（由 0.2 实测表实证） | 全新。**刻意不用 `P0-x/P1-x/P2-x`** —— 该前缀已被 v3 的缺口批次占用（v3-P1-3 = 本地增量落盘），沿用必然歧义 |
| `DC-n` | 批次编号（**D**ocument-**C**onsistency） | 全新。`B0`（百度源）已被 v3 占用，`B1` 与之仅差一字符，故不用 B 前缀 |
| `R1~R6` | 根因编号 | 既有 `A1-A3` 属 POTENTIAL_ISSUES 的架构悬置项，本文用 R 区分 |
| `M1~M16` | 实测基准指标编号 | 全新 |

正文凡出现 `v3-P1-3`、`v3-P0-3` 这类**带 v3 前缀**者指 v3 批次，非本文编号；未带前缀的 `ISS-xx` / `DC-x` 一律指本文。

### 0.2 实测基准表（本计划的唯一事实来源）

| #  | 指标                      | 文档陈述                        | **实测值**                       | 实测方式                                          |
| -- | ----------------------- | --------------------------- | ----------------------------- | --------------------------------------------- |
| M1 | 协议命令账本                  | 85                          | **85** ✅                      | `len(COMMANDS)`                               |
| M2 | L1 解析器注册项               | 61（README:9,107）            | **62**                        | `@register_parser` 计数                         |
| M3 | PROTOCOL_SPEC YAML      | 「7709 族 8 条 + UNKNOWN」（README:148） | **44 份文件 / 42 个唯一 spec_id** | glob + `spec_audit` summary                   |
| M4 | spec 覆盖率                | 94.7%（OPTIMIZATION_PLAN_v2）  | **81.0%**                     | `python -m tstdx.tools.spec_audit`            |
| M5 | HTTP 端点                 | 42                          | **42** ✅                     | 路由装饰器计数                                       |
| M6 | WS JSON-RPC 方法          | 19                          | **19** ✅                     | `JsonRpcHandler.METHODS`                      |
| M7 | MCP 工具                  | 12（README:17）               | **23**                        | `mcp_server.py` 工具声明                           |
| M8 | CLI 子命令                 | 19（README:91,127）           | **顶层 25**（另 hosts/feedback 各 2 嵌套） | `add_parser` 归属分析                             |
| M9 | 门面公开方法                  | 46（README:14,111）           | **58**                        | `facade/api.py` 方法定义计数                        |
| M10 | 源码规模                    | ~37.7k 行（FEATURE_MAP）       | **45,848 行 / 114 文件**        | 全树 .py 计数（排除 `__pycache__`）                   |
| M11 | 测试规模                    | 76 文件（README:169）/ 1100+（v3） | **105 文件 / 2,004 用例**       | `pytest --collect-only`                       |
| M12 | Golden 语料               | 530 payload                 | **530 JSON** ✅               | `tests/golden/**` glob                        |
| M13 | 异常类 / 错误码               | 40+ / E1-E9                 | **42 类 / 39 码 / E1-E9** ✅   | `errors.py` AST                               |
| M14 | Web 源模块 / 源类            | 17 模块 / 45 类（README:12, api/ADR-004） | **20 模块 / 48 类** | `tstdx/web/*.py`、`^class \w+Source` |
| M15 | CI job 数                | README:170 记「六步门禁」（✅ 与 `make gates` 一致），但未说明 CI 实际 job 数 | **ci.yml 10 job** + wheels + native | workflow 结构                 |
| M16 | 对抗矩阵规模                  | 9 payload × 85 命令           | 命令账本 85，实际注册解析 62（INDUSTRIAL 文档记 610 组合） | 口径待统一                     |

**统计结论**：16 项核心指标中，**11 项文档陈述已失效或漂移**（M2/M3/M4/M7/M8/M9/M10/M11/M14 + M15 部分 + M16 口径），5 项与实测一致（M1 85 账本 / M5 42 端点 / M6 19 WS 方法 / M12 530 golden / M13 异常体系）。

> 需要如实说明的是：**README 并非全错** —— 版本号、解析器数、MCP 工具数、CLI 子命令数、门面方法数、测试文件数、协议规范族数这 7 处失真集中在"会随功能批次增长"的量上，而静态设计性描述（9 payload、E1-E9、42 端点、19 WS 方法）基本准确。这恰好印证根因 R1：**没有门禁时，只有会变的数字会烂，也不会提醒你去改。**

### 0.3 根因判断（不修根因，改完还会再烂）

| 根因 | 说明 | 对应治理 |
|---|---|---|
| **R1 无机器可校验的一致性门禁** | 仓库有 6 道代码门禁（lint/type/test/originality/spec/golden/reachability），但**没有任何一道校验文档里的数字**。全仓检索 markdownlint / lychee / linkcheck 均命中 0。数字全靠人工转录，代码涨、文档不涨。 | DC-1（本计划核心） |
| **R2 多份文档互相抄同一数字** | 同一"45 源类"散在 README:12、`docs/api/README.md`、ADR-004、FEATURE_MAP 四处；改一处忘三处 → 口径互斥。 | DC-2 单一事实源 |
| **R3 版本落笔只改 CHANGELOG** | 1.3.0/1.4.0 两次发版都没回改 README:4 的版本行（仍 1.2.0），DESIGN:1 的标题也仍写 v2.0。 | DC-1 门禁 + DC-5 发版清单 |
| **R4 审计报告是一次性快照** | `ORIGINALITY/AUDIT_REPORT.md` 停在 08-31 的 84 文件，代码已 114 文件，且仍在报告"已删除的 compat 垫片"。 | DC-3 |
| **R5 spec 主键设计缺陷** | `spec_audit` 按 `spec_id` 建 dict（`audit_all` 中 `sorted(specs.items())`），family 只是字段不是命名空间 → 跨族撞号互相遮蔽（见 §1 ISS-01）。 | DC-4 |
| **R6 覆盖率断言缺失** | `ci.yml:95` 的 spec-coverage job 只跑 `--json` **不做阈值断言**，所以 94.7%→81.0% 的回退没有任何 CI 信号。 | DC-4 |

---

## 1. 问题清单（按严重度排序）

严重度定义：**P0 = 误导使用者/掩盖真实回退**；P1 = 影响评审与维护判断；P2 = 规范性问题。

### ISS-01 [P0] `spec_audit` 静默吞掉 2 份 spec，且 CI 不阻断 【根因 R5+R6】

**事实**：`PROTOCOL_SPEC/` 有 44 份 YAML，但审计 summary `total_specs: 42`。差值不是 UNKNOWN 空目录，而是**命令号跨族撞车后被 dict 去重覆盖**：

| 冲突命令号 | 胜出的 spec | 被吞掉的 spec | 后果 |
|---|---|---|---|
| `0x0001` | `TRADE/0x0001_LOGIN` | `F10/0x0001_F10_CATALOG` | F10 catalog spec **完全不进报告**，其 golden/parser 状态无人可见 |
| `0x0100` | `TRADE/0x0100_QUERY` | `7727/0x0100_EX_MARKET_COUNT` | 7727 首条 spec 的漂移（Ledger/Parser/Golden 三旗标）**不可见** |

叠加两处既有防线**恰好都看不见这个问题**：

- `tests/test_spec_coverage.py:38` 的 `test_all_spec_ids_unique` 只比对 `PROTOCOL_SPEC/7709` **单族目录内**的「文件数 vs 解析出的 id 数」（断言 ==8），跨族撞号天然不在其射程；
- `ci.yml:86-95` 的 spec-coverage job 只跑 `--json` **不做阈值断言**。

结论：**两份 spec 被静默隐藏（44 → 42）、覆盖率口径失真 13.7 个点，而全部门禁绿灯**。这是本次审计最实质的发现 —— 它不是"某处写错了"，而是"唯一该拦住它的两处检查，各自的前提假设都只覆盖了问题的一半"。

**改进方案（三选一，推荐 A）**：

- **A. 主键改为 `(family, spec_id)`**：`load_all_specs`（**定义在 `tstdx/tools/codegen.py:76`，由 `spec_audit.py:35` 导入**）返回类型改为 `dict[tuple[str,str], dict]`；`SpecAuditResult` 增 `family` 字段；输出表增列；`--json` 保持向后兼容（只增键）。**改动面比想象大，涉及 4 个调用点**：`codegen.py:444`、`codegen.py:479`、`spec_audit.py:182`、`tests/test_spec_coverage.py:28`，以及 `tstdx/tools/__init__.py:12,24` 的再导出。
- B. 给 TRADE 族迁号段（如 `0x0F00+`）：改 6 份 draft spec + `tests/trade/`，但**只是绕过，不解决"未来新族还会撞"的结构问题**，且与"命令号即事实"的协议语义不符（真实 TDX 交易通道命令号本就是 0x0001 级）。
- C. 仅加冲突检测：`load_all_specs` 遇到重复 spec_id 时 warn/fail。最小改动，但覆盖率仍按去重后统计。

**验收**：
```bash
python -m tstdx.tools.spec_audit --json | python -c "
import json,sys; d=json.load(sys.stdin)
assert d['summary']['total_specs'] == 44, d['summary']['total_specs']
assert sum(1 for r in d['results'] if r['spec_id']=='0x0001')==2
assert sum(1 for r in d['results'] if r['spec_id']=='0x0100')==2
print('ISS-01 OK')"
```

### ISS-02 [P0] `README.md` 数字/版本 11 项失真（覆盖 12 处），用户第一眼即被误导 【根因 R1+R3】

| 行号 | 现文 | 应改为（实测） |
|---|---|---|
| 4 | 当前版本：1.2.0 | **1.4.0** |
| 9 | 85 命令账本 · 61 L1 精确解析器 | 85 账本 · **62** L1 解析器 |
| 12 | HTTP Web 45 源类（17 模块） | **48 源类（20 模块）** |
| 14 | `UnifiedQuoteAPI`（46 公开方法…） | **58 公开方法** |
| 17 | MCP stdio 12 工具 | **23 工具** |
| 91 / 127 | CLI（19 子命令） | **CLI（25 子命令）** |
| 107 | 6 族 61 解析器 | 6 族 **62** 解析器 |
| 111 | UnifiedQuoteAPI(46 方法…) | **58 方法** |
| 148 | `PROTOCOL_SPEC/`（当前 7709 族 8 条 + UNKNOWN 归档） | **6 族 44 份**（7709 8 / 7727 15 / GOODS 11 / MAC 3 / F10 1 / TRADE 6）+ UNKNOWN 归档区 |
| 169 | 全量测试（76 文件） | **105 文件 / 2004 用例** |
| 163 | 降级链 `HTTP Web 源(45)` | **源(48 类)**，或按 ISS-07 改为指向 `tstdx list` |

> 行号基于 1.4.0 工作副本，落地时以 `grep -n` 重新定位，勿盲改。

### ISS-03 [P0] 两个新包在 README 架构图中缺席，`sinks` vs `sink` 混淆 【根因 R2】

`tstdx/sinks/`（DataFrame/Parquet/DuckDB/CSV **输出层**）与 `tstdx/sink/`（**写回 vipdoc `.day` 二进制**，v3 P1-3 新增，编码为 `DayBarReader` 解码的精确逆变换）是两个方向相反的包。README:105-127 代码地图（`tstdx/` 树：105 起、127 止）只有 `sinks`（117 行）；`trade/`（协议探测，带实盘红线）同样未收录。

**风险具体化**：读者按 README 找"写 .day"会去改 `sinks/csv.py`，实际要改 `sink/local_day.py`；找不到 `trade/` 会重复实现交易探测。

**改进**：README 代码地图补两行 + 新增一段 `> sink vs sinks` 辨析（各一句 + 指向各自 `__init__.py`）；`tstdx/sink/__init__.py` 与 `tstdx/sinks/__init__.py` 各自 docstring 首行**显式声明"本包非彼包"并交叉引用**（这是唯一能在 IDE 悬浮提示里救到读者的位置）。

### ISS-04 [P1] `DESIGN.md` 头部元信息与实际形态脱节 【根因 R2】

现文（`DESIGN.md:1-6`）：标题「完整设计方案 v2.0」、文档状态「v2.0（重大升级）」、工作区 `D:\workspace\tstdx`、历史版本只指 `archive/DESIGN_v1.0.md`。

实际：正文含 **33 章**，其中 20-32 章是 v3.0 补链、33 章是 v3.1 HTTP Web 源子系统；仓库现位于 `P:\github_public\tstdx`。v2.0 设计目标表中的命令数（36+）与实测（85/62）差 2.3 倍。

**改进（不重写正文，只处理元信息与漂移表）**：
1. 标题改「完整设计方案（当前 v3.1）」，头部增**版本分层表**：明确「第 1-19 章 = v2.0 基线 / 第 20-32 章 = v3.0 补链 / 第 33 章 = v3.1 增量」，避免读者以为 4102 行文档只服务 v2.0。
2. 工作区路径**从头部元信息删除**（机器本地绝对路径写进权威设计文档必腐，改留"仓库布局"相对描述）。
3. v2.0 设计目标表加脚注：「本表为 v2.0 立项时快照，最新实测值见 `docs/OPTIMIZATION_PLAN_v4.md` §0.2」—— 把"历史表"标记为历史，而不是让它冒充当期事实。

### ISS-05 [P1] `ORIGINALITY/AUDIT_REPORT.md` 陈旧，且报告"已不存在的缺陷" 【根因 R4】

现文：日期 2026-08-31、范围 84 文件、疑似抄袭 0、许可头 84/84、外部导入 17 条；"轻微问题"表仍列 `tstdx/compat/mootdx.py`（**已随 v1.2.0 移除**）。实际代码 114 文件。

**风险**：合规档案是"能不能安全分发"的凭据，一份引用了已删除文件的报告，会被评审者解读为**报告造假或从未复核**。

**改进**：DC-3 把报告改为**工具自动生成 + 头部写"生成命令"与"生成时间"**（`check_originality` 已支持 `--json`，加个模板渲染即可，零新依赖）；旧的手工撰写正文降级为"人工结论"段，与机器数据分栏。

### ISS-06 [P1] 覆盖率两套数字并存，被误读为质量回退 【根因 R6】

`OPTIMIZATION_PLAN_v2.md` 记 94.7%，实跑 81.0%。差值**主因是设计意图**：TRADE 族 6 份 draft spec 有意不入账本（红线：不连真实券商），拉低分母。但没有任何文档说明口径，读者只能读成"覆盖率跌了 13.7 点"。

**改进（两步）**：
1. `PROTOCOL_SPEC/README.md` 增「覆盖率口径」小节：定义分子/分母，并明确 draft 族的计入规则；
2. `spec_audit` summary 增 **分 status 的分组覆盖率**（stable / verified / inferred / draft 各一行）+ 非 draft 覆盖率，让 81.0% 与 94.7% 两个口径同时可见、各自可解释。
3. `ci.yml` spec-coverage job 加阈值断言（建议 `coverage_pct >= 80` 且 `stable 族覆盖率 == 100`），否则门禁形同日志打印。

### ISS-07 [P1] `troubleshooting.md` / `FAQ.md` 按 7 个 Web 源写分支，与 48 类现状脱节

`docs/troubleshooting.md` 第 3 节的降级排障分支按「7 个 Web 源全部失败」撰写；`docs/api/README.md`、ADR-004 写「14 模块 / 45 类」。三处口径互斥，且都小于实测（20 模块 / 48 类）。

**后果（用户可感知）**：按文档排障的人会以为降级链只有 7 跳，在 `auto` 路由下反复等超时；实际源数已 48 类，`source_health` 端点返回的源列表长度都对不上文档描述。

**改进**：排障文档**不写死源数量**，改为「执行 `tstdx list`（或 `GET /sources`）查看当前实际源清单」；数量类陈述统一指向 DC-2 生成的事实源。

### ISS-08 [P1] `docs/cookbook/README.md` 自相矛盾

正文：「v0.4.0 收口 6 篇；v0.7.0 补齐至 20 篇」，实际目录仅 6 篇 + 1 索引，13 项仍挂"规划中"。

**改进**：删去"补齐至 20 篇"的既成语气表述，改为「已落地 6 篇 + 规划 13 篇（未排期）」，并在索引中给规划项标注「未落地」，避免用户照目录找不存在的食谱。

### ISS-09 [P2] ADR 文件命名与索引不合规范

ADR-001~005 塞在 `docs/adr/README.md` 里，006~010 在 `ADR-006-010.md`。后果：无法按单条决策引用/链接（`#adr-007` 跨文件锚点混乱），新增 ADR-011 时不知道往哪个文件追加。

**改进**：拆为 `ADR-NNNN-短标题.md` 每份一个，`README.md` 退化为纯索引表（编号/标题/状态/决策日期/影响面）。**纯移动与切分，不改内容**；拆分后 `git log --follow` 链保留。

### ISS-10 [P2] 规划文档命名与版本轴重名

`OPTIMIZATION_PLAN_v1/v2/v3` 的 "v" 是**文档序号**（v1→1.0.0 基线，v2→1.4.0 基线，v3→对标扩展），而 `POTENTIAL_ISSUES_AND_PLAN.md` 被 FEATURE_MAP 称为"v3 路线"，`DESIGN.md` 的 v2.0/v3.0/v3.1 又是**设计版本号**。同一串字符承载三种语义。

**改进（低成本，不重命名文件以免断链）**：在 `docs/README.md`（新建，见 DC-6）给每份规划文档标注**「批次前缀 → 已完成状态 → 基线版本」**三元组，并在各文档头部加"本文的 vN 是文档序号，非产品版本"一行。真正的批次命名统一留给 J1 文档站重组时处理。

### ISS-11 [P2] 文档内部事实冲突：`native.yml` 是否存在

`docs/POTENTIAL_ISSUES_AND_PLAN.md` 归档记录称「该 workflow 实际不存在」，但仓库内 `.github/workflows/native.yml` 确实存在（`continue-on-error: true`）。`tstdx_native/` 源码目录确已删除，与 v1.4.0 M1b 弃用决议一致。

**改进**：更正该句为「`native.yml` 存在但为不阻塞的软门禁；`tstdx_native/` 源码已随 M1b 移除，workflow 保留是为 v1.6.0 若重启 Rust 内核留位」——并在 `native.yml` 顶部注释写明"为何保留"（否则下一个人还会误删或误报）。

---

## 2. 批次规划

| 批次 | 名称 | 内容 | 规模 | 覆盖问题 | 前置 |
|---|---|---|---|---|---|
| **DC-1** | 数字事实源与一致性门禁 | 新建 `docs/FACTS.json`（机器生成）+ `tstdx/tools/doc_facts.py`（生成器）+ `--check` 模式 + CI job | M | ISS-02、ISS-07、ISS-10、根因 R1/R3 | — |
| **DC-2** | 顶层文档对齐 | README 11 处数字/版本、sink vs sinks、trade 包、门禁数量；DESIGN 元信息与版本分层 | S | ISS-02、ISS-03、ISS-04 | DC-1 |
| **DC-3** | 合规档案自动化 | AUDIT_REPORT 改由 `check_originality --json` 渲染；旧手工正文降级为结论段；compat 残留清除 | S | ISS-05、根因 R4 | — |
| **DC-4** | spec 工具链与门禁修复 | spec 主键加 family 命名空间 + 分组覆盖率 + CI 阈值断言 + 口径文档 | M | ISS-01、ISS-06、根因 R5/R6 | — |
| **DC-5** | 发版清单固化 | `CONTRIBUTING.md` 增"发版必改清单"（README 版本行 / DESIGN 分层表 / AUDIT_REPORT 重跑 / FACTS 重生成 / CHANGELOG 落笔） | S | 根因 R3 | DC-1~DC-4 |
| **DC-6** | 文档导航与体例统一 | 新建 `docs/README.md`（三元组索引 + 按角色阅读路径）、ADR 拆分、cookbook 状态更正、troubleshooting 去硬编码 | M | ISS-07、ISS-08、ISS-09、ISS-10、ISS-11 | DC-2 |

**批次 DC-7（可选，与 v3 的 J1 合并）**：mkdocs 文档站 + pdoc API 参考自动生成 —— 一旦 API 参考是生成的，DC-1 的数字漂移问题从根上消失大半。建议 DC-1~DC-6 收口后并入 J1，不单独立项。

---

## 3. 批次详解

### DC-1 数字事实源与一致性门禁（本计划的核心，其余修复靠它防复发）

**为什么先做这个**：如果不建门禁，DC-2 把 11 处数字改对，下一次加源/加命令又会立刻漂。治理成本一次性，收益是"文档数字再也不会说谎"。

**设计（零新依赖，符合 ADR-001）**：

```
tstdx/tools/doc_facts.py
  collect_facts()      # 从代码/目录实测：命令数、解析器数、端点数、WS 方法数、
                       # MCP 工具数、CLI 子命令数、门面方法数、web 源类/模块数、
                       # YAML 数、golden 数、异常类/错误码数、源码行数、测试用例数
  render_json(path)    # 写 docs/FACTS.json
  check(docs_glob)     # 扫描文档中「数字 + 名词」模式，与 FACTS 比对，不一致 exit 1
CLI:
  python -m tstdx.tools.doc_facts            # 生成/刷新 docs/FACTS.json
  python -m tstdx.tools.doc_facts --check    # 校验文档（CI 用）
  python -m tstdx.tools.doc_facts --print    # 只看不落盘
```

**`docs/FACTS.json` 结构（草案）**：

```json
{
  "generated_at": "2026-09-04T12:00:00Z",
  "source": "python -m tstdx.tools.doc_facts",
  "version": "1.4.0",
  "facts": {
    "commands_ledger": 85, "l1_parsers": 62, "protocol_spec_yaml": 44,
    "http_endpoints": 42, "ws_methods": 19, "mcp_tools": 23,
    "cli_subcommands": 25, "facade_methods": 58,
    "web_source_classes": 48, "web_modules": 20,
    "golden_payloads": 530, "error_classes": 42, "error_codes": 39,
    "test_files": 105, "test_cases": 2004, "source_loc": 45848, "py_files": 114
  }
}
```

**校验器如何避免误报**（关键，否则 `--check` 会因中文数字表述刷屏而被关掉）：
- 白名单机制：只校验**已在 `FACTS` 表里注册**的指标，模式为 `f"{n} 个?{noun}"`；
- 历史陈述豁免：允许在"变更/修复记录"章节（`## [0-9.]+ 修改记录` 及其后）内出现旧数字，用 `# facts-ignore` 行内标记兜底；
- 冲突时**只报告不自动改文**（人工判断保留哪个口径），exit code 1 阻断。

**接入**：`Makefile` 加 `doc-facts` / `doc-consistency` 两个 target；`ci.yml` 加 `doc-consistency` job（复用 lint 的 python 版本，秒级）；并入 `make gates` 第七步。

**测试**：`tests/tools/test_doc_facts.py` —— ①生成 JSON 键完整且值与直接实测一致；②故意写一个错误数字进临时 md，`--check` 必须 exit 1；③`facts-ignore` 生效；④"修改记录"章节豁免生效。

### DC-2 顶层文档对齐（机械，但要求逐条附证据）

执行顺序：先跑 DC-1 的 `doc_facts` 拿到权威值 → 按 §1 ISS-02 表逐行改 README（11 项 / 12 处，其中 CLI「19 子命令」出现在 91、127 两行）→ 补 sink/sinks/trade（README:117 现仅有 `sinks/` 一行） → DESIGN 元信息四处（标题 / 版本分层表 / 删本地绝对路径 / 历史表脚注）→ 最后 `doc_facts --check` 必须绿。

**注意**：README:148 协议规范节要按族重写（含 status 分布），并**指向 `PROTOCOL_SPEC/README.md` 的口径定义**，避免又在 README 里立一个会被抄走的新数字。

### DC-3 合规档案自动化

`check_originality` 已支持 `--json`，本批次只需：
1. 新增 `--report ORIGINALITY/AUDIT_REPORT.md` 输出 Markdown（或外部小模板脚本渲染，放 `tstdx/tools/`，勿新增第三方依赖）；
2. 报告头部固定三行：生成命令、生成时间、代码基线版本；
3. "人工结论"与"机器数据"分栏，机器数据段禁止手工编辑（加 HTML 注释标记起止）；
4. 清除 `tstdx/compat/mootdx.py` 残留条目；
5. `CONTRIBUTING.md` 写明"改完代码需重跑并连同报告一起提交"。

### DC-4 spec 工具链修复（对 ISS-01 推荐方案 A 的展开）

```
codegen.py:76   load_all_specs(spec_dir) -> dict[tuple[str,str], dict]   # 键加 family，冲突不再互吞
                + 兼容策略：保留一个按 spec_id 索引的辅助视图，供不关心 family 的调用方使用
spec_audit.py:53  SpecAuditResult: + family: str
spec_audit.py:169 audit_all():  排序键改 (family, spec_id)
spec_audit.py:257 _print_table: 首列 "family/spec_id"（宽 18），表头同步
spec_audit.py:290 _result_to_dict: 增 "family" 键（只增不改，--json 消费方向后兼容）
spec_audit.py:215 coverage_summary: + by_family 分组
                                    + coverage_non_draft_pct（排除 draft，对齐 94.7% 旧口径）
tests/test_spec_coverage.py:28  SPECS 取值方式适配新键类型；并新增跨族唯一性用例
```

配套：
- `ci.yml:95` → `python -m tstdx.tools.spec_audit --json --min-coverage 80 --require-stable-full`；
- `Makefile:148` `spec-coverage` target 同步加参；
- `PROTOCOL_SPEC/README.md` 新增「§覆盖率口径」小节（分子=Has Parser、分母=唯一 (family,spec_id) 数、draft 是否计入，两条曲线都给）；
- 回归测试：`tests/tools/test_spec_audit.py` 加断言 ①`total == 44` ②`0x0001` 出现 2 次（login + f10_catalog）③`0x0100` 出现 2 次（query + ex_market_count）④`coverage_non_draft_pct >= 80`；
- **并把 `test_all_spec_ids_unique` 的比对范围从单族扩到全树**：断言 `len(load_all_specs('PROTOCOL_SPEC')) == len(glob('PROTOCOL_SPEC/**/*.yaml'))`（当前 44 vs 42 会立刻红，改完即永久防复发）。

**风险控制**：`spec_audit` 被 `make spec-coverage`、CI、以及 `AUDIT_AND_BRIDGES` 的 C5 项引用。JSON 只增键不改名；`--min-coverage` 缺省不设，保持旧行为默认值，避免外部调用方（若有）破功。

### DC-5 发版清单固化

`CONTRIBUTING.md` 增一节「发版必改清单（版本号 X.Y.Z）」，勾选项：CHANGELOG 落笔 + README 版本行 + `pyproject.toml`/`__init__.py`（已由 S4 收敛，交叉引用）+ `python -m tstdx.tools.doc_facts` 重生成 + `make audit-bridges` 24 项 + `make gates` 七步 + `ORIGINALITY/AUDIT_REPORT.md` 重跑。**PR 模板**（`.github/pull_request_template.md`）同步加 checklist 块，让"忘记改文档"在评审时可见。

### DC-6 文档导航与体例

- 新建 `docs/README.md`：一张总索引表（文档 / 面向角色 / 批次前缀 / 已完成状态 / 基线版本 / 是否现行），外加"按角色阅读路径"表（新用户 / 迁移用户 / 协议贡献者 / 架构评审 / 接手维护）；
- ADR 拆 11 文件 + 纯索引 README；
- cookbook README 状态更正；troubleshooting 去硬编码源数；
- `native.yml` 存在性冲突更正（ISS-11）。

---

## 4. 验收标准（全量，逐条可跑）

```bash
# 1. 数字一致性：文档无过时数字
python -m tstdx.tools.doc_facts --check                       # exit 0
# 2. spec 账本无静默遮蔽
python -m tstdx.tools.spec_audit --json | python -c "
import json,sys;d=json.load(sys.stdin)['summary']
assert d['total_specs']==44 and d['coverage_non_draft_pct']>=80"
# 3. README 版本与代码同源
python - <<'EOF'
import re,importlib.util
v=open('pyproject.toml',encoding='utf-8').read()
rv=re.search(r'^version\s*=\s*"([\d.]+)"',v,re.M).group(1)
assert rv in open('README.md',encoding='utf-8').read(), rv
print("version line OK:",rv)
EOF
# 4. 合规档案为新生成
python -m tstdx.tools.check_originality --json tstdx/ > /tmp/o.json && grep -q "compat/mootdx" ORIGINALITY/AUDIT_REPORT.md && echo "FAIL: 残留" || echo "OK"
# 5. 既有门禁不回归
make gates && make audit-bridges
```

| 交付物 | 验收判据 |
|---|---|
| DC-1 工具 | 4 类新测试全绿；`--check` 在人为注入错数字时 exit 1 |
| DC-2 文档 | `doc_facts --check` 绿；§1 ISS-02 表 11 行全部可 grep 到新值 |
| DC-3 档案 | 报告含生成时间戳，且 ≤ 本次提交时间；无已删除文件条目 |
| DC-4 工具链 | total=44；两个冲突号各 2 行；CI 断言生效（人为降到 80 以下必须红） |
| DC-5 清单 | PR 模板含 checklist；`CONTRIBUTING.md` 该节存在 |
| DC-6 体例 | `docs/README.md` 存在；`docs/adr/` 11 个 ADR 文件 + 索引；无断链 |

---

## 5. 风险与不做清单

| 风险 | 应对 |
|---|---|
| 改 `spec_audit` JSON 结构影响外部消费方 | 只增键不改名；`--min-coverage` 默认关闭 |
| `doc_facts --check` 误报导致门禁被随手关掉 | 首月 `continue-on-error: true`（先观察），同时白名单化历史陈述；稳定后转硬门禁 |
| README 数字改成"活的"后仍会漂 | 数字改为「以 `python -m tstdx.tools.doc_facts --print` 为准」的表述 + 门禁双保险 |
| ADR 拆分丢历史 | 用 `git mv` + 纯切分，不改内容；拆分 PR 单独提，不与内容修订混在一个 commit |
| 与 v3 未收口批次（v3 P0-3/P0-4/P1-1/P2-2/P2-3）冲突 | v4 不新增业务代码；若 v3 期间 web 源/命令数变化，DC-1 生成器自动反映，无需协调 |

**明确不做**：
- 不重写 DESIGN 正文 33 章（只动元信息与漂移表）；
- 不重命名 `OPTIMIZATION_PLAN_v*`（断链成本高，改由 `docs/README.md` 索引消歧）；
- 不把 TRADE 族迁入 commands.py 账本（交易红线，非缺陷）；
- 不引入 markdownlint / lychee 等新依赖做文档检查（用自有 `doc_facts`，符合 ADR-001 零硬依赖，且数字校验是语义级而非格式级）；
- 不在 v4 处理真机定标 5 悬案（那是 I1/L2 批次，受阻于合规采集时段，不是文档问题）。

---

## 6. 状态跟踪

| 批次 | 内容 | 状态 |
|---|---|---|
| DC-1 | 数字事实源 `doc_facts` + 一致性门禁 | ⬜ 待开始 |
| DC-2 | README 11 项（12 处）+ DESIGN 元信息 4 处对齐 | ⬜ 待开始 |
| DC-3 | AUDIT_REPORT 自动生成化 | ⬜ 待开始 |
| DC-4 | spec 主键 (family,spec_id) + 分组覆盖率 + CI 断言 | ⬜ 待开始 |
| DC-5 | 发版必改清单 + PR 模板 | ⬜ 待开始 |
| DC-6 | docs 索引 / ADR 拆分 / cookbook / troubleshooting / native.yml | ⬜ 待开始 |
| DC-7 | （并入 v3-J1）mkdocs + pdoc，数字彻底去手工 | ⬜ 可选 |

---

## 7. 修改记录

| 日期 | 记录 |
|---|---|
| 2026-09-04 | v4 立项：通读全部文档并对 16 项量化陈述实跑复核，识别 11 项文档失真 + 6 类根因（R1 无一致性门禁为总因）。发现最实质问题为 `spec_audit` 按 spec_id 去重导致 TRADE 族吞掉 F10/7727 各 1 份 spec（44 → 42），叠加 CI spec-coverage job 无阈值断言，构成静默回退。定六批次 DC-1~DC-6（+DC-7 可选并入 J1），范围与 v3 功能扩展正交。 |
