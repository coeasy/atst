# Structure Analysis

## Command

- **method**: 0xf
- **command_name**: CAPITAL_CHANGES
- **tag**: 000001
- **note**: 除权除息信息（深市）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 28 (compressed)
- **seq**: 7
- **method**: 0xf
- **zip_size**: 4532
- **unzip_size**: 13707
- **payload_len**: 13707

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0xf
- **body_len**: 8
- **body_hex**: `3030303030310000`

## Parse Context

- **code**: 000001
- **market**: 0
