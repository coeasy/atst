# tstdx 优化计划（合并版）

> 合并来源：① 项目全景梳理（服务面 / 发布卫生 / 长期队列）；② TDX 接口深度梳理（账本校准 / 门禁增强 / 协议扩展）。
> 基线快照（2026-09-02，v1.0.0）：核心 30,013 行 / 20 子包；测试 43 文件 **1075 用例**全绿；Golden 语料 **520**（real 50）；命令账本 **85 条**（L1=4 / 精确解析器 60 / client 在用 18）；Web 源 **14**；HTTP 端点 ~40 / MCP 10 工具 / WS 6 方法；lint 存量 **417** 项（ruff 最新规则）。
> 洁净室约束贯穿全部批次：`check_originality` 0 suspicious、接口事实须实测验证、参数未验证的端点不上线。
> **执行结果（2026-09-02，v1.1.0）**：批次 A-D 全部落实——lint 417→0、ruff format 全仓归一、账本 verified 8→11 + status 字段、MCP 10→12 工具、WS 6→7 方法、CLI +2 子命令、异步门面落地、工具控制台 UTF-8 统一；终验全绿：pytest 1100+ 用例 / check_originality 103 文件 0 suspicious / golden_audit 三旗标（--gate --require-markets --require-payloads）exit 0 / ruff check 0 违规。

## 批次总览

| 批次 | 主题 | 项 | 状态 |
|---|---|---|---|
| A | TDX 账本校准与门禁增强（P0） | A1 / A2 / A3 | ✅ 已完成 |
| B | 发布卫生（P0） | B1 / B2 / B3 | ✅ 已完成 |
| C | 服务面 parity（P1） | C1 / C2 / C3 / C4 | ✅ 已完成 |
| D | 韧性与体验打磨（P2） | D1 / D2 / D3 | ✅ 已完成 |
| E | 长期队列（P3） | E1-E10 | 📋 规划（见下文明细） |

---

## 批次 A：TDX 账本校准与门禁增强（P0）

**问题根因**：三个口径脱节——账本 tier（L1=4）低估实际解析器（60 个精确 parser）；client 在用 18 个命令号中 3 个（0x051A/0x056A/0x07E5）账本仍是 D 级；有 spec+golden 的命令（0x0010/0x0537/0x0FC5）未标 verified；facade 注释命令号与 client 实现漂移。

### A1 facade 注释漂移修复 ✅
- `facade/api.py`：`block_quotes` 注释 0x02CF → **0x07E5**；`volume_price_dist` 注释 0x02EE → **0x051A**（client.py 实测命令号为准）。

### A2 命令账本校准 ✅
- `commands.py`：`Command` 新增 **`status` 字段**（`online` / `offline` / `degraded`，默认 online）——「实测下线事实」结构化归宿。
- 0x054C / 0x053E / 0x0450 标注 **status=offline**（多主站实测无响应；0x054C client 已内置 0x0530 逐只回退）。
- 0x0010 / 0x0537 / 0x0FC5 升 **verified=True**（有 golden 实采 + 精确解析器；按账本自有升级规则）。
- 0x0FB4 / 0x06B9 保持 verified=False（语料无实采样本，不可虚标）。
- 新增 `by_status()` / `unknown_commands()` 查询助手（`unknown_command_ids` 保留为别名）。
  - **修订（2026-09-19，v17 第 46 步 / F-65）**：这句里的"别名"关系从未成立。账本侧自初始提交
    （`f73ef61` 的 `commands.py`）就只有 `unknown_command_ids()`，没有过 `unknown_commands()`。
    仓里确实有这个名字，但它是 `Sniffer.unknown_commands()`（`tstdx/transport/sniff.py`）——
    采集器给出"观察到但**未登记**"的裸命令号，与账本函数给出"已登记但**语义未经 golden 校正**"
    的 `Command` 行，是两件不同的事，名字撞车属巧合。归档原文按史保留、不回溯改写；当前口径见
    `docs/api/interfaces.md`「命令账本查询面」：`unknown_command_ids()` 与 `by_family()` 一起被
    定为账本的公开查询面，同批删掉的是 `stats()` 与 `get_command_by_name()`（不留别名）。

### A3 Golden payload 有效性下限门禁 ✅
- **问题实证**：2026-09-02 午休实采的 0x0537 样本仅 4 字节（疑似空分时布局）——`real 样本 ≠ 有效样本`。
- `golden_audit.py` 新增 **`MIN_PAYLOAD_BYTES`** 按命令下限表（单记录最小布局推算）与 **`suspect_short`** 分类：低于下限的 real 样本不计入有效 real 统计，报告单列警示；**`--require-payloads`** 将其纳入硬门禁（可选严格模式，默认 INFO 不阻断，避免误伤合法空响应）。

---

## 批次 B：发布卫生（P0）

### B1 ruff 配置段迁移 ✅
- `pyproject.toml`：top-level `select/ignore` → **`[tool.ruff.lint]`**（现行写法已被 ruff 判废弃，每次运行告警）。

### B2 lint 债务清理（417 项 → 0）✅
- 存量分布：F401×119（未用导入）/ UP035+UP037×112（typing→collections.abc 迁移）/ I001×44（导入排序）/ SIM105×28（contextlib.suppress）/ F841×23 / F822×15 / B905×11（zip strict）/ F821×9（**真实引用缺失**）/ E741×8。
- 策略：`ruff check --fix` 安全修复分推进 → F821/F822 手工修复（TYPE_CHECKING 导入等）→ `ruff format` 全仓 → 每步全量 pytest 回归。

### B3 1.1.0 版本发布 ✅
- `pyproject.toml` version → **1.1.0**；CHANGELOG `[Unreleased]` 三个批次（统一响应/信任度/扩展）+ 本次升级批次归并为 `[1.1.0] - 2026-09-02` 正式段。

---

## 批次 C：服务面 parity（P1）

**问题根因**：facade 能力（~40 方法）与 CLI（0 web 能力）/ MCP（10 工具）/ WS（6 方法）/ docs/api（新能力 0 提及）四端不对齐。

### C1 CLI 新增 web 能力子命令 ✅
- `tstdx changes`（盘中异动：`--types/--page/--size/--json`）、`tstdx hot`（人气榜：`--page/--size/--json`）。

### C2 MCP 工具扩容 ✅
- 新增 `get_stock_changes` / `get_hot_rank` 工具（inputSchema 与既有工具一致）。

### C3 WS 方法扩容 ✅
- `stock_changes` JSON-RPC 方法（异动池天然适合推送型通道，后续 E2 实时推送的基础）。

### C4 docs/api 更新 ✅
- API reference 补齐：盘中异动 / 人气榜 / 统一搜索 / 问财 / IPO / 大单流向 / 所属板块端点与 facade 方法。

---

## 批次 D：韧性与体验打磨（P2）

### D1 工具 CLI 控制台编码统一 ✅
- **问题实证**：`capture.py` 在 GBK 控制台输出乱码（golden_audit 已修，其余工具未修）。
- 新增 `tstdx/tools/_console.py` 统一助手（stdout/stderr reconfigure UTF-8 + errors=replace），`capture.py` / `spec_audit.py` / `golden_expand.py` / `check_originality.py` 全部接入。

### D2 异步门面（紧凑版）✅
- 新增 `facade/async_api.py` **`AsyncUnifiedQuoteAPI`**：核心 10 方法
  （quotes/bars/finance/minute/capital_changes/trades/stock_changes/hot_rank/wencai/search_symbols）
  经 **`asyncio.to_thread`** 桥接（方向与已删除的 `_async_bridge.run_sync` 相反：
  事件循环内跑同步阻塞调用，不阻塞 loop）；`arun()` 泛化通道可调任意
  `UnifiedQuoteAPI` 公开方法；`aquery()` 永不抛异常边界与同步版对齐。

### D3 版本一致性自检 ✅
- `tstdx/__init__.py` 版本常量与 pyproject 对齐 1.1.0；`CLI version` 子命令输出同步。

---

## 批次 E：长期队列（P3；E1/E3/E5/E7/E8/E10 已于 2026-09-02 实施 ✅）

| # | 项 | 说明 | 状态 |
|---|---|---|---|
| E1 | 0x0530 并发批采 | `TdxClient.quotes_concurrent`（线程池 + 连接池并行，保序容错）+ facade/async 镜像 | ✅ 已实施（罐头 26 用例；live 验证待主站闲时） |
| E2 | 0x0547 实时五档订阅流 | PushChannel + streaming + 心跳保活 + 断线重连完整订阅 API | 📋 排期（价值重估：盘面池命令实测下线，push 语义优先级下调） |
| E3 | 0x044D 全市场代码表导出 | `export_security_list` 分页遍历（短页停 + max_pages 防御）+ facade `security_list_all` | ✅ 已实施 |
| E4 | 0x000F GBBQ 全市场复权因子库 | 按个股查询 → 全文件拉取本地复权因子表 | 📋 排期 |
| E5 | 盘面池命令 golden 化 | capture `pool` 计划（0x07E5 四类/0x051A/0x056A）→ **实测三主站全部无响应，判定 offline 入账**；golden 化前提取决于请求参数校正 | ✅ 实测闭环（结论：不可 golden 化，client 方法保留） |
| E6 | EXT/MAC/GOODS 实采计划 | 7727 族港股/美股/期货实采 → 42 条 D 级命令 verified | 📋 排期（goods 主站连接超时，需先刷新主站池） |
| E7 | 分时族补强 | 探测 0x0FEB（服务端断连，判定不识别 → offline）/ 0x051B（有响应 2B 空布局 → degraded，语义待采） | ✅ 探测闭环 |
| E8 | Golden 深市补录 | core 计划补 m0 变体（0x000F/0x0010/0x0537/0x0FC5 × market=0）→ 实采 10/10 成功，**real 50→60，四命令 mkt=[0,1] 全覆盖** | ✅ 已实施（0x537 沪市空布局 degraded 事实入账，suspect_short 门禁跟踪） |
| E9 | `web/` 域内聚 | fundflow.py（1034 行四类职责）拆「资金流域」与「情绪池域」 | 📋 排期 |
| E10 | bench 基线 | `benches/bench_web_parsers.py`：异动池 **33.7 万 rec/s** / 人气榜 **98.4 万 rec/s**（5k 罐头） | ✅ 已实施 |

## 已知残留风险（诚实记录）

- 0x0537 沪市空分时（4B × 3）由 A3 门禁 `suspect_short` 标记；E8 深市补录证实命令本身可用（m0 619B 有效），沪市空布局根因在请求字段与主站语义差异（反转探测亦空），degraded 入账持续跟踪；
- **0x07E5 / 0x051A / 0x056A 三主站实测无响应（2026-09-02 午休，remaining=16 连头都没有）→ offline 入账**；client/facade 方法保留（参数校正后可能复活），上层调用应按 TdxError 容错；
- 0x0FEB 服务端直接断连（判定不识别，offline）；0x051B 有响应但仅 2B 空布局（degraded）；
- EXT/MAC/GOODS 三族解析器语义未实采验证（E6），对外文档已标注 best-effort；goods 主站当前连接超时，需先刷新主站池；
- 0x0547 push 帧为启发式布局（best-effort），E2 前不建议生产依赖；
- E1 live 压测未完成：13:00 后主站进入交易时段负载激增（单请求 100ms→40s），罐头层已 26 用例覆盖，待闲时复测并发收益。
