# 食谱 01：批量拉取全市场 K 线

目标：把沪深 A 股日线拉到本地，供研究/回测使用。

## 要点

1. **代码表不能从 7709 拿**：`0x044D SECURITY_LIST` 与 `0x0450`（旧版）在多主站实测无响应，
   登记为 `STATUS_OFFLINE`，首页请求即抛 `CommandOffline`（`security_list` /
   `export_security_list` 同一条路）。可用的来源见下一步。
2. 控制并发与限速，避免被封 IP
3. 失败按 `RetryAdvice` 决定重试还是换主站
4. 落地 Parquet 增量更新

## 代码表从哪来

| 来源 | 拿到什么 | 何时用 |
|---|---|---|
| 本地通达信目录 `vipdoc/sh/lday/*.day` | 带前缀的代码（文件名即答案） | 已装过通达信、要"这只票有本地数据"的交集 |
| Web Provider 的列表能力（如热点榜/检索） | `code` + `name` | 只要一个当天的可交易清单，容忍非全市场 |
| 自备清单（CSV / 数据库） | 你关心的那一束 | 回测宇宙本来就自定义 |

```python
from pathlib import Path

TDX_HOME = Path("C:/new_tdx")


def symbols_from_vipdoc(market: str = "sh") -> list[str]:
    """文件名即 ``sh600036.day`` 这种带前缀的代码——协议拿不到列表时这是最省事的一条路。"""
    folder = TDX_HOME / "vipdoc" / market / "lday"
    return sorted(path.stem for path in folder.glob(f"{market}*.day"))
```

## 完整示例

```python
import time
from pathlib import Path

from tstdx.client import TdxClient
from tstdx.errors import TdxError, advice_for
from tstdx.output import write

OUT = Path("data/kline/day")
OUT.mkdir(parents=True, exist_ok=True)

# slots_per_host 才是连接池的并发旋钮（每台主站几条连接）；没有 pool_size 这个东西。
client = TdxClient(timeout=10, slots_per_host=4)


def fetch(symbols: list[str]) -> int:
    ok = 0
    for symbol in symbols:  # "sh600519" / "sz000001"；"0"/"1"/"2" 数字前缀同样接受
        try:
            bars = client.bars(symbol, period="day", count=250)
        except TdxError as exc:
            advice = advice_for(exc)
            if not advice.retryable:
                print(f"跳过 {symbol}: {exc}")
                continue
            time.sleep(advice.backoff or 0.5)
            continue
        if bars:
            write(bars, str(OUT / f"{symbol}.parquet"))  # .parquet 后缀即选定 sink
            ok += 1
        time.sleep(0.2)  # 礼貌限速
    return ok


if __name__ == "__main__":
    total = fetch(symbols_from_vipdoc("sh") + symbols_from_vipdoc("sz"))
    print(f"完成 {total} 只")
```

`client.bars(...)` 默认 `as_format="dict"`，每根 K 线是一个 dict，键固定为
`datetime / open / high / low / close / volume / amount`（要对象或 DataFrame 用
`as_format="tuple"` / `"dataframe"`）。

## 增量更新

`start` 是分页偏移而不是日期索引（`0x052D` 的 16-bit 地址空间，`start` 越大取到的 K 线越旧），
"上次拉到哪一天"换不成一个 `start` 值。真正能续拉的做法是每次把 `count` 往大了要、落库时按
`datetime` 去重合并——翻页是客户端做的，调用方只管一次说清楚要多少根：

```python
bars = client.bars(symbol, period="day", count=1000)  # > 800：内部按 800 一页发两次请求
history = {b["datetime"]: b for b in previous_batch}  # 上一批：你自己从 parquet 读回来的行
history.update({b["datetime"]: b for b in bars})
write(sorted(history.values(), key=lambda b: b["datetime"]), str(OUT / f"{symbol}.parquet"))
```

单次的天花板是协议给的：`count` 与 `start` 各自 ≤ `0xFFFF`，且 `start + count > 0x10000` 直接抛
`ParseError`。真要把十年日线一次拿完，就按 `start` 分段自己调多次。

## 陷阱

- **不要**全速并发拉取 —— 主站会封 IP；`slots_per_host=4` + 0.2s 间隔是经验安全值
- 高峰期（9:30 开盘后 30 分钟）响应变慢，错峰执行
- 不可重试的错误（如 `IntegrityViolation`）`advice_for(exc).retryable` 为 `False`，重试无意义，直接跳过
- `count` 超过单页（800 条，服务端上限）时客户端会自动翻页；但一次给多少仍受 16-bit
  分页地址空间约束，别指望一个 `count` 拿到整只股票的全部历史
