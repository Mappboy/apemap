// Offline browser regression: retained evidence -> editable definition -> school draft.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const [url, screenshots] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({channel: "msedge", headless: true});
  try {
    const errors = [];
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    page.setDefaultTimeout(15000);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => new URL(route.request().url()).origin === new URL(url).origin ? route.continue() : route.abort());
    await page.goto(url);
    const shortcuts = page.locator('[aria-label="Manual institution shortcuts"]');
    await shortcuts.scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({path: `${screenshots}/institution-shortcuts-before-desktop.png`});
    await page.getByText("Optional source and notes", {exact: true}).click();
    await page.locator('[name="notes"]').fill("Browser school notes");
    await page.locator('[name="reviewer"]').fill("Browser Reviewer");
    await page.locator('[name="field_relationship_type"]').selectOption("direct");
    await page.locator("[data-institution-generate]").click();
    await page.locator('[name="institution_generation_token"]').first().waitFor({state: "attached"});
    assert.equal(await page.locator('[name="field_school_name"]').inputValue(), "Historical School");
    assert.equal(await page.locator('[name="field_sector"]').inputValue(), "");
    assert.ok((await page.locator("main").innerText()).includes("Closure day differs"));
    await page.locator("#review-decision").scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({path: `${screenshots}/institution-draft-after-desktop.png`});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (screenshots) await page.screenshot({path: `${screenshots}/institution-draft-after-narrow.png`});
    await page.locator('[name="institution_sources_confirmed"]').check();
    await page.getByRole("button", {name: "Validate and preview", exact: true}).click();
    await page.getByRole("button", {name: "Save this decision", exact: true}).waitFor();
    await page.locator("#preview > details.technical-details > summary").click();
    assert.ok((await page.locator("#preview").innerText()).includes("institution_draft_provenance"));
    await page.getByRole("button", {name: "Save this decision", exact: true}).click();
    assert.equal(await page.locator('[name="notes"]').inputValue(), "Browser school notes");
    assert.equal(await page.locator('[name="reviewer"]').inputValue(), "Browser Reviewer");
    assert.equal(await page.locator('[name="field_relationship_type"]').inputValue(), "direct");
    assert.equal(await page.locator('[name="field_institution_ref"]').inputValue(), "manual:historical-school");
    assert.ok((await page.locator("[data-selected-school]").innerText()).includes("manual:historical-school"));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (screenshots) await page.screenshot({path: `${screenshots}/institution-return-after-narrow.png`});
    const nojs = await browser.newPage({javaScriptEnabled: false, viewport: {width: 1280, height: 900}});
    await nojs.goto(url);
    await nojs.getByText("Optional source and notes", {exact: true}).click();
    await nojs.locator('[name="notes"]').fill("No-script school notes");
    await nojs.getByRole("button", {name: "Use this institution", exact: true}).click();
    assert.equal(await nojs.locator('[name="field_institution_ref"]').inputValue(), "manual:geilston-bay-high-school");
    assert.equal(await nojs.locator('[name="notes"]').inputValue(), "No-script school notes");
    if (screenshots) await nojs.screenshot({path: `${screenshots}/institution-reuse-nojs-desktop.png`});
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
