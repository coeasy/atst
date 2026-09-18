# tstdx 行情数据分页与完整性审计及优化方案（v5，2026-09-04）

> **定位**：本文档是「数据分页与完整性」专项审计方案，与既有版本轴不重叠——
> v1=性能/可靠性/易用性优化、v2=功能扩展（N 批次）、v3=能力扩展（对标竞品）、v4=文档一致性治理。v5 聚焦回答两个问题：**① TDX 获取行情数据是否存在分页问题？② 还有哪些潜在问题？**
>
> **审计方法**：3 路并行代码审计（K 线分页 / 列表·逐笔·文件分页 / Web 源·路由·流式分页）+ 全部关键发现人工源码复核（文件:行号级）。审计范围：`tstdx/client.py`、`tstdx/protocol/parsers/`、`tstdx/web/`、`tstdx/sources/`、`tstdx/streaming/`、`tstdx/facade/`、`tstdx/integration/`。

***

## 1. 结论摘要

**分页问题确实存在，且是当前最大的数据正确性风险来源。** 核心结论三条：

1. **TDX 协议侧：`bars()`（0x052D）完全没有分页封装。** `count` 原样透传单次请求；解析器定义的 `MAX_BARS_PER_REQUEST = 800`（std7709.py:456）**是死代码，全仓无调用方引用**。服务端对超上限请求只回 ≤800 根，客户端**静默截断、无告警**。MCP 工具把 count 放宽到 2000（MAX_BARS_COUNT），放大了该风险。
2. **Web 侧：三条「静默截断/假全量」路径。** ① 东财 K 线 `lmt=count` 无上限钳制；② 东财数据中心接口（业绩/股东/解禁/研报/公告等 10+ 面）**只取首页 20 条当全量**，丢弃返回体中的 `result.pages` 总页数；③ 腾讯 K 线 `count` 无钳制，超出接口上限静默截断。另有新浪并发分页 worker 异常处理不全（429/超时击穿整个 `fetch_all`）。
3. **辅助链路：文件下载/逐笔/板块成分的翻页责任全部甩给调用方。** `file_download`（0x06B9）忽略响应 `total_len` 无多包循环，大文件静默截断；`trade_today`（0x0FC5）、`block_members`（0x1210）无全量导出助手。

除分页外，还确认 **市场编号撞码（bj→0）、跨源 datetime 口径不一致、流式跨页快照非原子 diff** 三类正确性问题（详单见 §3-§4）。

***

## 2. 分页机制现状盘点（全景表）

| 链路 | 命令/接口 | 分页封装 | 页大小 | 终止条件 | 完整性保障 | 评价 |
|---|---|---|---|---|---|---|
| K 线 | `bars()` 0x052D | ❌ 无（单次直发） | 服务端定（~800） | — | 无 | **【BUG】静默截断** |
| 历史分时 | `minute_history()` 0x0FB4 | ❌ 无 | 单日 ≤240 点 | — | 单包可容纳 | 低风险 |
| 当日分时 | `minute_today()` 0x0537 | ❌ 无 | 单包 | — | 单包可容纳 | 低风险 |
| 代码表单页 | `security_list()` 0x044D | ❌ 无 | 服务端定（~1000） | — | 无 | 需调用方手翻 |
| 代码表全量 | `export_security_list()` | ✅ max_pages 循环 | **写死 1000** | 空页/短页 | 截断 UserWarning | **【风险】阈值硬编码+无总数校验** |
| 代码表全量(facade) | `security_list_all()` | 薄包装透传 | 同上 | 同上 | 同上 | 同上（问题透传） |
| 当日逐笔 | `trade_today()` 0x0FC5 | ❌ 无（count=0 默认） | 服务端定 | — | 无 | **【风险】必漏单**（单日可达 ~18000 笔） |
| 板块行情 | `block_quotes()` 0x07E5 | ❌ 无 | 单包 | — | 无 | 中风险 |
| 板块成分 | `block_members()` 0x1210 | ❌ 无 | 单包 | — | 无 | **【风险】全 A 板块漏成分** |
| 文件下载 | `file_download()` 0x06B9 | ❌ 忽略 total_len | 单包 | — | 无 | **【BUG】大文件静默截断** |
| 批量快照 | `quotes_snapshot()` 0x054C | ✅ 80/片分片 | 80 | range 覆盖 | 失败回退逐只 0x0530 | 健壮 ✅ |
| 新浪全市场 | `fetch_all` getHQNodeData | ✅ 并发分页 | 80/页 | 空页/短页 | 无 total 校验 | **【BUG】worker 异常收口不全；【风险】失败页静默丢弃** |
| 东财 K 线 | history.py `lmt=count` | ❌ 无 | 接口上限 | — | 无 | **【BUG】静默截断** |
| 东财数据中心 | corporate.py `fetch_rows` | ❌ **只取首页** | 20/页 | — | **丢弃 result.pages** | **【BUG】假全量** |
| 腾讯 K 线 | ifzq `count` 直拼 | ❌ 无 | 接口上限 | — | 无 | **【风险】静默截断** |
| 腾讯批量行情 | TENCENT_BATCH=100 | ✅ 切批循环 | 100 | range 覆盖 | — | 健壮 ✅ |
| 路由层 | `DataSourceRouter` | — | — | — | 空列表=不可用降级；**部分数据当成功** | **【风险】截断不透传** |
| 流式 | `streaming/engine.py` | 依赖 fetch_all | 80/页 | 同上 | 跨页非原子 diff | **【风险】误判变动/漏报** |

> **「静默截断」判定**：调用方请求 N 条、服务端能力 M 条（M<N），客户端拿 M 条即返回，不告警不补拉——下游无法区分「历史只有 M 条」与「被截断」。

***

## 3. 问题详单 —— 分页类

### 3.1 【BUG·P0】`bars()` 无分页：count>800 静默截断

- **位置**：client.py:407-452（单次 `struct.pack` 直发 count 字段）；std7709.py:454-456（`MAX_BARS_PER_REQUEST=800` 死代码）；mcp_server.py:104,196（`MAX_BARS_COUNT=2000` 放宽入口）。
- **触发条件**：`bars(sym, count=1200)` 或经 MCP/facade 请求 >800 根。
- **后果**：返回 ≤800 根无告警；回测/补历史场景拿到「以为完整」的残缺序列。上市 20 年标的取月线/年线全量必然触发。
- **旁证**：`sink/local_day.py:164` 的 `max_windows` 循环让用户自己按 offset 翻页，但无 datetime 去重——库把翻页责任甩给所有调用方。transport/pool 换槽重试幂等（无状态读），但一旦将来加分页需注意重试与翻页状态交互。

### 3.2 【BUG·P0】`file_download()`（0x06B9）大文件静默截断

- **位置**：client.py:772-788（仅返回单包 `r.get("data")` 拼接，无 offset 循环）；解析器 std7709_extra.py:331（响应含 total_len 但客户端不消费）。
- **机制**：无多包循环、无断点续传、无累计长度校验；`length=0` 语义（全文件 vs 单包）未定义。F10Client.download 仅是单次包装。
- **后果**：gpcw 财务文件、板块文件等 >单包的下载拿到残缺字节，下游解析出脏数据或失败。

### 3.3 【BUG·P0】东财数据中心接口「只取首页当全量」

- **位置**：web/corporate.py `fetch_rows`（page=1、size=20 固定）；`parse_rows` 丢弃返回体 `result.pages / result.count`。
- **影响面**：业绩(performance)、股东(shareholders)、解禁(unlocks)、研报(reports)、公告(notices)、龙虎榜、大宗交易等 **10+ 个数据面全部只给首页 20 条**。
- **后果**：`api.shareholders("600519")` 看似成功实为前 20 条——**假全量比失败更危险**。

### 3.4 【BUG·P0】新浪 `fetch_all` worker 异常收口不全

- **位置**：web/adapters.py:231-243（worker 仅 `except SourceDeprecated`）；web/base.py:805-860（令牌桶等待 30s 超时抛 `WebRateLimited`）。
- **机制**：M3 引入 2-6 路并发分页后，任一页 429 限速等待超时/读超时，异常从 worker 线程逃逸（其余 worker 继续跑），`out` 缺页、`last_valid` 未收敛——**fetch_all 返回残缺数据或挂起**。
- **后果**：全市场快照（streaming 引擎上游）偶发残缺且无告警。

### 3.5 【风险·P1】`export_security_list` 满页阈值硬编码 + 无总数校验

- **位置**：client.py:740（`len(rows) < 1000` 判末页）。
- **机制**：0x044D 请求体无 limit 字段，页大小由服务端决定；客户端假设恒 1000。某主站返回 500/800/页时非末页被误判短页 → **提前截断漏券**。且不调 `security_count()`（0x044E，client.py:636-648）交叉校验。facade `security_list_all()`（api.py:641-655）为薄包装，问题透传。

### 3.6 【风险·P1】`trade_today` / `block_members` 无全量导出

- **位置**：client.py:759-765（0x0FC5 count=0 默认无循环）；client.py:1421-1428（0x1210 同）。
- **后果**：活跃股单日逐笔可达 ~18000 笔只取首页必漏单；「全 A」板块成分超单包漏股。

### 3.7 【风险·P1】新浪分页「短页即停」无 total 校验 + 失败页丢弃

- **位置**：web/adapters.py:244-267。
- **机制**：终止信号=空页/短页，不解析接口 total；盘中新股/复牌致页数变化时中间页恰短页 → 提前 stop 漏尾部页。某页失败时 `last_valid=min(page)` 丢弃该页**及其后所有已成功页**。

### 3.8 【风险·P1】东财/腾讯 K 线 count 无上限钳制

- **位置**：web/history.py:298-303（`lmt=count` 直拼）；web/adapters.py:755-758、adapters_ext.py:105-109（腾讯 `count` 直拼）。
- **后果**：`api.history("sh600519", count=5000)` 经 web 路由拿到被截断序列；与 TDX 路由混用时两链路截断点不同，**同参数不同结果**。

### 3.9 【风险·P2】盘中翻页锚点漂移无防护

- **位置**：`bars()` 的 `start` 语义=「距最新一根的偏移」（client.py:425 文档已注明）。
- **机制**：库不提供多页封装，调用方手翻时盘中新 K 线落盘致锚点漂移、页间重叠/错位；无 datetime 去重/连续性校验工具。分页框架（PG1）落地后自动覆盖。

***

## 4. 问题详单 —— 非分页类（其他潜在问题）

### 4.1 【BUG·P0】`bj` 市场编号与深圳撞码

- **位置**：client.py:56 `_PREFIX_MARKET = {"sh": 1, "sz": 0, "bj": 0}`。
- **机制**：TDX 协议北交所市场编号为 **2**（业界一致），当前 `bj→0` 把北交所标的当深圳拉——`security_list("bj")` 返回深市代码表，北交所行情/代码**完全缺失且无报错**。注释「0=深/北」与协议事实不符，属注释合理化错误实现。facade 层 `security_list_all/security_count`（api.py:653-667）经 `split_symbol` 同样受染。
- **修复注意**：需同步 `domain/symbol.py` 单一事实源与 `to_tdx_market`，并用 golden 采集 market=2 的 0x044D/0x0530 样本对拍验证。

### 4.2 【风险·P1】跨源 datetime 口径不一致

- **位置**：sources/__init__.py（router 拼接）；TDX/reader 源 Bar.datetime=`"2026-08-31 15:00"`，腾讯/东财 web 源=`"2026-08-31"`。
- **后果**：auto 路由 TDX 失败降级 web 后，同标的两次调用 datetime 形态不同；下游按 datetime join/去重错位或重复。复权口径守卫（`adjust=""`）已有先例，datetime 无守卫。

### 4.3 【风险·P1】流式引擎跨页快照非原子 diff

- **位置**：streaming/engine.py:132-141（`DeltaMerger.update` 逐 key diff）；engine.py:376-377（`cur=None` 静默 continue）。
- **机制**：全市场快照来自并发分页（跨页数秒间隔），diff 把非原子快照当同一时刻比较——跨页期间真实变动被误判「本次变动」；某轮部分标的缺失被静默跳过，**漏报**。

### 4.4 【风险·P1】路由层「部分数据当成功」不透传

- **位置**：sources/__init__.py:223-401（空列表=不可用继续降级是正确设计；但**非空但截断**的数据直接当成功返回）。
- **后果**：下游（facade/ws/mcp）无法感知「本次结果可能不完整」。与 §3 各截断问题叠加放大。

### 4.5 【风险·P2】0x0FC5 逐笔记录布局未定标

- **位置**：std7709_extra.py:171（`RECORD_SIZE=15` 推断值，代码自标注未定标；golden 样本 74B/10 条无法整除）。
- **关联**：v2 方案 P5 已立项（受阻于真机样本采集），本方案不重复立项，仅在 PG6 全量导出落地时联动验证。
- **后果**：逐笔解析存在错位/截断风险，需 golden_expand 真机定标。

### 4.6 【风险·P2】MCP `get_security_list` 单页语义

- **位置**：mcp_server.py:363-366（仅调单次 `security_list(start)`，分页交调用方且无总数提示）。
- **后果**：MCP 下游（LLM 工具调用）最易把首页当全量。待 PG6 全量助手落地后接默认全量。

***

## 5. 优化改进方案

> 编号规则：**PG**=分页体系，**DC**=数据正确性（与 v4 文档治理的 DC 前缀语境不同，此处为 Data Correctness；两者文件不同、编号体系独立，不冲突）。优先级 P0（数据正确性，立即）/ P1（完整性，本迭代）/ P2（长期）。每项含验收标准。

### 5.1 PG 批次：分页体系重构

| # | 优化项 | 方案 | 验收标准 | 优先级 | 工作量 |
|---|---|---|---|---|---|
| PG1 | **`bars()` 分页封装** | ① count>800 时循环：`start += 本页实际条数` 翻页，直至凑满/空页/短页；② 跨页 datetime 去重+连续性校验（同 category 相邻页根不重不漏）；③ `strict=True` 参数：返回条数<请求条数且非历史不足时抛 `TruncatedDataError`，默认发 `UserWarning`（与 E3 风格一致）；④ `MAX_BARS_PER_REQUEST` 从解析器移到 client 侧真正使用；⑤ 盘中翻页检测到首页 datetime 漂移时自动重对齐（重拉校准 start） | `bars("sh600519", count=2000)` 返回 ≥1200 根且 datetime 连续无重复（实机主站验证）；fake pool 单测覆盖 800 边界/末页短页/空页/服务端截断 4 分支 | **P0** | 中 |
| PG2 | **file_download 多包循环** | 读响应 `total_len`，offset 递增循环至累计长度达标；校验累计长度==total_len，不符抛 `TruncatedDataError`；F10Client.download 复用 | fake 模拟 3 包文件拼装正确；截断注入抛错 | **P0** | 中 |
| PG3 | **东财数据中心全量翻页** | `fetch_rows` 解析 `result.pages`，循环 pageNumber 至尽头（max_pages=50 防御+告警）；对外参数加 `page_size`（默认 20→100） | `shareholders("600519")` 返回全量（条数 vs 东财页数×size 对拍）；fake pages=3 单测全拉 | **P0** | 中 |
| PG4 | **Web K 线上限钳制+告警** | **实测标定后收敛范围**（2026-09-05 实测：东财 push2his `lmt=3000` 如实返回 3000 根，**无需钳制**；腾讯 ifzq `800→800 / 2000→640 / 4000→null`，钳 800）；腾讯 fqkline/mkline 钳 `TENCENT_KLINE_MAX=800`（web/limits.py 集中管理）+ 超限日志告警；东财保持透传并在 limits.py 注明实测证据 | 腾讯 count=2000 → URL 含 800 + 告警；东财 lmt=3000 透传不降级 | **P0** | 低 |
| PG5 | **`export_security_list` 动态阈值+总数校验** | ① 满页判定改为「本页==第 1 页页大小探测值」（替代写死 1000）；② 循环结束后调 `security_count(0x044E)` 交叉校验，不一致发 warning；③ 可选返回 `(rows, meta)` 形态（总数/是否截断） | fake 页大小 500 时不再提前截断；总数不一致路径单测覆盖；facade `security_list_all` 透传修复 | P1 | 低 |
| PG6 | **`export_trades_today` / `export_block_members` 全量助手** | 参照 export_security_list 模式：start 递增循环+空页/短页终止+max_pages 防御+截断告警；MCP `get_security_list` 增自动翻页开关（默认全量）；与 4.5 定标联动 | 活跃标的逐笔条数 ≈ 当日总量；全 A 板块成分 >4000 只 | P1 | 中 |
| PG7 | **新浪 fetch_all 加固** | ① worker 捕获全部 `WebSourceError` 子类：单页退避重试 2 次，仍失败记录 `failed_pages` 并继续其余页；② 结束后 `failed_pages` 非空→补拉一轮→仍失败则 warning 告知缺页页码；③ 短页终止时与接口 total（若提供）对账 | 注入 429/超时的 fake 单测：重试后成功；永久失败页不击穿、不静默、告警含页码 | **P0** | 低 |
| PG8 | **盘中翻页锚点防护文档化+工具** | PG1 覆盖 bars；docs/cookbook 增「手写翻页正确姿势」：start 语义、锚点漂移场景、公开 `dedupe_bars`/`assert_contiguous` 工具函数 | 文档+工具函数单测 | P2 | 低 |

### 5.2 DC 批次：数据正确性

| # | 优化项 | 方案 | 验收标准 | 优先级 | 工作量 |
|---|---|---|---|---|---|
| DC1 | **bj 市场编号修正（market=2）** | `_PREFIX_MARKET["bj"]=2`；同步 `domain/symbol.py` 单一事实源与 `to_tdx_market`；golden 采集 market=2 的 0x044D/0x0530 真实样本对拍；`security_count(market=2)` 验证北交所 ~260 只 | `security_list("bj")` 返回 8 开头代码且条数≈北交所总数；全量 pytest 不回退 | **P0** | 低 |
| DC2 | **跨源 datetime 口径统一** | web 源 Bar.datetime 统一为 `YYYY-MM-DD HH:MM`（日 K 补 `15:00`）；reader 源归一；router 拼接前统一 normalize；提供 `raw_datetime` 保留原值 | tdx/web 两路由同标的 datetime 字符串相等；下游 join 单测 | P1 | 中 |
| DC3 | **流式快照原子 diff** | 一轮轮询的全部页拉齐后**一次性**进 DeltaMerger（页集合完整才 diff）；部分失败该轮跳过 diff 并记 warning（替代静默 continue）；快照带轮次序号 | fake 注入缺页轮次不产生虚假变动事件；变动事件数==真实变动数 | P1 | 中 |
| DC4 | **路由层截断信息透传** | ApiResponse.extra 增加 `truncated: bool` 与 `returned/requested`；ws/mcp 响应体透传该字段 | 截断场景下游可见；集成测试断言 extra 字段 | P1 | 低 |
| DC5 | **0x0FC5 逐笔布局定标**（承接 v2-P5） | `tools/golden_expand.py` 采集活跃标的全日逐笔真实样本，反推记录布局（15B 假设验证或修正），golden 三旗标入轨；与 PG6 联动 | 解析条数==样本声明数；golden_audit --gate 通过 | P2 | 中 |
| DC6 | **MCP/WS 工具全量语义** | `get_security_list` 等 MCP 工具默认全量+翻页（PG6）；响应体带 `total_pages/complete` 元信息 | MCP 工具调用返回全量代码表 | P2 | 低 |

### 5.3 与既有版本轴的关系

- **v1**：M1（Rust 内核 0x0537/0x0530）、R2-R4、U2-U4、F1（复权）、Q4 继续有效。**PG1+DC2 应先于 v1-F1 实施**（复权计算依赖完整且口径一致的历史 K 线）。
- **v2**：P5（0x0FC5 定标）由本方案 DC5 承接联动；其余 N 批次不冲突。
- **v3/v4**：主题不同（能力扩展/文档治理），无交集。
- **PG4/DC2 与 v1-F1 耦合**：复权链路依赖 PG1+DC2 先行。

***

## 6. 实施路线

**第一批（P0，立即，~1 迭代）**：
1. **DC1** bj 市场撞码修复（一行改动+golden 验证+事实源同步，成本最低收益直接）
2. **PG1** bars 分页封装（800 边界/去重/告警/strict）——影响面最大
3. **PG7** 新浪 worker 异常收口（并发分页已上线，线上稳定性缺口）
4. **PG4** Web K 线上限钳制（低改动，防跨源不一致）
5. **PG2** file_download 多包循环
6. **PG3** 东财数据中心翻页（消灭「假全量」）

**第二批（P1）**：PG5 → PG6 → DC2（datetime 统一）→ DC3（流式原子 diff）→ DC4（截断透传），随后衔接 v1 第二批。

**第三批（P2）**：PG8、DC5、DC6 + v1/v2 遗留 P2 项。

### 验证方式（每批通用）

1. **全量 pytest 不回退**（以 CI 当前基线为准）；
2. **fake connection/source 单测**：分页边界（恰好 800/801）、末页短页、空页、服务端截断、限速 429 重试、缺页轮次——全部不依赖真实网络；
3. **golden 对拍**：PG1 用 0x052D 既有 golden 样本驱动解析器；DC1 新增 market=2 样本；`golden_audit --gate` 三旗标通过；
4. **实机冒烟**（沙箱已知可达主站：180.153.18.170 / 123.125.108.90 / 115.238.90.165）：
   - `bars("sh600519", count=2000)` 断言 ≥1200 根且 datetime 连续无重复；
   - `security_list("bj")` 断言返回 4/8 开头北交所代码（待 DC1 落地）；
   - 东财 `shareholders` 全量条数 vs 网页端对拍（待 PG3 落地）；
   - 新浪 `fetch_all` 注入失败页观察告警而非崩溃（待 PG7 落地）；
5. **性能护栏**：PG1 分页循环不得使 ≤800 根请求产生额外 RTT（仅 >800 时翻页）。

***

## 附录：修改记录

| 日期 | 记录 |
|---|---|
| 2026-09-04 | v5 初版：3 路并行审计（TDX K 线 / 列表·逐笔·文件 / Web·路由·流式）+ 人工复核（含 security_list_all 存在性核实、_PREFIX_MARKET/bj 撞码、adapters worker 异常面、history.py lmt 直拼），确认分页问题存在，产出 PG×8 + DC×6 共 14 项优化方案；与 v1-v4 版本轴对齐不撞号 |
| 2026-09-05 | **第一批 P0 落地**：DC1（bj→2：symbol.py tdx_market / client._PREFIX_MARKET / std7709.Market.BJ+infer_market+quote_request_market 显式映射，0x044E 实测 market=2→379）；PG1（bars 同步/异步分页循环+datetime 去重+strict/告警，MAX_BARS_PER_REQUEST 迁入 client 真正生效）；PG2（file_download 全量循环+total_len 校验，同步/异步）；PG3（fetch_rows all_pages+pages 元数据终止+max_pages 告警，holder_num/block_trades/unlocks/performance/forecast/ipo 默认全量）；PG4（实测标定收敛：东财无需钳制、腾讯钳 800，新建 web/limits.py）；PG7（新浪 worker 全异常收口+重试+补拉+缺页告警；顺带修复 adapters.py 缺失 warnings 导入的潜在 NameError）。新增 tests/client/test_v5_pagination.py（18 项）+ tests/web/test_v5_web_pagination.py（11 项），test_push.py bj 断言对齐 2。实测修正：东财 kline lmt=3000 如实返回，PG4 对东财不钳制（原审计假设有误） |
