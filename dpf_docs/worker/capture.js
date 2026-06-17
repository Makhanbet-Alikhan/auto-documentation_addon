// Main screenshot runner.
//
// Flow:
//   1. ensure an authenticated session (storageState).
//   2. fetch the generation spec from Odoo (/doc_gen/spec/<id>).
//   3. for every task: navigate to the action URL, wait for the Owl view to
//      render, capture the content area, optionally caption it.
//   4. POST each screenshot back to Odoo (/doc_gen/upload).
//
// The web client is an Owl 2 single-page app, so screenshots can only be taken
// in a real browser — hence this worker lives outside the Odoo python process.
import fs from "fs";
import { chromium } from "playwright";
import { config } from "./config.js";
import { ensureSession } from "./auth.js";
import { annotate } from "./annotate.js";

async function fetchSpec() {
  const url = `${config.baseUrl}/doc_gen/spec/${config.generationId}`;
  const res = await fetch(url, { headers: cookieHeaderFromStorage() });
  if (!res.ok) {
    throw new Error(`Could not fetch spec: HTTP ${res.status}`);
  }
  return res.json();
}

// Build a Cookie header from the saved storageState so the auth='user'
// spec endpoint accepts our request.
function cookieHeaderFromStorage() {
  if (!fs.existsSync(config.storageStatePath)) return {};
  const state = JSON.parse(fs.readFileSync(config.storageStatePath, "utf-8"));
  const cookies = (state.cookies || [])
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
  return cookies ? { Cookie: cookies } : {};
}

async function uploadScreenshot(payload) {
  const res = await fetch(`${config.baseUrl}/doc_gen/upload`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Doc-Token": config.workerToken,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    console.warn(`Upload failed for menu ${payload.doc_menu_id}: HTTP ${res.status}`);
  }
  return res.ok;
}

async function captureTask(page, task) {
  // Navigate to the stable client URL, e.g. /odoo/action-42.
  await page.goto(`${config.baseUrl}${task.web_url}`, {
    waitUntil: "domcontentloaded",
  });

  // Wait until the Owl view container is visible, then for network to settle.
  await page.waitForSelector(config.readySelector, {
    state: "visible",
    timeout: config.renderTimeout,
  });
  await page.waitForLoadState("networkidle").catch(() => {});

  // Capture the content area (without the top navbar) for a clean image.
  const target = page.locator(config.captureSelector).first();
  const exists = await target.count();
  const buffer = exists
    ? await target.screenshot({ type: "png" })
    : await page.screenshot({ type: "png", fullPage: false });

  // Optional AI caption from screen context.
  const contextText =
    `Odoo screen "${task.name}" showing model ${task.res_model} ` +
    `in ${(task.view_modes || []).join(", ")} view.`;
  const caption = await annotate(buffer, contextText);

  return {
    doc_menu_id: task.doc_menu_id,
    filename: `menu_${task.doc_menu_id}.png`,
    image_b64: buffer.toString("base64"),
    ...(caption ? { caption } : {}),
  };
}

async function main() {
  // Make sure we have a logged-in session before doing anything else.
  if (!fs.existsSync(config.storageStatePath)) {
    console.log("No session found; logging in...");
    await ensureSession();
  }

  const spec = await fetchSpec();
  const browser = await chromium.launch({ headless: config.headless });
  const context = await browser.newContext({
    storageState: config.storageStatePath,
    viewport: config.viewport,
    deviceScaleFactor: config.deviceScaleFactor,
  });
  const page = await context.newPage();

  let captured = 0;
  let failed = 0;
  for (const mod of spec.modules || []) {
    console.log(`Module ${mod.technical_name}: ${mod.tasks.length} task(s)`);
    for (const task of mod.tasks) {
      try {
        const payload = await captureTask(page, task);
        await uploadScreenshot(payload);
        captured += 1;
        console.log(`  captured: ${task.name}`);
      } catch (err) {
        failed += 1;
        console.warn(`  failed: ${task.name} -> ${err.message}`);
        await uploadScreenshot({
          doc_menu_id: task.doc_menu_id,
          error: err.message,
        });
      }
    }
  }

  await browser.close();
  console.log(`Done. captured=${captured} failed=${failed}`);
}

main().catch((err) => {
  console.error("Worker crashed:", err);
  process.exit(1);
});
