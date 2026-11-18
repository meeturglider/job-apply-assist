const FIELD_ALIASES = {
  name: ["full name", "first and last name", "candidate name", "applicant name", "name"],
  email: ["email address", "email", "e-mail"],
  phone: ["phone number", "phone", "mobile number", "mobile"],
  linkedin: ["linkedin url", "linkedin profile", "linkedin"],
  github: ["github url", "github profile", "github"],
  portfolio: ["portfolio url", "portfolio", "website", "personal website"],
  role: ["job title", "position title", "desired role", "role", "position"],
  company: ["company name", "company", "employer"],
  location: ["current location", "location", "city"],
  experience_years: ["years of experience", "experience years", "years experience"],
  skills: ["technical skills", "skills"],
  resume_markdown: ["resume markdown", "full resume", "resume text"],
};

function normalize(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function payloadValues(payload) {
  const fields = payload.fields || {};
  const values = { ...fields };
  values.skills = Array.isArray(fields.skills) ? fields.skills.join(", ") : String(fields.skills || "");
  values.experience_years = fields.experience_years ? String(fields.experience_years) : "";
  values.resume_markdown = String(payload.resume_markdown || "");
  return values;
}

function candidateText(element) {
  const parts = [];
  if (element.labels) {
    element.labels.forEach((label) => parts.push(label.textContent || ""));
  }
  ["name", "id", "placeholder", "aria-label", "autocomplete", "title"].forEach((attribute) => {
    parts.push(element.getAttribute(attribute) || "");
  });
  return normalize(parts.join(" "));
}

function bestValue(element, values) {
  const text = candidateText(element);
  let best = null;
  let bestLength = 0;
  Object.entries(FIELD_ALIASES).forEach(([key, aliases]) => {
    const value = String(values[key] || "").trim();
    if (!value) return;
    aliases.forEach((alias) => {
      if (text.includes(normalize(alias)) && normalize(alias).length > bestLength) {
        best = value;
        bestLength = normalize(alias).length;
      }
    });
  });
  return best;
}

function allowedElement(element) {
  if (!element || element.disabled || element.readOnly) return false;
  if (element.tagName === "TEXTAREA") return true;
  if (element.tagName === "SELECT") return true;
  if (element.tagName !== "INPUT") return false;
  const type = normalize(element.type || "text");
  return ["text", "email", "tel", "url", "search", "number"].includes(type);
}

function setValue(element, value) {
  if (element instanceof HTMLSelectElement) {
    const target = normalize(value);
    const option = Array.from(element.options).find((item) => {
      const optionText = normalize(item.textContent || "");
      const optionValue = normalize(item.value);
      return optionText === target || optionValue === target || optionText.includes(target);
    });
    if (!option) return false;
    element.value = option.value;
  } else {
    element.value = value;
  }
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function fillFields(payload) {
  const values = payloadValues(payload);
  const elements = Array.from(
    document.querySelectorAll("input, textarea, select"),
  ).filter(allowedElement);
  let count = 0;
  elements.forEach((element) => {
    if (String(element.value || "").trim()) return;
    const value = bestValue(element, values);
    if (value && setValue(element, value)) count += 1;
  });
  return count;
}

function showToast(count) {
  const previous = document.getElementById("job-assist-prefill-toast");
  if (previous) previous.remove();
  const toast = document.createElement("div");
  toast.id = "job-assist-prefill-toast";
  toast.textContent = `Job Assist filled ${count} fields. Review before submitting.`;
  toast.style.cssText =
    "position:fixed;right:20px;bottom:20px;z-index:2147483647;padding:12px 16px;" +
    "border-radius:8px;background:#0f172a;color:#e2e8f0;font:13px sans-serif;" +
    "box-shadow:0 8px 28px rgba(0,0,0,.35)";
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 6000);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "JOB_ASSIST_FILL") return false;
  const count = fillFields(message.payload || {});
  showToast(count);
  chrome.runtime.sendMessage({ type: "JOB_ASSIST_FILLED" });
  sendResponse({ ok: true, count });
  return true;
});
