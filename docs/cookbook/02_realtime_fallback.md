# 食谱 02：实时行情监控 + 多源降级

目标：盘中持续监控自选股，主站挂了自动切 Web 源，不中断。

## 要点

1. `DataSourceRouter` 五级降级：tdx → web → reader → cache → synthetic
2. 周期轮询 + 异常隔离
3. 阈值告警

## 完整示例

```python
import time

from tstdx.sources.router import DataSourceRouter
from tstdx.errors import TdxError

WATCHLIST = ["sh600519", "sz000001", "sz300750"]
ALERT_PCT = 5.0

router = DataSourceRouter()  # 默认 tdx 优先，失败自动降级


def poll_once() -> None:
    try:
        quotes = router.quotes(WATCHLIST)
    except TdxError as e:
        print(f"[WARN] 所有源失败: {e}，60s 后重试")
        return
    for q in quotes:
        flag = "⚠️" if abs(q.change_pct) >= ALERT_PCT else "  "
        src = getattr(q, "source", "tdx")
        print(f"{flag} {q.code} {q.price:.2f} {q.change_pct:+.2f}% (via {src})")


if __name__ == "__main__":
    while True:
        poll_once()
        time.sleep(3)
```

## 关键说明

- **怎么知道走了哪个源**：Quote 的 `source` 属性记录实际来源；也可订阅路由的降级事件
- **主站恢复后自动回切**：路由按优先级每次重新尝试，tdx 恢复后自动回到主站
- **Web 源全挂**：抛 `AllSourcesExhausted`（E7040），建议 30s 后重试

## 每源限流

Web 源自带 RateLimiter（见 `tstdx/web/base.py`），被反爬拦截（E7010）会自动换下一个源。自定义频率：

```python
router = DataSourceRouter(web_rate_limit=2.0)  # 每源 2 req/s
```

## 只信 Web 源（禁用主站）

```python
router = DataSourceRouter(enabled=["web"])  # 或 prefer="web"
```
