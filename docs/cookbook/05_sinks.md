# 食谱 05：数据落地三件套（DataFrame / Parquet / DuckDB）

目标：把拉到的数据用合适的格式落地。

## write() 自动分发

```python
from atst.output import write

bars = client.bars("sh600519", period="day", count=500)

# DataFrame（内存对象，需 pandas）—— sink 无法从 dest 推断，显式传 fmt
from atst.output import to_dataframe

df = to_dataframe(bars)        # 等价于 write(bars, "", fmt="dataframe")

# Parquet 文件（需 pyarrow，列式压缩，适合静态研究数据）
write(bars, "data/kline_600519.parquet")

# DuckDB 表（需 duckdb，可直接 SQL 分析）
write(bars, "duckdb:data/market.db@kline_600519")

# CSV 文件（零依赖，标准库 csv）
write(bars, "data/kline_600519.csv")
```

推断不出来就报错，不静默返回：`write(bars, "out.txt")` 抛 `ValueError`（此前它静默回一个
DataFrame 且什么都不落盘，那条语义已删）。内存里的 DataFrame 要显式写
`write(bars, "", fmt="dataframe")`。

## 选型建议

| 格式 | 适用 | 优势 | 注意 |
|---|---|---|---|
| DataFrame | 交互分析、画图 | 即取即用 | 内存受限，别一次全市场 |
| Parquet | 长期存储、增量更新 | 压缩比高、列裁剪快 | 不可追加，重写整文件 |
| CSV | 给别的工具看 | 零依赖 | 无类型，日期靠字符串 |
| DuckDB | SQL 分析、大文件 | 免服务、能跑 SQL | 单写者；并发读写注意 |

## DuckDB 直接 SQL

写进去的列名就是 K 线行的键（`datetime / open / high / low / close / volume / amount`）：

```python
import duckdb

con = duckdb.connect("data/market.db")
con.execute("""
    SELECT "datetime", close,
           LAG(close) OVER (ORDER BY "datetime") AS prev_close,
           close / LAG(close) OVER (ORDER BY "datetime") - 1 AS ret
    FROM kline_600519
    ORDER BY "datetime" DESC LIMIT 10
""").fetchall()
```

## Sink：`write()` 的对象化写法

`atst.output.Sink` 不是可继承的策略基类，没有 `scheme` 这类子类协议，也没有注册表可以往
`write()` 里插新格式。它就是 `write()` 的对象化写法，且**只收三种 fmt**：

```python
from atst.output import Sink

Sink("dataframe").write(bars)                       # 内存对象
Sink("parquet", path="out.parquet").write(bars)      # 落盘
Sink("duckdb", table="kline", database="mkt.duckdb").write(bars)
```

`path` 对 parquet 必填、`table` 对 duckdb 必填，缺了直接 `ValueError`；`columns=` 统一裁剪列。
CSV 目前只有 `write()` 那一条路（`Sink("csv")` 会落到"未知 sink 格式"）。要加第五种落地格式，
就是往这两个函数里加分支，不是子类化。

## 缺依赖的报错

未安装对应 extra 时抛 `DependencyMissingError`（E1020, http 503），
提示安装命令，例如 `pip install "atst[parquet]"`。CSV 走标准库，不需要 extra。
