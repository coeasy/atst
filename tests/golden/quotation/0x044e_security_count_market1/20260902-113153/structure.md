# Structure Analysis

## Command

- **method**: 0x44e
- **command_name**: SECURITY_COUNT
- **tag**: market1
- **note**: 上证证券数量

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 12 (uncompressed)
- **seq**: 2
- **method**: 0x44e
- **zip_size**: 2
- **unzip_size**: 2
- **payload_len**: 2

## Record Hypothesis

- **record_size_hypothesis**: 0
- **record_count_hypothesis**: 0
- **size_note**: 无法推断记录大小（payload 长度为 0 或非标准记录）

## Request Body

- **method**: 0x44e
- **body_len**: 6
- **body_hex**: `010000000000`

## Parse Context

- **market**: 1
