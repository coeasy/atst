# Structure Analysis

## Command

- **method**: 0x537
- **command_name**: MINUTE_TODAY
- **tag**: 600000
- **note**: 分时图数据（上证，市场在前）

## Frame Header

- **magic**: 0x74cbb1
- **zip_flag**: 28 (compressed)
- **seq**: 1
- **method**: 0x537
- **zip_size**: 1027
- **unzip_size**: 1196
- **payload_len**: 1196

## Record Hypothesis

- **record_size_hypothesis**: 4
- **record_count_hypothesis**: 299
- **size_note**: 启发式推断（payload_len=1196, factor=4）

## Request Body

- **method**: 0x537
- **body_len**: 12
- **body_hex**: `010036303030303000000000`

## Parse Context

- **code**: 600000
- **market**: 1
