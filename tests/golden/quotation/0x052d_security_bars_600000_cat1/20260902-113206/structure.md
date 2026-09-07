# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 600000_cat1
- **note**: 600000 周期 1，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 2
- **method**: 0x52d
- **zip_size**: 168
- **unzip_size**: 168
- **payload_len**: 168

## Record Hypothesis

- **record_size_hypothesis**: 56
- **record_count_hypothesis**: 3
- **size_note**: 启发式推断（payload_len=168, factor=56）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `01003630303030300100010000000a0000000000000000000000`

## Parse Context

- **category**: 1
- **code**: 600000
- **market**: 1
