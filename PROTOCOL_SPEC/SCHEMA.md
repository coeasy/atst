# SCHEMA — YAML Spec 格式定义

> **Version**: 1.0
> **Status**: stable

---

## 1. Overview

每个 YAML spec 文件定义一条 TDX 协议命令的二进制帧结构。格式设计目标：

- **自描述**：无需外部文档即可理解命令语义
- **可机器解析**：`codegen.py` 可直接消费
- **可审计**：`spec_audit.py` 可对比 spec 与实现
- **紧凑**：避免冗余，保持可读

---

## 2. Top-Level Structure

```yaml
# ─── 元数据（必填）──────────────────────────────────────────────────
spec_id: "0x052d"          # 命令号，16 位十六进制，带 "0x" 前缀
name: "security_bars"      # 短名（snake_case）
family: "7709"             # 协议族：7709 | 7727 | MAC | F10 | GOODS
version: "1.0"             # spec 版本（SemVer）
description: "K-line data (multi-category)"
status: "stable"           # stable | verified | inferred | draft | deprecated

# ─── 请求体（必填）──────────────────────────────────────────────────
request:
  length: 26               # 请求体字节数（不含 12 字节帧头）
  fields: [...]            # 字段定义列表

# ─── 响应体（必填）──────────────────────────────────────────────────
response:
  header: [...]            # 响应头部字段（在记录列表之前）
  fields: [...]            # 响应记录字段（每条记录的布局）
  record_size: 32          # 单条记录字节数（变长时为 null）
  encoding: "diff"         # 可选：diff | absolute | raw

# ─── 命令上下文（可选）────────────────────────────────────────────────
context:
  price_scale: 1000        # 价格缩放因子
  max_batch: 800           # 单次请求上限
  # ... 其他上下文参数

# ─── 错误码（可选）──────────────────────────────────────────────────
error_codes:
  - code: 0
    meaning: "success"
  - code: 1
    meaning: "invalid code"

# ─── Web 兼容映射（可选）──────────────────────────────────────────────
web_mapping:
  adapter: "easyquotation"
  endpoint: "..."

# ─── 备注（可选）──────────────────────────────────────────────────────
notes: "..."

# ─── Golden 样本引用（可选）───────────────────────────────────────────
golden_samples:
  - "tests/golden/quotation/..."
```

---

## 3. Field Definition

每个字段是一个对象，描述一个二进制字段：

```yaml
- name: "market"           # 字段名（snake_case）
  offset: 0                # 字节偏移（相对于所属结构体起始）
  type: "uint16"           # 数据类型（见 §4）
  length: 2                # 字节长度（与 type 一致，显式声明供 codegen）
  description: "Market ID"
  format: null             # 可选：格式化说明（如 "yyyymmdd_bcd"）
  value: null              # 可选：固定值（如 header 中的 flag=1）
  signed: false            # 可选：是否为有符号
  scale: null              # 可选：缩放因子（如 price/1000）
  unit: null               # 可选：单位（如 "share", "lot", "yuan"）
  nullable: false          # 可选：是否可为空
  default: null            # 可选：默认值
```

---

## 4. Type System

| Type | Size (bytes) | Python Type | Description |
|------|-------------|-------------|-------------|
| `uint8` | 1 | `int` | 8-bit unsigned integer |
| `int8` | 1 | `int` | 8-bit signed integer |
| `uint16` | 2 | `int` | 16-bit unsigned integer (little-endian) |
| `int16` | 2 | `int` | 16-bit signed integer (little-endian) |
| `uint32` | 4 | `int` | 32-bit unsigned integer (little-endian) |
| `int32` | 4 | `int` | 32-bit signed integer (little-endian) |
| `float32` | 4 | `float` | IEEE 754 single-precision |
| `float64` | 8 | `float` | IEEE 754 double-precision |
| `string[N]` | N | `str` | Fixed-length string, NUL-padded (GBK encoded) |
| `leb128` | 1-8 | `int` | LEB128 signed variable-length integer (TDX price encoding) |
| `tdx_float` | 4 | `float` | TDX custom 4-byte float (volume/amount encoding) |
| `varint` | 1-8 | `int` | 6-bit variable-length integer (extended market) |
| `raw[N]` | N | `bytes` | Opaque byte sequence |
| `bool` | 1 | `bool` | Single-bit boolean (0=false, nonzero=true) |

**Special formats** (used in `format` field):

| Format | Description | Type |
|--------|-------------|------|
| `yyyymmdd_bcd` | BCD-encoded date | `uint16` |
| `yyyymmdd` | Numeric date as integer | `uint32` |
| `lc16_date` | TDX lc16 date encoding | `uint16` |
| `minutes_from_open` | Minutes since market open | `uint16` |
| `diff_from_open` | Price difference from open (LEB128) | `leb128` |
| `diff_from_prev` | Price difference from previous record | `leb128` |
| `gbk` | GBK-encoded string | `string[N]` |

---

## 5. Request Structure

```yaml
request:
  length: <int>         # Total request body bytes (excluding 12-byte frame header)
  fields:
    - name: <str>
      offset: <int>
      type: <str>
      # ... field properties
```

**Frame header** (12 bytes, common to all commands):

```
Offset  Size  Field         Description
0       1     magic1        0x0c
1       1     magic2        0x02 or 0x03
2       4     seq           Sequence number (big-endian)
6       4     pkg_len       Package length = 12 + body_len
10      2     method        Command ID (big-endian, e.g. 0x052D)
```

The 12-byte frame header is NOT part of `request.fields` — it is handled by
the framing layer (`tstdx/codec/framing.py`).

---

## 6. Response Structure

```yaml
response:
  header:
    - name: "status"
      type: "uint8"
    - name: "record_count"
      type: "uint16"
  fields:
    - name: <str>
      offset: <int>
      type: <str>
      # ...
  record_size: <int|null>
  encoding: "diff" | "absolute" | "raw"
```

**Header fields** are the fields that appear before the record list.
Typically `count: uint16` (number of records).

**Record fields** describe the layout of each individual record.
`record_size` is the byte length of one record. For variable-length records
(e.g. `0x052D` with LEB128 fields), set `record_size: null` and specify
a `min_record_size` instead.

---

## 7. Context Parameters

Context parameters are runtime configuration that affects parsing but
is not part of the wire format:

```yaml
context:
  price_scale: 1000        # Price divisor (7709 K-line = 1000, quote = 100)
  max_batch: 800           # Max records per request (server limit)
  volume_unit: "share"     # Expected volume unit after parsing
  category_variants:       # For multi-category commands
    - category: 0          # 5-min bars
      datetime: "lc16+minutes"
      volume_unit: "share"
    - category: 4          # Daily bars
      datetime: "yyyymmdd"
      volume_unit: "share"
    - category: 5          # Weekly bars
      datetime: "yyyymmdd"
      volume_unit: "lot"   # ×100 to convert to shares
```

---

## 8. Status Values

| Status | Meaning |
|--------|---------|
| `stable` | Layout verified by golden samples; parser fully implemented and tested |
| `verified` | Layout verified by golden samples; parser pending codegen |
| `inferred` | Layout from public documentation; pending golden verification |
| `draft` | Auto-generated by ProtocolSniffer; pending human review |
| `deprecated` | Command discontinued or replaced |

---

## 9. Codegen Interface

`codegen.py` expects a spec dict with the following minimum keys:

```python
{
    "spec_id": "0x052d",
    "name": "security_bars",
    "family": "7709",
    "version": "1.0",
    "status": "stable",
    "request": {"length": 26, "fields": [...]},
    "response": {"header": [...], "fields": [...], "record_size": 32},
    "context": {...},
    "notes": "...",
    "golden_samples": [...],
}
```

The generator produces:

1. A `BaseParser` subclass with `parse_payload()` method
2. A docstring describing the field layout
3. A `RECORD_SIZE` class attribute (when fixed-size)
4. Type annotations for all return value fields

---

## 10. Error Codes

Optional per-command error code mapping:

```yaml
error_codes:
  - code: 0
    meaning: "success"
  - code: 1
    meaning: "invalid market or code"
  - code: 2
    meaning: "out of range"
```

If omitted, the command follows the protocol-family default error mapping
(defined in `tstdx/errors.py`).

---

## 11. Web Mapping (Optional)

For commands that have HTTP equivalents in the Web adapter layer:

```yaml
web_mapping:
  adapter: "sina"           # Adapter name
  endpoint: "/hq/list"      # HTTP path
  param_mapping:            # spec field → HTTP parameter
    market: "market"
    code: "symbol"
  response_mapping:         # spec field → HTTP response field
    price: "price"
    volume: "volume"
  normalization:            # Volume/amount unit adjustments
    volume_factor: 100      # Tencent ×100, Eastmoney ×100
    amount_factor: 10000
```

---

## 12. Validation Rules

The following rules MUST be satisfied by every spec file:

1. **`spec_id`** must match the file name prefix: `0x052D_SECURITY_BARS.yaml`
   → `spec_id: "0x052d"`
2. **`family`** must match the directory name: `PROTOCOL_SPEC/7709/` →
   `family: "7709"`
3. **Field offsets** must be non-decreasing: `fields[0].offset < fields[1].offset < ...`
4. **`request.length`** must equal `max(field.offset + field.length) + 1`
   (or the sum of all field lengths if contiguous)
5. **`golden_samples`** paths must exist relative to project root
6. **`status: stable`** requires at least one entry in `golden_samples`