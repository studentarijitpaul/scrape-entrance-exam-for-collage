# Admission Exam Research & Verification Scraper

Finds out **which entrance exam a student needs for a specific course at a
specific university**, using only evidence from the university's own
official website (admission notifications, brochures/prospectuses, and
official admission pages) — never a guess.

If reliable official evidence can't be found, the tool honestly says
**NOT FOUND** instead of making something up.

---

## 1. Project Purpose

For each row of `(University, Course, Admission Year)` you provide, the tool:

1. Crawls the university's official website (admissions pages + PDFs).
2. Looks for sentences that connect *that specific course* to *a named
   entrance exam* (or to "merit-based/direct admission").
3. Classifies the result into one of four statuses, with a confidence level
   and the exact evidence sentence + source link.
4. Saves everything into a clean, filterable Excel workbook.

## 2. Folder Structure

```
project/
├── scraper.py       # crawls sites/PDFs, extracts evidence, runs the pipeline
├── checker.py        # evidence-based rules -> VERIFIED / NO ENTRANCE EXAM / CROSS-CHECK REQUIRED / NOT FOUND
├── store.py           # reads input Excel, writes formatted output Excel
├── config.py          # all tunable settings, keyword lists, trust rules
├── utils.py            # logging, caching, robots.txt, rate-limited fetching
├── build_input.py       # optional helper to expand a college list into input rows
├── requirements.txt
├── README.md
├── input/
│   └── universities.xlsx    # <- your list of colleges/courses/years to research
├── output/
│   └── admission_exam_results.xlsx   # <- generated results (created by the run)
├── cache/               # cached pages/PDFs + resume-tracking status.json
└── logs/                # run.log
```

## 3. Installation

```bash
cd project
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Only needed once, only if you want JS-rendered pages handled by a real browser:
playwright install chromium
```

## 4. Requirements

- Python 3.10+
- Internet access to each university's domain (and, for exam-authority
  cross-checks, domains like `nta.ac.in`)
- `requests`, `beautifulsoup4`, `openpyxl`, `pypdf` (required)
- `playwright` (optional — only used as a fallback for JavaScript-heavy pages)

## 5. Input Excel Format

`input/universities.xlsx`, one row per (university, course, year) you want
checked:

| University Name | Official Website | Course | Admission Year |
|---|---|---|---|
| ABC University | https://example.edu | BBA | 2026 |
| ABC University | https://example.edu | MBA | 2026 |
| XYZ University | https://xyz.edu | B.Tech | 2026 |

A ready-to-run `input/universities.xlsx` has already been generated for you
from your college list (`collage-list.xlsx`), expanded to check **B.Tech,
MBA, and BBA** for every one of your 65 colleges (195 rows total, admission
year 2026). If you only want specific courses per college, edit that file
directly, or regenerate it:

```bash
python build_input.py your_college_list.xlsx --courses "MBA,BBA" --year 2027
```

## 6. Running the Scraper

```bash
python scraper.py
```

This does everything: reads `input/universities.xlsx`, scrapes each site,
verifies each course, and writes `output/admission_exam_results.xlsx`.

Console output looks like:

```
[INFO] Processing: ABC University
[INFO] Course: BBA
[INFO] Admission Year: 2026

[SEARCH] Visiting: https://example.edu/admissions/bba
[FOUND] 1 evidence item(s) on page

[CHECKER] Entrance exam: CUET-UG
[CHECKER] Status: VERIFIED

[STORE] Saving result
[DONE] ABC University - BBA
```

**Resuming:** if you stop the run partway through 195 rows, just run
`python scraper.py` again — rows already marked done in
`cache/status.json` are skipped automatically.

## 7. Output Excel Structure

`output/admission_exam_results.xlsx` has three sheets:

- **FINAL_RESULTS** — one row per university+course+year: exam, authority,
  status, confidence, source link, evidence snippet, notes. Color-coded by
  status, filterable, with clickable source links.
- **EVIDENCE** — every evidence snippet collected (even ones the checker
  didn't ultimately rely on), with source type and relevance.
- **REVIEW_QUEUE** — only the `CROSS-CHECK REQUIRED` and `NOT FOUND` rows,
  for a human researcher to look at directly.

## 8. Verification Logic

| Status | Meaning |
|---|---|
| 🟢 **VERIFIED** | An official source (current-year notification, brochure, or admissions page) explicitly names the exam for this exact course, unambiguously. |
| ⚪ **NO ENTRANCE EXAM** | An official source explicitly states merit-based/direct admission, unambiguously. |
| 🟡 **CROSS-CHECK REQUIRED** | Relevant official evidence exists but is ambiguous, conflicting between sources, or doesn't clearly confirm the requested admission year — or only third-party sources mention an exam. |
| 🔴 **NOT FOUND** | No reliable official evidence was located at all. |

Course-specificity is enforced: mentioning CUET somewhere on the site does
**not** verify BBA unless the sentence actually connects CUET to BBA.
Year-specificity is enforced similarly: an older document isn't treated as
current unless it explicitly says the policy continues.

Confidence (`HIGH` / `MEDIUM` / `LOW`) reflects how directly the evidence
supports the conclusion — a `LOW`-confidence result is never labelled
`VERIFIED`.

## 9. Troubleshooting

| Problem | What it means / what to do |
|---|---|
| `Could not fetch ... blocked_by_robots_txt` | The site's robots.txt disallows that page. The scraper respects this and moves on — check the page manually if you need that specific URL. |
| `http_403` / `http_429` | Site is blocking or rate-limiting the request. The scraper already waits between requests (`config.REQUEST_DELAY_SECONDS`); if it persists, that site may need manual research. |
| Lots of `NOT FOUND` for one university | Its site may be JS-heavy and Playwright isn't installed (`pip install playwright && playwright install chromium`), or the site uses Cloudflare-style bot protection — this tool deliberately does not try to bypass that (see Limitations). |
| Excel says "Input file must have a 'University Name' column" | Check your input file's header row matches the format in §5. |
| Run seems to restart from scratch | Delete `cache/status.json` only if you actually want to reprocess everything; otherwise leave it — that's what makes resuming work. |

## 10. Limitations (human review still needed)

- **Cloudflare / bot-walled sites**: the tool does not attempt to bypass
  CAPTCHAs or hard bot-protection. Those rows will come back `NOT FOUND`
  and need manual lookup.
- **PDF quality**: scanned/image-only PDFs (no extractable text layer)
  won't yield evidence — pair with manual review or add OCR if you need it.
- **Conflicting official pages**: flagged as `CROSS-CHECK REQUIRED` rather
  than auto-resolved — a human should decide which source is authoritative.
- **Stale documents**: if only a 2024/2025 brochure exists for a 2026
  admission year request, the tool downgrades to `CROSS-CHECK REQUIRED`
  rather than assuming the policy is unchanged.
- **Third-party mentions**: sites like coaching blogs or Quora are used for
  discovery only and can never produce a `VERIFIED` result by themselves.
- **Not a substitute for the official notification**: always confirm a
  `VERIFIED` result against the linked source before relying on it for an
  actual admission decision — official policies can change.
