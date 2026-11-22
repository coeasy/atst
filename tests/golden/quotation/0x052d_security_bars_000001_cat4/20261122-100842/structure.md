# Structure Analysis

## Command

- **method**: 0x52d
- **command_name**: SECURITY_BARS
- **tag**: 000001_cat4
- **note**: 000001 周期 4，10 根

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 13
- **method**: 0x52d
- **zip_size**: 184
- **unzip_size**: 184
- **payload_len**: 184

## Record Hypothesis

- **record_size_hypothesis**: 8
- **record_count_hypothesis**: 23
- **size_note**: 启发式推断（payload_len=184, factor=8）

## Request Body

- **method**: 0x52d
- **body_len**: 26
- **body_hex**: `00003030303030310400010000000a0000000000000000000000`

## Parse Context

- **category**: 4
- **code**: 000001
- **market**: 0
