# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository. Read this before making changes.

---

## 1. What this project is

**Current version: 1.7.1** — the canonical constant is `__version__` in `workspace_comparator/__init__.py`; the README badge, both template titles, the `index.html` header, and visible Playwright version assertion carry the same number by hand (see §10 "Bump the app version").

**Workspace Comparator** is a **local, single-user Django web tool** that compares two complete project directories ("left" and "right") and works out which files *correspond* to each other — text or native binary, with any extension — even when projects have been restructured, renamed, or migrated between build systems.

It is designed for **codebase archaeology / migration verification**: "we refactored/migrated project A into project B — which files are the same, which changed, and which have no counterpart?"

Portable test truth comes from two repo-owned fixtures: the bundled `demo/InvoicerClassic` ↔ `demo/InvoicerMaven` migration and the 236-file dataset generated at runtime by `HardStoneVisiblePlaywrightTest.py`. The historical MAE pair under `D:/Proyectos/Workspaces/` is still supported by `AutomatedTestsStarter.py` when those external directories exist, but it is not required by the portable suites.

### Two screens

1. **Directory comparison** (`/`) — pick two folders, get a BeyondCompare-style two-panel table: matched files in the middle-joined green rows, unmatched files in red rows. Each matched row shows a **content-status pill** (`==` identical, `~=` minor, `!=` different) and, for AI-matched pairs, an "AI-Matched" label. The stats bar also owns a dynamic extension filter and all-column fuzzy search/highlight navigator.
2. **File comparison** (`/file-compare/`) — double-click a matched or unmatched row to open a **Beyond Compare 5-style** side-by-side viewer: aligned text rows with word-level highlighting or locked native-binary hex rows with byte highlighting. Context folding, minimap, section navigation, syntax coloring, swapping, and single-file views are covered by `docs/screenshots/04-diff-viewer.png` and the hard-stone screenshots.

Both screens are **content-type aware**: every real file is scanned regardless of extension. Actual bytes decide text versus binary, so a Java source file named `.exe` stays text while unknown binary bytes stay binary. Text uses deterministic matching plus bounded LLM arbitration; native binaries use deterministic byte matching only, receive a **BIN** tag, and open in the locked **`hexdump -C`-style hex viewer**.

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
| LLM backend | Local **Ollama API** at `http://127.0.0.1:11434`, model `glm-5.2:cloud` (the model may use Ollama Cloud/network) |
| Browser tests | **Playwright** Python package from `requirements.txt`; Chromium binary installed once with `python -m playwright install chromium` |
| Frontend | Vanilla JS + inline CSS. **No build step, no npm, no framework, no bundler.** |
| Target OS | **Windows** (drive-letter browsing, `D:\` paths). Has Unix fallbacks but is exercised on Windows. |
| Database | None |

`requirements.txt` is the single Python dependency manifest: Django and Requests for runtime, Playwright for browser suites, and PyInstaller for release builds. Playwright's Chromium browser binary is not a Python wheel and still requires the one-time `python -m playwright install chromium` command.

There is **no virtual environment directory** checked in and none required by the tooling; the dev machine uses a system/global Python. If you create one, don't commit it.

---

## 3. Repository layout

```
WorkspaceComparator/
├── manage.py                     # Standard Django entry point
├── requirements.txt              # runtime + Playwright API + PyInstaller
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
│   │   ├── correspondence.py     #   Orchestrator: the phased matching engine
│   │   ├── file_scanner.py       #   Every-file discovery + visible exclusions
│   │   ├── binary_detect.py      #   Content kind, charset, newline + byte helpers
│   │   ├── text_profile.py       #   Content-first language/format detection + LLM system prompt
│   │   ├── deterministic.py      #   Language-aware + generic text similarity scoring
│   │   ├── llm_comparator.py     #   Dynamic-system-prompt Ollama fallback arbiter
│   │   └── file_diff.py          #   ★ Aligned-diff engine + hex diff engine (see §4b/§5) ★
│   ├── templates/comparator/
│   │   ├── index.html            # ★ Main page — SELF-CONTAINED (inline CSS+JS) ★
│   │   └── file_compare.html     # ★ BC-style diff viewer — SELF-CONTAINED (inline CSS+JS) ★
│   └── static/comparator/        # ⚠ DEAD CODE — see §8. NOT loaded by the templates.
│       ├── css/styles.css
│       └── js/app.js
│
├── launcher.py                   # PyInstaller entry point (standalone exe, port 9000)
├── build.py                      # One-file exe build + smoke test
├── demo/                         # Bundled demo migration (InvoicerClassic ↔ InvoicerMaven,
│                                 #   incl. a binary pair: app-icon.png / branding/logo.png)
├── docs/screenshots/             # README screenshots
├── test_browser.py               # Headless Playwright smoke test (port 9876)
├── AutomatedTestsStarter.py      # Visible Playwright full test (port 9877, slow-mo)
├── HardStoneVisiblePlaywrightTest.py # Portable visible 236-file / 60-check regression
└── test_screenshots/             # Output PNGs from the test scripts (gitignored)
```

**Mental model:** `views.py` is a thin adapter. The interesting engineering is entirely in `comparator/services/`. When asked to change *behavior* (matching, scoring, diffing), you almost always edit `services/`, not views or templates.

---

## 4. How the matching engine works (the core algorithm)

This is the heart of the app. Entry point: `comparator/services/correspondence.py :: find_correspondences(left_dir, right_dir)`.

### Step 0 — Scan (`file_scanner.py`)
`scan_directory()` walks every directory and returns every real file, including dot directories, build outputs, unknown extensions, and extensionless names. `inspect_file()` content-sniffs each file and records either `is_binary=True` or an effective text charset. No extension is unsupported and `SKIP_DIRS` is historical only. User exclusions (`{'files': [...], 'dirs': [...]}`) are non-destructive: matching files remain in the result's ignored collections and are never compared. The UI renders them as dark-gray rows only when **Show excluded** is checked. Patterns containing `/` match forward-slash relative paths; plain patterns match basenames anywhere. The scanner also adds explicit ignored `.` and `..` directory aliases.

### Content kind, charset, and newline rules (`binary_detect.py`)
- **Content is authoritative.** `inspect_file()` samples the first 8192 bytes. A recognized Unicode text layout stays text; otherwise NUL bytes or a high control-byte ratio classify native binary. `BINARY_EXTENSIONS` is a descriptive catalogue only and never overrides textual bytes.
- **Unknown and misleading extensions are valid.** Text named `.exe`, `.jar`, `.o`, a custom suffix, or no suffix enters the complete text pipeline. Unknown binary bytes enter the deterministic binary/hex pipeline.
- **Auto charset detection** recognizes UTF-8 BOM, UTF-16/32 BOMs, common BOM-less UTF-16/32 lane patterns, strict UTF-8, then Windows-1252 or Latin-1.
- **Per-side overrides** are `auto`, `utf-8`, `utf-8-sig`, `utf-16`, `utf-16-le`, `utf-16-be`, `utf-32`, `utf-32-le`, `utf-32-be`, `cp1252`, `latin-1`, `ascii`, `shift_jis`, `gb18030`, `big5`, and `euc-kr`. An override changes decoding only after binary sniffing; it cannot send native bytes to the LLM.
- **Logical newline equality** is global: decoded `CRLF`, `LF`, and lone `CR` canonicalize to `LF` before status, deterministic scoring, LLM prompts, and text diff rows. Binary comparison remains byte-exact.
- Empty and unreadable files remain reportable rather than disappearing. Content sampling is intentionally bounded, so the scanner does not read every large artifact in full merely to classify it.

### The 4 phases
Matching is **greedy and order-dependent**. Files start "free"; once matched, both sides are removed from the free pool. Phases run in sequence, each consuming from what the previous left behind:

- **Phase 1 — Exact path match.** Same filename **and** same `relative_dir`. Instant match, `similarity=100`, `match_type='exact_path'`. Highest confidence. Binary pairs get their content status from **byte equality** (`_binary_status`: `identical`/`different`, never `minor`) instead of text normalization.
- **Phase 2-BIN — Binary files, same filename, different directory.** Runs BEFORE the text Phase 2 so binaries can never leak into text scoring or LLM arbitration. Binary bytes are opaque to the LLM ("no way to tell the differences with an LLM"), so the **exact filename is the only key**; among several same-named candidates the ranking is `(byte-identical, directory-path similarity)` — identity trumps, then the closest `relative_dir` (SequenceMatcher) wins. Match is `match_type='binary'` (UI labels it *Moved*), similarity = 100 when identical else `binary_similarity()`'s chunk-level estimate (display-only), counted in `stats['binary_matches']`. Binaries with **no same-named counterpart stay unmatched** — Phases 3/3b skip them entirely (a renamed binary is undecidable).
- **Phase 2 — Same filename, different directory (text).** For each remaining left file, *all* right files with the same filename are scored deterministically first (sorted best-first). If any has `confidence=='high'` and `similarity > 85` → accept as `deterministic`, no LLM. Otherwise **ask the LLM** for at most the top `MAX_LLM_PER_FILE` (3) candidates whose deterministic sim clears the `LLM_MIN_SIM` (15) noise floor; a score `>= 70` → accept as `llm_verified`. If the LLM is unreachable (returns `-1`) but deterministic `> 40`, fall back to accepting as `deterministic`. The bound matters: without it, boilerplate names (`__init__.py`, `index.js`) with hundreds of same-named candidates trigger one LLM round-trip *each* and a compare never finishes.
- **Phase 3 — Similar filename (fuzzy), any text extension.** For still-unmatched text files, compare filenames with `compute_filename_similarity` and content regardless of extension. Score = `filename_sim*30 + content_sim*0.70`; high-confidence deterministic candidates auto-accept and ambiguous candidates use the bounded LLM fallback.
- **Phase 3b — Renamed files (content-only).** Text leftovers are swept across any extension. Deterministic similarity `>= CONTENT_SIM_THRESHOLD` produces a content match; otherwise the best bounded candidates can reach the LLM. A cheap length-ratio bound prunes the O(L·R) sweep first, and the sweep short-circuits entirely (before its eager left-file reads) as soon as the right side has no free text candidate — e.g. an empty or binary-only right project. Binaries never enter this phase.
- **Phase 4 — Leftovers.** Everything still free is reported as `unmatched_left` / `unmatched_right`.

Results (`ComparisonResult`) carry `matched`, `unmatched_left`, `unmatched_right`, `ignored_left`, and `ignored_right`, plus stats for total/comparable/ignored files, exact/deterministic/binary/LLM matches, LLM calls, effective engine settings, exclusions, and charsets. `showExcluded` is not part of this service contract; it is a client-only rendering preference, so ignored entries and counts remain complete even when their table section is hidden. Sorting anchors on the **left side** (the user's original project): primary key = left filename, secondary key = left directory. Unmatched and ignored lists sort independently by filename then directory.

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
1. Canonicalize `CRLF`, `LF`, and `CR`, then detect each content profile with extension only as a secondary hint.
2. Strip comments using C-style, hash-style, markup, SQL, assembly/Lisp, or generic behavior as appropriate.
3. Replace string literals with `""` to focus on structure.
4. Normalize whitespace (collapse runs, drop blank lines).
5. Compute token similarity with `difflib.SequenceMatcher`.
6. Extract language-specific plus generic declarations, markup element names, and structured-data keys; drop `_NOISE`; compute Jaccard overlap.
7. Blend `0.60 * token_sim + 0.40 * identifier_sim` when identifiers exist, otherwise use token similarity. Confidence is high above 85, medium above 40, else low.

`compute_content_status(content1, content2, ext) -> 'identical' | 'minor' | 'different'` drives the `==`/`~=`/`!=` symbols: equal after newline canonicalization → `identical`; equal only after comment/string/whitespace normalization → `minor`; else `different`.

`compute_filename_similarity(a, b)` → SequenceMatcher ratio on the lowercased base names (extension removed).

### LLM arbiter (`llm_comparator.py`)
Only invoked for genuinely ambiguous text candidates. `text_profile.py` detects language/format from content first and extension second, then builds a dynamic Ollama `system` message containing each side's profile, extension hint, charset, and language-specific comparison guidance. Unknown text gets a generic structural fallback. `compare_with_llm(...)` sends that system message plus the migration prompt to Ollama's `/api/generate` and asks for one 0–100 correspondence score.

- Files are truncated to `MAX_FILE_CHARS = 6000` via `_smart_truncate` (keeps 40% head / 35% middle / 25% tail with `/* ... truncated ... */` markers) so headers, core logic, and closings all survive.
- Returns **`-1`** on any failure (connection refused, timeout, non-numeric answer). Callers treat `-1` as "LLM unavailable" and fall back to deterministic scoring. **The app degrades gracefully with no Ollama running** — you just lose the AI-arbitrated matches.
- **Binary lock-out**: content sniffing removes native binaries before text phases, binary candidate loops explicitly reject them, and `_LLMGate.score` keeps a final NUL defense for files that change after scanning. Charset overrides can never force binary bytes into the LLM.
- **Circuit breaker** (`_LLMGate` in `correspondence.py`, `LLM_FAILURE_LIMIT = 3`): all Phase 2/3/3b LLM escalations go through the gate. It probes Ollama once per run and disables arbitration after the configured number of consecutive failures. A success resets the counter; `stats['llm_calls']` counts only real HTTP attempts.
- The prompt begins with `/no_think` (a directive for thinking-capable models to skip visible reasoning).

**Encoding and newlines:** `binary_detect.py` owns shared content sniffing and decoding. Auto mode recognizes UTF BOMs, common BOM-less UTF-16/32 layouts, UTF-8, Windows-1252, and Latin-1. The Engine Settings dialog also provides independent left/right charset overrides for additional legacy encodings. Decoded `CRLF`, `LF`, and `CR` are canonicalized to `LF` before matching, status classification, LLM prompting, and visual diffing.

---

## 4b. The diff alignment engine (`file_diff.py`) — the second core algorithm

The file-compare view is powered by its own algorithm, separate from the directory-matching engine. Entry point: `compute_file_diff(left_path, right_path, left_encoding='auto', right_encoding='auto')`. It intentionally imitates Beyond Compare's alignment behavior.

**Pipeline:**
1. Read both files through shared charset-aware decoding, canonicalize all newline conventions to `LF`, then split into lines.
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

### The hex diff engine (introduced in v1.3.0; content-first routing in v1.6.0)

`compute_hex_diff(left_path, right_path)` powers the binary/HEX view. Both files are chunked into **16-byte rows** (canonical `hexdump -C` width); `SequenceMatcher(autojunk=False)` over the *chunk lists* aligns them (so 16-byte-multiple insertions/deletions produce proper `add`/`del` gap rows), and `replace` blocks are **zipped positionally** — rows are fixed-width, there is nothing smarter to anchor on. Row shapes reuse the text model (`t`: `eq/mod/del/add`), but `l`/`r` are **1-based 16-byte-row indices** into base64-encoded byte windows (`left_b64`/`right_b64`); the UI formats the hexdump text and computes **per-byte** change highlighting itself by comparing the paired rows' bytes. No `m`, no `ls`/`rs` in hex rows.

```python
HEX_BYTES_PER_ROW  = 16       # hexdump -C row width (UI hard-assumes 16 too)
HEX_VIEW_MAX_BYTES = 131072   # 128 KB rendered per side; the rest is reported, not drawn
_HEX_MAX_MATCH_COST = 8_000_000  # difflib work bound -> positional pairing fallback
```

Honesty guarantees: `identical` is computed on the **whole files** (`bytes_equal` → `filecmp.cmp(shallow=False)`), never on the truncated windows; `truncated.{left,right}` + `left_total`/`right_total` let the UI say "differs beyond the rendered window". The `match_cost` guard (in `binary_detect.py`) caps difflib's quadratic blow-up on degenerate repetitive content (zero-page-heavy files) by falling back to offset-zipped pairing. Known limitation: a **non-16-multiple** insertion shifts every following row and degrades to a long `mod` run — accepted, BC-grade byte realignment isn't worth it for artifact comparison.

`compute_hex_single(path)` returns the one-file equivalent (`b64/truncated/total/binary/meta`) for the unmatched view. Binary detection is content-first (Unicode text recognition, then NUL/control-byte sniff) and lives in `binary_detect.py` together with shared charset decoding, `bytes_equal`, `binary_similarity`, and `read_head`.

---

## 5. HTTP surface (`comparator/urls.py` + `views.py`)

| Method | Path | View | Purpose |
|---|---|---|---|
| GET | `/` | `index` | Render main comparison page |
| POST | `/api/compare/` | `compare` | Body `{left_dir, right_dir, settings?, exclusions?, charsets?}` → matched/unmatched/ignored/stats JSON. `charsets` is `{left, right}` with `auto` or a supported explicit codec. Excluded entries are always returned under `ignored_left` / `ignored_right`; **Show excluded** is client-only and is not sent to this endpoint. Effective settings, exclusions, and charsets are echoed in stats. `@csrf_exempt`. |
| GET | `/api/browse/?path=` | `browse` | Directory picker backend. No `path` → drive letters (Windows) or `/` (Unix). Returns `{entries:[{name,path}], current, parent}`. Skips dotfiles + `node_modules`/`__pycache__`/`$recycle.bin`/etc. Returns 403 on `PermissionError`. |
| GET | `/file-compare/?left=&right=` | `file_compare` | Render the diff viewer. Accepts `unmatched=left\|right`, `hex=1`, and `left_encoding` / `right_encoding`. Content sniffing locks HEX when either side is native binary. |
| GET | `/api/file-diff/?left=&right=` | `file_diff` | Return aligned text rows or hex payload. Accepts the same unmatched/hex/encoding parameters. Binary content always returns hex regardless of the requested text mode. |

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
  left_path, right_path, left_encoding, right_encoding }
```
Inside `replace` blocks, `_align_replace()` recursively anchors on the best-matching line pair (`SequenceMatcher.ratio() > PAIR_THRESHOLD=0.5`, perf guard `MAX_PAIR_AREA=2500`) so *corresponding lines face each other* even when line numbers drift; unpairable lines become stacked del/add rows. `ls`/`rs` are word-level intra-line segments (`[text, changed]`) that drive the in-line change highlighting. `m:1` flags minor rows (whitespace-only / blank), which the UI's "Minor" toggle can treat as same. The unmatched single-file mode (in `views.py`) still returns the old `left_lines/right_lines/opcodes` shape.

**Hex payload shape** (`compute_hex_diff`, marked by `hex: true`):
```
{ hex: true,
  rows: [ {t:'eq',l:1,r:1}, {t:'mod',l:2,r:2}, {t:'del',l:3}, {t:'add',r:3} ],
                                     # l/r = 1-based 16-byte ROW indices into the windows
  left_b64, right_b64,               # base64 byte windows (<= HEX_VIEW_MAX_BYTES each)
  identical: bool,                   # FULL-file byte equality (truthful under truncation)
  truncated: {left, right}, left_total, right_total,
  left_binary, right_binary,         # content-level detection per side (drives BIN chips)
  left_meta, right_meta, left_path, right_path }
```
The unmatched hex variant adds `unmatched: 'left'|'right'` and fills only that side's `*_b64/meta/total`. Compare entries carry `binary: true` when either side is binary. Every `FileInfo.to_dict()` includes `binary`, effective `encoding`, `ignored`, and `ignored_reason`; `stats.binary_matches` feeds the teal Binary chip.

---

## 6. Frontend conventions (important — read before touching UI)

**The templates are fully self-contained.** `index.html` and `file_compare.html` each embed **all** their CSS in a `<style>` block and **all** their JS in a `<script>` block. There are **no `<link>` or external `<script src>` tags**. The `{% load static %}` machinery is effectively unused by the live UI.

Consequences:
- To change the look or behavior of the main page, **edit `comparator/templates/comparator/index.html` directly.** Do **not** edit `comparator/static/comparator/*` expecting it to show up — those files are stale/dead (see §8).
- The JS uses an **event-delegation** pattern: a single capturing `click` listener on `document` reads `data-action="..."` attributes and dispatches in `handleAction()`. When you add a button, give it a `data-action` and add a case — don't attach per-element listeners. Backdrop actions are valid only when `event.target` is the backdrop itself; never `preventDefault()` for descendant controls, because that breaks native checkbox/select behavior.
- `index.html`'s browse **modal is toggled via inline `style.display`** (`"block"`/`"none"`), *not* via a `.hidden` class. The Engine Settings modal contains four numeric rows plus left/right charset selectors; numeric values persist in `wcEngineSettings`, charsets in `wcTextCharsets`, and both are sent on compare. The Exclusions modal uses Accept/Cancel draft semantics and persists patterns plus checked-by-default `showExcluded` in `wcExclusions`; its file/folder lists are independently bounded and scrollable. Enabled patterns are always sent, while **Show excluded** only controls whether returned ignored rows render in the table and applies immediately to the current report without another API request.
- The stats-bar mini-form is client-only state under `APP.resultView`. `prepareResultTools()` derives extension options from both sides of matched rows plus every unmatched/ignored entry. `*.*` is the reset value and `[no extension]` maps to `__no_extension__`. A matched row passes an extension filter when **either** side matches. Search operates on rendered cells after extension/Show-excluded filtering, using case/diacritic folding, token AND matching, bounded fuzzy subsequences, `<mark class="search-mark">` highlights, and `focusSearchHit()` navigation. It must never mutate `APP.lastData` or trigger `/api/compare/`.
- Row double-click opens the file-compare page in a new tab. Matched/unmatched rows carry file paths plus effective `data-left-encoding` / `data-right-encoding`; ignored rows intentionally have no open action.
- The "Match" column header responds to **double-click** to toggle sorting by content status (`different → minor → identical`). The original order is cached in `APP.lastData`.
- HTML escaping: `index.html` uses an `esc()` helper (textContent round-trip) plus `escAttr()` for attribute values; `file_compare.html` uses a faster regex-replace `esc()`. Preserve escaping whenever injecting file names, paths, or line content.

### The diff viewer (`file_compare.html`) — feature map & internals

**User-facing features** (BC 5 imitation): four view modes — All / Diffs / Same / **Context** (keys `1`–`4`; **Context is the default** and collapses unchanged runs into clickable "··· N unchanged lines ···" separators); a **Minor** toggle (`m`) that treats whitespace-only rows as same; a **HEX switch** (`h`, teal toggle in the toolbar) that swaps the whole viewer into a colored `hexdump -C` byte comparison — optional on text pairs, **checked + `disabled` + 🔒 lock glyph** whenever a binary is involved (`FORCE_HEX`, from the server's `force_hex` context or a hex-upgraded API response; `setHex()` hard-refuses to leave hex when forced); **section navigation** (Prev/Next buttons, keys `n`/`p`, flash-highlights the target section, indicator "Section k / N" in the status bar); a **Swap** button (reloads the page with sides exchanged, preserving `hex=1`); a clickable/draggable **overview minimap** with a live viewport rectangle; word-level intra-line change highlighting; diagonal-hatched alignment gaps; file info bars (name / dir / size / mtime, plus a teal **BIN** chip on binary sides); a bottom status bar with per-type line counts (or "✓ Files are identical" / "✓ Files are byte-for-byte identical"); synchronized two-panel scrolling; and a single-file **unmatched** mode that disables the toolbar and hides the minimap (the HEX switch stays live there — hexdump works on one file too).

**Hex mode internals**: `HEX`/`FORCE_HEX`/`HEXDATA` + `HEXL`/`HEXR` (`Uint8Array` decoded from `left_b64`/`right_b64`); responses are cached per mode in `CACHE = {text, hex}` so toggling never refetches; `loadView()`→`applyData()` is the single load path for pair *and* unmatched. `render()` picks `hexLineHtml` (offset gutter `hex8`, `hexCells()` builds the canonical `xx xx … |ascii|` body, `.ch` spans mark differing bytes on paired `mod` rows, `.hx-z` dims NUL bytes, `.hx-a`/`.hx-p` style the ascii gutter) — the **one-`.fcl`-per-row-per-panel invariant holds in hex too** (the truncation notice `t-sep t-trunc` is appended to BOTH panels). Minor is force-disabled in hex (no whitespace in bytes); `hex=1` is kept in the URL via `history.replaceState`; `body.hexmode` class scopes hex CSS. Modes/context/sections/minimap all run unchanged through `efft()` because hex rows use the same `t` values.

**JS architecture** (all inline, ES5-flavored):
- Global state: `ROWS` (the backend row model), `MODE` (`all|diff|same|ctx`), `MINOR` (bool), `EXPANDED` (row-index map of user-expanded context), `SECTIONS` (contiguous diff runs), `CUR` (current section), `VIS` (currently rendered items), `CTX = 3` (context lines).
- Render pipeline: `buildVisible()` (filters + context collapsing) → `render()` (builds both panels' HTML strings; **exactly one `.fcl` element per visible row per panel**, gaps included — this invariant is what keeps the two panels pixel-aligned, never break it) → `drawMinimap()`.
- `efft(row)` is the single source of truth for a row's *effective* type under the Minor toggle; filters, sections, minimap colors and row CSS all go through it.
- **Syntax highlighter**: `highlightAll()` precomputes per-line HTML in one stateful pass. `langOf(path, lines)` uses content signatures first, a broad extension catalogue second, and a generic keyword/string/number fallback for every other text format. It is decorative: modified rows render diff segments instead so change visibility wins.
- Row CSS classes are `t-eq / t-mod / t-min / t-del / t-add / t-gap / t-sep / t-unm`; intra-line changed words are `<span class="ch">`; syntax spans are `sk/ss/sc/sn` (keyword/string/comment/number). Colors live in the `<style>` block — change them there.
- Keyboard shortcuts are ignored while typing in inputs, and disabled entirely in unmatched mode.

---

## 7. Running & testing

All commands run from the repo root (`C:\Development\WorkspaceComparator` on the current machine). PowerShell is the primary shell.

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
If Ollama is down, the app still works — it falls back to deterministic scoring and `llm_matches` stays 0.
The app only calls the loopback Ollama API, but the configured `:cloud` model may use Ollama's cloud service. Set Max LLM candidates to 0 for deterministic-only comparison.

### Browser tests (Playwright)
```powershell
python test_browser.py              # headless, port 9876, 6 smoke checks + screenshots
python AutomatedTestsStarter.py     # VISIBLE Chromium, port 9877, ~20 tests, slow_mo=400
python HardStoneVisiblePlaywrightTest.py # VISIBLE, generated 236-file dataset, 60 screenshots/checks
```
- All scripts start an isolated Django server with `--noreload`, drive Chromium, and write PNGs to `test_screenshots/`.
- `test_browser.py` is a portable six-check headless smoke test; when the historical MAE directories are absent it uses the bundled demo.
- `AutomatedTestsStarter.py` is the legacy visible MAE-workspace suite and still requires those external directories.
- `HardStoneVisiblePlaywrightTest.py` is the portable primary regression: it creates 236 real files, runs 60 headed checks, opens text and binary viewers, verifies all-extension matching, charsets, newline equality, independently scrollable exclusion lists, both **Show excluded** states, dynamic extension options, cross-extension OR filtering, fuzzy highlighted search with automatic scrolling, and locked hex, then keeps the browser visible for inspection.
- Playwright is declared in `requirements.txt`; install its browser binary separately with `python -m playwright install chromium` if missing.

### Verifying service and diff-viewer changes
There are no `pytest` / Django `TestCase` modules yet. Use focused scratch fixtures for pure-service assertions, then run the hard-stone suite for visible end-to-end coverage. `compute_similarity`, `compute_content_status`, content/charset inspection, dynamic prompt generation, `compute_file_diff`, `_align_replace`, `_inline_segments`, and the hex functions are directly testable without a server.

The same workflow verifies **hex/binary changes**: `compute_hex_diff()` / `compute_hex_single()` / `is_binary_file()` / `binary_similarity()` are pure; craft byte fixtures (identical pair, single-byte flip, 16-byte-aligned insertion, >128 KB truncation pair, all-zero cost-guard pair, UTF-16 "text" file) and assert on the row signature. For the engine, build a two-folder fixture with same-named binaries in different dirs and assert `match_type='binary'`, the `(byte-identity, dir-clue)` ranking, and `stats['llm_calls'] == 0`. The bundled demo (`demo/InvoicerClassic` ↔ `demo/InvoicerMaven`) includes a binary pair (`app-icon.png` exact match, `branding/logo.png` moved+changed) for eyeballing.

---

## 8. Known issues, gotchas & tech debt

Read this list before you're surprised by something.

1. **`comparator/static/comparator/{css/styles.css, js/app.js}` is DEAD CODE.** It is an *older generation* of the UI and is **not loaded** by `index.html` (which inlines everything). Evidence: `app.js` references element IDs that no longer exist in the template (`btnClear`, `modalBody`, `modalCurrentPath`, `btnModalUp`, `btnModalSelect`…), and renders a **similarity `%` badge** (`sim-badge`) whereas the live UI renders **content-status symbols** (`==`/`~=`/`!=`). Don't edit these files to change the app. Consider deleting them (or wiring them back up) to remove confusion — but confirm intent first.

2. **Greedy, order-dependent matching.** The engine matches the first-good candidate per phase rather than solving a global optimal assignment. In projects with many same-named files (`__init__.py`, `index.js`, `package-info.java`), results can depend on scan/iteration order. If precision matters there, this is where to invest (e.g. Hungarian-algorithm assignment).

3. **Performance.** The matching engine now caches file contents per run (a `read()` closure over a dict in `find_correspondences`), and the `_LLMGate` circuit breaker stops LLM calls after 3 consecutive failures — but large workspaces can still trigger many *successful* LLM calls (120 s timeout each). A big migration comparison can take minutes — the frontend loading overlay waits up to 300 s in tests. LLM result caching remains a good target. (`file_diff.py` reads are still uncached — fine, it's two files per request.)

4. **Windows-first.** Drive-letter browsing is the primary UI path. Unix has a `/` fallback but is less exercised. Portable tests use generated or bundled fixtures; JSON relative directories normalize to forward slashes.

5. **Security posture is "localhost only" by design.** `@csrf_exempt` compare, `ALLOWED_HOSTS=['*']`, `DEBUG=True`, committed insecure `SECRET_KEY`, and **arbitrary filesystem read** via `browse`/`file-diff` (any path the server user can read). **Never expose this server to a network.** If productionizing is ever requested, that's a from-scratch security review (path sandboxing, CSRF, auth, secret management, `DEBUG=False`).

6. **`DATA_UPLOAD_MAX_MEMORY_SIZE = 50 MB`** in settings — raised to allow large compare payloads. Keep in mind if request-body errors appear.

7. **Shared text decoding is in `binary_detect.py`.** Keep `inspect_file`, `detect_text_encoding`, `read_text_file`, and `normalize_newlines` consistent; correspondence and diff services both use them.

8. **The syntax highlighter is best-effort, not a parser.** It's a hand-rolled scanner in `file_compare.html` (strings, line/block comments with cross-line state, keywords, numbers). Edge cases *will* mis-color (regex literals in JS, nested template strings, `#` preprocessor lines in C). That's accepted — it's decorative. Do not "fix" it by pulling in a highlighting library (would break the zero-dependency, self-contained-template convention); if a mis-coloring matters, patch the scanner.

9. **The diff viewer has visible regression coverage.** `HardStoneVisiblePlaywrightTest.py` opens text and native-binary rows, verifies CRLF/LF equality, and checks the locked hex viewer. Keep focused service checks for algorithmic edge cases too.

10. **`views.py` still emits the legacy shape for unmatched files.** The `?unmatched=` branch of the `file_diff` view returns `left_lines/right_lines/opcodes/minor_flags` (pre-row-model) *for text files*; binary/`hex=1` unmatched requests get the hex payload instead. The template's unmatched path only reads `left_lines`/`right_lines`, so the dead keys are harmless — but if you refactor, that branch is where the old shape lives.

11. **Hex alignment is 16-byte-chunk-granular.** A non-16-multiple insertion in a binary shifts every subsequent row, so the tail renders as a long `mod` run instead of a clean gap (documented in §4b). Also, the hex UI hard-assumes 16 bytes/row (`hexCells`, `hex8` offsets) — changing `HEX_BYTES_PER_ROW` alone will break the frontend.

12. **`BINARY_EXTENSIONS` is not a gate.** Content bytes decide text versus binary. Adding a catalogue entry must never make demonstrably textual content binary. Use exclusions when a tree contains unwanted artifact noise; **Show excluded** decides only whether the resulting ignored rows are rendered.

13. **Ignored visibility is presentation-only.** Never use `showExcluded` to prune scans, alter matching, remove `ignored_left` / `ignored_right`, or change ignored stats. It belongs to `wcExclusions` in the main-page client and must re-render `APP.lastData` without rescanning.

14. **Result filtering/search is also presentation-only.** Extension filtering must preserve a matched pair when either filename has the selected suffix. Search may decorate freshly rendered text nodes, but must not rewrite paths, data attributes, response arrays, stats, matching order, or backend request bodies. Re-render from `APP.lastData` before applying new marks.

---

## 9. Conventions for editing this codebase

- **Match the surrounding style.** The Python uses dataclasses, module-level regex constants (`_UPPER_SNAKE` with a leading underscore for "private"), type hints, and section-banner comments (`# ===== ... =====`). The JS is deliberately ES5-flavored (`var`, no arrow-function reliance in delegation paths) for maximum browser tolerance and inlining. Don't introduce a build step, framework, or npm dependency without being asked.
- **Behavior changes → `services/`.** HTTP/shape changes → `views.py`. Look/feel/interaction → the **template** (not `static/`).
- **Keep the graceful-degradation contract:** anything touching the LLM must keep working when Ollama is absent (i.e. respect the `-1` sentinel).
- **Keep responses cache-busted.** The `no-cache` headers on HTML views are load-bearing for live editing; don't remove them.
- **No database.** Don't add models/migrations unless persistence is an explicit goal.
- **The row model is a contract.** `file_diff.py` (producer) and `file_compare.html` (consumer) must move together — if you change row keys (`t/l/r/ls/rs/m`), update both plus the shape docs in §5. The renderer's one-`.fcl`-per-row-per-panel invariant is what keeps the panels aligned.
- **Keep `index.html` test-stable.** The Playwright suites assert on its element IDs, `data-action` attributes, row classes, and the modal's inline `style.display` toggle. Restyle freely; rename/restructure carefully.
- After changing matching, charset, exclusion, or viewer logic, run the portable hard-stone suite. Use the MAE pair only as an optional large external migration check.

---

## 10. Quick reference — where do I change X?

| I want to… | Edit |
|---|---|
| Change all-file scanning / exclusion classification | `services/file_scanner.py` → `scan_directory` / exclusion helpers |
| Change text/binary or charset detection | `services/binary_detect.py` → `inspect_file` / `looks_binary_bytes` / decoding helpers |
| Change language-aware LLM system prompts | `services/text_profile.py` + `services/llm_comparator.py` |
| Change binary matching (dir clue, ranking) | `services/correspondence.py` → Phase 2-BIN block / `_dir_similarity` |
| Change the hex view size cap / row width | `services/file_diff.py` → `HEX_VIEW_MAX_BYTES` / `HEX_BYTES_PER_ROW` (UI assumes 16!) |
| Change hex colors / byte highlighting | `file_compare.html` → `hexCells()` + `.hx-z/.hx-a/.hx-p/.ch` CSS, `.fc-hex-*` for the switch |
| Change the BIN tag / Binary chip on the main page | `templates/comparator/index.html` → `.bin-tag` / `#statBin` / `badge-teal` |
| Change user-exclusion pattern matching / caps | `services/file_scanner.py` → `normalize_exclusions` / `_excluded` |
| Change exclusion-list scrolling / **Show excluded** rendering | `templates/comparator/index.html` → `.excl-columns` / `.excl-list` / `wcExclusions` / `renderCurrentResults` |
| Change extension filtering / table search / hit highlighting | `templates/comparator/index.html` → `.result-tools` / `APP.resultView` / `prepareResultTools` / `applyTableSearch` / `focusSearchHit` |
| Make matching stricter/looser | `services/correspondence.py` → the 4 threshold constants |
| Change how similarity is scored | `services/deterministic.py` → `compute_similarity` (token/identifier blend) |
| Change the `==`/`~=`/`!=` classification | `services/deterministic.py` → `compute_content_status` |
| Change language detection / dynamic system prompt | `services/text_profile.py` → profiles/signals/guidance |
| Change the LLM user prompt / model / host | `services/llm_comparator.py` → `PROMPT_TEMPLATE`, `MODEL_NAME`, `OLLAMA_BASE` |
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
| Bump the app version | `workspace_comparator/__init__.py` → `__version__` (feeds launcher/build), plus README badge, both template titles, `index.html` header, hard-stone version assertion, and §1 of both agent guides |
