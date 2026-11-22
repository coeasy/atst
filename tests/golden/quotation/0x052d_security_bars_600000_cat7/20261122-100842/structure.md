# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 600000_cat7
- **note**: 600000 周期 7，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 8
- **method**: 0x52d
- **zip_size**: 165
- **unzip_size**: 165
- **payload_len**: 165

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `01003630303030300700010000000a0000000000000000000000`

## Parse Context

- **category**: 7
- **code**: 600000
- **market**: 1
