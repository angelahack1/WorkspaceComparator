# Workspace Comparator — Standalone Executable

Workspace Comparator is a local Django web tool that compares two source-code
project directories and works out which files correspond to each other, with a
Beyond Compare-style side-by-side diff viewer.

This document explains how to generate **`WorkSpaceComparator.exe`** — a single,
fully self-contained Windows executable that runs on **any Windows machine,
even without Python installed**.

---

## 1. What you get

- **One single file**: `dist/WorkSpaceComparator.exe` (~30–50 MB).
- It embeds the Python interpreter, Django, the application code and all the
  HTML/CSS/JS of the UI. Nothing else is needed at runtime.
- On double-click it:
  1. Starts the web server on **http://127.0.0.1:9000/** (not 8000).
  2. Waits until the server is completely loaded and accepting connections.
  3. **5 seconds later**, opens your default browser on the application.
- Closing the console window (or pressing `Ctrl+C` in it) stops the server.

---

## 2. Prerequisites (only for BUILDING the exe)

The *target* machine needs nothing. The machine where you **build** needs:

| Requirement | Notes |
|---|---|
| Windows 10/11 | PyInstaller builds for the OS it runs on — build on Windows to get a Windows exe. |
| Python 3.10+ (3.12 recommended) | Check with `python --version`. |
| Internet access | `build.py` downloads packages with `pip` on first run. |

You do **not** need to pre-install Django, requests or PyInstaller —
`build.py` installs everything automatically.

---

## 3. How to generate the exe (step by step)

Open a terminal (PowerShell or CMD) **in the repository root**
(the folder containing `manage.py` and `build.py`) and run:

```powershell
python build.py
```

That is all. The script performs four steps, printing a banner for each:

1. **`pip install -r requirements.txt`** — installs the runtime dependencies
   (`django`, `requests`). This is done so that anyone can clone the repo on a
   clean machine and build the exe without any manual setup.
2. **`pip install pyinstaller`** — installs (or updates) PyInstaller.
3. **Cleanup** — deletes any previous `build/`, `dist/` and `.spec` output so
   every build is reproducible from scratch.
4. **PyInstaller** — runs the actual build (takes a few minutes):

   ```
   python -m PyInstaller --onefile --name WorkSpaceComparator
       --paths <repo root>
       --add-data comparator/templates;comparator/templates
       --collect-submodules django
       --hidden-import <project + django modules loaded by string>
       launcher.py
   ```

When it finishes you will see:

```
======================================================================
  BUILD SUCCESSFUL
======================================================================
  Executable : D:\...\WorkspaceComparator\dist\WorkSpaceComparator.exe
```

### Why those PyInstaller options?

- `--onefile` — packs everything into **one** exe instead of a folder.
- `--add-data comparator/templates;...` — the entire UI lives in two
  self-contained HTML templates (`index.html`, `file_compare.html`); they are
  data files, not Python code, so they must be bundled explicitly.
- `--collect-submodules django` + the `--hidden-import` list — Django loads
  many modules from **strings** in `settings.py` (`INSTALLED_APPS`,
  `MIDDLEWARE`, `ROOT_URLCONF`, template backends…). PyInstaller's static
  analysis cannot see string imports, so they are declared explicitly.
- `launcher.py` — the entry point. It replaces `manage.py runserver`: it boots
  Django programmatically, serves on port **9000**, and opens the browser
  5 seconds after the server is confirmed up.

---

## 4. Using the executable

1. Copy `dist\WorkSpaceComparator.exe` to the target machine (USB stick,
   network share, whatever). **No Python, no pip, no installation required.**
2. Double-click it. A console window opens showing the server log:

   ```
   ============================================================
     Workspace Comparator - standalone server
   ============================================================
   Starting server at http://127.0.0.1:9000/
   Keep this window open while using the application.
   Server is up. Opening browser in 5 seconds...
   ```

3. Your default browser opens automatically at **http://127.0.0.1:9000/**.
4. Keep the console window open while you work. Close it (or `Ctrl+C`) to
   stop the server.

### Optional switches

| How | Effect |
|---|---|
| `WorkSpaceComparator.exe --no-browser` | Start the server without opening a browser. |
| `set WSC_PORT=9500` before running | Serve on a different port than 9000. |
| `set WSC_NO_BROWSER=1` before running | Same as `--no-browser`. |

### AI-assisted matching (optional)

The comparison engine optionally uses a local **Ollama** LLM
(`http://127.0.0.1:11434`, model `glm-5.2:cloud`) to arbitrate ambiguous file
matches. This is **not** bundled in the exe. If Ollama is not running on the
target machine, the app degrades gracefully to deterministic matching —
everything still works, you just get `llm_matches = 0`.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Windows SmartScreen: "Windows protected your PC" | Normal for unsigned exes. Click *More info → Run anyway* (or sign the binary). |
| Antivirus quarantines the exe | Some AVs are suspicious of PyInstaller one-file binaries. Add an exclusion, or build with `--onedir` instead. |
| First start takes several seconds | Expected: a one-file exe unpacks itself to a temp folder on each launch. |
| `ERROR: port 9000 is already in use` | Another program (or a second copy of this exe) is on 9000. Close it or use `set WSC_PORT=...`. |
| Browser doesn't open | Open http://127.0.0.1:9000/ manually; use `--no-browser` machines without a default browser. |
| `TemplateDoesNotExist` at runtime | The templates weren't bundled — always build via `python build.py` (it passes the required `--add-data`). |
| Build fails in step 1/2 | pip/network issue — check internet access and proxy settings, then rerun `python build.py`. |

---

## 6. Security note

This tool is a **localhost, single-user developer utility**: it binds to
`127.0.0.1` only, but it has no authentication and can read any file the user
can. Never expose it to a network.

---

## 7. Development (without the exe)

```powershell
pip install -r requirements.txt
python manage.py runserver          # dev server on http://127.0.0.1:8000
```

The exe is just a frozen wrapper around the same code — see `launcher.py`
(runtime entry point) and `build.py` (build pipeline).
