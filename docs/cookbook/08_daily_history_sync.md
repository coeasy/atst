# 08：用 TDX 接口每天全量同步历史日线到本地目录

每日盘后把 A 股日线同步一份到本地 parquet 目录，脚本在 `scripts/sync_daily_history.py`。
直接跑，不需要写任何代码：

```bash
python scripts/sync_daily_history.py --root data/kline/day
```

脚本会自己找宇宙（本地通达信 `vipdoc` → 自备清单 → 命令行 `--symbols`），并发拉日线，
按日期键去重合并后原子改名落盘，并把「上次拉到哪」写进 `state.json`。

## 快速开始

```bash
# 1) 先验证连通性：只拉不落盘
python scripts/sync_daily_history.py --root data/kline/day --dry-run

# 2) 指定几只试试（代码可带/不带 sh、sz 前缀）
python scripts/sync_daily_history.py --root data/kline/day --symbols 600519 sz000001

# 3) 日常调度：全市场增量续拉
python scripts/sync_daily_history.py --root data/kline/day

# 4) 指数（sh000001 / sz399001）必须走 --index，否则主站回的是假 OHLC
python scripts/sync_daily_history.py --root data/kline/day --index

# 5) 换历史窗口 / 更慢更礼貌 / 更猛
python scripts/sync_daily_history.py --root data/kline/day --lookback 800 --gap-sleep 0.5
python scripts/sync_daily_history.py --root data/kline/day --workers 8
```

落地结构（每支一个文件，文件名即代码）：

```
data/kline/day/
├── sh600519.parquet
├── sz000001.parquet
└── state.json          # 上次跑完的断点证据
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--root` | `data/kline/day` | 落盘根目录，`state.json` 默认也放这里 |
| `--symbols` | – | 显式指定代码，跳过宇宙解析 |
| `--universe-file` | – | 自备清单 CSV，一行一个代码（可带 `symbol` 列名） |
| `--vipdoc` | 自动探测 | 本地通达信目录，从 `vipdoc/<mkt>/lday/*.day` 取代码表 |
| `--full` | 关 | 全量重拉（默认按 `state.json` 的日期增量续拉） |
| `--lookback` | `320` | 每只最少拉多少根日线 |
| `--index` | 关 | 把清单里的代码当**指数**拉（`0x052D` 的 index 位） |
| `--workers` | `4` | 并发只数；实际瓶颈是连接池 `slots_per_host` |
| `--gap-sleep` | `0.2` | 每只之间的礼貌间隔秒数 |
| `--timeout` | `10.0` | 单请求超时（秒） |
| `--dry-run` | 关 | 只拉取不落盘 |
| `--state` | `<root>/state.json` | 自定义状态文件路径 |

退出码：`0` 成功（至少一只落盘）；`2` 宇宙缺失（找不到代码表）或全市场一只都没成
（视为环境级：主站不可达，不是脚本缺陷）。

## 为什么是这套设计

这几条都是踩出来的，不是装饰。

**1. 续拉不能按日期游标。** `0x052D` 的 `start` 是 16-bit **分页偏移**，不是日期。
"上次那根到哪了"只能靠 `state.json` 里记的末日 + 多要 `RESUME_OVERLAP`（30 根）回来合并。
每轮拉回 `lookback` 根，和磁盘上已有的按日期键去重合并，长度才会收敛到稳定值（320→350→350），
否则每轮都把文件重写成 `lookback` 根，历史窗口随轮数往上漂。

**2. 落地必须原子。** 先写同目录 `.tmp` 再 `os.replace`。写一半崩了不会留半个坏 parquet。
注意原子改名的临时文件后缀不是 `.parquet`，`atst.output` 的 `write()` 按后缀推断
恰好在这里会抛 `ValueError`（审计 §2-15 已经把那个静默语义拿掉了），所以脚本里显式传
`fmt="parquet"`。

**3. 限速在连接池那一层。** 并发旋钮是 `TdxClient(slots_per_host=…)`（每台主站几条连接），
脚本再叠一道 `--gap-sleep` 加随机 jitter。不可重试的错误（`Advice.retryable` 为假）直接
SKIP，不白等。

**4. 代码表不能从 tdx 拿。** `0x044D SECURITY_LIST` 已登记 offline，一调就抛 `CommandOffline`。
宇宙只能来自本地 `vipdoc`、自备清单或 `--symbols`——三者都没有时脚本**明确报错退出 2**，
不会静默拉个零回来。

**5. 口径自检放在合并之后。** OHLC 自洽（high<low、high/low 包裹 open/close）、
volume 单位是股、日期升序去重，任一条破就把标记号进 `rejected` 且**不落盘**。
自检必须在「合并 + 排好序之后」做：`--full` 拉回来的是倒序（最新在前），
在原始批次上校验只会撞出假的「日期非升序」。
日期键只认 `YYYY-MM-DD`，取不到的行直接丢——坏代码（实测 `sh999999` 会回 `8414-91-57`
这种荒唐日期）留着会在下一次合并里把整个文件带歪。

## 调度

```cron
# crontab -e —— 工作日 18:30（收盘后，错峰避开开盘前 30 分钟）
30 18 * * 1-5 cd /path/to/atst && python scripts/sync_daily_history.py --root data/kline/day
```

Windows 计划任务同理，指向仓库里的 `scripts/sync_daily_history.py`。
跑完看一眼 `state.json` 的 `last_run` / `bars` 就知道有没有真的成。

## 常见坑

| 现象 | 原因 |
|---|---|
| 指数拉回来 `high < low`、日期是 `0080-26-13` | 没加 `--index`，`0x052D` 的 index 位没置上 |
| 报「找不到 universe」 | `0x044D` 已 offline；给 `--symbols` / `--universe-file` / `--vipdoc` |
| 每轮行数一直涨 | 没合并（用 `--dry-run` 或自己改过 `_dedup_merge`） |
| 全市场 SKIP | 主站不可达，先 `python -m atst hosts scan` 看真实 RTT 排主站 |
| 一只被 REJECT | 口径自检拦下了，看输出里的原因；文件**没被改写**，上一版仍在 |

## 边界

- 只同步**日线**。分钟线可换 `period="min"`（ `--lookback` 语义要跟着调），但 `0x052D`
  分页上限是 `0xFFFF` 且 `start + count > 0x10000` 会直接抛 `ParseError`，长历史要自己切 windows。
- 不负责复权。`adjusted-bars` 是另一个 capability，需要复权序列得自己接。
- 落盘是**追加合并**语义：只保留去重后的 `YYYY-MM-DD`，同一天重复覆盖。停牌日不会补空行。
- 脚本直接运行时会把仓库根加进 `sys.path`，所以不要求先 `pip install -e .`。
