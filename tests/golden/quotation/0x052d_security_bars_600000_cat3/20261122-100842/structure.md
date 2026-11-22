# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 600000_cat3
- **note**: 600000 周期 3，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 4
- **method**: 0x52d
- **zip_size**: 172
- **unzip_size**: 172
- **payload_len**: 172

## Record Hypothesis

- **record_size_hypothesis**: 4
- **record_count_hypothesis**: 43
- **size_note**: 启发式推断（payload_len=172, factor=4）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `01003630303030300300010000000a0000000000000000000000`

## Parse Context

- **category**: 3
- **code**: 600000
- **market**: 1
