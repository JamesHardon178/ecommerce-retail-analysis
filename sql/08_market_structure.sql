-- ============================================================
-- 08 市场结构：英国 vs 海外占比与同口径同比增速
-- ------------------------------------------------------------
-- 业务问题：英国以外市场占比多少？增速如何？收入是否过度依赖少数国家？
-- 关键口径处理（否则结论会错）：
--   数据止于 2011-12-09，直接比 2010 全年 vs 2011 全年会低估 2011。
--   因此统一采用「同口径年度窗口」：
--       Y2010 = 2010-01-01 ~ 2010-12-09
--       Y2011 = 2011-01-01 ~ 2011-12-09
--   两个窗口天数完全相同（各 343 天），同比才成立。
-- 窗口函数：RANK（收入排名）、SUM OVER（累计占比）、
--           LAG（同一市场同比，此处用 pivot 对比代替更直观）
-- ============================================================
WITH windowed AS (
    SELECT country,
           CASE
               WHEN invoice_date_only BETWEEN '2010-01-01' AND '2010-12-09' THEN 'Y2010'
               WHEN invoice_date_only BETWEEN '2011-01-01' AND '2011-12-09' THEN 'Y2011'
           END AS yr,
           revenue
      FROM transactions
     WHERE invoice_date_only BETWEEN '2010-01-01' AND '2011-12-09'
),
agg AS (
    SELECT country,
           ROUND(SUM(CASE WHEN yr = 'Y2010' THEN revenue END), 2) AS revenue_y2010,
           ROUND(SUM(CASE WHEN yr = 'Y2011' THEN revenue END), 2) AS revenue_y2011
      FROM windowed
     GROUP BY country
),
tot AS (
    SELECT SUM(revenue_y2010) AS total_y2010,
           SUM(revenue_y2011) AS total_y2011
      FROM agg
)
SELECT a.country,
       CASE WHEN a.country = 'United Kingdom' THEN '英国' ELSE '海外' END AS market_type,
       a.revenue_y2010,
       a.revenue_y2011,
       ROUND(100.0 * a.revenue_y2010 / t.total_y2010, 2) AS share_y2010_pct,
       ROUND(100.0 * a.revenue_y2011 / t.total_y2011, 2) AS share_y2011_pct,
       ROUND(100.0 * (a.revenue_y2011 - a.revenue_y2010)
             / NULLIF(a.revenue_y2010, 0), 2)            AS yoy_pct,
       ROUND(100.0 * (a.revenue_y2011 / t.total_y2011)
             - 100.0 * (a.revenue_y2010 / t.total_y2010), 2) AS share_change_pp,
       RANK() OVER (ORDER BY a.revenue_y2011 DESC)        AS revenue_rank,
       ROUND(100.0 * SUM(a.revenue_y2011) OVER (ORDER BY a.revenue_y2011 DESC
             ROWS UNBOUNDED PRECEDING) / t.total_y2011, 2) AS cumulative_share_pct,
       ROUND(t.total_y2010, 2) AS grand_total_y2010,
       ROUND(t.total_y2011, 2) AS grand_total_y2011
  FROM agg a
 CROSS JOIN tot t
 WHERE a.revenue_y2011 > 0
 ORDER BY revenue_rank;
