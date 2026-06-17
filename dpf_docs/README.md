# DPF Docs — Module Documentation Generator (Odoo 19)

Automatically generates documentation for any installed Odoo module:

- **Module description** — from the manifest (`summary` / `description`) plus the
  primary model docstring.
- **Menu tree** — the full `ir.ui.menu` hierarchy owned by the module.
- **Per-menu screenshots** — captured from the live web client by an external
  Playwright worker and stored as `ir.attachment`.
- **Per-menu description** — deterministic text built from model metadata, or an
  optional Vision-LLM caption.
- **Field tables** — `string`, `help`, `type`, `required` via `fields_get()`,
  merged with inline `#` comments recovered from the source code.

Output formats: **Markdown**, **QWeb PDF**, **Word (.docx)**, and the in-app viewer.

The Word export bundles every documented module into one `.docx` containing the
descriptions, the menu tree with embedded screenshots, and the model field
tables. Requires `python-docx` on the server (`pip install python-docx`).

---

## Why two processes?

The Odoo 19 backend UI is an **Owl 2 single-page app** that only renders in a
real browser. The Python addon therefore handles everything it *can* do on the
server (ORM introspection + source parsing + text composition), while a separate
**Node + Playwright** worker logs into the live client and takes the screenshots.

```
Odoo (Python)                         Node worker (Playwright)
  introspector  ─┐                       login (storageState)
  source_parser ─┼─► doc.spec  ──HTTP──►  goto /odoo/action-<id>
  text_composer ─┘                        screenshot .o_action_manager
        ▲                                  (optional Vision-LLM caption)
        └──────────  ir.attachment  ◄──HTTP── POST /doc_gen/upload
```

---

## Module layout

| Path | Purpose |
|------|---------|
| `models/doc_word_export.py` | Builds the `.docx` (screenshots + field tables) via python-docx. |
| `services/ast_extractor.py` | Pure-python AST + `tokenize` parser (docstrings & comments). |
| `services/text_composer.py` | Pure-python text composition (deterministic + LLM hook). |
| `models/introspector.py` | ORM introspection (`ir.ui.menu`, actions, `fields_get`). |
| `models/source_parser.py` | Resolves module path and parses every `.py` file. |
| `models/doc_generation.py` | Orchestrator: collect → persist → spec → render. |
| `models/doc_module.py` | Stored documentation for one module. |
| `models/doc_menu.py` | One menu / screen (+ screenshot + caption). |
| `models/doc_model_info.py` | One documented model (+ field table JSON). |
| `controllers/doc_api.py` | `/doc_gen/spec/<id>` and `/doc_gen/upload`. |
| `report/` | QWeb PDF template + report action. |
| `worker/` | Standalone Playwright worker (not loaded by Odoo). |

Each Python class lives in its own file, per the requested architecture.

---

## Usage

### 1. Install the addon

Copy `dpf_docs` into your addons path, update the apps list, and
install it. Open **Auto Documentation → Generations**.

### 2. Collect texts

Create a generation, pick modules from the **Installed Modules** dropdown (or
type extra technical names in the optional field), and press **1. Collect
Texts**. This creates the `doc.module` / `doc.menu` / `doc.model.info` records
and composes all text.

**How does it access other modules' code?** Automatically. The addon documents
modules that are already installed in the same Odoo database — you never upload
any files. It resolves what belongs to a module through `ir.model.data`, and
reads the source `.py` files straight from the server's addons path
(`get_module_path`). You only choose which modules to document.

### 3. Capture screenshots (worker)

Configure Odoo first (Settings → Technical → System Parameters), especially:

- `dpf_docs.base_url`
- `dpf_docs.worker_token` (set a real secret!)

Then run the worker:

```bash
cd worker
npm install                 # installs Playwright + Chromium
export ODOO_BASE_URL="http://localhost:8069"
export ODOO_DB="your_db"
export ODOO_LOGIN="admin"
export ODOO_PASSWORD="admin"
export DOC_WORKER_TOKEN="the-same-secret-as-in-odoo"
export DOC_GENERATION_ID="1"   # the generation record id
npm run login                  # one-time: saves storage-state.json
npm run capture                # captures + uploads every screenshot
```

To enable AI captions, also set `DOC_LLM_ENDPOINT`, `DOC_LLM_API_KEY`,
`DOC_LLM_MODEL` and tick **Use LLM Captions** on the generation.

### 4. Render

Back in Odoo press **3. Render Markdown**, **Print PDF**, and/or **Download
Word** (one `.docx` covering all selected modules, with screenshots embedded).

---

## Operational notes

- **Run as admin.** `fields_get()` hides group-restricted fields; generate under
  a high-privilege user for complete documentation.
- **Use demo data.** Screenshots of empty screens are not useful — point the
  worker at a database with a predictable demo dataset.
- **Version-proofing.** UI selectors (`.o_content`, `.o_action_manager`) and the
  action URL pattern are stored as config params / env vars, so an upgrade only
  needs a config change, not a code change.
- **Worker placement.** Keep the browser out of the Odoo container; run the
  worker in CI or a dedicated container and drive it via webhook / `queue_job`.
  The optional `ir.cron` only surfaces pending work, it does not launch a browser.

## License

LGPL-3.
