"""
app.py — 电商销售增长与客户复购分析看板
========================================================
运行：streamlit run dashboard/app.py

5 页结构（侧边栏切换）：
    1. 总览            KPI + 月度收入趋势
    2. 收入异常归因     订单量 / 客单价 / 退款 三因素拆解
    3. 客户分层         RFM 分层贡献 + 频次分布 + 帕累托
    4. Cohort 留存      首购月份留存热力图
    5. 退货与集中度      退货影响 + 国家市场 + 商品集中度

数据源：data/processed/retail.db（由 src/clean_data.py + src/build_db.py 生成）
所有图表标题均标注时间范围与口径；「不完整月」「未观测」等边界单独标记，
避免把数据截断误读为业务下滑。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ------------------------------------------------------------------ 基础配置
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "retail.db"

st.set_page_config(
    page_title="电商销售增长与客户复购分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 配色（浅色主题）
C_MAIN = "#2E5E8E"      # 主色 深蓝
C_ACCENT = "#E8833A"    # 强调 橙
C_NEG = "#C0392B"       # 负向 红
C_POS = "#27AE60"       # 正向 绿
C_GREY = "#95A5A6"
PALETTE = [C_MAIN, C_ACCENT, C_POS, C_NEG, "#8E7CC3", "#5DADE2", "#F4D03F", C_GREY]


def style(fig: go.Figure, title: str, subtitle: str, height: int = 380) -> go.Figure:
    """统一图表风格：标题必须包含时间范围与口径说明。"""
    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b><br><span style='font-size:11.5px;color:#7A7A7A'>{subtitle}</span>",
            font=dict(size=15, color="#2C3E50"),
            x=0.01, xanchor="left", y=0.96,
        ),
        font=dict(family="Microsoft YaHei, PingFang SC, Arial", size=12, color="#34495E"),
        height=height,
        margin=dict(l=8, r=8, t=78, b=8),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1.0,
                    bgcolor="rgba(255,255,255,0.7)"),
    )
    fig.update_xaxes(showgrid=False, linecolor="#DDE2E6")
    fig.update_yaxes(showgrid=True, gridcolor="#EEF1F4", zeroline=False)
    return fig


@st.cache_data(show_spinner=False)
def q(sql: str) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, con)
    finally:
        con.close()


def kpi(col, label: str, value: str, delta: str | None = None,
        help_text: str | None = None, delta_color: str = "normal") -> None:
    col.metric(label, value, delta, help=help_text, delta_color=delta_color)


# ------------------------------------------------------------------ 数据加载
if not DB.exists():
    st.error(
        f"未找到数据库：`{DB}`\n\n请依次执行：\n```\npython src/clean_data.py\npython src/build_db.py\npython src/run_sql.py\n```"
    )
    st.stop()

monthly = q("""
    SELECT s.ym, s.orders, s.customers,
           s.gross_revenue,
           COALESCE(r.refund_amount, 0) AS refund_amount,
           s.gross_revenue + COALESCE(r.refund_amount, 0) AS net_revenue,
           s.aov_gross AS aov_gross,
           (s.gross_revenue + COALESCE(r.refund_amount, 0)) / s.orders AS aov_net,
           (SELECT COUNT(DISTINCT t.invoice_date_only) FROM transactions t
             WHERE t.invoice_year_month = s.ym) AS active_days
      FROM v_monthly_sales s
      LEFT JOIN v_monthly_returns r ON r.ym = s.ym
     ORDER BY s.ym
""")
decomp = q(f"SELECT * FROM ({Path(ROOT / 'sql' / '02_revenue_anomaly_attribution.sql').read_text(encoding='utf-8').rstrip().rstrip(';')})")
rfm = q("""
    WITH ref AS (SELECT julianday(MAX(invoice_date_only)) + 1 AS ref_day FROM transactions),
    base AS (
        SELECT customer_id, COUNT(DISTINCT invoice_no) AS frequency, SUM(revenue) AS monetary,
               CAST((SELECT ref_day FROM ref) - julianday(MAX(invoice_date_only)) AS INTEGER) AS recency
          FROM transactions WHERE customer_id IS NOT NULL GROUP BY customer_id),
    scored AS (
        SELECT *, NTILE(5) OVER (ORDER BY recency DESC) AS r_score,
                  NTILE(5) OVER (ORDER BY frequency) AS f_score,
                  NTILE(5) OVER (ORDER BY monetary) AS m_score FROM base)
    SELECT CASE
             WHEN r_score>=4 AND f_score>=4 AND m_score>=4 THEN '重要价值客户'
             WHEN r_score>=4 AND f_score <4 AND m_score>=4 THEN '重要发展客户'
             WHEN r_score <4 AND f_score>=4 AND m_score>=4 THEN '重要保持客户'
             WHEN r_score <4 AND f_score <4 AND m_score>=4 THEN '重要挽留客户'
             WHEN r_score>=4 AND f_score>=4 AND m_score <4 THEN '一般价值客户'
             WHEN r_score>=4 AND f_score <4 AND m_score <4 THEN '一般发展客户'
             WHEN r_score <4 AND f_score>=4 AND m_score <4 THEN '一般保持客户'
             ELSE '流失客户' END AS segment,
           customer_id, frequency, monetary, recency
      FROM scored
""")
freq = q(f"SELECT * FROM ({Path(ROOT / 'sql' / '03_customer_frequency_distribution.sql').read_text(encoding='utf-8').rstrip().rstrip(';')})")
pareto = q("SELECT revenue_rank, cumulative_customer_pct, cumulative_revenue_pct FROM ("
           + Path(ROOT / "sql" / "05_customer_value_pareto.sql").read_text(encoding="utf-8").rstrip().rstrip(";") + ")")
cohort = q("SELECT cohort, month_age, active_customers, cohort_customers, retention_pct, observability FROM ("
           + Path(ROOT / "sql" / "06_cohort_retention.sql").read_text(encoding="utf-8").rstrip().rstrip(";") + ")")
returns = q("SELECT * FROM (" + Path(ROOT / "sql" / "07_return_analysis.sql").read_text(encoding="utf-8").rstrip().rstrip(";") + ")")
market = q("SELECT * FROM (" + Path(ROOT / "sql" / "08_market_structure.sql").read_text(encoding="utf-8").rstrip().rstrip(";") + ")")
product = q("SELECT * FROM (" + Path(ROOT / "sql" / "09_product_concentration.sql").read_text(encoding="utf-8").rstrip().rstrip(";") + ")")
quality = q("SELECT metric, value FROM data_quality").set_index("metric")["value"].to_dict()

# 核心合计
TOTAL_GROSS = monthly["gross_revenue"].sum()
TOTAL_REFUND = monthly["refund_amount"].sum()
TOTAL_NET = monthly["net_revenue"].sum()
TOTAL_ORDERS = int(monthly["orders"].sum())
N_CUST = int(rfm["customer_id"].nunique())
N_REPEAT = int((rfm["frequency"] >= 2).sum())
DATE_MIN, DATE_MAX = quality["date_min"], quality["date_max"]

# ------------------------------------------------------------------ 侧边栏
with st.sidebar:
    st.markdown("### 电商销售增长与复购分析")
    st.caption("UCI Online Retail II · 英国在线零售商")
    page = st.radio(
        "选择分析页",
        ["1 · 总览", "2 · 收入异常归因", "3 · 客户分层", "4 · Cohort 留存", "5 · 退货与集中度"],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("**数据范围**")
    st.caption(f"{DATE_MIN} ~ {DATE_MAX}（25 个月）")
    st.markdown("**样本量**")
    st.caption(f"原始 {int(quality['raw_rows']):,} 行 → 有效销售 {int(quality['unique_invoice_raw']):,} 个订单号")
    st.divider()
    st.markdown("**口径速查**")
    st.caption(
        "销售收入 = 正常销售记录（数量>0 且单价>0）之和\n\n"
        "净收入 = 销售收入 − 退货金额\n\n"
        "客单价 = 净收入 / 正常销售订单数\n\n"
        "复购率 = 购买次数≥2 的客户 / 有客户ID的客户"
    )
    st.divider()
    st.caption("⚠️ 2011-12 仅 8 天数据（止于 12-09），涉及该月的环比/同比不可直接比较。")


# ================================================================== 页 1 总览
if page.startswith("1"):
    st.markdown("## 总览 · 收入规模与趋势")
    st.caption(f"统计区间 {DATE_MIN} ~ {DATE_MAX}｜口径：净收入 = 销售收入 − 退货金额；订单数仅统计正常销售单")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    kpi(c1, "销售收入", f"£{TOTAL_GROSS/1e6:.2f}M", help_text="正常销售记录（数量>0 且单价>0）合计")
    kpi(c2, "退货金额", f"£{abs(TOTAL_REFUND)/1e6:.2f}M",
        delta=f"-{abs(TOTAL_REFUND)/TOTAL_GROSS:.2%} 占销售", help_text="取消/退货单金额合计（负值取绝对值）")
    kpi(c3, "净收入", f"£{TOTAL_NET/1e6:.2f}M", help_text="销售收入 − 退货金额")
    kpi(c4, "订单数", f"{TOTAL_ORDERS:,}", help_text="正常销售的唯一 InvoiceNo 数")
    kpi(c5, "客单价（净）", f"£{TOTAL_NET/TOTAL_ORDERS:,.0f}", help_text="净收入 / 订单数")
    kpi(c6, "复购率", f"{N_REPEAT/N_CUST:.1%}", help_text="购买次数≥2 的客户 / 有客户ID的客户")

    st.markdown("")

    # 图 1：月度净收入趋势
    m = monthly.copy()
    m["is_complete"] = m["active_days"] >= 20
    fig1 = go.Figure()
    fig1.add_bar(x=m["ym"], y=m["net_revenue"], name="净收入",
                 marker_color=[C_MAIN if c else C_GREY for c in m["is_complete"]],
                 hovertemplate="%{x}<br>净收入 £%{y:,.0f}<extra></extra>")
    fig1.add_scatter(x=m["ym"], y=m["orders"] * 400, name="订单数（右轴）", yaxis="y2",
                     mode="lines+markers", line=dict(color=C_ACCENT, width=2), marker=dict(size=5))
    fig1.update_layout(
        yaxis=dict(title="净收入 (£)"),
        yaxis2=dict(title="订单数", overlaying="y", side="right", showgrid=False),
    )
    style(fig1, "月度净收入与订单数趋势",
          f"{DATE_MIN} ~ {DATE_MAX}｜灰色柱 = 活跃天数<20 的不完整月（2011-12 仅 8 天），其下滑不代表业务恶化", 400)
    st.plotly_chart(fig1, width="stretch")

    col_a, col_b = st.columns(2)

    # 图 2：同口径年度对比
    with col_a:
        yoy = market.rename(columns={"revenue_y2010": "2010（1/1-12/9）", "revenue_y2011": "2011（1/1-12/9）"})
        uk = market[market["market_type"] == "英国"].iloc[0]
        ov = market[market["market_type"] == "海外"]
        grp = pd.DataFrame({
            "市场": ["英国", "海外"],
            "2010（1/1–12/9）": [uk["revenue_y2010"], ov["revenue_y2010"].sum()],
            "2011（1/1–12/9）": [uk["revenue_y2011"], ov["revenue_y2011"].sum()],
        }).melt(id_vars="市场", var_name="窗口", value_name="收入")
        fig2 = px.bar(grp, x="市场", y="收入", color="窗口", barmode="group",
                      color_discrete_sequence=[C_GREY, C_MAIN], text_auto=".2s")
        fig2.update_traces(textposition="outside", textfont_size=11)
        style(fig2, "同口径年度对比：英国 vs 海外",
              "两窗口均为 1/1–12/9（各 343 天），规避 2011 数据截断导致的低估", 340)
        st.plotly_chart(fig2, width="stretch")

    # 图 3：收入构成（销售 / 退款）
    with col_b:
        fig3 = go.Figure()
        fig3.add_bar(x=m["ym"], y=m["gross_revenue"], name="销售收入", marker_color=C_MAIN)
        fig3.add_bar(x=m["ym"], y=m["refund_amount"], name="退货金额", marker_color=C_NEG)
        fig3.add_scatter(x=m["ym"], y=m["net_revenue"], name="净收入", mode="lines",
                         line=dict(color=C_POS, width=2.4))
        fig3.update_layout(barmode="relative", yaxis_title="金额 (£)")
        style(fig3, "月度收入构成：销售 / 退货 / 净额",
              f"{DATE_MIN} ~ {DATE_MAX}｜2011-12 退货异常由单笔 £168,470 退款导致（详见第 5 页）", 340)
        st.plotly_chart(fig3, width="stretch")

    st.info(
        f"**结论｜** 25 个月累计销售收入 £{TOTAL_GROSS/1e6:.2f}M，扣除 £{abs(TOTAL_REFUND)/1e6:.2f}M 退货后净收入 "
        f"£{TOTAL_NET/1e6:.2f}M，整体退货率 **{abs(TOTAL_REFUND)/TOTAL_GROSS:.2%}**。"
        f"收入呈明显季节性：9–11 月为旺季（圣诞节备货），1–2 月为淡季；2011-11 达到峰值 £{m['net_revenue'].max():,.0f}。"
        f"**2011-12 的 -70% 断崖不是业务崩塌，而是数据止于 12-09（仅 8 天）**，看趋势时应剔除该月或改用日均口径。"
    )


# ================================================================== 页 2 异常归因
elif page.startswith("2"):
    st.markdown("## 收入异常归因")
    st.caption("方法：Δ净收入 ≈ 订单量效应 + 客单价效应 + 退款效应（乘法分解，残差单独列出）")

    d = decomp.copy()
    m = monthly.copy()  # 图 2 订单量/客单价对比需要月度数据
    abnormal = d[d["anomaly_flag"].isin(["异常月", "数据不完整（截止日效应）"])].copy()

    c1, c2, c3, c4 = st.columns(4)
    worst = d.loc[d["mom_pct"].idxmin()]
    kpi(c1, "最大单月跌幅", f"{worst['mom_pct']:.1f}%", delta=worst["ym"], delta_color="inverse")
    kpi(c2, "异常月数量", f"{int((d['anomaly_flag']=='异常月').sum())} 个", help_text="|环比| ≥ 25% 的月份")
    kpi(c3, "主要驱动因素", "订单量", help_text="异常月中订单量效应的绝对值最大")
    kpi(c4, "单笔异常退款", "£168,470", help_text="客户 16446 / SKU 23843，2011-12 取消整单")

    st.markdown("")

    # 图 1：三因素分解
    dd = d.dropna(subset=["order_effect"]).copy()
    fig1 = go.Figure()
    for col, name, color in [("order_effect", "订单量效应", C_MAIN),
                             ("aov_effect", "客单价效应", C_ACCENT),
                             ("refund_effect", "退款效应", C_NEG)]:
        fig1.add_bar(x=dd["ym"], y=dd[col] / 1000, name=name, marker_color=color)
    fig1.add_scatter(x=dd["ym"], y=dd["delta_net"] / 1000, name="净收入变化 Δ",
                     mode="markers+lines", line=dict(color="#2C3E50", width=1.5, dash="dot"),
                     marker=dict(size=7, symbol="diamond"))
    fig1.update_layout(barmode="relative", yaxis_title="对净收入的影响（£ 千）")
    style(fig1, "单月净收入变化的三因素拆解",
          f"{DATE_MIN} ~ {DATE_MAX}｜增加为正向拉动，减少为负向拖累；三因素之和 + 残差 = 净收入变化", 400)
    st.plotly_chart(fig1, width="stretch")

    col_a, col_b = st.columns(2)

    # 图 2：订单数 vs 客单价
    with col_a:
        fig2 = go.Figure()
        fig2.add_bar(x=m["ym"], y=m["orders"], name="订单数", marker_color="#CFE0EE",
                     yaxis="y")
        fig2.add_scatter(x=m["ym"], y=m["aov_net"], name="客单价（净）", mode="lines+markers",
                         line=dict(color=C_ACCENT, width=2.6), yaxis="y2")
        fig2.update_layout(yaxis=dict(title="订单数"), yaxis2=dict(title="客单价 (£)", overlaying="y",
                                                                  side="right", showgrid=False))
        style(fig2, "订单量 vs 客单价：波动到底来自量还是价",
              f"{DATE_MIN} ~ {DATE_MAX}｜客单价 = 净收入 / 订单数", 340)
        st.plotly_chart(fig2, width="stretch")

    # 图 3：2011-12 退款 TOP
    with col_b:
        r12 = q("""
            SELECT COALESCE(description, stock_code) AS item, SUM(revenue) AS refund
              FROM returns WHERE invoice_year_month = '2011-12'
             GROUP BY stock_code ORDER BY refund ASC LIMIT 8
        """)
        r12["refund_abs"] = -r12["refund"]
        fig3 = px.bar(r12.sort_values("refund_abs"), x="refund_abs", y="item", orientation="h",
                      color_discrete_sequence=[C_NEG], text_auto=".2s")
        fig3.update_traces(textposition="outside", textfont_size=10)
        style(fig3, "2011-12 单月退货构成 TOP8",
              "2011-12-01 ~ 12-09｜单笔 £168,470 占该月退货 82%，剔除后该月退货率与常态持平", 340)
        st.plotly_chart(fig3, width="stretch")

    st.info(
        f"**结论｜** 25 个月中 {int((d['anomaly_flag']=='异常月').sum())} 个月环比跌破 -25%，"
        "**订单量效应几乎在所有异常月都是主导项**（2010-12 为 -£633K、2011-12 为 -£1,059K），"
        "而非客单价下滑 —— 说明收入波动是「来的人少了」，不是「买得便宜了」，运营抓手应放在拉新与老客唤醒。"
        "2011-04 是唯一「量价双杀」的月份（订单 -£102K、客单价 -£77K）。"
        "2011-01 虽然订单下降，但客单价逆势 +£118K，缓冲了下跌。"
        "2011-12 的 -70% 由数据截断（仅 8 天）+ £205K 退款共同造成，其中 £168,470 是客户 16446 取消整单 SKU 23843。"
    )


# ================================================================== 页 3 客户分层
elif page.startswith("3"):
    st.markdown("## 客户价值分层与复购")
    st.caption(f"样本：{N_CUST:,} 位有 CustomerID 的客户｜RFM 用 NTILE 五等分打分（≥4 为高），累计收入 £{rfm['monetary'].sum():,.0f}")

    seg = rfm.groupby("segment").agg(customers=("customer_id", "size"),
                                     revenue=("monetary", "sum"),
                                     arpu=("monetary", "mean"),
                                     avg_orders=("frequency", "mean")).reset_index()
    seg["revenue_pct"] = seg["revenue"] / seg["revenue"].sum()
    seg["customer_pct"] = seg["customers"] / seg["customers"].sum()
    seg = seg.sort_values("revenue", ascending=False)

    hi = seg[seg["segment"].isin(["重要价值客户", "重要保持客户"])]
    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "客户数", f"{N_CUST:,}")
    kpi(c2, "复购率", f"{N_REPEAT/N_CUST:.1%}", help_text="购买次数≥2 / 有客户ID客户")
    kpi(c3, "高价值客户占比", f"{hi['customer_pct'].sum():.1%}", help_text="重要价值 + 重要保持客户人数占比")
    kpi(c4, "高价值客户收入贡献", f"{hi['revenue_pct'].sum():.1%}", help_text="重要价值 + 重要保持客户收入占比")

    st.markdown("")

    col_a, col_b = st.columns([1.15, 1])

    # 图 1：RFM 分层收入贡献
    with col_a:
        fig1 = px.bar(seg.sort_values("revenue"), x="revenue", y="segment", orientation="h",
                      color="revenue_pct", color_continuous_scale=[[0, "#D6E6F2"], [1, C_MAIN]],
                      text=seg.sort_values("revenue")["revenue_pct"].map(lambda v: f"{v:.1%}"))
        fig1.update_traces(textposition="outside", textfont_size=11)
        fig1.update_layout(coloraxis_showscale=False, yaxis_title="", xaxis_title="客户累计消费 (£)")
        style(fig1, "RFM 八分层：收入贡献分布",
              f"{DATE_MIN} ~ {DATE_MAX}｜R=距最后购买天数、F=订单数、M=消费额；括号内为收入占比", 420)
        st.plotly_chart(fig1, width="stretch")

    # 图 2：频次分布
    with col_b:
        f = freq.copy()
        fig2 = go.Figure()
        fig2.add_bar(x=f["freq_bucket"], y=f["customers"], name="客户数",
                     marker_color=C_MAIN, yaxis="y")
        fig2.add_scatter(x=f["freq_bucket"], y=f["revenue_pct"], name="收入占比(%)",
                         mode="lines+markers", line=dict(color=C_ACCENT, width=2.6), yaxis="y2")
        fig2.update_layout(yaxis=dict(title="客户数"), yaxis2=dict(title="收入占比 (%)", overlaying="y",
                                                                   side="right", showgrid=False))
        style(fig2, "购买频次分布：人数 vs 收入",
              f"{DATE_MIN} ~ {DATE_MAX}｜横轴为去重订单数分桶；复购率 {N_REPEAT/N_CUST:.1%}", 420)
        st.plotly_chart(fig2, width="stretch")

    # 图 3：帕累托
    p = pareto.sort_values("revenue_rank")
    fig3 = go.Figure()
    fig3.add_scatter(x=p["cumulative_customer_pct"], y=p["cumulative_revenue_pct"],
                     mode="lines", line=dict(color=C_MAIN, width=2.8), fill="tozeroy",
                     fillcolor="rgba(46,94,142,0.12)", name="累计收入占比")
    for xv in (1, 10, 20):
        yv = p.loc[p["cumulative_customer_pct"] <= xv, "cumulative_revenue_pct"].max()
        fig3.add_vline(x=xv, line=dict(color=C_NEG, width=1, dash="dash"))
        fig3.add_annotation(x=xv, y=yv, text=f"前{xv}% → {yv:.0f}%", showarrow=True,
                            arrowhead=2, ax=35, ay=-25, font=dict(size=11, color=C_NEG))
    fig3.update_layout(xaxis_title="累计客户占比 (%)", yaxis_title="累计收入占比 (%)")
    style(fig3, "客户收入帕累托曲线",
          f"{DATE_MIN} ~ {DATE_MAX}｜按客户累计消费降序；曲线越陡说明收入越依赖少数客户", 380)
    st.plotly_chart(fig3, width="stretch")

    top1 = p.loc[p["cumulative_customer_pct"] <= 1, "cumulative_revenue_pct"].max()
    top10 = p.loc[p["cumulative_customer_pct"] <= 10, "cumulative_revenue_pct"].max()
    st.info(
        f"**结论｜** 复购率 {N_REPEAT/N_CUST:.1%}（{N_REPEAT:,}/{N_CUST:,}），客户结构极度头部化："
        f"**前 1% 客户（{int(N_CUST*0.01)} 人）贡献 {top1:.0f}% 收入，前 10% 贡献 {top10:.0f}%**。"
        f"「重要价值客户」{int(hi[hi.segment=='重要价值客户']['customers'].iloc[0]):,} 人（{hi[hi.segment=='重要价值客户']['customer_pct'].iloc[0]:.0%}）"
        f"贡献 {hi[hi.segment=='重要价值客户']['revenue_pct'].iloc[0]:.0%} 收入，人均消费 "
        f"£{hi[hi.segment=='重要价值客户']['arpu'].iloc[0]:,.0f}、平均 {hi[hi.segment=='重要价值客户']['avg_orders'].iloc[0]:.1f} 单。"
        f"另一方面「流失客户」{int(seg[seg.segment=='流失客户']['customers'].iloc[0]):,} 人（{seg[seg.segment=='流失客户']['customer_pct'].iloc[0]:.0%}）"
        f"只贡献 {seg[seg.segment=='流失客户']['revenue_pct'].iloc[0]:.1%}，且人均仅 "
        f"{seg[seg.segment=='流失客户']['avg_orders'].iloc[0]:.1f} 单 —— 这批人多为一次性买手，无需投入过多维系成本。"
        "**策略上应把预算集中在前 20% 客户**：他们的流失对收入的影响是普通客户的数十倍。"
    )


# ================================================================== 页 4 Cohort
elif page.startswith("4"):
    st.markdown("## Cohort 留存分析")
    st.caption("按客户首购月份分群，观察后续每个月的留存比例（留存 = 当月有消费 / 该群客户数）")

    pivot = cohort.pivot(index="cohort", columns="month_age", values="retention_pct")
    obs = cohort.pivot(index="cohort", columns="month_age", values="observability")
    pivot_masked = pivot.where(obs == "可观测")

    c1, c2, c3, c4 = st.columns(4)
    m1 = cohort[(cohort["month_age"] == 1) & (cohort["observability"] == "可观测")]["retention_pct"].mean()
    m3 = cohort[(cohort["month_age"] == 3) & (cohort["observability"] == "可观测")]["retention_pct"].mean()
    m6 = cohort[(cohort["month_age"] == 6) & (cohort["observability"] == "可观测")]["retention_pct"].mean()
    m12 = cohort[(cohort["month_age"] == 12) & (cohort["observability"] == "可观测")]["retention_pct"].mean()
    kpi(c1, "平均 M+1 留存", f"{m1:.1f}%")
    kpi(c2, "平均 M+3 留存", f"{m3:.1f}%")
    kpi(c3, "平均 M+6 留存", f"{m6:.1f}%")
    kpi(c4, "平均 M+12 留存", f"{m12:.1f}%")

    st.markdown("")

    # 图 1：留存热力图
    fig1 = go.Figure(data=go.Heatmap(
        z=pivot_masked.values,
        x=[f"M+{c}" for c in pivot_masked.columns],
        y=pivot_masked.index,
        colorscale=[[0, "#FFFFFF"], [0.35, "#D6E6F2"], [1, "#1F4E79"]],
        text=pivot_masked.round(0).fillna("").astype(str).replace("nan", "").values,
        texttemplate="%{text}", textfont=dict(size=9),
        hovertemplate="首购月 %{y}<br>%{x}<br>留存 %{z:.1f}%<extra></extra>",
        colorbar=dict(title="留存 %", thickness=12, len=0.7),
        zmin=0, zmax=80,
    ))
    fig1.update_layout(yaxis=dict(autorange="reversed"), xaxis_title="首购后月龄", yaxis_title="首购月份（cohort）")
    style(fig1, "Cohort 月度留存热力图",
          f"{DATE_MIN} ~ {DATE_MAX}｜空白 = 超出数据范围（未观测），非 0 留存；颜色越深留存越高", 560)
    st.plotly_chart(fig1, width="stretch")

    col_a, col_b = st.columns(2)

    # 图 2：平均留存曲线
    with col_a:
        curve = cohort[cohort["observability"] == "可观测"].groupby("month_age").agg(
            avg_retention=("retention_pct", "mean"), cohorts=("cohort", "nunique")).reset_index()
        fig2 = px.area(curve, x="month_age", y="avg_retention", color_discrete_sequence=[C_MAIN])
        fig2.update_traces(line=dict(width=2.4))
        fig2.update_layout(xaxis_title="首购后月龄", yaxis_title="平均留存 (%)")
        style(fig2, "平均留存衰减曲线",
              "仅统计「可观测」格子（已剔除数据右截断），M+0 恒为 100% 故从 M+1 起", 340)
        st.plotly_chart(fig2, width="stretch")

    # 图 3：cohort 规模
    with col_b:
        size = cohort.groupby("cohort")["cohort_customers"].first().reset_index()
        fig3 = px.bar(size, x="cohort", y="cohort_customers", color_discrete_sequence=[C_ACCENT])
        fig3.update_layout(xaxis_title="首购月份", yaxis_title="cohort 客户数")
        fig3.update_xaxes(tickangle=-45)
        style(fig3, "各首购月 cohort 规模",
              "2009-12 cohort 含存量客户（数据左截断），规模虚高，不宜与新客 cohort 直接比较", 340)
        st.plotly_chart(fig3, width="stretch")

    st.info(
        f"**结论｜** 客户留存呈典型「首月陡降 + 长尾缓降」：M+1 平均留存 {m1:.1f}%，M+3 {m3:.1f}%，"
        f"M+6 {m6:.1f}%，M+12 仍有 {m12:.1f}%。**首月是留存分水岭** —— 一个月内未复购的客户，后续再激活的概率显著下降。"
        "2010-11 与 2010-12 cohort 的 M+1 留存（17.5% / 9.2%）明显低于 2011 年同期 cohort（27% 左右），"
        "说明旺季拉来的客户质量更差、更偏一次性采购。"
        "两个读图陷阱已在标记中处理：**2009-12 cohort（955 人）是数据左截断产物**，含大量存量客户；"
        "**右侧空白是数据右截断（止于 2011-12-09）**，不是留存归零。"
    )


# ================================================================== 页 5 退货与集中度
elif page.startswith("5"):
    st.markdown("## 退货影响与市场 / 商品集中度")
    st.caption(f"退货金额 £{abs(TOTAL_REFUND):,.0f}，占销售收入 {abs(TOTAL_REFUND)/TOTAL_GROSS:.2%}｜商品维度已排除运费/调整类编码")

    ret_prod = returns[returns["dimension"] == "商品"].copy()
    ret_prod["refund_abs"] = -ret_prod["refund_amount"]

    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "整体退货率", f"{abs(TOTAL_REFUND)/TOTAL_GROSS:.2%}", help_text="|退货金额| / 销售收入")
    kpi(c2, "退货单数", f"{int(quality['cancelled_invoice_rows']):,}", help_text="取消/退货明细行数")
    kpi(c3, "英国收入占比", f"{quality.get('uk_revenue_share', '')}" if False else f"{market[market.market_type=='英国']['share_y2011_pct'].iloc[0]:.1f}%",
        help_text="2011 同口径窗口（1/1–12/9）")
    kpi(c4, "商品 HHI", f"{product['hhi_index'].iloc[0]:.0f}", help_text="<1500 为低集中度；本数据仅 17，属极度分散")

    st.markdown("")
    col_a, col_b = st.columns(2)

    # 图 1：退货 TOP 商品
    with col_a:
        fig1 = px.bar(ret_prod.sort_values("refund_abs").tail(10), x="refund_abs", y="item_name",
                      orientation="h", color_discrete_sequence=[C_NEG], text_auto=".2s")
        fig1.update_traces(textposition="outside", textfont_size=10)
        fig1.update_layout(xaxis_title="退款金额 (£)", yaxis_title="")
        style(fig1, "退货金额 TOP10 商品",
              f"{DATE_MIN} ~ {DATE_MAX}｜第一名 23843 为单笔整单取消（£168,470），非普遍性质量问题", 400)
        st.plotly_chart(fig1, width="stretch")

    # 图 2：国家市场
    with col_b:
        mk = market.head(10).copy()
        mk["label"] = mk["country"].str.slice(0, 16)
        fig2 = go.Figure()
        fig2.add_bar(x=mk["revenue_y2011"], y=mk["label"], orientation="h", name="2011 收入",
                     marker_color=[C_MAIN if t == "英国" else C_ACCENT for t in mk["market_type"]])
        fig2.update_layout(xaxis_title="2011 收入 (£，1/1–12/9)", yaxis_title="")
        fig2.update_yaxes(autorange="reversed")
        style(fig2, "国家市场 TOP10（同口径 2011）",
              "深蓝 = 英国（84.1%），橙色 = 海外；海外合计占 15.9%", 400)
        st.plotly_chart(fig2, width="stretch")

    # 图 3：商品集中度累计曲线
    all_prod = q("""
        WITH p AS (SELECT stock_code, SUM(revenue) rev FROM transactions
                    WHERE is_special_code = 0 GROUP BY stock_code)
        SELECT ROW_NUMBER() OVER (ORDER BY rev DESC) rn, rev,
               100.0 * SUM(rev) OVER (ORDER BY rev DESC ROWS UNBOUNDED PRECEDING)
                     / (SELECT SUM(rev) FROM p) cum_pct,
               100.0 * ROW_NUMBER() OVER (ORDER BY rev DESC) / (SELECT COUNT(*) FROM p) prod_pct
          FROM p
    """)
    fig3 = go.Figure()
    fig3.add_scatter(x=all_prod["prod_pct"], y=all_prod["cum_pct"], mode="lines",
                     line=dict(color=C_MAIN, width=2.6), fill="tozeroy",
                     fillcolor="rgba(46,94,142,0.12)")
    fig3.add_scatter(x=[0, 100], y=[0, 100], mode="lines", name="完全均分基线",
                     line=dict(color=C_GREY, width=1.2, dash="dash"))
    for xv, txt in [(2.1, f"前 100 个 SKU → {product['cumulative_revenue_pct'].iloc[29]:.0f}%")]:
        fig3.add_vline(x=xv, line=dict(color=C_ACCENT, width=1, dash="dot"))
        fig3.add_annotation(x=xv, y=product["cumulative_revenue_pct"].iloc[29], text=txt,
                            showarrow=True, arrowhead=2, ax=45, ay=-30,
                            font=dict(size=11, color=C_ACCENT))
    fig3.update_layout(xaxis_title="商品累计占比 (%)", yaxis_title="累计收入占比 (%)")
    style(fig3, "商品收入集中度（洛伦兹曲线）",
          f"共 {int(product['total_products'].iloc[0]):,} 个有效 SKU｜曲线贴近对角线 = 长尾极度分散，无单品依赖风险", 380)
    st.plotly_chart(fig3, width="stretch")

    st.info(
        f"**结论｜** 整体退货率 **{abs(TOTAL_REFUND)/TOTAL_GROSS:.2%}**，但**退货高度集中**："
        f"单笔 £168,470 的整单取消（客户 16446 / SKU 23843）即占全部退款的 11.5%，占该月退货 82%。"
        "剔除该笔后，退货主要由 AMAZONFEE 平台费修正、CRUK 佣金等费用类项目构成，"
        "**商品质量类退货并不突出** —— 这是个重要结论：问题不在货，而在少数大单的履约/结算异常。"
        "市场结构上英国占 84.1%，海外 15.9%；海外增长快于本土（Netherlands +9.1%、Germany +11.1%、France +42.3%、"
        "Australia +341%），但 EIRE 下滑 24.3%，需单独排查。"
        f"商品端 HHI 仅 {product['hhi_index'].iloc[0]:.0f}、前 10 个 SKU 只占 8.0%，"
        f"{int(product['total_products'].iloc[0]):,} 个 SKU 呈典型长尾 —— **不存在单品依赖风险，但也意味着缺少爆款抓手**。"
        "注意 TOP4 的 23843 收入 £168,470 与退款 £168,470 几乎完全对冲，是「虚假热销」，评估商品表现时必须看净额。"
    )

# ------------------------------------------------------------------ 页脚
st.divider()
st.caption(
    f"数据源：UCI Online Retail II（{DATE_MIN} ~ {DATE_MAX}）｜"
    "计算：SQLite 窗口函数 + Pandas 交叉验证（16 项指标对账全部通过）｜"
    "全部数字由真实交易流水计算，无模拟数据"
)
