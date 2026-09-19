# 故障排查指南

## 诊断流程图

```
连接失败？
├─ 是 → 1. tstdx server-test 测速
│        2. 检查防火墙/端口 7709
│        3. 查看 AllHostsUnreachable → 降级配置
└─ 否 → 数据异常？
         ├─ 是 → 1. 检查错误码（E3xxx=协议 / E4xxx=数据 / E7xxx=Web源）
         │        2. 查看 advice 字段的建议动作
         │        3. 对比 golden 样本
         └─ 否 → 性能问题 → 见 §4
```

## 1. 连接问题

### 症状：`[E2010] ConnectionFailed`

```python
from tstdx.errors import ConnectionFailed
```

**排查**：
1. `tstdx server-test` — 确认至少一个主站可达
2. 手动 telnet 测试：`python -c "import socket; s=socket.create_connection(('119.147.212.81', 7709), timeout=5); print('OK')"`
3. 公司网络可能封锁非常用端口 — 显式传入 80/443 端口主站：
   `Client(hosts=["119.147.212.81:443"])`，或写进项目配置 `./tstdx.toml`
   （`[hosts]` 段 `servers = [["119.147.212.81", 443]]`，`Client()` 会读到它，
   见 [docs/configuration.md](configuration.md)）。

### 症状：`[E2030] ReadTimeout`

握手慢或主站过载。建议：
- `TdxClient(timeout=15)` 增大超时
- 避开 9:30-10:00 高峰时段大批量拉取

### 症状：`[E2040] AllHostsUnreachable`

所有主站不可达。错误对象上的 `fallback_to_web` 只是**历史兼容字段**，单一内核不消费
它 —— 不会自动改走 Web 源，这是 provider-first 口径而非缺陷。要拿 Web 行情必须显式：

- 同 capability 换 Provider：`Client().quotes("sh600000", provider="tencent")`
  （`tencent/sina/eastmoney/baidu` 都是注册表里的 Provider id）
- 显式跨源编排：`Client().quotes(symbols, policy=FallbackPolicy(...))` —— 这是
  唯一的跨源通道（见 `docs/ARCHITECTURE.md` §2）
- 或独立使用 legacy Web 入口 `tstdx.web.WebQuoteClient`（需 `pip install "tstdx[web]"`）

## 2. 协议/解析问题

### 症状：`[E3035] CommandOffline`（命令已下线）

TDX 主站已停答该命令，85 命令账本把它标成 offline，客户端在发帧前就 fail-fast（不再走超时重试链）。

- 看 `context["name"]` / `context["summary"]` 确认是哪条命令，账本状态在
  `tstdx/protocol/commands.py`，实测背景见 `docs/tdx_status.md`
- 要拿同类数据得由你显式改选：同一 capability 换 Provider
  （`Client().quotes("sh600000", provider="eastmoney")`），或改用替代能力
  （如 `security_list` 停答后用东财目录类接口）
- 内核不会自动替换 Provider，所以也不会替你决定"换谁"——错误自带的事实到此为止

E3030 那一格此前点名的是一个已退役的类：内核不会发出账本外的命令，那个类既无抛点、
也已按 F-68 裁决 (a) 从错误树删除，退役名单与对应 code 只在 `docs/errors.md` §一之二
登记。未知/低置信样本的归档由离线工具链（`tools/capture`、`ProtocolSniffer`）负责，
不是运行期错误。

### 症状：`[E3042] IntegrityViolation`（禁止降级）

**这是保护机制**：请求参数可能写反（如实测 0x0530 的 market 字节反转会得到「帧合法但内容全错」的响应）。
- 检查 market/code 参数顺序
- 不要试图绕过此错误 — 它在阻止脏数据

### 症状：`[E3041] LowConfidenceParse`

L2 启发式置信度过低。显式指定 profile：

```python
from tstdx.reader.profile import DataProfile

bars = client.bars("sh600519", profile=DataProfile(...))
```

## 3. Web 源问题

### 症状：`[E7010] AntiSpiderBlocked`

被反爬拦截：
- 库已自动带 Referer + UA；仍被拦截说明频率过高
- 每个源自带限流；触发后自动切换下一源
- 连续失败达阈值会抛 `SourceDeprecated`（该源可能已改版）

### 症状：`[E7040] AllSourcesExhausted`

7 个 Web 源全部失败：
- 检查本机网络（能否 curl 新浪接口）
- 查看各源最近失败原因：`client.router.last_errors()`

### 数据口径对不上

确认归一化模块在工作：volume 应为股、amount 应为元。腾讯源 ×100（手→股）+ ×10000（万→元），东财 ×100。异常值请提 Issue 附原始响应。

## 4. 性能问题

### 批量拉取慢

- `0x0530` 逐只请求是服务端约束；并发请求数受连接池限制
- `TdxClient(pool_size=8)` 增大连接池
- 大批量历史 K 线优先本地 vipdoc（`reader/formats.py`），比在线快百倍

### 内存占用高

- Streaming 订阅设置 `BackpressureQueue` 上限
- Sink 写入用流式（DuckDB/Parquet），避免一次性 DataFrame

## 5. 开关与日志

```python
import logging

logging.getLogger("tstdx").setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)
```

反馈/遥测默认关闭。误开启检查环境变量 `TSTDX_FEEDBACK`。

## 6. 仍未解决？

1. 收集信息：`tstdx version` + 错误码 + 复现脚本
2. 搜索 [Issues](https://github.com/coeasy/tstdx/issues)
3. 提交 Bug Report（附最小复现代码，**脱敏**后）
