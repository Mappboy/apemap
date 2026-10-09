"use strict";
const status = document.getElementById("research-status");
if (status) {
  const poll = async () => {
    try {
      const response = await fetch(status.dataset.statusUrl, { credentials: "same-origin" });
      const result = await response.json();
      if (result.status !== "running") { window.location.reload(); return; }
      window.setTimeout(poll, 2000);
    } catch (_) {
      status.textContent = "Status unavailable. Refresh to check this research job.";
    }
  };
  window.setTimeout(poll, 2000);
}
