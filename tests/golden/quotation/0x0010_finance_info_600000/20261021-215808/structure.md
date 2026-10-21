# Structure Analysis

## Command

- **method**: 0x10
- **command_name**: FINANCE_INFO
- **tag**: 600000
- **note**: 财务基础信息

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 2
- **method**: 0x10
- **zip_size**: 145
- **unzip_size**: 145
- **payload_len**: 145

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x10
- **body_len**: 9
- **body_hex**: `010001363030303030`

## Parse Context

- **code**: 600000
- **market**: 1
