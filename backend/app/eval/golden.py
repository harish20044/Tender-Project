"""The golden set: tenders whose correct answers are known.

Measuring extraction needs documents where the right answer is not in doubt.
Real tender packs are the eventual target, but every one of them has to be
read and annotated by hand before it can score anything, so the set starts
with a document this project builds itself — the layout, the wording and
every figure in it are defined here, which makes the expected values
genuinely known rather than asserted.

That buys two things a real pack cannot: the page each fact sits on is known
exactly, so page attribution is measurable and not merely plausible; and the
figures are written in the forms Indian tenders actually use — crore, lakh,
months — so unit conversion is exercised rather than assumed.

The obvious limitation is that a synthetic document cannot prove performance
on a real one. `GoldenCase` is shaped so a real annotated pack drops in
beside this one as soon as there is a pack to annotate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldenPage:
    heading: str
    body: str


@dataclass(frozen=True)
class GoldenCase:
    """One document, its expected facts, and where each one sits."""

    key: str
    reference: str
    title: str
    pages: tuple[GoldenPage, ...]
    # fact key -> the value extraction should produce, in the unit the
    # extraction schema declares for it.
    expected_facts: dict[str, object]
    # fact key -> the 1-based page it is stated on.
    expected_pages: dict[str, int] = field(default_factory=dict)
    # Questions whose answers the document genuinely does not contain. A
    # system that answers these is inventing, which is the failure that
    # matters most here.
    unanswerable: tuple[str, ...] = ()


NHAI_BYPASS = GoldenCase(
    key="nhai-bypass",
    reference="GOLDEN/NHAI/RO-BPL/2026-27/EPC-14",
    title="Four-lane bypass with major bridge, NH-46 Sehore",
    pages=(
        GoldenPage(
            "NOTICE INVITING TENDER",
            """National Highways Authority of India
Regional Office, Bhopal

NIT No: NHAI/RO-BPL/2026-27/EPC-14
Name of Work: Construction of a four-lane bypass including a major bridge at
Km 42.500 on NH-46, Sehore District, Madhya Pradesh, on EPC Mode.

Estimated Contract Value: Rs. 284,50,00,000 (Rupees Two Hundred Eighty Four
Crore Fifty Lakh only).

Earnest Money Deposit (EMD): Rs. 1,42,25,000 (Rupees One Crore Forty Two Lakh
Twenty Five Thousand only). The EMD shall be submitted in the form of a Bank
Guarantee from any Scheduled Commercial Bank, valid for 180 days from the date
of bid submission. Micro and Small Enterprises registered under NSIC are exempt
from payment of EMD on production of a valid registration certificate.

Tender Document Fee: Rs. 25,000 (non-refundable), payable online only.

Contract Type: Engineering, Procurement and Construction (EPC) - Lump Sum.
""",
        ),
        GoldenPage(
            "CRITICAL DATES",
            """Date of Publication: 14 September 2026
Last Date for Pre-Bid Queries: 24 September 2026, 17:00 hrs
Pre-Bid Meeting: 29 September 2026, 11:00 hrs at the Regional Office, Bhopal
Bid Submission End Date: 21 October 2026, 15:00 hrs
Technical Bid Opening: 22 October 2026, 15:30 hrs
Bid Validity Period: 120 days from the date of bid opening.

Contract Completion Period: 24 months from the date of the Appointed Date,
including the monsoon period.

Defect Liability Period: 60 months from the date of the Completion Certificate.

Milestone 1: 15% of the Contract Price within 6 months.
Milestone 2: 45% of the Contract Price within 14 months.
Milestone 3: 75% of the Contract Price within 20 months.
""",
        ),
        GoldenPage(
            "ELIGIBILITY CRITERIA",
            """1. Financial Capacity
   (a) Minimum Average Annual Turnover of Rs. 142,25,00,000 (Rupees One
       Hundred Forty Two Crore Twenty Five Lakh) during the last three
       financial years.
   (b) The Bidder shall have a positive Net Worth of not less than
       Rs. 28,45,00,000 as at the close of the preceding financial year.

2. Technical Capacity
   (a) The Bidder shall have satisfactorily completed, in the last seven
       financial years, at least one similar work of value not less than
       Rs. 113,80,00,000.
   (b) "Similar work" means construction of highways of at least four lanes
       including at least one major bridge of span not less than 60 metres.
   (c) Minimum seven years of experience in highway construction.

3. Registration
   The Bidder shall be registered as a Class-I (Civil) contractor with any
   Central or State Government department or Public Sector Undertaking.

4. Joint Ventures
   Joint Ventures of not more than three members are permitted. The Lead
   Member shall hold not less than 51% interest.

5. Subcontracting
   Subcontracting is permitted up to a maximum of 30% of the Contract Price.
   Subcontracting of the bridge superstructure works is not permitted.
""",
        ),
        GoldenPage(
            "CONDITIONS OF CONTRACT",
            """Performance Security: The successful Bidder shall furnish a Performance
Security equal to 5% of the Contract Price within 28 days of the Letter of
Acceptance, in the form of an unconditional Bank Guarantee.

Retention Money: 5% of each running account bill shall be retained, released
against the Completion Certificate and the expiry of the Defect Liability
Period in equal halves.

Mobilisation Advance: An interest-bearing mobilisation advance of up to 10% of
the Contract Price may be granted against an equivalent Bank Guarantee.

Payment Terms: Running account bills shall be submitted monthly and paid within
28 days of certification by the Engineer.

Price Escalation: Price adjustment shall be payable in accordance with the
formula at Clause 14.8, linked to the Wholesale Price Index.

Liquidated Damages: Liquidated damages for delay shall be levied at 0.05% of
the Contract Price per day of delay, subject to a maximum of 10% of the
Contract Price.

Termination: The Authority may terminate the Contract if the Contractor fails
to achieve any Milestone by more than 90 days.

Dispute Resolution: Disputes shall first be referred to the Dispute Resolution
Board. Unresolved disputes shall be settled by arbitration under the
Arbitration and Conciliation Act, 1996, seated at New Delhi.

Insurance: The Contractor shall maintain Contractor's All Risk insurance and
Third Party Liability insurance of not less than Rs. 5,00,00,000 per occurrence.
""",
        ),
    ),
    expected_facts={
        # Written as crore and lakh in the document; expected in plain rupees.
        "value.estimated": 2_845_000_000,
        "emd.amount": 14_225_000,
        "fee.tender_document": 25_000,
        "security.performance_percent": 5,
        "advance.mobilisation_percent": 10,
        "eligibility.annual_turnover_minimum": 1_422_500_000,
        "eligibility.net_worth_minimum": 284_500_000,
        "eligibility.similar_work_value_minimum": 1_138_000_000,
        "eligibility.experience_years_minimum": 7,
        "eligibility.jv_allowed": True,
        "eligibility.subcontracting_percent_max": 30,
        # Text facts are scored on containment either way round, so the
        # expected value is the minimum that must appear, not the only
        # acceptable wording: "Class-I" also accepts "Class-I (Civil)".
        "eligibility.contractor_class": "Class-I",
        "risk.arbitration_seat": "New Delhi",
        "date.bid_submission": "2026-10-21",
        "date.bid_opening": "2026-10-22",
        # Written as months; expected in days.
        "duration.completion_days": 730,
        "duration.defect_liability_days": 1825,
        "duration.bid_validity_days": 120,
        "risk.liquidated_damages_percent_per_day": 0.05,
        "risk.liquidated_damages_cap_percent": 10,
        "risk.price_escalation_allowed": True,
        "risk.retention_percent": 5,
    },
    expected_pages={
        "value.estimated": 1,
        "emd.amount": 1,
        "fee.tender_document": 1,
        "date.bid_submission": 2,
        "date.bid_opening": 2,
        "duration.completion_days": 2,
        "duration.defect_liability_days": 2,
        "duration.bid_validity_days": 2,
        "eligibility.annual_turnover_minimum": 3,
        "eligibility.net_worth_minimum": 3,
        "eligibility.similar_work_value_minimum": 3,
        "eligibility.experience_years_minimum": 3,
        "eligibility.jv_allowed": 3,
        "eligibility.subcontracting_percent_max": 3,
        "eligibility.contractor_class": 3,
        "risk.arbitration_seat": 4,
        "security.performance_percent": 4,
        "advance.mobilisation_percent": 4,
        "risk.liquidated_damages_percent_per_day": 4,
        "risk.liquidated_damages_cap_percent": 4,
        "risk.price_escalation_allowed": 4,
        "risk.retention_percent": 4,
    },
    unanswerable=(
        # Deliberately absent: the document states no refund terms, no
        # equipment requirement and no local-content rule.
        "When and how is the EMD refunded?",
        "What plant and equipment must the bidder own or deploy?",
        "Are there local content or Make-in-India requirements?",
    ),
)

CASES: tuple[GoldenCase, ...] = (NHAI_BYPASS,)
CASES_BY_KEY: dict[str, GoldenCase] = {case.key: case for case in CASES}

# The trailer's /ID array is the one part of a generated PDF that is random
# per write, and it is the only thing that stops two renders being identical.
_TRAILER_ID = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]*)>\s*<([0-9A-Fa-f]*)>\s*\]")


def _freeze_document_id(pdf: bytes) -> bytes:
    """Blank the trailer's random document ID.

    Overwritten in place rather than rewritten, so the result is exactly as
    long as the original: ``startxref`` and the cross-reference table hold
    byte offsets, and shifting anything by even one byte would produce a
    corrupt file.
    """
    match = _TRAILER_ID.search(pdf)
    if match is None:
        return pdf
    frozen = bytearray(pdf)
    for group in (1, 2):
        start, end = match.span(group)
        frozen[start:end] = b"0" * (end - start)
    return bytes(frozen)


def render_pdf(case: GoldenCase) -> bytes:
    """Build the case's PDF.

    Generated rather than committed as a binary: the text above is then the
    single definition of both the document and its expected answers, and the
    two cannot drift apart in review.

    Byte-for-byte reproducible, which matters more than it looks. Ingest
    identifies a document version by the hash of its bytes, so a PDF carrying
    a creation timestamp is a different document on every run: the corpus
    fills with versions of the same file, and retrieval ends up answering
    from whichever copy happens to rank first. Fixing the metadata makes a
    re-run reuse the version it already has.
    """
    import pymupdf

    document = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        document.set_metadata(  # type: ignore[no-untyped-call]
            {
                "title": case.title,
                "author": "Tender Intelligence evaluation set",
                "subject": case.reference,
                "creator": "app.eval.golden",
                "producer": "app.eval.golden",
                # A fixed date, so the bytes do not change run to run.
                "creationDate": "D:20260101000000Z",
                "modDate": "D:20260101000000Z",
            }
        )
        for page_spec in case.pages:
            page = document.new_page()
            page.insert_text((60, 70), page_spec.heading, fontsize=15, fontname="helv")
            page.insert_textbox(
                pymupdf.Rect(60, 95, 540, 780),  # type: ignore[no-untyped-call]
                page_spec.body,
                fontsize=9.5,
                fontname="helv",
                lineheight=1.35,
            )
        return _freeze_document_id(bytes(document.tobytes()))  # type: ignore[no-untyped-call]
    finally:
        document.close()  # type: ignore[no-untyped-call]
