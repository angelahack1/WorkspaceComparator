# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository. Read this before making changes.

---

## 1. What this project is

**Workspace Comparator** is a **local, single-user Django web tool** that compares two source-code project directories ("left" and "right") and works out which files *correspond* to each other — even when the projects have been restructured, renamed, or migrated between build systems (e.g. a plain Java workspace vs. its Maven-ified successor).

It is designed for **codebase archaeology / migration verification**: "we refactored/migrated project A into project B — which files are the same, which changed, and which have no counterpart?"

The canonical example baked into the tests is comparing:
- `D:/Proyectos/Workspaces/WorkspaceMAE` (original)
- `D:/Proyectos/Workspaces/WorkspaceMAEMaven` (migrated)

### Two screens

1. **Directory comparison** (`/`) — pick two folders, get a BeyondCompare-style two-panel table: matched files in the middle-joined green rows, unmatched files in red rows. Each matched row shows a **content-status pill** (`==` identical, `~=` minor, `!=` different) and, for AI-matched pairs, an "AI-Matched" label.
2. **File comparison** (`/file-compare/`) — double-click any row to open a full **Beyond Compare 5-style** side-by-side diff viewer: *content-aligned* rows (corresponding lines face each other even when line numbers drift, with diagonal-hatched gap placeholders), word-level intra-line change highlighting, a collapsible **Context** view, an overview **minimap**, section navigation, syntax coloring, and a single-file view for unmatched files. The visual/behavioral reference is `BeyondCompareCoolComparission.jpg` in the repo root.

### It is NOT
- Not multi-user, not authenticated, not deployed to production. `DEBUG=True`, `ALLOWED_HOSTS=['*']`, a hard-coded insecure `SECRET_KEY`, and `@csrf_exempt` on the compare endpoint all confirm this is a **localhost developer utility**. Do not treat it as internet-facing.
- Not backed by a database. `DATABASES = {}` — there are **no models, no migrations, no admin, no ORM**. Do not run `migrate` or add models unless you are deliberately introducing persistence.

---

## 2. Tech stack & environment

| Thing | Value |
|---|---|
| Language | Python **3.12** (dev machine: 3.12.10) |
| Framework | Django **5.2** (`requirements.txt` pins `>=4.2,<6.0`) |
| HTTP client (for LLM) | `requests` |
| LLM backend | **Ollama** at `http://127.0.0.1:11434`, model `glm-5.2:cloud` |
| Browser tests | **Playwright** (Chromium) — *not* in `requirements.txt`, installed separately |
| Frontend | Vanilla JS + inline CSS. **No build step, no npm, no framework, no bundler.** |
| Target OS | **Windows** (drive-letter browsing, `D:\` paths). Has Unix fallbacks but is exercised on Windows. |
| Database | None |

`requirements.txt` only lists `django` and `requests`. Playwright must be installed manually to run the browser tests (it *is* installed on the current dev machine).

There is **no virtual environment directory** checked in and none required by the tooling; the dev machine uses a system/global Python. If you create one, don't commit it.

---

## 3. Repository layout

```
WorkspaceComparator/
├── manage.py                     # Standard Django entry point
├── requirements.txt              # django, requests  (NO playwright)
├── CLAUDE.md                     # This file
│
├── workspace_comparator/         # Django PROJECT (config only)
│   ├── settings.py               # No DB, DEBUG=True, 50 MB upload cap
│   ├── urls.py                   # includes comparator.urls
│   ├── wsgi.py / asgi.py         # Standard, unused in dev
│   └── __init__.py
│
├── comparator/                   # The one Django APP
│   ├── apps.py                   # ComparatorConfig
│   ├── urls.py                   # 5 routes (see §5)
│   ├── views.py                  # Thin HTTP layer over services/
│   ├── services/                 # ★ ALL THE REAL LOGIC LIVES HERE ★
│   │   ├── correspondence.py     #   Orchestrator: the 4-phase matching engine
│   │   ├── file_scanner.py       #   Recursive source-file discovery
│   │   ├── deterministic.py      #   Language-aware similarity scoring
│   │   ├── llm_comparator.py     #   Ollama fallback arbiter
│   │   └── file_diff.py          #   ★ Aligned-diff engine (BC-style row model, see §4b/§5) ★
│   ├── templates/comparator/
│   │   ├── index.html            # ★ Main page — SELF-CONTAINED (inline CSS+JS) ★
│   │   └── file_compare.html     # ★ BC-style diff viewer — SELF-CONTAINED (inline CSS+JS) ★
│   └── static/comparator/        # ⚠ DEAD CODE — see §8. NOT loaded by the templates.
│       ├── css/styles.css
│       └── js/app.js
│
├── test_browser.py               # Headless Playwright smoke test (port 9876)
├── AutomatedTestsStarter.py      # Visible Playwright full test (port 9877, slow-mo)
├── test_screenshots/             # Output PNGs from the test scripts (committed artifacts)
├── BeyondCompareCoolComparission.jpg  # Design reference: the BC 5 look the diff viewer imitates
└── .claude/settings.local.json   # Pre-approved Bash permissions
```

**Mental model:** `views.py` is a thin adapter. The interesting engineering is entirely in `comparator/services/`. When asked to change *behavior* (matching, scoring, diffing), you almost always edit `services/`, not views or templates.

---

## 4. How the matching engine works (the core algorithm)

This is the heart of the app. Entry point: `comparator/services/correspondence.py :: find_correspondences(left_dir, right_dir)`.

### Step 0 — Scan (`file_scanner.py`)
`scan_directory()` walks each tree with `os.walk`, keeping only files whose extension is in `SOURCE_EXTENSIONS` (C family, Java, Rust, C#, Go, Kotlin/Scala, Swift/Obj-C, Python, JS/TS, plus config: `.gradle .xml .properties .yaml .yml .toml`). It **prunes** noise dirs (`SKIP_DIRS`: `node_modules`, `.git`, `target`, `build`, `dist`, `bin`, `obj`, `.idea`, `.vscode`, plus Python environments `site-packages`/`venv`/`env`/`envs`/`virtualenv` — an embedded runtime brings thousands of third-party `__init__.py` files that explode Phase 2) and any dotfile dir. `scan_directory(root, exclusions=None)` also accepts **user exclusions** (`{'files': [...], 'dirs': [...]}` fnmatch wildcards from the UI's 🚫 Exclusions dialog): matching directories are pruned from the walk (contents never scanned), matching files skipped; patterns containing `/` match the forward-slash relative path, plain patterns match the basename anywhere. Each hit becomes a `FileInfo(filename, relative_dir, full_path, extension)`. Relative dirs are normalized to forward slashes; root-level dir is `''`.

### The 4 phases
Matching is **greedy and order-dependent**. Files start "free"; once matched, both sides are removed from the free pool. Phases run in sequence, each consuming from what the previous left behind:

- **Phase 1 — Exact path match.** Same filename **and** same `relative_dir`. Instant match, `similarity=100`, `match_type='exact_path'`. Highest confidence.
- **Phase 2 — Same filename, different directory.** For each remaining left file, *all* right files with the same filename are scored deterministically first (sorted best-first). If any has `confidence=='high'` and `similarity > 85` → accept as `deterministic`, no LLM. Otherwise **ask the LLM** for at most the top `MAX_LLM_PER_FILE` (3) candidates whose deterministic sim clears the `LLM_MIN_SIM` (15) noise floor; a score `>= 70` → accept as `llm_verified`. If the LLM is unreachable (returns `-1`) but deterministic `> 40`, fall back to accepting as `deterministic`. The bound matters: without it, boilerplate names (`__init__.py`, `index.js`) with hundreds of same-named candidates trigger one LLM round-trip *each* and a compare never finishes.
- **Phase 3 — Similar filename (fuzzy), same extension.** For still-unmatched files, compare filenames with `compute_filename_similarity` (SequenceMatcher on the base name). Require `>= 0.70` similarity and matching extension. Score = `filename_sim*30 + content_sim*0.70` (a blended heuristic). Candidates are collected and sorted by combined score first; a high-confidence deterministic candidate auto-accepts, else medium/low candidates with filename sim `> 0.80` escalate to the LLM under the same `MAX_LLM_PER_FILE` / `LLM_MIN_SIM` bounds.
- **Phase 3b — Renamed files (content-only).** Leftovers whose filenames are too different for Phase 3 are swept purely by content: same extension + deterministic similarity `>= CONTENT_SIM_THRESHOLD` (60) → match with `match_type='content'` (the UI labels these rows "Renamed"). A cheap length-ratio bound prunes the O(L·R) sweep before any expensive comparison. No LLM involvement.
- **Phase 4 — Leftovers.** Everything still free is reported as `unmatched_left` / `unmatched_right`.

Results (`ComparisonResult`) carry `matched`, `unmatched_left`, `unmatched_right`, and a `stats` dict (`total_left/right`, `exact_path_matches`, `deterministic_matches`, `llm_matches`, `llm_calls`). Sorting anchors on the **left side** (the user's original project): primary key = left filename, secondary key = left directory — the right file follows its partner even when its own name is completely different. Unmatched lists sort by (filename, directory) on their own side.

### Tunable thresholds (top of `correspondence.py`)
```python
DETERMINISTIC_HIGH      = 85.0   # above this + 'high' confidence -> auto-match
DETERMINISTIC_UNCERTAIN = 40.0   # below this with same name -> still worth LLM / fallback
LLM_MATCH_THRESHOLD     = 70     # LLM must return >= this to accept a match
FILENAME_SIM_THRESHOLD  = 0.70   # minimum filename similarity to consider in Phase 3
LLM_FAILURE_LIMIT       = 3      # consecutive LLM failures before the _LLMGate breaker trips
MAX_LLM_PER_FILE        = 3      # LLM-arbitrate only the top-N candidates per left file
LLM_MIN_SIM             = 15.0   # noise floor: don't LLM-arbitrate near-zero deterministic scores
CONTENT_SIM_THRESHOLD   = 60.0   # Phase 3b: content-only match threshold for renamed files
```
If someone reports "too many / too few matches," these constants are the first knobs to turn. The last four (`LLM_FAILURE_LIMIT`, `MAX_LLM_PER_FILE`, `LLM_MIN_SIM`, `CONTENT_SIM_THRESHOLD`) are **defaults only**: the UI's ⚙ Engine Settings dialog (persisted in `localStorage`, disabled while a comparison runs) sends per-request overrides in the compare body, resolved and clamped by `resolve_settings()` / `SETTING_BOUNDS`.

### Deterministic scoring (`deterministic.py`)
`compute_similarity(content1, content2, ext) -> (pct, confidence)`:
1. Strip comments (language-aware: C-style `//` `/* */` for the C family incl. Java/JS/Rust/Go/etc.; `#` and triple-quoted strings for Python).
2. Replace string literals with `""` to focus on structure.
3. Normalize whitespace (collapse runs, drop blank lines).
4. Token-level similarity via `difflib.SequenceMatcher` (×100).
5. Extract **structural identifiers** (class/function/struct/impl names via per-language regexes), drop a `_NOISE` set (`main`, `this`, `get`, `set`, `toString`, keywords…), and compute Jaccard overlap.
6. Blend: `0.60 * token_sim + 0.40 * identifier_sim` (falls back to token-only if no identifiers). Confidence: `>85` high, `>40` medium, else low.

`compute_content_status(content1, content2, ext) -> 'identical' | 'minor' | 'different'` drives the `==`/`~=`/`!=` symbols in the UI: raw-equal → `identical`; equal after comment/string/whitespace normalization → `minor`; else `different`.

`compute_filename_similarity(a, b)` → SequenceMatcher ratio on the lowercased base names (extension removed).

### LLM arbiter (`llm_comparator.py`)
Only invoked for genuinely ambiguous Phase 2/3 cases (to keep latency down). `compare_with_llm(name_a, content_a, name_b, content_b) -> int` sends a carefully engineered prompt to Ollama's `/api/generate` (model `glm-5.2:cloud`, `temperature=0.05`, `num_predict=256`, `think: false`, 120 s timeout) asking for **a single integer 0–100** (correspondence %). `glm-5.2:cloud` is a *thinking* model: the top-level `"think": false` parameter is what stops it burning the token budget on reasoning (with a tight `num_predict` that produced empty responses → "LLM returned non-numeric answer" on every call). If the server rejects the `think` param (HTTP 400, older Ollama), the request is retried once without it and the choice is remembered (`_send_think_param`). Answer parsing (`_parse_score`) strips leaked `<think>…</think>` blocks, falls back to the separate `thinking` response field, normalizes "85/100"-style replies, and takes the **last** number when prose surrounds it. The prompt explicitly tells the model to *ignore* migration-expected differences (package/namespace moves, import changes, `javax`↔`jakarta`, logging-framework swaps, semantic-preserving renames) and to judge functional purpose, core logic, API surface, naming signals, and architectural role.

- Files are truncated to `MAX_FILE_CHARS = 6000` via `_smart_truncate` (keeps 40% head / 35% middle / 25% tail with `/* ... truncated ... */` markers) so headers, core logic, and closings all survive.
- Returns **`-1`** on any failure (connection refused, timeout, non-numeric answer). Callers treat `-1` as "LLM unavailable" and fall back to deterministic scoring. **The app degrades gracefully with no Ollama running** — you just lose the AI-arbitrated matches.
- **Circuit breaker** (`_LLMGate` in `correspondence.py`, `LLM_FAILURE_LIMIT = 3`): all Phase 2/3 LLM escalations go through the gate. It probes `is_ollama_available()` once per run, and after 3 *consecutive* `-1` results it disables LLM arbitration for the rest of the run (short-circuiting to `-1`), so a broken backend can no longer turn a large comparison into hundreds of doomed 120 s calls that never finish. A success resets the failure counter. `stats['llm_calls']` counts only real HTTP attempts.
- The prompt begins with `/no_think` (a directive for thinking-capable models to skip visible reasoning).

**Encoding:** all file reads in the services use a fallback chain `utf-8 → utf-8-sig → latin-1 → cp1252`, returning `''` if all fail. `_read_file` is duplicated in `correspondence.py` and `file_diff.py` — if you change encoding handling, change both.

---

## 4b. The diff alignment engine (`file_diff.py`) — the second core algorithm

The file-compare view is powered by its own algorithm, separate from the directory-matching engine. Entry point: `compute_file_diff(left_path, right_path)`. It intentionally imitates Beyond Compare's alignment behavior.

**Pipeline:**
1. Read both files (same encoding-fallback `_read_file`), split into lines.
2. `difflib.SequenceMatcher(autojunk=False)` over the *line lists* produces coarse opcodes.
3. Opcodes are converted into an **aligned row model** (see §5 for the JSON shape): `equal` → `eq` rows; `delete`/`insert` → one-sided `del`/`add` rows (the other panel renders a hatched gap); `replace` → **`_align_replace()`**, the interesting part.
4. `_align_replace(ll, rl, i1, i2, j1, j2)` recursively finds the single **best-matching line pair** in the block (`SequenceMatcher.ratio()`, pruned by `real_quick_ratio`/`quick_ratio` upper bounds), anchors on it as a `mod` row, and recurses into the sub-blocks on either side. This is what makes *corresponding lines face each other* regardless of line-number drift.
5. Each `mod` row gets **word-level intra-line segments** from `_inline_segments()`: both lines are tokenized with `_TOKEN_RE` (`\w+|\s+|[^\w\s]`), diffed with SequenceMatcher, and emitted as `[text, changed]` pairs — the UI highlights only the changed words.

**Tunables (top of `file_diff.py`):**
```python
PAIR_THRESHOLD = 0.5    # min ratio for two lines to be considered "the same line, edited"
MAX_PAIR_AREA  = 2500   # L*R above this -> cheap sequential pairing (perf guard)
```

**Fallback behavior (`_sequential`)**: when a block is too big (`L*R > MAX_PAIR_AREA`) or no pair clears `PAIR_THRESHOLD`, lines are zipped in order — BUT pairs with almost nothing in common (ratio `< 0.3`, e.g. a code line facing a blank line) are **not** forced into a `mod` pair; they are emitted as grouped del-run + add-run, which is what BC does. Don't reintroduce forced pairing — it produces ugly "everything changed" rows.

**Minor classification**: a `mod` pair is minor (`m:1`) when the lines are equal after removing *all* whitespace (`_is_ws_only`, i.e. `''.join(a.split()) == ''.join(b.split())` — catches internal whitespace changes too); a `del`/`add` is minor when the line is blank. Minor rows carry no `ls`/`rs` segments.

**Complexity note**: best-pair search is O(L·R) ratio computations per replace block, bounded by `MAX_PAIR_AREA`; recursion depth is bounded by `min(L,R) ≤ 50` under that guard. Typical source diffs are fast; pathological files degrade gracefully to sequential pairing.

`_file_meta()` adds `{size, mtime}` per file for the UI's file info bars.

---

## 5. HTTP surface (`comparator/urls.py` + `views.py`)

| Method | Path | View | Purpose |
|---|---|---|---|
| GET | `/` | `index` | Render main comparison page |
| POST | `/api/compare/` | `compare` | Body `{left_dir, right_dir, settings?, exclusions?}` → JSON of matched/unmatched/stats. `settings` optionally overrides the tunable engine constants for that run (keys = `SETTING_BOUNDS` in `correspondence.py`: `llm_failure_limit`, `max_llm_per_file`, `llm_min_sim`, `content_sim_threshold`); values are clamped, unknown keys ignored, effective values echoed back in `stats.settings`. `exclusions` is `{files: [...], dirs: [...]}` wildcard patterns (fnmatch; patterns with `/` match relative paths, plain patterns match basenames anywhere); matching items are pruned from both scans and never appear in results — normalized by `normalize_exclusions()` in `file_scanner.py` (caps: 200 patterns, 300 chars) and echoed back in `stats.exclusions`. `@csrf_exempt`. |
| GET | `/api/browse/?path=` | `browse` | Directory picker backend. No `path` → drive letters (Windows) or `/` (Unix). Returns `{entries:[{name,path}], current, parent}`. Skips dotfiles + `node_modules`/`__pycache__`/`$recycle.bin`/etc. Returns 403 on `PermissionError`. |
| GET | `/file-compare/?left=&right=` | `file_compare` | Render the BC-style diff viewer. Also accepts `?unmatched=left\|right` single-file mode. |
| GET | `/api/file-diff/?left=&right=` | `file_diff` | **Aligned-row diff JSON** (shape below). `?unmatched=left\|right` returns one file's raw lines (legacy shape: `left_lines`/`right_lines` + empty `opcodes`). |

**Response conventions:** errors are `JsonResponse({'error': ...}, status=4xx/5xx)`. `compare` maps `ValueError` (e.g. missing directory) → 400 and any other exception → 500 (logged via `logger.exception`). All HTML responses set aggressive `no-cache` headers (this tool is edited live and must never serve stale markup).

**`file_diff.py` output shape** (consumed by `file_compare.html`) — an **aligned row model**, BeyondCompare-style:
```
{ left_lines: [...], right_lines: [...],
  rows: [                                   # one entry per visual row, both panels
    {t:'eq',  l:12, r:14},                  #   equal pair (1-based line numbers)
    {t:'mod', l:13, r:15, ls:[[txt,chg]..], rs:[...]},  # modified pair + word-level segments
    {t:'mod', l:.., r:.., m:1},             #   whitespace-only pair (minor)
    {t:'del', l:..}, {t:'add', r:..},       #   one-sided rows -> hatched gap on other panel
  ],
  left_meta:{size,mtime}, right_meta:{...}, # file info bars
  left_path, right_path }
```
Inside `replace` blocks, `_align_replace()` recursively anchors on the best-matching line pair (`SequenceMatcher.ratio() > PAIR_THRESHOLD=0.5`, perf guard `MAX_PAIR_AREA=2500`) so *corresponding lines face each other* even when line numbers drift; unpairable lines become stacked del/add rows. `ls`/`rs` are word-level intra-line segments (`[text, changed]`) that drive the in-line change highlighting. `m:1` flags minor rows (whitespace-only / blank), which the UI's "Minor" toggle can treat as same. The unmatched single-file mode (in `views.py`) still returns the old `left_lines/right_lines/opcodes` shape.

---

## 6. Frontend conventions (important — read before touching UI)

**The templates are fully self-contained.** `index.html` and `file_compare.html` each embed **all** their CSS in a `<style>` block and **all** their JS in a `<script>` block. There are **no `<link>` or external `<script src>` tags**. The `{% load static %}` machinery is effectively unused by the live UI.

Consequences:
- To change the look or behavior of the main page, **edit `comparator/templates/comparator/index.html` directly.** Do **not** edit `comparator/static/comparator/*` expecting it to show up — those files are stale/dead (see §8).
- The JS uses an **event-delegation** pattern: a single capturing `click` listener on `document` reads `data-action="..."` attributes and dispatches in `handleAction()`. When you add a button, give it a `data-action` and add a case — don't attach per-element listeners.
- `index.html`'s browse **modal is toggled via inline `style.display`** (`"block"`/`"none"`), *not* via a `.hidden` class. Tests assert on `el.style.display`. Keep that mechanism. The ⚙ **Engine Settings modal** (`#settingsModal`, opened by `#btnSettings`) follows the same pattern: inline `style.display`, `data-action="settings-*"` cases in `handleAction()`, four slider+number rows defined in `SETTINGS_DEF` (which must mirror `SETTING_BOUNDS` in `correspondence.py`), values persisted in `localStorage["wcEngineSettings"]` and sent as `settings` in the compare POST. The button is disabled and the dialog force-closed while `APP.comparing` is true. The 🚫 **Exclusions modal** (`#exclusionsModal`, opened by `#btnExclusions`) works the same way: BeyondCompare-style file/folder wildcard pattern lists with per-pattern enable switches, draft-copy semantics (Cancel discards, Accept commits), persisted in `localStorage["wcExclusions"]`, enabled patterns sent as `exclusions` in the compare POST, locked while comparing.
- Row double-click opens the file-compare page in a new tab. Matched rows carry `data-left-path`/`data-right-path`; unmatched rows carry `data-unmatched-side` + one path.
- The "Match" column header responds to **double-click** to toggle sorting by content status (`different → minor → identical`). The original order is cached in `APP.lastData`.
- HTML escaping: `index.html` uses an `esc()` helper (textContent round-trip) plus `escAttr()` for attribute values; `file_compare.html` uses a faster regex-replace `esc()`. Preserve escaping whenever injecting file names, paths, or line content.

### The diff viewer (`file_compare.html`) — feature map & internals

**User-facing features** (BC 5 imitation): four view modes — All / Diffs / Same / **Context** (keys `1`–`4`; **Context is the default** and collapses unchanged runs into clickable "··· N unchanged lines ···" separators); a **Minor** toggle (`m`) that treats whitespace-only rows as same; **section navigation** (Prev/Next buttons, keys `n`/`p`, flash-highlights the target section, indicator "Section k / N" in the status bar); a **Swap** button (reloads the page with sides exchanged); a clickable/draggable **overview minimap** with a live viewport rectangle; word-level intra-line change highlighting; diagonal-hatched alignment gaps; file info bars (name / dir / size / mtime); a bottom status bar with per-type line counts (or "✓ Files are identical"); synchronized two-panel scrolling; and a single-file **unmatched** mode that disables the toolbar and hides the minimap.

**JS architecture** (all inline, ES5-flavored):
- Global state: `ROWS` (the backend row model), `MODE` (`all|diff|same|ctx`), `MINOR` (bool), `EXPANDED` (row-index map of user-expanded context), `SECTIONS` (contiguous diff runs), `CUR` (current section), `VIS` (currently rendered items), `CTX = 3` (context lines).
- Render pipeline: `buildVisible()` (filters + context collapsing) → `render()` (builds both panels' HTML strings; **exactly one `.fcl` element per visible row per panel**, gaps included — this invariant is what keeps the two panels pixel-aligned, never break it) → `drawMinimap()`.
- `efft(row)` is the single source of truth for a row's *effective* type under the Minor toggle; filters, sections, minimap colors and row CSS all go through it.
- **Syntax highlighter**: `highlightAll()` precomputes per-line HTML for both files in one stateful pass (`hlLine()` threads multi-line comment/triple-quote state), keyed by extension (`langOf()`: C-family superset incl. Java/JS/TS/Rust/Go/Kotlin, or Python, else plain). It is **decorative**: `mod` rows with intra-line segments skip syntax coloring and render `segHtml()` diff segments instead — diff visibility beats pretty colors.
- Row CSS classes are `t-eq / t-mod / t-min / t-del / t-add / t-gap / t-sep / t-unm`; intra-line changed words are `<span class="ch">`; syntax spans are `sk/ss/sc/sn` (keyword/string/comment/number). Colors live in the `<style>` block — change them there.
- Keyboard shortcuts are ignored while typing in inputs, and disabled entirely in unmatched mode.

---

## 7. Running & testing

All commands run from the repo root (`D:\Proyectos\WorkspaceComparator`). PowerShell is the primary shell; a Bash tool is also available.

### Run the dev server
```powershell
python manage.py runserver          # http://127.0.0.1:8000
python manage.py runserver 9000     # custom port
python manage.py check              # sanity check config (pre-approved)
```
No `migrate` needed (no database). No `collectstatic` needed (static isn't served in the live UI).

### The LLM backend (optional)
For AI-arbitrated matches, Ollama must be running locally with the model available:
```powershell
ollama serve                        # exposes http://127.0.0.1:11434
ollama run glm-5.2:cloud            # ensure the model is pulled
```
If Ollama is down, the app still works — it just falls back to deterministic scoring and `llm_matches` will be 0. (On the current dev machine Ollama responds `200`.)

### Browser tests (Playwright)
```powershell
python test_browser.py              # headless, port 9876, ~7 smoke checks + screenshots
python AutomatedTestsStarter.py     # VISIBLE Chromium, port 9877, ~20 tests, slow_mo=400
```
- Both scripts start their own Django server (`--noreload`), drive Chromium, and write PNGs to `test_screenshots/`. **They hard-code real paths** (`D:/Proyectos/Workspaces/WorkspaceMAE` etc.) — they will FAIL on a machine that doesn't have those directories. Treat them as environment-specific smoke tests, not a portable unit suite.
- `AutomatedTestsStarter.py` opens a visible browser window (deliberately slowed so a human can watch), runs a full comparison of the MAE workspaces (up to 5 min wait), and keeps the browser open 30 s at the end for inspection.
- The tests only cover the **main page** (`index.html`) — the diff viewer has no automated coverage. They assert on element IDs (`#btnBrowseLeft`, `#leftDir`, `#modalList`…), `data-action` attributes, row classes (`.row-matched`, `.row-separator`), and the modal's inline `style.display` — **keep those stable** when editing `index.html`.
- Playwright is **not** in `requirements.txt`; install with `pip install playwright && playwright install chromium` if missing.

### Verifying diff-viewer changes (no fixtures in repo)
There are **no `pytest` / Django `TestCase` unit tests.** For alignment-engine changes, the proven workflow is: write two small variant files (e.g. a Java class pair with an inserted block, a renamed constant, and a whitespace-only change) into a scratch directory, then either (a) call `compute_file_diff()` directly and print the `rows` (pure function, no server needed), or (b) start the server and open `/file-compare/?left=<path>&right=<path>` with the paths URL-encoded, watching the browser console for JS errors. If you add logic to `services/`, consider adding real unit tests — `compute_similarity`, `compute_content_status`, `compute_file_diff`, `_align_replace`, and `_inline_segments` are all pure functions that are trivial to test in isolation.

---

## 8. Known issues, gotchas & tech debt

Read this list before you're surprised by something.

1. **`comparator/static/comparator/{css/styles.css, js/app.js}` is DEAD CODE.** It is an *older generation* of the UI and is **not loaded** by `index.html` (which inlines everything). Evidence: `app.js` references element IDs that no longer exist in the template (`btnClear`, `modalBody`, `modalCurrentPath`, `btnModalUp`, `btnModalSelect`…), and renders a **similarity `%` badge** (`sim-badge`) whereas the live UI renders **content-status symbols** (`==`/`~=`/`!=`). Don't edit these files to change the app. Consider deleting them (or wiring them back up) to remove confusion — but confirm intent first.

2. **Greedy, order-dependent matching.** The engine matches the first-good candidate per phase rather than solving a global optimal assignment. In projects with many same-named files (`__init__.py`, `index.js`, `package-info.java`), results can depend on scan/iteration order. If precision matters there, this is where to invest (e.g. Hungarian-algorithm assignment).

3. **Performance.** The matching engine now caches file contents per run (a `read()` closure over a dict in `find_correspondences`), and the `_LLMGate` circuit breaker stops LLM calls after 3 consecutive failures — but large workspaces can still trigger many *successful* LLM calls (120 s timeout each). A big migration comparison can take minutes — the frontend loading overlay waits up to 300 s in tests. LLM result caching remains a good target. (`file_diff.py` reads are still uncached — fine, it's two files per request.)

4. **Windows-first.** Drive-letter browsing (`browse`) and the test paths assume Windows. Unix has fallbacks (`/` root) but is not the exercised path. Paths are normalized to forward slashes in JSON responses.

5. **Security posture is "localhost only" by design.** `@csrf_exempt` compare, `ALLOWED_HOSTS=['*']`, `DEBUG=True`, committed insecure `SECRET_KEY`, and **arbitrary filesystem read** via `browse`/`file-diff` (any path the server user can read). **Never expose this server to a network.** If productionizing is ever requested, that's a from-scratch security review (path sandboxing, CSRF, auth, secret management, `DEBUG=False`).

6. **`DATA_UPLOAD_MAX_MEMORY_SIZE = 50 MB`** in settings — raised to allow large compare payloads. Keep in mind if request-body errors appear.

7. **Duplicated `_read_file`** in `correspondence.py` and `file_diff.py` with identical encoding-fallback logic. Change both together, or refactor into one shared helper.

8. **The syntax highlighter is best-effort, not a parser.** It's a hand-rolled scanner in `file_compare.html` (strings, line/block comments with cross-line state, keywords, numbers). Edge cases *will* mis-color (regex literals in JS, nested template strings, `#` preprocessor lines in C). That's accepted — it's decorative. Do not "fix" it by pulling in a highlighting library (would break the zero-dependency, self-contained-template convention); if a mis-coloring matters, patch the scanner.

9. **The diff viewer has no automated test coverage.** `test_browser.py` / `AutomatedTestsStarter.py` only exercise `index.html`. Changes to `file_compare.html` or `file_diff.py` must be verified manually (see §7 "Verifying diff-viewer changes").

10. **`views.py` still emits the legacy shape for unmatched files.** The `?unmatched=` branch of the `file_diff` view returns `left_lines/right_lines/opcodes/minor_flags` (pre-row-model). The template's unmatched path only reads `left_lines`/`right_lines`, so the dead keys are harmless — but if you refactor, that branch is where the old shape lives.

---

## 9. Conventions for editing this codebase

- **Match the surrounding style.** The Python uses dataclasses, module-level regex constants (`_UPPER_SNAKE` with a leading underscore for "private"), type hints, and section-banner comments (`# ===== ... =====`). The JS is deliberately ES5-flavored (`var`, no arrow-function reliance in delegation paths) for maximum browser tolerance and inlining. Don't introduce a build step, framework, or npm dependency without being asked.
- **Behavior changes → `services/`.** HTTP/shape changes → `views.py`. Look/feel/interaction → the **template** (not `static/`).
- **Keep the graceful-degradation contract:** anything touching the LLM must keep working when Ollama is absent (i.e. respect the `-1` sentinel).
- **Keep responses cache-busted.** The `no-cache` headers on HTML views are load-bearing for live editing; don't remove them.
- **No database.** Don't add models/migrations unless persistence is an explicit goal.
- **The row model is a contract.** `file_diff.py` (producer) and `file_compare.html` (consumer) must move together — if you change row keys (`t/l/r/ls/rs/m`), update both plus the shape docs in §5. The renderer's one-`.fcl`-per-row-per-panel invariant is what keeps the panels aligned.
- **Keep `index.html` test-stable.** The Playwright suites assert on its element IDs, `data-action` attributes, row classes, and the modal's inline `style.display` toggle. Restyle freely; rename/restructure carefully.
- After changing matching logic, sanity-check against the WorkspaceMAE ↔ WorkspaceMAEMaven example if those directories exist locally; otherwise construct a small two-folder fixture. After changing alignment logic, use a scratch file pair and eyeball the `rows` output or the rendered viewer (§7).

---

## 10. Quick reference — where do I change X?

| I want to… | Edit |
|---|---|
| Change which file types are scanned | `services/file_scanner.py` → `SOURCE_EXTENSIONS` / `SKIP_DIRS` |
| Change user-exclusion pattern matching / caps | `services/file_scanner.py` → `normalize_exclusions` / `_excluded` |
| Make matching stricter/looser | `services/correspondence.py` → the 4 threshold constants |
| Change how similarity is scored | `services/deterministic.py` → `compute_similarity` (token/identifier blend) |
| Change the `==`/`~=`/`!=` classification | `services/deterministic.py` → `compute_content_status` |
| Change the LLM prompt / model / host | `services/llm_comparator.py` → `PROMPT_TEMPLATE`, `MODEL_NAME`, `OLLAMA_BASE` |
| Make line pairing stricter/looser | `services/file_diff.py` → `PAIR_THRESHOLD` (also the `0.3` stacking cutoff in `_sequential`) |
| Tune alignment performance | `services/file_diff.py` → `MAX_PAIR_AREA` |
| Change intra-line (word) diff granularity | `services/file_diff.py` → `_TOKEN_RE` / `_inline_segments` |
| Change what counts as "minor" | `services/file_diff.py` → `_is_ws_only` + blank-line checks in `_del_row`/`_add_row` |
| Change context size around diffs | `file_compare.html` → `CTX` constant (default 3) |
| Change diff colors / hatching / gutter accents | `file_compare.html` → `t-*` CSS classes and `.ch` |
| Change syntax-highlight keywords / colors | `file_compare.html` → `C_KW`/`P_KW`, `langOf()`, `hlLine()`, `sk/ss/sc/sn` CSS |
| Change minimap colors / behavior | `file_compare.html` → `drawMinimap()` |
| Change the main table UI | `templates/comparator/index.html` (inline CSS+JS) — **not** `static/` |
| Change the diff-viewer UI | `templates/comparator/file_compare.html` (inline CSS+JS) |
| Add/modify an API route | `comparator/urls.py` + `comparator/views.py` |
| Change server config | `workspace_comparator/settings.py` |
