-- ============================================================
-- 06 Cohort 留存分析（按首购月份分群）
-- ------------------------------------------------------------
-- 业务问题：按首购月份分群的客户，后续每个月的留存与复购如何？
-- 口径：
--   cohort      = 客户在该数据集内的首购月份（仅统计有 CustomerID 的客户）
--   month_age   = 交易月份 - 首购月份（0 = 首购当月）
--   留存率      = 该 cohort 在 month_age=N 仍有消费的客户数 / cohort 规模
-- 两个必须说明的边界（否则会得出错误结论）：
--   1) 左截断：2009-12 cohort 的客户并非「真新客」，只是数据从 2009-12 起算，
--      存量客户被误判为首购 → 该 cohort 规模被高估（955 人）。
--   2) 右截断：数据止于 2011-12-09。若 cohort 的 month_age 对应月份已超出数据范围，
--      留存应标为「未观测」而非 0（本查询用 is_observable 区分）。
-- 窗口函数：SUM OVER（cohort 规模回填）、ROW_NUMBER（矩阵排序）
-- ============================================================
WITH first_order AS (
    SELECT customer_id,
           MIN(invoice_year_month) AS cohort
      FROM transactions
     WHERE customer_id IS NOT NULL
     GROUP BY customer_id
),
activity AS (
    SELECT DISTINCT customer_id, invoice_year_month AS ym
      FROM transactions
     WHERE customer_id IS NOT NULL
),
bounds AS (
    SELECT MIN(invoice_year_month) AS min_ym,
           MAX(invoice_year_month) AS max_ym
      FROM transactions
),
joined AS (
    SELECT a.customer_id,
           f.cohort,
           a.ym,
           (CAST(substr(a.ym, 1, 4) AS INTEGER) * 12 + CAST(substr(a.ym, 6, 2) AS INTEGER))
         - (CAST(substr(f.cohort, 1, 4) AS INTEGER) * 12 + CAST(substr(f.cohort, 6, 2) AS INTEGER)) AS month_age
      FROM activity a
      JOIN first_order f ON f.customer_id = a.customer_id
),
cohort_size AS (
    SELECT cohort, COUNT(*) AS cohort_customers
      FROM first_order
     GROUP BY cohort
),
matrix AS (
    SELECT j.cohort,
           j.month_age,
           COUNT(DISTINCT j.customer_id) AS active_customers
      FROM joined j
     GROUP BY j.cohort, j.month_age
)
SELECT m.cohort,
       m.month_age,
       m.active_customers,
       cs.cohort_customers,
       ROUND(100.0 * m.active_customers / cs.cohort_customers, 2) AS retention_pct,
       CASE
           WHEN (CAST(substr(m.cohort, 1, 4) AS INTEGER) * 12 + CAST(substr(m.cohort, 6, 2) AS INTEGER)
                 + m.month_age)
              > (CAST(substr(b.max_ym, 1, 4) AS INTEGER) * 12 + CAST(substr(b.max_ym, 6, 2) AS INTEGER))
           THEN '未观测'
           ELSE '可观测'
       END AS observability,
       b.min_ym,
       b.max_ym,
       ROUND(AVG(100.0 * m.active_customers / cs.cohort_customers) OVER (PARTITION BY m.month_age), 2) AS avg_retention_same_age,
       ROW_NUMBER() OVER (ORDER BY m.cohort, m.month_age) AS row_no
  FROM matrix m
  JOIN cohort_size cs ON cs.cohort = m.cohort
 CROSS JOIN bounds b
 ORDER BY m.cohort, m.month_age;
