-- ============================================================
-- 04 RFM 客户价值分层
-- ------------------------------------------------------------
-- 业务问题：客户如何按价值分层？高价值客户贡献多少收入？
-- 打分方法（1-5 分，NTILE 五等分，行数均分）：
--   R（Recency）  = 距数据截止日 +1 的交易间隔天数，越近分越高
--   F（Frequency）= 去重订单数，越多分越高
--   M（Monetary） = 累计消费金额，越多分越高
-- 分层规则（>=4 为高，<=3 为低）→ 标准 8 类
-- 说明：NTILE 是行数均分，与 pandas.qcut 的分位点切分略有差异（可差 1 个客户），
--       属实现差异，交叉验证时以「分层收入占比」为准。
-- ============================================================
WITH ref AS (
    SELECT julianday(MAX(invoice_date_only)) + 1 AS ref_day FROM transactions
),
base AS (
    SELECT t.customer_id,
           COUNT(DISTINCT t.invoice_no) AS frequency,
           SUM(t.revenue)               AS monetary,
           CAST((SELECT ref_day FROM ref) - julianday(MAX(t.invoice_date_only)) AS INTEGER) AS recency
      FROM transactions t
     WHERE t.customer_id IS NOT NULL
     GROUP BY t.customer_id
),
scored AS (
    SELECT customer_id, frequency, monetary, recency,
           NTILE(5) OVER (ORDER BY recency DESC) AS r_score,  -- 越久未买分数越低
           NTILE(5) OVER (ORDER BY frequency)    AS f_score,
           NTILE(5) OVER (ORDER BY monetary)     AS m_score
      FROM base
),
labeled AS (
    SELECT s.*,
           r_score || '-' || f_score || '-' || m_score AS rfm_code,
           CASE
               WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN '重要价值客户'
               WHEN r_score >= 4 AND f_score  < 4 AND m_score >= 4 THEN '重要发展客户'
               WHEN r_score  < 4 AND f_score >= 4 AND m_score >= 4 THEN '重要保持客户'
               WHEN r_score  < 4 AND f_score  < 4 AND m_score >= 4 THEN '重要挽留客户'
               WHEN r_score >= 4 AND f_score >= 4 AND m_score  < 4 THEN '一般价值客户'
               WHEN r_score >= 4 AND f_score  < 4 AND m_score  < 4 THEN '一般发展客户'
               WHEN r_score  < 4 AND f_score >= 4 AND m_score  < 4 THEN '一般保持客户'
               ELSE '流失客户'
           END AS segment
      FROM scored s
),
tot AS (SELECT COUNT(*) n, SUM(monetary) rev FROM base)
SELECT l.segment,
       COUNT(*)                                                AS customers,
       ROUND(100.0 * COUNT(*) / t.n, 2)                        AS customer_pct,
       ROUND(SUM(l.monetary), 2)                               AS revenue,
       ROUND(100.0 * SUM(l.monetary) / t.rev, 2)               AS revenue_pct,
       ROUND(AVG(l.monetary), 2)                               AS arpu,
       ROUND(AVG(l.frequency), 2)                              AS avg_orders,
       ROUND(AVG(l.recency), 0)                                AS avg_recency_days,
       ROUND(SUM(l.monetary) / (SELECT SUM(monetary) FROM base), 6) AS revenue_share_raw,
       RANK() OVER (ORDER BY SUM(l.monetary) DESC)             AS revenue_rank
  FROM labeled l
 CROSS JOIN tot t
 GROUP BY l.segment
 ORDER BY revenue DESC;
