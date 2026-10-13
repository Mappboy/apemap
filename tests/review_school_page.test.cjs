// Actual server-rendered page checks; optional Playwright + installed Edge.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const [url, screenshots] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({channel: "msedge", headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 720}});
    page.setDefaultTimeout(10000);
    page.on("pageerror", error => console.error(error));
    // Never visit evidence providers or any other external origin.
    await page.route("**/*", route => new URL(route.request().url()).origin === new URL(url).origin
      ? route.continue() : route.abort());
    await page.goto(url);
    await page.locator('[data-use-candidate][value="acara:123"]').click();
    await page.waitForFunction(() => document.querySelector('[data-inline-evidence]').textContent.includes("History confirms the Hobart campus"));
    await page.evaluate(() => scrollTo(0, 0));
    const brief = page.locator('[data-inline-evidence]');
    assert.ok(await brief.isVisible());
    assert.equal(await brief.locator(':scope > details').evaluate(el => el.open), false);
    assert.ok((await brief.innerText()).includes("Unknown:"));
    for (const locator of [brief, page.locator('[data-workspace-action="preview"]')]) {
      const box = await locator.boundingBox();
      assert.ok(box.y >= 0 && box.y + box.height <= 720, JSON.stringify(box));
    }
    if (screenshots) await page.screenshot({path: `${screenshots}/school-desktop.png`});
    await page.locator('[data-use-candidate][value="acara:456"]').click();
    await page.waitForFunction(() => document.querySelector('[data-inline-evidence]').textContent.includes("Melbourne campus conflicts"));
    assert.ok(!(await brief.innerText()).includes("History confirms the Hobart campus"));
    await page.locator('a[href="#individual-school-resolution"]').click();
    const assertions = page.locator('#individual-school-resolution a');
    assert.ok(await assertions.count() >= 2);
    assert.ok((await assertions.allTextContents()).some(text => text.includes("Other Member")));
    await assertions.last().focus();
    assert.equal(await assertions.last().evaluate(el => el === document.activeElement), true);

    await page.setViewportSize({width: 390, height: 844});
    await page.goto(url);
    await page.locator('[data-use-candidate][value="acara:123"]').click();
    await page.waitForFunction(() => document.querySelector('[data-inline-evidence]').textContent.includes("History confirms the Hobart campus"));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const panel = page.locator('.workspace-decision-panel');
    assert.equal(await panel.evaluate(el => getComputedStyle(el).position), "static");
    for (const locator of [panel, page.locator('[data-primary-comparison]')]) {
      const box = await locator.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= 390, JSON.stringify(box));
    }
    await page.locator('[data-workspace-action="preview"]').scrollIntoViewIfNeeded();
    assert.ok(await page.locator('[data-workspace-action="preview"]').isVisible());
    if (screenshots) await page.screenshot({path: `${screenshots}/school-narrow.png`, fullPage: true});
    console.log("Desktop recommendation/actions, candidate refresh, keyboard split links and actual 390px page passed.");
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
