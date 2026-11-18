chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === "JOB_ASSIST_PAYLOAD") {
    chrome.storage.session.set({ jobAssistPrefill: message.payload }, () => {
      chrome.action.setBadgeText({ text: "READY" });
      chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
      sendResponse({ ok: true });
    });
    return true;
  }
  if (message && message.type === "JOB_ASSIST_FILLED") {
    chrome.action.setBadgeText({ text: "" });
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  const stored = await chrome.storage.session.get("jobAssistPrefill");
  const payload = stored.jobAssistPrefill;
  if (!payload) {
    chrome.action.setBadgeText({ text: "" });
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "JOB_ASSIST_FILL", payload });
  } catch (_) {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["target.js"] });
    await chrome.tabs.sendMessage(tab.id, { type: "JOB_ASSIST_FILL", payload });
  }
});
