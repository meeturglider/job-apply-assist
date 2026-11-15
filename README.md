# Job Apply Assist

Local, evidence-based ATS resume tailoring. Paste a job description (or fetch a public
posting URL), compare it with `MASTER_RESUME.md` and your portfolio, review the match
report, then generate a truthfulness-checked resume in PDF, DOCX, and Markdown.

The app never invents experience, skills, metrics, or certifications. Every generated
claim is checked against your source resume and portfolio. When the local LLM produces
an unsupported claim, the sanitizer removes it or falls back to deterministic selection.

Degree requirements and minimum-years requirements are evaluated against the education
section and computed professional experience instead of being treated as missing skills.

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

The app expects `MASTER_RESUME.md` in the working directory. Run commands from the
repository root so relative paths resolve correctly.

## CLI

```bash
job-assist serve
job-assist profile --refresh
job-assist analyze --url https://example.com/jobs/123
job-assist analyze --text job-description.txt
job-assist analyze --text - --no-generate < job-description.txt
```

Generated artifacts are written to `data/applications/<application-id>/` and indexed in
`data/applications.db`. These contain personal data and are gitignored.

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

## Configuration

See `.env.example`. Key settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `ollama` | Set to `none` for deterministic-only mode. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint. |
| `OLLAMA_MODEL` | `qwen3:8b` | Model used for extraction and tailoring. |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | Per-request timeout. |
| `MASTER_RESUME_PATH` | `MASTER_RESUME.md` | Canonical resume source. |
| `PORTFOLIO_BASE_URL` | `https://hari.is-a.dev` | Portfolio base URL. |
| `DATA_DIR` | `data` | Database, cache, and generated artifacts. |

## Development

```bash
pytest
ruff check app tests
ruff format --check app tests
mypy app
```

## Current Scope

Included:

- JD URL fetch with manual-paste fallback
- Local LLM + deterministic analysis and tailoring
- Profile merge across resume, portfolio JSON, and JSON-LD
- Match scoring with evidence
- PDF, DOCX, HTML, and Markdown exports
- Truthfulness validation and sanitization
- ATS checks and application history

Not included:

- Automatic job applications
- LinkedIn credential automation or scraping behind a login
- Fabricated metrics, skills, or experience
- Recruiter/outcome analytics beyond the local application index

## Project Layout

```text
app/
├── config.py, models.py, service.py, store.py, llm.py, prompts.py
├── data/skills.yaml
├── jd/            # URL fetch, extraction, analysis
├── matching/      # evidence matching and scoring
├── profile/       # resume parsing, portfolio fetch, merge
├── rendering/     # Markdown, HTML, DOCX, PDF, ATS checks
├── tailoring/     # selection, LLM build, validation, sanitization
└── web/           # single-page local UI
prompts/           # extraction and tailoring prompts
tests/             # hermetic unit and service tests
MASTER_RESUME.md   # canonical resume source
```

## Philosophy

Apply smarter, not harder. Tailor deliberately, keep every claim grounded, and keep
personal data local.
