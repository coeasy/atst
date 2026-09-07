# 食谱 03：本地 vipdoc 离线解析

目标：不联网，直接读通达信安装目录的二进制数据文件。

## 支持的文件

| 文件 | 内容 | 周期 |
|---|---|---|
| `vipdoc/sh/lday/sh600036.day` | 日线 | day |
| `vipdoc/sz/fzline/sz000001.lc1` | 1 分钟 | 1m |
| `vipdoc/sh/minline/sh600036.lc5` | 5 分钟 | 5m |
| `vipdoc/sh/gpcw/sh600036.gpcw` | 财务 | — |
| `T0002/hq_cache/*.dat` | 扩展数据 | — |

## 完整示例

```python
from pathlib import Path

from tstdx.reader.formats import (
    read_day_file,
    read_lc1_file,
    read_lc5_file,
)
from tstdx.errors import DataFileNotFound

TDX_HOME = Path("C:/new_tdx")  # 你的通达信安装目录


def load_daily(code: str, market: str = "sh"):
    path = TDX_HOME / "vipdoc" / market / "lday" / f"{market}{code}.day"
    if not path.exists():
        raise DataFileNotFound(str(path))
    return read_day_file(path)


if __name__ == "__main__":
    bars = load_daily("600036")
    print(f"共 {len(bars)} 根日线，最新: {bars[-1]}")

    # 分钟线
    m5 = read_lc5_file(TDX_HOME / "vipdoc/sh/minline/sh600036.lc5")
    print(f"5 分钟线 {len(m5)} 根")
```

## 自动探测文件规格

不确定文件格式/字节序/记录长度时，用 profile 探测：

```python
from tstdx.profile.detect import detect

data = path.read_bytes()
result = detect(data, hint_market=1, hint_period="day")
print(f"置信度 {result.confidence:.2f}, 候选记录长 {result.candidates}")
```

## 市场预设

`profile/presets.py` 内置 9 个市场预设（SH_A/SZ_A/BJ_A/基金/债券/黄金/期货等），按代码前缀自动匹配：

```python
from tstdx.profile.presets import match_preset

preset = match_preset("600036", market=1)  # SH_A
```

## 优势与陷阱

- **快**：本地解析比在线拉取快百倍，适合全市场历史回测
- **不实时**：vipdoc 收盘后才更新当天数据；盘中请用在线源
- **编码**：F10/财务文件是 GBK/GB18030，用 `tstdx.charset.encoding.decode_bytes` 解码
