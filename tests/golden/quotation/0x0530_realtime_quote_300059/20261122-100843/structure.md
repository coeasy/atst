# Structure Analysis

## Command

- **method**: 0x530
- **command_name**: REALTIME_QUOTE
- **tag**: 300059
- **note**: 300059 实时快照（market=0，请求字节已按反转语义换算）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 33
- **method**: 0x530
- **zip_size**: 85
- **unzip_size**: 85
- **payload_len**: 85

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x530
- **body_len**: 8
- **body_hex**: `0101333030303539`

## Parse Context

- **code**: 300059
- **market**: 0
- **price_scale**: 100
