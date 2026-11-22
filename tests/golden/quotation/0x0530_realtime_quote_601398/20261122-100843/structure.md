# Structure Analysis

## Command

- **method**: 0x530
- **command_name**: REALTIME_QUOTE
- **tag**: 601398
- **note**: 601398 实时快照（market=1，请求字节已按反转语义换算）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 27
- **method**: 0x530
- **zip_size**: 84
- **unzip_size**: 84
- **payload_len**: 84

## Record Hypothesis

- **record_size_hypothesis**: 28
- **record_count_hypothesis**: 3
- **size_note**: 启发式推断（payload_len=84, factor=28）

## Request Body

- **method**: 0x530
- **body_len**: 8
- **body_hex**: `0100363031333938`

## Parse Context

- **code**: 601398
- **market**: 1
- **price_scale**: 100
