-- ============================================================
-- 01 月度收入趋势与环比/同比
-- ------------------------------------------------------------
-- 业务问题：收入趋势如何？哪个月份异常？
-- 口径：
--   净收入 = 销售收入（transactions.revenue 合计） - 退货金额（returns.revenue 合计，负数）
--   活跃天数 = 当月有交易的自然日数，用于识别「数据截断月」
--     · 2011-12 仅 8 天（数据止于 12-09）→ 环比/同比不可直接比
--     · 2009-12 与 2010-12 均止于 12-23（圣诞前最后发货日）
-- 窗口函数：LAG（环比/同比）、RANK（收入排名）
-- ============================================================
WITH base AS (
    SELECT s.ym,
           s.orders,
           s.customers,
           s.gross_revenue,
           COALESCE(r.refund_amount, 0)                AS refund_amount,
           s.gross_revenue + COALESCE(r.refund_amount, 0) AS net_revenue,
           s.aov_gross                                  AS aov,
           (SELECT COUNT(DISTINCT t.invoice_date_only)
              FROM transactions t
             WHERE t.invoice_year_month = s.ym)         AS active_days
      FROM v_monthly_sales s
      LEFT JOIN v_monthly_returns r ON r.ym = s.ym
),
calc AS (
    SELECT b.*,
           LAG(net_revenue, 1)  OVER (ORDER BY ym) AS prev_month_net,
           LAG(net_revenue, 12) OVER (ORDER BY ym) AS same_month_last_year_net,
           LAG(orders, 1)       OVER (ORDER BY ym) AS prev_month_orders,
           ROUND(100.0 * (net_revenue - LAG(net_revenue, 1) OVER (ORDER BY ym))
                 / NULLIF(LAG(net_revenue, 1) OVER (ORDER BY ym), 0), 2)  AS mom_pct,
           ROUND(100.0 * (net_revenue - LAG(net_revenue, 12) OVER (ORDER BY ym))
                 / NULLIF(LAG(net_revenue, 12) OVER (ORDER BY ym), 0), 2) AS yoy_pct,
           ROUND(100.0 * refund_amount / NULLIF(gross_revenue, 0), 2)       AS refund_rate_pct
      FROM base b
)
SELECT ym,
       orders,
       customers,
       ROUND(gross_revenue, 2)  AS gross_revenue,
       ROUND(refund_amount, 2)  AS refund_amount,
       ROUND(net_revenue, 2)    AS net_revenue,
       ROUND(aov, 2)            AS aov_gross,
       active_days,
       CASE WHEN active_days >= 20 THEN '完整月' ELSE '不完整月' END AS data_status,
       ROUND(prev_month_net, 2) AS prev_month_net,
       mom_pct,
       yoy_pct,
       refund_rate_pct,
       RANK() OVER (ORDER BY net_revenue DESC) AS net_revenue_rank
  FROM calc
 ORDER BY ym;
