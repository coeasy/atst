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

# 5) 全市场：先探测代码表，再照常同步
python scripts/sync_daily_history.py --scan        # 约 15–20 分钟，写 data/universe.csv
python scripts/sync_daily_history.py               # 随后这行按 universe.csv 跑全市场（实测 8385 只 / 六类）

# 6) 长历史全量重拉 / 只要股票 / 更慢更礼貌
python scripts/sync_daily_history.py --full --lookback 800
python scripts/sync_daily_history.py --exclude-class etf lof bond bshare
python scripts/sync_daily_history.py --workers 8 --gap-sleep 0.5
```

落地结构（每支一个文件，文件名即代码；`data/` 已在 `.gitignore` 里）：

```
data/
├── universe.csv      # 代码表（--scan 生成，两列：代码,类别）
├── state.json        # 断点：上次拉到哪、哪些失败、哪些被口径自检拦下
└── day/
    ├── stock/        sh600519.parquet …
    ├── index/        sh000001.parquet …
    ├── etf/          sh510300.parquet …
    ├── lof/          sh501018.parquet …
    ├── bond/         sh113050.parquet …
    └── bshare/       sh900932.parquet …
```

## 标的类别：不是"只有 A 股股票"

代码段决定类别，类别决定**落盘目录**和**走不走指数位**。这张表是实测的——
113 个候选段逐个抽 30 个尾号探测过，46 个有货、67 个是空段：

| 类别 | 代码段 | 落盘目录 | 走指数位 |
|---|---|---|---|
| `stock` | 沪 600/601/603/605/688/689、深 000/001/002/003/300/301 | `data/day/stock/` | 否 |
| `index` | 沪 000\*、深 399\* | `data/day/index/` | **是** |
| `etf` | 沪 510/511/512/513/515/516/519/520/530/560/561/562/563/588/589、深 158/159 | `data/day/etf/` | 否 |
| `lof` | 沪 501/502、深 160/161/163/164/165 | `data/day/lof/` | 否 |
| `bond` | 沪 110/111/113/118、深 121/123/127/131（可转债） | `data/day/bond/` | 否 |
| `bshare` | 沪 900\*、深 200\*（B 股） | `data/day/bshare/` | 否 |

`--scan` 默认扫全部 6 类 / 48 段 / **48000 个候选代码**。三类本来以为能用、
实测不是那么回事的：

- **北交所 `bj*` 拿不到。** 客户端直接拒：`[E3040] symbol-based 7709 request
  尚未验证 market=2；当前只允许 SZ/SH`。扫它 1000 个候选一个都探不出来，所以
  不在默认段表里——见脚本里的 `UNSUPPORTED_CLASSES` 备注，别去猜它是"空段"。
- **B 股段 `sh900*` / `sz200*` 密度极低。** 抽样 30 个尾号 0 命中，但
  `sh900932`、`sz200011` 这些真能拉到日线。所以段表按"实测有货"保留，
  宁可多扫 2000 个候选也不漏。
- **ETF 段远不止 510/511。** 沪 530/560/561/562/563/588/589、深 158 都有货，
  只扫老段会漏掉一多半 ETF。

按类别裁剪：`--include-class stock index` 只同步股票和指数；
`--exclude-class etf lof bond bshare` 反过来。

## 全量还是增量

TDX 的 `bars` 只给"最近 N 根"，没有翻页游标，所以"全量"在这里有两个意思：

| 模式 | 做什么 | 什么时候用 |
|---|---|---|
| 默认（增量） | 每只只拉最近 `--lookback`（320）根，与磁盘上已有的按日期去重合并 | **每天盘后都跑这个**。历史不会丢，也不会随轮数漂 |
| `--full` | 按 `MAX_WINDOW`（8000）拉，整体重写成长历史 | 首次铺底、换机器、校准口径 |

即：**首次跑一次 `--full` 铺满历史，之后每天无参数跑增量**就够了。
日常那 320 根和全量 8000 根相比只是一次请求的分页数差别，增量省的是 90% 流量。

注意合并语义：每轮拉回的 320 根会和本地已有的去重合并，所以长度收敛到
稳定值（320 → 350 → 350）而不是每轮都涨。停牌日不补空行。

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--root` | `<仓库>/data` | 落盘根目录（锚仓库根，不依赖当前工作目录） |
| `--symbols` | – | 显式指定代码，跳过宇宙解析 |
| `--universe-file` | – | 自备清单 CSV，一行一个代码（可带 `symbol` 列名） |
| `--vipdoc` | 自动探测 | 本地通达信目录，从 `vipdoc/<mkt>/lday/*.day` 取代码表 |
| `--index` / `--no-index` | 自动 | 强制/禁止走指数位（默认按类别自动判定） |
| `--full` | 关 | 全历史重拉（每只按 `MAX_WINDOW`） |
| `--lookback` | `320` | 每只每轮拉多少根日线 |
| `--limit` | `0`（全部） | 只同步前 N 只，试水用 |
| `--include-class` / `--exclude-class` | 全类别 | 按类别裁剪同步范围 |
| `--workers` | `4` | 并发只数；实际瓶颈是连接池 `slots_per_host` |
| `--gap-sleep` | `0.2` | 每只之间的礼貌间隔秒数 |
| `--timeout` | `10.0` | 单请求超时（秒） |
| `--dry-run` | 关 | 只拉取不落盘 |
| `--scan` | 关 | 只探测全市场代码表并写入 `<root>/universe.csv`，不落行情 |
| `--scan-class` | 全类别 | `--scan` 要探测的类别 |
| `--scan-refresh` | 关 | `--scan` 连已知代码也重探（默认跳过，已入库的不重复探） |
| `--list-class` | 关 | 打印类别 / 代码段 / 目录 / 候选规模后退出 |
| `--state` | `<root>/state.json` | 自定义状态文件路径 |

退出码：`0` 成功（至少一只落盘）；`2` 宇宙缺失（找不到代码表）或全市场一只都没成
（视为环境级：主站不可达，不是脚本缺陷）。

## 为什么是这套设计

这几条都是踩出来的，不是装饰。

**1. 零参数要真的能用。** 默认 `--root` 锚在仓库根的 `data/`，不是相对当前工作目录——
crontab 的 cwd 往往不是仓库根，按 cwd 解析会让数据在换目录时悄悄漂移。
宇宙按 `本地 vipdoc → data/universe.csv → 内置默认宇宙` 三级兜底，三级全落空时
用内置宇宙（20 只龙头 + 8 个宽基指数，每只都实测过主站回得来日线），
所以"什么都没准备"这条路也有数据。

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

## 调度

```cron
# crontab -e —— 工作日 18:30（收盘后，错峰避开开盘前 30 分钟）
30 18 * * 1-5 cd /path/to/atst && python scripts/sync_daily_history.py >> data/sync.log 2>&1
```

Windows 计划任务同理，指向仓库里的 `scripts/sync_daily_history.py`。
跑完看一眼输出里的「按类别」和 `data/state.json` 的 `last_run` / `bars`
就知道有没有真的成；落盘那行会顺带打出总文件数与体积。

## 常见坑

| 现象 | 原因 |
|---|---|
| 指数拉回来 `high < low`、日期是 `0080-26-13` | index 位没置上，加 `--index` 或确认代码走的是 `sh000*` / `sz399*` |
| 报「找不到 universe」 | `0x044D` 已 offline；给 `--symbols` / `--universe-file` / `--vipdoc`，或先 `--scan` |
| 每轮行数一直涨 | 没合并（用 `--dry-run` 或自己改过 `_dedup_merge`） |
| 全市场 SKIP | 主站不可达，先 `python -m atst hosts list` 看真实主站清单 |
| 一只被 REJECT | 口径自检拦下了，看输出里的原因；文件**没被改写**，上一版仍在 |
| `--scan` 之后零参数跑变慢 | 正常：`universe.csv` 生效，跑的是全市场；加 `--limit N` 先试水 |
| `--scan` 跑一次要十几分钟 | 正常：48000 个候选，实际约 22ms/个；已入库的代码会被跳过，重跑秒回 |
| 北交所代码探不出来 | 协议层未开放（E3040），见 `UNSUPPORTED_CLASSES` 备注 |

## 边界

- 只同步**日线**。分钟线可换 `period="min"`（`--lookback` 语义要跟着调），但 `0x052D`
  分页上限是 `0xFFFF` 且 `start + count > 0x10000` 会直接抛 `ParseError`，长历史要自己切 windows。
- 不负责复权。`adjusted-bars` 是另一个 capability，需要复权序列得自己接。
- `--scan` 用的是"主站认不认这个代码"的判据，会得到主站口径的 universe：实测全 6 类
  **8385 只**（stock 5223 / etf 1819 / index 554 / bond 356 / lof 355 / bshare 78），
  含少量已退市但主站仍留历史的代码；要精确名单请自备 `--universe-file`。
- 全市场默认 320 根铺底 + 增量，落盘约 **279 MiB / 8385 个 parquet**，一轮约 9 分钟；
  `--full` 铺长历史会按 8000 根/只涨（单只 8000 根约 0.4 MiB，自己乘）。
- 落盘是**追加合并**语义：只保留去重后的 `YYYY-MM-DD`，同一天重复覆盖。停牌日不会补空行。
- 迁移到分类目录前写的扁平 `data/day/*.parquet` 仍会被读回来当断点；新一轮落盘会写进
  对应类别子目录，之后扁平那份就成了孤儿文件，可直接删。
- 脚本直接运行时会把仓库根加进 `sys.path`，所以不要求先 `pip install -e .`。
