# Structure Analysis

## Command

- **method**: 0x530
- **command_name**: REALTIME_QUOTE
- **tag**: 000858
- **note**: 000858 实时快照（market=0，请求字节已按反转语义换算）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 31
- **method**: 0x530
- **zip_size**: 75
- **unzip_size**: 75
- **payload_len**: 75

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x530
- **body_len**: 8
- **body_hex**: `0101303030383538`

## Parse Context

- **code**: 000858
- **market**: 0
- **price_scale**: 100
