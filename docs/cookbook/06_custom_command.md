# 食谱 06：自定义协议命令（三级分派 + Spec + Codegen）

目标：85 命令账本之外的命令也能用 —— 从原始字节到正式解析器的完整路径。

## 第一步：先拿到原始字节，看清分派落在哪一级

三级分派的口径是 L1 精确解析器 → L2 通用启发式 → L3 原始透传（`atst.protocol.registry.dispatch`）。
账本里没有的命令既没有 L1 解析器，L2 多半也给不出可信结构，最终落在 L3。要观察这一格，
用语义客户端上那个"任意命令"入口，而不是自己拼帧：

```python
from atst.client import TdxClient

client = TdxClient()
body = b"\x00\x00" + b"000001".ljust(6, b"\x00")  # 请求体自己按猜的结构拼

result = client.request_result(0x1234, body)  # 返回完整 ParseResult
print(result.tier, result.confidence)  # 未登记命令：L3 / 0.0
print(result.raw[:64])  # L3 时唯一有效载荷是原始字节（rows 为空）
print(result.warnings)  # 为什么降级，写在这里
```

`request_result()` 是唯一能把 L2/L3 的中间结论（`tier` / `confidence` / `raw` / `warnings` /
`meta`）交到你手上的入口；`request()` 是它的下游，只回 `result.rows`，落到 L3 时你只会看到
一个空列表。

## 第二步：用 Prober 探测 + 生成 spec 草稿

```python
from atst.client import TdxClient
from atst.protocol.prober import Prober

prober = Prober(TdxClient(timeout=3.0), rate_limit=1.0)  # 限速 1 req/s

if not prober.only_offline_hours():
    print("盘中，不发任何请求")  # 默认构造就是这么处理的，见下
result = prober.probe_command(0x1234, market=0, code="000001")
print(result.frame_size, result.plausible_record_size, result.entropy)
print(result.notes)  # 被盘中守卫拦下时，原因在这里而不是异常里

path = prober.archive(result)  # 草稿要显式归档才落盘
print(f"spec 草稿: {path}")  # PROTOCOL_SPEC/UNKNOWN/1234_DRAFT.yaml
```

> ⚠️ 探测合规：**只在非交易时段发**（9:15-11:30 / 13:00-15:00 阻断）。默认构造
> （`block_offline_only=True, strict=False`）在盘中**不发包**，只回一个带 `notes` 的失败
> `ProbeResult`；`strict=True` 才抛 `TdxError`，`block_offline_only=False` 则完全不设守卫。
> `probe_command()` 对超时/断连/协议错误同样不抛异常，原因一律进 `result.notes`。
> 已存在的 DRAFT 默认**不被覆盖**（`archive(..., overwrite=True)` 才会）。

## 第三步：把草稿完善成正式 spec

编辑 `PROTOCOL_SPEC/<族目录>/0x1234_<name>.yaml`（族目录是 `7709` / `7727` / `MAC` / `F10` /
`GOODS` / `TRADE`，`spec_audit` 按这张表把 `family` 映射回协议族常量），按 `SCHEMA.md` 填写：

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
python -m atst.tools.codegen PROTOCOL_SPEC/7709/0x1234_my_command.yaml --write
```

`--write` 落到 `atst/tools/generated_draft/` 下的草稿（目录由命令自己建，不进版本库）；
省略 spec 位置参则处理 `--spec-dir`（默认 `PROTOCOL_SPEC`）下的全部 spec，`--list` 只列 spec。

补全业务逻辑后注册进分派表。`register_parser()` 是**装饰器工厂**，第一个参数只有命令号，
其余全是关键字；解析器只需继承 `BaseParser` 并实现 `parse_payload`（帧拆解、异常转译、
`ParseResult` 包装都由框架做）：

```python
from atst.protocol.commands import Family
from atst.protocol.registry import BaseParser, register_parser


@register_parser(0x1234, family=Family.STANDARD, name="MY_COMMAND", head=2)
class MyCommandParser(BaseParser):
    """`head` 只是元信息：记录数头（`uint16 count`）由解析器自己读，框架不跳。"""

    def parse_payload(self, reader, **ctx):
        count = self.guarded_count(reader, ctx, 16)
        return [{"raw": reader.read_bytes(count)}]  # 换成真字段布局
```

注册之后，`dispatch()` 与所有语义方法一样看得到 `tier=L1` 的结果；`ctx` 是请求侧上下文
通道（K 线的 `category`、指数的 `index` 都靠它），从 `request(..., ctx={...})` 透传进来。

## 第五步：spec_audit 验证契约

```bash
python -m atst.tools.spec_audit --json --strict   # spec ↔ 实现双向校验
pytest tests/test_spec_coverage.py                 # CI 门禁
```

## 被动嗅探（替代主动探测）

不想发探测请求？挂在现有流量上被动收集：

```python
from atst.transport.sniff import Sniffer, attach

sniffer = Sniffer(ring_size=16)
attach(sniffer, client)  # 就地包装 client 的 request：每个响应自动进环形缓冲

sniffer.observe_response(0x1234, payload)  # 也可以自己喂
print(sniffer.unknown_commands())  # 全族皆未登记的命令号
print(sniffer.export_drafts())  # 为未知命令号生成 DRAFT，返回写入路径列表
```

`export_drafts()` 的 `family` 是双重参数：既决定草稿归哪一族，也限定"未知"的判定域——
默认只按 `quotation` 族判未知。

## 纪律

- status=stable 的 spec 必须有 golden 样本（`tests/golden/`）
- `IntegrityViolation`（E3042）绝不允许降级解析 —— 降级会把"已知错误"换成"看起来像数据的
  噪声"，`dispatch()` 对它是直接 re-raise，不走 L2/L3
- spec 变更走 PR + 双人复核（评审流程见 `PROTOCOL_SPEC/README.md`）
