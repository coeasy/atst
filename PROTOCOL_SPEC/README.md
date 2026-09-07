# PROTOCOL\_SPEC — TDX 协议命令规范体系

> **Status**: Active (v0.1.0)
> **Created**: 2026-08-31
> **Owner**: Tier A item A1, docs/archive/GAP\_ANALYSIS\_v0.md

***

## 1. Purpose

`PROTOCOL_SPEC/` 是 **tstdx 项目的协议规范权威源**，以人可读的 YAML 文件形式
精确描述每一个 TDX 协议命令的二进制帧结构——请求体、响应头、响应记录布局、
字段类型、偏移量、编码格式。

它的核心目标有三个：

1. **可追溯性**：每个解析器的字段布局都有对应的规范文件，修改字段前先改 spec，
   再由 `codegen.py` 重新生成解析器，避免"解析器悄悄漂移"。
2. **可生成性**：`tools/codegen.py` 读取 YAML spec 自动生成 `BaseParser` 子类，
   新增协议族或命令时不再需要手写解析代码。
3. **可审计性**：`tools/spec_audit.py` 对比 spec 与实现（解析器行为），
   输出契约差异报告，作为 CI 门禁。

***

## 2. Directory Layout

```
PROTOCOL_SPEC/
├── README.md          # ← 本文件
├── SCHEMA.md          # YAML spec 格式定义
├── 7709/              # 标准 7709 协议族（A 股行情，golden-verified）
│   ├── 0x000D_HANDSHAKE.yaml
│   ├── 0x0004_HEARTBEAT.yaml
│   ├── 0x052D_SECURITY_BARS.yaml
│   ├── 0x0530_REALTIME_QUOTE.yaml
│   ├── 0x0537_MINUTE_TODAY.yaml
│   ├── 0x0FC5_TRADE_TODAY.yaml
│   ├── 0x0FC6_TRADE_TODAY_ALT.yaml
│   └── 0x044E_SECURITY_COUNT.yaml
├── 7727/              # 扩展市场协议族（港股/美股/期货/外汇/期权）
│   ├── 0x0100_EX_MARKET_COUNT.yaml
│   ├── 0x0101_EX_MARKET_LIST.yaml
│   ├── 0x0102_EX_INSTRUMENT_COUNT.yaml
│   ├── 0x0103_EX_INSTRUMENT_LIST.yaml
│   ├── 0x0104_EX_INSTRUMENT_BARS.yaml
│   ├── 0x0105_EX_INSTRUMENT_QUOTE.yaml
│   ├── 0x0106_EX_INSTRUMENT_TRADE.yaml
│   ├── 0x0107_EX_INSTRUMENT_MINUTE.yaml
│   ├── 0x0108_EX_INSTRUMENT_INFO.yaml
│   ├── 0x0109_EX_BATCH_QUOTE.yaml
│   ├── 0x010A_EX_HK_QUOTE.yaml
│   ├── 0x010B_EX_US_QUOTE.yaml
│   ├── 0x010C_EX_FUTURE_QUOTE.yaml
│   ├── 0x010D_EX_FX_QUOTE.yaml
│   └── 0x010E_EX_OPTION_QUOTE.yaml
├── GOODS/             # 商品语义协议族（期货/期权/外汇 0x0200–0x020A）
│   ├── 0x0200_GOODS_COUNT.yaml
│   ├── 0x0201_GOODS_LIST.yaml
│   ├── 0x0202_GOODS_BARS.yaml
│   ├── 0x0203_GOODS_QUOTE.yaml
│   ├── 0x0204_GOODS_TRADE.yaml
│   ├── 0x0205_GOODS_MINUTE.yaml
│   ├── 0x0206_GOODS_INFO.yaml
│   ├── 0x0207_GOODS_HOLDING.yaml
│   ├── 0x0208_GOODS_OPTION_GREEKS.yaml
│   ├── 0x0209_GOODS_FX_RATE.yaml
│   └── 0x020A_GOODS_CALENDAR.yaml
├── MAC/               # MAC 板块分析协议族（F2 板块数据在线化）
│   ├── 0x120F_MAC_BLOCK_LIST.yaml
│   ├── 0x1210_MAC_BLOCK_MEMBERS.yaml
│   └── 0x2000_MAC_BLOCK_QUOTE.yaml
├── TRADE/             # 交易协议族（P2-1 · 洁净室推断，独立端口/帧布局）
│   ├── 0x0001_LOGIN.yaml
│   ├── 0x0002_HEARTBEAT.yaml
│   ├── 0x0003_LOGOUT.yaml
│   ├── 0x0100_QUERY.yaml
│   ├── 0x1000_SEND_ORDER.yaml
│   └── 0x1001_CANCEL_ORDER.yaml
└── UNKNOWN/           # 自动发现的未知命令（.gitkeep 占位）
```

> **协议族目录**：F10 族暂无 spec YAML（等待真机样本后按本 README 命名规则
> 补充）。7727 / GOODS / MAC 族的 spec 为 **`status: inferred`**（洁净室推断
> 布局，由 `tests/protocol/test_7727_goods_mac_coverage.py` 合成载荷锁定），
> 真机 golden 样本到位后逐个翻转 `stable` 并补 `golden_samples`。
>
> **TRADE 交易族**：帧布局为洁净室推断占位（**`status: draft`**），独立于
> 行情族（8 字节帧头、价格以分、长度前缀 GBK 串），由 `tstdx/trade/`
> 模拟器回路验证；**红线**——绝不连接真实券商通道。真机抓包定标前
> 不得将布局当作协议事实，相关命令也未登记入 `commands.py` 账本。

命名规则：每个 YAML 文件以 `{hex_cmd_id}_{short_name}.yaml` 命名。
例如 `0x052D_SECURITY_BARS.yaml` 对应命令 `0x052D`，名称 `security_bars`。

***

## 3. Spec ↔ Parser Relationship

```
PROTOCOL_SPEC/7709/0x052D_SECURITY_BARS.yaml
        │
        ▼
tools/codegen.py  ──→  tstdx/protocol/parsers/std7709.py
                           class SecurityBarsParser(BaseParser): ...
        │                              │
        │                              ▼
        │                    tests/golden/...
        │                    tests/test_spec_coverage.py
        ▼                              │
tools/spec_audit.py  ──────────────────┘
   └── 报告 spec 与实现的契约差异
```

- **Spec → Parser**：`codegen.py` 读取 YAML，生成 `BaseParser` 子类。
  生成的解析器包含完整的字段布局、类型转换、边界检查。

- **Parser → Spec**：`spec_audit.py` 反射 `BaseParser` 类的 docstring 和
  类属性，反推实际字段布局，与 YAML spec 比对。

- **Golden → Spec**：自采集的 golden 样本（`tests/golden/`）是验证 spec
  正确性的黄金标准。每个 spec 文件底部的 `golden_samples` 字段引用对应
  样本路径。

***

## 4. Codegen Workflow

```bash
# 1. 修改或新建 spec
$ $EDITOR PROTOCOL_SPEC/7709/0x052D_SECURITY_BARS.yaml

# 2. 生成解析器
$ python -m tstdx.tools.codegen PROTOCOL_SPEC/7709/0x052D_SECURITY_BARS.yaml

# 3. 验证 spec 与实现的契约一致性
$ python -m tstdx.tools.spec_audit

# 4. 运行 golden 回归
$ pytest tests/unit/test_golden.py -k "0x052D"
```

### 4.1 模板系统

`_templates/` 下存放 codegen 使用的 Jinja2 模板：

| 模板                    | 用途                      |
| --------------------- | ----------------------- |
| `parser_class.j2`     | 生成 `BaseParser` 子类骨架    |
| `parser_docstring.j2` | 生成 docstring（字段布局 + 示例） |
| `test_stub.j2`        | 生成 pytest 测试骨架          |

模板由 `codegen.py` 加载，传入 spec dict 渲染。详见 `SCHEMA.md` §5。

***

## 5. Spec ↔ Implementation Validation

`tools/spec_audit.py` 实现以下检查：

| 检查项           | 说明                                                                |
| ------------- | ----------------------------------------------------------------- |
| **请求体长度**     | spec `request.length` == 解析器实际构造的请求体长度                            |
| **字段偏移**      | spec 各字段的 `offset` 累加 == 解析器 `reader` 的实际读取顺序                     |
| **字段类型**      | spec `type` 映射到 Python 类型（`uint16` → `int`，`tdx_float` → `float`） |
| **记录大小**      | spec `response.record_size` == 解析器 `RECORD_SIZE` 类属性              |
| **golden 覆盖** | spec `golden_samples` 引用的样本文件存在且 sha256 匹配                        |
| **命令号一致**     | spec `spec_id` == 解析器 `@register_parser(cmd=...)` 参数              |

CI 门禁：`spec_audit` 任一检查失败则 pipeline 红灯。

***

## 6. Versioning & Status

每个 spec 文件有 `version` 和 `status` 字段：

| 字段                   | 值                                | 含义     |
| -------------------- | -------------------------------- | ------ |
| `status: stable`     | 字段布局已 golden-verified，代码已实装      | <br /> |
| `status: verified`   | 字段布局已 golden-verified，代码待生成      | <br /> |
| `status: inferred`   | 字段布局来自公开资料推断，待样本锁定               | <br /> |
| `status: draft`      | 自动草稿（由 ProtocolSniffer 生成），待人工评审 | <br /> |
| `status: deprecated` | 命令已被主站下线或替代                      | <br /> |

版本号遵循 SemVer：

- **MAJOR**：字段布局或类型发生不兼容变更（如新增必填字段）

- **MINOR**：新增可选字段或新增命令

- **PATCH**：修正注释、golden sample 引用、拼写

***

## 7. Adding a New Spec

1. 在 `PROTOCOL_SPEC/<family>/` 下新建 `<hex_cmd>_<name>.yaml`
2. 填写 `spec_id`、`name`、`family`、`version`、`description`
3. 填写 `request` 部分（参考 `SCHEMA.md`）
4. 填写 `response` 部分（含 `header` 和 `fields`）
5. 如有 golden 样本，填入 `golden_samples` 路径
6. 运行 `codegen.py` 生成解析器
7. 运行 `spec_audit.py` 验证一致性
8. 在 `tstdx/protocol/commands.py` 中注册命令（如尚未注册）

***

## 8. Cross-Reference: Spec → commands.py

| Spec File                     | commands.py Entry          | Tier | Status   |
| ----------------------------- | -------------------------- | ---- | -------- |
| `0x000D_HANDSHAKE.yaml`       | `HANDSHAKE` (0x000D)       | L2   | inferred |
| `0x0004_HEARTBEAT.yaml`       | `HEARTBEAT` (0x0004)       | L2   | inferred |
| `0x052D_SECURITY_BARS.yaml`   | `SECURITY_BARS` (0x052D)   | L1   | stable   |
| `0x0530_REALTIME_QUOTE.yaml`  | `REALTIME_QUOTE` (0x0530)  | L1   | stable   |
| `0x0537_MINUTE_TODAY.yaml`    | `MINUTE_TODAY` (0x0537)    | L2   | inferred |
| `0x0FC5_TRADE_TODAY.yaml`     | `TRADE_TODAY` (0x0FC5)     | L2   | inferred |
| `0x0FC6_TRADE_TODAY_ALT.yaml` | `TRADE_TODAY_ALT` (0x0FC6) | D    | inferred |
| `0x044E_SECURITY_COUNT.yaml`  | `SECURITY_COUNT` (0x044E)  | L1   | stable   |

***

## 9. Relationship to DESIGN.md

| DESIGN.md Section | PROTOCOL\_SPEC Correspondence        |
| ----------------- | ------------------------------------ |
| §4 协议全覆盖          | `PROTOCOL_SPEC/` 根目录                 |
| §5 命令登记表          | `PROTOCOL_SPEC/<family>/*.yaml`      |
| §6 报文层            | spec 的 `request` / `response` 部分     |
| §16 Golden 样本     | spec 的 `golden_samples` 字段           |
| §20 验收闭环          | `spec_audit.py` 检查矩阵                 |
| §24 错误分类          | spec 的 `error_codes` 字段（见 SCHEMA.md） |
| §33 Web 兼容        | spec 的 `web_mapping` 字段（见 SCHEMA.md） |

