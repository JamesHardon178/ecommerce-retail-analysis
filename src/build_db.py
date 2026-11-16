"""
build_db.py — 构建 SQLite 分析库
========================================================
输入：data/processed/transactions.parquet / returns.parquet / quality_report.json
输出：data/processed/retail.db
        ├─ transactions  正常销售明细（1,007,913 行）
        ├─ returns       取消/退货明细（19,104 行）
        ├─ data_quality  数据质量指标（长表，便于 SQL 直接查）
        └─ v_*           预置视图（统一口径，避免每条 SQL 各写一遍）

同时用 Pandas **独立**算一遍核心指标写入 data/processed/core_metrics.json，
供 tests/ 与 SQL 结果做交叉验证（两条独立路径算同一个数，才能证明口径没写错）。

运行：python src/build_db.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
DB_PATH = PROCESSED / "retail.db"

SCHEMA = """
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS returns;
DROP TABLE IF EXISTS data_quality;
DROP VIEW  IF EXISTS v_sales;
DROP VIEW  IF EXISTS v_monthly_sales;
DROP VIEW  IF EXISTS v_monthly_returns;
DROP VIEW  IF EXISTS v_customer_orders;

CREATE TABLE transactions (
    invoice_no          TEXT    NOT NULL,
    stock_code          TEXT,
    description         TEXT,
    quantity            INTEGER NOT NULL,
    invoice_date        TEXT    NOT NULL,   -- 'YYYY-MM-DD HH:MM:SS'
    invoice_date_only   TEXT    NOT NULL,   -- 'YYYY-MM-DD'，日期维度分组用
    invoice_year_month  TEXT    NOT NULL,   -- 'YYYY-MM'，月度分组用
    unit_price          REAL    NOT NULL,
    revenue             REAL    NOT NULL,   -- quantity * unit_price，恒 > 0
    customer_id         INTEGER,            -- NULL = 匿名交易
    country             TEXT,
    is_special_code     INTEGER NOT NULL DEFAULT 0,  -- 1=运费/手工调整等非商品编码
    source_sheet        TEXT,
    source_year         INTEGER
);

CREATE TABLE returns (
    invoice_no          TEXT    NOT NULL,
    stock_code          TEXT,
    description         TEXT,
    quantity            INTEGER NOT NULL,   -- 恒 < 0
    invoice_date        TEXT    NOT NULL,
    invoice_date_only   TEXT    NOT NULL,
    invoice_year_month  TEXT    NOT NULL,
    unit_price          REAL    NOT NULL,
    revenue             REAL    NOT NULL,   -- 恒 < 0（负数即退款额）
    customer_id         INTEGER,
    country             TEXT,
    is_special_code     INTEGER NOT NULL DEFAULT 0,
    source_sheet        TEXT,
    source_year         INTEGER
);

CREATE TABLE data_quality (
    metric      TEXT PRIMARY KEY,
    value       TEXT,
    description TEXT
);

CREATE INDEX idx_tx_invoice   ON transactions(invoice_no);
CREATE INDEX idx_tx_customer  ON transactions(customer_id);
CREATE INDEX idx_tx_month     ON transactions(invoice_year_month);
CREATE INDEX idx_tx_stock     ON transactions(stock_code);
CREATE INDEX idx_tx_country   ON transactions(country);
CREATE INDEX idx_rt_customer  ON returns(customer_id);
CREATE INDEX idx_rt_month     ON returns(invoice_year_month);
CREATE INDEX idx_rt_stock     ON returns(stock_code);

-- 统一入口视图：后续所有业务 SQL 都从 v_sales / v_monthly_* 出发，口径不会漂
CREATE VIEW v_sales AS
SELECT * FROM transactions;

CREATE VIEW v_monthly_sales AS
SELECT invoice_year_month                       AS ym,
       COUNT(DISTINCT invoice_no)               AS orders,
       COUNT(DISTINCT customer_id)              AS customers,
       SUM(revenue)                             AS gross_revenue,
       SUM(revenue) / COUNT(DISTINCT invoice_no) AS aov_gross
FROM transactions
GROUP BY invoice_year_month;

CREATE VIEW v_monthly_returns AS
SELECT invoice_year_month AS ym,
       COUNT(DISTINCT invoice_no) AS return_orders,
       SUM(revenue)              AS refund_amount
FROM returns
GROUP BY invoice_year_month;

-- 客户级订单汇总（客户级分析统一从这里出发，天然排除匿名交易）
CREATE VIEW v_customer_orders AS
SELECT customer_id,
       COUNT(DISTINCT invoice_no) AS order_count,
       MIN(invoice_date_only)     AS first_order_date,
       MAX(invoice_date_only)     AS last_order_date,
       SUM(revenue)               AS total_revenue,
       SUM(revenue) / COUNT(DISTINCT invoice_no) AS avg_order_value
FROM transactions
WHERE customer_id IS NOT NULL
GROUP BY customer_id;
"""

TX_COLS = ["invoice_no", "stock_code", "description", "quantity", "invoice_date",
           "invoice_date_only", "invoice_year_month", "unit_price", "revenue",
           "customer_id", "country", "is_special_code", "source_sheet", "source_year"]
RT_COLS = TX_COLS


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """把 parquet 的列名/类型整理成 SQLite 友好的形式。"""
    out = df.rename(columns={
        "InvoiceNo": "invoice_no", "StockCode": "stock_code", "Description": "description",
        "Quantity": "quantity", "InvoiceDate": "invoice_date", "UnitPrice": "unit_price",
        "Revenue": "revenue", "CustomerID": "customer_id", "Country": "country",
        "IsSpecialCode": "is_special_code", "SourceSheet": "source_sheet",
        "SourceYear": "source_year", "InvoiceYearMonth": "invoice_year_month",
    }).copy()

    out["invoice_date"] = out["invoice_date"].dt.strftime("%Y-%m-%d %H:%M:%S")
    out["invoice_date_only"] = out["invoice_date"].str.slice(0, 10)
    out["is_special_code"] = out["is_special_code"].astype("int64")
    # customer_id 保持可空整型，NA -> NULL（绝不能填 0，0 会被当成真实客户）
    out["customer_id"] = out["customer_id"].astype("Int64")
    return out


def to_rows(df: pd.DataFrame, cols: list[str]):
    """转成 sqlite3 能接受的元组流：pd.NA/NaN -> None。"""
    sub = df[cols].astype(object)
    sub = sub.where(pd.notna(sub), None)
    for row in sub.itertuples(index=False, name=None):
        yield row


def compute_core_metrics(sales: pd.DataFrame, returns: pd.DataFrame, quality: dict) -> dict:
    """Pandas 独立计算核心指标 —— 与 SQL 结果互为交叉验证。"""
    gross = float(sales["Revenue"].sum())
    refund = float(returns["Revenue"].sum())          # 负数
    net = gross + refund
    orders = int(sales["InvoiceNo"].nunique())
    cust = sales.loc[sales["CustomerID"].notna()]
    n_customers = int(cust["CustomerID"].nunique())

    orders_per_customer = cust.groupby("CustomerID")["InvoiceNo"].nunique()
    repeat_customers = int((orders_per_customer >= 2).sum())

    # 商品集中度（排除运费/调整类编码）
    goods = sales[sales["IsSpecialCode"] == 0]
    prod_rev = goods.groupby("StockCode")["Revenue"].sum().sort_values(ascending=False)
    top10_prod_share = float(prod_rev.head(10).sum() / prod_rev.sum())

    country_rev = sales.groupby("Country")["Revenue"].sum().sort_values(ascending=False)
    uk_share = float(country_rev.get("United Kingdom", 0) / country_rev.sum())
    top5_country_share = float(country_rev.head(5).sum() / country_rev.sum())

    # 月度
    monthly = sales.groupby("InvoiceYearMonth").agg(
        orders=("InvoiceNo", "nunique"),
        customers=("CustomerID", "nunique"),
        revenue=("Revenue", "sum"),
    )
    monthly["aov"] = monthly["revenue"] / monthly["orders"]

    metrics = {
        "date_range": [quality["volume"]["date_min"][:10], quality["volume"]["date_max"][:10]],
        "raw_rows": quality["volume"]["total_rows_raw"],
        "sales_rows": int(len(sales)),
        "return_rows": int(len(returns)),
        "gross_revenue": round(gross, 2),
        "refund_amount": round(refund, 2),
        "net_revenue": round(net, 2),
        "return_rate_by_amount": round(abs(refund) / gross, 6),
        "orders": orders,
        "customers_with_id": n_customers,
        "aov_net": round(net / orders, 2),
        "aov_gross": round(gross / orders, 2),
        "repeat_customers": repeat_customers,
        "repeat_rate": round(repeat_customers / n_customers, 6),
        "orders_per_customer_mean": round(float(orders_per_customer.mean()), 4),
        "revenue_per_customer_mean": round(float(cust.groupby("CustomerID")["Revenue"].sum().mean()), 2),
        "uk_revenue_share": round(uk_share, 6),
        "top5_country_share": round(top5_country_share, 6),
        "top10_product_share": round(top10_prod_share, 6),
        "unique_products": int(goods["StockCode"].nunique()),
        "months": int(monthly.shape[0]),
        "monthly": {
            k: {"orders": int(v["orders"]), "customers": int(v["customers"]),
                "revenue": round(float(v["revenue"]), 2), "aov": round(float(v["aov"]), 2)}
            for k, v in monthly.to_dict("index").items()
        },
    }
    return metrics


def main() -> None:
    sales = pd.read_parquet(PROCESSED / "transactions.parquet")
    returns = pd.read_parquet(PROCESSED / "returns.parquet")
    quality = json.loads((PROCESSED / "quality_report.json").read_text(encoding="utf-8"))
    print(f"[load] transactions={len(sales):,}  returns={len(returns):,}")

    sales = prepare(sales)
    returns = prepare(returns)

    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    cur.executemany(
        f"INSERT INTO transactions ({','.join(TX_COLS)}) VALUES ({','.join('?' * len(TX_COLS))})",
        to_rows(sales, TX_COLS),
    )
    print(f"[db] transactions 写入 {len(sales):,} 行")
    cur.executemany(
        f"INSERT INTO returns ({','.join(RT_COLS)}) VALUES ({','.join('?' * len(RT_COLS))})",
        to_rows(returns, RT_COLS),
    )
    print(f"[db] returns 写入 {len(returns):,} 行")

    # 数据质量指标转长表
    qa_rows = [
        ("raw_rows", quality["volume"]["total_rows_raw"], "两 sheet 合并原始行数"),
        ("date_min", quality["volume"]["date_min"][:10], "最早交易日期"),
        ("date_max", quality["volume"]["date_max"][:10], "最晚交易日期"),
        ("sheet_rows_year_2009_2010", quality["source"]["sheets"]["Year 2009-2010"]["rows"], "sheet1 行数"),
        ("sheet_rows_year_2010_2011", quality["source"]["sheets"]["Year 2010-2011"]["rows"], "sheet2 行数"),
        ("duplicate_rows", quality["anomalies"]["duplicate_rows"], "完全重复行（已删除）"),
        ("cancelled_invoice_rows", quality["anomalies"]["cancelled_invoice_rows"], "取消单行数（保留在 returns）"),
        ("negative_qty_rows", quality["anomalies"]["negative_qty_rows"], "负数量行数"),
        ("zero_price_rows", quality["anomalies"]["zero_price_rows"], "零单价行数"),
        ("negative_price_rows", quality["anomalies"]["negative_price_rows"], "负单价行数"),
        ("missing_customer_rows", quality["anomalies"]["missing_customer_rows"], "客户ID缺失行数"),
        ("null_description_rows", quality["nulls"]["Description"], "商品描述缺失行数"),
        ("unique_invoice_raw", quality["volume"]["unique_invoice_raw"], "原始唯一订单号数"),
        ("unique_customer_raw", quality["volume"]["unique_customer_raw"], "原始唯一客户数"),
        ("unique_stockcode_raw", quality["volume"]["unique_stockcode_raw"], "原始唯一商品编码数"),
        ("unique_country_raw", quality["volume"]["unique_country_raw"], "原始国家数"),
        ("sha256_xlsx", quality["source"]["sha256"], "原始文件 SHA-256"),
    ]
    cur.executemany(
        "INSERT INTO data_quality (metric, value, description) VALUES (?,?,?)",
        [(m, str(v), d) for m, v, d in qa_rows],
    )

    con.commit()

    # 建库后自检：表行数 / 视图可查
    checks = {
        "transactions": cur.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        "returns": cur.execute("SELECT COUNT(*) FROM returns").fetchone()[0],
        "v_monthly_sales_rows": cur.execute("SELECT COUNT(*) FROM v_monthly_sales").fetchone()[0],
        "v_customer_orders_rows": cur.execute("SELECT COUNT(*) FROM v_customer_orders").fetchone()[0],
        "sales_negative_revenue": cur.execute("SELECT COUNT(*) FROM transactions WHERE revenue<=0").fetchone()[0],
        "returns_positive_revenue": cur.execute("SELECT COUNT(*) FROM returns WHERE revenue>=0").fetchone()[0],
    }
    assert checks["transactions"] == len(sales), "transactions 写入行数与源不符"
    assert checks["returns"] == len(returns), "returns 写入行数与源不符"
    assert checks["sales_negative_revenue"] == 0, "transactions 出现非正收入"
    assert checks["returns_positive_revenue"] == 0, "returns 出现非负退款"
    con.close()

    metrics = compute_core_metrics(
        pd.read_parquet(PROCESSED / "transactions.parquet"),
        pd.read_parquet(PROCESSED / "returns.parquet"),
        quality,
    )
    metrics["db_checks"] = checks
    (PROCESSED / "core_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 60)
    print("SQLite 建库完成 ->", DB_PATH.name)
    print("=" * 60)
    for k, v in checks.items():
        print(f"{k:<28} {v:>10,}")
    print("-" * 60)
    print(f"销售收入      £{metrics['gross_revenue']:>15,.2f}")
    print(f"退货金额      £{metrics['refund_amount']:>15,.2f}")
    print(f"净收入        £{metrics['net_revenue']:>15,.2f}")
    print(f"订单数        {metrics['orders']:>16,}")
    print(f"客单价(净)    £{metrics['aov_net']:>15,.2f}")
    print(f"客户数        {metrics['customers_with_id']:>16,}")
    print(f"复购率        {metrics['repeat_rate']:>16.2%}")
    print("=" * 60)


if __name__ == "__main__":
    main()
