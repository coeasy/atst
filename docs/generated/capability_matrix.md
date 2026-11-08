<!-- 本文件由 scripts/gen_docs.py 生成，请勿手改；改代码后运行 `make docs-gen`。 -->

# capability × provider 矩阵

- Provider：14 个
- 迁移 capability：212 个
- 迁移绑定：282 条

| capability | 状态 | 声明 Provider | 可执行 Provider |
|---|---|---|---|
| `adjusted_bars` | `alive` | `derived` | `derived` |
| `all_market` | `alive` | `tencent`、`sina`、`derived` | `tencent`、`sina`、`derived` |
| `announcements` | `alive` | `eastmoney`、`cninfo`、`derived` | `eastmoney`、`cninfo`、`derived` |
| `auction` | `offline` | `tdx` | — |
| `baidu_kline` | `alive` | `baidu` | `baidu` |
| `baidu_minute` | `alive` | `baidu` | `baidu` |
| `baidu_quote` | `alive` | `baidu` | `baidu` |
| `baidu_ticks` | `alive` | `baidu` | `baidu` |
| `baidu_valuation_history` | `alive` | `baidu` | `baidu` |
| `balance_sheet` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `big_order_flow` | `alive` | `derived` | `derived` |
| `block_quotes` | `offline` | `tdx` | — |
| `block_trade_stats` | `alive` | `eastmoney` | `eastmoney` |
| `block_trades` | `alive` | `derived` | `derived` |
| `board_attribution` | `alive` | `eastmoney` | `eastmoney` |
| `board_list` | `alive` | `sina` | `sina` |
| `board_member` | `alive` | `sina` | `sina` |
| `board_members` | `alive` | `sina` | `sina` |
| `board_rank` | `alive` | `tencent` | `tencent` |
| `bond_all_base_info` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `bond_base_info` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `bond_history_bill` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `bond_kline` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `bond_realtime` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `bond_today_bill` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `bond_trades` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `breaking_news` | `alive` | `wallstreet`、`derived` | `wallstreet`、`derived` |
| `capital_changes` | `alive` | `tdx` | `tdx` |
| `cash_flow` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `chip_distribution` | `alive` | `derived` | `derived` |
| `chip_distributions` | `alive` | `derived` | `derived` |
| `concept_index` | `alive` | `derived` | `derived` |
| `concept_members` | `alive` | `ths`、`derived` | `ths`、`derived` |
| `connect_deals` | `alive` | `eastmoney` | `eastmoney` |
| `convertible_bond` | `alive` | `jsl` | `jsl` |
| `convertible_bonds` | `alive` | `derived` | `derived` |
| `corporate_action` | `alive` | `tdx`、`eastmoney` | `tdx`、`eastmoney` |
| `daily_enriched` | `alive` | `derived` | `derived` |
| `dc_query` | `alive` | `derived` | `derived` |
| `dc_reports` | `alive` | `builtin` | `builtin` |
| `dividend_history` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `earnings_preview` | `alive` | `derived` | `derived` |
| `em_board_members` | `alive` | `eastmoney` | `eastmoney` |
| `em_boards` | `alive` | `eastmoney` | `eastmoney` |
| `equity_pledge` | `alive` | `eastmoney` | `eastmoney` |
| `equity_structure` | `alive` | `eastmoney` | `eastmoney` |
| `esg_history` | `alive` | `sina` | `sina` |
| `esg_rating` | `alive` | `sina` | `sina` |
| `esg_ratings_all` | `alive` | `sina` | `sina` |
| `etf_shares` | `alive` | `eastmoney` | `eastmoney` |
| `ex_bars` | `alive` | `tdx` | `tdx` |
| `ex_instruments` | `alive` | `tdx` | `tdx` |
| `ex_market_list` | `alive` | `tdx` | `tdx` |
| `ex_quotes` | `alive` | `tdx` | `tdx` |
| `executive_holds` | `alive` | `derived` | `derived` |
| `f10` | `alive` | `tdx` | `tdx` |
| `f10_catalog` | `alive` | `tdx` | `tdx` |
| `fin_report` | `alive` | `derived` | `derived` |
| `finance` | `alive` | `tdx` | `tdx` |
| `financial_abstract` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `forecast` | `alive` | `derived` | `derived` |
| `free_holders` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_asset_allocation` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_base_info` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_base_info_multi` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_companies` | `alive` | `derived` | `derived` |
| `fund_company_archives` | `alive` | `derived` | `derived` |
| `fund_company_base_info` | `alive` | `derived` | `derived` |
| `fund_company_funds` | `alive` | `derived` | `derived` |
| `fund_company_scale` | `alive` | `derived` | `derived` |
| `fund_detail` | `alive` | `derived` | `derived` |
| `fund_estimate` | `alive` | `derived` | `derived` |
| `fund_flow` | `alive` | `sina`、`eastmoney`、`derived` | `sina`、`eastmoney`、`derived` |
| `fund_flow_history` | `alive` | `derived` | `derived` |
| `fund_holdings` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_industry_distribution` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_list` | `alive` | `derived` | `derived` |
| `fund_manager` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_manager_eval` | `alive` | `derived` | `derived` |
| `fund_manager_list` | `alive` | `derived` | `derived` |
| `fund_manager_profile` | `alive` | `derived` | `derived` |
| `fund_manager_style` | `alive` | `derived` | `derived` |
| `fund_manager_yield` | `alive` | `derived` | `derived` |
| `fund_nav_history` | `alive` | `derived` | `derived` |
| `fund_nav_history_mob` | `alive` | `derived` | `derived` |
| `fund_period_change` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_public_dates` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_rank` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fund_rank_trend` | `alive` | `derived` | `derived` |
| `fund_rating` | `alive` | `derived` | `derived` |
| `fund_search` | `alive` | `derived` | `derived` |
| `fund_snapshot` | `alive` | `derived` | `derived` |
| `fund_yield_curve` | `alive` | `derived` | `derived` |
| `futures_base_info` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `futures_kline` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `futures_position_rank` | `needs_verify` | `eastmoney` | `eastmoney` |
| `futures_realtime` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `futures_trades` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `fx_rates` | `alive` | `boc` | `boc` |
| `global_quotes` | `alive` | `tencent` | `tencent` |
| `globals` | `alive` | `tencent` | `tencent` |
| `goods_bars` | `alive` | `tdx` | `tdx` |
| `goods_quotes` | `alive` | `tdx` | `tdx` |
| `goodwill` | `alive` | `eastmoney` | `eastmoney` |
| `goodwill_market` | `alive` | `eastmoney` | `eastmoney` |
| `history` | `alive` | `sina`、`eastmoney`、`derived` | `sina`、`eastmoney`、`derived` |
| `hk_announcements` | `alive` | `cninfo`、`derived` | `cninfo`、`derived` |
| `hk_quotes` | `alive` | `tencent`、`sina`、`derived` | `tencent`、`sina`、`derived` |
| `holder_changes` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `holder_num` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `holder_num_detail` | `alive` | `eastmoney` | `eastmoney` |
| `hot_boards` | `alive` | `derived` | `derived` |
| `hot_rank` | `alive` | `eastmoney`、`ths`、`derived` | `eastmoney`、`ths`、`derived` |
| `income_sheet` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `index` | `alive` | `derived` | `derived` |
| `index_constituents` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `index_list` | `alive` | `builtin` | `builtin` |
| `index_valuation` | `needs_verify` | `eastmoney` | `eastmoney` |
| `industry_board` | `alive` | `sina` | `sina` |
| `industry_boards` | `alive` | `sina` | `sina` |
| `industry_index` | `alive` | `derived` | `derived` |
| `interactive_qa` | `needs_verify` | `cninfo` | `cninfo` |
| `intraday` | `alive` | `eastmoney` | `eastmoney` |
| `ipo_calendar` | `alive` | `derived` | `derived` |
| `ipo_review` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `klines` | `alive` | `derived` | `derived` |
| `limit_pool` | `alive` | `eastmoney`、`ths`、`derived` | `eastmoney`、`ths`、`derived` |
| `limit_up_ladder` | `alive` | `derived` | `derived` |
| `longhu` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `mac_quotes` | `alive` | `tdx` | `tdx` |
| `macro_asset_invest` | `alive` | `eastmoney` | `eastmoney` |
| `macro_bond_yield` | `alive` | `eastmoney` | `eastmoney` |
| `macro_cpi` | `alive` | `derived` | `derived` |
| `macro_forex_reserve` | `alive` | `eastmoney` | `eastmoney` |
| `macro_gdp` | `alive` | `derived` | `derived` |
| `macro_industrial` | `alive` | `eastmoney` | `eastmoney` |
| `macro_loan` | `alive` | `eastmoney` | `eastmoney` |
| `macro_lpr` | `alive` | `eastmoney` | `eastmoney` |
| `macro_money_supply` | `alive` | `eastmoney` | `eastmoney` |
| `macro_pmi` | `alive` | `eastmoney` | `eastmoney` |
| `macro_ppi` | `alive` | `derived` | `derived` |
| `macro_repo_rate` | `alive` | `eastmoney` | `eastmoney` |
| `macro_reserve_ratio` | `alive` | `eastmoney` | `eastmoney` |
| `macro_retail` | `alive` | `eastmoney` | `eastmoney` |
| `macro_social_financing` | `alive` | `eastmoney` | `eastmoney` |
| `margin` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `margin_daily` | `alive` | `eastmoney` | `eastmoney` |
| `market_breadth` | `alive` | `derived` | `derived` |
| `market_stat` | `alive` | `tencent` | `tencent` |
| `minute` | `alive` | `tdx`、`tencent`、`eastmoney`、`baidu` | `tencent`、`eastmoney`、`baidu` |
| `minute_history` | `offline` | `tdx` | — |
| `minute_klines` | `alive` | `tencent`、`eastmoney` | `tencent`、`eastmoney` |
| `minute_web` | `alive` | `tencent` | `tencent` |
| `news` | `alive` | `sina`、`derived` | `sina`、`derived` |
| `news_broadcast` | `needs_verify` | `eastmoney` | `eastmoney` |
| `news_financial` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `northbound` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `northbound_hold` | `alive` | `derived` | `derived` |
| `notices` | `alive` | `derived` | `derived` |
| `options_list` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `options_position_rank` | `needs_verify` | `eastmoney` | `eastmoney` |
| `options_snapshot` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `options_trends` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `org_profile` | `alive` | `derived` | `derived` |
| `org_profiles` | `alive` | `derived` | `derived` |
| `org_seat_trades` | `alive` | `eastmoney` | `eastmoney` |
| `performance` | `alive` | `derived` | `derived` |
| `profile` | `alive` | `derived` | `derived` |
| `quotes_concurrent` | `alive` | `tdx` | `tdx` |
| `rank` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `rates` | `alive` | `boc` | `boc` |
| `rating_consensus` | `alive` | `derived` | `derived` |
| `rating_forecast` | `alive` | `derived` | `derived` |
| `reports` | `alive` | `derived` | `derived` |
| `research_reports` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `research_visits` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `rights_issue` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `risk_scan` | `alive` | `eastmoney` | `eastmoney` |
| `screening` | `alive` | `iwencai` | `iwencai` |
| `search_symbols` | `alive` | `sina` | `sina` |
| `sector_flow` | `alive` | `derived` | `derived` |
| `security_list_all` | `offline` | `tdx` | — |
| `seo` | `alive` | `eastmoney` | `eastmoney` |
| `shareholder_changes` | `alive` | `derived` | `derived` |
| `shareholders` | `alive` | `derived` | `derived` |
| `sina_board_fund_flow` | `alive` | `derived` | `derived` |
| `sina_fund_flow` | `alive` | `derived` | `derived` |
| `southbound_ranks` | `alive` | `eastmoney` | `eastmoney` |
| `st_list` | `alive` | `eastmoney` | `eastmoney` |
| `stock_all_performance` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `stock_base_info` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `stock_boards` | `alive` | `eastmoney` | `eastmoney` |
| `stock_changes` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `stock_report_dates` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `stock_valuation` | `alive` | `eastmoney`、`derived` | `eastmoney`、`derived` |
| `suggest` | `alive` | `sina` | `sina` |
| `suspension` | `alive` | `eastmoney` | `eastmoney` |
| `sw_industry` | `needs_verify` | `eastmoney` | `eastmoney` |
| `sw_industry_history` | `needs_verify` | `eastmoney` | `eastmoney` |
| `sync_daily` | `alive` | `derived` | `derived` |
| `theme_attribution` | `alive` | `ths`、`derived` | `ths`、`derived` |
| `ticks` | `alive` | `tencent` | `tencent` |
| `top_holders` | `alive` | `derived` | `derived` |
| `trade_calendar` | `alive` | `builtin` | `builtin` |
| `trades` | `alive` | `tdx`、`tencent`、`baidu` | `tencent`、`baidu` |
| `trading_calendar` | `alive` | `eastmoney` | `eastmoney` |
| `unlock_stocks` | `alive` | `derived` | `derived` |
| `unlocks` | `alive` | `derived` | `derived` |
| `us_quotes` | `alive` | `tencent` | `tencent` |
| `valuation_history` | `alive` | `eastmoney` | `eastmoney` |
| `volume_price` | `offline` | `tdx` | — |
| `wencai` | `alive` | `iwencai` | `iwencai` |

## Provider × Channel

| Provider | Channel | live | local | 周期 | 下线能力 |
|---|---|---|---|---|---|
| `tdx` | `quotation` | 是 | 否 | 15min、1min、30min、5min、60min、day、month、season、week、year | auction、block_quotes、minute、minute_history、security_list、security_list_all、trades、volume_price |
| `tdx` | `extended` | 是 | 否 | — | — |
| `tdx` | `goods` | 是 | 否 | — | — |
| `tdx` | `f10` | 否 | 否 | — | — |
| `tdx` | `mac` | 是 | 否 | — | — |
| `local_vipdoc` | `vipdoc` | 否 | 是 | 1min、5min、day | — |
| `tencent` | `quote` | 是 | 否 | — | — |
| `tencent` | `kline` | 否 | 否 | day、month、week | — |
| `tencent` | `minute_kline` | 否 | 否 | 15min、1min、30min、5min、60min | — |
| `tencent` | `minute` | 是 | 否 | — | — |
| `tencent` | `ticks` | 是 | 否 | — | — |
| `tencent` | `global` | 是 | 否 | — | — |
| `tencent` | `market_stat` | 是 | 否 | — | — |
| `tencent` | `board_rank` | 是 | 否 | — | — |
| `tencent` | `catalog` | 否 | 否 | — | — |
| `sina` | `quote` | 是 | 否 | — | — |
| `sina` | `history_kline` | 否 | 否 | 1200min、120min、15min、30min、5min、60min、day | — |
| `sina` | `suggest` | 否 | 否 | — | — |
| `sina` | `industry_board` | 否 | 否 | — | — |
| `sina` | `board_list` | 否 | 否 | — | — |
| `sina` | `board_member` | 否 | 否 | — | — |
| `sina` | `fund_flow` | 否 | 否 | — | — |
| `sina` | `news` | 否 | 否 | — | — |
| `sina` | `catalog` | 否 | 否 | — | — |
| `eastmoney` | `quote` | 是 | 否 | — | — |
| `eastmoney` | `kline` | 否 | 否 | 15min、1min、30min、5min、60min、day | — |
| `eastmoney` | `trends` | 是 | 否 | — | — |
| `eastmoney` | `rank` | 否 | 否 | — | — |
| `eastmoney` | `fund_flow` | 否 | 否 | — | — |
| `eastmoney` | `limit_pool` | 是 | 否 | — | — |
| `eastmoney` | `stock_changes` | 是 | 否 | — | — |
| `eastmoney` | `northbound` | 是 | 否 | — | — |
| `eastmoney` | `hot_rank` | 是 | 否 | — | — |
| `eastmoney` | `corporate` | 否 | 否 | — | — |
| `eastmoney` | `longhu` | 否 | 否 | — | — |
| `eastmoney` | `margin` | 否 | 否 | — | — |
| `eastmoney` | `index_constituents` | 否 | 否 | — | — |
| `eastmoney` | `fund` | 否 | 否 | — | — |
| `eastmoney` | `derivatives` | 否 | 否 | — | — |
| `eastmoney` | `datacenter` | 否 | 否 | — | — |
| `eastmoney` | `news` | 否 | 否 | — | — |
| `eastmoney` | `research` | 否 | 否 | — | — |
| `eastmoney` | `options` | 是 | 否 | — | — |
| `eastmoney` | `catalog` | 否 | 否 | — | — |
| `baidu` | `quote` | 是 | 否 | — | — |
| `baidu` | `kline` | 否 | 否 | day、month、week | — |
| `baidu` | `minute` | 是 | 否 | — | — |
| `baidu` | `ticks` | 是 | 否 | — | — |
| `baidu` | `catalog` | 否 | 否 | — | — |
| `jsl` | `bond` | 否 | 否 | — | — |
| `boc` | `fx` | 否 | 否 | — | — |
| `iwencai` | `screening` | 否 | 否 | — | — |
| `cninfo` | `catalog` | 否 | 否 | — | — |
| `ths` | `catalog` | 否 | 否 | — | — |
| `wallstreet` | `catalog` | 否 | 否 | — | — |
| `builtin` | `catalog` | 否 | 否 | — | — |
| `derived` | `catalog` | 否 | 否 | — | — |
| `derived` | `adjustment` | 否 | 否 | — | — |
| `derived` | `sync` | 否 | 否 | — | — |
| `derived` | `enriched` | 否 | 否 | — | — |
