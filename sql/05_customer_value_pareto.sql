-- ============================================================
-- 05 客户收入帕累托（高价值客户集中度）
-- ------------------------------------------------------------
-- 业务问题：收入是否集中在少数客户？前 1% / 10% / 20% 客户各贡献多少？
-- 口径：按客户累计消费金额降序排名，计算累计收入占比
-- 窗口函数：ROW_NUMBER（客户排名）、SUM OVER（累计占比）
-- ============================================================
WITH per_customer AS (
    SELECT customer_id,
           COUNT(DISTINCT invoice_no) AS order_count,
           SUM(revenue)               AS total_revenue
      FROM transactions
     WHERE customer_id IS NOT NULL
     GROUP BY customer_id
),
ranked AS (
    SELECT customer_id,
           order_count,
           total_revenue,
           ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS revenue_rank,
           COUNT(*)     OVER ()                            AS total_customers,
           SUM(total_revenue) OVER ()                      AS grand_total,
           ROUND(100.0 * total_revenue / SUM(total_revenue) OVER (), 4) AS revenue_pct,
           ROUND(100.0 * SUM(total_revenue) OVER (ORDER BY total_revenue DESC
                 ROWS UNBOUNDED PRECEDING) / SUM(total_revenue) OVER (), 2) AS cumulative_revenue_pct,
           ROUND(100.0 * ROW_NUMBER() OVER (ORDER BY total_revenue DESC) / COUNT(*) OVER (), 2) AS cumulative_customer_pct
      FROM per_customer
)
SELECT revenue_rank,
       customer_id,
       order_count,
       ROUND(total_revenue, 2)       AS total_revenue,
       revenue_pct,
       cumulative_revenue_pct,
       cumulative_customer_pct,
       total_customers,
       ROUND(grand_total, 2)         AS grand_total,
       CASE
           WHEN cumulative_customer_pct <= 1  THEN 'P1（前 1%）'
           WHEN cumulative_customer_pct <= 5  THEN 'P5（前 5%）'
           WHEN cumulative_customer_pct <= 10 THEN 'P10（前 10%）'
           WHEN cumulative_customer_pct <= 20 THEN 'P20（前 20%）'
           WHEN cumulative_customer_pct <= 50 THEN 'P50（前 50%）'
           ELSE 'P100（后 50%）'
       END AS pareto_band
  FROM ranked
 ORDER BY revenue_rank;
