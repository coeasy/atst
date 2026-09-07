# 食谱 01：批量拉取全市场 K 线

目标：把沪深 A 股全市场日线拉到本地，供研究/回测使用。

## 要点

1. 先拿证券数量与列表（`0x044E` + 股票列表命令）
2. 控制并发与限流，避免被封 IP
3. 失败自动换主站（`RetryAdvice.switch_host`）
4. 落地 Parquet 增量更新

## 完整示例

```python
import time
from pathlib import Path

from tstdx import TdxClient
from tstdx.errors import TdxError, advice_for
from tstdx.output import write

OUT = Path("data/kline/day")
OUT.mkdir(parents=True, exist_ok=True)

client = TdxClient(pool_size=4, timeout=10)


def fetch_market(market: int) -> int:
    count = client.security_count(market=market)
    ok = 0
    for i in range(0, count, 100):  # 每批 100 只
        batch = client.security_list(market=market, start=i, count=100)
        for sec in batch:
            code = sec.code
            try:
                bars = client.bars(f"{market}{code}", period="day", count=250)
            except TdxError as e:
                advice = advice_for(e)
                if not advice.retryable:
                    print(f"跳过 {code}: {e}")
                    continue
                time.sleep(advice.backoff or 0.5)
                continue
            if bars:
                prefix = "sh" if market == 1 else "sz"
                write(bars, f"parquet://{OUT / (prefix + code)}.parquet")
                ok += 1
        time.sleep(0.2)  # 礼貌限速
    return ok


if __name__ == "__main__":
    total = fetch_market(1) + fetch_market(0)
    print(f"完成 {total} 只")
```

## 增量更新

记录每只股票最后日期，下次从 `start` 参数续拉：

```python
bars = client.bars(symbol, period="day", count=800, start=last_date_index)
```

## 陷阱

- **不要**全速并发拉取 —— 主站会封 IP；`pool_size=4` + 0.2s 间隔是经验安全值
- 高峰期（9:30 开盘后 30 分钟）响应变慢，错峰执行
- `TdxError.advice.retryable=False` 时（如 `IntegrityViolation`）重试无意义，直接跳过
