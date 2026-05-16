-- Model: agg_monthly_biz_summary
-- Owner: data_team
-- Managed by: BigQuery Scheduled Query (not in dbt)
-- Description: Monthly business KPI summary used in the RevOps dashboard.
--              One row per month and asset_class, plus a Total row per month.

WITH trading AS (
    SELECT
        DATE_TRUNC(transaction_date, MONTH)       AS month,
        asset_class,
        SUM(gtv_idr)                              AS gtv_idr,
        SUM(gtv_usd)                              AS gtv_usd,
        SUM(transaction_count)                    AS transaction_count
    FROM fct_trading_daily
    GROUP BY 1, 2
),
mtu AS (
    SELECT
        DATE_TRUNC(month, MONTH)                  AS month,
        COUNT(DISTINCT user_id)                   AS mtu
    FROM aum_monthly_snapshot
    WHERE aum_idr > 0
    GROUP BY 1
)
SELECT
    t.month,
    t.asset_class,
    t.gtv_idr,
    t.gtv_usd,
    t.transaction_count,
    NULL                                          AS mtu
FROM trading AS t

UNION ALL

SELECT
    t.month,
    'Total'                                       AS asset_class,
    SUM(t.gtv_idr),
    SUM(t.gtv_usd),
    SUM(t.transaction_count),
    m.mtu
FROM trading AS t
LEFT JOIN mtu AS m
    ON t.month = m.month
GROUP BY 1, 6

ORDER BY 1, 2
