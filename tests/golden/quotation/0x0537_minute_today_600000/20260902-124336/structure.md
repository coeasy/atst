# Structure Analysis

## Command

- **method**: 0x537
- **command_name**: MINUTE_TODAY
- **tag**: 600000
- **note**: 分时图数据

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 5
- **method**: 0x537
- **zip_size**: 4
- **unzip_size**: 4
- **payload_len**: 4

## Record Hypothesis

- **record_size_hypothesis**: 4
- **record_count_hypothesis**: 1
- **size_note**: 启发式推断（payload_len=4, factor=4）

## Request Body

- **method**: 0x537
- **body_len**: 12
- **body_hex**: `363030303030010000000000`

## Parse Context

- **code**: 600000
- **market**: 1
