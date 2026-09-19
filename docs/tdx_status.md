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
> `CommandOffline` / `AllHostsUnreachable` 转换为新增的
> `SourceUnavailable`（E7050），context 附 `alternatives` 明确可用替代方案；
> 显式 `route="tdx"` 保持 W12 契约透传原异常，显式
> `route="web"/"local"` 仍报 `ValueError`（收缩承诺不变）。

## 一、7709 标准行情族（✅ 全部打通，含 2 条已停答命令的兜底 + 3 条 offline 命令的替代路径）

| 接口 | 命令 | 实测 | 说明 |
|---|---|---|---|
| quotes 实时行情 | 0x0530 | ✅ | sh600519 真实价格 |
| bars 日/分钟 K 线 | 0x052D | ✅ | day/5min 均正常，分页正常 |
| quotes_snapshot 批量快照 | 0x054C | ✅ | |
| snapshot 五档全量 | 0x0535 | ✅ | |
| minute_today 当日分时 | 0x0537 | ✅ | 87 点 |
| trade_today 逐笔 | 0x0FC5 | ✅ | |
| finance_info 财务 | 0x0010 | ✅ | 37 字段 |
| capital_changes 除权除息 | 0x000F | ✅ | 250 事件 |
| security_count 证券总数 | 0x044E | ✅ | 27904 |
| **security_list 代码表** | 0x044D | ⚠️→✅ | **命令已停答**（6 台主站读取超时，2026-09-06 实测）；已登记 offline + fail-fast（<75ms），`UnifiedQuoteAPI.security_list / security_list_all` 自动降级**东财 clist**（实测沪 100 行/深 3089 行真实数据） |
| **minute_history 历史分时** | 0x0FB4 | ⚠️ | **命令已停答**（3 台主站 × 多日期超时）；已登记 offline + fail-fast；替代：`minute_klines`（web 分钟 K 线） |
| **block_quotes 板块行情** | 0x07E5 | ⛔→⚠️ | 命令 offline（既知下线）；**P13-A：新增 web 近似兜底**——auto/显式 web 路由降级到 `WebQuoteSession.board_rank`（腾讯板块排行，含领涨股与 5/20 日涨幅），每行附 `source="tencent_board_rank"` 标记；`block_type` 映射 0/1/2→concept/industry/region，3（指数）无映射 |
| **auction_snapshot 竞价** | 0x056A | ⛔→⚠️ | 命令 offline；**P13-A：auto 路由下抛 `SourceUnavailable`**，`context["alternatives"]` 提示 `hot_rank`（人气榜）/ `longhu`（龙虎榜）/ `quotes + snapshot`（实时快照 + 五档） |
| **volume_price_dist 量价分布** | 0x051A | ⛔→⚠️ | 命令 offline；**P13-A：auto 路由下抛 `SourceUnavailable`**，提示 `bars`（K 线，自算量价指标）；筹码分布为 TDX 端本地计算，web 侧无对应能力 |

## 二、扩展市场族（⛔ 环境级失效，P13-A 显式化替代方案）

| 项 | 实测 | 说明 |
|---|---|---|
| 7727 主站池（4 台候选） | ⛔ | 全部连接超时；且 13 台 7709 存活主机均未双开 7727——扩展行情服务疑似整体迁移/下线 |
| **ex_market_list** | ⛔→⚠️ | **P13-A：auto 路由下抛 `SourceUnavailable`**，`context["alternatives"]` 提示按市场直取行情跳过目录列举：`hk_quotes`（港股）/ `us_quotes`（美股）/ `rates`（外汇）/ `dc_query`（期货期权） |
| **ex_instruments** | ⛔→⚠️ | 同上，与 ex_market_list 同因 |
| ex_bars / ex_quotes | ⛔ | 7727 主站失效；web 侧 hk_quotes/us_quotes 已覆盖行情场景（语义直取） |
| GOODS 商品语义（0x0200 族） | ⛔ | 与 EXTENDED 共池（7727）；另 `domain.symbol` 不解析商品代码（SymbolError），双重不可用 |
| MAC 专属（0x120F 族，2 台候选） | ⛔ | 两台候选连接超时；MAC 命令发往标准 7709 主站无响应 |

## 三、F10 资料族（⛔ 内容停发，P13-A 显式化替代方案）

| 项 | 实测 | 说明 |
|---|---|---|
| **catalog 栏目目录** | 0x0001 | 命令 offline（7709 上无响应）；**P13-A：auto 路由下抛 `SourceUnavailable`**，`context["alternatives"]` 提示 `dc_query`（dividend/performance/holder_num/ipo）/ `corporate_action` / `finance` |
| download 文件下载 | 0x06B9 | ⚠️→✅ 服务器**应答但返回空字节**（F10 内容停止分发）——已加空内容检测，显式抛 `DataError`（同步/异步镜像一致），不再静默返回空文本 |

## 四、异步面

- `AsyncTdxClient` 与同步逐方法镜像（33 组共享 `_mixin` 骨架）；新增的
  offline fail-fast 与空内容检测在异步侧同步生效（实测 async security_list /
  minute_history / download 与同步行为一致）。
- P13-A 的 `SourceUnavailable` 转换只存在于 `UnifiedQuoteAPI`/`AsyncQuoteAPI` 门面，
  而这两个门面已随 v17 单内核删除：v17 运行期没有任何 `SourceUnavailable` 抛点，
  offline 命令统一以 `CommandOffline` 结束本次查询（F-44 的剩余面即由此而来）。

## 五、P13-A 兜底路径矩阵（2026-09-06 增量）

> **v17 状态**：本表描述的是 pre-v17 门面的跨源兜底路由，随门面一并下线。
> 表中所有 "auto→`SourceUnavailable`" 的转换在 v17 **不存在**——单内核不做
> Provider 切换，offline 命令按 `CommandOffline` 结束本次查询。"替代"一列仍是
> 有效的方法学指引（由调用方自己改选能力），只是不再由运行期自动给出。

| 方法 | 原契约 | 新契约（P13-A） | 语义差异 |
|---|---|---|---|
| `block_quotes` | 仅 tdx | tdx/web/auto 三路由 | web 兜底为腾讯板块排行（近似），字段 schema 不同；每行附 `source` 标记；`block_type=3`（指数）无映射；`start>0` 显式 web 报 `ValueError` |
| `auction` | 仅 tdx | auto/tdx 保持，tdx 失效时 auto→SourceUnavailable | web 侧无对应能力；替代：`hot_rank` / `longhu` / `quotes + snapshot` |
| `volume_price` | 仅 tdx | 同上 | 同上；替代：`bars`（自算） |
| `f10_catalog` | 仅 tdx | 同上 | 替代：`dc_query` / `corporate_action` / `finance` |
| `ex_market_list` | 仅 tdx | 同上 | 替代：按市场直取行情（hk_quotes / us_quotes / rates / dc_query） |
| `ex_instruments` | 仅 tdx | 同上 | 同上 |

**路由契约保持**：

- 显式 `route="tdx"` → 透传原异常（`CommandOffline` / `AllHostsUnreachable`），
  用户显式要求 tdx 时看到真实错误；
- 显式 `route="web"/"local"` → 保持 `ValueError`（除 `block_quotes` 的 web 支持）；
- auto/None → tdx 抛 `CommandOffline`/`AllHostsUnreachable` 时转成
  `SourceUnavailable`（含 `alternatives` + `hint`）。

## 六、维护约定

1. 「命令级失效」登记在账本（`protocol/commands.py` 的 `status=STATUS_OFFLINE`
   + 实测日期注释），fail-fast 前先尝试过全部主站——误标时改账本即可恢复。
2. 主机级失效标注在 `transport/hosts.py` 池注释（含实测日期与复测建议）。
3. 兜底路径以「能力不丢」为准：代码表 → 东财 clist（行内 `source` 字段标记
   数据来源）；历史分时 → 引导 `minute_klines`；板块行情 → 腾讯板块排行
   （P13-A）；字段 schema 差异在方法文档中声明。
4. 「仅 tdx」方法的环境级失效在 v17 表现为 `CommandOffline`（账本 `STATUS_OFFLINE`）
   或传输层原异常（`ConnectionFailed`/`AllHostsUnreachable`/`WebSourceError`）：
   内核不替换 Provider，所以也不替用户决定"换谁"。pre-v17 门面曾用
   `SourceUnavailable`（E7050）+ `context["alternatives"]` 承载同一事实，
   该抛点已随门面删除（F-44 剩余面）。
