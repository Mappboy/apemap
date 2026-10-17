// Offline browser regression for quick research -> inspected batch -> exact preview.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const [url, screenshots] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({channel: "msedge", headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => new URL(route.request().url()).origin === new URL(url).origin
      ? route.continue() : route.abort());
    await page.goto(url);
    await page.locator("[data-research-disclosure] > summary").click();
    await page.locator("[data-research-start]").click();
    const proposal = page.locator("[data-manual-proposal]");
    await proposal.waitFor();
    assert.ok((await proposal.innerText()).includes("Test Historical School"));
    await proposal.scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({path: `${screenshots}/quick-manual-after-desktop.png`});
    await page.setViewportSize({width: 390, height: 844});
    await proposal.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (screenshots) await page.screenshot({path: `${screenshots}/quick-manual-after-narrow.png`});
    await proposal.getByRole("button", {name: "Save manual institution proposal"}).click();
    const definition = page.locator('[aria-label="Sourced manual institution proposal"]');
    await definition.waitFor();
    assert.ok((await definition.innerText()).includes("manual:test-historical-school"));
    assert.ok((await definition.innerText()).includes("Unknown"));
    await definition.scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({path: `${screenshots}/manual-batch-after-narrow.png`});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
      JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('*')].map(el => ({tag:el.tagName, cls:el.className, right:el.getBoundingClientRect().right, text:el.textContent.slice(0,70)})).filter(el => el.right > innerWidth+1).slice(-12))));
    await page.setViewportSize({width: 1280, height: 900});
    await definition.scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({path: `${screenshots}/manual-batch-after-desktop.png`});
    const checks = page.locator("[data-source-inspected]");
    assert.equal(await checks.count(), 3);
    for (const check of await checks.all()) await check.check();
    await page.locator('[name="institution_action"]').selectOption("create_only");
    await page.locator('[name="notes"]').fill("Inspected deterministic fixture sources; create identity only.");
    await page.getByRole("button", {name: "Record individual review", exact: true}).click();
    await page.locator("[data-stage-retain]").click();
    await page.locator("[data-stage-preview]").click();
    const apply = page.locator("[data-apply-preview]");
    await apply.waitFor();
    assert.ok((await page.locator("main").innerText()).includes("manual:test-historical-school"));
    await apply.click();
    assert.ok((await page.locator("main").innerText()).includes("applied"));
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
