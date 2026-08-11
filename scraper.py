"""
scraper.py
----------
Collects admission-exam evidence for a (University, Course, Admission Year)
combination from a university's official website, and orchestrates the full
pipeline: read input -> scrape -> verify (checker.py) -> save (store.py).

Run directly:
    python scraper.py

Design notes
------------
* requests + BeautifulSoup is tried first for every page. Playwright is only
  invoked when the static fetch looks like an empty JS-app shell (see
  config.PLAYWRIGHT_FALLBACK_ENABLED / PLAYWRIGHT_MIN_TEXT_LENGTH).
* The crawl is a scored frontier, not a blind crawl: links are ranked by how
  many admission/course/year keywords they contain, and only the top-ranked
  MAX_PAGES_PER_SITE pages are actually visited.
* Evidence is only recorded when a course reference and an exam mention (or
  a "no entrance exam" / ambiguous phrase) appear together in the same
  local text window - this is what keeps evidence course-specific instead
  of "university accepts CUET somewhere on some page".
* Nothing here decides VERIFIED/NOT FOUND/etc. That is checker.py's job.
  scraper.py only ever hands over raw, source-attributed evidence.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pypdf import PdfReader

import config
import utils
from utils import log

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


# --------------------------------------------------------------------------
# EVIDENCE DATA STRUCTURE
# --------------------------------------------------------------------------
@dataclass
class Evidence:
    university: str
    course: str
    admission_year: int
    text: str                       # evidence snippet with context
    source_title: str
    source_url: str
    source_type: str                # notification | brochure | official_page | exam_authority | third_party
    tier: int                       # config.SOURCE_TIER_*
    is_official: bool
    pdf_page: int | None = None
    source_date: str | None = None
    matched_exams: list[str] = field(default_factory=list)
    no_exam_phrase: bool = False
    ambiguous_phrase: bool = False
    relevance: str = "MEDIUM"


# --------------------------------------------------------------------------
# COURSE TERM NORMALIZATION
# --------------------------------------------------------------------------
COURSE_ALIASES = {
    "bba": ["bba", "bachelor of business administration"],
    "mba": ["mba", "master of business administration"],
    "b.tech": ["b.tech", "btech", "b. tech", "bachelor of technology"],
    "btech": ["b.tech", "btech", "b. tech", "bachelor of technology"],
    "bca": ["bca", "bachelor of computer applications"],
    "mca": ["mca", "master of computer applications"],
    "bcom": ["b.com", "bcom", "bachelor of commerce"],
    "mcom": ["m.com", "mcom", "master of commerce"],
}


def course_terms(course: str) -> list[str]:
    key = course.strip().lower().replace(" ", "")
    return COURSE_ALIASES.get(key, [course.strip().lower()])


# --------------------------------------------------------------------------
# LINK SCORING / DISCOVERY
# --------------------------------------------------------------------------
def _year_terms(year: int) -> list[str]:
    y = str(year)
    y_short = y[2:]
    prev_short = str(year - 1)[2:]
    return [y, f"{prev_short}-{y_short}", f"{y_short}-{str(year + 1)[2:]}"]


def score_link(url: str, anchor_text: str, course: str, year: int) -> int:
    haystack = f"{url} {anchor_text}".lower()
    score = 0
    for kw in config.PRIORITY_KEYWORDS:
        if kw in haystack:
            score += 2
    for term in course_terms(course):
        if term in haystack:
            score += 5
    for yt in _year_terms(year):
        if yt in haystack:
            score += 4
    if haystack.strip().endswith(".pdf") or ".pdf" in haystack:
        score += 3
    return score


def extract_links(html: bytes, base_url: str, course: str, year: int) -> list[tuple[str, int, str]]:
    """Return (absolute_url, score, anchor_text) for every same-domain-or-pdf
    link on the page, sorted by score descending."""
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        # Stay on the same domain, unless it's a PDF (brochures are sometimes
        # hosted on a CDN/subdomain) or a recognised exam authority domain.
        same_domain = parsed.netloc == base_domain or parsed.netloc.endswith("." + base_domain)
        is_pdf = abs_url.lower().endswith(".pdf")
        is_exam_authority = any(d in parsed.netloc for d in config.EXAM_AUTHORITY_DOMAINS)
        if not (same_domain or is_pdf or is_exam_authority):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)

        anchor_text = a.get_text(strip=True) or ""
        score = score_link(abs_url, anchor_text, course, year)
        results.append((abs_url, score, anchor_text))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# --------------------------------------------------------------------------
# PAGE FETCHING (requests first, Playwright fallback)
# --------------------------------------------------------------------------
def fetch_rendered_html(url: str) -> tuple[str | None, str | None]:
    """Fetch a page's HTML, falling back to Playwright if the static fetch
    looks like an empty JS shell. Returns (html_text, error)."""
    content, err = utils.fetch(url)
    if content is None:
        return None, err

    text_len = len(BeautifulSoup(content, "html.parser").get_text(strip=True))

    if text_len >= config.PLAYWRIGHT_MIN_TEXT_LENGTH or not config.PLAYWRIGHT_FALLBACK_ENABLED:
        return content.decode("utf-8", errors="ignore"), None

    if not _PLAYWRIGHT_AVAILABLE:
        log.info(f"[WARN] Page looks JS-rendered but Playwright not installed: {url}")
        return content.decode("utf-8", errors="ignore"), None

    log.info(f"[PLAYWRIGHT] Static fetch too thin, rendering with browser: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.USER_AGENT)
            page.goto(url, timeout=config.PLAYWRIGHT_TIMEOUT_MS, wait_until="networkidle")
            html = page.content()
            browser.close()
            utils.cache_set(url, html.encode("utf-8"))
            return html, None
    except Exception as e:
        log.info(f"[WARN] Playwright render failed for {url}: {e}")
        return content.decode("utf-8", errors="ignore"), None


# --------------------------------------------------------------------------
# TEXT / EVIDENCE EXTRACTION
# --------------------------------------------------------------------------
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return _SENTENCE_SPLIT_RE.split(text)


def find_exam_mentions(text: str) -> list[str]:
    lower = text.lower()
    found = []
    for exam_name, patterns in config.KNOWN_EXAMS.items():
        for pat in patterns:
            if re.search(pat, lower):
                found.append(exam_name)
                break
    return found


def classify_source(url: str, anchor_text: str, university_domain: str) -> tuple[str, int, bool]:
    """Return (source_type, tier, is_official)."""
    lower_url = url.lower()
    lower_anchor = anchor_text.lower()
    parsed = urlparse(url)
    domain = parsed.netloc

    is_official_domain = domain == university_domain or domain.endswith("." + university_domain)
    is_exam_authority = any(d in domain for d in config.EXAM_AUTHORITY_DOMAINS)
    is_untrusted = any(d in domain for d in config.UNTRUSTED_DOMAINS)

    if is_exam_authority:
        return "exam_authority", config.SOURCE_TIER_EXAM_AUTHORITY, True

    if is_official_domain:
        if "notification" in lower_url or "notification" in lower_anchor or "circular" in lower_url:
            return "notification", config.SOURCE_TIER_NOTIFICATION, True
        if any(k in lower_url or k in lower_anchor for k in ("brochure", "prospectus")) or lower_url.endswith(".pdf"):
            return "brochure", config.SOURCE_TIER_BROCHURE, True
        return "official_page", config.SOURCE_TIER_OFFICIAL_PAGE, True

    if is_untrusted:
        return "third_party", config.SOURCE_TIER_THIRD_PARTY, False

    return "third_party", config.SOURCE_TIER_THIRD_PARTY, False


def build_evidence_from_text(
    text: str,
    university: str,
    course: str,
    year: int,
    source_title: str,
    source_url: str,
    source_type: str,
    tier: int,
    is_official: bool,
    pdf_page: int | None = None,
) -> list[Evidence]:
    """Scan `text` for windows where a course reference co-occurs with an
    exam mention / no-exam phrase / ambiguous phrase, and turn each into an
    Evidence record with a concise, context-preserving snippet."""
    evidence_list: list[Evidence] = []
    sentences = split_sentences(text)
    if not sentences:
        return evidence_list

    c_terms = course_terms(course)

    for i, sentence in enumerate(sentences):
        lower_sentence = sentence.lower()
        course_hit = any(term in lower_sentence for term in c_terms)
        if not course_hit:
            continue

        # Build a local window: this sentence plus one neighbour on each side,
        # so an exam name in an adjacent sentence is still captured.
        window = " ".join(sentences[max(0, i - 1): min(len(sentences), i + 2)])
        window = window[: config.PDF_CONTEXT_CHARS + 200]
        lower_window = window.lower()

        exams = find_exam_mentions(window)
        no_exam = any(p in lower_window for p in config.NO_EXAM_PHRASES)
        ambiguous = any(p in lower_window for p in config.AMBIGUOUS_PHRASES)

        if not exams and not no_exam:
            continue  # course mentioned, but nothing that helps classify admission route

        relevance = "HIGH" if (exams or no_exam) and not ambiguous else "MEDIUM"

        evidence_list.append(
            Evidence(
                university=university,
                course=course,
                admission_year=year,
                text=window.strip(),
                source_title=source_title,
                source_url=source_url,
                source_type=source_type,
                tier=tier,
                is_official=is_official,
                pdf_page=pdf_page,
                source_date=None,
                matched_exams=exams,
                no_exam_phrase=no_exam,
                ambiguous_phrase=ambiguous,
                relevance=relevance,
            )
        )

    return evidence_list


# --------------------------------------------------------------------------
# PDF HANDLING
# --------------------------------------------------------------------------
def extract_pdf_evidence(
    pdf_url: str, university: str, course: str, year: int, source_title: str,
    tier: int, is_official: bool,
) -> list[Evidence]:
    content, err = utils.fetch(pdf_url, allow_binary=True)
    if content is None:
        log.info(f"[WARN] Could not download PDF {pdf_url}: {err}")
        return []

    if len(content) > config.MAX_PDF_SIZE_MB * 1024 * 1024:
        log.info(f"[WARN] Skipping oversized PDF (> {config.MAX_PDF_SIZE_MB}MB): {pdf_url}")
        return []

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as e:
        log.info(f"[WARN] Could not parse PDF {pdf_url}: {e}")
        return []

    all_evidence: list[Evidence] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            continue
        if not page_text.strip():
            continue
        page_evidence = build_evidence_from_text(
            page_text, university, course, year, source_title, pdf_url,
            "brochure", tier, is_official, pdf_page=page_num,
        )
        all_evidence.extend(page_evidence)

    return all_evidence


# --------------------------------------------------------------------------
# MAIN CRAWL FOR ONE (university, course, year)
# --------------------------------------------------------------------------
def collect_evidence(
    university: str, website: str, course: str, year: int
) -> tuple[list[Evidence], list[str]]:
    """Crawl `website` looking for evidence about `course` admission for
    `year`. Returns (evidence_list, notes)."""
    notes: list[str] = []
    evidence: list[Evidence] = []

    if not website:
        notes.append("No official website provided - cannot verify.")
        return evidence, notes

    parsed_home = urlparse(website)
    if parsed_home.scheme not in ("http", "https"):
        website = "https://" + website
        parsed_home = urlparse(website)
    university_domain = parsed_home.netloc

    visited: set[str] = set()
    visited_pdfs: set[str] = set()
    frontier: list[tuple[str, int, str]] = [(website, 100, "homepage")]

    pages_crawled = 0
    pdfs_crawled = 0

    while frontier and pages_crawled < config.MAX_PAGES_PER_SITE:
        frontier.sort(key=lambda x: x[1], reverse=True)
        url, score, anchor_text = frontier.pop(0)

        if url in visited:
            continue
        visited.add(url)

        if url.lower().endswith(".pdf"):
            if pdfs_crawled >= config.MAX_PDFS_PER_SITE or url in visited_pdfs:
                continue
            visited_pdfs.add(url)
            pdfs_crawled += 1
            source_type, tier, is_official = classify_source(url, anchor_text, university_domain)
            log.info(f"[PDF] Downloading and searching: {url}")
            pdf_evidence = extract_pdf_evidence(
                url, university, course, year, anchor_text or "PDF Document", tier, is_official
            )
            if pdf_evidence:
                log.info(f"[FOUND] {len(pdf_evidence)} evidence item(s) in PDF")
            evidence.extend(pdf_evidence)
            continue

        log.info(f"[SEARCH] Visiting: {url}")
        html, err = fetch_rendered_html(url)
        if html is None:
            notes.append(f"Could not fetch {url}: {err}")
            continue

        pages_crawled += 1
        source_type, tier, is_official = classify_source(url, anchor_text, university_domain)
        page_title = _extract_title(html) or url

        text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
        page_evidence = build_evidence_from_text(
            text, university, course, year, page_title, url, source_type, tier, is_official
        )
        if page_evidence:
            log.info(f"[FOUND] {len(page_evidence)} evidence item(s) on page")
        evidence.extend(page_evidence)

        # Discover further links, but only within the page/PDF budget.
        if pages_crawled < config.MAX_PAGES_PER_SITE:
            for link_url, link_score, link_text in extract_links(html.encode("utf-8"), url, course, year):
                if link_url not in visited and link_score > 0:
                    frontier.append((link_url, link_score, link_text))

    if not evidence:
        notes.append(
            f"Crawled {pages_crawled} page(s) and {pdfs_crawled} PDF(s); "
            "no course-specific admission-exam evidence located."
        )

    return evidence, notes


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


# --------------------------------------------------------------------------
# ORCHESTRATION: full pipeline (input -> scrape -> checker -> store)
# --------------------------------------------------------------------------
def run_pipeline() -> None:
    import checker
    import store

    log.info("=" * 70)
    log.info("Admission Exam Research & Verification Scraper")
    log.info("=" * 70)

    rows = store.read_input_rows(config.INPUT_FILE)
    log.info(f"[INFO] Loaded {len(rows)} row(s) from {config.INPUT_FILE}")

    status_db = utils.load_status_db()
    results = store.read_existing_results(config.OUTPUT_FILE)  # resume support
    all_evidence: list[Evidence] = []

    for row in rows:
        university = row["university"]
        website = row["website"]
        course = row["course"]
        year = row["admission_year"]

        if utils.is_row_done(status_db, university, course, year):
            log.info(f"[SKIP] Already processed: {university} - {course} ({year})")
            continue

        log.info("")
        log.info(f"[INFO] Processing: {university}")
        log.info(f"[INFO] Course: {course}")
        log.info(f"[INFO] Admission Year: {year}")

        try:
            evidence, notes = collect_evidence(university, website, course, year)
        except Exception as e:
            log.info(f"[ERROR] Unexpected failure for {university} - {course}: {e}")
            evidence, notes = [], [f"Unexpected error: {e}"]

        all_evidence.extend(evidence)

        verdict = checker.verify(university, course, year, evidence, notes)
        log.info(f"[CHECKER] Entrance exam: {verdict['entrance_exam'] or 'Not Found'}")
        log.info(f"[CHECKER] Status: {verdict['verification_status']}")

        log.info("[STORE] Saving result")
        results.append(verdict)

        utils.mark_row_done(status_db, university, course, year)
        log.info(f"[DONE] {university} - {course}")

    store.write_workbook(config.OUTPUT_FILE, results, all_evidence)
    log.info("")
    log.info(f"[DONE] Wrote {len(results)} result row(s) to {config.OUTPUT_FILE}")


if __name__ == "__main__":
    run_pipeline()
