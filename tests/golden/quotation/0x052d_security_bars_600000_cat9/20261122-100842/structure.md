# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 600000_cat9
- **note**: 600000 周期 9，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 28 (compressed)
- **seq**: 10
- **method**: 0x52d
- **zip_size**: 187
- **unzip_size**: 188
- **payload_len**: 188

## Record Hypothesis

- **record_size_hypothesis**: 4
- **record_count_hypothesis**: 47
- **size_note**: 启发式推断（payload_len=188, factor=4）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `01003630303030300900010000000a0000000000000000000000`

## Parse Context

- **category**: 9
- **code**: 600000
- **market**: 1
