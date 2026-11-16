-- ============================================================
-- 07 退货/取消订单影响分析（商品 / 国家 / 客户 三维度）
-- ------------------------------------------------------------
-- 业务问题：退货集中在哪些商品、国家、客户？对收入影响多少？
-- 口径：退款金额取 returns.revenue（恒为负），故按 ASC 排序取「最负」= 退款最多
-- 说明：退货率 = |退款金额| / 销售收入。整体退货率在 core_metrics.json 中为
--       7.14%，但高度受单笔异常退货影响（客户 16446 / SKU 23843，£168,470 单笔）。
-- 窗口函数：RANK（各维度内部排名）、SUM OVER（维度内占比分母）、
--           ROW_NUMBER（异常单笔定位）
-- ============================================================
WITH r AS (
    SELECT * FROM returns
),
dim AS (
    SELECT '商品' AS dimension,
           stock_code AS item_key,
           COALESCE(MAX(description), '(无描述)') AS item_name,
           ROUND(SUM(revenue), 2)  AS refund_amount,
           COUNT(*)                AS return_lines,
           COUNT(DISTINCT invoice_no) AS return_orders
      FROM r
     GROUP BY stock_code
    UNION ALL
    SELECT '国家',
           country,
           country,
           ROUND(SUM(revenue), 2),
           COUNT(*),
           COUNT(DISTINCT invoice_no)
      FROM r
     GROUP BY country
    UNION ALL
    SELECT '客户',
           COALESCE(CAST(customer_id AS TEXT), '匿名客户'),
           COALESCE(CAST(customer_id AS TEXT), '匿名客户'),
           ROUND(SUM(revenue), 2),
           COUNT(*),
           COUNT(DISTINCT invoice_no)
      FROM r
     GROUP BY customer_id
),
ranked AS (
    SELECT d.*,
           SUM(refund_amount) OVER (PARTITION BY dimension) AS dimension_total,
           RANK()  OVER (PARTITION BY dimension ORDER BY refund_amount ASC) AS rk,
           COUNT(*) OVER (PARTITION BY dimension) AS dimension_items
      FROM dim d
)
SELECT dimension,
       rk,
       item_key,
       item_name,
       refund_amount,
       ROUND(100.0 * refund_amount / dimension_total, 2) AS pct_of_dimension,
       return_lines,
       return_orders,
       ROUND(dimension_total, 2)  AS dimension_total,
       dimension_items
  FROM ranked
 WHERE rk <= 15
 ORDER BY dimension, rk;
