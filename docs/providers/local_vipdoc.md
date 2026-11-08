# local_vipdoc Provider 接口与 Channel 契约

> Provider ID: `local_vipdoc`  
> API compatibility selector: `provider="local_vipdoc"`  
> Role: `local_history`

## 1. 定位

从本机通达信安装目录读取 `vipdoc` 二进制（`.day` / `.lc1` / `.lc5` / `gpcw`）。
**历史文件永远不冒充实时数据**：它不联网、不重试、不跨 Provider 尝试；没有配置
`core.vipdoc_root` 时直接报配置错误。它只被显式 `provider="local_vipdoc"` 选中，
也不是任何重新路由的默认目标。

## 2. Channel

```text
vipdoc
```

- `bars`（`period` = `day` / `1min` / `5min`，`local=True`）

该 Provider 没有 `CHANNEL_ADAPTERS`：`vipdoc` 由执行器的专用绑定
（`DEDICATED_CAPABILITIES` 路径里的本地读盘实现）直接消费，不走 web adapter/session。

## 3. Unified API

```python
md.bars("sh600519", provider="local_vipdoc", period="day")
```

同一 `bars` 能力也可由其他 Provider 提供；选 `local_vipdoc` 意味着只读本地文件，
不会因为缺文件而自动换成在线源。

## 4. Path / Rate

- 根目录来自 `core.vipdoc_root`（`Config.validate()` 要求非空字符串）。
- 目录布局按通达信标准：`vipdoc/sh/lday/sh600519.day`、`vipdoc/sz/fzline/...` 等。
- 全本地文件读取，无网络限流；大目录扫描的 IO 成本由调用方承担。

## 5. Freshness

返回的是**文件里最后一根 bar** 的时间，不是"现在"。`currentness="live"` 对
`local_vipdoc` 没有意义：规划期按 channel 的 `local=True` 口径校验，调用方应自行
比对 `bar.datetime` 与当前时间。

## 6. Errors

- 未配置 `vipdoc_root` → `ConfigError`；
- 文件不存在 / 目录不合法 → `ValidationError`（E1010 家族，规划期即拒绝）；
- 二进制越界、长度不足 → `ParseError` 家族（context 带 `provider=local_vipdoc`、路径、偏移）。

## 7. Provider-specific Model

返回标准 `Bar` / `MinuteBar`；`period` 与文件后缀严格对应，不做跨周期插值。

## 8. Conformance

验证路径解析、周期映射、复权字段（本地文件本身不复权）、时间戳时区、
缺文件时不静默换源、`local=True` 的规划期拒绝。
