"""
tests/test_metrics.py — 关键口径测试
========================================================
验证原则：**测试口径约束本身**，而不是复述计算结果。
即：不管数据怎么变，这些不变式（invariant）必须永远成立；
数字本身（如净收入 £19.01M）另有 16 项 SQL vs Pandas 交叉验证兜底。

运行：python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def db():
    db_path = PROCESSED / "retail.db"
    assert db_path.exists(), f"缺少 {db_path}，请先运行 src/clean_data.py 与 src/build_db.py"
    con = sqlite3.connect(db_path)
    yield con
    con.close()


@pytest.fixture(scope="session")
def metrics():
    p = PROCESSED / "core_metrics.json"
    assert p.exists(), "缺少 core_metrics.json"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def quality():
    p = PROCESSED / "quality_report.json"
    assert p.exists(), "缺少 quality_report.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1. 清洗行数守恒
def test_segment_counts_sum_to_raw_rows(quality):
    """任何清洗都必须满足：各分段行数之和 == 原始总行数（加总口径闭环）。"""
    counts = quality["segments"]["counts"]
    assert sum(counts.values()) == quality["volume"]["total_rows_raw"]


# ---------------------------------------------------------------- 2. 销售口径硬约束
def test_sales_rows_strictly_positive(db):
    """销售收入口径：Quantity>0 且 UnitPrice>0，且 Revenue = Quantity * UnitPrice。"""
    df = pd.read_sql_query(
        "SELECT quantity, unit_price, revenue FROM transactions", db
    )
    assert (df["quantity"] > 0).all()
    assert (df["unit_price"] > 0).all()
    # 浮点容差 0.01（两位小数货币）
    assert ((df["quantity"] * df["unit_price"] - df["revenue"]).abs() < 0.01).all()


# ---------------------------------------------------------------- 3. 退货口径硬约束
def test_returns_negative_only(db):
    """退货口径：returns 全部为 C 开头订单号、负数量、负金额，且与 transactions 完全不重叠。"""
    df = pd.read_sql_query(
        "SELECT invoice_no, quantity, revenue FROM returns", db
    )
    assert df["invoice_no"].str.startswith("C").all()
    assert (df["quantity"] < 0).all()
    assert (df["revenue"] < 0).all()
    overlap = pd.read_sql_query(
        "SELECT COUNT(*) n FROM returns r JOIN transactions t USING(invoice_no)", db
    )["n"].iloc[0]
    assert overlap == 0


# ---------------------------------------------------------------- 4. 净收入口径
def test_net_revenue_identity(metrics):
    """净收入 = 销售收入 + 退货金额（退货为负），且与两条路径计算一致。"""
    assert abs(metrics["net_revenue"] - (metrics["gross_revenue"] + metrics["refund_amount"])) < 0.01


# ---------------------------------------------------------------- 5. 订单与客单价口径
def test_orders_and_aov(metrics, db):
    """订单数 = 正常销售唯一 InvoiceNo；客单价 = 净收入 / 订单数。"""
    n_orders = pd.read_sql_query(
        "SELECT COUNT(DISTINCT invoice_no) n FROM transactions", db
    )["n"].iloc[0]
    assert n_orders == metrics["orders"]
    assert abs(metrics["aov_net"] - metrics["net_revenue"] / metrics["orders"]) < 0.01


# ---------------------------------------------------------------- 6. 客户数排除匿名
def test_customer_count_excludes_null(metrics, db):
    """客户级分析必须排除 CustomerID 缺失的行。"""
    n = pd.read_sql_query(
        "SELECT COUNT(DISTINCT customer_id) n FROM transactions WHERE customer_id IS NOT NULL", db
    )["n"].iloc[0]
    assert n == metrics["customers_with_id"]
    # 匿名行确实存在且未被误填客户 ID
    anon = pd.read_sql_query(
        "SELECT COUNT(*) n FROM transactions WHERE customer_id IS NULL", db
    )["n"].iloc[0]
    assert anon > 0


# ---------------------------------------------------------------- 7. 复购率口径
def test_repeat_rate_definition(metrics, db):
    """复购率 = 订单数≥2 的客户 / 有 CustomerID 的客户。
    容差 1e-6：core_metrics.json 存储时做了 round(6)，容差须与存储精度一致。"""
    df = pd.read_sql_query(
        "SELECT customer_id, COUNT(DISTINCT invoice_no) n FROM transactions "
        "WHERE customer_id IS NOT NULL GROUP BY customer_id", db
    )
    repeat_rate = (df["n"] >= 2).sum() / len(df)
    assert abs(repeat_rate - metrics["repeat_rate"]) < 1e-6


# ---------------------------------------------------------------- 8. 重复行已删除
def test_no_duplicate_business_rows(db, quality):
    """「完全重复」= 业务 8 列（含 Description）全同，清洗口径必须与 cleaning_log 一致。
    已知合法保留：2 组「7 列相同但 Description 不同」的行（同一发票同一商品被录入
    两种描述，如 BUNTING , SPOTTY / SPOTTY BUNTING），不构成完全重复，必须保留 ——
    该断言同时防止未来有人误把 7 列当判重口径。"""
    cols8 = ("invoice_no, stock_code, description, quantity, invoice_date, unit_price, "
             "customer_id, country")
    cols7 = ("invoice_no, stock_code, quantity, invoice_date, unit_price, "
             "customer_id, country")
    dup8 = pd.read_sql_query(
        f"SELECT COUNT(*) n FROM (SELECT {cols8} FROM transactions "
        f"GROUP BY {cols8} HAVING COUNT(*) > 1)", db)["n"].iloc[0]
    assert dup8 == 0

    dup7 = pd.read_sql_query(
        f"SELECT COUNT(*) n FROM (SELECT {cols7} FROM transactions "
        f"GROUP BY {cols7} HAVING COUNT(*) > 1)", db)["n"].iloc[0]
    assert dup7 == 2

    # 全期删除的重复行数与质量报告一致（>0，说明判重逻辑真的在跑）
    assert quality["anomalies"]["duplicate_rows"] == 34_335


# ---------------------------------------------------------------- 9. 时间范围与截断月
def test_date_range_and_truncation(db, metrics):
    """数据时间范围正确；2011-12 是 8 天截断月，标记口径必须能识别它。"""
    rng = pd.read_sql_query(
        "SELECT MIN(invoice_date) dmin, MAX(invoice_date) dmax FROM transactions", db
    )
    assert rng["dmin"].iloc[0].startswith("2009-12-01")
    assert rng["dmax"].iloc[0].startswith("2011-12-09")
    # 截断月识别（防「把截断当业务下滑」的关键护栏）
    days = pd.read_sql_query(
        "SELECT COUNT(DISTINCT invoice_date_only) d FROM transactions "
        "WHERE invoice_year_month='2011-12'", db
    )["d"].iloc[0]
    assert days == 8
