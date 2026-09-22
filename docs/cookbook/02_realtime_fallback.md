# 食谱 02：实时行情监控 + 显式跨源容错

目标：盘中持续监控自选股；主站不可用时按**你指定的**顺序改走 Web 源，
且每次换源都可审计。

> 历史背景：本页曾描述 `DataSourceRouter` 的 tdx → web → reader → cache →
> synthetic 五级自动降级。该聚合路由与全部缓存层已随 v16 Phase 2 物理删除。
> 现在**默认永不换源**；跨源只能由调用方显式给出 `FallbackPolicy`。

## 要点

1. `Client.quotes()` 单发：绑定恰好一个 Provider，失败就抛，不会偷偷换源
2. 需要容错时用 `FallbackPolicy(providers=(...))` → `OrchestratedResult.attempts`
3. 周期轮询 + 异常隔离 + 阈值告警

## 完整示例

```python
import time
from typing import Any

from tstdx import Client, FallbackPolicy
from tstdx.errors import TdxError

WATCHLIST = ["sh600519", "sz000001", "sz300750"]
ALERT_PCT = 5.0

# 有序显式策略：先主站，再腾讯 Web 源。不写这个参数就永远不会换源。
POLICY = FallbackPolicy(providers=("tdx", "tencent"))

client = Client()


def _field(row: Any, name: str, default: Any = 0.0) -> Any:
    """tdx 回 dict、Web 源回 Quote 对象：两种形状读同一个字段。"""
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def poll_once() -> None:
    try:
        out = client.quotes(WATCHLIST, policy=POLICY)
    except TdxError as e:
        print(f"[WARN] {e}（{e.code}），60s 后重试")
        return
    used = out.result.meta.provider
    tried = [(a.provider, a.status, a.code) for a in out.attempts]
    for q in out.result.data:
        price = _field(q, "price")
        last = _field(q, "last_close")
        pct = (price - last) / last * 100 if last else 0.0
        flag = "⚠️" if abs(pct) >= ALERT_PCT else "  "
        print(f"{flag} {_field(q, 'code')} {price:.2f} {pct:+.2f}% (via {used})")
    print("   attempts:", tried)


if __name__ == "__main__":
    while True:
        poll_once()
        time.sleep(3)
```

`_field()` 那层不是装饰：`quotes` 的行形状按源不同。dict 行的键固定为
`code / datetime / price / last_close / open / high / low / volume / amount / bid / ask`；
`change`、`pct_change`、`turnover_rate` 只在 `Quote` 对象（`tstdx.domain.models.Quote`）上是
派生属性，dict 里没有 —— 涨幅要么自己按 `price` 与 `last_close` 算，要么统一转成 `Quote`。
**7709 实时路径上 `datetime` / `bid` / `ask` 恒空**（口径见 `docs/tdx_status.md`），
别拿它们当"没有行情"的判据。

只要不传 `policy=`，同样的循环就是单源版本：

```python
result = client.quotes(WATCHLIST)          # QueryResult，失败即抛 TdxError
print(result.meta.provider, result.meta.channel)
```

## 关键说明

- **怎么知道走了哪个源**：`QueryResult.meta.provider` / `.channel` 是权威答案；
  跨源时 `OrchestratedResult.attempts` 逐源记录 `provider / status / code`
- **主站恢复后自动回切**：策略是**有序**列表，每次调用都从第一个源重新尝试，
  所以 tdx 恢复后自然回到主站——不存在"粘住备用源"的状态
- **全部源都挂**：抛 `AllSourcesExhausted`（E7040），建议 30s 后重试
- **provenance 不一致即抛**：结果来源与被请求 Provider 不符时内核报错，
  绝不返回伪装成主站结果的 Web 数据

## 每源限流

Web Provider 内置令牌桶限流（`tstdx/transport/ratelimit.py`，各源默认频率见
`tstdx/web/base.py`），被反爬拦截（E7010）时该次尝试记为 `failed` 并继续下一个源。
批量轮询建议自行控制节奏：

```python
for chunk in (WATCHLIST[i : i + 50] for i in range(0, len(WATCHLIST), 50)):
    client.quotes_batch(chunk)   # 三态审计，不会因为一只失败而整批丢失
    time.sleep(1.0)
```

## 只信 Web 源（禁用主站）

把策略列表写成你想要的源集合即可，或直接指定单一 Provider：

```python
client.quotes(WATCHLIST, provider="tencent")            # 单源，失败不换
client.quotes(WATCHLIST, policy=FallbackPolicy(providers=("tencent", "sina")))
```
