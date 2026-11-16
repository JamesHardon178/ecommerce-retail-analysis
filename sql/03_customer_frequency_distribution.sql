-- ============================================================
-- 03 客户购买频次分布与复购率
-- ------------------------------------------------------------
-- 业务问题：复购率多高？购买频次如何分布？高频客户贡献多少收入？
-- 口径：
--   购买次数 = 客户去重订单数（COUNT DISTINCT invoice_no，仅正常销售）
--   复购客户 = 购买次数 >= 2
--   复购率   = 复购客户数 / 有 CustomerID 的客户数
-- 窗口函数：SUM OVER（累计占比）、ROW_NUMBER（分桶排序）
-- ============================================================
WITH per_customer AS (
    SELECT customer_id,
           COUNT(DISTINCT invoice_no) AS order_count,
           SUM(revenue)               AS total_revenue
      FROM transactions
     WHERE customer_id IS NOT NULL
     GROUP BY customer_id
),
summary AS (
    SELECT COUNT(*)                                   AS total_customers,
           SUM(CASE WHEN order_count >= 2 THEN 1 ELSE 0 END) AS repeat_customers,
           ROUND(1.0 * SUM(CASE WHEN order_count >= 2 THEN 1 ELSE 0 END) / COUNT(*), 6) AS repeat_rate,
           ROUND(AVG(order_count), 2)                 AS avg_orders_per_customer,
           ROUND(SUM(total_revenue), 2)               AS total_revenue
      FROM per_customer
),
bucketed AS (
    SELECT CASE
               WHEN order_count = 1  THEN '01. 1 次（未复购）'
               WHEN order_count = 2  THEN '02. 2 次'
               WHEN order_count = 3  THEN '03. 3 次'
               WHEN order_count <= 5 THEN '04. 4-5 次'
               WHEN order_count <= 10 THEN '05. 6-10 次'
               WHEN order_count <= 20 THEN '06. 11-20 次'
               ELSE                       '07. 20 次以上'
           END                          AS freq_bucket,
           CASE
               WHEN order_count = 1  THEN 1
               WHEN order_count = 2  THEN 2
               WHEN order_count = 3  THEN 3
               WHEN order_count <= 5 THEN 4
               WHEN order_count <= 10 THEN 5
               WHEN order_count <= 20 THEN 6
               ELSE 7
           END                          AS bucket_order,
           customer_id,
           total_revenue
      FROM per_customer
),
agg AS (
    SELECT freq_bucket,
           bucket_order,
           COUNT(*)                     AS customers,
           ROUND(SUM(total_revenue), 2) AS revenue,
           ROUND(AVG(total_revenue), 2) AS arpu,
           ROUND(AVG(total_revenue) /
                 NULLIF((SELECT AVG(total_revenue) FROM per_customer), 0), 2) AS arpu_index
      FROM bucketed
     GROUP BY freq_bucket, bucket_order
)
SELECT a.freq_bucket,
       a.customers,
       ROUND(100.0 * a.customers / s.total_customers, 2) AS customer_pct,
       ROUND(100.0 * SUM(a.customers) OVER (ORDER BY a.bucket_order
             ROWS UNBOUNDED PRECEDING) / s.total_customers, 2) AS cumulative_customer_pct,
       a.revenue,
       ROUND(100.0 * a.revenue / s.total_revenue, 2)     AS revenue_pct,
       a.arpu,
       a.arpu_index,
       ROW_NUMBER() OVER (ORDER BY a.revenue DESC)        AS revenue_rank,
       s.total_customers,
       s.repeat_customers,
       ROUND(100.0 * s.repeat_rate, 2)                    AS repeat_rate_pct,
       s.avg_orders_per_customer,
       ROUND(s.total_revenue, 2)                          AS total_customer_revenue
  FROM agg a
 CROSS JOIN summary s
 ORDER BY a.bucket_order;
