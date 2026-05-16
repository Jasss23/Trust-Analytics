-- Model: fct_trading_daily
-- Owner: data_team
-- Managed by: dbt + Airflow
-- Description: Daily trading summary aggregated from raw_transactions.
--              One row per transaction_date and asset_class combination.

SELECT
    transaction_date,
    asset_class,
    SUM(amount_idr)                               AS gtv_idr,
    SUM(amount_usd)                               AS gtv_usd,
    ROUND(SUM(amount_idr) / SUM(amount_usd), 1)   AS implied_fx_rate,
    COUNT(*)                                      AS transaction_count,
    COUNT(DISTINCT user_id)                       AS unique_traders
FROM raw_transactions
WHERE status = 'completed'
GROUP BY 1, 2
ORDER BY 1, 2
