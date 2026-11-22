# Structure Analysis

## Command

- **method**: 0x537
- **command_name**: MINUTE_TODAY
- **tag**: 600000
- **note**: 分时图数据

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 28 (compressed)
- **seq**: 19
- **method**: 0x537
- **zip_size**: 1033
- **unzip_size**: 1087
- **payload_len**: 1087

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x537
- **body_len**: 12
- **body_hex**: `010036303030303000000000`

## Parse Context

- **code**: 600000
- **market**: 1
