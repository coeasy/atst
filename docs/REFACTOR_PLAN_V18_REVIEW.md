# tstdx v18 复评方案：主链复核、静默失败面与收口次序

> **文档状态**：待用户裁决（§5 的六个决策点）。本轮**不改任何代码**，只做一次全盘复评：
> 把磁盘上现存的东西重新量一遍，量出的新账登记进 v17 台账（F-72…F-76），并把剩下的活按
> 顺序排成可执行的 Phase（§7）。
>
> 取证时间：2026-09-20。工作树：`wt_s50review`（detached @ `616d065`）。解释器：3.13.12。
> 本文出现的**每个数字都对应本轮一份日志**（§6 逐个列名），没有任何一格来自记忆或上一轮台账。

---

## 0. 本轮实测基线（同一工作树、同一解释器）

| 项 | 读数 | 日志 |
|---|---|---|
| 九项确定性门禁 G1–G9 | **9/9 rc=0**（originality `Total: 191 / Original: 191 / Suspicious: 0`、spec_audit `total_specs 44 / coverage_pct 100.0`、golden_audit `all L1 verified commands have real samples (OK)`、reachability `无未登记孤儿 ✓`、contract_audit、docs links `82 files`、mypy、ruff check `All checks passed!`、ruff format `472 files already formatted`） | `gates.log` |
| 离线全量测试 | **3598 项 / 0 失败 / 0 错误 / 5 跳过**，170.416s，`SUITE_RC=0` | `offline.xml`、`offline.log` |
| 覆盖率 | **81.50%**（TOTAL 22406 语句 / 3569 未命中 / 5982 分支 / 1006 部分；阈值 77 未下调，`Required test coverage of 77.0% reached`） | `offline.log` |
| 注册表面 | **11 Provider × 56 channel**、`DIRECT_BINDINGS` 251 条、内核直绑三元组 17 条、能力发现面 172 个名字（内核直绑 7 ∪ 目录迁移 165） | `probe_counts.log` |
| 公开出口规模 | `tstdx.__all__` 45、`tstdx.web.__all__` 69、`WebQuoteSession` 公开方法 140、`Client` 类体公开名 15 | `probe_counts.log`、`probe_surface.log` |
| 构造开销（禁网） | `Client()` 5.3 ms、`UnifiedRuntime()` 0.6 ms、`audit_runtime()` 0.2 ms —— 每次构造都跑的注册表审计**不是**热点 | `probe_surface.log` |

## 1. 主要功能：一个入口、一条主链、四个服务面

| 出口 | 载体 | 规模（现算） | 谁钉住它 |
|---|---|---|---|
| 库面 | `Client` / `AsyncClient` → `tstdx/client/api.py` | 类体 15 个公开名 + 167 个能力经 `__getattr__` 动态出现 | `tests/architecture`、`tests/client` |
| 执行内核 | `UnifiedRuntime` → `QueryPlanner` → `DirectProviderExecutor` | 17 条内核直绑三元组、251 条直绑 | `tests/runtime`、`tests/query` |
| 会话面 | `tstdx.web.session` 的 Web Provider 会话组合层 | 140 个公开方法 | `tests/web` |
| 服务面 | CLI / HTTP（10 路由）/ WS（10 方法）/ MCP（9 工具） | 四面只翻译不执行 | 两条 AST 结构门禁（F-29 / F-56） |
| 流式面 | `Client.stream` → `StreamPlanner` → `StatefulQuoteStream` | tdx-only | F-55/F-56 判据 |
| 配置面 | `[core]`/`[hosts]`/`[rate_limit]`/`[web]`/`[security]` 五段 | 每键都有读者（ADR-016） | `tests/runtime/test_kernel_config_wiring.py`、F-43 袋级门禁 |
| 工程面 | 协议账本 44 项规格、golden 样本、`tstdx/tools/` 门禁工具 | spec_audit 覆盖 100.0% | G2/G3 |

## 2. 架构与实现细节（模块规模，`scale.log`）

| 子包 | 行数 | 文件 | 角色 |
|---|---|---|---|
| `tstdx/web/` | 17913 | 51 | Web Provider 适配器 + 会话组合层（占包体近三成） |
| `tstdx/transport/` | 5425 | 12 | 7709 同步/异步池、主机选择、限速 |
| `tstdx/protocol/` | 5066 | 16 | 命令编解码 + 协议账本 |
| `tstdx/tools/` | 4798 | 10 | 仓内门禁工具（originality / spec_audit / golden_audit） |
| `tstdx/client/` | 3406 | 11 | 唯一入口 + tdx 子客户端族（`_mixin.py` 955 行为模板层） |
| 根级模块 | 3122 | 10 | `typed_query` / `query` / `errors` / `result` 等契约文件 |
| 其余 16 个子包 | ~14000 | 66 | runtime、catalog、config、domain、streaming、integration、observability、trade（实验性模拟器）等 |

主链每一步都在同一轮里被读到：`UnifiedRuntime.execute()` 就四行——编译计划、执行、校验出处、返回
（`tstdx/runtime/kernel.py:83-87`）；内核是配置的**唯一**消费者（`tstdx/runtime/kernel.py:10-14`）；
执行器按 `(provider, channel, capability)` 三元组精确派发，未命中直绑表即回落
`_migrated_capability`（`tstdx/runtime/executor.py:70-71`）。零缓存承诺在代码里是"没有对象可读"：
缓存模块已在 Phase 2 物理删除，`ResultMeta` 也不存在缓存命中位。

## 3. 主体链路贯通判定：7 个内核直绑便捷方法逐格下场

`Client` 的 7 个便捷方法就是内核直绑的全部能力名（`probe_counts.log`）。把出网能力钉死后逐个调用
（`probe_core7.log`、`probe_quotes.log`）：

| 方法 | tdx 绑定 | 禁网下的真实下场 | 判定 |
|---|---|---|---|
| `quotes` | ✅ `_tdx_quotes` | **返回 `data=[]`、`warnings=()`、`degraded=None`**，此前发生 16 次 connect 失败 | ⚠️ 链通，但整体失败被写成空成功 → F-72 |
| `bars` | ✅ `_tdx_bars` | `AllHostsUnreachable [E2040]` | ✅ 链通、失败显形 |
| `snapshot` | ✅ `_tdx_snapshot` | `AllHostsUnreachable [E2040]` | ✅ 链通、失败显形 |
| `minute` | ✅ `_tdx_minute` | `NotImplementedFeature [E9010]`（`0x0537` request/parser 仍 inferred） | ❌ 恒定失败（已登记已写明） |
| `trades` | ✅ `_tdx_trades` | `NotImplementedFeature [E9010]`（`0x0FC5` 同上） | ❌ 恒定失败（已登记已写明） |
| `security_count` | ✅ `_tdx_security_count` | `AllHostsUnreachable [E2040]` | ✅ 链通、失败显形 |
| `security_list` | ✅ `_tdx_security_list` | `CommandOffline [E3035]`（`0x044D` 账本 offline） | ❌ 恒定失败（已登记已写明） |

**结论**：主链**贯通**——四个服务面、内核、直绑表、Web 一跳都接得上，没有任何一格是"调不到实现"的
断链。但"贯通"不等于"每格都能拿到数据"：7 格里 3 格在任何 Provider 上都注定抛错（分钟线/逐笔/
代码表，均已在 `docs/api/interfaces.md:37,38,41,172-176` 写明），其余 4 格需要真实网络才能给数；
`snapshot`、`security_count`、`security_list` 三格**各自只有 tdx 一条绑定**（`probe_core7.log` 末段）。

## 4. 不合理点清单

### 4.1 已裁决、尚未执行（不是新账）

1. **F-70 裁决 (b)**：4 份对标/审计文档仍把已删除的门面写成今天的对外接口。已取证到名字落点
   （`Temp/step49/probe_doc_names.log`：反引号方法名命中会话面/能力面的比例，逐份 33/34、9/12、6/6、
   18/23），改写尚未落盘。
2. **F-71 裁决 (c)**：`tstdx.web` 的第二条数据入口保留，但 `WebQuoteClient` 的**有序降级**必须改成
   显式单源。第 48 步只把内核自己那一跳钉住（`_web_quotes`），公开便捷入口 `get_quotes` 在
   `source` 缺省时仍会按 `[web] enabled_sources` 顺序换源——本轮再次实测：同一符号
   `provider=tencent` 与 `provider=sina` 各抛各的 `WebSourceError [E7000]`，而缺省路径不指名（`probe_quotes.log`）。

### 4.2 本轮新增（已登记进 v17 台账 §0.3）

| # | 级别 | 一句话 |
|---|---|---|
| F-72 | **P0** | tdx `quotes` 把"全部主机失败"写成"成功且没有数据"：逐只失败被塞进 `client.last_errors` 私有侧信道，内核不读，于是 `data=[]` + `warnings=()` + HTTP 200 |
| F-73 | P1 | 活文档 `docs/troubleshooting.md:97` 让人去调一个**已删除对象**上的方法（`client.router.last_errors()`）；代码里 `.router` 零命中 |
| F-74 | P1 | `tstdx/deprecation.py` 206 行兼容机制在 `tstdx/` 包内**零消费者**，只被测试钉着；它服务的门面层早已物理删除 |
| F-75 | P2 | 3 个恒定抛错的能力仍占着 HTTP 路由 / WS 方法 / MCP 工具三面入口（§3 表），调用方只能靠读文档预知 |
| F-76 | P2 | 167 个能力靠 `__getattr__` 动态出现：`dir()` 看不见、类型检查器看不见，只有 `capabilities()` 给一份 172 个名字的扁平元组 |

#### F-72 详述（本轮最重要的发现）

- **事实**：`_t_quotes` 对每只符号单独捕获 `TdxError`，写进 `errors` 列表；调用方没传 `_collect`
  时，收尾走 `self._set_last_errors(errors)` 后**照常返回已集到的行**（`tstdx/client/_mixin.py:326-364`）。
  内核 `_tdx_quotes` 正是那个"没传 `_collect`"的调用方（`tstdx/runtime/executor.py:503-505`）。
  8 台主机 × 2 轮全失败 ⇒ 返回值是空列表，`ResultMeta.warnings` 是空元组，`degraded` 是 None。
- **为什么既有判据看不见**：F-45 的 `strict` 开关读的就是 `warnings`，而这条路径压根不往里写；
  F-52/F-65/F-66 的门禁都是"声明面 ↔ 执行面"对账，不看失败投递方向；离线全量测试用假客户端，
  假客户端不会同时让所有主机失败，所以 3598 项全绿也照不到它。
- **旁证（这不是我一家之言）**：CLI 早就绕过了它——`tstdx/cli/runtime_commands.py:847-856` 在打印
  "0 条"之前专门去读 `c.last_errors` 才拿得到真实原因。库面与 CLI 面共用一个内核，却只有 CLI
  知道失败原因，这就是不对称的落点。
- **代价**：任何拿 `Client.quotes()` 做健康检查的下游（HTTP `/v13/quotes`、WS `quotes`、MCP
  `get_quotes`）在整体断网时看到的是 200/空，与"这只代码不存在"完全同形。
- **三条路径**：
  - (a) **整体失败即抛**：全部符号都失败时抛 `AllHostsUnreachable`（与 `bars` 同一条判据），部分失败仍逐只隔离；
  - (b) **失败进 warnings 通道**：保持返回空列表，但把逐只失败写进 `ResultMeta.warnings`，于是 `strict=True` 立刻可用，HTTP 也能按既有约定标 `degraded`；
  - (c) **维持现状**，只在文档写明 `quotes` 是"逐只容错、整体失败也返回空"。
  - **默认建议 (b) + (a) 的组合**：全部失败→抛（与其余 6 格同语义），部分失败→写进 `warnings`。
    纯 (c) 与"数据请求不需要缓存、直连数据源"的目标冲突：调用方无法区分"没有这只票"和"根本连不上"。

#### F-73 / F-74 / F-75 / F-76 的路径与默认

- **F-73**：(a) 该行改成指向今天真实存在的观察点；(b) 连同 `last_errors` 一起处置（见 F-74/72）；
  (c) 删掉该行。**默认 (a)**：判据侧顺带扩形——把「反引号里的 `obj.attr` 点号调用」也纳入死路径判据，
  否则同类指令还会再长出来（小写点号形状是现有两条门禁的盲区，与 F-70 是同族不同格）。
- **F-74**：(a) 物理删除 `tstdx/deprecation.py` 与其测试（与 F-18/F-65/F-68 同法：造好但无生产调用点）；
  (b) 保留并接进某个真实退役流程；(c) 移进 `tstdx/tools/` 当工程脚本。**默认 (a)**：包内零消费者 +
  门面层已删 ⇒ 它是为一条已经不存在的桥留的支架。
- **F-75**：(a) 三面继续保留但在响应里显式区分"该能力在本仓被结构化拦截"与"Provider 故障"；
  (b) 从 HTTP/WS/MCP 三面摘掉这 3 格，只留库面 + 发现面；(c) 保持现状（文档已写明）。
  **默认 (a)**：与 F-66 (c) 的"只声明名字、不声明可用性"是同一条口径的延伸，改名/摘面是破坏性变更。
- **F-76**：(a) 现状不动，把"发现面只给名字"的口径在 `docs/api/interfaces.md` 再钉一次（第 47 步已钉）；
  (b) 给目录迁移能力生成显式 `def` 桩（体积换可发现性）；(c) 收窄动态面。**默认 (a)**：
  167 个显式方法会把 `Client` 从 15 个名字撑成 180+，与 v17"收敛命名空间"的方向相反。

### 4.3 阻塞中（需要用户或 CI 环境，本轮不动）

F-37 余条（`0x000F`/`0x0010` 解码布局 + 工作日盘中复跑，实时复跑未获授权）、随之挂着的 F-38、
F-20（heartbeat 默认值需真机验证）、F-15（覆盖率阈值只准在 CI exact-head 读数上重钉）、
`v1.1.0-dev.1` 标签（用户已答"暂不打，先清完待裁决项"）。

## 5. 决策点（每条给出默认；用户不答即按默认执行）

| # | 问题 | 选项 | 默认 |
|---|---|---|---|
| D1 | F-72 的整体失败语义 | (a) 全失败即抛 / (b) 写进 warnings / (c) 维持现状只补文档 | **(a)+(b) 组合**：全失败抛 `AllHostsUnreachable`，部分失败写 `warnings` |
| D2 | F-73 死指令行 | (a) 改写 + 判据扩到小写点号形状 / (b) 只改写 / (c) 只删行 | **(a)** |
| D3 | F-74 `deprecation.py` | (a) 删除 / (b) 接线 / (c) 移入 tools | **(a)** |
| D4 | F-75 三面恒定失败入口 | (a) 保留但错误分类 / (b) 三面摘除 / (c) 不动 | **(a)** |
| D5 | F-76 动态面 | (a) 不动 / (b) 生成显式桩 / (c) 收窄 | **(a)** |
| D6 | 执行次序 | (甲) 先把 v17 已裁决两步（F-70 (b)、F-71 (c)）做完，再开 v18 新账 / (乙) 先做 P0 的 F-72 | **(甲)**：两步已有裁决、无新决策，且 F-72 的判据要复用 F-71 收口后的会话面 |

## 6. 取证日志清单（全部本轮产生，目录 `Temp/review50/`）

`scale.log`（规模）、`probe_surface.log`（直绑表/公开面/构造耗时）、`probe_core7.log`（7 格下场 +
绑定普查）、`probe_quotes.log`（禁网下 quotes 的空成功 + connect 计数）、`probe_counts.log`
（注册表/能力/会话规模）、`gates.log`（九项门禁）、`offline.log`/`offline.xml`（离线全量 + 覆盖率）。

## 7. 执行计划

| 步 | 动作 | 主要判据 | 验收 |
|---|---|---|---|
| 第 50 步 | 执行 F-70 (b)：4 份文档逐份改写成 v17 口径 | 逐份名字落点由本轮日志回查；删除会 `AttributeError` 的"校验指令" | 九门禁 + 离线全量同值 |
| 第 51 步 | 执行 F-71 (c)：`WebQuoteClient` 缺省单源、显式指名；`[web]` 两段键语义一并处置；修 `session.py:168` 假事实 | 把"缺省即降级"从可执行形状改成结构性不存在；F-20 实时复测仍不授权 → 记 blocked | 同上 |
| 第 52 步 | 清偿 F-72（按 D1） | 一条行为判据：禁网夹具下 `quotes` 全失败 ⇒ 要么抛、要么 `warnings` 非空；`strict=True` 必须能拒收空成功 | 同上 |
| 第 53 步 | 清偿 F-73 + F-74（按 D2、D3） | 死路径判据扩到小写点号形状；删除后防回潮 unimportable 守卫 | 同上 |
| 第 54 步 | 按 D4/D5 落 F-75/F-76 口径（或摘面） | 三面错误分类响应形状对账 | 同上 |
| 第 55 步 | 发布前清单复评：F-37/F-38/F-20/F-15 + tag | 需用户授权实时复跑与 CI 读数 | 单独一轮 |

每一步沿用既有落地程序：步骤树 → 逐文件字节相同搬回主树 → 提交 → `wt_sNNship` 提交树复测（九门禁
日志逐字节相同 + 离线全量读数）→ 台账记两轮数。**门禁阈值只升不降，且升只能凭 CI 环境 exact-head 读数。**
