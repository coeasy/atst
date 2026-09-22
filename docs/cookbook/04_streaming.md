# 食谱 04：流式订阅与断线恢复

目标：订阅自选股准实时推送；断线自动重连、重复去重、缺口检测。

> **口径先说清**：本包的"流式"是**客户端轮询**（poll 循环），不是服务端推送——7709 没有
> push 通道。所以"不丢数据"不是这一层能承诺的东西：队列满时它**丢最旧**，发现缺口时它
> **只报告不回填**。要回填得自己拉历史（见下）。

## 要点

1. 面向用户的入口是 `Client.stream(...)`，返回 `StatefulQuoteStream`
2. 底层组件 `StreamEngine(poll, symbols, *, interval, diff_only, max_queue, reconnect)`
   自己接一个 `poll(symbols) -> Sequence[Mapping]`
3. 事件统一是 `StreamEvent(kind, key, payload, ts)`，`kind` 取
   `quote` / `diff` / `reconnect` / `gap` / `error`
4. 背压：`BackpressureQueue` 溢出丢**最旧**并回调 `on_drop`；`tstdx.streaming.base` 的
   `QuoteStream` 路径溢出的那一次会抛 `BackpressureOverflow`（可恢复）

## 完整示例（推荐入口）

```python
import time

from tstdx import Client

WATCHLIST = ["sh600519", "sz000001"]

with Client() as client:
    stream = client.stream(
        WATCHLIST,
        provider="tdx",
        interval=1.0,
        on_quote=lambda code, quote: print(code, quote["price"]),
        on_error=lambda exc: print("[stream]", exc),
    )
    stream.start()
    time.sleep(10)
    stream.stop()
    print(stream.state)
```

`on_quote` 收两个参数（代码、行情的 dict 行）。`stream.subscribe(symbol, interval=, on_quote=)`
可以在已启动的流上追加密钥，`unsubscribe(symbol)` 摘除。

## 直接用组件（自己给 poll）

```python
import time

from tstdx import Client
from tstdx.streaming import ReconnectPolicy, StreamEngine

WATCHLIST = ["sh600519", "sz000001"]
client = Client()


def poll(symbols):
    result = client.quotes(symbols)
    return result.data  # Sequence[Mapping]


engine = StreamEngine(
    poll,
    WATCHLIST,
    interval=1.0,
    max_queue=10_000,
    reconnect=ReconnectPolicy(base=1.0, cap=30.0, max_attempts=10),
)
engine.subscribe(lambda ev: print(ev.kind, ev.key, ev.payload))
engine.start()
try:
    time.sleep(10)
finally:
    engine.stop()
```

`StreamEngine` 也是上下文管理器（`with StreamEngine(poll, WATCHLIST) as eng: ...`）。

## 断线恢复时间线

```
正常轮询 ──► 拉取失败
             ├─ ReconnectPolicy: base 起指数退避，封顶 cap，jitter 打散同批重连
             ├─ 每一轮失败都下发一条 error 事件（订阅方自己决定何时降级/告警）
             └─ 恢复后 DeltaMerger 去重：同一根 K 线 / 同一条行情不会重复下发
正常轮询 ◄──┘
```

> `ReconnectPolicy.max_attempts` 只做计数（`should_give_up()`），**引擎本身不据此停手**；
> 想设"重试 N 次就退出"的策略，请在 `error` 事件里自己数。

## 缺口：检测是真的，回填要自己动手

`GapFiller` 只对**单调键**（自增 `seq`、或按周期步进的 `datetime`）判断连续性：

```python
from tstdx.streaming import GapFiller

gf = GapFiller()
gf.observe("sh600519", 41)
continuous = gf.observe("sh600519", 43)  # False：42 没见到
print(gf.gaps("sh600519"))  # [(41, 43)]
```

拿到缺口后自己去补：`client.bars("sh600519", period="1m", count=...)`，
再把补到的行与流上的行合并去重（`DeltaMerger` 就是干这个的）。
非数值键（例如日期字符串）`GapFiller` 无法做 `+1` 比较，会退化成"总是连续"，
缺口判断得由上层按周期步长自己算。

## 背压

队列容量是 `max_queue`。消费跟不上时不会无限涨内存：`BackpressureQueue` 丢弃**最旧**
的那一条并触发 `on_drop` 回调（行情语义下"最新优先"）。要感知丢了多少，就接 `on_drop`
计数；要的是"绝不丢"，这一层给不了——请改成落盘（见食谱 05）后离线补齐。
