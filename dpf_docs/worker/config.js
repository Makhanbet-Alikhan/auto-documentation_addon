// Centralised configuration for the screenshot worker.
// All values can be overridden through environment variables so the worker
// can run in CI / a container without editing source.

export const config = {
  // Base URL of the live Odoo instance the worker drives.
  baseUrl: process.env.ODOO_BASE_URL || "http://localhost:8069",

  // Database + credentials used to log in once and reuse the session.
  database: process.env.ODOO_DB || "odoo",
  login: process.env.ODOO_LOGIN || "admin",
  password: process.env.ODOO_PASSWORD || "admin",

  // Shared secret that must match dpf_docs.worker_token in Odoo.
  workerToken: process.env.DOC_WORKER_TOKEN || "change-me-please",

  // Which generation run to process.
  generationId: process.env.DOC_GENERATION_ID || "1",

  // Persisted browser session (cookies) so we log in only once.
  storageStatePath: process.env.DOC_STORAGE_STATE || "./storage-state.json",

  // Selectors are configurable because they may shift between Odoo versions.
  readySelector: process.env.DOC_READY_SELECTOR || ".o_content",
  captureSelector: process.env.DOC_CAPTURE_SELECTOR || ".o_action_manager",

  // Rendering settings for crisp, reproducible screenshots.
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2,

  // Milliseconds to wait for the Owl view to settle after navigation.
  renderTimeout: Number(process.env.DOC_RENDER_TIMEOUT || 15000),

  // Run headless by default; set DOC_HEADLESS=0 to watch it work.
  headless: process.env.DOC_HEADLESS !== "0",
};
