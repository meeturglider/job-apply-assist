const state = {
  currentId: null,
  timer: null,
  mode: "analysis",
  detail: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      // Keep the status message.
    }
    throw new Error(detail);
  }
  return response.json();
}

function showNotice(message, type = "warning") {
  const notice = $("notice");
  notice.hidden = false;
  notice.className = `notice ${type}`;
  notice.textContent = message;
}

function hideNotice() {
  $("notice").hidden = true;
}

function showProgress(mode, message = "Working...") {
  state.mode = mode;
  $("empty-state").hidden = true;
  $("result").hidden = true;
  $("progress-card").hidden = false;
  $("progress-message").textContent = message;
  const steps = $("progress-steps");
  clear(steps);
  const labels =
    mode === "generation"
      ? ["Load analysis", "Build tailored resume", "Truthfulness check", "Render files"]
      : ["Fetch job description", "Extract requirements", "Load profile", "Match evidence and score"];
  labels.forEach((label) => steps.appendChild(el("li", "", label)));
}

function setProgress(record) {
  $("progress-card").hidden = false;
  $("progress-message").textContent = record.message || "Working...";
  $("progress-stage").textContent = (record.stage || "working").replaceAll("_", " ");
  const steps = [...$("progress-steps").children];
  const stageMap = {
    queued: 0,
    fetching: 0,
    matching: 2,
    analyzing: 1,
    tailoring: 1,
    rendering: 2,
    manual_paste: 0,
    done: 3,
    error: 0,
  };
  const active = stageMap[record.stage] ?? 0;
  steps.forEach((step, index) => {
    step.classList.toggle("active", index <= active);
  });
}

function stopPolling() {
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
}

async function refreshHealth() {
  try {
    const health = await api("/api/health");
    const llm = $("llm-status");
    llm.textContent = health.llm_available
      ? `${health.llm_model} ready`
      : "Deterministic mode";
    llm.className = `pill ${health.llm_available ? "success" : "warning"}`;
    const profile = health.profile;
    const ps = $("profile-status");
    ps.textContent = profile.master_resume_exists
      ? "Profile source ready"
      : "MASTER_RESUME.md missing";
    ps.className = `pill ${profile.master_resume_exists ? "" : "warning"}`;
  } catch (error) {
    $("llm-status").textContent = "Health check failed";
    $("llm-status").className = "pill warning";
  }
}

async function refreshProfile() {
  const button = $("refresh-profile");
  button.disabled = true;
  button.textContent = "Refreshing...";
  try {
    const profile = await api("/api/profile/refresh", { method: "POST", body: "{}" });
    showNotice(
      `Profile refreshed: ${profile.experience_count} roles, ${profile.project_count} projects, ${profile.skill_count} skills.`,
      "success",
    );
    await refreshHealth();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Refresh profile";
  }
}

async function refreshHistory() {
  try {
    const data = await api("/api/applications");
    const list = $("history");
    clear(list);
    if (!data.applications.length) {
      list.appendChild(el("div", "muted", "No applications yet."));
      return;
    }
    data.applications.forEach((record) => {
      const item = el("button", "history-item");
      item.type = "button";
      item.classList.toggle("active", record.id === state.currentId);
      const left = el("div");
      left.appendChild(el("div", "history-title", record.role || record.company || "Untitled role"));
      left.appendChild(el("div", "history-sub", `${record.company || "Unknown company"} · ${record.status}`));
      item.appendChild(left);
      if (record.score) {
        item.appendChild(el("div", "history-score", `${Math.round(record.score)}%`));
      }
      item.addEventListener("click", () => openApplication(record.id, record.status));
      list.appendChild(item);
    });
  } catch (error) {
    console.error(error);
  }
}

async function openApplication(applicationId, status) {
  stopPolling();
  state.currentId = applicationId;
  hideNotice();
  try {
    if (status && ["queued", "analyzing", "generating"].includes(status)) {
      state.mode = status === "generating" ? "generation" : "analysis";
      showProgress(state.mode, "Resuming the latest run...");
      poll();
      return;
    }
    const detail = await api(`/api/applications/${applicationId}`);
    renderDetail(detail);
  } catch (error) {
    showNotice(error.message, "error");
  }
  refreshHistory();
}

function renderDetail(detail) {
  state.detail = detail;
  const record = detail.record;
  if (record.status === "needs_paste") {
    showNotice(
      record.message || "This job site needs the description pasted manually.",
      "warning",
    );
    $("progress-card").hidden = true;
    $("result").hidden = true;
    $("empty-state").hidden = false;
    $("job-text").focus();
    return;
  }
  if (record.status === "error") {
    showNotice(record.message || "The run failed.", "error");
    $("progress-card").hidden = true;
    $("result").hidden = true;
    $("empty-state").hidden = false;
    return;
  }
  if (!detail.analysis || !detail.job) {
    showNotice("The analysis is not ready yet.", "warning");
    return;
  }
  hideNotice();
  $("progress-card").hidden = true;
  $("empty-state").hidden = true;
  $("result").hidden = false;
  renderAnalysis(detail);
  renderGenerated(detail);
  refreshHistory();
}

function renderAnalysis(detail) {
  const analysis = detail.analysis;
  const job = detail.job;
  const record = detail.record;

  $("result-company").textContent = [job.company, job.location].filter(Boolean).join(" · ") || "Target role";
  $("result-title").textContent = job.title || "Target role";
  $("result-summary").textContent = job.summary || "";

  const report = analysis.score_report || {};
  const suitability = report.candidate_suitability || {};
  const score = Math.round(suitability.value ?? analysis.score.total);
  const ring = $("score-ring");
  const color = score >= 85 ? "#34d399" : score >= 70 ? "#60a5fa" : score >= 55 ? "#fbbf24" : "#f87171";
  ring.style.background = `conic-gradient(${color} ${score}%, #1e293b ${score}%)`;
  $("score-value").textContent = `${score}%`;
  $("score-label").textContent = analysis.score_label;
  $("score-label").style.color = color;

  const chips = $("top-chips");
  clear(chips);
  const musts = analysis.matches.filter(
    (item) =>
      item.importance === "must_have" &&
      !["Education", "Experience", "Responsibility", "Certification"].includes(item.category),
  );
  musts.slice(0, 12).forEach((match) => {
    chips.appendChild(el("span", `chip ${match.status}`, match.skill));
  });
  if (!musts.length) chips.appendChild(el("span", "chip", "No must-have skills detected"));

  renderBreakdown(analysis);
  renderSkillLists(analysis);
  renderEvidence(analysis);
  renderRecommendations(analysis, job);
  renderKeywords(analysis);
  renderWarnings([...(analysis.warnings || []), ...(job.warnings || [])]);

  const button = $("generate-button");
  button.disabled = false;
  button.textContent =
    record.status === "generated" ? "Regenerate tailored resume" : "Generate tailored resume";
}

function renderBreakdown(analysis) {
  const container = $("breakdown");
  clear(container);
  const report = analysis.score_report;
  let definitions;
  if (report && report.candidate_suitability) {
    definitions = [
      ["Candidate fit", report.candidate_suitability],
      ["Evidence coverage", report.evidence_coverage],
      ["Profile keywords", report.keyword_coverage],
    ];
  } else {
    const score = analysis.score;
    definitions = [
      ["Must-have", { value: score.must_have, max: 55 }],
      ["Nice-to-have", { value: score.nice_to_have, max: 15 }],
      ["Role fit", { value: score.responsibilities, max: 15 }],
      ["Certs", { value: score.certifications, max: 5 }],
      ["Seniority", { value: score.seniority, max: 5 }],
      ["Keywords", { value: score.keywords, max: 5 }],
    ];
  }
  definitions.forEach(([label, metric]) => {
    const maximum = metric.maximum ?? metric.max ?? 100;
    const value = metric.value ?? 0;
    const card = el("div", "breakdown-card");
    card.appendChild(el("span", "", label));
    card.appendChild(el("strong", "", Number(value).toFixed(1)));
    const track = el("div", "breakdown-track");
    const fill = el("div", "breakdown-fill");
    fill.style.width = `${Math.min(100, maximum ? (value / maximum) * 100 : 0)}%`;
    track.appendChild(fill);
    card.appendChild(track);
    if (metric.explanation) {
      const note = el("span", "breakdown-note", metric.explanation);
      note.title = metric.explanation;
      card.appendChild(note);
    }
    container.appendChild(card);
  });
  if (report && report.components) {
    Object.entries(report.components).forEach(([key, metric]) => {
      const card = el("div", "breakdown-card component-card");
      card.appendChild(el("span", "", key.replaceAll("_", " ")));
      card.appendChild(el("strong", "", Number(metric.value || 0).toFixed(1)));
      const track = el("div", "breakdown-track");
      const fill = el("div", "breakdown-fill");
      const maximum = metric.maximum || 100;
      fill.style.width = `${Math.min(100, (Number(metric.value || 0) / maximum) * 100)}%`;
      track.appendChild(fill);
      card.appendChild(track);
      if (metric.explanation) {
        const note = el("span", "breakdown-note", metric.explanation);
        note.title = metric.explanation;
        card.appendChild(note);
      }
      container.appendChild(card);
    });
  }
}

function skillLine(match) {
  const line = el("div", `skill-line ${match.status}`);
  line.appendChild(el("span", "skill-dot"));
  const body = el("div");
  body.appendChild(el("span", "", match.skill));
  const explanation = match.explanation || match.note;
  if (explanation) body.appendChild(el("span", "skill-note", explanation));
  line.appendChild(body);
  return line;
}

function renderSkillLists(analysis) {
  const matched = analysis.matches.filter((item) => item.status === "matched");
  const gaps = analysis.matches.filter((item) => item.status !== "matched");
  $("matched-count").textContent = `${matched.length} matched`;
  $("missing-count").textContent = `${gaps.length} gaps`;
  const matchedList = $("matched-list");
  const gapList = $("gap-list");
  clear(matchedList);
  clear(gapList);
  if (!matched.length) matchedList.appendChild(el("div", "muted", "No direct matches found."));
  matched.forEach((match) => matchedList.appendChild(skillLine(match)));
  if (!gaps.length) gapList.appendChild(el("div", "muted", "No gaps detected."));
  gaps.forEach((match) => gapList.appendChild(skillLine(match)));
}

function renderEvidence(analysis) {
  const container = $("evidence-list");
  clear(container);
  analysis.matches.forEach((match) => {
    if (!match.evidence || !match.evidence.length) return;
    const details = document.createElement("details");
    details.className = "evidence";
    const summary = document.createElement("summary");
    summary.appendChild(el("span", "", match.skill));
    summary.appendChild(el("span", `chip ${match.status}`, match.evidence_class || match.status));
    details.appendChild(summary);
    const body = el("div", "evidence-body");
    match.evidence.forEach((evidence) => {
      body.appendChild(el("div", "evidence-source", evidence.source));
      body.appendChild(el("div", "evidence-text", evidence.text));
    });
    details.appendChild(body);
    container.appendChild(details);
  });
  if (!container.children.length) {
    container.appendChild(el("div", "muted", "No supporting evidence was found for the job requirements."));
  }
}

function renderRecommendations(analysis, job) {
  const list = $("recommendations");
  clear(list);
  const items = [...(analysis.recommendations || [])];
  if (job.role_family) items.push(`Detected role family: ${job.role_family.replaceAll("_", " ")}.`);
  if (job.seniority) items.push(`Detected seniority: ${job.seniority}.`);
  items.forEach((item) => list.appendChild(el("li", "", item)));
}

function renderKeywords(analysis) {
  $("keyword-pct").textContent = `${analysis.keyword_coverage_pct}% covered`;
  const wrap = $("keyword-wrap");
  clear(wrap);
  const entries = Object.entries(analysis.keyword_coverage || {});
  entries
    .sort((a, b) => Number(a[1]) - Number(b[1]))
    .forEach(([keyword, present]) => {
      const chip = el("span", `keyword ${present ? "present" : ""}`, keyword);
      chip.title = present ? "Found in your profile evidence" : "Not found in your profile evidence";
      wrap.appendChild(chip);
    });
  if (!entries.length) wrap.appendChild(el("div", "muted", "No keywords detected."));
}

function renderWarnings(warnings) {
  const unique = [...new Set((warnings || []).filter(Boolean))];
  const panel = $("warning-panel");
  const list = $("warnings");
  clear(list);
  panel.hidden = unique.length === 0;
  unique.forEach((warning) => list.appendChild(el("li", "", warning)));
}

function renderGenerated(detail) {
  const record = detail.record;
  const generated = $("generated");
  const artifacts = record.artifacts || [];
  const hasArtifacts = artifacts.length > 0 && record.status === "generated";
  generated.hidden = !hasArtifacts;
  if (!hasArtifacts) return;

  const metadata = detail.metadata || {};
  const usedLlm = String(metadata.method || "").startsWith("llm");
  const method = usedLlm ? "LLM-tailored" : "Deterministic";
  const model = metadata.model && usedLlm ? ` · ${metadata.model}` : "";
  $("generation-method").textContent = `${method}${model}`;
  const downloads = $("downloads");
  clear(downloads);
  const labels = { pdf: "Download PDF", docx: "Download DOCX", markdown: "Markdown", html: "HTML" };
  artifacts.forEach((artifact) => {
    const link = document.createElement("a");
    link.className = "download-button";
    link.href = `/api/applications/${record.id}/artifacts/${artifact.filename}`;
    link.textContent = labels[artifact.kind] || artifact.filename;
    link.target = artifact.kind === "html" ? "_blank" : "_self";
    downloads.appendChild(link);
  });

  const preview = $("resume-preview");
  const htmlArtifact = artifacts.find((artifact) => artifact.kind === "html");
  if (htmlArtifact) {
    preview.src = `/api/applications/${record.id}/artifacts/${htmlArtifact.filename}?v=${Date.now()}`;
  }

  renderAts(detail.ats_report);
  renderValidation(detail.validation);
}

function renderAts(report) {
  const container = $("ats-checks");
  clear(container);
  if (!report || !report.checks) {
    container.appendChild(el("div", "muted", "ATS report unavailable."));
    return;
  }
  report.checks.forEach((check) => {
    const item = el("div", `check-item ${check.passed ? "passed" : "failed"}`);
    item.appendChild(el("span", "check-icon", check.passed ? "✓" : "!"));
    const body = el("div");
    body.appendChild(el("span", "check-title", check.name));
    body.appendChild(el("span", "check-detail", check.detail));
    item.appendChild(body);
    container.appendChild(item);
  });
}

function renderValidation(validation) {
  const container = $("validation-checks");
  clear(container);
  if (!validation) {
    container.appendChild(el("div", "muted", "Validation report unavailable."));
    return;
  }
  const issues = validation.issues || [];
  const reviewItems = validation.review_items || [];
  if (validation.valid && !issues.length && !reviewItems.length) {
    const item = el("div", "check-item passed");
    item.appendChild(el("span", "check-icon", "✓"));
    const body = el("div");
    body.appendChild(el("span", "check-title", "No unsupported claims detected"));
    body.appendChild(el("span", "check-detail", "Every generated fact passed the evidence check."));
    item.appendChild(body);
    container.appendChild(item);
    return;
  }
  if (reviewItems.length) {
    const item = el("div", "check-item warning");
    item.appendChild(el("span", "check-icon", "!"));
    const body = el("div");
    body.appendChild(el("span", "check-title", "Human review required"));
    body.appendChild(
      el(
        "span",
        "check-detail",
        `${validation.checked_claims || 0} claims checked, ${validation.supported_claims || 0} supported. Review the items below before downloading.`,
      ),
    );
    item.appendChild(body);
    container.appendChild(item);
  }
  issues.forEach((issue) => {
    const warning = issue.severity === "warning";
    const item = el("div", `check-item ${warning ? "warning" : "failed"}`);
    item.appendChild(el("span", "check-icon", warning ? "!" : "×"));
    const body = el("div");
    body.appendChild(el("span", "check-title", issue.location));
    body.appendChild(el("span", "check-detail", issue.message));
    item.appendChild(body);
    container.appendChild(item);
  });
  reviewItems.forEach((review) => {
    const item = el("div", "check-item warning");
    item.appendChild(el("span", "check-icon", "?"));
    const body = el("div");
    body.appendChild(el("span", "check-title", `${review.location}: ${review.claim}`));
    const detail = review.source
      ? `${review.reason} Closest source: ${review.source}`
      : review.reason;
    body.appendChild(el("span", "check-detail", detail));
    item.appendChild(body);
    container.appendChild(item);
  });
}

function setFormError(message) {
  const error = $("form-error");
  error.hidden = !message;
  error.textContent = message || "";
}

async function submitAnalysis(event) {
  event.preventDefault();
  setFormError("");
  hideNotice();
  const url = $("job-url").value.trim();
  const text = $("job-text").value.trim();
  if (!url && !text) {
    setFormError("Provide a job URL or paste the job description.");
    return;
  }
  const button = $("analyze-button");
  button.disabled = true;
  button.textContent = "Starting...";
  try {
    const data = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({
        url,
        text,
        company: $("job-company").value.trim(),
        role: $("job-role").value.trim(),
      }),
    });
    state.currentId = data.application.id;
    state.mode = "analysis";
    showProgress("analysis", "Starting analysis...");
    poll();
  } catch (error) {
    setFormError(error.message);
    button.disabled = false;
    button.textContent = "Analyze match";
  }
}

async function generateResume() {
  if (!state.currentId) return;
  hideNotice();
  const button = $("generate-button");
  button.disabled = true;
  button.textContent = "Starting...";
  try {
    await api(`/api/applications/${state.currentId}/generate`, {
      method: "POST",
      body: "{}",
    });
    showProgress("generation", "Starting resume generation...");
    poll();
  } catch (error) {
    showNotice(error.message, "error");
    button.disabled = false;
    button.textContent = "Generate tailored resume";
  }
}

async function poll() {
  if (!state.currentId) return;
  try {
    const data = await api(`/api/applications/${state.currentId}/status`);
    const record = data.application;
    setProgress(record);
    const terminal = ["analyzed", "generated", "error", "needs_paste"].includes(record.status);
    if (!terminal) {
      state.timer = setTimeout(poll, 1400);
      return;
    }
    stopPolling();
    $("analyze-button").disabled = false;
    $("analyze-button").textContent = "Analyze match";
    const detail = await api(`/api/applications/${state.currentId}`);
    $("progress-card").hidden = true;
    renderDetail(detail);
    if (record.status === "generated") {
      showNotice("Tailored resume generated. Review the preview and download the format you need.", "success");
    }
    refreshHistory();
  } catch (error) {
    stopPolling();
    showNotice(error.message, "error");
    $("analyze-button").disabled = false;
    $("analyze-button").textContent = "Analyze match";
  }
}

$("analyze-form").addEventListener("submit", submitAnalysis);
$("generate-button").addEventListener("click", generateResume);
$("refresh-profile").addEventListener("click", refreshProfile);

refreshHealth();
refreshHistory();
