<div align="center">

# 🔍 Workspace Comparator

### *"We migrated project A into project B… which files are actually the same?"*

A local, zero-cloud web tool that finds the **file correspondences** between two source-code
workspaces — surviving renames, moves and whole build-system migrations — with a
Beyond Compare-style diff viewer and an optional local **AI referee**. 🧠

<p align="center">
  <img src="https://img.shields.io/badge/VERSION-V1.3.0-4b8bf5?style=for-the-badge&labelColor=2b2d31" alt="Version v1.3.0">
  <img src="https://img.shields.io/badge/PYTHON-3.12.10-3e6e9e?style=for-the-badge&labelColor=2b2d31&logo=python&logoColor=white" alt="Python 3.12.10">
  <img src="https://img.shields.io/badge/DJANGO-5.2.15-43a047?style=for-the-badge&labelColor=2b2d31&logo=django&logoColor=white" alt="Django 5.2.15">
  <img src="https://img.shields.io/badge/PLATFORM-WIN%2010%20%7C%2011-3e78c2?style=for-the-badge&labelColor=2b2d31&logo=windows&logoColor=white" alt="Platform Windows 10 | 11">
</p>
<p align="center">
  <img src="https://img.shields.io/badge/PYINSTALLER-6.18.0-9c27e0?style=for-the-badge&labelColor=2b2d31" alt="PyInstaller 6.18.0">
  <img src="https://img.shields.io/badge/AI%20ENGINE-OLLAMA%20%C2%B7%20GLM--5.2-43a047?style=for-the-badge&labelColor=2b2d31" alt="AI engine: Ollama · glm-5.2:cloud (optional)">
  <img src="https://img.shields.io/badge/LICENSE-MIT-4b8bf5?style=for-the-badge&labelColor=2b2d31" alt="License MIT">
</p>

<img src="docs/screenshots/02-results.png" width="100%" alt="Comparison results: matched files joined in green with ==/~=/!= pills, AI-Matched and Renamed labels, unmatched files in red">

</div>

## ✨ What you get

- 🧩 **4-phase matching engine** — exact path → same name → fuzzy name → pure content. It catches files that were **renamed** (`StringHelper.java` → `TextUtils.java`) or **moved** (`src/` → `src/main/java/`).
- 🏷️ **Honest pills** on every match: `==` identical · `~=` only comments/whitespace changed · `!=` really different.
- 🤖 **AI-arbitrated matches** *(optional)* — ambiguous pairs go to a **local** Ollama model. No Ollama running? The tool simply falls back to pure heuristics. Nothing ever leaves your machine.
- 🔢 **Binary files, compared in hex** — icons, jars, keystores and other binary artifacts are matched by **exact filename** (the directory path is the tie-break clue; the AI is *never* asked to judge bytes) and open in a colored, `hexdump -C`-style side-by-side viewer with per-byte change highlighting. A **HEX** switch lets you hex-view text files too — for true binaries it's locked on.
- ⚙️ **Settings** & 🚫 **Exclusions** dialogs to tune the engine and skip noise, right from the UI.

## 🚀 Get it — the easy way

Go to **[Releases](../../releases)** → download **`WorkSpaceComparator.exe`** → double-click. Done. 💅

No Python, no pip, no installer — one self-contained file. It starts a private server and
opens your browser at `http://127.0.0.1:9000/` all by itself. Close the console window when
you're finished.

## 🖱️ How to use it

**1.** Pick your two folders (type the paths or browse 📁) and hit **Compare**.
**2.** Read the verdict: green joined rows are correspondences, red rows have no counterpart.
**3.** **Double-click any row** to open the side-by-side diff viewer:

<div align="center">
<img src="docs/screenshots/04-diff-viewer.png" width="100%" alt="Beyond Compare-style diff viewer: content-aligned rows, word-level change highlights, hatched gaps, minimap and context folding">
</div>

Corresponding lines **face each other** even when line numbers drift, changed **words** are
highlighted inside the line, unchanged runs fold away, and the minimap gives you the whole
file at a glance. Tune the engine anytime with the ⚙ Settings dialog:

<div align="center">
<img src="docs/screenshots/03-settings.png" width="70%" alt="Engine Settings dialog with the four tunable matching thresholds">
</div>

> 🧪 **Try it right now** — the repo ships a tiny demo migration: compare
> `demo/InvoicerClassic` against `demo/InvoicerMaven` and watch every match type appear —
> including a pair of binary logos that light up the **BIN** tag and the hex viewer.

## 🛠️ Run from source

```powershell
pip install -r requirements.txt
python manage.py runserver        # → http://127.0.0.1:8000
```

Optional AI referee: `ollama serve` with the `glm-5.2:cloud` model pulled.

## 📦 Build the release exe

```powershell
python build.py                   # full build → dist/WorkSpaceComparator.exe
python build.py --skip-deps       # faster rebuild
```

The script runs PyInstaller in one-file mode, then **smoke-tests the exe** — boots it and
verifies the embedded UI is current — before declaring victory. 🏁 Upload the result to a
GitHub Release and your users are one double-click away.

---

<div align="center">

Made with 💜 by **Ángela López Mendoza** · 📧 [angela@xaiht.org](mailto:angela@xaiht.org)

*MIT licensed — compare boldly, migrate fearlessly.*

</div>
