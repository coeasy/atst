# 08：用 TDX 接口每天全量同步历史日线到本地目录

每日盘后把 A 股日线同步一份到本地 `data/` 目录，脚本在 `scripts/sync_daily_history.py`。
**一条命令直接跑**，不需要写任何代码、不需要先准备清单：

```bash
python scripts/sync_daily_history.py
```

脚本会自己定宇宙（本地通达信 `vipdoc` → `data/universe.csv` → 内置默认宇宙），
按代码段自动判定指数位，并发拉日线，按日期键去重合并后原子改名落盘，
并把「上次拉到哪」写进 `data/state.json`。

## 快速开始

```bash
# 1) 零参数：增量同步内置宇宙（20 只龙头 + 8 个宽基指数），落 data/day/
python scripts/sync_daily_history.py

# 2) 先验证连通性：只拉不落盘
python scripts/sync_daily_history.py --dry-run

# 3) 指定几只试试（代码可带/不带 sh、sz 前缀）
python scripts/sync_daily_history.py --symbols 600519 sz000001

# 4) 全市场：先探测代码表，再照常同步
python scripts/sync_daily_history.py --scan          # 约 5 分钟，写 data/universe.csv
python scripts/sync_daily_history.py                 # 随后这行就跑全市场

# 5) 长历史全量重拉 / 更慢更礼貌 / 更猛
python scripts/sync_daily_history.py --full --lookback 800
python scripts/sync_daily_history.py --workers 8 --gap-sleep 0.5
```

落地结构（每支一个文件，文件名即代码；`data/` 已在 `.gitignore` 里）：

```
data/
├── universe.csv      # 代码表（--scan 生成，或自备）
├── state.json        # 断点：上次拉到哪、哪些失败、哪些被口径自检拦下
└── day/
    ├── sh600519.parquet
    ├── sz000001.parquet
    └── sh000001.parquet   # 指数也落同一层，靠 sh000* / sz399* 前缀区分
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--root` | `<仓库>/data` | 落盘根目录（锚仓库根，不依赖当前工作目录） |
| `--symbols` | – | 显式指定代码，跳过宇宙解析 |
| `--universe-file` | – | 自备清单 CSV，一行一个代码（可带 `symbol` 列名） |
| `--vipdoc` | 自动探测 | 本地通达信目录，从 `vipdoc/<mkt>/lday/*.day` 取代码表 |
| `--index` / `--no-index` | 自动 | 强制/禁止走指数位（默认按 `sh000*` / `sz399*` 自动判定） |
| `--full` | 关 | 全量重拉（默认按末日 + 30 根增量续拉） |
| `--lookback` | `320` | 每只最少拉多少根日线 |
| `--limit` | `0`（全部） | 只同步前 N 只，试水用 |
| `--workers` | `4` | 并发只数；实际瓶颈是连接池 `slots_per_host` |
| `--gap-sleep` | `0.2` | 每只之间的礼貌间隔秒数 |
| `--timeout` | `10.0` | 单请求超时（秒） |
| `--dry-run` | 关 | 只拉取不落盘 |
| `--scan` | 关 | 只探测全市场代码表并写入 `<root>/universe.csv`，不落行情 |
| `--scan-segment` | 沪深 12 段 | `--scan` 枚举的代码段（每段的 000–999 逐个探） |
| `--scan-bj` | 关 | `--scan` 时一并探测北交所段 |
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

**2. 指数位靠代码段自动判定。** `0x052D` 的 index 位弄反，主站回的是一堆荒唐日期：
实测 `sh000001` 不走 index 位得到 `5616-57-83`、`sh000300` 得到 `0865-09-14`。
所以 `sh000*` / `sz399*` 自动走 `bars(index=True)`，其余（含 `sh900*` / `sz200*` B 股）
走个股路径；`--index` / `--no-index` 可整体覆盖。

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
这个差别就是判据：`--scan` 枚举代码段（sh600/601/603/605/688/689、sz000/001/002/003/300/301
各 1000 个），逐个 `bars(count=2)`，非空即存在，结果写 `data/universe.csv`。
实测单个 1000 段探测约 25 秒、命中 747 只，全段 12 个候选代码约五分钟。
不存在的代码留着会在下一次合并里把整个文件带歪，所以一律不收。

**8. 口径自检放在合并之后。** OHLC 自洽（high<low、high/low 包裹 open/close）、
volume 单位是股、日期升序去重，任一条破就把标记进 `state.json` 的 `rejected` 且**不落盘**。
自检必须在「合并 + 排好序之后」做：`--full` 拉回来的是倒序（最新在前），
在原始批次上校验只会撞出假的「日期非升序」。
日期键只认 `YYYY-MM-DD`，取不到的行直接丢（坏代码 `sh999999` 会回 `8414-91-57` 这种荒唐日期）。

## 调度

```cron
# crontab -e —— 工作日 18:30（收盘后，错峰避开开盘前 30 分钟）
30 18 * * 1-5 cd /path/to/atst && python scripts/sync_daily_history.py >> data/sync.log 2>&1
```

Windows 计划任务同理，指向仓库里的 `scripts/sync_daily_history.py`。
跑完看一眼 `data/state.json` 的 `last_run` / `bars` 就知道有没有真的成。

## 常见坑

| 现象 | 原因 |
|---|---|
| 指数拉回来 `high < low`、日期是 `0080-26-13` | index 位没置上，加 `--index` 或换 `sh000*` / `sz399*` 写法 |
| 报「找不到 universe」 | `0x044D` 已 offline；给 `--symbols` / `--universe-file` / `--vipdoc`，或先 `--scan` |
| 每轮行数一直涨 | 没合并（用 `--dry-run` 或自己改过 `_dedup_merge`） |
| 全市场 SKIP | 主站不可达，先 `python -m atst hosts list` 看真实主站清单 |
| 一只被 REJECT | 口径自检拦下了，看输出里的原因；文件**没被改写**，上一版仍在 |
| `--scan` 之后零参数跑变慢 | 正常：`universe.csv` 生效，跑的是全市场；加 `--limit N` 先试水 |

## 边界

- 只同步**日线**。分钟线可换 `period="min"`（`--lookback` 语义要跟着调），但 `0x052D`
  分页上限是 `0xFFFF` 且 `start + count > 0x10000` 会直接抛 `ParseError`，长历史要自己切 windows。
- 不负责复权。`adjusted-bars` 是另一个 capability，需要复权序列得自己接。
- `--scan` 用的是"主站认不认这个代码"的判据，会得到主站口径的 universe（约 5000 只，
  含少量已退市但主站仍留历史的代码）；要精确名单请自备 `--universe-file`。
- 落盘是**追加合并**语义：只保留去重后的 `YYYY-MM-DD`，同一天重复覆盖。停牌日不会补空行。
- 脚本直接运行时会把仓库根加进 `sys.path`，所以不要求先 `pip install -e .`。
