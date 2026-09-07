# tstdx 功能梳理与优化改进方案（v2，2026-09-03）

> **基线**：v1.4.0（K1 版本落笔已完成；P0/P1/P2 三批 24 项全落地，含 M5/M7、U5、F1/F2/F3/F4、Q2/Q4）。
> **本文档**：基于对 `tstdx/` 全部模块的**新一轮实测梳理**（2026-09-03），定位 **v1.4.0 之后**的功能扩展机会与潜在问题。
> **文档谱系**：v1 `docs/OPTIMIZATION_PLAN_v1.md`（P0-P2 批次，已收口）→ 本文（N 批次，新提案）。

---

## 1. 当前功能与逻辑实现梳理（实测基线）

### 1.1 规模快照（本轮实测）

| 维度 | 实测值 | 维度 | 实测值 |
|---|---|---|---|
| 版本 | **1.4.0**（pyproject.toml:7，K1 已落地） | 测试文件 | 90（tests/ 递归） |
| 注册解析器 | **62**（@register_parser） | 协议命令账本 | 85 / 5 协议族 |
| HTTP 端点 | 43（8 组，http_server.py:23-38） | WS JSON-RPC 方法 | **19**（ws_server.py:133） |
| MCP 工具 | **23**（mcp_server.py TOOLS） | 门面公开方法 | ~61（facade/api.py） |
| CLI 子命令 | ~20（cli.py add_parser） | spec_audit 覆盖率 | 94.7%（7727/GOODS/MAC 已入 spec） |
| Web 源 | 17 模块 / 45 Source 类 | native | 已废弃（v1.4.0 M1b，v1.6.0 删除） |

### 1.2 九层架构（与 FEATURE_MAP 一致，**但见 P2 节过时项**）

```
L8 工具链    tools/{capture,codegen,spec_audit,golden_audit,golden_expand,check_originality}
L7 服务面    integration/{http 42 端点, ws, mcp} + observability(3 导出器) + feedback
L6 存储离线  reader(vipdoc) + profile(帧/文件探测) + sinks(DF/Parquet/DuckDB/CSV) + i18n
L5 多源降级  sources(5 级路由 tdx→web→reader→cache→synthetic) + web(45 源)
L4 门面      facade: UnifiedQuoteAPI(~60 方法, auto/tdx/web/local 四路由+熔断) + async 镜像
L3 域模型    domain: symbol / models / calendar / adjust(复权) / finance(除权事件)
L2 客户端    client.py: TdxClient/AsyncTdxClient + std/goods/ex/mac/f10 五族 + @overload 工厂
L1 传输      transport: base(RLock 租约)/async_/pool(4 槽+心跳+退避)/ratelimit/speedtest/hosts/sniff
L0 协议      protocol: commands(85) / registry(62 L1 注册, L1/L2/L3 三级 dispatch) / parsers(6 族)
```

### 1.3 本轮梳理发现的能力边界（新增 / 与本轮强相关）

- **F10 资料链不完整**：解析器已注册 `0x0001 F10_CATALOG`（f10.py:92），
  `F10Client` 仅有 `download`/`parse_text`（client.py:1325-1332），**无 `catalog` 客户端方法**，
  但 docstring（client.py:1316-1317）却宣称「先 `catalog` 取栏目目录」——文档与实现漂移，
  用户无法枚举 F10 栏目。**详见 P1/N1。**
- **服务面能力对称（N2/N3 已收口）**：HTTP 43 端点覆盖基本面/资金/扩展市场/任务全组；
  WS 已由 9 扩至 19 方法（补 capital_changes/adjusted_bars/block_quotes/all_market/
  board/security_list/minute_klines/f10_download/f10_catalog）；MCP 已由 13 扩至 23
  工具（补 adjusted_bars/all_market/minute_klines/板块行情与成分/ex_bars/goods_bars/
  index_list/security_list/search_symbols）。三服务面共享同一批门面/客户端方法。
- **复权链路已通但无缓存**：`adjusted_bars` → `capital_changes` + `AdjustEngine.compute_factors`
  （domain/adjust.py:103），每次调用重取除权事件，无因子/事件本地缓存。**详见 N4。**
- **分时/分钟 API 三入口并存**：facade 同时有 `minute`（0x0537 双路）、`minute_web`
  （强制 HTTP 当日分时）、`minute_klines`（分钟 K 线，A 股腾讯/港美东财）——语义重叠易混。**详见 N6。**
- **数据正确性悬案仍在**：0x0FC5 逐笔布局 **15B 消费与 golden 74B 不整除**（std7709_extra.py:161-167
  ⚠️ 自述「真实布局仍含未识别字段」）；0x0530 反转族/0x0547 bj/0x1300 diff base/std7727 base/
  指数 4 字节尾——真机定标周（v1 的 I1）未执行。**详见 P5。**

---

## 2. 功能扩展机会（N 批次新提案）

### N1 F10 栏目目录客户端方法（高价值，低风险）⭐ ✅ 已落地

**现状**：`0x0001 F10_CATALOG` 解析器已注册（f10.py:92），但 `F10Client` 无 `catalog()` 方法。
用户只能 `request(0x0001, ...)` 裸调或写死 filename。

**建议**：
- `F10Client.catalog(symbol) -> list[dict]`（同步）+ `AsyncF10Client.catalog`（异步镜像），
  解析 0x0001 栏目目录，输出 `[{file_index, filename, ...}]`；
- 门面 `UnifiedQuoteAPI.f10_catalog(symbol)`；
- HTTP `/f10/{symbol}/catalog` 端点 + MCP `get_f10_catalog` 工具 + CLI `tstdx f10 <symbol>`;
- 补齐 F10Client docstring（修 P1）。

**验收**：`F10Client.catalog` 有离线合成载荷测试；服务面三处接线各 ≥1 用例；`make gates` 绿。
**落地记录**：客户端方法（client.py）+ 门面/HTTP/MCP/CLI/WS 五处接线 + `test_f10_client.py` 离线用例，全量 pytest/ruff 绿。

### N2 WebSocket 方法面扩展（服务面 parity）⭐ ✅ 已落地

**现状**：WS `METHODS` 仅 9 个（ws_server.py:125-135），远窄于 HTTP 42 端点。

**建议**：WS JSON-RPC 增加：`capital_changes`、`adjusted_bars`、`block_quotes`、
`all_market`、`board_list`/`board_members`、`security_list`、`minute_klines`、
`f10_download`——每个复用已有客户端方法，走同一错误映射。

**验收**：每个新方法离线 fake 客户端用例；推送侧不受影响。
**落地记录**：`METHODS` 9→19（另补 `f10_catalog`）；跨源方法（adjusted_bars/all_market/
board*/minute_klines）经 `_facade_obj()` 走 `UnifiedQuoteAPI`，其余复用客户端；
`tests/integration/test_ws_server_f4.py` FakeClient/FakeFacade 全覆盖。

### N3 MCP 工具扩展（12 → 23）⭐ ✅ 已落地

**现状**：MCP 工具 12 个（mcp_server.py:253-431），覆盖基础行情/基本面/主站。

**建议**：新增 `get_adjusted_bars`、`get_all_market`、`get_minute_klines`、
`get_board_quotes`、`get_board_members`、`get_ex_bars`、`get_goods_bars`、
`get_index_list`、`get_security_list`、`get_f10_catalog`、`search_symbols`。

**验收**：工具表 + handler 各带离线单测；`tools/call` 往返测试。
**落地记录**：`get_f10_catalog` 随 N1 落地（13）；本轮 N3 补其余 10 个（13→23）。
`ToolSpec` 新增 `use_facade` 标记——跨源 / web 独有能力（复权/全市场/分钟K线/
板块成分/扩展市场/商品/指数/搜索）统一走惰性 `UnifiedQuoteAPI`（`MCPServer.
_facade_obj()`，与 WS 同一模式），板块行情与证券列表复用客户端；facade 工具
不触发 TdxClient 创建（保持惰性）。`tests/unit/test_mcp_server.py` 增
`stub_facade` 夹具 + 10 工具各 ≥1 离线用例 + 非法参数显式报错用例。

### N4 复权因子库 / 除权事件缓存（I2 落地）⭐ ✅ 已落地

**现状**：`adjusted_bars` 每次调用重取 `capital_changes`（在线 0x0010 或 gpcw），
无本地缓存；因子计算为纯函数（domain/adjust.py:103）。

**建议**：
- `domain/finance.py` 增加**除权事件 TTL 缓存**（按 symbol，默认 24h，进程级）；
- 可选落盘缓存（~/.tstdx/factors/{symbol}.json），离线复用；
- 缓存命中时跳过 0x0010/gpcw 网络与解析。

**验收**：同一 symbol 连续两次 `adjusted_bars`，第二次不打网络（fake 计数断言）；
缓存失效/损坏走降级重取。
**落地记录**：`domain/finance.py` 新增 `CapitalChangeCache`（TTL 内存 + 磁盘双级，
默认 24h；缓存命中跳过网络；损坏/失效自动降级重取）；`UnifiedQuoteAPI` 增
`factor_cache` 属性并接线 `capital_changes`（``use_cache=True`` 默认开）；
`tests/domain/test_finance.py` 增缓存命中/失效/损坏降级用例。

### N5 CLI 子命令补齐（低风险）✅ 已落地

**现状**：CLI 缺 `adjusted-bars`、`f10`、`all-market`、`minute-klines`、
`ex`/`goods` 深层子命令（仅 `goods` 一个）。

**建议**：新增 `tstdx adjusted-bars <symbol> [--method qfq|hfq|fixed|none]`、
`tstdx f10 <symbol> [--file <name>]`、`tstdx all-market [--node hs_a] [--source sina|tencent]`、
`tstdx minute-klines <symbol> [--period 5min]`。

**验收**：各命令带离线/合成数据用例；`tstdx --help` 分组正确。
**落地记录**：`f10` 随 N1 落地；本轮 N5 补 `adjusted-bars`（门面 F1 链路，
`--method` 四项）、`all-market`（sina/tencent + node/page-size/max-pages）、
`minute-klines`（A 股腾讯 mkline / 港美东财 push2his）。三者统一走
`UnifiedQuoteAPI` 上下文管理器 + `to_dicts` 序列化 + `_print_bars_table` 排版；
`tests/unit/test_cli_semantics.py` 增 `TestN5FacadeSubcommands`（mock 门面，
7 用例：默认参数 / 显式 flag / 缺参 SystemExit）。

### N6 分时/分钟 API 语义收敛或文档化（低风险）✅ 已落地

**现状**：`minute`（0x0537 双路）/ `minute_web`（强制 web 当日分时）/ `minute_klines`
（分钟 K 线）三入口并存。

**建议**：选「文档化 + 命名澄清」而非破坏性收敛——在 `minute` docstring 明示
「如需分钟 K 线请用 `minute_klines`，如需强制 web 当日分时用 `minute_web`」，
并补 cookbook 小节；不删方法（兼容面稳定）。

**验收**：docstring + 迁移说明一致；无行为变更。
**落地记录**：`facade/api.py` 三方法 docstring 各增 N6 语义澄清 note（互指）；
`docs/cookbook/README.md` 增「分时 / 分钟 K 线 API 选型」小节 + 对照表；无行为变更。

### N7 all_market 扩展市场（港股/美股）⭐ ✅ 已落地

**现状**：`all_market` 仅 `sina`/`tencent` 两源，覆盖 A 股（node=hs_a/cyb）。

**建议**：评估为港股（hk）全市场枚举（腾讯 `getBoardRankList` hk 板块）与美股
加 `source="hk"`/`"us"` 语义，或至少在 docstring 标注「仅 A 股」避免误用。

**验收**：能力声明与 docstring 一致；若实装则带腾讯 hk/us 冒烟（有界）。
**落地记录**：选「标注 + 显式拒绝」方案（不实装整市场枚举）——`web/facade.py`
`all_market` 对 ``node ∈ {hk, us, hk_main, us_main}`` 显式 ``ValueError``
（提示改用 `hk_quotes`/`us_quotes`/`klines`/`minute_klines`）；`facade/api.py`
`all_market` docstring 增 N7 能力边界 note；`test_easyquotation_alignment.py`
含 hk/us 显式拒绝用例。

---

## 3. 潜在问题清单（本轮实测确认）

| # | 严重度 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| P1 | **高**（文档漂移） | ~~`F10Client` docstring 引用不存在的 `catalog` 方法；0x0001 解析器存在但无客户端入口~~ ✅ 已修复（随 N1 落地） | client.py:1316 vs f10.py:92 | 随 N1 补方法并修 docstring |
| P2 | 中 | ~~FEATURE_MAP §1.1/§1.2 架构图仍列 `compat.easy_tdx / eltdx / mootdx + web.easyquotation` 层~~ ✅ 已校正（实测无 `tstdx/compat/`、无 `web/easyquotation.py`，架构图已是 v1.2.0 清理后形态；迁移文档 `get_index()` 指向不存在的 `fetch_index()` 已修为 `WebQuoteSession.index()`）；**easyquotation 全功能覆盖硬约束无自动化验证** ✅ 已补 | FEATURE_MAP_AND_ROADMAP.md vs 实际无 compat 层；docs/migration/easyquotation.md:51 假目标 | 校正架构图 + 新增**能力对齐测试**：`tests/facade/test_easyquotation_alignment.py` 断言 `all_market`/`bars`/`index_list`/`quotes` 与 easyquotation 四入口（`all`/`get_klines`/`get_index`/`get_stock_market`）语义对齐 + 迁移文档目标真实存在 |
| P3 | 中 | ~~CI 9 job 全 `ubuntu-latest`，不验证 Windows~~ ✅ 已修复 | ci.yml（全 ubuntu） | test job 增 `windows-latest`（3.12 单版本）——已在矩阵 include 中实装 |
| P4 | 低 | ~~`ruff format --check` 不含 `scripts/`~~ ✅ 已修复 | ci.yml format job | format 范围补 `scripts/`——已实装 |
| P5 | **高**（数据正确性） | 0x0FC5 逐笔 15B 消费与 golden 74B 不整除——真实布局含未识别字段；0x0530 反转族 / 0x0547 bj / 0x1300 diff base / std7727 base / 指数 4 字节尾 全部待真机定标 | std7709_extra.py:161-167 ⚠️ 自述 | 真机定标周（v1 I1 落地）；**当前状态：受阻于真机样本（合规非交易时段采集前置项），无法离线臆造布局——已审查并维持 `guarded_count` 15B 守卫红线（IntegrityViolation 降级），`TestP1eTradeTodayAlignment` 锁定声明/守卫/消费三者一致；状态见 §4 批次 N2** |
| P6 | 中 | 性能只有内存基线（benches/bench_memory.py），无解析热路径耗时基线/门禁 | benches/ 仅有 bench_memory | 补解析/请求耗时基准 + nightly 门禁 ✅ 已落地：`benches/bench_time.py`（parse_quotes/parse_kline/serialize 三类，合成离线）+ CI benchmark-smoke 增 P6 校验 + `tests/unit/test_bench_time.py` 4 冒烟 |
| P7 | 低 | ~~`minute`/`minute_web`/`minute_klines` 三入口语义重叠易误用~~ ✅ 已随 N6 文档化 | facade/api.py:477/722/733 | 随 N6 文档化 |
| P8 | 低 | ~~WS/MCP 服务无鉴权（本地回环假设未文档化）~~ ✅ 已明示 | ws_server.py / mcp_server.py | docstring 明示「仅限本地/受信网络」——`ws_server.py` 增 P8 warning（受信网络假设 + 网关建议）；`mcp_server.py` 增 P8 warning（stdio 信任边界） |
| P9 | 低 | ~~`get_client` 对未知 kind 静默回退 `TdxClient`~~ ✅ 已修复（显式 `ValueError`） | client.py:1371 | 未知 kind 显式 `ValueError`（对齐 W12 显式拒绝风格） |

---

## 4. 实施路线（批次与依赖）

### 批次 N0（立即，纯增量低风险，1-2 天）
N1（F10 catalog 客户端 + docstring 修复，含 P1）+ P4（format 补 scripts/）+ P9（get_client 显式拒绝）。**✅ 已落地（2026-09-03）。**

### 批次 N1（功能纵深，服务面扩展）
N2（WS 方法扩展）+ N3（MCP 工具扩展）+ N5（CLI 子命令补齐）——三服务面统一走同一批客户端方法，共享测试。
**进度**：N1（F10 catalog 门面/HTTP/MCP/CLI 接线）、N2（WS 9→19）、N3（MCP 13→23）、
**N5（CLI adjusted-bars/all-market/minute-klines 补齐）✅ 全部落地**。

### 批次 N2（数据正确性与性能）
P5（真机定标周，依赖合规非交易时段采集——v1 I1 前置项，挡 N4 口径正确性）
+ N4（复权因子缓存，可在定标前以现状口径先行）
+ N6（分钟 API 文档化）+ P3（CI Windows 矩阵）+ P6（性能耗时基线）。
**进度**：N4 ✅（CapitalChangeCache TTL+磁盘，tests/domain/test_finance.py）、
N6 ✅（docstring + cookbook 小节）、P3 ✅（windows-latest 已在矩阵）、
P6 ✅（bench_time.py + CI smoke + 冒烟测试）；**P5 ⚠️ 受阻**——真机定标需
合规非交易时段采集样本（v1 I1 前置项，外部依赖），离线无法臆造布局；
已审查并维持 `guarded_count` 15B 守卫红线（IntegrityViolation 降级），
`TestP1eTradeTodayAlignment` 锁定声明/守卫/消费一致。**待真机样本到位后
按 v1 I1 定标周流程修正 0x0FC5/0x0530/0x0547/0x1300/std7727/指数尾布局。**

### 批次 N3（文档与生态）
P2（FEATURE_MAP 架构图校正 + easyquotation 能力对齐验证测试）+ N7（all_market 港美或标注）
+ mkdocs 文档站（v1 J1 carryover）+ P8（服务面受信假设文档化）。
**进度**：P2 ✅（架构图实测校正 + 迁移文档 `get_index()` 假目标修复 +
`tests/facade/test_easyquotation_alignment.py` 四入口语义对齐）、
N7 ✅（hk/us 显式拒绝 + docstring 边界）、P8 ✅（WS/MCP docstring 受信假设）、
**J1（mkdocs 文档站）⚠️ 未排期**（v1 carryover，常青）。

### 依赖图
```
N0（1-2 天）──→ N1（服务面三件套共享客户端方法）
N2 内：P5(真机定标) 与 N4/N6 并行；N4 建议先以现状口径落地缓存
N3 为常青批次，P2 对齐测试可在任意批次先行（低风险）
```

### 验证方式（每批通用）
1. 全量 pytest（当前基线 90 文件 / 1100+ 用例，不得回退）；
2. 新增项各自带单元/集成测试（协议类用合成载荷，服务面用 fake 客户端）；
3. `make gates` 六步（lint→format→全量→对抗矩阵→golden 三旗标→可达性）+ spec_audit 覆盖率不回退；
4. 数据正确性项以「帧结构合法但内容错误」红线为准，真机样本到位前不臆造布局。

---

## 附：修改记录

| 日期 | 记录 |
|---|---|
| 2026-09-03 | **N 批次全部收口（除 P5 受阻）**：N4 复权缓存（`CapitalChangeCache` TTL+磁盘）、N6 分钟 API 文档化（docstring + cookbook 选型小节）、N7 all_market 港美边界（hk/us 显式拒绝 + docstring）、P2（FEATURE_MAP 实测校正 + 迁移文档 `get_index()` 假目标修复 + `tests/facade/test_easyquotation_alignment.py` 四入口语义对齐）、P3（windows-latest 已在矩阵，复核确认）、P6（`benches/bench_time.py` + CI benchmark-smoke 增 P6 + `tests/unit/test_bench_time.py`）、P8（WS/MCP docstring 受信假设明示）。**P5 受阻于真机样本**（合规非交易时段采集前置项），维持 15B 守卫红线，状态文档化于 §3 P5 / §4 N2。全量 pytest / ruff / mypy 复核。 |
| 2026-09-03 | N5 批次落地：CLI 新增 `adjusted-bars`/`all-market`/`minute-klines`（统一走 `UnifiedQuoteAPI` + `to_dicts` + `_print_bars_table`），`TestN5FacadeSubcommands` 7 用例。N1 服务面批次（HTTP 43 / WS 19 / MCP 23 / CLI 补齐）全部落地。剩余：N4（复权缓存，N2 批次）、P5（真机定标）、N6/N7。 |
| 2026-09-03 | N3 批次落地：MCP 工具 13→23（`ToolSpec.use_facade` + `MCPServer.facade` 惰性接线；新增 adjusted_bars/all_market/minute_klines/board_quotes/board_members/ex_bars/goods_bars/index_list/security_list/search_symbols，各带离线用例）。基线快照更新（MCP 23）。剩余：N5（CLI 补齐）、N4（复权缓存）、N6/N7。 |
| 2026-09-03 | N0/N1/N2 批次落地：F10 catalog 客户端 + 门面/HTTP/MCP/CLI/WS 五处接线（P1 修复）、WS 方法面 9→19（N2）、get_client 显式拒绝（P9 修复）、P4 format 补 scripts/。基线快照更新（HTTP 43 / WS 19 / MCP 13 / spec 94.7%）。剩余：N3（MCP 12→20+）、N5（CLI 补齐）、N4（复权缓存）、N6/N7。 |
| 2026-09-03 | 初版：v1.4.0 之后新一轮实测梳理——N 批次 7 项扩展（F10 catalog / WS / MCP / 复权缓存 / CLI / 分钟 API / all_market 港美）+ 9 项潜在问题（P1 F10 docstring 漂移、P2 compat 架构图过时 + easyquotation 覆盖无验证、P5 0x0FC5 15B 悬案等）。 |
