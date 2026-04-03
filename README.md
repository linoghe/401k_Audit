# 401k Audit Reconciliation Tool

Reconciles payroll 401k deductions against actual deposits into a 401k account, identifies discrepancies, and calculates missed investment growth for each shortfall using real fund performance data.

Produces a detailed HTML report suitable for DOL complaints or legal action.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

1. **Fill in your data** in the CSV files under `data/`:
   - `payroll_deductions.csv` — one row per pay period showing what was withheld
   - `actual_deposits.csv` — one row per deposit that actually appeared in the 401k

2. **Edit `config.yaml`** to set your fund ticker(s), date range, and matching parameters.

3. **Run the audit:**

```bash
python src/main.py
```

4. **Review output** in the `output/` directory:
   - `reconciliation_report.html` — full report with executive summary, line-by-line reconciliation, growth calculations, and methodology
   - `reconciliation_summary.csv` — machine-readable summary of all discrepancies

## How It Works

- Matches each payroll deduction to the nearest actual deposit within a configurable time window.
- Flags missing deposits, partial deposits, and late deposits.
- For each discrepancy, fetches historical NAV data for the fund(s) via Yahoo Finance and computes what the missing money would have grown to if invested on time.
- All calculations and data sources are documented in the report for legal credibility.

## CSV Column Reference

### payroll_deductions.csv

| Column | Description |
|---|---|
| `date` | Pay date (YYYY-MM-DD) |
| `gross_pay` | Gross pay for the period |
| `deduction_amount` | Dollar amount withheld for 401k |
| `deduction_pct` | Contribution percentage |
| `pay_period` | Label (e.g., "2023-W02", "Jan 1-15") |
| `source_notes` | Where this data came from (e.g., "pay stub") |

### actual_deposits.csv

| Column | Description |
|---|---|
| `date` | Date deposit appeared in 401k (YYYY-MM-DD) |
| `deposit_amount` | Dollar amount deposited |
| `fund_ticker` | Fund the deposit was invested in |
| `shares_purchased` | Number of shares bought |
| `nav_price` | NAV price at time of purchase |
| `source_notes` | Where this data came from (e.g., "Fidelity statement") |
