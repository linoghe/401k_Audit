#!/usr/bin/env python3
"""
401(k) Audit Reconciliation Tool

Compares payroll 401k deductions against actual deposits, identifies
discrepancies, calculates missed investment growth, and generates
a detailed report.

Usage:
    python src/main.py                  # uses default config.yaml
    python src/main.py -c my_config.yaml
"""

import argparse
import os
import sys
from datetime import date, timedelta

# Allow running as `python src/main.py` from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv

import yaml

from reconcile import load_deductions, load_deposits, reconcile, summarize
from growth import (
    calculate_missed_growth,
    fetch_fund_history,
    growth_summary,
)
from report import FundDataPoint, generate_report, generate_summary_csv


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_corroborating_evidence(project_root: str) -> dict:
    """Load W-2 data and Guideline annual summaries for the report."""
    evidence = {
        "w2": None,
        "annual_summaries": [],
    }

    # W-2 data (hardcoded from parsed PDF — tax year 2025)
    evidence["w2"] = {
        "tax_year": 2025,
        "employer": "Sodha Q Enterprises",
        "ein": "85-0494226",
        "box12_d": 10991.71,
        "box12_d_label": "Elective deferrals to 401(k) [Code D]",
        "box12_aa": 1000.00,
        "box12_aa_label": "Designated Roth contributions [Code AA]",
        "total_reported": 11991.71,
        "source": "2025 W-2 (Gusto)",
    }

    # Guideline annual summaries
    for filename, year in [
        ("dc_participant_annual_summary (4).csv", 2025),
        ("dc_participant_annual_summary (3).csv", 2026),
    ]:
        filepath = os.path.join(project_root, "data", filename)
        if not os.path.exists(filepath):
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Category", "").strip() != "Payroll":
                    continue
                evidence["annual_summaries"].append({
                    "year": year,
                    "pretax": float(row.get("Pre-tax", "0").replace("'", "")),
                    "roth": float(row.get("Roth", "0").replace("'", "")),
                    "employer": float(row.get("Employer", "0").replace("'", "")),
                    "total": float(row.get("Total", "0").replace("'", "")),
                    "pending": float(row.get("Pending", "0").replace("'", "")),
                    "source": f"Guideline annual summary ({filename})",
                })

    return evidence


def main():
    parser = argparse.ArgumentParser(
        description="401(k) Audit Reconciliation Tool"
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = (
        args.config if os.path.isabs(args.config)
        else os.path.join(project_root, args.config)
    )

    print(f"Loading config from {config_path}")
    config = load_config(config_path)

    data_cfg = config.get("data", {})
    output_cfg = config.get("output", {})
    fund_allocations = config.get("fund_allocations", {})
    fund_tickers = list(fund_allocations.keys())
    match_window = config.get("match_window_days", 7)
    late_threshold = config.get("late_deposit_threshold_days", 15)

    ded_path = os.path.join(project_root, data_cfg["payroll_deductions"])
    dep_path = os.path.join(project_root, data_cfg["actual_deposits"])

    # --- Load data ---
    print(f"Loading payroll deductions from {ded_path}")
    deductions = load_deductions(ded_path)
    print(f"  Found {len(deductions)} deduction records")

    print(f"Loading actual deposits from {dep_path}")
    deposits = load_deposits(dep_path)
    print(f"  Found {len(deposits)} deposit records")

    # --- Reconcile ---
    print(f"\nReconciling (match window: {match_window} days, late threshold: {late_threshold} days)...")
    match_results = reconcile(
        deductions, deposits,
        match_window_days=match_window,
        late_threshold_days=late_threshold,
    )

    recon_summ = summarize(match_results)
    print(f"\n  Reconciliation Summary:")
    print(f"    Pay periods with 401(k):  {recon_summ['total_deductions']}")
    print(f"    Employee withheld:         ${recon_summ['total_employee_deducted']:,.2f}")
    print(f"    Employer match reported:   ${recon_summ['total_employer_match']:,.2f}")
    print(f"    Total expected in 401(k):  ${recon_summ['total_expected']:,.2f}")
    print(f"    Actually deposited:        ${recon_summ['total_deposited_completed']:,.2f}")
    print(f"    Total shortfall:           ${recon_summ['total_shortfall']:,.2f}")
    print(f"    Clean matches:             {recon_summ['clean_matches']}")
    print(f"    Unfunded (Processing):     {recon_summ['unfunded_deposits']}")
    print(f"    Missing deposits:          {recon_summ['missing_deposits']}")
    print(f"    Partial deposits:          {recon_summ['partial_deposits']}")
    print(f"    Late deposits:             {recon_summ['late_deposits']}")

    # --- Growth calculations ---
    discrepant = [r for r in match_results if not r.is_clean]
    growth_results = []
    fund_histories_for_report: dict[str, list[FundDataPoint]] = {}

    if discrepant and fund_allocations:
        alloc_desc = ", ".join(f"{t} {w}%" for t, w in fund_allocations.items())
        print(f"\nCalculating missed growth using blended portfolio:")
        print(f"  {alloc_desc}")
        try:
            growth_results = calculate_missed_growth(
                match_results, fund_allocations
            )
            g_summ = growth_summary(growth_results)
            print(f"\n  Growth Summary:")
            print(f"    Missed principal:  ${g_summ['total_missed_principal']:,.2f}")
            print(f"    Missed growth:     ${g_summ['total_missed_growth']:,.2f}")
            print(f"    Total owed:        ${g_summ['total_owed']:,.2f}")
            if g_summ["items_with_errors"] > 0:
                print(f"    Items with errors: {g_summ['items_with_errors']}")

            # Collect fund history data for the report appendix (sampled monthly)
            date_range = config.get("date_range", {})
            start = date_range.get("start", "2024-09-01")
            end_date = date.today() + timedelta(days=1)
            for ticker in fund_tickers:
                try:
                    hist = fetch_fund_history(ticker, start, end_date.isoformat())
                    hist["month"] = hist["Date"].dt.to_period("M")
                    monthly = hist.groupby("month").first().reset_index()
                    fund_histories_for_report[ticker] = [
                        FundDataPoint(
                            date=row["Date"].strftime("%Y-%m-%d"),
                            close=row["Close"],
                        )
                        for _, row in monthly.iterrows()
                    ]
                except Exception as e:
                    print(f"  Warning: could not build appendix for {ticker}: {e}")

        except Exception as e:
            print(f"\n  Error calculating growth: {e}")
            print("  Report will be generated without growth calculations.")
    elif not discrepant:
        print("\nNo discrepancies found — skipping growth calculations.")
    elif not fund_allocations:
        print("\nNo fund allocations configured — skipping growth calculations.")

    # --- Load corroborating evidence ---
    print(f"\nLoading corroborating evidence (W-2, annual summaries)...")
    evidence = load_corroborating_evidence(project_root)
    if evidence["w2"]:
        w2 = evidence["w2"]
        print(f"  W-2 ({w2['tax_year']}): ${w2['total_reported']:,.2f} reported to IRS")
    for s in evidence["annual_summaries"]:
        funded = s["total"] - s["pending"]
        print(f"  Guideline {s['year']}: ${s['total']:,.2f} total, "
              f"${s['pending']:,.2f} pending, ${funded:,.2f} funded")

    # --- Generate outputs ---
    report_path = os.path.join(project_root, output_cfg.get("report_html", "output/reconciliation_report.html"))
    csv_path = os.path.join(project_root, output_cfg.get("summary_csv", "output/reconciliation_summary.csv"))

    print(f"\nGenerating HTML report...")
    report_file = generate_report(
        match_results, growth_results, fund_histories_for_report, config, report_path,
        evidence=evidence,
    )
    print(f"  Report written to: {report_file}")

    print(f"Generating summary CSV...")
    csv_file = generate_summary_csv(match_results, growth_results, csv_path)
    print(f"  CSV written to: {csv_file}")

    print("\nDone.")


if __name__ == "__main__":
    main()
