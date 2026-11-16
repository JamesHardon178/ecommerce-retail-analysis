-- ============================================================
-- 02 收入异常归因：订单量 / 客单价 / 退款 三因素拆解
-- ------------------------------------------------------------
-- 业务问题：异常月份的收入波动由什么导致？
-- 方法：乘法分解 ΔNet = Δ(订单数 × 客单价) - Δ退款
--   订单量效应 = (本期订单数 - 上期订单数) × 上期客单价
--   客单价效应 = 本期订单数 × (本期客单价 - 上期客单价)
--   退款效应   = -(本期退款 - 上期退款)        -- 退款为负值，取反表示对净收入的拖累
--   三项之和 ≈ ΔNet（残差项单独列出，保证可核对）
-- 异常判定：|环比| >= 25% 记为异常月
-- 窗口函数：LAG（本期 vs 上期）、NTILE（净收入分档）
-- ============================================================
WITH base AS (
    SELECT s.ym,
           s.orders,
           s.customers,
           s.aov_gross                                       AS aov,
           s.gross_revenue + COALESCE(r.refund_amount, 0)    AS net_revenue,
           COALESCE(r.refund_amount, 0)                      AS refund_amount,
           (SELECT COUNT(DISTINCT t.invoice_date_only)
              FROM transactions t WHERE t.invoice_year_month = s.ym) AS active_days
      FROM v_monthly_sales s
      LEFT JOIN v_monthly_returns r ON r.ym = s.ym
),
lagged AS (
    SELECT b.*,
           LAG(net_revenue)   OVER (ORDER BY ym) AS p_net,
           LAG(orders)        OVER (ORDER BY ym) AS p_orders,
           LAG(aov)           OVER (ORDER BY ym) AS p_aov,
           LAG(customers)     OVER (ORDER BY ym) AS p_customers,
           LAG(refund_amount) OVER (ORDER BY ym) AS p_refund
      FROM base b
),
decomposed AS (
    SELECT l.*,
           net_revenue - p_net                                            AS delta_net,
           (orders - p_orders) * p_aov                                    AS order_effect,
           orders * (aov - p_aov)                                         AS aov_effect,
           -(refund_amount - p_refund)                                    AS refund_effect,
           customers - p_customers                                        AS delta_customers,
           ROUND(100.0 * (net_revenue - p_net) / NULLIF(p_net, 0), 2)     AS mom_pct
      FROM lagged l
)
SELECT ym,
       ROUND(net_revenue, 2)  AS net_revenue,
       ROUND(p_net, 2)        AS prev_net_revenue,
       ROUND(delta_net, 2)    AS delta_net,
       mom_pct,
       orders,
       p_orders               AS prev_orders,
       ROUND(order_effect, 2) AS order_effect,
       ROUND(aov, 2)          AS aov,
       ROUND(p_aov, 2)        AS prev_aov,
       ROUND(aov_effect, 2)   AS aov_effect,
       ROUND(refund_effect, 2) AS refund_effect,
       ROUND(delta_net - (order_effect + aov_effect + refund_effect), 2) AS residual,
       customers,
       delta_customers,
       active_days,
       CASE
           WHEN p_net IS NULL                       THEN '首月'
           WHEN active_days < 20                    THEN '数据不完整（截止日效应）'
           WHEN ABS(mom_pct) >= 25                  THEN '异常月'
           ELSE '正常波动'
       END AS anomaly_flag,
       CASE
           WHEN p_net IS NULL OR active_days < 20 THEN NULL
           WHEN ABS(order_effect) >= ABS(aov_effect) AND ABS(order_effect) >= ABS(refund_effect) THEN '订单量驱动'
           WHEN ABS(aov_effect)   >= ABS(order_effect) AND ABS(aov_effect)   >= ABS(refund_effect) THEN '客单价驱动'
           ELSE '退款驱动'
       END AS dominant_driver,
       NTILE(4) OVER (ORDER BY net_revenue) AS revenue_quartile
  FROM decomposed
 ORDER BY ym;
