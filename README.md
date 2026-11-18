# Job Apply Assist

Local, evidence-based ATS resume tailoring. Paste a job description (or fetch a public
posting URL), compare it with `MASTER_RESUME.md` and your portfolio, review the match
report, then generate a truthfulness-checked resume in PDF, DOCX, and Markdown.

The app never invents experience, skills, metrics, or certifications. Every generated
claim is checked against your source resume and portfolio. When the local LLM produces
an unsupported claim, the sanitizer removes it or falls back to deterministic selection.

Degree requirements and minimum-years requirements are evaluated against the education
section and computed professional experience instead of being treated as missing skills.

In optional portal mode, users sign in with their own account, upload a resume into a private
session, keep a per-session application tracker, discover public recruiter contacts, draft an
email, and prefill open application forms through a companion browser extension. Portal mode
never submits forms, sends email from the server, scrapes LinkedIn behind a login, or persists
resumes, jobs, notes, drafts, or tracker state.

## Quick Start

Requirements:

- Python 3.11+
- Ollama (optional but recommended for higher-quality tailoring)
- Chromium for PDF output (installed once through Playwright)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium

# Optional: start Ollama and pull a model
ollama serve
ollama pull qwen3:8b

cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`.

Local LLM extraction and tailoring can take 1-4 minutes per run on an 8B model. The UI
runs these as background jobs and polls for progress, so the page stays responsive.

### Sharing a demo URL

Before exposing the app through a Cloudflare quick tunnel, set `SHARED_PASSWORD` in `.env`
and restart the server. The browser will prompt for credentials; the username can be
anything and the password is the shared secret. `RATE_LIMIT_PER_MINUTE` limits POST API
requests per client, and `TRUST_PROXY_HEADERS=true` lets the app use `CF-Connecting-IP`
from Cloudflare Tunnel instead of seeing every visitor as localhost.

This is a single shared password, not per-user identity. Anyone with the URL and password
can upload the shared resume, start jobs, and read history, so use dummy data for demos. For
per-user sign-in, use portal mode below instead of `SHARED_PASSWORD`.

### Private apply portal

Set `PORTAL_MODE=true` and restart the server. Accounts are stored as email/password hashes
in `data/accounts.db`; candidate data is not. Session cookies identify the user and carry a
CSRF token for all unsafe API calls. Each sign-in creates a fresh in-memory workspace with
its own application store, profile service, and generated artifacts. Signing out, expiring the
session, or restarting the server clears that workspace.

The portal UI adds:

- per-session resume upload and profile indicator; selecting a resume file uploads it
  immediately and the indicator tracks that session's workspace;
- a tracker for draft, analyzed, email drafted, prefilled, and submitted states;
- public contact extraction from job JSON-LD, `mailto:` links, and page text;
- a deterministic, evidence-grounded outreach draft plus a `mailto:` link;
- a prefill payload for the companion extension.

The auth card switches between **Sign in** and **New user? Create account**. Invalid email
addresses and short passwords are rejected in the browser with a specific message before a
request is sent; API validation uses the same messages. When registration is disabled with
`ALLOW_REGISTRATION=false`, the create-account option is hidden.

Load `extension/` as an unpacked Chrome extension.
 After clicking **Fill open application**
in the portal, switch to the target form and click the extension action. It fills only empty,
visible fields from the uploaded resume and current analysis, shows how many fields it filled,
and never clicks submit. Keep `SESSION_COOKIE_SECURE=true` for HTTPS tunnel URLs.

In local mode, the app expects `MASTER_RESUME.md` in the working directory. Run commands from
the repository root so relative paths resolve correctly.

The local-mode UI can temporarily replace that source with an uploaded Markdown/text resume
and can add up to five public portfolio or LinkedIn URLs to the next analysis. Uploaded
resumes are stored as `data/profile/resume.md`; **Use default** restores `MASTER_RESUME.md`.
Additional URLs are fetched with the same SSRF controls as job URLs, are attached to that
application record for reproducibility, and are never treated as persistent source defaults.
Portal mode uses **Clear resume** instead and keeps the uploaded text only in memory.

## CLI

```bash
job-assist serve
job-assist profile --refresh
job-assist analyze --url https://example.com/jobs/123
job-assist analyze --text job-description.txt
job-assist analyze --text - --no-generate < job-description.txt
```

In local mode, generated artifacts are written to `data/applications/<application-id>/` and
indexed in `data/applications.db`. These contain personal data and are gitignored. On POSIX
systems the app restricts the data directories to the current user and the database to
owner-only access. Portal mode writes no application database, profile directory, or artifact
files; only `data/accounts.db` persists.

## How It Works

1. Fetch or accept a job description. LinkedIn and auth-walled pages fall back to
   manual paste.
2. Extract requirements with the local LLM, falling back to deterministic extraction.
3. Normalize skills through the alias lexicon in `app/data/skills.yaml`.
4. Match requirements against resume bullets, projects, certifications, and portfolio
   evidence.
5. Score must-have/nice-to-have coverage, responsibilities, certifications, seniority,
   and keyword coverage.
6. Show matched, partial, and missing skills with supporting evidence.
7. On request, build a tailored resume with a strict source-evidence prompt.
8. Validate and sanitize the result, then render Markdown, HTML, DOCX, and PDF.
9. Run ATS checks on the PDF (selectable text, page length, sections, tables, keyword
   coverage) and store the report.

### Locked Resume Design

The generator uses a Google-style default: a clean single-column ATS-safe PDF, standard fonts,
consistent spacing, no tables/columns/photos/icons, reverse-chronological experience, and a
concise targeted summary. Skills and programming languages stay near the top for ATS and
recruiter scanning. Bullets follow X/Y/Z ("accomplished X, measured by Y, by doing Z") when the
source supplies a metric; the app never invents numbers and warns when recent bullets lack
quantified impact.

### Editing the Generated Resume

Generated resumes can be refined in the browser before download. The structured editor exposes
the targeted summary, skill groups, and experience/project bullets; names, dates, employers,
education, and certifications stay fixed to preserve claim traceability. Saving revalidates every
edit against the uploaded evidence, rejects unsupported claims or invented metrics, rewrites all
formats plus the validation and ATS reports, and marks the artifact as manually edited.

## Reviewing Evidence and Notes

The UI shows a progress percentage while analysis and generation run. After analysis, the
report separates suitability, evidence coverage, and keyword coverage so unrelated signals
cannot inflate the score.

Each matched skill can be expanded into its supporting source snippets and origins. The
provenance panel summarizes which resume, portfolio, extra profile URL, or user note supplied
the evidence. Keyword coverage shows both `present / total` and the source labels for each
keyword, while matches show the origin mix behind their evidence.

Application-specific evidence notes can be added for a target skill. A note must mention its
target to be accepted; accepted notes are tagged as `User note` and any dates, metrics, titles,
or outcome claims are flagged for review. Saving notes triggers revalidation automatically, so
the refreshed analysis is ready before generating a tailored resume. Revalidation keeps the
original baseline analysis for comparison and applies the same claim validation as the main
pipeline.

## Configuration

See `.env.example`. Key settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `ollama` | Set to `none` for deterministic-only mode. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint. |
| `OLLAMA_MODEL` | `qwen3:8b` | Model used for extraction and tailoring. |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | Per-request timeout. |
| `OLLAMA_MAX_RETRIES` | `1` | Retries for malformed or failed LLM JSON responses. |
| `OLLAMA_RETRY_BACKOFF_SECONDS` | `1` | Base backoff between LLM retries. |
| `MASTER_RESUME_PATH` | `MASTER_RESUME.md` | Canonical resume source. |
| `LOG_LEVEL` | `INFO` | Application log level. |
| `SHARED_PASSWORD` | empty | Enables Basic auth when set; any username works. |
| `RATE_LIMIT_PER_MINUTE` | `30` | Caps POST API requests per client per minute; `0` disables. |
| `TRUST_PROXY_HEADERS` | `false` | Uses `CF-Connecting-IP`/`X-Forwarded-For` for rate-limit buckets. |
| `PORTAL_MODE` | `false` | Enables per-user sessions with in-memory candidate data. |
| `ALLOW_REGISTRATION` | `true` | Allows new portal accounts. |
| `SESSION_TTL_MINUTES` | `480` | Lifetime of a portal session. |
| `SESSION_COOKIE_SECURE` | `false` | Marks session cookies `Secure`; enable for HTTPS tunnel URLs. |
| `PORTFOLIO_BASE_URL` | `https://hari.is-a.dev` | Portfolio base URL. |
| `DATA_DIR` | `data` | Database, cache, and generated artifacts. |

## Development

```bash
pytest
ruff check app tests
ruff format --check app tests
mypy app
```

See [`docs/engineering-report.md`](docs/engineering-report.md) for the refinement
architecture, validation matrix, operational notes, and known limitations.

## Current Scope

Included:

- JD URL fetch with manual-paste fallback
- Local LLM + deterministic analysis and tailoring
- Profile merge across resume, portfolio JSON, and JSON-LD
- Per-session resume upload and per-analysis profile URLs
- Match scoring with evidence and source provenance
- Evidence-grounded semantic profile-edit suggestions
- Evidence notes with accepted/rejected decisions and revalidation gate
- Analysis and generation progress percentage
- PDF, DOCX, HTML, and Markdown exports
- Truthfulness validation and sanitization
- ATS checks and application history
- Optional per-user portal with session-only trackers and artifacts
- Public contact discovery, outreach drafts, and reviewed form prefill

Not included:

- Automatic job applications or unattended form submission
- Server-side SMTP delivery
- LinkedIn credential automation or scraping behind a login
- Persistent candidate profiles, resumes, drafts, or tracker history in portal mode
- Fabricated metrics, skills, or experience
- Recruiter/outcome analytics beyond the local application index

## Project Layout

```text
app/
├── config.py, models.py, service.py, store.py, llm.py, prompts.py
├── auth.py, artifacts.py
├── data/skills.yaml
├── jd/            # URL fetch, extraction, analysis, public contacts
├── matching/      # evidence matching and scoring
├── profile/       # resume parsing, portfolio fetch, merge, memory service
├── rendering/     # Markdown, HTML, DOCX, PDF, ATS checks
├── tailoring/     # selection, LLM build, validation, sanitization
└── web/           # single-page local UI
extension/         # reviewed browser prefill extension
prompts/           # extraction and tailoring prompts
tests/             # hermetic unit and service tests
MASTER_RESUME.md   # canonical local-mode resume source
```

## Philosophy

Apply smarter, not harder. Tailor deliberately, keep every claim grounded, and keep
personal data local or confined to the current portal session.
