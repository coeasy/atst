# Structure Analysis

## Command

- **method**: 0x530
- **command_name**: REALTIME_QUOTE
- **tag**: 600000
- **note**: 600000 实时快照（market=1，请求字节已按反转语义换算）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 25
- **method**: 0x530
- **zip_size**: 80
- **unzip_size**: 80
- **payload_len**: 80

## Record Hypothesis

- **record_size_hypothesis**: 16
- **record_count_hypothesis**: 5
- **size_note**: 启发式推断（payload_len=80, factor=16）

## Request Body

- **method**: 0x530
- **body_len**: 8
- **body_hex**: `0100363030303030`

## Parse Context

- **code**: 600000
- **market**: 1
- **price_scale**: 100
