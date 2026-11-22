# Structure Analysis

## Command

- **method**: 0x530
- **command_name**: REALTIME_QUOTE
- **tag**: 002415
- **note**: 002415 实时快照（market=0，请求字节已按反转语义换算）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 32
- **method**: 0x530
- **zip_size**: 72
- **unzip_size**: 72
- **payload_len**: 72

## Record Hypothesis

- **record_size_hypothesis**: 8
- **record_count_hypothesis**: 9
- **size_note**: 启发式推断（payload_len=72, factor=8）

## Request Body

- **method**: 0x530
- **body_len**: 8
- **body_hex**: `0101303032343135`

## Parse Context

- **code**: 002415
- **market**: 0
- **price_scale**: 100
