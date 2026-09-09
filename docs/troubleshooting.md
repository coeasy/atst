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
3. 公司网络可能封锁非常用端口 — 尝试 80/443 端口主站（配置 `tstdx.toml`）

### 症状：`[E2030] ReadTimeout`

握手慢或主站过载。建议：
- `TdxClient(timeout=15)` 增大超时
- 避开 9:30-10:00 高峰时段大批量拉取

### 症状：`[E2040] AllHostsUnreachable`

所有主站不可达。此错误携带 `fallback_to_web=True` 建议：
- 安装 web extra：`pip install "tstdx[web]"`
- 确认 `SourcesRouter` 配置了 HTTP Web 源层

## 2. 协议/解析问题

### 症状：`[E3030] UnknownCommand`

命令不在 85 命令账本。分派器已用 L2/L3 兜底：
- 检查命令号拼写（十六进制大小写无关）
- 用 `Prober` 探测该命令的真实响应结构

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
