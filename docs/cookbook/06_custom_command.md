# 食谱 06：自定义协议命令（三级分派 + Spec + Codegen）

目标：85 命令账本之外的命令也能用 —— 从原始字节到正式解析器的完整路径。

## 第一步：L3 透传先拿原始字节

未知命令默认走三级分派兜底，L2 启发式失败时 L3 返回原始 bytes：

```python
from tstdx.protocol.registry import dispatch

result = dispatch(0x1234, raw_payload)
print(type(result))  # L3: bytes / L2: 启发式结构 / L1: 解析后对象
```

## 第二步：用 Prober 自动探测 + 生成 spec 草稿

```python
from tstdx.protocol.prober import Prober

prober = Prober(rate_limit=1.0)  # 限速 1 req/s
prober.assert_offline_hours()  # 仅非交易时段执行（硬约束）

result = prober.probe_command(0x1234, market=0, code="000001")
print(result.frame_size, result.plausible_record_size, result.entropy)
print(f"spec 草稿: {result.draft_path}")  # PROTOCOL_SPEC/UNKNOWN/0x1234_DRAFT.yaml
```

> ⚠️ 只在非交易时段探测（9:15-11:30 / 13:00-15:00 被阻断），避免对生产主站施压。

## 第三步：把草稿完善成正式 spec

编辑 `PROTOCOL_SPEC/<family>/0x1234_<name>.yaml`，按 SCHEMA.md 填写：

```yaml
spec_id: "0x1234"
name: "my_command"
family: "7709"
status: "candidate"        # draft → candidate → stable
request:
  fields: [...]
response:
  record_size: 32
  fields:
    - {name: date, type: uint16, format: yyyymmdd}
    - {name: price, type: tdx_float}
```

## 第四步：codegen 生成解析器骨架

```bash
python -m tstdx.tools.codegen PROTOCOL_SPEC/7709/0x1234_my_command.yaml --write
```

生成的 `parse()` 骨架（写到 `tstdx/tools/generated_draft/`）补充业务逻辑后，
在你的项目里注册即可（解析器不必进 tstdx 包）：

```python
from tstdx.protocol.registry import register_parser


class MyCommandParser:  # 由 codegen 骨架补全而来
    family, cmd, tier = 0x1234, ..., "L1"

    def parse(self, reader, ctx): ...


register_parser(0x1234, MyCommandParser)
```

## 第五步：spec_audit 验证契约

```bash
python -m tstdx.tools.spec_audit          # spec ↔ 实现双向校验
pytest tests/test_spec_coverage.py -v     # CI 门禁
```

## 被动嗅探（替代主动探测）

不想发探测请求？挂在现有流量上被动收集：

```python
from tstdx.transport.sniff import Sniffer

sniffer = Sniffer()
sniffer.observe_response(0x1234, payload)  # 每次响应喂进去
sniffer.export_drafts()  # 未知命令自动出草稿
```

## 纪律

- status=stable 的 spec 必须有 golden 样本（`tests/golden/`）
- `IntegrityViolation`（E3042）绝不允许降级解析 —— 那是在保护你
- spec 变更走 PR + 双人复核（评审流程见 PROTOCOL_SPEC/README.md）
