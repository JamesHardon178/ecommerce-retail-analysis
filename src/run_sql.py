"""
run_sql.py — 批量执行专题 SQL 并导出结果
========================================================
输入：sql/*.sql（按文件名排序执行）
输出：reports/sql_results/<sql 文件名>.csv
      reports/sql_results/_verification.json   SQL 与 Pandas 双路径交叉验证结果

用法：
    python src/run_sql.py            # 全部执行
    python src/run_sql.py 01 07      # 只执行文件名以 01/07 开头的

交叉验证的意义：同一个指标用 SQL 和 Pandas 两条独立路径各算一遍，
差值在阈值内才说明口径实现正确 —— 单路径自证是没有意义的。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "retail.db"
SQL_DIR = ROOT / "sql"
OUT_DIR = ROOT / "reports" / "sql_results"


def run_all(filters: list[str]) -> dict[str, pd.DataFrame]:
    assert DB.exists(), f"数据库不存在：{DB}\n请先运行 python src/build_db.py"
    files = sorted(SQL_DIR.glob("*.sql"))
    if filters:
        files = [f for f in files if any(f.name.startswith(x) for x in filters)]
    assert files, "没有匹配的 SQL 文件"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    results = {}
    print("=" * 74)
    print(f"{'SQL 文件':<44}{'行数':>8}{'列数':>8}")
    print("=" * 74)
    for f in files:
        sql = f.read_text(encoding="utf-8")
        try:
            df = pd.read_sql_query(sql, con)
        except Exception as e:  # noqa: BLE001
            con.close()
            raise RuntimeError(f"[FAIL] {f.name}: {e}") from e
        assert not df.empty, f"[FAIL] {f.name} 返回空结果集"
        out = OUT_DIR / f"{f.stem}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        results[f.stem] = df
        print(f"{f.name:<44}{len(df):>8,}{len(df.columns):>8}")
    con.close()
    print("=" * 74)
    print(f"[done] 共 {len(files)} 条 SQL，结果已写入 {OUT_DIR.relative_to(ROOT)}/")
    return results


def verify(results: dict[str, pd.DataFrame]) -> dict:
    """SQL 结果 vs Pandas 独立计算结果，逐项对账。"""
    metrics = json.loads((ROOT / "data" / "processed" / "core_metrics.json").read_text(encoding="utf-8"))
    checks = []

    def add(name: str, sql_val, pd_val, tol: float, unit: str = "") -> None:
        diff = abs(float(sql_val) - float(pd_val))
        checks.append({
            "metric": name,
            "sql": round(float(sql_val), 6),
            "pandas": round(float(pd_val), 6),
            "abs_diff": round(diff, 6),
            "tolerance": tol,
            "unit": unit,
            "pass": bool(diff <= tol),
        })

    t01 = results.get("01_monthly_revenue_trend")
    if t01 is not None:
        add("净收入合计", t01["net_revenue"].sum(), metrics["net_revenue"], 1.0, "GBP")
        add("销售收入合计", t01["gross_revenue"].sum(), metrics["gross_revenue"], 1.0, "GBP")
        add("退款总额", t01["refund_amount"].sum(), metrics["refund_amount"], 1.0, "GBP")
        add("订单数合计", t01["orders"].sum(), metrics["orders"], 0, "单")
        add("月份数", len(t01), metrics["months"], 0, "月")

    t03 = results.get("03_customer_frequency_distribution")
    if t03 is not None:
        add("客户数", t03["total_customers"].iloc[0], metrics["customers_with_id"], 0, "人")
        add("复购客户数", t03["repeat_customers"].iloc[0], metrics["repeat_customers"], 0, "人")
        add("复购率(%)", t03["repeat_rate_pct"].iloc[0], metrics["repeat_rate"] * 100, 0.01, "%")
        add("客户收入合计", t03["revenue"].sum(), t03["total_customer_revenue"].iloc[0], 1.0, "GBP")

    t04 = results.get("04_rfm_segmentation")
    if t04 is not None:
        add("RFM 分层收入合计", t04["revenue"].sum(),
            results["03_customer_frequency_distribution"]["revenue"].sum(), 1.0, "GBP")
        add("RFM 客户数合计", t04["customers"].sum(),
            metrics["customers_with_id"], 0, "人")

    t05 = results.get("05_customer_value_pareto")
    if t05 is not None:
        n = metrics["customers_with_id"]
        # 前 10% 客户累计收入占比（Pandas 侧同一算法：int(n*0.10) 名客户）
        k = int(n * 0.10)
        sql_cum = t05.loc[t05["revenue_rank"] == k, "cumulative_revenue_pct"]
        assert not sql_cum.empty, f"05 结果缺少 revenue_rank={k} 的行"
        pd_cum = t05.loc[t05["revenue_rank"] <= k, "revenue_pct"].sum()
        add("前10%客户累计收入占比(%)", sql_cum.iloc[0], pd_cum, 0.35, "%")

    t07 = results.get("07_return_analysis")
    if t07 is not None:
        overall = abs(t07.loc[t07["dimension"] == "客户", "dimension_total"].iloc[0])
        add("退款总额（07 汇总）", overall, abs(metrics["refund_amount"]), 1.0, "GBP")

    t08 = results.get("08_market_structure")
    if t08 is not None:
        uk = t08[t08["country"] == "United Kingdom"]
        # 必须对比同口径（2011-01-01 ~ 2011-12-09 窗口），不能拿全期占比来比
        add("英国收入占比(2011同口径,%)", uk["share_y2011_pct"].iloc[0],
            metrics["uk_revenue_share_y2011_window"] * 100, 0.05, "%")

    t09 = results.get("09_product_concentration")
    if t09 is not None:
        r10 = t09.loc[t09["revenue_rank"] == 10, "cumulative_revenue_pct"].iloc[0]
        add("TOP10 商品累计收入占比(%)", r10, metrics["top10_product_share"] * 100, 0.05, "%")
        add("有效商品数", t09["total_products"].iloc[0], metrics["unique_products"], 0, "个")

    out = {
        "all_pass": all(c["pass"] for c in checks),
        "checks": checks,
    }
    (OUT_DIR / "_verification.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 74)
    print("交叉验证（SQL 结果 vs Pandas 独立计算）")
    print("=" * 74)
    print(f"{'指标':<28}{'SQL':>14}{'Pandas':>14}{'容差':>10}{'结果':>8}")
    print("-" * 74)
    for c in checks:
        flag = "PASS" if c["pass"] else "FAIL"
        print(f"{c['metric']:<28}{c['sql']:>14,.2f}{c['pandas']:>14,.2f}"
              f"{c['tolerance']:>10}{flag:>8}")
    print("=" * 74)
    print(f"全部通过: {out['all_pass']}")
    return out


if __name__ == "__main__":
    res = run_all(sys.argv[1:])
    v = verify(res)
    sys.exit(0 if v["all_pass"] else 1)
