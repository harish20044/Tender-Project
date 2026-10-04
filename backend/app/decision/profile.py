"""What the bidding company can actually do.

A tender is not objectively biddable; it is biddable *by someone*. Every gate
in the engine compares a tender fact against one of these numbers, so the
recommendation is explicitly a recommendation for this company, not a general
verdict on the tender.

Kept as a plain dataclass with a documented default rather than a database
table, because there is one company using this and inventing a management UI
for a single row would be the wrong order of work.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompanyProfile:
    name: str

    # Financial capacity
    annual_turnover: float  # rupees, most recent three-year average
    net_worth: float  # rupees
    # The largest EMD the company can have tied up at once.
    emd_ceiling: float
    # Working capital available to carry a project between running bills.
    working_capital: float

    # Track record
    largest_completed_work: float  # rupees
    years_experience: int
    contractor_classes: tuple[str, ...] = ("Class-I",)

    # Appetite
    max_contract_value: float = 5_000_000_000
    min_contract_value: float = 10_000_000
    # Liquidated damages above this rate are treated as an unusual risk.
    ld_per_day_tolerance: float = 0.1  # percent per day
    ld_cap_tolerance: float = 10.0  # percent of contract price
    # Projects longer than this strain the company's bonding capacity.
    max_duration_days: int = 1095

    # How many days are needed to prepare a serious bid.
    bid_preparation_days: int = 10

    work_categories: tuple[str, ...] = field(
        default_factory=lambda: ("Roads & Highways", "Bridges", "General Civil")
    )


# A mid-size contractor, sized so the sample NHAI tender in the tests sits
# near several thresholds rather than passing or failing everything trivially.
DEFAULT_PROFILE = CompanyProfile(
    name="Default Contractor Profile",
    annual_turnover=1_800_000_000,
    net_worth=400_000_000,
    emd_ceiling=20_000_000,
    working_capital=350_000_000,
    largest_completed_work=1_400_000_000,
    years_experience=12,
    contractor_classes=("Class-I", "Class-II"),
)
