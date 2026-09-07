# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 600000_cat8
- **note**: 600000 周期 8，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 9
- **method**: 0x52d
- **zip_size**: 164
- **unzip_size**: 164
- **payload_len**: 164

## Record Hypothesis

- **record_size_hypothesis**: 4
- **record_count_hypothesis**: 41
- **size_note**: 启发式推断（payload_len=164, factor=4）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `01003630303030300800010000000a0000000000000000000000`

## Parse Context

- **category**: 8
- **code**: 600000
- **market**: 1
