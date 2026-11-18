window.addEventListener("message", (event) => {
  if (event.source !== window || event.origin !== window.location.origin) return;
  const data = event.data;
  if (!data || data.type !== "JOB_ASSIST_PREFILL" || !data.payload) return;
  chrome.runtime.sendMessage({ type: "JOB_ASSIST_PAYLOAD", payload: data.payload });
});
