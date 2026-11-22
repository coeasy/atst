# Structure Analysis

## Command

- **method**: 0x530
- **command_name**: REALTIME_QUOTE
- **tag**: 000002
- **note**: 000002 实时快照（market=0，请求字节已按反转语义换算）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 29
- **method**: 0x530
- **zip_size**: 88
- **unzip_size**: 88
- **payload_len**: 88

## Record Hypothesis

- **record_size_hypothesis**: 8
- **record_count_hypothesis**: 11
- **size_note**: 启发式推断（payload_len=88, factor=8）

## Request Body

- **method**: 0x530
- **body_len**: 8
- **body_hex**: `0101303030303032`

## Parse Context

- **code**: 000002
- **market**: 0
- **price_scale**: 100
