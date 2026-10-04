"""The fifty standard questions asked of every construction tender.

These are the questions an estimator works through before deciding whether to
bid, grouped by what they decide. They are stable across tenders, which is why
they are answered once at ingest and cached: recomputing them per page view
would be the largest avoidable token cost in the system.

Each key is stable and is what ``faq_answers.faq_key`` stores, so a question's
wording can be improved without orphaning the answers already cached against
it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Faq:
    key: str
    question: str
    category: str


FAQS: tuple[Faq, ...] = (
    # --- Identification -----------------------------------------------------
    Faq("tender.reference", "What is the tender reference number?", "Identification"),
    Faq("tender.authority", "Which authority is inviting this tender?", "Identification"),
    Faq("tender.scope", "What work is being tendered, in scope terms?", "Identification"),
    Faq("tender.location", "Where is the work located?", "Identification"),
    Faq(
        "tender.contract_type",
        "What type of contract is this (item rate, EPC, lump sum)?",
        "Identification",
    ),
    # --- Money --------------------------------------------------------------
    Faq("value.estimated", "What is the estimated contract value?", "Value"),
    Faq("emd.amount", "What is the earnest money deposit (EMD) amount?", "Value"),
    Faq("emd.form", "In what form must the EMD be submitted?", "Value"),
    Faq("emd.exemption", "Are any bidders exempt from paying EMD?", "Value"),
    Faq("emd.refund", "When and how is the EMD refunded?", "Value"),
    Faq("fee.tender_document", "What is the tender document fee?", "Value"),
    Faq(
        "security.performance", "What performance security or bank guarantee is required?", "Value"
    ),
    Faq("security.retention", "What retention money is withheld from payments?", "Value"),
    Faq("payment.terms", "What are the payment terms and running-bill schedule?", "Value"),
    Faq("payment.advance", "Is a mobilisation advance offered, and on what terms?", "Value"),
    Faq("price.escalation", "Is price escalation or variation payable?", "Value"),
    # --- Deadlines ----------------------------------------------------------
    Faq("date.published", "When was this tender published?", "Deadlines"),
    Faq("date.clarification", "What is the deadline for pre-bid queries?", "Deadlines"),
    Faq("date.prebid_meeting", "When and where is the pre-bid meeting?", "Deadlines"),
    Faq("date.submission", "What is the bid submission deadline?", "Deadlines"),
    Faq("date.opening", "When will the bids be opened?", "Deadlines"),
    Faq("date.validity", "How long must the bid remain valid?", "Deadlines"),
    Faq("duration.completion", "What is the contract completion period?", "Deadlines"),
    Faq("duration.defect_liability", "What is the defect liability period?", "Deadlines"),
    Faq("milestones.schedule", "Are interim milestones defined, and what are they?", "Deadlines"),
    # --- Eligibility --------------------------------------------------------
    Faq("eligibility.turnover", "What minimum annual turnover is required?", "Eligibility"),
    Faq(
        "eligibility.experience_similar", "What similar-work experience is required?", "Eligibility"
    ),
    Faq(
        "eligibility.experience_years", "How many years of experience are required?", "Eligibility"
    ),
    Faq("eligibility.networth", "What net worth or solvency is required?", "Eligibility"),
    Faq(
        "eligibility.registration",
        "What contractor registration or class is required?",
        "Eligibility",
    ),
    Faq(
        "eligibility.jv",
        "Are joint ventures or consortia permitted, and on what terms?",
        "Eligibility",
    ),
    Faq(
        "eligibility.subcontracting",
        "Is subcontracting allowed, and to what extent?",
        "Eligibility",
    ),
    Faq(
        "eligibility.equipment",
        "What plant and equipment must the bidder own or deploy?",
        "Eligibility",
    ),
    Faq("eligibility.personnel", "What key personnel must the bidder provide?", "Eligibility"),
    Faq(
        "eligibility.local_content",
        "Are there local content or Make-in-India requirements?",
        "Eligibility",
    ),
    # --- Submission ---------------------------------------------------------
    Faq("submission.mode", "How must the bid be submitted (online, physical, both)?", "Submission"),
    Faq("submission.envelopes", "What envelope or packet structure is required?", "Submission"),
    Faq("submission.documents", "What documents must accompany the bid?", "Submission"),
    Faq(
        "submission.boq_format",
        "In what format must the BOQ or price bid be submitted?",
        "Submission",
    ),
    Faq("submission.dsc", "Is a digital signature certificate required?", "Submission"),
    # --- Evaluation ---------------------------------------------------------
    Faq("evaluation.method", "How will bids be evaluated and the winner selected?", "Evaluation"),
    Faq(
        "evaluation.technical_criteria",
        "What are the technical qualification criteria?",
        "Evaluation",
    ),
    Faq("evaluation.weighting", "How are technical and financial scores weighted?", "Evaluation"),
    Faq("evaluation.rejection", "What causes a bid to be rejected outright?", "Evaluation"),
    # --- Risk ---------------------------------------------------------------
    Faq("risk.liquidated_damages", "What liquidated damages apply for delay?", "Risk"),
    Faq("risk.penalty", "What other penalties or deductions are specified?", "Risk"),
    Faq("risk.termination", "On what grounds can the contract be terminated?", "Risk"),
    Faq("risk.arbitration", "How are disputes resolved, and where?", "Risk"),
    Faq("risk.force_majeure", "What force majeure provisions apply?", "Risk"),
    Faq("risk.insurance", "What insurance must the contractor carry?", "Risk"),
)

FAQS_BY_KEY: dict[str, Faq] = {faq.key: faq for faq in FAQS}

CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(faq.category for faq in FAQS))


def faqs_for(category: str | None = None) -> tuple[Faq, ...]:
    if category is None:
        return FAQS
    return tuple(faq for faq in FAQS if faq.category.lower() == category.lower())
