# 08：用 TDX 接口每天同步全市场历史日线到本地目录

每日盘后把全市场日线同步一份到本地 `data/` 目录，脚本在 `scripts/sync_daily_history.py`。  
**一条命令直接跑**，不需要写任何代码、不需要先准备清单：

```bash
python scripts/sync_daily_history.py
```

脚本会自己定宇宙（本地通达信 `vipdoc` → `data/universe.csv` → 内置默认宇宙），  
按代码段判定标的类别与指数位，并发拉日线，按日期键去重合并后原子改名落盘，  
并把「上次拉到哪」写进 `data/state.json`。

## 快速开始

```bash
# 1) 看一眼会同步哪些类别、哪些代码段、多少候选（不联网，秒回）
python scripts/sync_daily_history.py --list-class

# 2) 零参数：增量同步内置宇宙（20 只龙头 + 8 个宽基指数），落 data/day/
python scripts/sync_daily_history.py

# 3) 先验证连通性：只拉不落盘
python scripts/sync_daily_history.py --dry-run

# 4) 指定几只试试（代码可带/不带 sh、sz 前缀）
python scripts/sync_daily_history.py --symbols 600519 sz000001

# 5) 全市场：先拿代码表，再照常同步
python scripts/sync_daily_history.py --fetch-list  # 约 20 秒，写 data/universe.csv（七类全要）
python scripts/sync_daily_history.py               # 随后这行按 universe.csv 跑全市场

# 6) 长历史全量重拉 / 只要股票 / 更慢更礼貌
python scripts/sync_daily_history.py --full --lookback 800
python scripts/sync_daily_history.py --exclude-class etf lof bond bshare
python scripts/sync_daily_history.py --workers 8 --gap-sleep 0.5

# 7) 只要某一类的全部标的（只拿清单，不落行情）
python scripts/sync_daily_history.py --fetch-list --fetch-class etf
atst universe etf --show 5                          # 或者用 CLI，见下面那节

# 8) 收工体检：不联网，看"清单 vs 落盘"对不对得上（有缺口退出码 2）
python scripts/sync_daily_history.py --doctor
python scripts/sync_daily_history.py --doctor --json   # 机器可读
```

> **代码表怎么拿？两个入口，别记混。**  
> `--fetch-list` 是直接向**清单端点**要（新浪行情中心节点，秒级）；  
> `--scan` 是**自己枚举段 + 逐个 `bars` 探**（TDX 协议，分钟级）。  
> 默认走 `--fetch-list`，拿不到再自动降级到 `--scan`。只有"主站口径"的名单  
> （比如连已退市但主站还留着的代码也要）才需要 `--scan`。

落地结构（每支一个文件，文件名即代码；`data/` 已在 `.gitignore` 里）：

```

data/  
├── universe.csv      # 代码表（--fetch-list / --scan 生成，三列：代码,类别,名称）  
├── state.json        # 断点：上次拉到哪、哪些失败、哪些被口径自检拦下  
└── day/  
    ├── stock/        sh600519.parquet …  
    ├── bse/          bj920000.parquet …  
    ├── index/        sh000001.parquet …  
    ├── etf/          sh510300.parquet …  
    ├── lof/          sh501018.parquet …  
    ├── bond/         sh113050.parquet …  
    └── bshare/       sh900932.parquet …

```

> **⚠ 北交所（bse 类）目录是"预留"，不会由同步产出文件。**  
> `bj*` 在 TDX 协议层直接被拒（`[E3040] market=2 尚未验证，只允许 SZ/SH`），  
> `--fetch-list` 会把 348 只北交所写进 `universe.csv`，但默认同步会**静默剔除**它们、  
> 显式 `--include-class bse` 则**全部 SKIP**（并给出告警），`--doctor` 也**不把它们算缺口**。  
> 所以这一格目录目前是空的——要真正落北交所，需要非 TDX 数据源，脚本暂不支持（见下文）。

## 标的类别：不是"只有 A 股股票"

代码段决定类别，类别决定**落盘目录**和**走不走指数位**。这张表是实测的——
113 个候选段逐个抽 30 个尾号探测过，46 个有货、67 个是空段。类别的单一事实源是
`atst.universe._classes.ASSET_CLASSES`（脚本的段表也从它派生，两处不会各改一半）：

| 类别 | 代码段 | 落盘目录 | 走指数位 | 清单端点（新浪） |
|---|---|---|---|---|
| `stock` | 沪 600/601/603/605/688/689、深 000/001/002/003/300/301/**302** | `data/day/stock/` | 否 | `hs_a` |
| `bse` | 北交所 `bj920` | `data/day/bse/` | 否 | `hs_a`（混在全 A 里） |
| `index` | 沪 000\*、深 399\* | `data/day/index/` | **是** | 无，只能探 |
| `etf` | 沪 510/511/512/513/515/516/519/520/530/560/561/562/563/588/589、深 158/159 | `data/day/etf/` | 否 | 无，只能探 |
| `lof` | 沪 501/502、深 160/161/163/164/165 | `data/day/lof/` | 否 | 无，只能探 |
| `bond` | 沪 110/111/113/118、深 121/123/127/131（可转债） | `data/day/bond/` | 否 | 无，只能探 |
| `bshare` | 沪 900\*、深 200\*（B 股） | `data/day/bshare/` | 否 | `hs_b` |

`--scan` 默认扫 **6 类 / 49 段 / 49000 个候选代码**（类别表里 7 类，
`bse` 因为客户端探不出来所以不在扫描表里，只由清单端点供给）。三类本来以为能用、
实测不是那么回事的：

- **北交所 `bj*`：`--scan` 探不到，但 `--fetch-list` 拿得到。** 客户端直接拒：
  `[E3040] symbol-based 7709 request 尚未验证 market=2；当前只允许 SZ/SH`——
  扫它 1000 个候选一个都探不出来，所以 `bse` 不在默认扫描表里（类别表保留 `bj920`
  一段备用，430/830/870 这些旧段实测已空，留着只是让每轮白探几千个注定空的候选）。
  但新浪 `hs_a` 里**带着**
  `bj*`：`--fetch-list` 全 A 拉回来按前缀拆，实测出 **348 只**，一条不少。
  想只要北交所就用 `--fetch-class bse`。
- **B 股段 `sh900*` / `sz200*` 密度极低。** 抽样 30 个尾号 0 命中，但
  `sh900932`、`sz200011` 这些真能拉到日线。所以段表按"实测有货"保留，
  宁可多扫 2000 个候选也不漏。
- **ETF 段远不止 510/511。** 沪 530/560/561/562/563/588/589、深 158 都有货，
  只扫老段会漏掉一多半 ETF。

按类别裁剪：`--include-class stock index` 只同步股票和指数；
`--exclude-class etf lof bond bshare` 反过来。

## 按类别取标的清单（`--fetch-list` / `atst universe`）

"全市场有哪些标的"是一个**工具方法**，不是行情 capability：`atst list 0`
（`0x044D SECURITY_LIST`）实测已 offline（E3030/E3035 多主站无响应），所以
`atst.universe` 用三个源按 **代码表 → 新浪 → tdx 段表**的顺序降级供给，
先拿到哪个用哪个：

| 源 | 怎么做 | 耗时 | 覆盖 |
|---|---|---|---|
| `table` | 读 `<root>/universe.csv`（上面两条命令产出的那张表） | 毫秒 | 表里有什么就有什么 |
| `sina` | 新浪行情中心节点（`hs_a` 全 A / `hs_b` B 股），分页并发 | 约 10 秒 | `stock` / `bse` / `bshare`，**带名称** |
| `tdx` | 自己枚举段 + `bars(count=1)` 探 | 分钟级 | 全类别（ETF/LOF/可转债/指数只能靠它） |

```python
from atst.universe import list_universe

list_universe("stock")                       # 全部 A 股（5223 只）
list_universe("etf")                         # 全部 ETF（走 tdx 探）
list_universe("bse")                         # 全部北交所（348 只，从 hs_a 里拆出来）
list_universe("all")                         # 七类全量
list_universe("stock", source="sina")        # 显式指定源；拿不到会抛，不会悄悄换源
list_universe("stock", source="auto", limit=10)   # 试水 10 条
```

CLI 同一套：

```bash
atst universe --list-class                   # 七类的段/目录/节点一览（不联网）
atst universe etf --dry-run                  # 只看各源代价，不真拉
atst universe etf --show 5                   # 打印前 5 条
atst universe stock --json                   # 机器可读
```

脚本侧（`--fetch-list`）只是把结果直接写进 `<root>/universe.csv`，供后续同步消费：

```bash
python scripts/sync_daily_history.py --fetch-list                       # 七类全要
python scripts/sync_daily_history.py --fetch-list --fetch-class etf bond  # 只要两类
python scripts/sync_daily_history.py --fetch-list --fetch-dry-run        # 先试算代价
```

三步都不落行情，只在**拿代码表**：

| 参数                | 默认     | 说明                                                      |
| ----------------- | ------ | ------------------------------------------------------- |
| `--fetch-list`    | 关      | 只拉代码表写 `<root>/universe.csv`，不落行情                       |
| `--fetch-class`   | `all`  | 要哪些类别（`all` 或 `stock,bse,etf` 这种逗号列表）                   |
| `--fetch-source`  | `auto` | 强制源：`auto` / `table` / `sina` / `tdx`；显式指定且拿不到会告一句并跳过该类 |
| `--fetch-dry-run` | 关      | 只报告每类从哪个源拿、多少条 / 多少候选，不真写表                              |

实测条数（2026-10-03，新浪源）：`stock` 5223 / `bse` 348 / `bshare` 78；  
`etf` `lof` `bond` `index` 四个类新浪没有入口，会直接降级到 tdx 段表探测，  
`--fetch-dry-run` 会先告诉你这四类各要探多少候选（段数 × 1000）再让你决定。

**HTTP 面**（起了 `atst serve` 之后）只发**已经落好的表**，不在请求里外呼：

```bash
curl 'http://127.0.0.1:8000/v13/universe'                # 类别表（静态）
curl 'http://127.0.0.1:8000/v13/universe/etf'            # 本地代码表里的全部 ETF
curl 'http://127.0.0.1:8000/v13/universe/all?limit=50'   # 七类各取前 50 条
```

这是刻意的分工：新浪那一级是秒级外呼、tdx 那一级是分钟级批作业，把它们塞进请求
路径就会占住网关（超时与重试语义也全乱）。**刷新清单归批任务（`--fetch-list`），
网关只负责把表发出去**。表还没生成时回 `200` + `count: 0` + `hint`，不是 4xx。

## 全量还是增量

TDX 的 `bars` 只给"最近 N 根"，没有翻页游标，所以"全量"在这里有两个意思：

| 模式       | 做什么                                      | 什么时候用                      |
| -------- | ---------------------------------------- | -------------------------- |
| 默认（增量）   | 每只只拉最近 `--lookback`（320）根，与磁盘上已有的按日期去重合并 | **每天盘后都跑这个**。历史不会丢，也不会随轮数漂 |
| `--full` | 按 `MAX_WINDOW`（8000）拉，整体重写成长历史           | 首次铺底、换机器、校准口径              |

即：**首次跑一次 `--full` 铺满历史，之后每天无参数跑增量**就够了。  
日常那 320 根和全量 8000 根相比只是一次请求的分页数差别，增量省的是 90% 流量。

但 `--full` 只决定**每只拉多深，不决定跑多少只**。"全市场"由代码表决定，而代码表  
是要先探出来的：本机没有通达信目录、`<root>/universe.csv` 又不存在时，脚本会回落到  
**内置样例宇宙 28 只**（20 只龙头 + 8 个宽基指数）。所以

```bash
python scripts/sync_daily_history.py --full      # 只有 28 只：--full 管不到"多少只"
python scripts/sync_daily_history.py --scan      # 先探出全市场 8385 只 → data/universe.csv
python scripts/sync_daily_history.py --full      # 这才真是全市场
```

脚本现在会把这两行原样打回给你（`--root` 也一起拼回去），落盘预算  
（根数 / 主站请求数）也在开跑前印出来——成本项是**请求数**不是根数，  
`bars` 没翻页游标、`count` 超过单页 800 就自动分页，全市场 `--full` 是  
8385 只 × 10 页 ≈ 8.4 万次请求，这个量级得先看到再决定要不要莽。

注意合并语义：每轮拉回的 320 根会和本地已有的去重合并，所以长度收敛到  
稳定值（320 → 350 → 350）而不是每轮都涨。停牌日不补空行。

## 同步全市场全部历史：怎么跑（推荐流程）

把"全市场所有上市股票的完整日线历史"落盘，分四步。每一步都只要一条命令，
且都**零参数可跑**（找不到代码表时脚本会把该跑的命令原样打回给你）：

```bash
# ① 拿代码表（秒级，七类全要；stock 5223 / bse 348 / bshare 78 + etf/lof/bond/index）
python scripts/sync_daily_history.py --fetch-list
#   → 写 data/universe.csv（8733 只，含北交所）

# ② 首次铺满历史：每只按 MAX_WINDOW(8000) 拉，整体重写成长历史
python scripts/sync_daily_history.py --full
#   → 8385 只（六类）× ~10 页 ≈ 8.4 万次主站请求，实测约 30–60 分钟
#     开跑前预算会印在屏幕上，先看到再决定要不要莽

# ③ 日常增量：之后每天盘后无参数跑这个，历史不会丢、也不会随轮数漂
python scripts/sync_daily_history.py
#   → 每只只拉最近 320 根，与磁盘已有的按日期去重合并

# ④ 体检（不联网）：清单 vs 落盘对不对得上，有缺口退出码 2
python scripts/sync_daily_history.py --doctor
```

**关键点：**

- `--full` 只决定**每只拉多深（8000 根）**，不决定**跑多少只**。"跑多少只"由
  `universe.csv` 决定，所以 ① 必须先于 ②——否则 `--full` 只落到内置 28 只样例。
- 增量（③）和全量（②）的合并语义相同：每轮拉回的 N 根会和本地已有的按日期键
  去重合并，长度收敛到稳定值。所以**先 `--full` 铺一次，之后无参数增量即可**，
  日常那 320 根和全量 8000 根只是一次请求的分页数差别，增量省的是 90% 流量。
- 限速在连接池层（`slots_per_host` / `SessionRateLimiter`），单轮 8 万请求是协议
  上限下的真实代价，分批跑（如 `--include-class stock` 先跑股票、再跑其他类）能
  更温和地避开主站软限流。

**北交所（bj\*）的边界——必须知道：** 上面的 ② 实际只落 **8385 只（六类）**，
不是 8733。另外 348 只北交所在 TDX 协议层取不到（`E3040`），它们只会出现在
`universe.csv` 里：默认同步静默剔除、显式 `--include-class bse` 全部 SKIP（有告警）、
`--doctor` 也不算缺口（单列"协议不支持"）。**要真正含北交所的全 A 股落盘，需要
非 TDX 数据源，脚本目前不支持。** 如果只想干净地落"能落的那部分"，不必管这 348 只；
它们不会污染缺口读数与 `state.failed`。

## 参数

| 参数                                    | 默认                  | 说明                                       |
| ------------------------------------- | ------------------- | ---------------------------------------- |
| `--root`                              | `<仓库>/data`         | 落盘根目录（锚仓库根，不依赖当前工作目录）                    |
| `--symbols`                           | –                   | 显式指定代码，跳过宇宙解析                            |
| `--universe-file`                     | –                   | 自备清单 CSV，一行一个代码（可带 `symbol` 列名）          |
| `--vipdoc`                            | 自动探测                | 本地通达信目录；默认跨平台自动探测（Windows `C:/new_tdx`、`D:/new_tdx`；其他系统 `~/new_tdx`、`/opt/new_tdx`、`~/.wine/drive_c/new_tdx`），从 `vipdoc/<mkt>/lday/*.day` 取代码表 |
| `--index` / `--no-index`              | 自动                  | 强制/禁止走指数位（默认按类别自动判定）                     |
| `--full`                              | 关                   | 全历史重拉（每只按 `MAX_WINDOW`）                  |
| `--lookback`                          | `320`               | 每只每轮拉多少根日线                               |
| `--limit`                             | `0`（全部）             | 只同步前 N 只，试水用                             |
| `--include-class` / `--exclude-class` | 全类别                 | 按类别裁剪同步范围                                |
| `--workers`                           | `4`                 | 并发只数；实际瓶颈是连接池 `slots_per_host`           |
| `--gap-sleep`                         | `0.2`               | 每只之间的礼貌间隔秒数                              |
| `--timeout`                           | `10.0`              | 单请求超时（秒）                                 |
| `--dry-run`                           | 关                   | 只拉取不落盘                                   |
| `--scan`                              | 关                   | 只探测全市场代码表并写入 `<root>/universe.csv`，不落行情  |
| `--scan-class`                        | 全类别                 | `--scan` 要探测的类别                          |
| `--scan-refresh`                      | 关                   | `--scan` 连已知代码也重探（默认跳过，已入库的不重复探）         |
| `--fetch-list`                        | 关                   | 只拉代码表写 `<root>/universe.csv`（走 `atst.universe`，秒级） |
| `--fetch-class`                       | `all`               | `--fetch-list` 要取的类别（`etf` / `etf,bond` / `all`）|
| `--fetch-source`                      | `auto`              | `--fetch-list` 的取数源：`auto` / `table` / `sina` / `tdx` |
| `--fetch-dry-run`                     | 关                   | `--fetch-list` 只看各源代价，不真拉                   |
| `--doctor`                            | 关                   | 不联网体检：清单 vs 落盘的缺口 / 空文件 / 落伍 / 断点漂移     |
| `--json`                              | 关                   | 只影响 `--doctor` 的输出（机器可读）                 |
| `--list-class`                        | 关                   | 打印类别 / 代码段 / 目录 / 候选规模后退出                |
| `--state`                             | `<root>/state.json` | 自定义状态文件路径                                |
| `--verbose`                           | 关                   | 逐只打印明细（状态 / 行数 / 末日 / 原因）；默认只在结尾汇总 SKIP/REJECT + 单行滚动进度 |

退出码：`0` 成功（至少一只落盘）；`2` 宇宙缺失（找不到代码表）或全市场一只都没成  
（视为环境级：主站不可达，不是脚本缺陷）；`--doctor` 下 `2` = 有缺口或空文件。

## 体检：`--doctor`（不联网）

同步链路跑到最后，唯一该问的问题是"**代码表里的每一只，磁盘上到底有没有**"。
`--doctor` 回答它，而且**不联网**——断网、盘前、主站挂了都能跑：

```bash
python scripts/sync_daily_history.py --doctor
python scripts/sync_daily_history.py --doctor --json    # 机器可读
```

读出来四件事，它们对应四类不同的坏法：

| 读数 | 含义 | 为什么单列一格 |
|---|---|---|
| **缺口** | 代码表里有、`day/<类别>/` 里没有 parquet | 同步从没成功过，或被 `--exclude-class` 裁掉却忘了 |
| **空文件** | parquet 在，但 0 行 | 最像"成功了"的失败：文件在那儿，数据不在 |
| **落伍** | 最后日期落后于全市场最新交易日 | 单只停牌是正常；成片落伍说明某一类根本没被同步 |
| **断点漂移** | `state.json` 记的日期 ≠ 磁盘实际日期 | 换过 `--root`、或磁盘被手工改过；磁盘才是断点第一事实源 |

退出码 `2` 只在**缺口 / 空文件**时给（它们要修）；落伍与断点漂移是"要知道"，
不抬退出码，适合放进定时任务的告警而不是熔断。

本机实测（8385 只代码表、六类落了盘）：

```text
体检 data
  清单：代码表 data/universe.csv（8385 只）
  落盘：8385/8385（bond 356 / bshare 78 / etf 1819 / index 554 / lof 355 / stock 5223）
  最新交易日：2026-09-30
  缺口：0
  空文件：0
  落伍：104  例：sh113575 sh501023 sh501037 ...
```

那 104 只"落伍"集中在可转债与 LOF——它们本来就不是每天有成交，属于正常读数；
这正是把它和"缺口"分开报的原因：混在一起会让人以为同步坏了。

## 读回：把落盘的日线拿回来

落盘只是前半段。读回来用 `atst.output.from_parquet`（与 `to_parquet` 对称的那一面）：

```python
from atst.output import from_parquet

rows = from_parquet("data/day/stock/sh600519.parquet")               # list[dict]
dates = from_parquet("data/day/stock/sh600519.parquet", columns=["date"])

import pandas as pd
df = pd.DataFrame(rows)
```

| 情况 | 行为 |
|------|------|
| 文件在、有数据 | `list[dict]`；`columns=` 只取需要的列（断点续拉只读 `date` 那一列）|
| 文件在、0 行 | `[]` |
| 路径不存在 | 抛 `FileNotFoundError`（打错路径不该看起来像"库里没数据"）|
| 没装 pyarrow | 抛 `DependencyMissingError`（不静默返回空列表）|

日期列回来是 `datetime` 对象而不是原字符串——`to_parquet` 经 DataFrame 写，`date` /
`datetime` 这类列名会被解析成时间类型；按 `YYYY-MM-DD` 比日期的调用方要自己归一一次
（脚本侧的 `_last_date_on_disk` 就是这么做的）。

CSV 那一侧的对称面是 `atst.output.from_csv`（零依赖）：导出给 Excel / 人工过目、
加工之后读回程序。CSV 没有类型，读回全 `str`，不做隐式数值推断：

```python
from atst.output import to_csv, from_csv

to_csv(rows, "watchlist.csv")            # utf-8-sig，Excel 双击直开
back = from_csv("watchlist.csv")         # list[dict[str, str]]，带不带 BOM 都认
codes = from_csv("watchlist.csv", columns=["code"])
```

DuckDB 那侧用它自己的查询接口，所以读回面是 Parquet 与 CSV 两支。

---

## 为什么是这套设计

这几条都是踩出来的，不是装饰。

**1. 零参数要真的能用。** 默认 `--root` 锚在仓库根的 `data/`，不是相对当前工作目录——  
crontab 的 cwd 往往不是仓库根，按 cwd 解析会让数据在换目录时悄悄漂移。  
宇宙按 `本地 vipdoc → data/universe.csv → 内置默认宇宙` 三级兜底，三级全落空时  
用内置宇宙（20 只龙头 + 8 个宽基指数，每只都实测过主站回得来日线），  
所以"什么都没准备"这条路也有数据。`vipdoc` 的默认探测是**跨平台**的：  
Windows 看 `C:/new_tdx`、`D:/new_tdx`；其他系统看 `~/new_tdx`、`/opt/new_tdx`、  
`~/.wine/drive_c/new_tdx`（Wine 里装的 Windows 版通达信）——不再硬编码 Windows 盘符。

**2. 类别从代码段推出来，指数位跟着类别走。** `0x052D` 的 index 位弄反，主站回的是  
一堆荒唐日期：实测 `sh000001` 不走 index 位得到 `5616-57-83`、`sh000300` 得到  
`0865-09-14`。所以只有 `index` 类走 `bars(index=True)`；B 股（`sh900*` / `sz200*`）  
虽然长得像指数也走个股路径。`--index` / `--no-index` 可整体覆盖。

**3. 续拉不依赖 `state.json`。** 断点的第一事实源是**磁盘上的 parquet 本身**：  
`state.json` 只是它的加速副本。state 被误删、或换了 `--root` 之后 state 没跟过来，  
脚本从 parquet 尾部读回最后日期继续，而不是退化成"最近 lookback 根从头再拉"。  
`--full` 一开就直接拉满 `MAX_WINDOW` 窗口。

**4. 续拉不能按日期游标。** `0x052D` 的 `start` 是 16-bit **分页偏移**，不是日期。  
"上次那根到哪了"只能靠末日 + 多要 `RESUME_OVERLAP`（30 根）回来合并。  
每轮拉回 `lookback` 根，和磁盘上已有的按日期键去重合并，长度才会收敛到稳定值  
（320 → 350 → 350），否则每轮都把文件重写成 `lookback` 根，历史窗口随轮数往上漂。

**5. 落地必须原子。** 先写同目录 `.tmp` 再 `os.replace`。写一半崩了不会留半个坏 parquet。  
注意原子改名的临时文件后缀不是 `.parquet`，`atst.output` 的 `write()` 按后缀推断  
恰好在这里会抛 `ValueError`（审计 §2-15 已经把那个静默语义拿掉了），所以脚本里显式传  
`fmt="parquet"`。

**6. 限速在连接池那一层。** 并发旋钮是 `TdxClient(slots_per_host=…)`（每台主站几条连接），  
脚本再叠一道 `--gap-sleep` 加随机 jitter。不可重试的错误（`Advice.retryable` 为假）直接  
SKIP，不白等。

**7. 代码表可以自己探出来。** `0x044D SECURITY_LIST` 已登记 offline，一调就抛 `CommandOffline`。  
但主站对**不存在的代码回空首页**（声明 0 条），对**存在的代码回真实数据**——  
这个差别就是第一道判据。第二道是**日期键得是真日历日期**：像 `sh999999` 这种  
主站回 `0080-26-13`、另一支回 `8414-91-57`，**非空且长得像日期**，只判"非空"  
就会被当成真代码写进代码表，之后每轮为它白跑一次请求。两道都过才收。  
实测单段 1000 个候选约 25 秒、命中 747 只。

**8. `--scan` 自带段级断点。** 已入库的代码默认不重探，所以第二次跑 `--scan` 基本秒回；  
想要重新验证全部代码加 `--scan-refresh`。探错了不会写进代码表，但会白白漏掉一只，  
所以传输异常会重试一次再判"不存在"。

**9. 口径自检放在合并之后。** OHLC 自洽（high<low、high/low 包裹 open/close）、  
volume 单位是股、日期升序去重，任一条破就把原因记进 `state.json` 的 `rejected` 且**不落盘**。  
自检必须在「合并 + 排好序之后」做：`--full` 拉回来的是倒序（最新在前），  
在原始批次上校验只会撞出假的「日期非升序」。  
日期键只认 `YYYY-MM-DD`，取不到的行直接丢。

**10. 观测输出做了节流，礼貌间隔只夹在提交侧。** 之前每只都 `print + flush` 再 `sleep`  
一次，全市场 8733 只就是 8733 行刷屏 + 几十秒空转（那段时间其实什么都没取）。现在：  
进度默认只在终端用回车覆盖刷一行（`OK n / SKIP m / REJECT k …`），非终端（cron 重定向到日志）  
按阈值平铺、不往日志里塞控制字符；明细只在 `--verbose` 时逐只打印，诊断信息不再淹没在刷屏里；  
SKIP / REJECT 在结尾统一汇总预览，该修的一目了然。  
`--gap-sleep` 只在大到"真能限速"（≥ 0.05s）时才夹在每次 `pool.submit` 之间——默认全市场被  
`_effective_gap` 折算到 0.01s，夹它只是 ~87 秒空转、毫无限速收益；真正的限速在连接池的  
`slots_per_host` / `SessionRateLimiter`（见 §6）。收尾 `finally` 关掉连接池，不留孤儿连接 / 心跳线程。

## 调度

```cron
# crontab -e —— 工作日 18:30（收盘后，错峰避开开盘前 30 分钟）
30 18 * * 1-5 cd /path/to/atst && python scripts/sync_daily_history.py >> data/sync.log 2>&1
```

Windows 计划任务同理，指向仓库里的 `scripts/sync_daily_history.py`。  
跑完看一眼输出里的「按类别」和 `data/state.json` 的 `last_run` / `bars`  
就知道有没有真的成；落盘那行会顺带打出总文件数与体积。

## 常见坑

| 现象                                  | 原因                                                                                                           |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| 指数拉回来 `high < low`、日期是 `0080-26-13` | index 位没置上，加 `--index` 或确认代码走的是 `sh000*` / `sz399*`                                                          |
| 报「找不到 universe」                     | `0x044D` 已 offline；给 `--symbols` / `--universe-file` / `--vipdoc`，或先 `--scan`                                |
| 跑完只有默认那 20 + 8 只龙头                  | `--full` 只管每只拉多深、不管跑多少只。全市场要先 `--scan` 落 `universe.csv`（脚本现在会直接把命令打给你）；换过 `--root` 的也别指望读旧那份表——它写在新的 root 底下 |
| 每轮行数一直涨                             | 没合并（用 `--dry-run` 或自己改过 `_dedup_merge`）                                                                      |
| 全市场 SKIP                            | 主站不可达，先 `python -m atst hosts list` 看真实主站清单                                                                  |
| 一只被 REJECT                          | 口径自检拦下了，看输出里的原因；文件**没被改写**，上一版仍在                                                                             |
| `--scan` 之后零参数跑变慢                   | 正常：`universe.csv` 生效，跑的是全市场；加 `--limit N` 先试水                                                                |
| `--scan` 跑一次要十几分钟                   | 正常：48000 个候选，实际约 22ms/个；已入库的代码会被跳过，重跑秒回                                                                      |
| 北交所代码探不出来                           | 协议层未开放（E3040）：默认同步静默剔除、`--include-class bse` 会全部 SKIP（有告警）、`--doctor` 不算缺口（单列"协议不支持"）；脚本暂只支持 TDX 源，bj 需非 TDX 源 |

## 边界

- 只同步**日线**。分钟线可换 `period="min"`（`--lookback` 语义要跟着调），但 `0x052D`  
  分页上限是 `0xFFFF` 且 `start + count > 0x10000` 会直接抛 `ParseError`，长历史要自己切 windows。
- 不负责复权。`adjusted-bars` 是另一个 capability，需要复权序列得自己接。
- `--scan` 用的是"主站认不认这个代码"的判据，会得到主站口径的 universe：实测全 6 类  
  **8385 只**（stock 5223 / etf 1819 / index 554 / bond 356 / lof 355 / bshare 78），  
  含少量已退市但主站仍留历史的代码；要精确名单请自备 `--universe-file`。
- 全市场默认 320 根铺底 + 增量，落盘约 **290 MiB / 8733 个 parquet**（279 MiB 为
  8385 只时的实测读数，按只数折算），一轮约 9 分钟；  
  `--full` 铺长历史会按 8000 根/只涨（单只 8000 根约 0.4 MiB，自己乘）。  
  注意"8733 个"是含北交所的口径，`--full` 实际只会产出 8385 个（六类）文件——
  北交所 bj\* 在 TDX 协议层取不到，见上方「同步全市场全部历史」与落地结构里的 ⚠ 注。
- 落盘是**追加合并**语义：只保留去重后的 `YYYY-MM-DD`，同一天重复覆盖。停牌日不会补空行。
- 迁移到分类目录前写的扁平 `data/day/*.parquet` 仍会被读回来当断点；新一轮落盘写进  
  对应类别子目录并**顺手收走扁平那份**（数据已合并，留着只是幽灵副本）。删不掉
  只报外观问题，不碍同步。
- 续拉窗口只信磁盘：文件缺失 / 0 行 / 坏文件都会从铺底窗口重拉，`state.json` 只是
  `--doctor` 的对照副本、不参与取数决策——空文件跑一遍增量就能修回铺底窗口
  （要补完整历史仍要 `--full`）。
- 脚本直接运行时会把仓库根加进 `sys.path`，所以不要求先 `pip install -e .`。
