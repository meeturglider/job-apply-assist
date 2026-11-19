# Engineering Refinement Report

## Baseline and Scope

The refinement started from commit `3465970` (`Fix degree and experience matching and
resume header`). The application already provided local job analysis, evidence matching,
resume tailoring, document rendering, and ATS checks. This work hardens the existing
architecture rather than replacing it.

Goals:

- Make scores explainable instead of compressing unrelated signals into one number.
- Distinguish missing evidence from contradicting evidence.
- Prevent unsupported resume claims from surviving generation.
- Make LLM failures bounded, observable, and recoverable.
- Treat fetched job URLs as untrusted input.
- Verify generated documents, not just render them.
- Improve API diagnostics, task lifecycle handling, and personal-data protection.
- Add regression coverage and document operational limits.

No new orchestration framework, vector database, database migration, or hosted service was
introduced. New model fields are additive with safe defaults.

## Delivery Summary

| Commit | Change |
| --- | --- |
| `6f1656e` | Separate suitability, evidence, and keyword scoring |
| `187c333` | Classify evidence and requirements by dimension |
| `f6704d7` | Validate resume claims against source evidence |
| `46b1d14` | Harden LLM schemas, retries, and fallback tracking |
| `dee5ba8` | Harden job URL fetching against SSRF |
| `7780b8e` | Fix artifact preview, downloads, and document verification |
| `2b5f3ab` | Improve API errors, logging, tasks, and data protection |
| Add refinement regression coverage and this report | Add end-to-end acceptance tests, award regressions, documentation, and final verification |
| Private apply portal | Add per-user accounts, session-scoped workspaces, CSRF, public contacts, outreach drafts, tracker, prefill payloads, and a reviewed browser extension |

## Architecture and Data Flow

```text
job URL or pasted text
        |
        v
JD fetch and extraction
        |
        v
rules + optional GLiNER-small candidate extraction (seconds, local CPU)
        |
        v
Ollama 8B candidate review (IDs only, no free-form skill invention)
        |
        v
deterministic validation, canonicalization, merge, and fallback
        |
        v
requirement matching by dimension and evidence class
        |
        v
suitability score + evidence coverage + keyword coverage
        |
        v
tailored resume plan (LLM or deterministic)
        |
        v
claim validation -> sanitization -> deterministic fallback if still invalid
        |
        v
Markdown / HTML / DOCX / PDF
        |
        v
PDF + DOCX ATS verification and stored report
```

Application records are stored in `data/applications.db`; generated artifacts remain under
`data/applications/<application-id>/`. There is no schema migration because the added
Pydantic fields use defaults and ignore unknown legacy data.

Portal mode swaps the persistence layer rather than forking the service. `ApplicationService`
accepts an application store, profile service, and artifact store; the default implementation
uses SQLite and files, while portal sessions use `MemoryApplicationStore`,
`MemoryProfileService`, and `MemoryArtifactStore`. Only account identities and password hashes
persist in `data/accounts.db`. Each session owns a workspace, and shutdown tears down all
active workspaces.

## Scoring Model

The primary score is now **candidate suitability**, weighted out of 100:

| Component | Weight |
| --- | ---: |
| Must-have requirements | 50 |
| Nice-to-have requirements | 15 |
| Responsibilities | 15 |
| Experience | 5 |
| Education | 5 |
| Certifications | 5 |
| Seniority | 5 |

Direct evidence receives full credit, adjacent evidence receives half credit, and
contradicting evidence receives a negative credit. Missing evidence receives no credit.

Two independent metrics are reported separately so they cannot inflate suitability:

- **Evidence coverage:** counts direct, adjacent, unverified, and contradicted requirements.
- **Keyword coverage:** measures JD keyword presence in profile evidence.

The legacy `AnalysisResult.score.total` remains populated from candidate suitability for
API compatibility. Each score component includes value, maximum, percentage, and a plain
language explanation.

## Evidence and Matching

`app/matching/dimensions.py` classifies responsibilities into technical, leadership,
architecture, delivery, strategic impact, or domain dimensions. Matches retain the source
requirement, source evidence, confidence, explanation, and one of four evidence classes:

- `direct`: source text supports the requirement.
- `adjacent`: related source evidence supports partial credit.
- `unverified`: no source evidence was found; this is not proof the candidate lacks it.
- `contradicted`: source text explicitly negates the requirement.

Education and experience requirements are evaluated as their own categories rather than
being treated as missing skills. Computed professional experience is rounded for threshold
matching, while the precise value remains in the profile.

Employer names are searchable evidence and a small curated entity map connects subsidiary
names to parent companies, for example `Google Operations Center` to `Alphabet`. A JD
keyword for the parent is therefore counted as present, and the analysis emits a grounded
profile-edit suggestion such as `Google Operations Center (Alphabet)`. Keyword coverage in
the UI now shows the `present / total` denominator and derives a clamped percentage from the
coverage map, so a stored or visual formatting issue cannot render an impossible value.

When the LLM is enabled, `app/matching/suggestions.py` adds an advisory semantic pass after
matching. It submits only JD keywords that are not literally present and a bounded, indexed
list of existing profile evidence to a narrow schema. Returned proposals are accepted only
when their keyword is in scope, their evidence index exists, and their wording is grounded
in that indexed evidence. Accepted suggestions are labelled `Possible profile edit` and
never alter scores, coverage, matches, or generated resume facts. In deterministic-only
mode the stage is skipped and the curated deterministic suggestions remain.

## Claim Validation and Sanitization

`app/tailoring/claims.py` extracts checkable claims from generated text:

- technologies
- numbers and metrics
- dates
- team sizes
- financial amounts
- adoption or scope counts
- leadership verbs
- outcome verbs
- job titles when validating the resume header

Claims are checked against source-specific evidence. The validator reports checked and
supported claim counts plus `ReviewItem` entries for paraphrases or weak traceability.
Unsupported hard claims are validation errors. The sanitizer removes unsupported text and
falls back to deterministic selection if repair cannot produce a valid resume.

Awards are treated as discrete source facts. An award that is not present in the profile is
now a hard validation error instead of an advisory warning. Near-duplicate award variants
merged from the master resume and portfolio are deduplicated before display.

## LLM Reliability

`app/llm_schemas.py` defines narrow Pydantic schemas for legacy JD extraction, candidate
review, suggestions, and resume tailoring. The candidate-review schema accepts only IDs from
the locally extracted candidate list, so the 8B model classifies and merges evidence instead of
re-extracting the full JD.
`OllamaClient.json` now:

- passes the Pydantic JSON schema to Ollama's structured-output mode so long resume plans are
  generated in the requested shape;
- validates JSON against the requested schema after parsing;
- strips code fences/surrounding prose and trailing commas from model output;
- uses a 32k context and 8k output-token budget by default to prevent truncated resume JSON;
- retries malformed JSON or schema mismatches up to `OLLAMA_MAX_RETRIES`, capped at three;
- applies bounded linear backoff;
- avoids retrying non-retryable timeout and provider-disabled errors;
- records attempts on the client and in fallback metadata;
- logs operation, attempt, model, retryability, and outcome without logging resume content.

Extraction, review, and tailoring store `llm_attempts`, `extraction_stages`, and
`extraction_fallback_reason` so the active pipeline is visible and deterministic fallback is
distinguishable from an LLM result. GLiNER is optional (`NLP_PROVIDER=none` by default) and
lazily loaded; missing packages or cached weights add a warning without blocking analysis.
The 8B model remains the dominant latency source, so analysis and generation still run as
background tasks.

## URL Fetch Security

`app/jd/fetcher.py` treats job URLs as untrusted input. Controls include:

- HTTP/HTTPS-only URL validation;
- rejection of embedded credentials and invalid ports;
- rejection of localhost, private, link-local, reserved, and non-global resolved addresses;
- manual redirect handling with validation of every hop;
- a five-redirect cap and HTTPS-to-HTTP downgrade rejection;
- streamed response bodies with a 5 MB cap;
- environment proxy isolation through `trust_env=False`;
- explicit auth-wall and manual-paste fallback behavior.

This reduces SSRF risk but is not a substitute for running the app on a trusted local
machine or placing it behind an authenticated reverse proxy if exposed to a network.

## Interactive Source Controls

A later incremental change adds per-session resume upload and per-analysis profile URLs.
`POST /api/profile/resume` stores Markdown/text under `data/profile/resume.md`, and
`POST /api/profile/resume/reset` removes it so the canonical `MASTER_RESUME.md` is used
again. `AnalyzeRequest.profile_urls` accepts up to five public URLs; the application loads
JSON, JSON-LD, or page metadata, merges only additive evidence into the profile, tags that
evidence with its source URL, and persists the merged `profile.json` with the application
artifacts. Defaults are unchanged when no extra URLs are supplied, and failures are reported
as warnings rather than aborting analysis.

Additional URL fetching reuses `fetch_public_text`, so the SSRF controls above apply to
profile sources as well as job URLs. Uploaded resumes are also validated against source
evidence by the same claim validator used for the canonical resume.

## Private Apply Portal

`PORTAL_MODE=true` enables real accounts with durable per-account candidate data. `AccountStore`
uses PBKDF2-SHA256 password and recovery-code hashes in a private SQLite database. Registration
returns a one-time recovery code, signed-in users can rotate it through a CSRF-protected
endpoint, and `/api/auth/reset` accepts email + code + new password without email or SMS.
Reset rotates the code, invalidates all sessions for that user, and uses generic failures plus
attempt lockout to limit account enumeration and guessing. `SessionManager` keeps login sessions
in memory and prunes expired entries; each login loads the account's workspace from
`data/users/<account_id>/` with its own application store, profile service, and artifacts.
Restart recovery marks interrupted runs as errors so they can be started again. Session and
CSRF cookies are `HttpOnly`/`SameSite=Lax` as appropriate, and every unsafe API
method requires the CSRF header to match the session token. Rate-limit keys become
`user:<id>` for signed-in sessions and `ip:<address>` for unauthenticated portal requests.
After sign-in the UI shows saved applications for selection, editing, or a new session; clearing
stale browser drafts prevents an old application ID from being used after restart. Manual draft
sessions are `draft`-status records that can be analyzed in place without creating duplicates.

`app/jd/contacts.py` extracts public recruiter candidates from JSON-LD `applicationContact`,
`mailto:` links, and page text. It does not authenticate to LinkedIn or scrape private pages.
`ApplicationService.draft_outreach` creates a deterministic greeting, role/company context,
computed experience, and evidence-backed skills; user notes are whitespace-normalized and
bounded. The draft is opened through a `mailto:` link, so the app never sends email.

`prefill_payload` returns only fields derived from the uploaded resume and analyzed job, plus
the tailored Markdown artifact. The unpacked extension in `extension/` holds the payload in
`chrome.storage.session`, waits for the user to click its action on the target tab, fills only
empty visible fields, emits input/change events, displays a review reminder, and never submits
a form.

## Document Generation and Verification

HTML and Markdown artifacts are previewable inline; downloads force attachment semantics.
All artifacts are served with `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
PDF and DOCX both use A4 page dimensions with compact single-column spacing. Section headings
keep with following content, and experience/project entries may break across pages so a title
is not stranded alone at the bottom of a page. Editing session company, role, or source URL
updates the record plus job/analysis/metadata labels, then rerenders existing resume content
with method `details-update`; it never re-tailors or overwrites manual resume edits.

PDF checks include selectable text, page count, contact details, candidate identity,
experience coverage, standard sections, absence of ATS-breaking tables, keyword coverage,
and must-have keyword presence. DOCX verification extracts text and checks identity, contact
details, and experience coverage. DOCX checks still run when PDF rendering is unavailable.

## API, Logging, Tasks, and Privacy

- API responses include a validated or generated `X-Request-ID`.
- API responses use `Cache-Control: no-store`.
- Errors use a consistent envelope: `detail`, `code`, and `request_id`.
- Validation errors return structured field locations and clean validator messages instead of
  leaking Pydantic wrappers or internals.
- Unexpected exceptions return a generic message; details go only to application logs.
- Request logs include method, path, status, duration, and request ID.
- Analyze request fields have maximum lengths to bound payload size.
- The browser client formats string and object error payloads and includes the request ID.
- Application shutdown cancels outstanding background tasks and waits for cancellation.
- POSIX data directories are restricted to `0700`; the SQLite database is restricted to `0600`.
- Optional HTTP Basic auth is enforced by `SHARED_PASSWORD`; missing or invalid credentials
  receive a structured `401` with `WWW-Authenticate` and never reach application routes.
- POST API requests use an in-process fixed-window rate limit (`RATE_LIMIT_PER_MINUTE`)
  keyed by `CF-Connecting-IP` when `TRUST_PROXY_HEADERS` is enabled, otherwise by socket peer.
  Rejections return structured `429` responses with `Retry-After`.
- Portal mode replaces Basic auth with account sessions and requires a CSRF header for unsafe
  requests; API responses still use `Cache-Control: no-store`.
- Portal application, profile, note, draft, and artifact data persists only under the owning
  account's directory; account identity, password hash, and recovery-code hash are the only
  persisted user records.

## Verification

Automated checks:

```bash
ruff format --check app tests
ruff check app tests
mypy app
pytest -q
```

Result at the time of this report: **99 tests passed**, Ruff clean, mypy clean. The only test
warning is Starlette's upstream `TestClient`/`httpx` deprecation notice.

Coverage includes scoring separation, evidence classes, dimension matching, education and
experience matching, claim validation and sanitization, schema retries, SSRF controls,
artifact headers, API error envelopes, request IDs, data permissions, task shutdown, DOCX
verification, award merge/deduplication, resume upload/reset, per-analysis profile URL
merging, parent-company entity matching, grounded semantic keyword suggestions, LLM
suggestion rejection/fallback behavior, shared-password gating, per-client rate limiting,
proxy-aware rate-limit keys, an end-to-end deterministic analyze/generate flow, portal
registration/login, session isolation, CSRF enforcement, durable per-account storage,
restart persistence, interrupted-run recovery, session-scoped
profile status, resume-before-analysis gating, contact extraction, outreach drafting, tracker
updates, prefill payloads, portal UI/extension controls, Google-style prompt constraints,
unquantified-impact warnings, manual batched evidence-note reanalysis, optional GLiNER candidate
extraction with bounded chunks and dedupe, candidate-only Ollama review, validated manual resume
edits that regenerate every artifact, browser-local editor drafts with stale-draft protection,
and automatic same-company role merging across HTML, Markdown, and DOCX rendering.

Real-profile smoke check against the local `MASTER_RESUME.md` and live portfolio JSON:

- computed experience: `15.8` years since `Feb 2011`;
- rounded matching/summary: `16+` years;
- education: `Bachelor of Engineering – Electronics & Communication`;
- 70 evidence-backed skills, 10 portfolio projects, 6 deduplicated awards;
- no profile warnings;
- deterministic end-to-end run generated Markdown, HTML, DOCX, and PDF with 61/61
  supported claims, no review items, and no failed ATS checks.

## Operational Notes

- Bind to `127.0.0.1` unless an authenticated reverse proxy protects the app.
- `LLM_PROVIDER=none` exercises rules/review fallback, matching, tailoring, and validation
  without Ollama.
- `NLP_PROVIDER=none` keeps the app hermetic; install `.[nlp]`, run `job-assist warm-ner`, then
  set `NLP_PROVIDER=gliner` to enable the local NER stage. `NLP_MODEL_PATH` supports offline
  weights.
- `PORTAL_MODE=true` switches to per-user sessions; set `SESSION_COOKIE_SECURE=true` behind an
  HTTPS tunnel and keep `ALLOW_REGISTRATION=false` after provisioning known users.
- If PDF generation fails, install Chromium with `playwright install chromium`. DOCX,
  Markdown, HTML, and DOCX ATS checks still work.
- `LOG_LEVEL`, `OLLAMA_MAX_RETRIES`, `OLLAMA_RETRY_BACKOFF_SECONDS`, `OLLAMA_NUM_CTX`, and
  `OLLAMA_MAX_PREDICT` are available in `.env.example`.
- Generated artifacts contain personal data and remain gitignored.

## Known Limitations

- Skill matching and claim extraction are heuristic. Conservative checks reduce risk but
  are not a formal proof of truthfulness.
- LLM output remains probabilistic. Schema validation and retries improve reliability but do
  not guarantee quality or latency.
- SSRF defenses block the common classes of private-network access; they are not a complete
  network security boundary.
- Local mode has one optional shared password, not per-user accounts; portal mode has accounts
  with private on-disk workspaces under `data/users/`, so removing that directory permanently
  deletes the associated candidate data.
- PDF verification depends on Playwright Chromium; absent browser support is reported as an
  ATS failure rather than silently passing.
- Keyword coverage is requirement-oriented and does not model every ATS vendor's scoring.
- Fuzzy award deduplication is deliberately conservative; unusual punctuation or renamed
  awards may still require manual review.

## Rollback

Each refinement is isolated in its own commit. Reverting the final commit removes the new
regression tests and this report; reverting earlier commits independently restores the
corresponding behavior without a database migration step.
