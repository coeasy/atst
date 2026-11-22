# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 600000_cat6
- **note**: 600000 周期 6，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 7
- **method**: 0x52d
- **zip_size**: 192
- **unzip_size**: 192
- **payload_len**: 192

## Record Hypothesis

- **record_size_hypothesis**: 32
- **record_count_hypothesis**: 6
- **size_note**: 启发式推断（payload_len=192, factor=32）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `01003630303030300600010000000a0000000000000000000000`

## Parse Context

- **category**: 6
- **code**: 600000
- **market**: 1
