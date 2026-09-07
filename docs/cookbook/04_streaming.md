# 食谱 04：流式订阅与断线恢复

目标：订阅自选股准实时推送，断网自动重连，缺口自动补数，不丢数据。

## 要点

1. `StreamEngine` = ReconnectPolicy + DeltaMerger + GapFiller + BackpressureQueue
2. 断网 30s 内重连不丢增量（缺口由 GapFiller 用历史 K 线回填）
3. 队列满时背压（`BackpressureOverflow` 可恢复）

## 完整示例

```python
import time

from tstdx.streaming.engine import StreamEngine, ReconnectPolicy

WATCHLIST = ["sh600519", "sz000001"]

engine = StreamEngine(
    symbols=WATCHLIST,
    reconnect=ReconnectPolicy(max_retries=10, backoff_base=1.0, backoff_max=30.0),
    queue_size=10_000,
)


def on_quote(quote) -> None:
    print(f"{quote.code} {quote.price:.2f} {quote.change_pct:+.2f}%")


def on_gap(filled_bars) -> None:
    """断线期间缺口已由 GapFiller 自动回填。"""
    print(f"回填 {len(filled_bars)} 根缺失 K 线")


engine.on_quote = on_quote
engine.on_gap_filled = on_gap

if __name__ == "__main__":
    engine.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        engine.stop()
```

## 断线恢复时间线

```
正常推送 ──► 断网（连接断开）
             ├─ ReconnectPolicy: 1s, 2s, 4s... 指数退避重连
             ├─ 重连成功后: GapFiller 拉取断线期间 K 线
             └─ DeltaMerger: 去重合并，保证每根 K 线只出现一次
正常推送 ◄──┘
```

## 30 秒断网 0 丢失验证

测试脚本（模拟断网用 fake transport）：

```python
from tstdx.streaming.engine import StreamEngine

engine = StreamEngine(symbols=["sh600519"])
engine.start()
# ... 断开底层 transport 30 秒 ...
# engine.gap_report() 应显示缺口已回填、无丢失
report = engine.gap_report()
assert report["lost"] == 0
```

## 背压

消费速度跟不上时队列堆积，超过 `queue_size` 抛 `BackpressureOverflow`（advice: retryable, backoff=0.1s）。
处理方式：增大队列 / 提高消费并发 / 降采样（只收 tick 不收逐笔）。
