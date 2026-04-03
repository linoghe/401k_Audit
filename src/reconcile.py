"""
Reconciliation engine: matches payroll deductions to actual 401k deposits
and flags discrepancies (missing, partial, late, unfunded).

Handles Guideline-format deposits where employee + employer contributions
are combined into a single payroll deposit, and "Processing" status
indicates unfunded contributions.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Optional

import pandas as pd


class DiscrepancyType(Enum):
    MISSING = "missing"
    PARTIAL = "partial"
    LATE = "late"
    LATE_AND_PARTIAL = "late_and_partial"
    UNFUNDED = "unfunded"


@dataclass
class MatchResult:
    deduction_date: pd.Timestamp
    deduction_amount: float
    employer_match: float
    expected_total: float
    pay_period: str
    deposit_date: Optional[pd.Timestamp] = None
    deposit_amount: Optional[float] = None
    deposit_status: str = ""
    amount_shortfall: float = 0.0
    days_to_deposit: Optional[int] = None
    discrepancies: list[DiscrepancyType] = field(default_factory=list)
    source_deduction: str = ""
    source_deposit: str = ""
    transaction_id: str = ""

    @property
    def is_clean(self) -> bool:
        return len(self.discrepancies) == 0

    @property
    def status(self) -> str:
        if self.is_clean:
            return "OK"
        return ", ".join(d.value for d in self.discrepancies)


def load_deductions(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    required = {"date", "deduction_amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"payroll_deductions.csv missing columns: {missing}")
    if "employer_match" not in df.columns:
        df["employer_match"] = 0.0
    return df


def load_deposits(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    required = {"date", "deposit_amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"actual_deposits.csv missing columns: {missing}")
    if "status" not in df.columns:
        df["status"] = "Completed"
    if "transaction_id" not in df.columns:
        df["transaction_id"] = ""
    return df


def reconcile(
    deductions: pd.DataFrame,
    deposits: pd.DataFrame,
    match_window_days: int = 7,
    late_threshold_days: int = 15,
    amount_tolerance: float = 0.50,
) -> list[MatchResult]:
    """
    For each payroll deduction (where deduction_amount > 0), find the best
    matching deposit. Guideline deposits combine employee + employer, so we
    match against the expected total (deduction_amount + employer_match).

    Deposits with status 'Processing' are treated as unfunded — the employer
    submitted the record but never actually transferred the money.
    """
    results: list[MatchResult] = []
    claimed_deposit_indices: set[int] = set()

    # Only reconcile pay periods where money was supposed to be contributed
    active_deductions = deductions[deductions["deduction_amount"] > 0]

    for _, ded_row in active_deductions.iterrows():
        ded_date = ded_row["date"]
        ded_amount = float(ded_row["deduction_amount"])
        emp_match = float(ded_row.get("employer_match", 0))
        expected_total = ded_amount + emp_match
        pay_period = str(ded_row.get("pay_period", ""))
        source_ded = str(ded_row.get("source_notes", ""))

        window_start = ded_date
        window_end = ded_date + timedelta(days=match_window_days)

        # Search for matching deposit within the window
        candidates = deposits[
            (deposits["date"] >= window_start)
            & (deposits["date"] <= window_end)
            & (~deposits.index.isin(claimed_deposit_indices))
        ]

        # If no match in the normal window, widen search
        if candidates.empty:
            wide_end = ded_date + timedelta(days=late_threshold_days * 2)
            candidates = deposits[
                (deposits["date"] > window_end)
                & (deposits["date"] <= wide_end)
                & (~deposits.index.isin(claimed_deposit_indices))
            ]
            is_late_search = True
        else:
            is_late_search = False

        if candidates.empty:
            results.append(MatchResult(
                deduction_date=ded_date,
                deduction_amount=ded_amount,
                employer_match=emp_match,
                expected_total=expected_total,
                pay_period=pay_period,
                amount_shortfall=expected_total,
                discrepancies=[DiscrepancyType.MISSING],
                source_deduction=source_ded,
            ))
            continue

        # Pick closest deposit by date
        time_diffs = (candidates["date"] - ded_date).abs()
        best_idx = time_diffs.idxmin()
        best = candidates.loc[best_idx]
        claimed_deposit_indices.add(best_idx)

        dep_amount = float(best["deposit_amount"])
        dep_status = str(best.get("status", "Completed")).strip()
        dep_date = best["date"]
        days_delta = (dep_date - ded_date).days
        txn_id = str(best.get("transaction_id", ""))
        source_dep = str(best.get("source_notes", ""))

        # Check if the deposit is unfunded (stuck in Processing)
        if dep_status.lower() == "processing":
            results.append(MatchResult(
                deduction_date=ded_date,
                deduction_amount=ded_amount,
                employer_match=emp_match,
                expected_total=expected_total,
                pay_period=pay_period,
                deposit_date=dep_date,
                deposit_amount=dep_amount,
                deposit_status=dep_status,
                amount_shortfall=expected_total,
                days_to_deposit=days_delta,
                discrepancies=[DiscrepancyType.UNFUNDED],
                source_deduction=source_ded,
                source_deposit=source_dep,
                transaction_id=txn_id,
            ))
            continue

        # Deposit is completed — check for shortfall and lateness
        shortfall = max(0.0, expected_total - dep_amount)
        discs: list[DiscrepancyType] = []
        is_late = is_late_search or days_delta > late_threshold_days
        is_partial = shortfall > amount_tolerance

        if is_late and is_partial:
            discs.append(DiscrepancyType.LATE_AND_PARTIAL)
        elif is_late:
            discs.append(DiscrepancyType.LATE)
        elif is_partial:
            discs.append(DiscrepancyType.PARTIAL)

        results.append(MatchResult(
            deduction_date=ded_date,
            deduction_amount=ded_amount,
            employer_match=emp_match,
            expected_total=expected_total,
            pay_period=pay_period,
            deposit_date=dep_date,
            deposit_amount=dep_amount,
            deposit_status=dep_status,
            amount_shortfall=shortfall,
            days_to_deposit=days_delta,
            discrepancies=discs,
            source_deduction=source_ded,
            source_deposit=source_dep,
            transaction_id=txn_id,
        ))

    return results


def summarize(results: list[MatchResult]) -> dict:
    total_expected = sum(r.expected_total for r in results)
    total_deposited_completed = sum(
        r.deposit_amount or 0 for r in results
        if r.deposit_status.lower() != "processing"
    )
    total_shortfall = sum(r.amount_shortfall for r in results)
    n_clean = sum(1 for r in results if r.is_clean)
    n_missing = sum(1 for r in results if DiscrepancyType.MISSING in r.discrepancies)
    n_unfunded = sum(1 for r in results if DiscrepancyType.UNFUNDED in r.discrepancies)
    n_partial = sum(
        1 for r in results
        if DiscrepancyType.PARTIAL in r.discrepancies
        or DiscrepancyType.LATE_AND_PARTIAL in r.discrepancies
    )
    n_late = sum(
        1 for r in results
        if DiscrepancyType.LATE in r.discrepancies
        or DiscrepancyType.LATE_AND_PARTIAL in r.discrepancies
    )

    total_employee_deducted = sum(r.deduction_amount for r in results)
    total_employer_match = sum(r.employer_match for r in results)

    return {
        "total_deductions": len(results),
        "total_employee_deducted": total_employee_deducted,
        "total_employer_match": total_employer_match,
        "total_expected": total_expected,
        "total_deposited_completed": total_deposited_completed,
        "total_shortfall": total_shortfall,
        "clean_matches": n_clean,
        "missing_deposits": n_missing,
        "unfunded_deposits": n_unfunded,
        "partial_deposits": n_partial,
        "late_deposits": n_late,
    }
