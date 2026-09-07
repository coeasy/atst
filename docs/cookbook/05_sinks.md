# 食谱 05：数据落地三件套（DataFrame / Parquet / DuckDB）

目标：把拉到的数据用合适的格式落地。

## write() 自动分发

```python
from tstdx.output import write

bars = client.bars("sh600519", period="day", count=500)

# DataFrame（内存对象，需 pandas）
df = write(bars, "output://dataframe")

# Parquet 文件（需 pyarrow，列式压缩，适合静态研究数据）
write(bars, "parquet://data/kline_600519.parquet")

# DuckDB 表（需 duckdb，可直接 SQL 分析）
write(bars, "duckdb://data/market.db?table=kline_600519")
```

## 选型建议

| 格式 | 适用 | 优势 | 注意 |
|---|---|---|---|
| DataFrame | 交互分析、画图 | 即取即用 | 内存受限，别一次全市场 |
| Parquet | 长期存储、增量更新 | 压缩比高、列裁剪快 | 不可追加，重写整文件 |
| DuckDB | SQL 分析、大文件 | 免服务、能跑 SQL | 单写者；并发读写注意 |

## DuckDB 直接 SQL

```python
import duckdb

con = duckdb.connect("data/market.db")
con.execute("""
    SELECT date, close,
           LAG(close) OVER (ORDER BY date) AS prev_close,
           close / LAG(close) OVER (ORDER BY date) - 1 AS ret
    FROM kline_600519
    ORDER BY date DESC LIMIT 10
""").fetchall()
```

## Sink 策略类（自定义扩展）

```python
from tstdx.output import Sink


class CsvSink(Sink):
    scheme = "csv"

    def write(self, records, target):
        import csv

        with open(target, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=records[0].to_dict().keys())
            w.writeheader()
            for r in records:
                w.writerow(r.to_dict())


# 注册后 write(bars, "csv://out.csv") 自动生效
```

## 缺依赖的报错

未安装对应 extra 时抛 `DependencyMissingError`（E1020, http 503），
提示安装命令，例如 `pip install "tstdx[parquet]"`。
