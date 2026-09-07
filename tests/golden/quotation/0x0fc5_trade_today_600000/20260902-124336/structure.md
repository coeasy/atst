# Structure Analysis

## Command

- **method**: 0xfc5
- **command_name**: TRADE_TODAY
- **tag**: 600000
- **note**: 逐笔成交（历史分时成交）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 6
- **method**: 0xfc5
- **zip_size**: 75
- **unzip_size**: 75
- **payload_len**: 75

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0xfc5
- **body_len**: 12
- **body_hex**: `010036303030303000000a00`

## Parse Context

- **code**: 600000
- **market**: 1
