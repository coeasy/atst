# TDX 全接口连通性矩阵（2026-09-06 实测）

> 对全部 TDX 协议族 / 命令 / 主站池的**真实环境**逐项实测结果。
> 实测方式：真实网络直连公开主站取数（非 mock）；日期 2026-09-06（周日，
> 非交易时段——实时类返回最近交易日快照属正常）。
> 结论先行：**7709 标准行情族核心命令全部打通**；扩展市场（7727）/MAC/F10
> 内容分发为**环境级失效**（主机/命令停答），库内已有明确报错与替代路径。
>
> 入参面口径：`Client.quotes()` 只收 `symbols` / `provider` / `policy` / `currentness`，
> 没有 `route` 这个形参——传 `route="web"` 在构造期即 `TypeError`。选数据源用
> `provider=`；内核不替换 Provider，offline 命令一律以 `CommandOffline` 结束本次查询
> （详见 §四、§五、§六）。

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
| snapshot 快照合成 | 组合面 | ✅ | **不是一条命令**：`quotes` + 当日 `bars(count=1)` 合成的字典。它不含盘口深度（§一之二） |
| finance_info 财务 | 0x0010 | ⚠️ | **请求体顺序 bug 已修**（2026-10-21）：旧实现发 `<6s code><H market>`（代码在前），服务端按 `market@2 / code@3:9` 解读后记录头整体错位——`market` 读到 ASCII `'0'`(48)、`code` 只剩半个，且前 2 字节（两个 ASCII `'0'`，即十进制 12336）被当成「请求条数」，服务器于是回 100 条缓冲残留（旧「四份样本 14302 字节」正来源于此）。现发 9 字节 `<H 1><B market><6s code>`，深/沪 8 只标的重放：响应头与请求逐一相符、143B 记录干净解出、流通股本/总股本/上市日期与公开财务数据吻合。字段序已按 pytdx `GetFinanceInfo` / injoyai-tdx 交叉锁定，但**本仓 golden 未重采**（F-37），逐字段语义仍标 `verified=False`。解析器对记录头做回声校验，不符即发 `field_out_of_domain` 告警 |
| capital_changes 除权除息 | 0x000F | ⚠️ | **同一个请求体顺序 bug 已修**（2026-10-21）：修正后深/沪多只标的重放，茅台 45 条 / 平安 81 条记录全部解出，日期域 0 条畸形、类别分布合理（除权除息为主，最早回溯到平安 1990-03-01）。记录布局按 pytdx `GetXdXrInfo` 交叉验证：29B/条（`date@8`、`category@12`、16B 分类数据为 float32×4；茅台 2002-07-26 送配股前后两组股本数恰好成 1.1 倍）。**本仓 golden 未重采**（F-37），记录布局仍标 `verified=False`；块头回声不符同样发 `field_out_of_domain` 告警 |
| quotes_snapshot 批量快照 | 0x054C | ⚠️ | 账本 offline（多主站实测已下线），但是 `_OFFLINE_FALLBACK_OK` 里唯一被放行的一条：客户端仍按 60 只/包发一次，批量解不出或降级 L3 时**本片回退逐只 0x0530**——同一 Provider 内的分片回退，不换源 |
| minute_today 当日分时 | 0x0537 | ✅ | **请求体字节序 bug 已修**（2026-10-21）：旧实现发 `<6s code><H market><I 0>`（代码在前），服务端按市场在前解读时前 2 字节被当成代码字符读成 ASCII 码点而越域（沪市代码头两字节即 `'6''6'`）→ 上证回 4 字节空桩，深市靠服务端对残缺代码的宽容碰巧有响应。现发 12 字节 `<H market><6s code><I 0>`，6/8 主站 × 双市场均回满日 240 条。记录为 leb128 变长（`price_delta`/`avg_delta`/`vol`），价格与均价是**「基准 + 增量」**（pytdx 的累加写法在真机上只有 2/240 与 K 线相符，别抄）。与同日 0x052D 一分钟 K 线逐条对磅：价格 240/240、成交量 240/240；缓冲区按字节精确耗尽（1196/1223B）。账本 L1 / `verified=True` |
| trade_today 逐笔 | 0x0FC5 | ✅ | **同一个请求体字节序 bug 已修**（2026-10-21，上证旧实现回 2 字节空桩）。`start` 自最新一笔往回数（`0` = 当日最后 `count` 笔），翻页边界不重不漏。记录为 leb128 变长，价格**逐笔累加**（与 0x0537 的「基准 + 增量」相反，与 pytdx 一致）。全日逐笔（沪 4304 / 深 4537 笔）按分钟汇总与同日 0x052D 一分钟 K 线逐桶对账 237/240，余下 3 桶全部是竞价并入且算术精确（09:25 开盘竞价 + 09:30 连续 = 09:31 桶等）；末笔累加价与 0x0530 实时价一致（沪 9.66 / 深 11.41）。`buyorsell` 观测值 0/1/2/5/8，参考实现同样不解释 0/1/2 之外的取值，原样透传。账本 L1 / `verified=True` |
| security_list 代码表 | 0x044D | ⛔ | 命令已停答（6 台主站读取超时，2026-09-06 实测）；客户端 fail-fast，一调即抛 `CommandOffline`。**tdx 之外无人声明它**，要代码表得调用方自己另发 Query 或读本地 vipdoc 文件 |
| minute_history 历史分时 | 0x0FB4 | ⛔ | 3 台主站 × 多日期超时；fail-fast 抛 `CommandOffline`。同名能力无出路；要分钟线只能改走 `minute_klines`（tencent / eastmoney 的 web 适配器，另一条 Query） |
| block_quotes 板块行情 | 0x07E5 | ⛔ | 三主站实测无响应；fail-fast 抛 `CommandOffline`。今天没有任何自动换源（§五） |
| auction_snapshot 竞价 | 0x056A | ⛔ | 三主站实测无响应；fail-fast 抛 `CommandOffline`。替代需调用方自选：`hot_rank`（人气榜）/ `longhu`（龙虎榜）/ `quotes`（实时快照，无盘口深度） |
| volume_price_dist 量价分布 | 0x051A | ⛔ | 三主站实测无响应；fail-fast 抛 `CommandOffline`。筹码分布为 TDX 端本地计算，web 侧无同名能力，可用 `bars` 自算量价指标 |
| security_list_legacy 代码表旧包 | 0x0450 | ⚠️ | 账本 offline；包内既无解析器也无客户端入口，只在协议档案里留名。登记在此是为了让本表覆盖账本里每一条已停答的 quotation 命令 |
| quotes_legacy 批量行情旧包 | 0x053E | ⚠️ | 账本 offline（部分主站已停用）。解析器 `QuotesLegacyParser` 仍在，但没有任何客户端方法发它——只能经通用 `request` 口手工发包，且响应按 L2 通用解析处理 |
| minute_recent 近几日分时 | 0x0FEB | ⚠️ | 账本 offline；无解析器、无客户端入口，同上 |
| minute_subplot 分时副图 | 0x051B | ⚠️ | 账本 `degraded`（有响应但布局未锁定）；无解析器、无客户端入口，同上 |

### 一之二、`quotes` / `snapshot` 回来却填不上的三格

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

## 二、扩展市场族（⛔ 环境级失效）

| 项 | 实测 | 说明 |
|---|---|---|
| 7727 主站池（4 台候选） | ⛔ | 全部连接超时；且 13 台 7709 存活主机均未双开 7727——扩展行情服务疑似整体迁移/下线 |
| **ex_market_list** | ⛔ | 命令 offline，以 `CommandOffline` 结束；替代需调用方自选：按市场直取行情跳过目录列举 `hk_quotes`（港股）/ `us_quotes`（美股）/ `rates`（外汇）/ `dc_query`（期货期权） |
| **ex_instruments** | ⛔ | 同上，与 ex_market_list 同因 |
| ex_bars / ex_quotes | ⛔ | 7727 主站失效；web 侧 hk_quotes/us_quotes 已覆盖行情场景（语义直取） |
| GOODS 商品语义（0x0200 族） | ⛔ | 与 EXTENDED 共池（7727）；另 `domain.symbol` 不解析商品代码（SymbolError），双重不可用 |
| MAC 专属（0x120F 族，2 台候选） | ⛔ | 两台候选连接超时；MAC 命令发往标准 7709 主站无响应 |

## 三、F10 资料族（⛔ 内容停发）

| 接口 | 命令 | 实测 | 说明 |
|---|---|---|---|
| f10_catalog 栏目目录 | 0x0001 | ⚠️ | 账本 `online`、`verified=False`（布局未由真机 golden 锁定）。它**会发包**，只是解出来的目录可不可信没有判据。替代：`dc_query`（dividend/performance/holder_num/ipo）/ `corporate_action` / `finance`，都是另一条 Query |
| f10 download 文件下载 | 0x06B9 | ⚠️ | 服务器**应答但返回空字节**（F10 内容停止分发）——已加空内容检测，显式抛 `DataError`（同步/异步镜像一致），不再静默返回空文本；账本 `verified=False` |

## 四、异步面

- `AsyncTdxClient` 与同步逐方法镜像（33 组共享 `_mixin` 骨架）；offline fail-fast 与
  空内容检测在异步侧同步生效（实测 async security_list / minute_history / download 与
  同步行为一致）。
- 没有统一的「源不可用」异常：offline 命令一律以 `CommandOffline` 结束本次查询，
  传输层故障走 `ConnectionFailed` / `AllHostsUnreachable` / `WebSourceError`。
  退役名单与对应 code 见 `docs/errors.md` §一之二。

## 五、路由契约与替代能力

内核不替换 Provider：`CommandOffline` 与传输层原异常原样送到调用方，没有统一的「源不可用」
异常（`SourceUnavailable` E7050 已从错误树删除，退役登记见 `docs/errors.md` §一之二）。
`route` 形参不存在，`Client.quotes(..., route="web")` 是构造期 `TypeError` 而非运行期
`ValueError`；数据源由 `provider=` 单选（缺省走注册表默认绑定），`provider` 写了注册表没有的
id → 规划期 `ValidationError`（E1010 / 422），context 带 `known_providers` 名单与所写的
`provider`。

下表「替代」列是给调用方的方法学指引——这些能力没有第二条运行时路径，要同类数据得自己
另发一条 Query。哪些名字还有第二条路，看 `docs/api/interfaces.md`「能力发现面」那张由注册表
现推的出路列（由 `tests/architecture/test_offline_capability_honesty.py` 判它）。

| 能力 | 现状 | 替代（调用方另发一条 Query） |
|---|---|---|
| `security_list`（0x044D 代码表） | 命令停答，fail-fast `CommandOffline` | 读本地 `vipdoc` 文件，或按市场直取行情 |
| `block_quotes`（0x07E5 板块行情） | 命令停答，fail-fast `CommandOffline` | 无同名 web 能力；可用 `hot_rank`（人气榜）/ `quotes`（实时快照） |
| `auction_snapshot`（0x056A 竞价） | 命令停答，fail-fast `CommandOffline` | `hot_rank` / `longhu`（龙虎榜）/ `quotes + snapshot` |
| `volume_price_dist`（0x051A 量价分布） | 命令停答，fail-fast `CommandOffline` | `bars`（自算量价指标） |
| `f10_catalog`（0x0001 栏目目录） | 布局未锁定，内容不可采信 | `dc_query`（dividend/performance/holder_num/ipo）/ `corporate_action` / `finance` |
| `ex_market_list`（扩展市场目录） | 命令停答，fail-fast `CommandOffline` | 按市场直取行情：`hk_quotes`（港股）/ `us_quotes`（美股）/ `rates`（外汇）/ `dc_query`（期货期权） |
| `ex_instruments`（扩展市场合约） | 同上 | 同上 |

## 六、维护约定

1. 「命令级失效」登记在账本（`protocol/commands.py` 的 `status=STATUS_OFFLINE`
   + 实测日期注释），fail-fast 前先尝试过全部主站——误标时改账本即可恢复。
2. 主机级失效标注在 `transport/hosts.py` 池注释（含实测日期与复测建议）。
3. 本表不写"自动兜底"：内核不替换 Provider，所以"改走哪里"永远是**调用方另发一条
   Query**。抄一份可用性出来，就等于再造一个会过期的第二事实源——哪些名字还有第二条
   路，只以注册表现推的那张出路列为准。
4. 「仅 tdx」方法的环境级失效表现为 `CommandOffline`（账本 `STATUS_OFFLINE`）
   或传输层原异常（`ConnectionFailed` / `AllHostsUnreachable` / `WebSourceError`）：
   内核不替换 Provider，所以也不替用户决定"换谁"。
