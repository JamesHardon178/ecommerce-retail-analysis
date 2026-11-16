-- ============================================================
-- 09 商品集中度：TOP 商品贡献与 HHI 指数
-- ------------------------------------------------------------
-- 业务问题：收入是否过度依赖少数商品？
-- 口径：
--   排除 is_special_code = 1 的运费/手工调整类编码（POST/M/BANK CHARGES/AMAZONFEE 等），
--   否则「POST 邮费」会挤进 TOP 商品榜，掩盖真实商品结构。
--   HHI（赫芬达尔指数）= Σ(单品收入份额²) × 10000
--     < 1500  低集中度（竞争充分/长尾）
--     1500-2500 中度集中
--     > 2500  高度集中（依赖少数单品）
--   CRn = 前 n 个商品合计收入占比
-- 窗口函数：RANK、SUM OVER（累计占比）
-- ============================================================
WITH product AS (
    SELECT stock_code,
           COALESCE(MAX(description), '(无描述)') AS description,
           SUM(revenue)            AS revenue,
           SUM(quantity)           AS units,
           COUNT(DISTINCT invoice_no) AS orders,
           COUNT(DISTINCT customer_id) AS customers
      FROM transactions
     WHERE is_special_code = 0
     GROUP BY stock_code
),
tot AS (
    SELECT SUM(revenue) AS total_revenue,
           SUM(revenue * revenue) AS sum_sq
      FROM product
),
ranked AS (
    SELECT p.*,
           RANK() OVER (ORDER BY p.revenue DESC) AS revenue_rank,
           ROUND(100.0 * p.revenue / t.total_revenue, 4) AS revenue_pct,
           ROUND(100.0 * SUM(p.revenue) OVER (ORDER BY p.revenue DESC
                 ROWS UNBOUNDED PRECEDING) / t.total_revenue, 2) AS cumulative_revenue_pct,
           ROUND(100.0 * SUM(p.revenue) OVER (ORDER BY p.revenue DESC
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 3 PRECEDING) / t.total_revenue, 2) AS cumulative_before_self_pct,
           ROUND(10000.0 * t.sum_sq / (t.total_revenue * t.total_revenue), 1) AS hhi_index,
           (SELECT COUNT(*) FROM product) AS total_products,
           ROUND(t.total_revenue, 2) AS total_revenue
      FROM product p
     CROSS JOIN tot t
)
SELECT revenue_rank,
       stock_code,
       description,
       ROUND(revenue, 2) AS revenue,
       units,
       orders,
       customers,
       revenue_pct,
       cumulative_revenue_pct,
       cumulative_before_self_pct,
       hhi_index,
       total_products,
       total_revenue,
       CASE
           WHEN hhi_index < 1500 THEN '低集中度（长尾健康）'
           WHEN hhi_index < 2500 THEN '中度集中'
           ELSE '高度集中（单品依赖风险）'
       END AS concentration_level
  FROM ranked
 WHERE revenue_rank <= 30
 ORDER BY revenue_rank;
