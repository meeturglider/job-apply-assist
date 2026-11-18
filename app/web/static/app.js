const state = {
  currentId: null,
  timer: null,
  mode: "analysis",
  detail: null,
  resumeDraft: null,
  portalMode: false,
  allowRegistration: false,
  authMode: "login",
  profileReady: false,
  revalidating: false,
  csrf: "",
  accountEmail: "",
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
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.csrf && !["GET", "HEAD"].includes(method)) {
    headers["X-CSRF-Token"] = state.csrf;
  }
  const response = await fetch(path, { headers, ...options });
  if (response.status === 401 && state.portalMode && !path.startsWith("/api/auth/")) {
    showAuthPanel("Sign in again to continue.");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      const payload = body.detail;
      if (typeof payload === "string") {
        detail = payload;
      } else if (payload && typeof payload === "object") {
        detail = payload.message || detail;
        if (Array.isArray(payload.errors) && payload.errors.length) {
          const first = payload.errors[0];
          if (first && first.msg) {
            const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "";
            detail += ` ${field ? `${field}: ` : ""}${first.msg}`;
          }
        }
      }
      if (body.request_id) {
        detail += ` (request ${body.request_id})`;
      }
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

function setProgressDial(value) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  const dial = $("progress-dial");
  dial.style.setProperty("--progress", `${pct}%`);
  dial.setAttribute("aria-valuenow", String(pct));
  $("progress-percent").textContent = `${pct}%`;
}

function showProgress(mode, message = "Working...") {
  state.mode = mode;
  $("empty-state").hidden = true;
  $("result").hidden = true;
  $("progress-card").hidden = false;
  $("progress-message").textContent = message;
  setProgressDial(0);
  const steps = $("progress-steps");
  clear(steps);
  const labels =
    mode === "generation"
      ? ["Load analysis", "Build tailored resume", "Truthfulness check", "Render files"]
      : mode === "revalidation"
        ? ["Apply notes", "Score evidence", "Refresh suggestions", "Finish"]
        : [
            "Fetch job description",
            "Extract candidates",
            "Review with local model",
            "Load profile and match",
            "Finish",
          ];
  labels.forEach((label) => steps.appendChild(el("li", "", label)));
}

function setProgress(record) {
  $("progress-card").hidden = false;
  $("progress-message").textContent = record.message || "Working...";
  $("progress-stage").textContent = (record.stage || "working").replaceAll("_", " ");
  setProgressDial(record.progress_pct);
  const steps = [...$("progress-steps").children];
  const stageMaps = {
    analysis: {
      queued: 0,
      fetching: 0,
      manual_paste: 0,
      extracting: 1,
      reviewing: 2,
      matching: 3,
      scoring: 3,
      suggestions: 4,
      analyzing: 4,
      done: 4,
      error: 0,
    },
    generation: {
      tailoring: 1,
      rendering: 3,
      done: 3,
      error: 0,
    },
    revalidation: {
      notes: 0,
      scoring: 1,
      suggestions: 2,
      done: 3,
      error: 0,
    },
  };
  const active = (stageMaps[state.mode] ?? stageMaps.analysis)[record.stage] ?? 0;
  steps.forEach((step, index) => {
    step.classList.toggle("done", index < active);
    step.classList.toggle("active", index === active);
  });
}

function stopPolling() {
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
}

function updateProfileStatus(profile) {
  const ps = $("profile-status");
  state.profileReady = Boolean(profile.uploaded_resume || profile.master_resume_exists);
  ps.textContent = profile.uploaded_resume
    ? "Uploaded resume active"
    : profile.master_resume_exists
      ? "Profile source ready"
      : "Awaiting resume upload";
  ps.className = `pill ${state.profileReady ? "" : "warning"}`;
}

async function refreshHealth() {
  try {
    const health = await api("/api/health");
    const llm = $("llm-status");
    llm.textContent = health.llm_available
      ? `${health.llm_model} ready`
      : "Deterministic mode";
    llm.className = `pill ${health.llm_available ? "success" : "warning"}`;
    const profile =
      state.portalMode && state.accountEmail
        ? await api("/api/profile/status")
        : health.profile;
    updateProfileStatus(profile);
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

async function uploadResume() {
  const input = $("resume-file");
  const file = input.files && input.files[0];
  if (!file) {
    showNotice("Choose a Markdown or text resume first.", "warning");
    return;
  }
  if (!/\.(md|markdown|txt)$/i.test(file.name) && !file.type.startsWith("text/")) {
    showNotice("Only Markdown and text resumes are supported.", "error");
    return;
  }
  if (file.size > 1_000_000) {
    showNotice("The resume file must be 1 MB or smaller.", "error");
    return;
  }
  const button = $("upload-resume");
  button.disabled = true;
  button.textContent = "Uploading...";
  try {
    const text = await file.text();
    const profile = await api("/api/profile/resume", {
      method: "POST",
      body: JSON.stringify({ text, filename: file.name }),
    });
    showNotice(
      `Resume uploaded: ${profile.experience_count} roles, ${profile.skill_count} skills.`,
      "success",
    );
    if (profile.status) updateProfileStatus(profile.status);
    await refreshHealth();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Upload resume";
  }
}

async function resetResume() {
  const button = $("reset-resume");
  button.disabled = true;
  button.textContent = "Resetting...";
  try {
    const profile = await api("/api/profile/resume/reset", { method: "POST", body: "{}" });
    $("resume-file").value = "";
    showNotice(
      `Default resume restored: ${profile.experience_count} roles, ${profile.skill_count} skills.`,
      "success",
    );
    await refreshHealth();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Use default";
  }
}

function profileUrls() {
  return $("profile-urls")
    .value.split("\n")
    .map((value) => value.trim())
    .filter(Boolean);
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

const ORIGIN_LABELS = {
  resume: "Default resume",
  uploaded_resume: "Uploaded resume",
  portfolio: "Portfolio",
  additional_url: "Additional URL",
  user_note: "User note",
  derived: "Derived",
};

function originLabel(origin) {
  return ORIGIN_LABELS[origin] || "Derived";
}

function evidenceOrigin(match) {
  const first = (match.evidence || [])[0];
  return first?.origin || "derived";
}

function currentNotes() {
  return state.detail?.record?.notes || [];
}

function notesPending(record) {
  return Boolean(record.notes_revision && record.notes_revision !== record.notes_validated_revision);
}

async function saveApplicationNotes(entries) {
  const data = await api(`/api/applications/${state.currentId}/notes`, {
    method: "PUT",
    body: JSON.stringify({ notes: entries }),
  });
  state.detail.record = data.application;
  renderAnalysis(state.detail);
  showNotice("Evidence notes saved. Reanalyze when you're ready.", "success");
}

function noteEditor(target) {
  const wrapper = document.createElement("details");
  wrapper.className = "note-editor";
  wrapper.appendChild(el("summary", "", "Add evidence note"));
  const body = el("div", "note-editor-body");
  const existing = currentNotes().find((note) => note.target === target);
  const textarea = document.createElement("textarea");
  textarea.className = "note-input";
  textarea.rows = 3;
  textarea.placeholder = `What evidence supports "${target}" for this application?`;
  textarea.value = existing?.text || "";
  body.appendChild(textarea);

  const status = el("div", "micro note-status");
  if (existing) {
    status.textContent = `Status: ${existing.status}${existing.reason ? ` · ${existing.reason}` : ""}`;
  } else {
    status.textContent = "Notes are saved for this application only.";
  }
  body.appendChild(status);

  const save = el("button", "ghost-button", "Save evidence note");
  save.type = "button";
  save.addEventListener("click", async () => {
    const text = textarea.value.trim();
    if (text.length < 10) {
      status.textContent = "Add at least 10 characters.";
      return;
    }
    save.disabled = true;
    try {
      const entries = currentNotes()
        .filter((note) => note.target !== target)
        .map((note) => ({ target: note.target, text: note.text }));
      entries.push({ target, text });
      await saveApplicationNotes(entries);
    } catch (error) {
      status.textContent = error.message;
      save.disabled = false;
    }
  });
  body.appendChild(save);

  if (existing) {
    const remove = el("button", "ghost-button", "Remove note");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        const entries = currentNotes()
          .filter((note) => note.target !== target)
          .map((note) => ({ target: note.target, text: note.text }));
        await saveApplicationNotes(entries);
      } catch (error) {
        status.textContent = error.message;
        remove.disabled = false;
      }
    });
    body.appendChild(remove);
  }
  wrapper.appendChild(body);
  return wrapper;
}

async function revalidateNotes() {
  if (!state.currentId || state.revalidating) return;
  state.revalidating = true;
  const button = $("revalidate-button");
  button.disabled = true;
  button.textContent = "Revalidating...";
  try {
    await api(`/api/applications/${state.currentId}/revalidate`, {
      method: "POST",
      body: "{}",
    });
    showProgress("revalidation", "Reanalyzing evidence notes...");
    stopPolling();
    poll();
  } catch (error) {
    state.revalidating = false;
    showNotice(error.message, "error");
    button.disabled = false;
    button.textContent = "Reanalyze notes";
  }
}

function renderProvenance(detail) {
  const analysis = detail.analysis || {};
  const profile = detail.profile || {};
  const provenance = analysis.provenance || profile.provenance || {};
  const counts = provenance.evidence_counts || {};
  const chips = $("provenance-chips");
  clear(chips);
  Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .forEach(([origin, count]) => {
      chips.appendChild(el("span", `chip origin-${origin}`, `${originLabel(origin)}: ${count}`));
    });
  if (!chips.children.length) chips.appendChild(el("span", "micro", "No source breakdown available."));

  const resume = provenance.resume_name || "resume";
  const portfolio = provenance.portfolio_cached ? "portfolio loaded" : "portfolio unavailable";
  $("provenance-summary").textContent = `${resume} · ${portfolio}`;

  const details = $("provenance-detail-list");
  clear(details);
  const list = document.createElement("ul");
  list.className = "recommendations";
  Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .forEach(([origin, count]) => {
      list.appendChild(el("li", "", `${originLabel(origin)}: ${count} evidence item(s)`));
    });
  if (provenance.portfolio_url) {
    list.appendChild(el("li", "", `Portfolio source: ${provenance.portfolio_url}`));
  }
  (provenance.extra_urls || []).forEach((url) => list.appendChild(el("li", "", `Additional source: ${url}`)));
  details.appendChild(list);

  const keywordSources = analysis.keyword_sources || {};
  const keywordDetails = document.createElement("details");
  keywordDetails.className = "evidence";
  keywordDetails.appendChild(el("summary", "", "Per-keyword evidence support"));
  const keywordList = document.createElement("ul");
  keywordList.className = "recommendations";
  Object.entries(keywordSources)
    .slice(0, 40)
    .forEach(([keyword, sources]) => {
      const value = Array.isArray(sources) && sources.length ? sources.join("; ") : "No evidence found";
      keywordList.appendChild(el("li", "", `${keyword}: ${value}`));
    });
  keywordDetails.appendChild(keywordList);
  details.appendChild(keywordDetails);
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
  renderProvenance(detail);
  renderApplyWorkflow(detail);
  renderRecommendations(analysis, job);
  renderKeywords(analysis);
  renderWarnings([...(analysis.warnings || []), ...(job.warnings || [])]);

  const pending = notesPending(record);
  const button = $("generate-button");
  button.disabled = pending;
  button.textContent = pending
    ? "Reanalyze notes to generate"
    : record.status === "generated"
      ? "Regenerate tailored resume"
      : "Generate tailored resume";
  const revalidate = $("revalidate-button");
  revalidate.hidden = !pending;
  revalidate.disabled = false;
  revalidate.textContent = "Reanalyze notes";
}

function renderApplyWorkflow(detail) {
  const record = detail.record || {};
  const tracker = $("tracker-status");
  if (tracker) tracker.value = record.tracker_status || "draft";
  const contacts = $("contact-list");
  if (contacts) {
    clear(contacts);
    (record.contacts || []).forEach((contact) => {
      const label = `${contact.email}${contact.name ? ` · ${contact.name}` : ""}`;
      contacts.appendChild(el("span", "chip", label));
    });
    if (!(record.contacts || []).length) {
      contacts.appendChild(el("span", "micro", "No public contact found; add one manually."));
    }
  }
  const draft = record.email_draft;
  if ($("email-draft")) $("email-draft").value = draft ? draft.body : "";
  const link = $("mailto-link");
  if (link) {
    link.hidden = !draft;
    if (draft) {
      link.href = `mailto:${encodeURIComponent(draft.to)}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`;
    }
  }
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
    const value = Math.max(0, Math.min(Number(metric.value) || 0, maximum));
    const card = el("div", "breakdown-card");
    card.appendChild(el("span", "", label));
    card.appendChild(el("strong", "", value.toFixed(1)));
    const track = el("div", "breakdown-track");
    const fill = el("div", "breakdown-fill");
    fill.style.width = `${maximum ? (value / maximum) * 100 : 0}%`;
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
  gaps.forEach((match) => {
    gapList.appendChild(skillLine(match));
    const note = currentNotes().find((item) => item.target === match.skill);
    if (note) gapList.appendChild(el("div", "micro note-status", `${note.status}: ${note.reason}`));
    gapList.appendChild(noteEditor(match.skill));
  });
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
    summary.appendChild(
      el("span", `origin-badge origin-${evidenceOrigin(match)}`, originLabel(evidenceOrigin(match))),
    );
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
  const wrap = $("keyword-wrap");
  clear(wrap);
  const entries = Object.entries(analysis.keyword_coverage || {});
  const covered = entries.filter(([, present]) => present).length;
  const pct = entries.length ? Math.round((covered / entries.length) * 1000) / 10 : 100;
  $("keyword-pct").textContent = `${covered} of ${entries.length} keywords (${pct.toFixed(1)}%)`;
  entries
    .sort((a, b) => Number(a[1]) - Number(b[1]))
    .forEach(([keyword, present]) => {
      const sources = (analysis.keyword_sources || {})[keyword] || [];
      const origins = (analysis.keyword_origins || {})[keyword] || [];
      const chip = el(
        "span",
        `keyword ${present ? "present" : ""} ${origins.length ? `origin-${origins[0]}` : ""}`,
        keyword,
      );
      chip.title = sources.length
        ? `Supported by: ${sources.join("; ")}`
        : "No evidence found in your profile or portfolio.";
      wrap.appendChild(chip);
    });
  if (!entries.length) wrap.appendChild(el("div", "muted", "No keywords detected."));

  const suggestions = $("keyword-suggestions");
  clear(suggestions);
  (analysis.keyword_suggestions || []).forEach((item) => {
    const box = el("div", "suggestion");
    box.appendChild(el("strong", "", item.keyword));
    if (item.origin === "semantic") box.appendChild(el("span", "suggestion-badge", "Possible profile edit"));
    box.appendChild(el("div", "", item.suggestion));
    if (item.evidence) box.appendChild(el("span", "micro", `Evidence: ${item.evidence}`));
    else if (item.source) box.appendChild(el("span", "micro", `Based on ${item.source}`));
    box.appendChild(el("div", "micro", "No supporting evidence found; this is not proof the skill is absent."));
    box.appendChild(noteEditor(item.keyword));
    suggestions.appendChild(box);
  });
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
  $("resume-editor").hidden = true;
  state.resumeDraft = null;
  if (!hasArtifacts) return;

  const metadata = detail.metadata || {};
  const rawMethod = String(metadata.method || "");
  const usedLlm = rawMethod.startsWith("llm");
  const method =
    rawMethod === "manual-edit" ? "Manually edited" : usedLlm ? "LLM-tailored" : "Deterministic";
  const model = metadata.model && usedLlm ? ` · ${metadata.model}` : "";
  const sourceCounts = metadata.evidence_sources?.evidence_counts || {};
  const sourceMix = Object.entries(sourceCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([origin, count]) => `${originLabel(origin)} ${count}`)
    .join(" · ");
  $("generation-method").textContent = `${method}${model}${sourceMix ? ` · ${sourceMix}` : ""}`;
  const downloads = $("downloads");
  clear(downloads);
  const labels = { pdf: "Download PDF", docx: "Download DOCX", markdown: "Markdown", html: "HTML" };
  artifacts.forEach((artifact) => {
    const link = document.createElement("a");
    link.className = "download-button";
    link.href = `/api/applications/${record.id}/artifacts/${artifact.filename}?download=1`;
    link.textContent = labels[artifact.kind] || artifact.filename;
    link.target = "_self";
    link.setAttribute("download", artifact.filename);
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

function cloneResume(resume) {
  return JSON.parse(JSON.stringify(resume));
}

function parseLineList(value) {
  return String(value || "")
    .split("\n")
    .map((line) => line.trim().replace(/^[-*•]\s*/, ""))
    .filter(Boolean);
}

function parseInlineList(value) {
  return String(value || "")
    .split(/[,;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function resumeDateRank(value) {
  const text = String(value || "").trim();
  if (text.toLowerCase() === "present") return 999912;
  const match = /^([A-Za-z]{3})\s+(\d{4})$/.exec(text);
  if (!match) return 0;
  const months = {
    jan: 1,
    feb: 2,
    mar: 3,
    apr: 4,
    may: 5,
    jun: 6,
    jul: 7,
    aug: 8,
    sep: 9,
    oct: 10,
    nov: 11,
    dec: 12,
  };
  return Number(match[2]) * 100 + (months[match[1].slice(0, 3).toLowerCase()] || 0);
}

function roleListFor(entry) {
  const roles = entry.roles?.length
    ? entry.roles
    : [{ role: entry.role || "", start: entry.start || "", end: entry.end || "" }];
  return roles.map((role) => ({
    role: role.role || "",
    start: role.start || "",
    end: role.end || "",
  }));
}

function applyDataset(node, values) {
  Object.entries(values || {}).forEach(([key, value]) => {
    node.dataset[key] = String(value);
  });
}

function editorInput(parent, label, value, dataset) {
  const wrapper = el("label", "editor-field", label);
  const input = el("input", "note-input");
  input.type = "text";
  input.value = value || "";
  applyDataset(input, dataset);
  wrapper.appendChild(input);
  parent.appendChild(wrapper);
  return input;
}

function editorTextarea(parent, label, value, dataset) {
  const wrapper = el("label", "editor-field", label);
  const textarea = el("textarea", "note-input");
  textarea.value = value || "";
  applyDataset(textarea, dataset);
  wrapper.appendChild(textarea);
  parent.appendChild(wrapper);
  return textarea;
}

function addButton(label, handler) {
  const button = el("button", "icon-button", "+");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", handler);
  return button;
}

function removeButton(label, handler) {
  const button = el("button", "icon-button danger", "×");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", handler);
  return button;
}

function editorSection(title, addHandler) {
  const section = el("div", "resume-editor-section");
  const head = el("div", "resume-editor-section-head");
  head.appendChild(el("h4", "", title));
  if (addHandler) head.appendChild(addButton(`Add ${title.toLowerCase()}`, addHandler));
  section.appendChild(head);
  const body = el("div", "resume-editor-section-body");
  section.appendChild(body);
  return { section, body };
}

function editorItem(parent, title, removeHandler) {
  const item = el("div", "editor-item");
  const head = el("div", "editor-item-head");
  head.appendChild(el("span", "editor-item-title", title));
  head.appendChild(removeButton(`Remove ${title}`, removeHandler));
  item.appendChild(head);
  const body = el("div", "editor-fields");
  item.appendChild(body);
  parent.appendChild(item);
  return body;
}

function syncResumeDraftFromEditor() {
  const draft = state.resumeDraft;
  if (!draft) return;
  const editor = $("resume-editor");
  const summary = editor.querySelector('[data-resume-field="summary"]');
  if (summary) draft.summary = summary.value.trim();

  editor.querySelectorAll("[data-skill-field]").forEach((node) => {
    const group = draft.skills?.[Number(node.dataset.skillIndex)];
    if (!group) return;
    const field = node.dataset.skillField;
    group[field] = field === "items" ? parseInlineList(node.value) : node.value.trim();
  });

  editor.querySelectorAll("[data-experience-field]").forEach((node) => {
    const entry = draft.experience?.[Number(node.dataset.experienceIndex)];
    if (!entry) return;
    const field = node.dataset.experienceField;
    entry[field] = field === "bullets" ? parseLineList(node.value) : node.value.trim();
  });

  editor.querySelectorAll("[data-role-field]").forEach((node) => {
    const entry = draft.experience?.[Number(node.dataset.experienceIndex)];
    if (!entry) return;
    if (!entry.roles?.length) entry.roles = roleListFor(entry);
    const index = Number(node.dataset.roleIndex);
    while (entry.roles.length <= index) {
      entry.roles.push({ role: "", start: "", end: "" });
    }
    entry.roles[index][node.dataset.roleField] = node.value.trim();
  });

  (draft.experience || []).forEach((entry) => {
    if (!entry.roles?.length) return;
    const starts = entry.roles.map((role) => role.start).filter(Boolean);
    const ends = entry.roles.map((role) => role.end).filter(Boolean);
    entry.role = entry.roles[0].role || entry.role;
    entry.start = starts.sort((a, b) => resumeDateRank(a) - resumeDateRank(b))[0] || "";
    entry.end = entry.roles.some((role) => role.end.toLowerCase() === "present")
      ? "Present"
      : ends.sort((a, b) => resumeDateRank(a) - resumeDateRank(b))[ends.length - 1] || "";
  });

  editor.querySelectorAll("[data-project-field]").forEach((node) => {
    const project = draft.projects?.[Number(node.dataset.projectIndex)];
    if (!project) return;
    const field = node.dataset.projectField;
    project[field] = field === "bullets" ? parseLineList(node.value) : node.value.trim();
  });
}

function renderResumeEditor() {
  const editor = $("resume-editor");
  const draft = state.resumeDraft;
  clear(editor);
  if (!draft) {
    editor.appendChild(el("p", "muted", "Resume content is unavailable."));
    return;
  }
  draft.skills = draft.skills || [];
  draft.experience = draft.experience || [];
  draft.projects = draft.projects || [];
  const sections = el("div", "resume-editor-sections");

  const summary = editorSection("Career Summary");
  editorTextarea(summary.body, "Targeted summary", draft.summary || "", {
    resumeField: "summary",
  });
  sections.appendChild(summary.section);

  const skills = editorSection("Skills", () => {
    syncResumeDraftFromEditor();
    draft.skills.push({ category: "New category", items: [] });
    renderResumeEditor();
  });
  draft.skills.forEach((group, index) => {
    const body = editorItem(skills.body, group.category || `Skill group ${index + 1}`, () => {
      syncResumeDraftFromEditor();
      draft.skills.splice(index, 1);
      renderResumeEditor();
    });
    editorInput(body, "Category", group.category || "", {
      skillIndex: index,
      skillField: "category",
    });
    editorTextarea(body, "Items (comma or line separated)", (group.items || []).join(", "), {
      skillIndex: index,
      skillField: "items",
    });
  });
  sections.appendChild(skills.section);

  const experience = editorSection("Professional Experience", () => {
    syncResumeDraftFromEditor();
    draft.experience.push({ company: "", role: "", location: "", bullets: [""], roles: [] });
    renderResumeEditor();
  });
  draft.experience.forEach((entry, index) => {
    const roles = roleListFor(entry);
    const roleNames = roles.map((role) => role.role).filter(Boolean).join(" / ");
    const title = `${entry.company || "Company"} — ${roleNames || "Role"}`;
    const body = editorItem(experience.body, title, () => {
      syncResumeDraftFromEditor();
      draft.experience.splice(index, 1);
      renderResumeEditor();
    });
    editorInput(body, "Company", entry.company || "", {
      experienceIndex: index,
      experienceField: "company",
    });
    editorInput(body, "Location", entry.location || "", {
      experienceIndex: index,
      experienceField: "location",
    });

    const rolesWrap = el("div", "editor-roles");
    rolesWrap.appendChild(el("span", "editor-subtitle", "Roles"));
    roles.forEach((role, roleIndex) => {
      const roleBody = editorItem(
        rolesWrap,
        role.role || `Role ${roleIndex + 1}`,
        () => {
          syncResumeDraftFromEditor();
          entry.roles = roleListFor(entry);
          entry.roles.splice(roleIndex, 1);
          renderResumeEditor();
        },
      );
      editorInput(roleBody, "Role", role.role || "", {
        experienceIndex: index,
        roleIndex,
        roleField: "role",
      });
      editorInput(roleBody, "Start", role.start || "", {
        experienceIndex: index,
        roleIndex,
        roleField: "start",
      });
      editorInput(roleBody, "End", role.end || "", {
        experienceIndex: index,
        roleIndex,
        roleField: "end",
      });
    });
    const roleActions = el("div", "editor-role-actions");
    roleActions.appendChild(
      addButton("Add role", () => {
        syncResumeDraftFromEditor();
        entry.roles = roleListFor(entry);
        entry.roles.push({ role: "", start: "", end: "" });
        renderResumeEditor();
      }),
    );
    roleActions.appendChild(el("span", "muted", "Add role"));
    body.appendChild(roleActions);
    body.appendChild(rolesWrap);

    editorTextarea(body, "Bullets (one per line)", (entry.bullets || []).join("\n"), {
      experienceIndex: index,
      experienceField: "bullets",
    });
    const bulletActions = el("div", "editor-role-actions");
    bulletActions.appendChild(
      addButton("Add bullet", () => {
        syncResumeDraftFromEditor();
        entry.bullets = entry.bullets || [];
        entry.bullets.push("");
        renderResumeEditor();
      }),
    );
    bulletActions.appendChild(el("span", "muted", "Add bullet"));
    body.appendChild(bulletActions);
  });
  sections.appendChild(experience.section);

  const projects = editorSection("Projects", () => {
    syncResumeDraftFromEditor();
    draft.projects.push({ name: "", context: "", period: "", bullets: [""] });
    renderResumeEditor();
  });
  draft.projects.forEach((project, index) => {
    const title = project.name || `Project ${index + 1}`;
    const body = editorItem(projects.body, title, () => {
      syncResumeDraftFromEditor();
      draft.projects.splice(index, 1);
      renderResumeEditor();
    });
    editorInput(body, "Name", project.name || "", {
      projectIndex: index,
      projectField: "name",
    });
    editorInput(body, "Context", project.context || "", {
      projectIndex: index,
      projectField: "context",
    });
    editorInput(body, "Period", project.period || "", {
      projectIndex: index,
      projectField: "period",
    });
    editorTextarea(body, "Bullets (one per line)", (project.bullets || []).join("\n"), {
      projectIndex: index,
      projectField: "bullets",
    });
    const bulletActions = el("div", "editor-role-actions");
    bulletActions.appendChild(
      addButton("Add project bullet", () => {
        syncResumeDraftFromEditor();
        project.bullets = project.bullets || [];
        project.bullets.push("");
        renderResumeEditor();
      }),
    );
    bulletActions.appendChild(el("span", "muted", "Add bullet"));
    body.appendChild(bulletActions);
  });
  sections.appendChild(projects.section);

  editor.appendChild(sections);

  const actions = el("div", "resume-editor-actions");
  const save = el("button", "primary-button", "Save resume edits");
  save.id = "save-resume-edits";
  save.type = "button";
  save.addEventListener("click", saveResumeEdits);
  const cancel = el("button", "ghost-button", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", () => {
    editor.hidden = true;
    state.resumeDraft = null;
  });
  actions.appendChild(save);
  actions.appendChild(cancel);
  editor.appendChild(actions);
}

function toggleResumeEditor() {
  const editor = $("resume-editor");
  if (!editor.hidden) {
    syncResumeDraftFromEditor();
    editor.hidden = true;
    state.resumeDraft = null;
    return;
  }
  if (!state.detail?.resume) return;
  state.resumeDraft = cloneResume(state.detail.resume);
  renderResumeEditor();
  editor.hidden = false;
}

async function saveResumeEdits() {
  if (!state.currentId || !state.resumeDraft) return;
  syncResumeDraftFromEditor();
  const resume = cloneResume(state.resumeDraft);
  const button = $("save-resume-edits");
  button.disabled = true;
  button.textContent = "Saving edits...";
  try {
    const detail = await api(`/api/applications/${state.currentId}/resume`, {
      method: "PUT",
      body: JSON.stringify(resume),
    });
    renderDetail(detail);
    showNotice("Resume edits applied. Preview and downloads refreshed.", "success");
  } catch (error) {
    showNotice(error.message || "Could not save resume edits.", "error");
    button.disabled = false;
    button.textContent = "Save resume edits";
  }
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
  if (state.portalMode && !state.profileReady) {
    setFormError("Upload a resume before analyzing a job.");
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
        profile_urls: profileUrls(),
      }),
    });
    state.currentId = data.application.id;
    state.mode = "analysis";
    button.textContent = "Analyzing...";
    showProgress("analysis", "Analyzing job...");
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
    state.revalidating = false;
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
    state.revalidating = false;
    showNotice(error.message, "error");
    $("analyze-button").disabled = false;
    $("analyze-button").textContent = "Analyze match";
  }
}

async function reloadDetail() {
  if (!state.currentId) return;
  renderDetail(await api(`/api/applications/${state.currentId}`));
}

async function setTrackerStatus() {
  if (!state.currentId) return;
  try {
    const value = $("tracker-status").value;
    const data = await api(`/api/applications/${state.currentId}/tracker`, {
      method: "PATCH",
      body: JSON.stringify({ status: value }),
    });
    state.detail.record = data.application;
    renderApplyWorkflow(state.detail);
    showNotice(`Tracker status: ${value.replaceAll("_", " ")}.`, "success");
    refreshHistory();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function saveContact() {
  if (!state.currentId) return;
  const email = $("contact-email").value.trim();
  if (!email) {
    showNotice("Enter a recruiter email first.", "warning");
    return;
  }
  try {
    const data = await api(`/api/applications/${state.currentId}/contacts`, {
      method: "POST",
      body: JSON.stringify({ email, name: $("contact-name").value.trim() }),
    });
    state.detail.record = data.application;
    renderApplyWorkflow(state.detail);
    showNotice("Contact saved for this application.", "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function createOutreach() {
  if (!state.currentId) return;
  try {
    const data = await api(`/api/applications/${state.currentId}/outreach`, {
      method: "POST",
      body: JSON.stringify({
        contact_email: $("contact-email").value.trim(),
        user_note: $("outreach-note").value.trim(),
      }),
    });
    const detail = await api(`/api/applications/${state.currentId}`);
    renderDetail(detail);
    if (data.mailto) {
      const link = $("mailto-link");
      link.href = data.mailto;
      link.hidden = false;
    }
    showNotice("Email drafted. Review it before sending.", "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function sendPrefill() {
  if (!state.currentId) return;
  try {
    const payload = await api(`/api/applications/${state.currentId}/prefill`, {
      method: "POST",
      body: "{}",
    });
    window.postMessage({ type: "JOB_ASSIST_PREFILL", payload }, window.location.origin);
    await reloadDetail();
    showNotice(
      "Fill payload sent. Review every field in the open application before submitting.",
      "success",
    );
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function clearResume() {
  try {
    await api("/api/profile/resume/reset", { method: "POST", body: "{}" });
    $("resume-file").value = "";
    showNotice("Session resume cleared.", "success");
    await refreshHealth();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function showAuthError(message = "") {
  const error = $("auth-error");
  error.hidden = !message;
  error.textContent = message;
}

function setAuthMode(mode) {
  state.authMode = mode === "register" ? "register" : "login";
  const register = state.authMode === "register";
  $("auth-title").textContent = register
    ? "Create your private workspace"
    : "Sign in to your private workspace";
  $("auth-submit").textContent = register ? "Create account" : "Sign in";
  const toggle = $("auth-mode-toggle");
  toggle.textContent = register
    ? "Already have an account? Sign in"
    : "New user? Create account";
  toggle.hidden = !state.allowRegistration;
  $("auth-password").autocomplete = register ? "new-password" : "current-password";
  showAuthError();
}

function validateAuth(email, password) {
  if (
    !email ||
    email.length < 3 ||
    email.length > 320 ||
    !email.includes("@") ||
    email.startsWith("@") ||
    email.endsWith("@") ||
    /\s/.test(email)
  ) {
    return "Enter a valid email address.";
  }
  if (!password) return "Enter your password.";
  if (password.length < 8) return "Use a password of at least 8 characters.";
  return "";
}

function showAuthPanel(message = "") {
  $("auth-panel").hidden = false;
  $("workspace").hidden = true;
  $("status-strip").hidden = true;
  $("auth-password").value = "";
  setAuthMode("login");
  showAuthError(message);
}

function applySession(data) {
  state.csrf = data.csrf_token || "";
  state.accountEmail = data.account?.email || "";
  $("auth-panel").hidden = true;
  $("workspace").hidden = false;
  $("status-strip").hidden = false;
  const email = $("account-email");
  email.hidden = !state.accountEmail;
  email.textContent = state.accountEmail;
  $("logout-button").hidden = !state.accountEmail;
  showAuthError();
  refreshHealth();
  refreshHistory();
}

async function authenticate() {
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  const validation = validateAuth(email, password);
  if (validation) {
    showAuthError(validation);
    return;
  }
  const button = $("auth-submit");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = state.authMode === "register" ? "Creating..." : "Signing in...";
  try {
    const data = await api(`/api/auth/${state.authMode}`, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    $("auth-password").value = "";
    applySession(data);
  } catch (error) {
    showAuthError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) {
    state.csrf = "";
  }
  state.csrf = "";
  state.accountEmail = "";
  state.currentId = null;
  state.detail = null;
  stopPolling();
  showAuthPanel();
}

async function boot() {
  const config = await api("/api/auth/config");
  state.portalMode = Boolean(config.portal_mode);
  state.allowRegistration = Boolean(config.allow_registration);
  $("reset-resume").hidden = state.portalMode;
  $("clear-resume").hidden = !state.portalMode;
  $("history-scope").textContent = state.portalMode ? "Session only" : "Local only";
  setAuthMode("login");
  if (!state.portalMode) {
    $("status-strip").hidden = false;
    refreshHealth();
    refreshHistory();
    return;
  }
  try {
    applySession(await api("/api/auth/me"));
  } catch (_) {
    showAuthPanel();
  }
}

$("analyze-form").addEventListener("submit", submitAnalysis);
$("generate-button").addEventListener("click", generateResume);
$("edit-resume-button").addEventListener("click", toggleResumeEditor);
$("resume-editor").addEventListener("input", syncResumeDraftFromEditor);
$("revalidate-button").addEventListener("click", revalidateNotes);
$("refresh-profile").addEventListener("click", refreshProfile);
$("upload-resume").addEventListener("click", uploadResume);
$("resume-file").addEventListener("change", () => {
  if ($("resume-file").files && $("resume-file").files.length) uploadResume();
});
$("reset-resume").addEventListener("click", resetResume);
$("clear-resume").addEventListener("click", clearResume);
$("tracker-status").addEventListener("change", setTrackerStatus);
$("add-contact").addEventListener("click", saveContact);
$("draft-outreach").addEventListener("click", createOutreach);
$("prefill-form").addEventListener("click", sendPrefill);
$("auth-form").addEventListener("submit", (event) => {
  event.preventDefault();
  authenticate();
});
$("auth-mode-toggle").addEventListener("click", () => {
  setAuthMode(state.authMode === "login" ? "register" : "login");
});
$("logout-button").addEventListener("click", logout);

boot();
