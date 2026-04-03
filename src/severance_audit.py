#!/usr/bin/env python3
"""
Severance Agreement Compliance Audit

Parses the severance agreement terms and reconciles them against actual
paystub data to identify breaches: missed payments, late payments,
unpaid expenses, and outstanding balances.

Usage:
    python src/severance_audit.py
"""

import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jinja2 import Environment, FileSystemLoader


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScheduledPayment:
    number: int
    due_date: date
    amount: float
    description: str = ""


@dataclass
class ActualPayment:
    pay_date: date
    gross_amount: float
    net_amount: float
    pay_period_start: date
    pay_period_end: date
    is_off_cycle: bool
    federal_tax: float = 0.0
    state_tax: float = 0.0
    social_security: float = 0.0
    medicare: float = 0.0
    other_taxes: float = 0.0
    total_taxes: float = 0.0
    traditional_401k: float = 0.0
    roth_401k: float = 0.0
    employer_401k: float = 0.0
    reimbursements: float = 0.0
    source_file: str = ""


@dataclass
class PaymentMatch:
    scheduled: ScheduledPayment
    actual: Optional[ActualPayment] = None
    days_late: Optional[int] = None
    amount_correct: bool = True
    status: str = "OVERDUE"  # ON_TIME, LATE, OVERDUE


@dataclass
class ExpenseObligation:
    description: str
    amount: float
    paid: bool = False
    paid_date: Optional[date] = None
    paid_amount: float = 0.0
    source: str = ""


@dataclass
class AuditResult:
    agreement_date: date
    signature_date: date
    separation_date: date
    employee_name: str
    employer_name: str

    scheduled_payments: list[ScheduledPayment] = field(default_factory=list)
    actual_payments: list[ActualPayment] = field(default_factory=list)
    matches: list[PaymentMatch] = field(default_factory=list)
    expenses: list[ExpenseObligation] = field(default_factory=list)
    extra_payments: list[ActualPayment] = field(default_factory=list)

    total_promised: float = 0.0
    total_paid: float = 0.0
    total_outstanding: float = 0.0
    total_expenses_owed: float = 0.0
    total_expenses_paid: float = 0.0
    total_breach_amount: float = 0.0
    avg_days_late: float = 0.0
    max_days_late: int = 0
    healthcare_end: str = ""
    salary_continuation_end: str = ""

    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agreement terms (hardcoded from the parsed PDF)
# ---------------------------------------------------------------------------

def build_agreement_terms() -> dict:
    return {
        "agreement_date": date(2025, 10, 31),
        "signature_date": date(2025, 11, 25),
        "separation_date": date(2025, 10, 31),
        "employee_name": "Lindsay Ogden-Herrera",
        "employer_name": "Sodha Q Enterprises, LLC DBA agencyQ",
        "employment_start": date(2024, 8, 26),

        "salary_continuation_through": "January 15, 2026",
        "healthcare_continuation_through": "January 2026",

        "scheduled_payments": [
            ScheduledPayment(1, date(2025, 11, 16), 4583.33,
                             "11/16/2025 (or upon signature)"),
            ScheduledPayment(2, date(2025, 12, 1), 4583.33, "12/1/2025"),
            ScheduledPayment(3, date(2025, 12, 16), 4583.33, "12/16/2025"),
            ScheduledPayment(4, date(2026, 1, 1), 4583.33, "1/1/2026"),
            ScheduledPayment(5, date(2026, 1, 16), 4583.33, "1/16/2026"),
            ScheduledPayment(6, date(2026, 2, 1), 4583.33, "2/1/2026"),
        ],
        "total_severance": 27499.98,

        "expenses": [
            ExpenseObligation(
                description="Miro expense reimbursement (non-taxed)",
                amount=4620.23,
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Paystub parsing (reuse the already-parsed CSV)
# ---------------------------------------------------------------------------

def load_paystub_payments(csv_path: str, separation_date: date) -> list[ActualPayment]:
    """Load post-separation paystub records from payroll_deductions.csv."""
    payments = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pay_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            if pay_date <= separation_date:
                continue

            period_parts = row.get("pay_period", "").split(" to ")
            period_start = (
                datetime.strptime(period_parts[0].strip(), "%Y-%m-%d").date()
                if len(period_parts) == 2 else pay_date
            )
            period_end = (
                datetime.strptime(period_parts[1].strip(), "%Y-%m-%d").date()
                if len(period_parts) == 2 else pay_date
            )

            gross = float(row.get("gross_pay", 0))
            trad_401k = float(row.get("traditional_401k", 0))
            roth_401k = float(row.get("roth_401k", 0))
            employer_401k = float(row.get("employer_match", 0))
            off_cycle = row.get("off_cycle", "False").strip().lower() == "true"

            payments.append(ActualPayment(
                pay_date=pay_date,
                gross_amount=gross,
                net_amount=0.0,
                pay_period_start=period_start,
                pay_period_end=period_end,
                is_off_cycle=off_cycle,
                traditional_401k=trad_401k,
                roth_401k=roth_401k,
                employer_401k=employer_401k,
                source_file=row.get("source_notes", ""),
            ))

    payments.sort(key=lambda p: p.pay_date)
    return payments


def enrich_from_pdf(payment: ActualPayment, data_dir: str):
    """Read the original PDF to get tax breakdowns and net pay."""
    import pdfplumber

    pdf_name = payment.source_file.split()[-1] if payment.source_file else ""
    pdf_path = os.path.join(data_dir, pdf_name)
    if not pdf_name or not os.path.exists(pdf_path):
        return

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return

    lines = text.split("\n")
    for line in lines:
        low = line.lower().strip()
        parts = line.split()
        if not parts:
            continue

        def last_dollar(parts_list):
            for p in reversed(parts_list):
                clean = p.replace("$", "").replace(",", "")
                try:
                    return float(clean)
                except ValueError:
                    continue
            return None

        if "federal income tax" in low:
            v = last_dollar(parts)
            if v is not None:
                payment.federal_tax = v
        elif "co withholding" in low or "co income" in low:
            v = last_dollar(parts)
            if v is not None:
                payment.state_tax = v
        elif low.startswith("social security") and "employer" not in low:
            v = last_dollar(parts)
            if v is not None:
                payment.social_security = v
        elif low.startswith("medicare") and "employer" not in low:
            v = last_dollar(parts)
            if v is not None:
                payment.medicare = v
        elif "net pay" in low:
            v = last_dollar(parts)
            if v is not None:
                payment.net_amount = v
        elif "total reimbursements" in low:
            v = last_dollar(parts)
            if v is not None:
                payment.reimbursements = v

    payment.other_taxes = 0.0
    for line in lines:
        low = line.lower().strip()
        if "family and medical leave" in low and "employee" in low:
            parts = line.split()
            v = last_dollar(parts)
            if v is not None:
                payment.other_taxes += v

    payment.total_taxes = (
        payment.federal_tax + payment.state_tax +
        payment.social_security + payment.medicare +
        payment.other_taxes
    )


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def match_payments(
    scheduled: list[ScheduledPayment],
    actual: list[ActualPayment],
) -> tuple[list[PaymentMatch], list[ActualPayment]]:
    """
    Match scheduled severance payments to actual paystub payments
    sequentially: the first actual payment fulfills the first scheduled
    obligation, the second fulfills the second, and so on. Any scheduled
    payment without a corresponding actual payment is MISSING/OVERDUE.
    """
    matches = []
    for i, sp in enumerate(scheduled):
        if i < len(actual):
            ap = actual[i]
            days_late = max(0, (ap.pay_date - sp.due_date).days)
            amount_ok = abs(ap.gross_amount - sp.amount) < 0.02
            status = "ON_TIME" if days_late == 0 else "LATE"
            matches.append(PaymentMatch(
                scheduled=sp,
                actual=ap,
                days_late=days_late,
                amount_correct=amount_ok,
                status=status,
            ))
        else:
            matches.append(PaymentMatch(scheduled=sp, status="OVERDUE"))

    extras = actual[len(scheduled):]
    return matches, extras


# ---------------------------------------------------------------------------
# Full audit
# ---------------------------------------------------------------------------

def run_audit(project_root: str) -> AuditResult:
    terms = build_agreement_terms()

    csv_path = os.path.join(project_root, "data", "payroll_deductions.csv")
    data_dir = os.path.join(project_root, "data")
    actual = load_paystub_payments(csv_path, terms["separation_date"])

    for ap in actual:
        enrich_from_pdf(ap, data_dir)

    matches, extras = match_payments(terms["scheduled_payments"], actual)

    total_promised = terms["total_severance"]
    total_paid = sum(
        m.actual.gross_amount for m in matches if m.actual is not None
    )

    paid_count = sum(1 for m in matches if m.actual is not None)
    late_payments = [m for m in matches if m.status == "LATE"]
    late_days = [m.days_late for m in late_payments if m.days_late]
    avg_late = sum(late_days) / len(late_days) if late_days else 0
    max_late = max(late_days) if late_days else 0

    total_reimbursements = sum(ap.reimbursements for ap in actual)
    expenses = list(terms["expenses"])
    if total_reimbursements > 0:
        for exp in expenses:
            if total_reimbursements >= exp.amount - 0.02:
                exp.paid = True
                exp.paid_amount = exp.amount
                total_reimbursements -= exp.amount

    total_expenses_owed = sum(e.amount for e in expenses)
    total_expenses_paid = sum(e.paid_amount for e in expenses)

    outstanding_sev = total_promised - total_paid
    outstanding_exp = total_expenses_owed - total_expenses_paid
    total_breach = outstanding_sev + outstanding_exp

    notes = []

    if outstanding_sev > 0:
        overdue_count = sum(1 for m in matches if m.status == "OVERDUE")
        notes.append(
            f"{overdue_count} scheduled severance payment(s) remain unpaid, "
            f"leaving ${outstanding_sev:,.2f} overdue."
        )

    if late_payments:
        notes.append(
            f"Of the {paid_count} payments received, {len(late_payments)} were late "
            f"(average {avg_late:.0f} days, worst {max_late} days). The agreement "
            f"specifies exact payment dates."
        )

    if outstanding_exp > 0:
        unpaid = [e for e in expenses if not e.paid]
        for e in unpaid:
            notes.append(
                f"Unpaid expense: {e.description} — ${e.amount:,.2f} outstanding."
            )

    if extras:
        for ep in extras:
            notes.append(
                f"Unscheduled payment received on {ep.pay_date.isoformat()} "
                f"(${ep.gross_amount:,.2f}, period {ep.pay_period_start} – "
                f"{ep.pay_period_end}). This falls outside the agreement's "
                f"payment schedule."
            )

    result = AuditResult(
        agreement_date=terms["agreement_date"],
        signature_date=terms["signature_date"],
        separation_date=terms["separation_date"],
        employee_name=terms["employee_name"],
        employer_name=terms["employer_name"],
        scheduled_payments=terms["scheduled_payments"],
        actual_payments=actual,
        matches=matches,
        expenses=expenses,
        extra_payments=extras,
        total_promised=total_promised,
        total_paid=total_paid,
        total_outstanding=outstanding_sev,
        total_expenses_owed=total_expenses_owed,
        total_expenses_paid=total_expenses_paid,
        total_breach_amount=total_breach,
        avg_days_late=avg_late,
        max_days_late=max_late,
        healthcare_end=terms["healthcare_continuation_through"],
        salary_continuation_end=terms["salary_continuation_through"],
        notes=notes,
    )
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_severance_report(audit: AuditResult, output_path: str) -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(project_root, "templates")

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,
    )
    template = env.get_template("severance_report.html")

    html = template.render(
        audit=audit,
        generated_date=date.today().isoformat(),
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return os.path.abspath(output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    today = date.today().strftime("%Y-%m-%d")
    output_path = os.path.join(project_root, "output", f"severance_audit_report_{today}.html")

    print("=" * 60)
    print("  Severance Agreement Compliance Audit")
    print("=" * 60)

    print("\nLoading paystub data and agreement terms...")
    audit = run_audit(project_root)

    print(f"\n  Employee:      {audit.employee_name}")
    print(f"  Employer:      {audit.employer_name}")
    print(f"  Agreement:     {audit.agreement_date}")
    print(f"  Signed:        {audit.signature_date}")
    print(f"  Separation:    {audit.separation_date}")

    print(f"\n  PAYMENT RECONCILIATION")
    print(f"  ─────────────────────────────────────")
    print(f"  Total promised:        ${audit.total_promised:>10,.2f}")
    print(f"  Total paid (gross):    ${audit.total_paid:>10,.2f}")
    print(f"  Outstanding balance:   ${audit.total_outstanding:>10,.2f}")

    on_time = sum(1 for m in audit.matches if m.status == "ON_TIME")
    late = sum(1 for m in audit.matches if m.status == "LATE")
    missing = sum(1 for m in audit.matches if m.status == "MISSING")
    print(f"\n  On time:  {on_time}   Late:  {late}   Missing:  {missing}")
    if late:
        print(f"  Avg delay: {audit.avg_days_late:.0f} days   "
              f"Max delay: {audit.max_days_late} days")

    print(f"\n  EXPENSE REIMBURSEMENT")
    print(f"  ─────────────────────────────────────")
    for e in audit.expenses:
        status = "PAID" if e.paid else "UNPAID"
        print(f"  {e.description}: ${e.amount:,.2f} [{status}]")

    print(f"\n  TOTAL BREACH AMOUNT:   ${audit.total_breach_amount:>10,.2f}")

    print(f"\n  Notes:")
    for n in audit.notes:
        print(f"  • {n}")

    print(f"\nGenerating report...")
    path = generate_severance_report(audit, output_path)
    print(f"  Report written to: {path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
