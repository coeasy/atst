# TDX 全接口连通性矩阵（2026-09-06 实测 + P13-A 兜底补齐）

> 对全部 TDX 协议族 / 命令 / 主站池的**真实环境**逐项实测结果。
> 实测方式：真实网络直连公开主站取数（非 mock）；日期 2026-09-06（周日，
> 非交易时段——实时类返回最近交易日快照属正常）。
> 结论先行：**7709 标准行情族核心命令全部打通**；扩展市场（7727）/MAC/F10
> 内容分发为**环境级失效**（主机/命令停答），库内已有明确报错与替代路径。
>
> **P13-A 增量（2026-09-06 22:00）**：6 个「仅 tdx 路由」方法补齐兜底路径——
> `block_quotes` 增加 web 近似兜底（腾讯板块排行），`auction` / `volume_price` /
> `f10_catalog` / `ex_market_list` / `ex_instruments` 在 auto 路由下把
> `CommandOffline` / `AllHostsUnreachable` 转换为当时的 E7050「源不可用」异常，
> context 附 `alternatives` 明确可用替代方案。**该转换随 pre-v17 门面一起删除，
> v17 单内核不替换 Provider**，今天这些路径一律以 `CommandOffline` / 传输层原异常
> 结束本次查询（详见 §四、§五、§六）。入参面也已经收缩：v17 的 `Client.quotes()` 只收
> `symbols`/`provider`/`policy`/`currentness`，`route` 这个形参不存在——实测传 `route="web"`
> 在构造期即 `TypeError`，而不是 §五 末段那句历史口径说的 `ValueError`。选数据源用 `provider=`。

## 一、7709 标准行情族（逐格按命令账本判定）

> **本表的"实测"列与代码同源**：命令号、`status`、`tier`、`verified` 取自
> `atst/protocol/commands.py` 的账本，发包前拦截取自 `atst/client/core.py` 的
> `_OFFLINE_FALLBACK_OK` / `_UNVERIFIED_STRUCTURED_BLOCK`。判据是
> `tests/architecture/test_tdx_status_matrix.py`：写一个账本里没有的命令号、给一条
> offline/拦截命令标 ✅、或漏登记一条 offline/`degraded` 的 quotation 命令，都当场红。
> 三档含义——**✅**：账本 `online` 且 `verified=True`（真机 golden 锁过），结构化 API 给数；
> **⚠️**：账本上这条命令给不出可采信的内容（offline，或 `verified=False` 布局未锁定）；
> **⛔**：这条接口在 atst 的结构化入口上一次都发不出去（账本 offline 且 fail-fast，或被
> inferred 拦截），调用方拿到的是异常而不是数据。

| 接口 | 命令 | 实测 | 说明 |
|---|---|---|---|
| quotes 实时行情 | 0x0530 | ✅ | sh600519 真实价格。`datetime`/`bid`/`ask` 三格恒空，见 §一之二 |
| bars 日/分钟 K 线 | 0x052D | ✅ | day/5min 均正常，分页正常 |
| security_count 证券总数 | 0x044E | ✅ | 27904 |
| snapshot 快照合成 | 组合面 | ✅ | **不是一条命令**：`quotes` + 当日 `bars(count=1)` 合成的字典。本行此前配了一个账本里不存在的命令号，并把它叫"五档全量"——那个名字是 pre-v17 的叫法，它不含盘口深度（§一之二） |
| finance_info 财务 | 0x0010 | ⚠️ | 实采四份均回 14302 字节 / 100 行；**只有条数可采信**，逐字段语义未由 golden 锁定（F-37，账本 `verified=False`）。错位值现在会带一条 `field_out_of_domain` 告警随结果上 wire（G7，类别表见 `docs/errors.md` §一之四） |
| capital_changes 除权除息 | 0x000F | ⚠️ | 回 250 条；**只有条数可采信**，记录布局未锁定（F-37；`market`/`code`/`date` 三格实测落在域外，账本 `verified=False`）。同上，那条判断由出口处的值域尺子重新量出，不再只活在 `ParseResult.warnings` 袋里 |
| quotes_snapshot 批量快照 | 0x054C | ⚠️ | 账本 offline（多主站实测已下线），但是 `_OFFLINE_FALLBACK_OK` 里唯一被放行的一条：客户端仍按 60 只/包发一次，批量解不出或降级 L3 时**本片回退逐只 0x0530**——同一 Provider 内的分片回退，不换源 |
| minute_today 当日分时 | 0x0537 | ⛔ | 账本 `degraded`、request/parser 仍为 inferred：结构化 API 在**发包前**抛 `NotImplementedFeature`（`_UNVERIFIED_STRUCTURED_BLOCK`）。本行此前写"✅ 87 点"，那是绕过拦截直接喂字节时的读数，今天的调用方拿不到 |
| trade_today 逐笔 | 0x0FC5 | ⛔ | 账本 `online` 但布局未锁定，与 0x0537 同族：发包前抛 `NotImplementedFeature`。同名能力在 `baidu`/`tencent` 上有出路（另一条 Query） |
| security_list 代码表 | 0x044D | ⛔ | 命令已停答（6 台主站读取超时，2026-09-06 实测）；客户端 fail-fast，一调即抛 `CommandOffline`。**tdx 之外无人声明它**——"代码表改走东财 clist"那条转换是 pre-v17 门面的行为，随门面删除（§五），要代码表得调用方自己另发 Query 或读本地 vipdoc 文件 |
| minute_history 历史分时 | 0x0FB4 | ⛔ | 3 台主站 × 多日期超时；fail-fast 抛 `CommandOffline`。同名能力无出路；要分钟线只能改走 `minute_klines`（tencent / eastmoney 的 web 适配器，另一条 Query） |
| block_quotes 板块行情 | 0x07E5 | ⛔ | 三主站实测无响应；fail-fast 抛 `CommandOffline`。本行此前写的"P13-A 换到腾讯板块排行（每行附 `source` 标记）"随 pre-v17 门面删除，今天没有任何自动换源（§五） |
| auction_snapshot 竞价 | 0x056A | ⛔ | 三主站实测无响应；fail-fast 抛 `CommandOffline`。替代需调用方自选：`hot_rank`（人气榜）/ `longhu`（龙虎榜）/ `quotes`（实时快照，无盘口深度） |
| volume_price_dist 量价分布 | 0x051A | ⛔ | 三主站实测无响应；fail-fast 抛 `CommandOffline`。筹码分布为 TDX 端本地计算，web 侧无同名能力，可用 `bars` 自算量价指标 |
| security_list_legacy 代码表旧包 | 0x0450 | ⚠️ | 账本 offline；包内既无解析器也无客户端入口，只在协议档案里留名。登记在此是为了让本表覆盖账本里每一条已停答的 quotation 命令 |
| quotes_legacy 批量行情旧包 | 0x053E | ⚠️ | 账本 offline（部分主站已停用）。解析器 `QuotesLegacyParser` 仍在，但没有任何客户端方法发它——只能经通用 `request` 口手工发包，且响应按 L2 通用解析处理 |
| minute_recent 近几日分时 | 0x0FEB | ⚠️ | 账本 offline；无解析器、无客户端入口，同上 |
| minute_subplot 分时副图 | 0x051B | ⚠️ | 账本 `degraded`（有响应但布局未锁定）；无解析器、无客户端入口，同上 |

### 一之二、`quotes` / `snapshot` 回来却填不上的三格（G8）

7709 实时行情给得出价格，给不出**时间戳与盘口深度**。`Client.quotes()` / `Client.snapshot()`
返回的记录里 `datetime` 恒为 `null`、`bid` 与 `ask` 恒为空列表，而 `price`/`volume`/`amount`
都是真数。这不是丢字段，是解析器按"不臆造未锁定布局"的契约主动留空
（`atst/protocol/parsers/_std7709_quote.py`：五档尾段 73~99 字节长短随标的而变，精确布局尚未
锁定，于是整段按 LEB128 原样收进 `extra['tail_leb128']`、未识别的 `u4` 收进 `extra['_u4']`，
**绝不**把它们当价格或时间输出）。

调用方要拿到"这一笔是什么时候的"，取的是**请求时刻**而非响应时刻——响应里没有这个信息。
要盘口深度（买五卖五），7709 这条链上目前任何接口都给不了：换 `provider` 也一样，`snapshot`
这个发现名的出路只有 tdx（推导见 `docs/api/interfaces.md` 的「能力发现面」）。

同一形状在 `docs/api/interfaces.md` §6 与本节都写了，判据是
`tests/architecture/test_tdx_status_matrix.py` 的最后一节——它拿 `Quote` 的字段名回查文档，
所以把这三格从文档里删掉、或改解析器真的开始填它们，都会当场红。

## 二、扩展市场族（⛔ 环境级失效，P13-A 显式化替代方案）

| 项 | 实测 | 说明 |
|---|---|---|
| 7727 主站池（4 台候选） | ⛔ | 全部连接超时；且 13 台 7709 存活主机均未双开 7727——扩展行情服务疑似整体迁移/下线 |
| **ex_market_list** | ⛔→⚠️ | 命令 offline；v17 以 `CommandOffline` 结束（pre-v17 门面曾转 E7050 + `alternatives`）；替代需调用方自选：按市场直取行情跳过目录列举 `hk_quotes`（港股）/ `us_quotes`（美股）/ `rates`（外汇）/ `dc_query`（期货期权） |
| **ex_instruments** | ⛔→⚠️ | 同上，与 ex_market_list 同因 |
| ex_bars / ex_quotes | ⛔ | 7727 主站失效；web 侧 hk_quotes/us_quotes 已覆盖行情场景（语义直取） |
| GOODS 商品语义（0x0200 族） | ⛔ | 与 EXTENDED 共池（7727）；另 `domain.symbol` 不解析商品代码（SymbolError），双重不可用 |
| MAC 专属（0x120F 族，2 台候选） | ⛔ | 两台候选连接超时；MAC 命令发往标准 7709 主站无响应 |

## 三、F10 资料族（⛔ 内容停发）

| 接口 | 命令 | 实测 | 说明 |
|---|---|---|---|
| f10_catalog 栏目目录 | 0x0001 | ⚠️ | 账本 `online`、`verified=False`（布局未由真机 golden 锁定）。本节此前写"命令 offline（7709 上无响应）"——那句话与账本不符，也进不了「能力发现面」的死名字推导，本轮按账本更正：它**会发包**，只是解出来的目录可不可信没有判据。替代：`dc_query`（dividend/performance/holder_num/ipo）/ `corporate_action` / `finance`，都是另一条 Query |
| f10 download 文件下载 | 0x06B9 | ⚠️ | 服务器**应答但返回空字节**（F10 内容停止分发）——已加空内容检测，显式抛 `DataError`（同步/异步镜像一致），不再静默返回空文本；账本 `verified=False` |

## 四、异步面

- `AsyncTdxClient` 与同步逐方法镜像（33 组共享 `_mixin` 骨架）；新增的
  offline fail-fast 与空内容检测在异步侧同步生效（实测 async security_list /
  minute_history / download 与同步行为一致）。
- P13-A 的 E7050「源不可用」转换只存在于 `UnifiedQuoteAPI`/`AsyncQuoteAPI` 门面，
  而这两个门面已随 v17 单内核删除：v17 运行期没有那个类，也没有与之等价的统一
  不可用异常，offline 命令统一以 `CommandOffline` 结束本次查询（F-44 的剩余面即由此而来）。

## 五、P13-A 兜底路径矩阵（2026-09-06 增量）

> **v17 状态**：本表描述的是 pre-v17 门面的跨源兜底路由，随门面一并下线。
> 表中所有 "auto→E7050 不可用异常" 的转换在 v17 **不存在**——单内核不做
> Provider 切换，offline 命令按 `CommandOffline` 结束本次查询。"替代"一列仍是
> 有效的方法学指引（由调用方自己改选能力），只是不再由运行期自动给出。

| 方法 | 原契约 | 新契约（P13-A） | 语义差异 |
|---|---|---|---|
| `block_quotes` | 仅 tdx | tdx/web/auto 三路由 | web 兜底为腾讯板块排行（近似），字段 schema 不同；每行附 `source` 标记；`block_type=3`（指数）无映射；`start>0` 显式 web 报 `ValueError` |
| `auction` | 仅 tdx | auto/tdx 保持，tdx 失效时 auto→E7050 不可用异常（v17 已无此转换） | web 侧无对应能力；替代：`hot_rank` / `longhu` / `quotes + snapshot` |
| `volume_price` | 仅 tdx | 同上 | 同上；替代：`bars`（自算） |
| `f10_catalog` | 仅 tdx | 同上 | 替代：`dc_query` / `corporate_action` / `finance` |
| `ex_market_list` | 仅 tdx | 同上 | 替代：按市场直取行情（hk_quotes / us_quotes / rates / dc_query） |
| `ex_instruments` | 仅 tdx | 同上 | 同上 |

**路由契约：pre-v17 → v17**。下面前两条是已删除门面的历史口径，逐字保留只为让旧
issue 读得懂；v17 的对应事实写在第三条与末条。

- 历史：显式 `route="tdx"` → 透传原异常（`CommandOffline` / `AllHostsUnreachable`）；
  显式 `route="web"/"local"` → `ValueError`（除 `block_quotes` 的 web 支持）。
- 历史：auto/None → 门面在 tdx 抛 `CommandOffline`/`AllHostsUnreachable` 时转成统一
  「不可用」异常（含 `alternatives` + `hint`）。
- **v17：`route` 形参不存在**，`Client.quotes(..., route="web")` 是构造期 `TypeError`
  而非运行期 `ValueError`；数据源由 `provider=` 单选（缺省走注册表默认绑定），
  `provider` 写了注册表没有的 id → 规划期 `ValidationError`（E1010/422），context 带
  `known_providers` 名单与所写的 `provider`。
- **v17：单内核不替换 Provider**，`CommandOffline` / 传输层原异常原样到调用方，不再有
  统一的"源不可用"异常（E7050 已按 F-68 裁决 (a) 从错误树删除，退役登记见
  `docs/errors.md` §一之二）。替代方案仍在本表"替代"一列，但由调用方自己发起另一条 Query。

## 六、维护约定

1. 「命令级失效」登记在账本（`protocol/commands.py` 的 `status=STATUS_OFFLINE`
   + 实测日期注释），fail-fast 前先尝试过全部主站——误标时改账本即可恢复。
2. 主机级失效标注在 `transport/hosts.py` 池注释（含实测日期与复测建议）。
3. 本表不写"自动兜底"：v17 内核不替换 Provider，所以"改走哪里"永远是**调用方另发一条
   Query**。哪些名字还有第二条路，看 `docs/api/interfaces.md`「能力发现面」那张由注册表现推
   的出路列（由 `tests/architecture/test_offline_capability_honesty.py` 判它）。本节此前手抄
   过一份"代码表 → 东财 clist""板块行情 → 腾讯板块排行"，那两条转换都随 pre-v17 门面删除；
   抄一份可用性出来，就等于再造一个会过期的第二事实源。
4. 「仅 tdx」方法的环境级失效在 v17 表现为 `CommandOffline`（账本 `STATUS_OFFLINE`）
   或传输层原异常（`ConnectionFailed`/`AllHostsUnreachable`/`WebSourceError`）：
   内核不替换 Provider，所以也不替用户决定"换谁"。pre-v17 门面曾用 E7050 那个
   统一「源不可用」类 + `context["alternatives"]` 承载同一事实，该抛点已随门面删除
   （F-44 剩余面），类本身已按 F-68 裁决 (a) 从错误树里移除。
