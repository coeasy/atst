# Structure Analysis

## Command

- **method**: 0xf
- **command_name**: CAPITAL_CHANGES
- **tag**: 000001
- **note**: 除权除息信息（深市）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 28 (compressed)
- **seq**: 3
- **method**: 0xf
- **zip_size**: 872
- **unzip_size**: 2360
- **payload_len**: 2360

## Record Hypothesis

- **record_size_hypothesis**: 8
- **record_count_hypothesis**: 295
- **size_note**: 启发式推断（payload_len=2360, factor=8）

## Request Body

- **method**: 0xf
- **body_len**: 9
- **body_hex**: `010000303030303031`

## Parse Context

- **code**: 000001
- **market**: 0
