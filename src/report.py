"""
Report generator: renders the reconciliation data into a detailed HTML report.
"""

import csv
import os
from dataclasses import dataclass
from datetime import date

from jinja2 import Environment, FileSystemLoader

from growth import GrowthResult, growth_summary
from reconcile import MatchResult, summarize


@dataclass
class FundDataPoint:
    date: str
    close: float


def generate_report(
    match_results: list[MatchResult],
    growth_results: list[GrowthResult],
    fund_histories: dict[str, list[FundDataPoint]],
    config: dict,
    output_path: str,
    evidence: dict = None,
) -> str:
    """
    Render the full HTML report and write it to output_path.
    Returns the absolute path to the written file.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(project_root, "templates")

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,
    )
    template = env.get_template("report.html")

    recon_summ = summarize(match_results)
    growth_summ = growth_summary(growth_results) if growth_results else None

    fund_allocations = config.get("fund_allocations", {})
    fund_tickers = list(fund_allocations.keys())
    date_range = config.get("date_range", {})

    html = template.render(
        generated_date=date.today().isoformat(),
        date_range_start=date_range.get("start", "N/A"),
        date_range_end=date_range.get("end", "N/A"),
        recon_summary=recon_summ,
        growth_summ=growth_summ,
        match_results=match_results,
        growth_results=growth_results,
        fund_tickers=fund_tickers,
        fund_allocations=fund_allocations,
        match_window_days=config.get("match_window_days", 7),
        late_threshold_days=config.get("late_deposit_threshold_days", 15),
        fund_data_appendix=fund_histories,
        w2=evidence.get("w2") if evidence else None,
        annual_summaries=evidence.get("annual_summaries", []) if evidence else [],
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return os.path.abspath(output_path)


def generate_summary_csv(
    match_results: list[MatchResult],
    growth_results: list[GrowthResult],
    output_path: str,
) -> str:
    """
    Write a machine-readable CSV summarizing each deduction and its outcome.
    """
    growth_by_date = {
        g.deduction_date: g for g in growth_results
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "pay_period",
            "deduction_date",
            "employee_deduction",
            "employer_match",
            "expected_total",
            "deposit_date",
            "deposit_amount",
            "deposit_status",
            "shortfall",
            "days_to_deposit",
            "status",
            "missed_growth",
            "total_owed",
            "transaction_id",
            "source_deduction",
            "source_deposit",
        ])

        for r in match_results:
            g = growth_by_date.get(r.deduction_date)
            missed_growth = f"{g.missed_growth:.2f}" if g and g.missed_growth else ""
            total_owed = ""
            if g and g.missed_growth is not None:
                total_owed = f"{r.amount_shortfall + g.missed_growth:.2f}"
            elif r.amount_shortfall > 0:
                total_owed = f"{r.amount_shortfall:.2f}"

            writer.writerow([
                r.pay_period,
                r.deduction_date.strftime("%Y-%m-%d"),
                f"{r.deduction_amount:.2f}",
                f"{r.employer_match:.2f}",
                f"{r.expected_total:.2f}",
                r.deposit_date.strftime("%Y-%m-%d") if r.deposit_date else "",
                f"{r.deposit_amount:.2f}" if r.deposit_amount else "",
                r.deposit_status,
                f"{r.amount_shortfall:.2f}",
                r.days_to_deposit if r.days_to_deposit is not None else "",
                r.status,
                missed_growth,
                total_owed,
                r.transaction_id,
                r.source_deduction,
                r.source_deposit,
            ])

    return os.path.abspath(output_path)
