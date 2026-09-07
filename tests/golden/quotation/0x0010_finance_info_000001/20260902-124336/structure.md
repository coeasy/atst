# Structure Analysis

## Command

- **method**: 0x10
- **command_name**: FINANCE_INFO
- **tag**: 000001
- **note**: 财务基础信息（深市）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 28 (compressed)
- **seq**: 8
- **method**: 0x10
- **zip_size**: 5934
- **unzip_size**: 14302
- **payload_len**: 14302

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x10
- **body_len**: 8
- **body_hex**: `3030303030310000`

## Parse Context

- **code**: 000001
- **market**: 0
