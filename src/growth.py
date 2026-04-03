"""
Growth calculator: fetches historical fund NAV data via yfinance and
computes what missed contributions would have grown to if invested on time,
using a weighted blend across the actual portfolio allocation.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from reconcile import DiscrepancyType, MatchResult


@dataclass
class GrowthResult:
    deduction_date: pd.Timestamp
    pay_period: str
    missed_amount: float
    fund_ticker: str
    nav_on_due_date: Optional[float]
    nav_current: Optional[float]
    growth_factor: Optional[float]
    current_value: Optional[float]
    missed_growth: Optional[float]
    error: Optional[str] = None


def fetch_fund_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch adjusted close prices for a fund ticker between start and end dates.
    Returns a DataFrame with columns ['Date', 'Close'].
    """
    fund = yf.Ticker(ticker)
    hist = fund.history(start=start, end=end)
    if hist.empty:
        raise ValueError(f"No price data returned for {ticker} ({start} to {end})")
    hist = hist[["Close"]].reset_index()
    hist.columns = ["Date", "Close"]
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None)
    return hist


def _nearest_nav(history: pd.DataFrame, target_date: pd.Timestamp,
                 max_lookback_days: int = 5) -> Optional[float]:
    """
    Get the NAV closest to (but not after) the target date.
    Markets may be closed on the exact date, so we look back up to
    max_lookback_days to find the nearest trading day.
    """
    window_start = target_date - timedelta(days=max_lookback_days)
    candidates = history[
        (history["Date"] >= window_start) & (history["Date"] <= target_date)
    ]
    if candidates.empty:
        return None
    return float(candidates.iloc[-1]["Close"])


def _blended_growth_factor(
    histories: dict[str, pd.DataFrame],
    allocations: dict[str, float],
    due_date: pd.Timestamp,
    current_date: pd.Timestamp,
) -> Optional[float]:
    """
    Compute a weighted-average growth factor across all funds in the portfolio.
    Each fund's growth factor (NAV_current / NAV_due) is weighted by its
    allocation percentage.
    """
    total_weight = 0.0
    weighted_factor = 0.0

    for ticker, weight_pct in allocations.items():
        if ticker not in histories:
            continue
        nav_due = _nearest_nav(histories[ticker], due_date)
        nav_now = _nearest_nav(histories[ticker], current_date)
        if nav_due and nav_now and nav_due > 0:
            fund_factor = nav_now / nav_due
            weighted_factor += fund_factor * (weight_pct / 100.0)
            total_weight += weight_pct / 100.0

    if total_weight == 0:
        return None

    # Normalize in case weights don't sum to exactly 1.0
    return weighted_factor / total_weight


def calculate_missed_growth(
    results: list[MatchResult],
    fund_allocations: dict[str, float],
    as_of_date: Optional[date] = None,
) -> list[GrowthResult]:
    """
    For each discrepant MatchResult, calculate the missed growth using a
    blended growth factor across the full portfolio allocation.

    For MISSING and UNFUNDED deposits, the full expected_total is the missed amount.
    For PARTIAL deposits, only the shortfall is the missed amount.
    For LATE deposits with no shortfall, growth is calculated on the full amount
    for the delayed period only.
    """
    if as_of_date is None:
        as_of_date = date.today()

    discrepant = [r for r in results if not r.is_clean]
    if not discrepant:
        return []

    earliest = min(r.deduction_date for r in discrepant)
    start_str = (earliest - timedelta(days=10)).strftime("%Y-%m-%d")
    end_str = (as_of_date + timedelta(days=1)).strftime("%Y-%m-%d")

    # Fetch history for all funds in the portfolio
    histories: dict[str, pd.DataFrame] = {}
    for ticker in fund_allocations:
        try:
            histories[ticker] = fetch_fund_history(ticker, start_str, end_str)
        except Exception as e:
            print(f"  Warning: could not fetch {ticker}: {e}")

    current_ts = pd.Timestamp(as_of_date)
    alloc_desc = ", ".join(f"{t} {w}%" for t, w in fund_allocations.items())
    growth_results: list[GrowthResult] = []

    for r in discrepant:
        if DiscrepancyType.MISSING in r.discrepancies or DiscrepancyType.UNFUNDED in r.discrepancies:
            missed_amount = r.expected_total
        elif (DiscrepancyType.PARTIAL in r.discrepancies
              or DiscrepancyType.LATE_AND_PARTIAL in r.discrepancies):
            missed_amount = r.amount_shortfall
        elif DiscrepancyType.LATE in r.discrepancies:
            # Late but full amount: calculate the growth difference between
            # on-time investment and actual late investment
            missed_amount = r.expected_total
            factor_due = _blended_growth_factor(histories, fund_allocations, r.deduction_date, current_ts)
            factor_actual = _blended_growth_factor(histories, fund_allocations, r.deposit_date, current_ts) if r.deposit_date else None

            if factor_due and factor_actual:
                value_if_on_time = missed_amount * factor_due
                value_actual = missed_amount * factor_actual
                missed_growth_val = value_if_on_time - value_actual
                growth_results.append(GrowthResult(
                    deduction_date=r.deduction_date,
                    pay_period=r.pay_period,
                    missed_amount=missed_amount,
                    fund_ticker=f"Blended ({alloc_desc})",
                    nav_on_due_date=None,
                    nav_current=None,
                    growth_factor=factor_due,
                    current_value=value_if_on_time,
                    missed_growth=missed_growth_val,
                ))
            else:
                growth_results.append(GrowthResult(
                    deduction_date=r.deduction_date,
                    pay_period=r.pay_period,
                    missed_amount=missed_amount,
                    fund_ticker="Blended",
                    nav_on_due_date=None,
                    nav_current=None,
                    growth_factor=None,
                    current_value=None,
                    missed_growth=None,
                    error="Insufficient NAV data for late-deposit growth calc",
                ))
            continue
        else:
            continue

        blended_factor = _blended_growth_factor(
            histories, fund_allocations, r.deduction_date, current_ts
        )

        if blended_factor is not None:
            current_value = missed_amount * blended_factor
            missed_growth_val = current_value - missed_amount
            growth_results.append(GrowthResult(
                deduction_date=r.deduction_date,
                pay_period=r.pay_period,
                missed_amount=missed_amount,
                fund_ticker=f"Blended ({alloc_desc})",
                nav_on_due_date=None,
                nav_current=None,
                growth_factor=blended_factor,
                current_value=current_value,
                missed_growth=missed_growth_val,
            ))
        else:
            growth_results.append(GrowthResult(
                deduction_date=r.deduction_date,
                pay_period=r.pay_period,
                missed_amount=missed_amount,
                fund_ticker="Blended",
                nav_on_due_date=None,
                nav_current=None,
                growth_factor=None,
                current_value=None,
                missed_growth=None,
                error="Could not retrieve NAV data for growth calculation",
            ))

    return growth_results


def growth_summary(growth_results: list[GrowthResult]) -> dict:
    total_missed_principal = sum(g.missed_amount for g in growth_results)
    total_missed_growth = sum(g.missed_growth or 0 for g in growth_results)
    total_current_value = sum(g.current_value or 0 for g in growth_results)
    errors = [g for g in growth_results if g.error]

    return {
        "total_missed_principal": total_missed_principal,
        "total_missed_growth": total_missed_growth,
        "total_owed": total_missed_principal + total_missed_growth,
        "total_current_value": total_current_value,
        "items_with_errors": len(errors),
    }
