# 数据说明

## 目录用途

| 路径 | 用途 | 是否入库 |
|---|---|---|
| `data/raw/` | 原始数据（`online_retail_II.xlsx`） | ❌ 不入库 |
| `data/processed/` | 清洗后的 parquet、SQLite 数据库 | ❌ 不入库 |
| `reports/sql_results/` | SQL 查询结果 CSV（**入库**，体积小、可复现） | ✅ 入库 |

## 数据来源

- **数据集**：Online Retail II
- **发布方**：UCI Machine Learning Repository
- **官方页面**：https://archive.ics.uci.edu/dataset/502/online+retail+ii
- **下载地址**：https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip
- **内容**：一家英国注册的在线零售商的全部交易流水，时间跨度 **2009-12-01 ~ 2011-12-09**
- **许可**：CC BY 4.0

## 原始文件结构

单文件 `online_retail_II.xlsx`，包含两个 sheet：

| Sheet 名 | 行数（原始） | 时间范围 |
|---|---|---|
| `Year 2009-2010` | 525,461 | 2009-12-01 ~ 2010-12-09 |
| `Year 2010-2011` | 541,910 | 2010-12-01 ~ 2011-12-09 |
| **合计** | **1,067,371** | 2009-12-01 ~ 2011-12-09 |

> 注意：两个 sheet **在时间上有重叠**（2010-12-01 ~ 2010-12-09 同时出现在两个 sheet 中）。
> 清洗阶段按 `InvoiceNo + StockCode + Quantity + InvoiceDate + UnitPrice` 判重，重叠部分会被识别为完全重复行删除。

字段（原始表头）与项目内标准名对应：

| 原始列名 | 项目内标准名 | 类型 | 说明 |
|---|---|---|---|
| `Invoice` | `InvoiceNo` | str | 订单号，`C` 开头 = 取消/退货 |
| `StockCode` | `StockCode` | str | 商品编码 |
| `Description` | `Description` | str | 商品名称 |
| `Quantity` | `Quantity` | int | 数量，负数 = 退货/调整 |
| `InvoiceDate` | `InvoiceDate` | datetime | 交易时间（精确到分钟） |
| `Price` | `UnitPrice` | float | 单价（英镑 GBP） |
| `Customer ID` | `CustomerID` | float | 客户 ID，缺失 = 匿名交易 |
| `Country` | `Country` | str | 客户所在国家 |

## 为什么原始数据不入库

1. `online_retail_II.xlsx` 单文件 **44 MB**，超过 GitHub 单文件推荐体积（50 MB 硬限制、1 MB 以上即警告）；
2. 数据是**公开可复现**的，仓库只需保存「获取方式 + 校验和」，无需保存副本；
3. 清洗产物（parquet / SQLite）**可由原始数据一键重建**，属于派生物，同样不入库。

## 复现步骤

```bash
# 1. 下载并解压（任选其一）
#    方式 A：浏览器打开 https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip
#    方式 B：命令行
curl -L -o data/raw/online_retail_ii.zip "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
unzip data/raw/online_retail_ii.zip -d data/raw/

# 2. 校验文件完整性（Linux/macOS）
shasum -a 256 data/raw/online_retail_II.xlsx

# 3. 跑通管线
python src/clean_data.py     # 清洗 → data/processed/*.parquet
python src/build_db.py       # 建库 → data/processed/retail.db
python src/run_sql.py        # 9 条 SQL → reports/sql_results/*.csv
streamlit run dashboard/app.py
```

## 文件校验和

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `online_retail_II.xlsx` | 45,622,278 bytes | 见 `data/raw/CHECKSUM.txt`（由 `clean_data.py` 首次运行生成） |

> 若校验和与官方不一致，说明下载被截断或篡改，请重新下载 —— **不要用残缺文件跑分析**，
> 否则所有结论都会失真。
