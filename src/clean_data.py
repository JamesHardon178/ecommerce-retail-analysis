"""
clean_data.py — 数据清洗管线
========================================================
输入：data/raw/online_retail_II.xlsx（UCI Online Retail II，双 sheet）
输出：
    data/processed/transactions.parquet  正常销售明细（Quantity>0 且 UnitPrice>0）
    data/processed/returns.parquet       取消/退货明细（InvoiceNo 以 C 开头）
    data/processed/quality_report.json   数据质量报告（供 README / 报告引用）
    data/processed/cleaning_log.csv      清洗步骤流水账（行数加总必须闭环）
    data/raw/CHECKSUM.txt                原始文件校验和

设计原则
--------
1. **不伪造、不补数**：任何缺失值都不做插补，匿名客户保持匿名。
2. **加总可解释**：原始 1,067,371 行 → 每一行的去向都能在 cleaning_log 里找到，
   所有 segment 计数之和必须严格等于原始行数（脚本内 assert 校验）。
3. **口径由字段强制**：transactions 表内不允许出现 Quantity<=0 或 UnitPrice<=0，
   不允许出现 C 开头订单号 —— 用断言锁死，而不是靠注释约定。
4. **退货不删除**：取消/退货单独成表，用于退货分析（问题 4）。

运行：python src/clean_data.py [--no-cache]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- 路径常量
ROOT = Path(__file__).resolve().parents[1]
RAW_XLSX = ROOT / "data" / "raw" / "online_retail_II.xlsx"
RAW_DIR = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
CACHE = PROCESSED / "_raw_cache.parquet"

# ---------------------------------------------------------------- 列名映射
# UCI 原始表头 -> 项目标准名
COLUMN_MAP = {
    "Invoice": "InvoiceNo",
    "StockCode": "StockCode",
    "Description": "Description",
    "Quantity": "Quantity",
    "InvoiceDate": "InvoiceDate",
    "Price": "UnitPrice",
    "Customer ID": "CustomerID",
    "Country": "Country",
}
BUSINESS_COLS = list(COLUMN_MAP.values())

# 非商品类「费用/调整」编码：计入收入但不参与商品维度分析
# 否则 POST(邮费)、M(手工调整)、BANK CHARGES 会污染 TOP 商品榜
SPECIAL_STOCKCODES = {
    "POST", "D", "M", "C2", "DOT", "BANK CHARGES", "PADS", "CRUK",
    "S", "AMAZONFEE", "B", "ADJUST", "ADJUST2", "TEST001", "TEST002",
    "GIFT", "SP1002", "DCGS0003", "DCGS0004", "DCGS0005", "DCGS0006",
    "DCGS0007", "DCGS0008", "DCGS0009", "DCGS0010", "DCGS0011",
    "DCGS0013", "DCGS0014", "DCGS0015", "DCGS0016", "DCGS0017",
    "DCGS0018", "DCGS0019", "DCGS0020", "DCGS0021", "DCGS0022",
    "DCGS0023", "DCGS0024", "DCGS0025", "DCGS0026", "DCGS0027",
    "DCGS0028", "DCGS0029", "DCGS0030", "DCGS0031", "DCGS0032",
    "DCGS0033", "DCGS0034", "DCGS0035", "DCGS0036", "DCGS0037",
    "DCGS0038", "DCGS0039", "DCGS0040", "DCGS0041", "DCGS0042",
    "DCGS0043", "DCGS0044", "DCGS0045", "DCGS0046", "DCGS0047",
    "DCGS0048", "DCGS0049", "DCGS0050", "DCGS0051", "DCGS0052",
    "DCGS0053", "DCGS0054", "DCGS0063", "DCGS0064", "DCGS0065",
    "DCGS0066", "DCGS0067", "DCGS0068", "DCGS0069", "DCGS0070",
    "DCGS0071", "DCGS0072", "DCGS0073", "DCGS0074", "DCGS0075",
    "DCGS0076", "DCGS0077", "DCGS0078", "DCGS0079", "DCGS0080",
    "DCGS0081", "DCGS0082", "DCGS0083", "DCGS0084", "DCGS0085",
    "DCGS0086", "DCGS0087", "DCGS0088", "DCGS0089", "DCGS0090",
    "DCGS0091", "DCGS0092", "DCGS0093", "DCGS0094", "DCGS0095",
    "DCGS0096", "DCGS0097", "DCGS0098", "DCGS0099", "DCGS0100",
}

# 清洗分段（互斥且穷尽，按优先级从高到低匹配）
SEGMENT_PRIORITY = [
    ("duplicate", "完全重复行（跨 sheet 时间重叠导致）"),
    ("cancel_positive_qty", "取消单但数量为正（手工调整，非真实退货）"),
    ("cancellation", "取消/退货单（InvoiceNo 以 C 开头且数量为负）"),
    ("negative_qty", "负数量但非取消单（库存调整/坏账核销）"),
    ("nonpositive_price", "单价 <= 0（赠品/样品/内部调价）"),
    ("missing_customer", "客户ID缺失（匿名交易，仍计入收入）"),
    ("valid_sale", "正常销售（Quantity>0 且 UnitPrice>0 且有客户ID）"),
]


# ================================================================ 工具函数
def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_raw(use_cache: bool = True) -> tuple[pd.DataFrame, dict]:
    """读取双 sheet 并合并，返回 (合并后原表, sheet 元信息)。"""
    assert RAW_XLSX.exists(), (
        f"缺少原始数据文件：{RAW_XLSX}\n"
        "请从 https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip 下载后解压到 data/raw/"
    )

    if (
        use_cache
        and CACHE.exists()
        and (PROCESSED / "_sheet_meta.json").exists()
        and CACHE.stat().st_mtime > RAW_XLSX.stat().st_mtime
    ):
        print(f"[load] 命中缓存 {CACHE.name}，跳过 xlsx 解析")
        return pd.read_parquet(CACHE), json.loads(
            (PROCESSED / "_sheet_meta.json").read_text(encoding="utf-8")
        )

    print(f"[load] 解析 {RAW_XLSX.name}（{RAW_XLSX.stat().st_size / 1e6:.1f} MB），请稍候 ...")
    xl = pd.ExcelFile(RAW_XLSX, engine="openpyxl")
    sheet_meta, frames = {}, []
    for name in xl.sheet_names:
        df = xl.parse(name)
        df = df.rename(columns=COLUMN_MAP)
        # 关键：Invoice 列在 xlsx 里是混合类型（普通单号是 int，取消单 'C489449' 是 str），
        # 不统一成 string 的话 .str.startswith('C') 会因 dtype=object 静默失效
        for col in ("InvoiceNo", "StockCode", "Description", "Country"):
            df[col] = df[col].astype("string")
        df["SourceSheet"] = name
        sheet_meta[name] = {
            "rows": int(len(df)),
            "date_min": str(df["InvoiceDate"].min()),
            "date_max": str(df["InvoiceDate"].max()),
        }
        frames.append(df)
        print(f"[load]   sheet '{name}': {len(df):,} 行")

    # pandas 3.0 起不允许 ignore_index 与 keys 同时使用
    df_all = pd.concat(frames, keys=range(len(frames)), names=["_src"])
    df_all = df_all.reset_index(level="_src").drop(columns="_src").reset_index(drop=True)
    sheet_meta["_merged_rows"] = int(len(df_all))

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_all.to_parquet(CACHE, index=False)
    (PROCESSED / "_sheet_meta.json").write_text(
        json.dumps(sheet_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[load] 合并后 {len(df_all):,} 行，已缓存 -> {CACHE.name}")
    return df_all, sheet_meta


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """类型规整。缺失值一律保留为 NA，不做任何插补。"""
    out = df.copy()

    # 订单号：统一大写去空格。UCI 中存在小写 'c' 开头的取消单，必须一并识别
    out["InvoiceNo"] = out["InvoiceNo"].astype("string").str.strip().str.upper()
    out["StockCode"] = out["StockCode"].astype("string").str.strip().str.upper()
    out["Description"] = out["Description"].astype("string").str.strip()
    out["Country"] = out["Country"].astype("string").str.strip()

    out["Quantity"] = pd.to_numeric(out["Quantity"], errors="coerce").astype("int64")
    out["UnitPrice"] = pd.to_numeric(out["UnitPrice"], errors="coerce").astype("float64")
    # 客户 ID 用可空整数，保留 NA 语义（不能填 0 —— 0 会被当成一个真实客户）
    out["CustomerID"] = pd.to_numeric(out["CustomerID"], errors="coerce").astype("Int64")
    out["InvoiceDate"] = pd.to_datetime(out["InvoiceDate"], errors="coerce")

    # UCI 的 sheet 命名是「财年区间」（Year 2009-2010），取起始年作为来源标识
    out["SourceYear"] = out["SourceSheet"].astype("string").str.extract(r"(\d{4})-(\d{4})")[0].astype("int64")

    # 派生字段
    out["IsCancelled"] = out["InvoiceNo"].str.startswith("C").fillna(False)
    out["Revenue"] = out["Quantity"] * out["UnitPrice"]
    out["IsSpecialCode"] = out["StockCode"].isin(SPECIAL_STOCKCODES)
    out["InvoiceYearMonth"] = out["InvoiceDate"].dt.strftime("%Y-%m")

    return out


def assign_segment(df: pd.DataFrame) -> pd.DataFrame:
    """
    打互斥分段标签。判定顺序即优先级，先命中先归属，后续不再覆盖。
    这样 sum(各段计数) 必然等于总行数 —— 加总口径闭环。
    """
    out = df.copy()
    # 判重基于业务列（不含 SourceSheet/SourceYear，否则跨 sheet 重叠不会被识别）
    dup_mask = out.duplicated(subset=BUSINESS_COLS, keep="first")

    seg = pd.Series("valid_sale", index=out.index, dtype="string")
    seg[out["CustomerID"].isna()] = "missing_customer"
    seg[out["UnitPrice"] <= 0] = "nonpositive_price"
    seg[out["Quantity"] < 0] = "negative_qty"
    seg[out["IsCancelled"]] = "cancellation"
    # 真实数据里存在极少数「C 开头但数量为正」的单（如 C496350 手工调整），
    # 它不是退货，若混入 returns 会让退货率口径失真 —— 单列一段并排除
    seg[out["IsCancelled"] & (out["Quantity"] > 0)] = "cancel_positive_qty"
    seg[dup_mask] = "duplicate"   # 最高优先级：重复行先归为 duplicate

    out["Segment"] = seg
    return out


# ================================================================ 质量报告
def build_quality_report(df: pd.DataFrame, sheet_meta: dict, checksum: str) -> dict:
    """df 为 normalize 后、去重前的全量数据。"""
    seg_counts = df["Segment"].value_counts().to_dict()
    seg_revenue = df.groupby("Segment", observed=True)["Revenue"].sum().round(2).to_dict()

    report = {
        "source": {
            "file": RAW_XLSX.name,
            "size_bytes": RAW_XLSX.stat().st_size,
            "sha256": checksum,
            "sheets": {k: v for k, v in sheet_meta.items() if not k.startswith("_")},
        },
        "volume": {
            "total_rows_raw": int(len(df)),
            "sheet_rows_sum": int(sum(v["rows"] for k, v in sheet_meta.items() if not k.startswith("_"))),
            "date_min": str(df["InvoiceDate"].min()),
            "date_max": str(df["InvoiceDate"].max()),
            "months_covered": int(df["InvoiceYearMonth"].nunique()),
            "unique_invoice_raw": int(df["InvoiceNo"].nunique()),
            "unique_customer_raw": int(df["CustomerID"].nunique()),
            "unique_stockcode_raw": int(df["StockCode"].nunique()),
            "unique_country_raw": int(df["Country"].nunique()),
        },
        "nulls": {c: int(df[c].isna().sum()) for c in BUSINESS_COLS},
        "anomalies": {
            "duplicate_rows": int((df["Segment"] == "duplicate").sum()),
            "cancelled_invoice_rows": int(df["IsCancelled"].sum()),
            "negative_qty_rows": int((df["Quantity"] < 0).sum()),
            "zero_qty_rows": int((df["Quantity"] == 0).sum()),
            "zero_price_rows": int((df["UnitPrice"] == 0).sum()),
            "negative_price_rows": int((df["UnitPrice"] < 0).sum()),
            "missing_customer_rows": int(df["CustomerID"].isna().sum()),
            "special_code_rows": int(df["IsSpecialCode"].sum()),
        },
        "segments": {
            "counts": {k: int(seg_counts.get(k, 0)) for k, _ in SEGMENT_PRIORITY},
            "revenue": {k: float(seg_revenue.get(k, 0.0)) for k, _ in SEGMENT_PRIORITY},
            "definitions": {k: v for k, v in SEGMENT_PRIORITY},
        },
    }
    return report


# ================================================================ 主流程
def main(use_cache: bool = True) -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 0. 校验和（数据可复现的凭据）
    checksum = sha256_of(RAW_XLSX)
    (RAW_DIR / "CHECKSUM.txt").write_text(
        f"{checksum}  {RAW_XLSX.name}\n", encoding="utf-8"
    )

    # 1. 读取 + 规整
    df_raw, sheet_meta = read_raw(use_cache=use_cache)
    total_raw = len(df_raw)
    print(f"[step] 原始合并行数: {total_raw:,}")

    df = normalize(df_raw)
    del df_raw

    # 2. 分段
    df = assign_segment(df)

    # 3. 质量报告
    report = build_quality_report(df, sheet_meta, checksum)
    (PROCESSED / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 4. 切表
    sales = df[df["Segment"].isin(["valid_sale", "missing_customer"])].copy()
    returns = df[df["Segment"] == "cancellation"].copy()

    # 5. 口径硬约束（不满足直接崩，绝不静默放过）
    assert (sales["Quantity"] > 0).all(), "sales 中出现非正数量"
    assert (sales["UnitPrice"] > 0).all(), "sales 中出现非正单价"
    assert not sales["InvoiceNo"].str.startswith("C").any(), "sales 中出现取消单"
    assert returns["InvoiceNo"].str.startswith("C").all(), "returns 中出现非取消单"
    assert (returns["Quantity"] < 0).all(), "returns 中出现非负数量"
    assert (returns["Revenue"] < 0).all(), "returns 中出现非负退款额"

    seg_sum = sum(report["segments"]["counts"].values())
    assert seg_sum == total_raw, f"分段加总 {seg_sum:,} != 原始行数 {total_raw:,}"

    # 6. 清洗流水账（链式扣减，每一步 rows_in - removed == rows_out）
    counts = report["segments"]["counts"]
    steps = [
        ("duplicate", "删除完全重复行（跨 sheet 时间重叠）", "business 8 列完全相同，保留首条"),
        ("cancel_positive_qty", "排除取消单中数量为正的异常行", "C 单却为正数量，属手工调整，非真实退货"),
        ("cancellation", "拆出取消/退货单（C 开头且数量为负）", "单独成表 returns，用于退货分析"),
        ("negative_qty", "排除负数量非取消单", "库存调整/坏账核销，无客户销售语义"),
        ("nonpositive_price", "排除单价 <= 0 记录", "赠品/样品/内部调价，不产生收入"),
        ("missing_customer", "拆出匿名交易（客户ID缺失）", "仍计入收入，仅从客户级分析排除"),
    ]
    log, cur_rows = [], total_raw
    log.append(dict(step=0, description="两 sheet 合并原始行数", rows_in=total_raw,
                    rows_out=total_raw, removed=0, reason="-"))
    for i, (key, desc, reason) in enumerate(steps, start=1):
        removed = counts[key]
        log.append(dict(step=i, description=desc, rows_in=cur_rows,
                        rows_out=cur_rows - removed, removed=removed, reason=reason))
        cur_rows -= removed
    assert cur_rows == counts["valid_sale"], f"流水账末态 {cur_rows:,} != valid_sale {counts['valid_sale']:,}"

    log_df = pd.DataFrame(log)
    log_df.to_csv(PROCESSED / "cleaning_log.csv", index=False, encoding="utf-8-sig")

    # 7. 落盘
    keep = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
            "UnitPrice", "CustomerID", "Country", "SourceSheet", "SourceYear",
            "IsCancelled", "Revenue", "IsSpecialCode", "InvoiceYearMonth"]
    sales[keep].to_parquet(PROCESSED / "transactions.parquet", index=False)
    returns[keep].to_parquet(PROCESSED / "returns.parquet", index=False)

    # 8. 控制台摘要
    gross = sales["Revenue"].sum()
    refund = returns["Revenue"].sum()  # 负数
    print("\n" + "=" * 66)
    print("数据质量报告")
    print("=" * 66)
    print(log_df.to_string(index=False))
    print("-" * 66)
    print(f"原始行数            {total_raw:>12,}")
    for k, label in SEGMENT_PRIORITY:
        mark = "└" if k == "valid_sale" else "├"
        print(f"  {mark} {label:<24}    {counts[k]:>10,}")
    print(f"加总校验            {seg_sum:>12,}  {'OK' if seg_sum == total_raw else 'FAIL'}")
    print("-" * 66)
    print(f"时间范围            {report['volume']['date_min'][:10]} ~ {report['volume']['date_max'][:10]}")
    print(f"销售收入(gross)     £{gross:>14,.2f}")
    print(f"退货金额(refund)    £{refund:>14,.2f}")
    print(f"净收入(net)         £{gross + refund:>14,.2f}")
    print(f"订单数(正常销售)    {sales['InvoiceNo'].nunique():>12,}")
    print(f"客户数(有ID)        {sales['CustomerID'].nunique():>12,}")
    print("=" * 66)
    print(f"[done] transactions={len(sales):,} 行 | returns={len(returns):,} 行")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true", help="忽略 parquet 缓存，强制重读 xlsx")
    main(use_cache=not ap.parse_args().no_cache)
