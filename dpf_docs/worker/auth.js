// Logs into the Odoo web client once and saves the session to a
// storageState file. Playwright then reuses that file so every capture run
// starts already authenticated (recommended Playwright auth pattern).
import { chromium } from "playwright";
import { config } from "./config.js";

export async function ensureSession() {
  const browser = await chromium.launch({ headless: config.headless });
  const context = await browser.newContext({
    viewport: config.viewport,
    deviceScaleFactor: config.deviceScaleFactor,
  });
  const page = await context.newPage();

  // Odoo's login form lives at /web/login.
  await page.goto(`${config.baseUrl}/web/login`, { waitUntil: "domcontentloaded" });

  // Some deployments show a database selector first.
  const dbField = page.locator('select[name="db"], input[name="db"]');
  if (await dbField.count()) {
    try {
      await dbField.first().fill(config.database);
    } catch (_) {
      // selector variant: ignore if it is not fillable
    }
  }

  await page.fill('input[name="login"]', config.login);
  await page.fill('input[name="password"]', config.password);
  await Promise.all([
    page.waitForLoadState("networkidle"),
    page.click('button[type="submit"]'),
  ]);

  // After login we should be on /odoo (the web client root).
  await page.waitForSelector(".o_main_navbar, .o_web_client", { timeout: config.renderTimeout });

  await context.storageState({ path: config.storageStatePath });
  await browser.close();
  return config.storageStatePath;
}

// Allow "npm run login" to refresh the session manually.
if (import.meta.url === `file://${process.argv[1]}`) {
  ensureSession()
    .then((p) => {
      console.log(`Session saved to ${p}`);
      process.exit(0);
    })
    .catch((err) => {
      console.error("Login failed:", err);
      process.exit(1);
    });
}
