"use strict";

const institutionStatus = document.querySelector("[data-institution-status]");
async function waitForInstitution(url, destination) {
  try {
    const response = await fetch(url, {credentials: "same-origin", headers: {Accept: "application/json"}});
    const result = await response.json();
    if (!response.ok || result.status === "failed") throw new Error(result.error || "Institution interpretation failed. Your school draft is unchanged.");
    if (result.status === "running") { window.setTimeout(() => waitForInstitution(url, destination), 1500); return; }
    window.dispatchEvent(new CustomEvent("apemap:institution-navigation"));
    window.location.assign(destination);
  } catch (error) {
    const status = institutionStatus || document.querySelector("[data-institution-running]");
    if (status) status.textContent = error.message;
    const button = document.querySelector("[data-institution-generate]");
    if (button) button.disabled = false;
  }
}

document.addEventListener("submit", async (event) => {
  if (!event.submitter?.matches("[data-institution-generate]")) return;
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  institutionStatus.textContent = "Interpreting retained evidence…";
  try {
    const response = await fetch(button.formAction, {method: "POST", body: new FormData(event.target), credentials: "same-origin", headers: {Accept: "application/json"}});
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "Institution interpretation could not start.");
    await waitForInstitution(job.status_url, job.review_url);
  } catch (error) {
    institutionStatus.textContent = error.message;
    button.disabled = false;
  }
});

if (document.querySelector("[data-institution-running]")) {
  window.setTimeout(() => waitForInstitution(window.location.href, window.location.href), 1500);
}
