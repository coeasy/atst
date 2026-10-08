# atst ↔ free-stockdb 深度对标、差距分析与补全方案

> **文档性质**：对标/方案快照。对标对象是 [`hello245m/free-stockdb`](https://github.com/hello245m/free-stockdb)
> 仓库 HEAD（README 署名发行包 v0.2.0，2026-07 数据快照）；对标基准是 **atst v1.4.3**（`atst/_version.py`）。
> 文中接口名、字段名、协议细节均来自两侧仓库**实际源码/配置**，不用二手描述。
> 本文按仓库惯例落入 `docs/archive/parity/`（既有的 5 份对标文档同目录），
> 因此不进入 `active_docs()` 射程；涉及现行契约的口径以
> [interfaces.md](../../api/interfaces.md) 与 [ARCHITECTURE.md](../../ARCHITECTURE.md) 为准。
>
> 生成时间：2026-10-07
>
> **更正（2026-10-08 落地后复核）**：正文里 `sh600519` 后复权价格因子写作 **1.331** 是**错的**——
> 那是"窗口盖不住早期事件"时的降级读数（现金红利被整项丢掉）。窗口延伸修好之后，同一根 bar
> 的因子是 **7.39106838**，且在 `count=5 / 60 / 2000` 下完全一致。其余"真实缺口四条"（分页截断 /
> prev_close 缺失 / 配股恒 0 / 因子不落盘）的描述在 2026-10-07 当时是准确的，前三条已落地、
> 第四条（因子落盘）仍未做。逐项落地状态见
> [../plans/ADJUST_AND_WIDE_DAILY_PLAN.md](../plans/ADJUST_AND_WIDE_DAILY_PLAN.md)。

---

## 0. 三问直答

| 问题 | 结论 |
|------|------|
| **free-stockdb 的数据来自哪里？** | **仓库里没有数据源**。`sync_url.txt` 全文只有注释和 `https://data.example.com/stockdb` 这类占位示例，第一条有效行才是镜像根；同步器读该目录下的 `manifest.txt`（`sha256 size path`），按清单把 LevelDB 分片拉到 `./data`。**上游一手来源未公开**（README 只声明"数据版权由各数据源及其权利人决定"）；财务/在线 tick 走闭源 `stockdb.pyd` 里的 `get_fundamentals` / `get_last_tick`，源码不在仓库内。**它是"数据分发 + 本地引擎"，不是"数据采集器"**——数据源可替换、可审计性为零。 |
| **它有哪些接口？** | 9 组：① 原生 KV 查询面 `rd.get/vals/keys/mget/pipe/do/url`（通配 `*`、`start<end`/`start>end` 范围与升降序、字段投影）；② 高级 SDK `get_data`/`get_data_async`（8 种周期 + qfq/hfq 内存复权 + 批量 + DataFrame）；③ 指标 `zb.get`（38 指标 + `zhishu` 5 种加权）；④ 板块 `bk.get`（概念 + 申万一/二/三级双向映射）；⑤ HTTP `?cmd=get|set|zb.get|bk.get`（127.0.0.1:7899）；⑥ MCP（1 个 tool `get_market_kline`）；⑦ Excel/WPS JS 宏；⑧ HTML 网页浏览/导出；⑨ 标的面（`股票代码` / `退市*` / 复权因子 `cum`）。 |
| **atst 能否覆盖全部？** | **不能，且不应追求 1:1 覆盖。** 分层看：**数据获取层 atst 全面超出**（195 能力 / 14 Provider，财报、宏观、资金流、基金、期权期货、ESG 等是 free-stockdb 完全没有的）；**本地引擎层 atst 落后**（无本地时序服务、无分钟/tick 落盘、无统一本地查询面）；**计算层 atst 空缺**（无指标引擎）；**消费面各缺两块**（atst 缺 Excel/HTML，free-stockdb 缺 WS 实时订阅与 CLI）。可绕过性排序见 §6。 |

---

## 1. free-stockdb 数据来源解剖

### 1.1 同步链路（唯一入口）

```
sync_url.txt 第一条有效行        ← 用户自备；仓库内只有注释与示例
        │  支持：本地目录 / file:// / http(s):// / 多点挂载
        │  （"site1 or path1  minutes_data  save_to_data_path" 形态，可写多段）
        ▼
<mirror>/manifest.txt            ← 每行 "<sha256> <size> <relative-path>"
        │  路径必须相对、禁止 ".."；发布者先传数据、最后更新 manifest
        ▼
stockdb_updater（C++）           ← 下载到 *.part → 校验 size + sha256 → 替换
        │  已存在且校验一致则跳过（幂等、断点续传）
        ▼
./data（LevelDB 分片：CURRENT / MANIFEST-* / *.ldb / 日志）
```

**关键事实**：`docs/DATA_SOURCE.md` 明写"`stockdb_updater` **不内置任何数据地址**"。镜像即"信任锚"——
SHA-256 只能发现传输损坏，不能替代对发布者的信任。

### 1.2 存储与服务

| 层 | 实现 | 事实源 |
|----|------|--------|
| 存储 | LevelDB（LGDB 配置：`cache_size 500` / `write_buffer_size 16` / `block_size 32` / `compression yes` / `replication.binlog yes`） | `stockdb.conf` |
| 数据模型 | `table:key:subkey → value`，表含 `日k` `分钟k` `复权` `板块` `股票代码` `退市`（tick 按需） | `cpp/src/server.cpp`、`pybao/stock_sdk.py` |
| 服务 | `stockdb.exe`，`127.0.0.1:7899`，可选 `auth` / `readonly` / `slowlog` | `stockdb.conf` |
| 客户端 | `stockdb.pyd`（3.8+ 与 3.14t 两个二进制），同步 + 异步 + pipe；另经 `.pth` 全局安装 | `pybao/安装.py` |
| 压缩 | README 称 Zstd 压缩历史数据，"比 csv/mysql 小 3 倍以上" | README |

### 1.3 在线补充（闭源，源码不在仓库）

`调用方式/python/sdk_test.py` 直接调用但未在开源文件中定义：

* `get_fundamentals(query(cash_flow).filter(cash_flow.code == '000001.XSHE'), statDate='2024q4')` —— **JoinQuant 风格财务 API**；
* `get_last_tick('000001', count=10)` —— 在线 tick。

两者来自闭源 `stockdb.pyd`，**协议、上游、配额均不可审计**。

### 1.4 一手来源可审计性：这是它的结构性短板

| 维度 | free-stockdb | atst v1.4.3 |
|------|--------------|-------------|
| 上游是否可见 | ❌ 镜像黑盒，只有 manifest 哈希 | ✅ 14 个 Provider 直连公开源，能力→Provider→Channel 可追溯 |
| 断源后果 | 镜像一停，增量即止（本地快照仍可查） | 单源下线可显式 `FallbackPolicy` 切换，且**永不隐式换源** |
| 字段口径可验 | ❌ 只能抽样自证 | ✅ `DataProfile` + golden 样本 + 单位/时区契约 |
| 许可合规 | 使用者自担（README 明文免责） | 各 Provider 文档逐条标注非官方/限流风险 |

---

## 2. free-stockdb 接口全清单

### 2.1 原生 KV 查询面（`rd`）

| 能力 | 形态 | 说明 |
|------|------|------|
| 点查 | `rd.get("日k:600633:20260625")` / `rd.get("日k","600633","20260625")` | 日 K 8 位日期；分钟 K 14 位 `YYYYMMDDHHMMSS` |
| 取值 | `rd.vals(...)` | 返回值序列 |
| 键枚举 | `rd.keys("日k","600633","202606*")` | 前缀匹配 |
| 通配 | `rd.vals("日k","*","20260625")` / `"6*"` / `rd.vals("退市*")` | 全横截面 |
| 范围 | `"20260620<20260626"` 升序 / `"20260620>20260626"` 降序；`N` 表示开放端 | 服务端前缀扫描 |
| 批量 | `rd.pipe()` + `pp.mget(...)` + `pp.do()`（同步）/`await pp`（异步） | 单路 pipeline |
| 投影 | `.get("code,date,amount,high")` → 二维数组 | 少字段时省序列化 |
| URL 化 | `.url()` | 生成等价 HTTP 查询串 |

### 2.2 高级 SDK（`StockDBClient`）

```
get_data(code, start, end, frequency, fields, limit, desc, as_df, fq)
get_data_async(...)                       # 同签名
```
* `code`：单只 `"600633"` 或批量 `["600633","600422"]`（批量走 pipeline）；
* `frequency`：`1d` / `1m` / `5m` / `15m` / `30m` / `60m` / `1w` / `1M`；
  `1w`/`1M` 由日 K **内存聚合**，`5m`~`60m` 由 1 分钟 K 内存聚合（按交易时段 elapsed 对齐，非墙钟整除）；
* `fq`：`qfq` / `hfq` / `None`，**内存折算**（除权因子二分查找，代码 `1`/`5` 开头保留 3 位小数、其余 2 位）；
* `fields` / `limit` / `desc` / `as_df`（pandas，批量合并时补 `code` 列）。

### 2.3 指标面 `zb.get`（事实源 `zhibiao.py` 的 `supported` 集合）

**38 个技术指标**：

| 类别 | 指标 |
|------|------|
| 趋势 | `ma` `ema` `sma` `wma` `dma` `expma` `trix` `dmi` `dfma` `bbi` |
| 震荡 | `macd` `kdj` `rsi` `wr` `cci` `psy` `bias` `roc` `mtm` `dpo` |
| 通道 | `boll` `ktn` `taq` |
| 量价 | `obv` `vr` `emv` `mfi` `cr` `mass` |
| 综合/基础 | `atr` `asi` `brar` `xsii` `std` `sum` `hhv` `llv` `ref` |

（README 表述"39 种指标" = 上述 38 + `zhishu`。）

**`zhishu` 指数合成 5 种加权**：`1` 等权、`2` 流通市值加权、`3` 成交额加权、`4` 成交量加权、`5` 总市值加权（分钟 K 仅支持 1/3/4，因无市值字段）；`base` 基点默认 1000。

其他参数：`n`（每个指标独立参数串，如 `n=["5,10,20", None, "12,26,9"]`）、`cross`（`True` / `"with_value"`，金叉死叉信号；仅基础类支持 `fields`）、批量多标的。

### 2.4 板块面 `bk.get`

```
bk.get(x, category, fields)
```
* `category`：`0` 概念 / `1` 申万一级 / `2` 申万二级 / `3` 申万三级；
* **双向**：传股票代码 → 所属板块；传板块名/代码 → `symbols` 成分股；不给 `x` 只给 `category` → 该类别全量；
* `fields`：`code` `name` `source` `type` `group` `category` `symbols`（别名 `symbol`/`codelist`）；
* README 称内置申万一二三级 + 1200 概念板块，毫秒级查询。

### 2.5 HTTP / MCP / Excel / HTML

| 面 | 形态 |
|----|------|
| HTTP | `GET http://127.0.0.1:7899/?cmd=get&t=日k:600633:20260625`；另有 `cmd=set`、`cmd=zb.get`、`cmd=bk.get` |
| MCP | `调用方式/ai_mcp/stock_mcp_server.py`，`native_mcp`（纯标准库 stdio），**仅 1 个 tool** `get_market_kline` |
| Excel/WPS | `wps_js_macro.js` 宏 + `查看数据.html`（浏览 + 导出） |
| HTML | `数据网页版.html` / `查看数据.html`，双击即开 |

### 2.6 字段契约

| 表 | 字段数 | 字段 |
|----|--------|------|
| `日k` | **21** | `amount` `amplitude` `close` `code` `date` `float_mv` `float_share` `high` `is_st` `low` `name` `open` `pb` `pct_chg` `pe_ttm` `pre_close` `total_mv` `total_share` `turnover` `vol_ratio` `volume` |
| `分钟k` | **8** | `amount` `close` `code` `date` `high` `low` `open` `volume` |
| `复权` | 2 | key `复权:<code>:<date>` → `cum`（累积因子） |

**注意它把估值/市值/换手率/量比/ST/名称摊平进日线行**——这是它"拿来即回测"体验的核心，也是 atst 目前**结构上不同**的地方（atst 是规范化的 7 字段窄表 + 估值走独立能力）。

---

## 3. atst v1.4.3 现状清点（基准事实）

| 项 | 实测值 | 来源 |
|----|--------|------|
| 能力总数 | **195** | `Client().capabilities()` |
| 能力健康度 | `alive` 178 / `needs_verify` 7 / `offline` 5 | `Client().capability_statuses()` |
| Provider | **14**：`tdx` `local_vipdoc` `tencent` `sina` `eastmoney` `baidu` `jsl` `boc` `iwencai` `cninfo` `ths` `wallstreet` `builtin` `derived` | `atst/providers/__init__.py` |
| 暴露面 | Python（`Client` 17 便捷方法 + `call`/`typed`）、CLI、HTTP 12 路由、WS 13 方法、MCP 9 工具 | [api/README.md](../../api/README.md) |
| 本地数据湖 | `data/day/<类别>/*.parquet`：6 类别（stock/etf/lof/bond/bshare/index），**8385 文件 / 22,680,669 根 / 8381 标的**，末日 2026-10-03 | `data/state.json` |
| 同步工具 | `scripts/sync_daily_history.py`（零参数可跑，断点以**磁盘末日期**为第一事实源，原子改名；`--scan` 自产代码表实测 5223 只） | 脚本头注释 |
| 本地读取 | `atst/output.from_parquet()`、`to_duckdb()`、`Sink`；`atst/reader/` 解析 TDX `.day`/`.min`/板块/财务二进制 | `atst/output/__init__.py` |
| 日线字段 | `BAR_FIELDS = datetime, open, high, low, close, volume, amount`（**7 窄表**），单位/复权口径由 `DataProfile` 决定 | `atst/domain/models.py` |
| 复权 | `adjusted_bars`（derived/adjustment 通道，组合能力），`method=qfq/hfq/fixed/none` | [interfaces.md](../../api/interfaces.md) §复权 K 线 |
| 板块 | `sw_industry` / `sw_industry_history` / `concept_members`（**双向**：板块→成分股、股票→所属板块）/ `stock_boards` / `em_boards` / `em_board_members` / `board_members` / `industry_boards` / `hot_boards` / `board_rank` / `sector_flow` | 能力表 |
| 分钟 | `minute_klines`（1/5/15/30/60，tencent/eastmoney/baidu 路由）、`minute` 当日分时；TDX `minute_today`(`0x0537`)/`minute_history`(`0x0FB4`) **已下线** | interfaces.md |
| tick | `ticks` / `trades` / `baidu_ticks` / `intraday` / `block_trades` | 能力表 |

---

## 4. 覆盖矩阵（free-stockdb 接口 → atst 对应）

图例：✅ 已覆盖｜🟡 部分覆盖/口径不同｜❌ 空缺

| # | free-stockdb 接口 | atst 对应 | 状态 | 差距说明 |
|---|-------------------|-----------|------|----------|
| 1 | 日 K 单只/批量 | `bars` / `klines` / `Client.bars(...)` | ✅ | atst 多 Provider 可挑源；**已全市场落盘 22.7M 根**（free-stockdb 才需要同步） |
| 2 | 周 K / 月 K | `period="week"\|"month"`（TDX/腾讯/百度） | ✅ | free-stockdb 是日 K 内存聚合；atst 有原生周期，也有 `adjusted_bars` 的 period 口径 |
| 3 | 1/5/15/30/60 分钟 K | `minute_klines`（Web）+ `local_vipdoc`（本地 `.min`） | 🟡 | **能取不能存**：无分钟级批量落盘与批量回放，全市场分钟回测仍要逐股拉 |
| 4 | tick 级（按需） | `ticks` / `trades` / `baidu_ticks` | 🟡 | 同上，无 tick 落盘 |
| 5 | 内存周期聚合（分钟→5/15/30/60，日→周/月） | 无（周期由源端直接给） | 🟡 | 语义不同但结果等价；缺"任意分钟倍数聚合"工具函数 |
| 6 | 前/后复权（内存折算） | `adjusted_bars`（qfq/hfq/fixed/none） | 🟡 **P0**（口径已接线，缺口是"精度与完整性"，**不是"没有源"**） | **更正（2026-10-07 复核）**：执行体 `DirectProviderExecutor._composed_call` 里 `event_source` **默认 `"eastmoney"`**，走 `dividend_history`（`RPT_SHAREBONUS_DET`，真机列名已对拍）→ `capital_changes_from_dividends()` → `AdjustEngine`；`0x000F` 需显式 `event_source="tdx"` 且必须过 `reject_implausible_changes()` 布局闸。实测 `sh600519` hfq 价格因子 1.331、qfq 归一到最新 = 1.0，**复权是能出正确结果的**。真实缺口有四条且均已实测复现：**① 事件分页截断**（executor 未传 `size`，默认 20 条；600519 实际 28 条，末条只到 2009-07-01，早期因子丢失）；**② `prev_close` 缺失**（TDX bar 不带，实测告警 `adjust_prev_close_missing` → 现金红利被忽略，价格因子退化成 `1/(1+S+R)`）；**③ 配股恒 0**（东财该表无配股列，`rights_ratio`/`rights_price` 置 0）；**④ 因子不落盘**（每次全量重算）。详见 [ADJUST_AND_WIDE_DAILY_PLAN.md](../plans/ADJUST_AND_WIDE_DAILY_PLAN.md)。 |
| 7 | 复权因子表 `cum` | `atst/domain/adjust.compute_factors` + `dividend_history` / `corporate_action` | 🟡 | 无持久化的因子表（本地湖里没有 factor 分区） |
| 8 | 板块双向映射（概念 + 申万一/二/三级） | `concept_members`（双向）、`sw_industry`、`stock_boards`、`em_board_members` | 🟡 | 覆盖面更宽（东财/同花顺多源），但**层级与快照化未统一**：申万一二三级的父子关系与"某日属于哪级"无 as-of 快照 |
| 9 | 21 字段宽表日线（含 pe_ttm/pb/mv/turnover/is_st/name） | `BAR_FIELDS` 7 字段 + `stock_valuation` / `valuation_history` / `profile` / `st_list` | 🟡 **P1** | 字段**存在但分散**，需调用方二次拼接；无 as-of 宽表 |
| 10 | 全横截面查询（`日k:* :某日`） | 本地湖按标的**分文件**存储，无横截面索引 | ❌ **P0** | 8385 个文件做横截面 = 8385 次读；需 DuckDB 视图或分区数据集 |
| 11 | 范围/前缀/通配查询、字段投影、升降序、limit | `from_parquet(columns=...)`（单文件投影） | ❌ **P0** | 无统一本地查询 DSL（谓词下推、范围、批量投影、降序、limit） |
| 12 | 本地 KV 服务（7899，多进程共享） | 无本地服务 | ❌ **P0** | 无进程外共享；多策略/多用户各自开文件 |
| 13 | 38 指标 + zhishu 5 加权 | **无指标引擎** | ❌ **P1** | 计算层是明确空缺（与 finkit 分工，但"取数即算"闭环断在这里） |
| 14 | 财报（JQ 风格 `get_fundamentals`） | `income_sheet` / `balance_sheet` / `cash_flow` / `financial_abstract` / `fin_report` / `stock_all_performance` | ✅ **超出** | atst 财报面显著更全（多期、多表、业绩预告、预约披露） |
| 15 | 实时行情 / 在线 tick | `quotes` / `snapshot` / `ticks` / WS `subscribe`+`push` | ✅ **超出** | atst 有 WS 实时订阅 + 流式引擎；free-stockdb 是离线库 |
| 16 | HTTP 接口 | HTTP 12 路由 + 通用 `POST /v13/query/{capability}`（195 能力全可达） | ✅ **超出** | free-stockdb 只有 4 个 cmd |
| 17 | MCP | MCP 9 工具（含通用 `query_capability`） | ✅ **超出** | free-stockdb 仅 1 tool |
| 18 | CLI | `atst` CLI（bars/quote/minute/... + `--json`） | ✅ **超出** | free-stockdb 无 CLI |
| 19 | Excel / WPS | 无 | ❌ **P1** | 缺 JS 宏 + 导出模板 |
| 20 | HTML 网页浏览/导出 | 无 | ❌ **P1** | 缺开箱网页 |
| 21 | 标的清单 / 退市 | `security_list` / `security_list_all` / `st_list` / `universe` / `data/universe.csv`（8381 只） | ✅ | 退市/新股/停牌的**历史修正**未纳入湖 |
| 22 | 增量同步 + 断点 + 校验 | `sync_daily`（vipdoc 写回）+ `scripts/sync_daily_history.py`（parquet） | 🟡 | 只覆盖**日线**；无分钟/tick；无 sha256 清单；类别间能力不齐 |
| 23 | 数据源可替换（镜像/file://） | 14 Provider 直连 | ✅ **超出** | atst 是"自己就是数据源"，不需要镜像 |
| 24 | 宏观 / 资金流 / 基金 / 期权期货 / ESG / 研报 / 公告 | 对应能力 80+ 项 | ✅ **远超** | free-stockdb 完全没有 |

**统计**：✅ 已覆盖或超出 **13** 项；🟡 部分覆盖 **9** 项；❌ 空缺 **4 项**（本地服务、统一本地查询面、指标引擎、Excel/HTML）。

---

## 5. 差距根因（不是"缺接口"，是"缺一层"）

```
free-stockdb 的形态：   [镜像/上游] → 同步落盘 → 本地 KV 服务 → 五种消费面 → 指标计算
                                        ↑ 它把价值全压在这一层

atst 今天的形态：       [14 Provider 直连] → 195 能力 → 五面暴露 → （部分）parquet 落盘
                                                                    ↑ 落盘是"副产物"，不是"底座"
```

三个结构性根因：

1. **本地层缺位**：`data/day` 有 22.7M 根数据，但它是**同步脚本的输出**，不是**被查询引擎管理的资产**——没有分区、没有清单、没有查询面、没有服务。数据躺在磁盘上，不能被"当数据库用"。
2. **复权精度与完整性未达标**（不是"没有事件源"）：默认源东财 `RPT_SHAREBONUS_DET` 已对拍可用，但 **事件分页默认只取 20 条**（长历史标的早期事件丢失）、**bar 不带 `prev_close` 导致现金红利被忽略**、**配股恒 0** 三条叠加，使长历史前复权与 free-stockdb 的 `cum` 因子不可比。落再多历史数据、因子不可信就不能用于回测。
3. **计算层空白**：atst 定位是数据基础设施，指标不进内核（与 finkit 分工）。但用户视角的"能不能替代 free-stockdb"恰恰卡在"取数即算"。

---

## 6. 补全方案（六期，按依赖排序）

### 不变量（每期都必须遵守）

* **禁止隐式跨 Provider fallback**——只能显式 `FallbackPolicy` + `ProviderOrchestrator`；本地命中与远端回补也必须是显式策略，**本地层不得偷偷顶替远端**。
* 新能力**双登记**：`WebQuoteSession` 上的 `@staticmethod` 只进 `MIGRATED_BINDINGS`，还须在 `atst/providers/__init__.py` 对应 Provider/Channel **显式声明** capability，否则 `Client()` 构造即 `CapabilityAuditError`。
* 新 Provider 会话源必须是合法 `SOURCE_ALIASES` 键（无自有会话源者映射名义源 `'sina'`）。
* 所有市场时刻走 `atst/domain/calendar.py`（`market_now()` / `to_market_tz()` / `market_timestamp()`），**禁 `time.localtime()` 与 naive `datetime.now()`**；改完必须 `TZ=UTC` 与 `TZ=Asia/Shanghai` 双跑。
* 报表名/接口名**禁止靠猜**，以 akshare 源码 grep `RPT_` + datacenter 实测为准。
* 东财 `push2*` 有 IP 级软限流：批量作业复用源层 `rate_limiter`，不得绕过。
* `ruff==0.15.2` + `mypy` 必须全绿；新增逻辑的判据进 `tests/`，全量 `pytest` 用 `CODEBUDDY_SAFE_DELETE_ENABLED=0 --basetemp="$TEMP/xxx"`。

---

### Phase A —— 本地行情集市（`atst/lake`）：把"副产物"变成"底座"

**目标**：`data/` 从"一堆 parquet"升级为**可查询、可校验、可增量、可共享**的本地集市，并让全横截面/批量回测不必逐股回源。

**设计**

1. **分区布局**（在现有 `data/day/<kind>/<symbol>.parquet` 之上加一层，不破坏现有断点）：
   * `data/lake/<kind>/<symbol>.parquet`（长历史，按 symbol 分文件，保留现有原子写语义）；
   * `data/lake/_manifest.json`：每个文件 `sha256` / `size` / `rows` / `first_date` / `last_date` / `updated_at`；
   * `data/lake/_state.json`：`last_run` / `symbols` / `bars` / 每标的末日期（**磁盘仍是第一事实源**，state 只是加速副本）。
2. **`atst/lake/` 模块**：
   * `LakeReader`：`read(symbols|kind, start, end, fields, desc, limit)` —— DuckDB/Arrow 谓词下推 + 字段投影 + 升降序 + limit；无 pyarrow 时降级到 `from_parquet` 逐文件（行为一致，只是慢）。
   * `cross_section(date, fields, kind=None)` —— 横截面切片（对应 free-stockdb `日k:* :某日`），走 `read_parquet(glob, where=...)` 单条 SQL，避免 8385 次文件读。
   * `lake_sync(...)` —— 复用 `scripts/sync_daily_history.py` 的断点/原子写/类别判定，产出集市并刷新 manifest。
3. **能力化**：注册 `lake_read` / `lake_cross_section` / `lake_sync` 三条能力到 `derived` Provider（双登记），使 `Client.call("lake_read", ...)` 与 HTTP `POST /v13/query/lake_read`、WS `query`、MCP `query_capability` **四面同义**。
4. **本地 Provider 路由**：新增显式策略 `LocalFirstPolicy`（命中本地且 freshness 达标 → 本地；否则远端并回写），走 `ProviderOrchestrator`，**不进默认路径**。

**交付物**：`atst/lake/{__init__,reader,sync,manifest}.py`、`tests/unit/test_lake_*.py`（离线 fixture）、`docs/lake.md`。

**验收**

* 全市场横截面（`cross_section("2026-10-03", ["close","volume"])`）单次 < 5s（DuckDB 路径）；
* `sha256` 清单与实际字节一致（manifest 校验测试）；
* 断点续跑幂等：同一天跑两次，`bars` 总数不增、内容一致；
* 四面（Python/HTTP/WS/MCP）对同一 capability 返回同构结果（复用现有 bridge 测试范式）。

---

### Phase B —— 复权精度与完整性硬化（**最高优先级**）

**目标**：不是"找一个事件源"（东财源已默认接线且可用），而是**补掉四条实测复现的缺陷**，让长历史前/后复权与 free-stockdb 的 `cum` 因子可比。

**设计**

1. **B1 事件分页拉全**（实测缺口：executor 未传 `size`，默认 20 条；600519 实际 28 条，末条只到 2009-07-01）：
   `_adjusted_events()` 显式传大 `size` 并**翻页到尽**（或按 `count`/上市年数推算），`capital_changes_from_dividends()` 保持丢弃无 `ex_dividend_date` 记录的纪律。
2. **B2 `prev_close` 注入**（实测告警 `adjust_prev_close_missing`：每股现金红利 1.1560 元被忽略，价格因子退化成 `1/(1+S+R)`）：
   `_adjusted_raw_bars()` **多取一根**，用前一根 `close` 填 `bar.extra["prev_close"]`；首根沿用既有 `open` 兜底并在文档写明。这一条直接决定现金红利是否参与除权价。
3. **B3 配股事件**（东财 `RPT_SHAREBONUS_DET` 无配股列，`rights_ratio`/`rights_price` 恒 0）：新增配股事件源，**报表名以抓包/akshare 实测为准，禁止按命名惯例猜**；抓不到则保留 0 并在结果侧告警（不许静默）。
4. **B4 因子表落盘**：`data/lake/adjust/<symbol>.parquet`（`date` / `cum` / `price_factor` / `vol_factor` / `event_type` / `source` / `verified`），`adjusted_bars` 命中本地表时不再回源；无本地表时保持现状。
5. **B5（次要）** `0x000F` golden 锁定：属于"多一条独立源用于交叉校验"，不再是可用性前置——已由 `reject_implausible_changes()` 挡住错位记录。

**验收**

* 至少 **30 只**跨行业样本（含高送转、ST、长期停牌、退市）与独立源（如本地 gbbq 文件 / 新浪除权页）逐日比对，qfq/hfq 相对误差 ≤ 1e-4；
* `0x000F` 的 golden 样本进 `tests/golden/`，F-37 账本 `verified=True`；
* 原 `AdjustError`（"事件日期解不开"）在三线源上不再触发。

---

### Phase C —— 宽表日线（`daily_enriched`）与 as-of 口径

**目标**：补上 free-stockdb"日线行内带估值/市值/换手/ST"的体验，且**不牺牲** atst 的单位与来源可追溯性。

**设计**

1. 新增 `derived` 能力 `daily_enriched(symbols, start, end, fields)`：以本地日线为主干，as-of 拼接
   `stock_valuation`（pe_ttm / pb）、市值（`total_mv` / `float_mv` / `total_share` / `float_share`）、
   `st_list`（`is_st`）、`profile`（`name`）、换手率与量比（来源字段口径逐源标注，新浪 `turnover` 原值透出为 `turnover_raw` 的既有纪律保留）。
2. 派生字段 `pct_chg` / `amplitude` 由 `pre_close` 链推出（`pre_close` 缺失时的兜底规则写入文档）。
3. 落盘为 `data/lake/enriched/<symbol>.parquet`，**字段命名与 free-stockdb 21 字段对齐**，并附加 `_src_*` provenance 列（atst 超出部分）。
4. 严格 as-of：**只用当时可得的数据**拼历史行（防前视），估值报告期与公告日的对齐规则写成可测常量。

**验收**：字段对齐表进文档；随机 50 标的 × 近 3 年，与实时估值接口在**最新交易日**上一致率 ≥ 99%；历史行无未来数据（as-of 单测）。

---

### Phase D —— 指标与合成层（`atst/indicators`）

**目标**：补齐 38 指标 + `zhishu` 5 加权，形成"取数即算"闭环。

**设计**

1. `atst/indicators/`：**薄封装优先**——核心算法复用 finkit（已立项"逐项超越 TA-Lib"），atst 只提供
   ① 批量取数（本地集市 + `cross_section` 矩阵化）、② 参数解析与校验（对齐 `n=["5,10,20", ...]` 形式）、
   ③ 结果封装（单标的 / 批量 / 长表）。**不重复实现指标数学**。
2. 能力化：`indicator` / `index_synthesis`（zhishu）进 `derived` Provider（双登记），四面可达。
3. `zhishu` 5 种加权：`1` 等权 / `2` 流通市值 / `3` 成交额 / `4` 成交量 / `5` 总市值；分钟级只开放 1/3/4 并**显式报错**（与 free-stockdb 一致）。
4. `cross`（金叉死叉）信号：作为 `indicator` 的 `mode` 参数，仅基础类支持 `fields`。

**验收**：每个指标至少 1 组外部基准对照（与 TA-Lib 或 finkit 基准的值比对，容差写入测试）；全市场 5000 只 × 3 指标批量计算在集市上 < 30s。

---

### Phase E —— 消费面补齐（Excel / WPS / HTML）

**目标**：补上 free-stockdb 有而 atst 无的两块，让"非 Python 用户"也能用。

**设计**

1. **Excel/WPS**：`docs/cookbook/` 增补 WPS JS 宏模板（对齐 `wps_js_macro.js` 的能力面：查日 K / 分钟 / 板块 / 指标），数据源走 HTTP 12 路由（`atst serve`）；另提供 `atst export --fmt xlsx`（经 `atst/output.write()`，保持原子写纪律）。
2. **HTML**：提供**静态**数据浏览页模板（读取导出的 parquet/json，不新增运行时依赖；不引入需要服务端渲染的东西）；放进 `docs/cookbook/` 与 examples，而不是给仓库加前端构建链。
3. 两块都**不新增 capability**，纯文档/脚本层交付，避免污染能力目录与门禁。

**验收**：文档链接门禁 `make audit-docs` 绿；示例脚本在离线 fixture 下可跑通（不依赖外网）。

---

### Phase F —— 数据源可审计 + 自身可镜像

**目标**：把"atst 能当自己的同步源"做成能力，正面回应 free-stockdb 的镜像协议。

**设计**

1. `atst lake export --mirror <dir>`：产出 `manifest.txt`（`sha256 size path`，相对路径、禁 `..`）+ 分片，格式与 `docs/DATA_SOURCE.md` 协议**兼容**（可被同类同步器消费）。
2. `atst lake import --source <dir|file://|http(s)://>`：校验 size + sha256 后替换，跳过一致文件（幂等）。
3. **source card**：每条能力附"最后成功时间 / 样本数 / 延迟 / 缺失率 / 单位口径 / 限流与费率"，落 `docs/providers/` 并可机读——这是 free-stockdb 结构性做不到的（它不知道上游是谁）。

**验收**：导出→导入往返后 `sha256` 全一致；导入中断后重跑不产生半文件；source card 覆盖全部 `alive` 能力。

---

## 7. 路线与优先级

| 期 | 内容 | 前置 | 为什么这个顺序 |
|----|------|------|----------------|
| **B** | 复权事实源硬化 | — | **唯一的数据可信度缺口**；没有它，A/C/D 的产出都不能用于回测 |
| **A** | 本地行情集市 | B（因子表要落进集市） | free-stockdb 的核心卖点（全市场批量）全压在这一层 |
| **C** | 宽表日线 | A + B | 依赖集市的 as-of 拼接能力 |
| **D** | 指标与合成层 | A | 依赖集市的批量矩阵取数 |
| **E** | Excel / HTML | A（HTTP 面已有） | 纯交付层，可与 C/D 并行 |
| **F** | 审计 + 镜像 | A | 集市稳定后才谈导出协议 |

**最小可用闭环（MVP）**：B + A 完成即可宣称"全市场日线批量回测跑得通且复权可信"——这正是 free-stockdb 的全部日线价值。
分钟/tick 落盘建议放到 A 之后的 **A2**：全市场分钟数据体量是日线的 ~240 倍，必须先有分区/清单/限流三件套再上量。

---

## 8. 风险与"不做的事"

| 风险 | 处置 |
|------|------|
| 全市场分钟落盘触发东财 IP 软限流（约 20+ 分钟才解除） | A2 阶段强制走源层 `rate_limiter`，限速 + 分时段；优先用腾讯/百度分钟源分散 |
| 换取"宽表体验"可能牺牲来源可追溯 | 宽表保留 `_src_*` provenance 列；窄表永远是事实源 |
| 指标层与 finkit 边界模糊 | atst 只做取数/参数/封装，**不实现数学**；finkit 是计算内核 |
| 新增本地层可能诱使"隐式本地优先" | 显式 `LocalFirstPolicy` + `FallbackPolicy`，默认路径不变；有测试钉住"不显式声明就不换源" |
| 文档数字漂移 | 本文在 `archive/`，不进 `active_docs()`；但若把任何结论写进活文档（README/ARCHITECTURE），须同步 `tests/architecture/test_doc_code_consistency.py` 的钉子 |

**明确不做**：
1. 不做闭源二进制分发（free-stockdb 的 `stockdb.pyd` 模式与本项目开源纪律冲突）；
2. 不做"镜像即产品"——atst 的价值是**一手可审计**，不是分发；
3. 不为了对齐而牺牲 `BAR_FIELDS` 窄表事实源（宽表是派生层）；
4. 不实现自定义 C++/Rust 存储引擎——DuckDB/Arrow 分区 + parquet 已够，避免重复造轮子。

---

## 9. 附录 A：覆盖结论一句话版

> **atst 在"数据从哪来、有多少、能否审计"上全面胜出；在"数据落盘后怎么被批量用"上明显落后。**
> free-stockdb 卖的是后者，而后者恰好是可以用 A/B 两期补上的工程活——不是数据护城河。
> 反过来，free-stockdb 的镜像黑盒、闭源 pyd、无实时订阅、无财报/宏观/基金/衍生品，是 atst 用 195 个能力换来的、对方结构性拿不到的东西。

## 10. 附录 B：字段对齐表（Phase C 目标态）

| free-stockdb 日线字段 | atst 来源 | 备注 |
|----------------------|-----------|------|
| `date` / `code` | 本地集市主键 | `date` 为 `YYYYMMDD`；atst 另有 `datetime` 字符串口径 |
| `open` `high` `low` `close` `pre_close` | `BAR_FIELDS` + prev_close 链 | 复权后按 Phase B 因子折算 |
| `volume` `amount` | `BAR_FIELDS` | 单位由 `DataProfile` 钉死（股 / 元） |
| `pct_chg` `amplitude` | 派生 | 由 `pre_close` 推出，缺失兜底规则入文档 |
| `turnover` `vol_ratio` | 分源字段 | 新浪 `turnover` 原值透出为 `turnover_raw` 的纪律保留 |
| `pe_ttm` `pb` | `stock_valuation` / `valuation_history` | as-of 拼接 |
| `total_mv` `float_mv` `total_share` `float_share` | 估值/基本面面 | as-of 拼接 |
| `is_st` | `st_list` | as-of 快照（历史 ST 状态需带日期） |
| `name` | `profile` | 名称随标的变更，取 as-of |
