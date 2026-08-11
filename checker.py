"""
checker.py
----------
Turns raw evidence (collected by scraper.py) into exactly one of four
verification statuses, following evidence-based rules only:

    VERIFIED               official source explicitly names the exam
    NO ENTRANCE EXAM        official source explicitly states merit/direct admission
    CROSS-CHECK REQUIRED    relevant but ambiguous / conflicting / stale evidence
    NOT FOUND                no reliable current official information located

Nothing here guesses. If the rules cannot confidently pick a status, the
result is CROSS-CHECK REQUIRED or NOT FOUND, never a fabricated VERIFIED.

Optional LLM classification (config.USE_LLM_CLASSIFICATION) may only
classify/extract from evidence already collected - see llm_classify(). It
is never used to invent an exam that wasn't in the evidence.
"""

from __future__ import annotations

from datetime import date

import config


def _year_is_current(evidence, year: int) -> bool:
    """Heuristic: does this piece of evidence appear to relate to the
    requested admission year (vs. an older, possibly-stale document)?"""
    y = str(year)
    y_prev = str(year - 1)
    haystack = f"{evidence.text} {evidence.source_url} {evidence.source_title}".lower()
    if y in haystack:
        return True
    # "2025-26" style ranges ending in this year also count.
    if f"{y_prev[2:]}-{y[2:]}" in haystack:
        return True
    return False


def _best_evidence(evidence_list):
    """Pick the single most representative evidence item: lowest tier
    (most official) first, then year-current, then HIGH relevance."""
    if not evidence_list:
        return None
    return sorted(
        evidence_list,
        key=lambda e: (e.tier, 0 if getattr(e, "_year_current", False) else 1,
                        0 if e.relevance == "HIGH" else 1),
    )[0]


def llm_classify(evidence_texts: list[str], course: str) -> dict | None:
    """OPTIONAL: classify already-collected evidence with an LLM. Disabled
    by default (config.USE_LLM_CLASSIFICATION). Must only extract/classify
    from `evidence_texts` - never invent an answer. Returns None if the
    feature is off or unavailable, in which case deterministic rules alone
    decide the outcome (as required by the project spec).
    """
    if not config.USE_LLM_CLASSIFICATION:
        return None
    # Intentionally not implemented in this offline project: wiring this to
    # the Anthropic API is a one-function change (see README "Optional AI
    # Component"). Left as None so deterministic rules remain authoritative.
    return None


def verify(university: str, course: str, year: int, evidence_list, scrape_notes: list[str]) -> dict:
    """Apply evidence-based rules and return a result dict ready for store.py."""

    for e in evidence_list:
        e._year_current = _year_is_current(e, year)

    official = [e for e in evidence_list if e.is_official]
    third_party = [e for e in evidence_list if not e.is_official]

    exam_evidence = [e for e in official if e.matched_exams]
    no_exam_evidence = [e for e in official if e.no_exam_phrase]

    distinct_exams = sorted({exam for e in exam_evidence for exam in e.matched_exams})
    ambiguous_present = any(e.ambiguous_phrase for e in exam_evidence + no_exam_evidence)
    any_year_current = any(e._year_current for e in exam_evidence + no_exam_evidence)
    conflicting = len(distinct_exams) > 1

    status = config.STATUS_NOT_FOUND
    confidence = config.CONFIDENCE_LOW
    entrance_exam = None
    admission_route = "Unknown"
    conflicting_sources = ""
    notes = list(scrape_notes)

    if conflicting:
        status = config.STATUS_CROSS_CHECK
        confidence = config.CONFIDENCE_MEDIUM
        entrance_exam = " / ".join(distinct_exams)
        admission_route = "Entrance Examination (conflicting reports)"
        conflicting_sources = "; ".join(
            f"{e.source_title} ({e.source_url}) -> {', '.join(e.matched_exams)}"
            for e in exam_evidence
        )
        notes.append("Multiple official sources disagree on the entrance exam; human review required.")

    elif exam_evidence and not ambiguous_present and any_year_current:
        status = config.STATUS_VERIFIED
        confidence = config.CONFIDENCE_HIGH
        entrance_exam = distinct_exams[0] if len(distinct_exams) == 1 else " / ".join(distinct_exams)
        admission_route = "Entrance Examination"

    elif exam_evidence and (ambiguous_present or not any_year_current):
        status = config.STATUS_CROSS_CHECK
        confidence = config.CONFIDENCE_MEDIUM
        entrance_exam = distinct_exams[0] if len(distinct_exams) == 1 else " / ".join(distinct_exams)
        admission_route = "Entrance Examination (unconfirmed)"
        if not any_year_current:
            notes.append(
                f"Evidence found does not clearly confirm it applies to admission year {year}; "
                "may be an older document."
            )
        if ambiguous_present:
            notes.append("Wording is hedged/ambiguous (e.g. 'may be considered', 'or equivalent').")

    elif no_exam_evidence and not ambiguous_present:
        status = config.STATUS_NO_EXAM
        confidence = config.CONFIDENCE_HIGH if any_year_current else config.CONFIDENCE_MEDIUM
        admission_route = "Merit-based / Direct Admission"
        if not any_year_current:
            notes.append(f"Merit-based admission stated, but not explicitly confirmed for {year}.")

    elif no_exam_evidence and ambiguous_present:
        status = config.STATUS_CROSS_CHECK
        confidence = config.CONFIDENCE_MEDIUM
        admission_route = "Unclear (merit vs entrance exam wording is ambiguous)"
        notes.append("Ambiguous wording around merit-based vs entrance-exam admission.")

    elif not official and third_party:
        third_party_exams = sorted({exam for e in third_party for exam in e.matched_exams})
        if third_party_exams:
            status = config.STATUS_CROSS_CHECK
            confidence = config.CONFIDENCE_LOW
            entrance_exam = " / ".join(third_party_exams)
            admission_route = "Entrance Examination (unverified - third-party source only)"
            notes.append(
                "Only third-party sources mention an exam for this programme; "
                "no official source was found to confirm it."
            )
        else:
            status = config.STATUS_NOT_FOUND
            notes.append("No official or third-party evidence located.")

    else:
        status = config.STATUS_NOT_FOUND
        if not evidence_list:
            notes.append("No relevant evidence found on the official website.")

    best = _best_evidence(exam_evidence or no_exam_evidence or official or evidence_list)

    result = {
        "university": university,
        "official_website": None,  # filled in by store.py from the input row
        "course": course,
        "admission_year": year,
        "programme_level": _infer_programme_level(course),
        "admission_route": admission_route,
        "entrance_exam": entrance_exam,
        "exam_authority": config.EXAM_TO_AUTHORITY.get(distinct_exams[0]) if len(distinct_exams) == 1 else None,
        "eligibility": None,
        "selection_criteria": None,
        "application_info": None,
        "evidence": best.text if best else None,
        "source_title": best.source_title if best else None,
        "source_url": best.source_url if best else None,
        "pdf_url": best.source_url if (best and best.source_type == "brochure") else None,
        "pdf_page": best.pdf_page if best else None,
        "source_date": best.source_date if best else None,
        "verification_status": status,
        "confidence": confidence,
        "checked_date": date.today().isoformat(),
        "conflicting_sources": conflicting_sources,
        "notes": " | ".join(notes) if notes else "",
    }
    return result


def _infer_programme_level(course: str) -> str:
    c = course.strip().lower()
    pg = {"mba", "mca", "m.tech", "mtech", "m.com", "mcom", "ma", "msc", "m.sc"}
    if c in pg:
        return "Postgraduate"
    return "Undergraduate"
