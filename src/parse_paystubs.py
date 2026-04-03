#!/usr/bin/env python3
"""
Gusto pay stub PDF parser: extracts 401(k) deduction data from Gusto-format
pay stub PDFs and writes payroll_deductions.csv.

Usage:
    python src/parse_paystubs.py                          # defaults
    python src/parse_paystubs.py -d data/ -o data/payroll_deductions.csv
"""

import argparse
import csv
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PayStubData:
    filename: str
    pay_period_start: str
    pay_period_end: str
    pay_date: str
    gross_pay: float
    traditional_401k: float = 0.0
    roth_401k: float = 0.0
    employer_401k_match: float = 0.0
    pretax_deductions: float = 0.0
    is_off_cycle: bool = False

    @property
    def total_employee_401k(self) -> float:
        return self.traditional_401k + self.roth_401k

    @property
    def deduction_pct(self) -> float:
        if self.gross_pay == 0:
            return 0.0
        return round((self.total_employee_401k / self.gross_pay) * 100, 2)


def _parse_dollar(s: str) -> float:
    cleaned = s.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _normalize_date(date_str: str) -> str:
    """Convert 'Mar 14, 2025' or similar to 'YYYY-MM-DD'."""
    from datetime import datetime
    date_str = date_str.strip().rstrip(".")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def parse_paystub_text(text: str, filename: str) -> PayStubData:
    """Parse the text content of a single Gusto pay stub PDF."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    pay_period_start = ""
    pay_period_end = ""
    pay_date = ""
    is_off_cycle = False
    gross_pay = 0.0
    traditional_401k = 0.0
    roth_401k = 0.0
    employer_401k_match = 0.0
    pretax_deductions = 0.0

    # --- Extract pay period and pay date ---
    # Standard: "Pay period: Sep 16, 2024 - Sep 30, 2024 Pay Day: Oct 1, 2024"
    # Off-cycle: "Pay period: Off-Cycle Payroll" then next line has the dates
    full_text = " ".join(lines)

    off_cycle_match = re.search(r"Off-Cycle Payroll", full_text)
    if off_cycle_match:
        is_off_cycle = True

    pay_period_match = re.search(
        r"(\w+ \d{1,2},? \d{4})\s*-\s*(\w+ \d{1,2},? \d{4})\s*Pay Day:\s*(\w+ \d{1,2},? \d{4})",
        full_text
    )
    if pay_period_match:
        pay_period_start = _normalize_date(pay_period_match.group(1))
        pay_period_end = _normalize_date(pay_period_match.group(2))
        pay_date = _normalize_date(pay_period_match.group(3))

    # --- Extract gross pay from Totals line ---
    totals_match = re.search(
        r"Totals\s+[\d.]+\s+\$([\d,]+\.\d{2})",
        full_text
    )
    if totals_match:
        gross_pay = _parse_dollar(totals_match.group(1))

    # --- Identify sections and extract deductions ---
    section = None
    for line in lines:
        if "Employee Deductions" in line:
            section = "employee_deductions"
            continue
        elif "Employer Contributions" in line:
            section = "employer_contributions"
            continue
        elif line.startswith("Summary"):
            section = "summary"
            continue
        elif any(line.startswith(s) for s in [
            "Employee Gross Earnings", "Employee Taxes", "Employer Tax",
            "Sick Policy", "Time Off Policy"
        ]):
            section = None
            continue

        if section == "employee_deductions":
            if "Traditional 401(k)" in line or (
                "401(k)" in line and "Roth" not in line and "Employer" not in line
            ):
                amounts = re.findall(r"\$([\d,]+\.\d{2})", line)
                if amounts:
                    traditional_401k = _parse_dollar(amounts[0])

            elif "Roth 401(k)" in line:
                amounts = re.findall(r"\$([\d,]+\.\d{2})", line)
                if amounts:
                    roth_401k = _parse_dollar(amounts[0])

        elif section == "employer_contributions":
            if "401(k)" in line:
                amounts = re.findall(r"\$([\d,]+\.\d{2})", line)
                if amounts:
                    employer_401k_match += _parse_dollar(amounts[0])

        elif section == "summary":
            if "Pre-Tax Deductions" in line:
                amounts = re.findall(r"\$([\d,]+\.\d{2})", line)
                if amounts:
                    pretax_deductions = _parse_dollar(amounts[0])

    return PayStubData(
        filename=filename,
        pay_period_start=pay_period_start,
        pay_period_end=pay_period_end,
        pay_date=pay_date,
        gross_pay=gross_pay,
        traditional_401k=traditional_401k,
        roth_401k=roth_401k,
        employer_401k_match=employer_401k_match,
        pretax_deductions=pretax_deductions,
        is_off_cycle=is_off_cycle,
    )


def read_pdf_text(path: str) -> str:
    """Extract text from a PDF file using pdfplumber (preferred) or PyPDF2."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass

    raise ImportError(
        "Install pdfplumber or PyPDF2 to parse PDFs: pip install pdfplumber"
    )


def parse_all_paystubs(pdf_dir: str) -> list[PayStubData]:
    """Parse all paystub_*.pdf files in a directory."""
    pattern = os.path.join(pdf_dir, "paystub_*.pdf")
    pdf_files = sorted(glob.glob(pattern))

    if not pdf_files:
        print(f"No paystub_*.pdf files found in {pdf_dir}")
        return []

    results = []
    for pdf_path in pdf_files:
        fname = os.path.basename(pdf_path)
        try:
            text = read_pdf_text(pdf_path)
            stub = parse_paystub_text(text, fname)
            results.append(stub)
            status = f"401k=${stub.total_employee_401k:.2f}" if stub.total_employee_401k > 0 else "no 401k"
            print(f"  Parsed {fname}: pay_date={stub.pay_date}, gross=${stub.gross_pay:.2f}, {status}")
        except Exception as e:
            print(f"  ERROR parsing {fname}: {e}")

    results.sort(key=lambda s: s.pay_date)
    return results


def write_deductions_csv(stubs: list[PayStubData], output_path: str):
    """Write parsed pay stub data to payroll_deductions.csv format."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date",
            "gross_pay",
            "deduction_amount",
            "deduction_pct",
            "pay_period",
            "source_notes",
            "traditional_401k",
            "roth_401k",
            "employer_match",
            "off_cycle",
        ])

        for s in stubs:
            period_label = f"{s.pay_period_start} to {s.pay_period_end}"
            source = f"Gusto paystub {s.filename}"
            writer.writerow([
                s.pay_date,
                f"{s.gross_pay:.2f}",
                f"{s.total_employee_401k:.2f}",
                f"{s.deduction_pct:.2f}",
                period_label,
                source,
                f"{s.traditional_401k:.2f}",
                f"{s.roth_401k:.2f}",
                f"{s.employer_401k_match:.2f}",
                s.is_off_cycle,
            ])

    print(f"\nWrote {len(stubs)} records to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Parse Gusto pay stub PDFs and extract 401(k) deduction data"
    )
    parser.add_argument(
        "-d", "--pdf-dir",
        default="data",
        help="Directory containing paystub_*.pdf files (default: data/)",
    )
    parser.add_argument(
        "-o", "--output",
        default="data/payroll_deductions.csv",
        help="Output CSV path (default: data/payroll_deductions.csv)",
    )
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdf_dir = (
        args.pdf_dir if os.path.isabs(args.pdf_dir)
        else os.path.join(project_root, args.pdf_dir)
    )
    output = (
        args.output if os.path.isabs(args.output)
        else os.path.join(project_root, args.output)
    )

    print(f"Scanning for Gusto pay stubs in {pdf_dir}...\n")
    stubs = parse_all_paystubs(pdf_dir)

    if not stubs:
        print("No pay stubs found. Exiting.")
        sys.exit(1)

    # Summary
    total_401k = sum(s.total_employee_401k for s in stubs)
    total_employer = sum(s.employer_401k_match for s in stubs)
    stubs_with_401k = [s for s in stubs if s.total_employee_401k > 0]
    stubs_without = [s for s in stubs if s.total_employee_401k == 0]

    print(f"\n--- Summary ---")
    print(f"  Total pay stubs parsed:       {len(stubs)}")
    print(f"  Stubs with 401(k) deductions: {len(stubs_with_401k)}")
    print(f"  Stubs without deductions:     {len(stubs_without)}")
    print(f"  Total employee 401(k):        ${total_401k:,.2f}")
    print(f"  Total employer match:         ${total_employer:,.2f}")

    if stubs_without:
        print(f"\n  Pay dates with NO 401(k) deduction:")
        for s in stubs_without:
            print(f"    {s.pay_date} (gross: ${s.gross_pay:,.2f}) - {s.filename}")

    write_deductions_csv(stubs, output)


if __name__ == "__main__":
    main()
