# Structure Analysis

## Command

- **method**: 0xfc5
- **command_name**: TRADE_TODAY
- **tag**: 000001
- **note**: 逐笔成交（深市）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 10
- **method**: 0xfc5
- **zip_size**: 77
- **unzip_size**: 77
- **payload_len**: 77

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0xfc5
- **body_len**: 12
- **body_hex**: `000030303030303100000a00`

## Parse Context

- **code**: 000001
- **market**: 0
