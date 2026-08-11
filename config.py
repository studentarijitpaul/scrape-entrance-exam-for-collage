"""
config.py
---------
Central configuration for the Admission Exam Research & Verification Scraper.

Nothing in this project should hard-code magic strings that belong here.
Tune crawl behaviour, keyword priorities, source-trust rules, and confidence
thresholds from this single file.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# PATHS
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "input" / "universities.xlsx"
OUTPUT_FILE = BASE_DIR / "output" / "admission_exam_results.xlsx"
CACHE_DIR = BASE_DIR / "cache"                 # raw page / PDF cache
STATUS_DB = BASE_DIR / "cache" / "status.json"  # resume tracking
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "run.log"

for _p in (CACHE_DIR, LOG_DIR, OUTPUT_FILE.parent):
    _p.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# CRAWL BEHAVIOUR
# --------------------------------------------------------------------------
REQUEST_TIMEOUT = 20          # seconds
REQUEST_DELAY_SECONDS = 2.0   # politeness delay between requests to the same host
MAX_PAGES_PER_SITE = 25       # hard cap on pages visited per university
MAX_PDFS_PER_SITE = 8
MAX_LINK_DEPTH = 2            # how many hops from the homepage we will follow
USER_AGENT = (
    "AdmissionExamResearchBot/1.0 "
    "(+educational-research; contact: student-project; respects robots.txt)"
)
RESPECT_ROBOTS_TXT = True

# Use Playwright only when a requests+BeautifulSoup fetch looks like it failed
# to render real content (e.g. near-empty body, JS-app shell detected).
PLAYWRIGHT_FALLBACK_ENABLED = True
PLAYWRIGHT_MIN_TEXT_LENGTH = 400   # below this, consider the static fetch "empty"
PLAYWRIGHT_TIMEOUT_MS = 25000

# --------------------------------------------------------------------------
# LINK / PAGE PRIORITIZATION KEYWORDS
# Pages/links are scored by how many of these terms appear in the URL or
# anchor text. Higher score = crawled first. This keeps the crawl scoped to
# admissions-relevant content rather than the whole site.
# --------------------------------------------------------------------------
PRIORITY_KEYWORDS = [
    "admission", "admissions", "undergraduate", "postgraduate",
    "bba", "mba", "b.tech", "btech", "engineering",
    "entrance", "entrance exam", "eligibility",
    "selection criteria", "selection process",
    "prospectus", "brochure", "admission notification",
    "application", "apply", "notification", "circular",
]

# Admission-year-aware terms are boosted further when they match the
# requested admission year (formatted at runtime, see scraper.py).
YEAR_TERMS_TEMPLATE = ["{year}", "{year_short}-{next_short}", "{prev_year}-{year_short}"]

# --------------------------------------------------------------------------
# SOURCE TRUST / PRIORITY TIERS
# Lower number = higher trust/priority. Used by checker.py when resolving
# conflicts and by scraper.py when deciding which evidence to prefer.
# --------------------------------------------------------------------------
SOURCE_TIER_NOTIFICATION = 1     # official admission notification
SOURCE_TIER_BROCHURE = 2         # official brochure / prospectus PDF
SOURCE_TIER_OFFICIAL_PAGE = 3    # official university admissions/programme page
SOURCE_TIER_EXAM_AUTHORITY = 4   # NTA / CUET / JEE / CAT / XAT / CMAT / state authority
SOURCE_TIER_THIRD_PARTY = 5      # anything else - discovery only, never sole evidence

# Domains recognised as "official examination authority" sources (Tier 4).
EXAM_AUTHORITY_DOMAINS = [
    "nta.ac.in", "cuet.samarth.ac.in", "jeemain.nta.nic.in", "jeemain.nta.ac.in",
    "iimcat.ac.in", "xatonline.in", "cmat.nta.nic.in", "cmat.nta.ac.in",
    "wbjeeb.nic.in", "wbjeeb.in",
]

# Domains that must NEVER be used as the sole evidence for a VERIFIED result.
# They may still be used for discovery/cross-checking (per spec).
UNTRUSTED_DOMAINS = [
    "reddit.com", "quora.com", "collegedunia.com", "shiksha.com",
    "getmyuni.com", "careers360.com", "collegedekho.com", "medium.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
]

# --------------------------------------------------------------------------
# ENTRANCE EXAM RECOGNITION
# Regex-friendly list of known Indian entrance exam names/aliases. Used by
# the extractor to spot candidate exam mentions in text before checker.py
# decides whether that mention is properly evidenced for the specific
# university + course + year.
# --------------------------------------------------------------------------
KNOWN_EXAMS = {
    "CUET-UG": ["cuet-ug", "cuet ug", "cuet(ug)", "common university entrance test"],
    "CUET-PG": ["cuet-pg", "cuet pg", "cuet(pg)"],
    "JEE Main": ["jee main", "jee-main", "joint entrance examination main"],
    "JEE Advanced": ["jee advanced", "jee-advanced"],
    "WBJEE": ["wbjee", "west bengal joint entrance examination"],
    "CAT": ["common admission test", r"\bcat\b"],
    "XAT": ["xavier aptitude test", r"\bxat\b"],
    "CMAT": ["common management admission test", r"\bcmat\b"],
    "MAT": ["management aptitude test", r"\bmat\b"],
    "NMAT": ["nmat by gmac", r"\bnmat\b"],
    "SNAP": ["symbiosis national aptitude test", r"\bsnap\b"],
    "GMAT": ["graduate management admission test", r"\bgmat\b"],
    "BITSAT": ["bits admission test", r"\bbitsat\b"],
    "VITEEE": ["vit engineering entrance examination", r"\bviteee\b"],
    "SRMJEEE": ["srm joint engineering entrance examination", r"\bsrmjeee\b"],
    "KIITEE": ["kiit entrance examination", r"\bkiitee\b"],
    "MET": ["manipal entrance test", r"\bmet\b"],
    "AEEE": ["amrita engineering entrance examination", r"\baeee\b"],
    "UPESEAT": ["upes engineering aptitude test", r"\bupeseat\b"],
    "NPAT": ["nmims programs after twelfth", r"\bnpat\b"],
}

# Phrases indicating "no entrance exam" / merit-based admission.
NO_EXAM_PHRASES = [
    "merit-based admission", "merit based admission", "based on merit",
    "direct admission", "no entrance examination is required",
    "admission through merit", "marks obtained in qualifying examination",
    "12th standard marks", "class 12 percentage", "qualifying examination marks",
]

# Phrases indicating hedged / ambiguous language -> CROSS-CHECK REQUIRED.
AMBIGUOUS_PHRASES = [
    "may be considered", "may also be eligible", "subject to change",
    "provisional", "tentative", "likely to be", "expected to",
    "one of the following", "or equivalent", "as applicable",
]

# --------------------------------------------------------------------------
# EXAM -> CONDUCTING AUTHORITY (for the "Exam Conducting Authority" column)
# --------------------------------------------------------------------------
EXAM_TO_AUTHORITY = {
    "CUET-UG": "NTA (National Testing Agency)",
    "CUET-PG": "NTA (National Testing Agency)",
    "JEE Main": "NTA (National Testing Agency)",
    "JEE Advanced": "IIT (Zone-wise organising institute)",
    "WBJEE": "WBJEEB (West Bengal Joint Entrance Examination Board)",
    "CAT": "IIMs (Indian Institutes of Management)",
    "XAT": "XLRI Jamshedpur / XAMI",
    "CMAT": "NTA (National Testing Agency)",
    "MAT": "AIMA (All India Management Association)",
    "NMAT": "GMAC (Graduate Management Admission Council)",
    "SNAP": "Symbiosis International (Deemed University)",
    "GMAT": "GMAC (Graduate Management Admission Council)",
    "BITSAT": "BITS Pilani",
    "VITEEE": "VIT (Vellore Institute of Technology)",
    "SRMJEEE": "SRM Institute of Science and Technology",
    "KIITEE": "KIIT Deemed to be University",
    "MET": "Manipal Academy of Higher Education",
    "AEEE": "Amrita Vishwa Vidyapeetham",
    "UPESEAT": "UPES Dehradun",
    "NPAT": "NMIMS Deemed to be University",
}

# --------------------------------------------------------------------------
# CONFIDENCE RULES
# --------------------------------------------------------------------------
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

# --------------------------------------------------------------------------
# STATUS LABELS
# --------------------------------------------------------------------------
STATUS_VERIFIED = "VERIFIED"
STATUS_NO_EXAM = "NO ENTRANCE EXAM"
STATUS_CROSS_CHECK = "CROSS-CHECK REQUIRED"
STATUS_NOT_FOUND = "NOT FOUND"

STATUS_COLORS = {
    STATUS_VERIFIED: "C6EFCE",       # green
    STATUS_NO_EXAM: "D9E1F2",        # light blue/grey
    STATUS_CROSS_CHECK: "FFEB9C",    # amber
    STATUS_NOT_FOUND: "FFC7CE",      # red
}

# --------------------------------------------------------------------------
# OPTIONAL LLM CLASSIFICATION (disabled by default)
# If enabled, an LLM may ONLY classify/extract from already-collected
# evidence. It must never invent facts. See checker.py `llm_classify()`.
# --------------------------------------------------------------------------
USE_LLM_CLASSIFICATION = False
LLM_MODEL = "claude-sonnet-4-6"

# --------------------------------------------------------------------------
# PDF HANDLING
# --------------------------------------------------------------------------
MAX_PDF_SIZE_MB = 25
PDF_CONTEXT_CHARS = 400   # characters of context kept around a matched sentence
