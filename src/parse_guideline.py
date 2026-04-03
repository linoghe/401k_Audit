#!/usr/bin/env python3
"""
Guideline transaction CSV parser: extracts payroll contribution deposits
from Guideline's detailed participant transaction list and writes actual_deposits.csv.

Supports two Guideline export formats:
  - dc_participant_transaction_list: has Requested/Fulfilled dates, Pre-tax/Roth/Employer split
  - guideline_transactions.csv: simpler format with combined Amount and Status

Usage:
    python src/parse_guideline.py
    python src/parse_guideline.py -i data/dc_participant_transaction_list\ \(1\).csv -o data/actual_deposits.csv
"""

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class PayrollDeposit:
    transaction_id: str
    requested_date: str
    fulfilled_date: str
    pretax: float
    roth: float
    employer: float
    total: float
    is_fulfilled: bool

    @property
    def status(self) -> str:
        return "Completed" if self.is_fulfilled else "Processing"

    @property
    def employee_total(self) -> float:
        return self.pretax + self.roth


def _parse_dollar(s: str) -> float:
    cleaned = s.replace("$", "").replace(",", "").replace('"', "").strip()
    if not cleaned or cleaned == "-":
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _normalize_date(date_str: str) -> str:
    """Convert various date formats to YYYY-MM-DD."""
    date_str = date_str.strip().strip('"')
    if not date_str:
        return ""
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def parse_detailed_transaction_list(input_path: str) -> list[PayrollDeposit]:
    """Parse dc_participant_transaction_list CSV (detailed format with Fulfilled dates)."""
    deposits = []

    with open(input_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            txn_type = row.get("Transaction type", "").strip()
            if txn_type != "Payroll":
                continue

            requested = _normalize_date(row.get("Requested date", ""))
            fulfilled = _normalize_date(row.get("Fulfilled date", ""))

            deposits.append(PayrollDeposit(
                transaction_id=row.get("Transaction Id", "").strip(),
                requested_date=requested,
                fulfilled_date=fulfilled,
                pretax=_parse_dollar(row.get("Pre-tax", "0")),
                roth=_parse_dollar(row.get("Roth", "0")),
                employer=_parse_dollar(row.get("Employer", "0")),
                total=_parse_dollar(row.get("Total", "0")),
                is_fulfilled=bool(fulfilled),
            ))

    deposits.sort(key=lambda d: d.requested_date)
    return deposits


def parse_simple_transactions(input_path: str) -> list[PayrollDeposit]:
    """Parse guideline_transactions.csv (simple format with Status column)."""
    deposits = []

    with open(input_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            txn_type = row.get("Type", "").strip()
            if txn_type != "Payroll":
                continue

            date_str = _normalize_date(row.get("Date", ""))
            status = row.get("Status", "").strip()

            deposits.append(PayrollDeposit(
                transaction_id=row.get("Transaction ID", "").strip(),
                requested_date=date_str,
                fulfilled_date=date_str if status.lower() == "completed" else "",
                pretax=0.0,
                roth=0.0,
                employer=0.0,
                total=_parse_dollar(row.get("Amount", "0")),
                is_fulfilled=status.lower() == "completed",
            ))

    deposits.sort(key=lambda d: d.requested_date)
    return deposits


def detect_and_parse(input_path: str) -> list[PayrollDeposit]:
    """Auto-detect CSV format and parse accordingly."""
    with open(input_path, "r", encoding="utf-8") as f:
        header = f.readline()

    if "Fulfilled date" in header or "Transaction type" in header:
        print(f"  Detected detailed transaction list format")
        return parse_detailed_transaction_list(input_path)
    else:
        print(f"  Detected simple transaction format")
        return parse_simple_transactions(input_path)


def write_actual_deposits_csv(deposits: list[PayrollDeposit], output_path: str):
    """Write parsed deposits to actual_deposits.csv format."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date",
            "deposit_amount",
            "fund_ticker",
            "shares_purchased",
            "nav_price",
            "source_notes",
            "transaction_id",
            "status",
            "fulfilled_date",
            "pretax",
            "roth",
            "employer",
        ])

        for d in deposits:
            date_to_use = d.fulfilled_date if d.is_fulfilled else d.requested_date
            source = f"Guideline txn {d.transaction_id}"
            if d.is_fulfilled:
                source += f" (fulfilled {d.fulfilled_date})"
            else:
                source += " (UNFUNDED - no fulfilled date)"

            writer.writerow([
                date_to_use,
                f"{d.total:.2f}",
                "",
                "",
                "",
                source,
                d.transaction_id,
                d.status,
                d.fulfilled_date,
                f"{d.pretax:.2f}",
                f"{d.roth:.2f}",
                f"{d.employer:.2f}",
            ])

    print(f"\nWrote {len(deposits)} payroll deposits to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Parse Guideline transaction CSV into actual_deposits.csv"
    )
    parser.add_argument(
        "-i", "--input",
        default="data/dc_participant_transaction_list (1).csv",
        help="Input Guideline CSV",
    )
    parser.add_argument(
        "-o", "--output",
        default="data/actual_deposits.csv",
        help="Output CSV path (default: data/actual_deposits.csv)",
    )
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_path = (
        args.input if os.path.isabs(args.input)
        else os.path.join(project_root, args.input)
    )
    output_path = (
        args.output if os.path.isabs(args.output)
        else os.path.join(project_root, args.output)
    )

    print(f"Parsing Guideline transactions from {input_path}...\n")
    deposits = detect_and_parse(input_path)

    if not deposits:
        print("No payroll deposits found. Exiting.")
        sys.exit(1)

    completed = [d for d in deposits if d.is_fulfilled]
    unfunded = [d for d in deposits if not d.is_fulfilled]
    total_completed = sum(d.total for d in completed)
    total_unfunded = sum(d.total for d in unfunded)

    print(f"\n--- Guideline Payroll Deposits ---")
    print(f"  Total payroll transactions:  {len(deposits)}")
    print(f"  Completed (funded):          {len(completed)}  (${total_completed:,.2f})")
    print(f"  Unfunded (no fulfilled date):{len(unfunded)}  (${total_unfunded:,.2f})")

    if unfunded:
        print(f"\n  UNFUNDED transactions (no fulfilled date):")
        for d in unfunded:
            print(f"    {d.requested_date}  ${d.total:>10,.2f}  "
                  f"(pretax=${d.pretax:.2f} roth=${d.roth:.2f} employer=${d.employer:.2f})  "
                  f"{d.transaction_id}")

    print(f"\n  All payroll deposits:")
    for d in deposits:
        marker = " *** UNFUNDED ***" if not d.is_fulfilled else ""
        fulfilled = d.fulfilled_date if d.is_fulfilled else "(none)"
        print(f"    {d.requested_date}  ${d.total:>10,.2f}  "
              f"fulfilled={fulfilled:<12}{marker}")

    write_actual_deposits_csv(deposits, output_path)


if __name__ == "__main__":
    main()
