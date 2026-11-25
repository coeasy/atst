<!-- 本文件由 scripts/gen_docs.py 生成，请勿手改；改代码后运行 `make docs-gen`。 -->

# 通达信命令账本（投影）

- 命令总数：85
- `verified=True`：9
- `verified=False`：76

| 命令 | 名称 | 层级 | verified | status |
|---|---|---|---|---|
| `('ex_quotation', 4)` | `EX_HEARTBEAT` | L2 | False | online |
| `('ex_quotation', 13)` | `EX_HANDSHAKE` | L2 | False | online |
| `('ex_quotation', 256)` | `EX_MARKET_COUNT` | L2 | False | online |
| `('ex_quotation', 257)` | `EX_MARKET_LIST` | L2 | False | online |
| `('ex_quotation', 258)` | `EX_INSTRUMENT_COUNT` | L2 | False | online |
| `('ex_quotation', 259)` | `EX_INSTRUMENT_LIST` | L2 | False | online |
| `('ex_quotation', 260)` | `EX_INSTRUMENT_BARS` | L2 | False | online |
| `('ex_quotation', 261)` | `EX_INSTRUMENT_QUOTE` | L2 | False | online |
| `('ex_quotation', 262)` | `EX_INSTRUMENT_TRADE` | L2 | False | online |
| `('ex_quotation', 263)` | `EX_INSTRUMENT_MINUTE` | L2 | False | online |
| `('ex_quotation', 264)` | `EX_INSTRUMENT_INFO` | L2 | False | online |
| `('ex_quotation', 265)` | `EX_BATCH_QUOTE` | L2 | False | online |
| `('ex_quotation', 266)` | `EX_HK_QUOTE` | L2 | False | online |
| `('ex_quotation', 267)` | `EX_US_QUOTE` | L2 | False | online |
| `('ex_quotation', 268)` | `EX_FUTURE_QUOTE` | L2 | False | online |
| `('ex_quotation', 269)` | `EX_FX_QUOTE` | L2 | False | online |
| `('ex_quotation', 270)` | `EX_OPTION_QUOTE` | L2 | False | online |
| `('f10', 1)` | `F10_CATALOG` | L2 | False | online |
| `('f10', 2)` | `F10_TEXT` | L2 | False | online |
| `('goods', 512)` | `GOODS_COUNT` | L2 | False | online |
| `('goods', 513)` | `GOODS_LIST` | L2 | False | online |
| `('goods', 514)` | `GOODS_BARS` | L2 | False | online |
| `('goods', 515)` | `GOODS_QUOTE` | L2 | False | online |
| `('goods', 516)` | `GOODS_TRADE` | L2 | False | online |
| `('goods', 517)` | `GOODS_MINUTE` | L2 | False | online |
| `('goods', 518)` | `GOODS_INFO` | L2 | False | online |
| `('goods', 519)` | `GOODS_HOLDING` | L2 | False | online |
| `('goods', 520)` | `GOODS_OPTION_GREEKS` | L2 | False | online |
| `('goods', 521)` | `GOODS_FX_RATE` | L2 | False | online |
| `('goods', 522)` | `GOODS_CALENDAR` | L2 | False | online |
| `('mac_quotation', 4623)` | `MAC_BLOCK_LIST` | L2 | False | online |
| `('mac_quotation', 4624)` | `MAC_BLOCK_MEMBERS` | L2 | False | online |
| `('mac_quotation', 4864)` | `MAC_UNIFIED_BARS` | L2 | False | online |
| `('mac_quotation', 4865)` | `MAC_UNIFIED_QUOTE` | L2 | False | online |
| `('mac_quotation', 5120)` | `MAC_FUND_FLOW` | L2 | False | online |
| `('mac_quotation', 5376)` | `MAC_MAINFORCE` | L2 | False | online |
| `('mac_quotation', 5632)` | `MAC_AUCTION` | L2 | False | online |
| `('mac_quotation', 5888)` | `MAC_MULTIDAY_MINUTE` | L2 | False | online |
| `('mac_quotation', 8192)` | `MAC_BLOCK_QUOTE` | L2 | False | online |
| `('mac_quotation', 8448)` | `MAC_INDEX_BARS` | L2 | False | online |
| `('mac_quotation', 8704)` | `MAC_RANK` | L2 | False | online |
| `('mac_quotation', 8960)` | `MAC_DDE` | L2 | False | online |
| `('mac_quotation', 9216)` | `MAC_CHIP` | L2 | False | online |
| `('mac_quotation', 9472)` | `MAC_NEWS` | L2 | False | online |
| `('mac_quotation', 9568)` | `MAC_CLIENT_INFO` | L2 | False | online |
| `('mac_quotation', 9570)` | `MAC_HEARTBEAT` | L2 | False | online |
| `('quotation', 4)` | `HEARTBEAT` | L2 | True | online |
| `('quotation', 13)` | `HANDSHAKE` | L2 | True | online |
| `('quotation', 15)` | `CAPITAL_CHANGES` | L2 | False | online |
| `('quotation', 16)` | `FINANCE_INFO` | L2 | False | online |
| `('quotation', 21)` | `PING` | L2 | False | online |
| `('quotation', 30)` | `FINANCE_EXT` | D | False | online |
| `('quotation', 32)` | `UNKNOWN_0020` | D | False | online |
| `('quotation', 1101)` | `SECURITY_LIST` | L2 | True | offline |
| `('quotation', 1102)` | `SECURITY_COUNT` | L1 | True | online |
| `('quotation', 1104)` | `SECURITY_LIST_LEGACY` | D | False | offline |
| `('quotation', 1106)` | `PRICE_LIMIT` | D | False | online |
| `('quotation', 1306)` | `VOLUME_PRICE_DIST` | L2 | False | offline |
| `('quotation', 1307)` | `MINUTE_SUBPLOT` | D | False | degraded |
| `('quotation', 1308)` | `INDEX_MOMENTUM` | L2 | False | online |
| `('quotation', 1312)` | `UNKNOWN_0520` | D | False | online |
| `('quotation', 1325)` | `SECURITY_BARS` | L1 | True | online |
| `('quotation', 1328)` | `REALTIME_QUOTE` | L1 | True | online |
| `('quotation', 1335)` | `MINUTE_TODAY` | L1 | True | online |
| `('quotation', 1342)` | `QUOTES_LEGACY` | L2 | False | offline |
| `('quotation', 1343)` | `LIMIT_UP_POOL` | D | False | online |
| `('quotation', 1351)` | `QUOTES_DEPTH_PUSH` | L2 | True | online |
| `('quotation', 1355)` | `QUOTES_BY_CATEGORY` | D | False | online |
| `('quotation', 1356)` | `QUOTES_SNAPSHOT` | L2 | False | offline |
| `('quotation', 1362)` | `RANK_LIST` | D | False | online |
| `('quotation', 1379)` | `ABNORMAL_MOVE` | D | False | online |
| `('quotation', 1386)` | `AUCTION_SNAPSHOT` | L2 | False | offline |
| `('quotation', 1721)` | `FILE_DOWNLOAD` | L2 | False | online |
| `('quotation', 2021)` | `BLOCK_QUOTES` | L2 | False | offline |
| `('quotation', 4008)` | `UNKNOWN_0FA8` | D | False | online |
| `('quotation', 4016)` | `HISTORY_BARS_EXT` | D | False | online |
| `('quotation', 4020)` | `MINUTE_HISTORY` | L2 | False | offline |
| `('quotation', 4021)` | `TRADE_HISTORY` | D | False | online |
| `('quotation', 4035)` | `TRADE_SUMMARY` | D | False | online |
| `('quotation', 4037)` | `TRADE_TODAY` | L1 | True | online |
| `('quotation', 4038)` | `TRADE_TODAY_ALT` | L2 | False | online |
| `('quotation', 4049)` | `SPARKLINE` | D | False | online |
| `('quotation', 4059)` | `LOGIN2` | L2 | False | online |
| `('quotation', 4061)` | `SERVER_INFO` | D | False | online |
| `('quotation', 4075)` | `MINUTE_RECENT` | D | False | offline |
