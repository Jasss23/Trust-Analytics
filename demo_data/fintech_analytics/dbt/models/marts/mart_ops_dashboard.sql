-- Model: mart_ops_dashboard
-- Owner: ops_team
-- Managed by: Ops team SQL (not in dbt)
-- Description: Monthly summary used by the Ops team for operational reporting.
--              One row per month and asset_class, plus a Total row per month.

WITH trading AS (
    SELECT
        DATE_TRUNC(transaction_date, MONTH)       AS month,
        asset_class,
        COUNT(*)                                  AS total_transactions,
        SUM(amount_idr)                           AS gtv_idr,
        ROUND(SUM(amount_idr) / 15000, 2)         AS gtv_usd_reported
    FROM raw_transactions
    WHERE status != 'failed'
    GROUP BY 1, 2
),
mixpanel_mtu AS (
    SELECT
        DATE_TRUNC(event_timestamp, MONTH)        AS month,
        asset_class,
        COUNT(DISTINCT user_id)                   AS mtu_mixpanel
    FROM stg_mixpanel_events
    WHERE mixpanel_event = 'trade_executed'
    GROUP BY 1, 2
),
mixpanel_mtu_total AS (
    SELECT
        DATE_TRUNC(event_timestamp, MONTH)        AS month,
        COUNT(DISTINCT user_id)                   AS mtu_mixpanel
    FROM stg_mixpanel_events
    WHERE mixpanel_event = 'trade_executed'
    GROUP BY 1
)
SELECT
    t.month,
    t.asset_class,
    t.total_transactions,
    t.gtv_idr,
    t.gtv_usd_reported,
    m.mtu_mixpanel
FROM trading AS t
LEFT JOIN mixpanel_mtu AS m
    ON  t.month       = m.month
    AND t.asset_class = m.asset_class

UNION ALL

SELECT
    t.month,
    'Total'                                       AS asset_class,
    SUM(t.total_transactions),
    SUM(t.gtv_idr),
    ROUND(SUM(t.gtv_idr) / 15000, 2),
    mt.mtu_mixpanel
FROM trading AS t
LEFT JOIN mixpanel_mtu_total AS mt
    ON t.month = mt.month
GROUP BY 1, 6

ORDER BY 1, 2
