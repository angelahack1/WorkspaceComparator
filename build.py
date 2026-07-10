# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : build.py                                                     ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
Build script for WorkSpaceComparator.exe.

Produces a single, fully self-contained Windows executable with PyInstaller:
    dist/WorkSpaceComparator.exe

The exe embeds Python, Django, the app code and the HTML templates, so it
can be copied to any Windows machine (no Python installed) and just run.

Usage (from the repository root):
    python build.py                 full build (installs deps first)
    python build.py --skip-deps     fast rebuild, skip the pip installs
    python build.py --no-verify     skip the post-build smoke test

Steps performed:
    1. pip install -r requirements.txt                       [--skip-deps]
    2. clean previous build/ and dist/ output
    3. run PyInstaller in --onefile mode against launcher.py
    4. smoke-test the exe: boot it on a spare port and verify the
       embedded UI is the CURRENT one (settings/exclusions buttons
       present), so a stale-template bundle can never ship silently.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from workspace_comparator import __version__ as APP_VERSION

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = 'WorkSpaceComparator'
ENTRY_SCRIPT = os.path.join(ROOT, 'launcher.py')

# Modules referenced only by string (settings, urls, INSTALLED_APPS,
# MIDDLEWARE, template backends...) that PyInstaller's static analysis
# cannot see. They MUST be declared explicitly.
HIDDEN_IMPORTS = [
    # Project (loaded via DJANGO_SETTINGS_MODULE / ROOT_URLCONF strings)
    'workspace_comparator',
    'workspace_comparator.settings',
    'workspace_comparator.urls',
    'workspace_comparator.wsgi',
    'comparator',
    'comparator.apps',
    'comparator.urls',
    'comparator.views',
    'comparator.services',
    'comparator.services.correspondence',
    'comparator.services.file_scanner',
    'comparator.services.binary_detect',
    'comparator.services.text_profile',
    'comparator.services.deterministic',
    'comparator.services.llm_comparator',
    'comparator.services.file_diff',
    # Django pieces referenced by string in settings.py
    'django.contrib.staticfiles',
    'django.contrib.staticfiles.apps',
    'django.middleware.security',
    'django.middleware.common',
    'django.template.backends.django',
    'django.template.context_processors',
    'django.template.loaders.app_directories',
    'django.core.servers.basehttp',
]

# Strings that MUST appear in the served index page for the bundle to be
# considered current. These are stable element IDs the Playwright suites
# also assert on (see CLAUDE.md section 7): the browse button plus the two
# feature buttons added by the engine-settings / exclusions work.
UI_MARKERS = [
    'id="btnBrowseLeft"',
    'id="btnSettings"',
    'id="btnExclusions"',
]

VERIFY_PORT = 9123             # spare port for the smoke test
VERIFY_TIMEOUT_SECONDS = 90    # onefile exes unpack before serving


def run_step(title: str, cmd: list) -> None:
    print()
    print('=' * 70)
    print(f'  {title}')
    print('=' * 70)
    print('  $ ' + ' '.join(cmd))
    print()
    subprocess.check_call(cmd, cwd=ROOT)


def _kill_process_tree(proc: 'subprocess.Popen') -> None:
    """Terminate a process AND its children.

    PyInstaller --onefile exes are a bootloader that spawns a child
    process; plain terminate() kills only the bootloader and the child
    keeps running (and keeps the exe file locked). taskkill /T gets the
    whole tree on Windows.
    """
    if os.name == 'nt':
        subprocess.call(
            ['taskkill', '/PID', str(proc.pid), '/T', '/F'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _kill_lingering_instances() -> None:
    """Stop any still-running copy of the app so dist/ can be cleaned."""
    if os.name != 'nt':
        return
    rc = subprocess.call(
        ['taskkill', '/IM', f'{APP_NAME}.exe', '/T', '/F'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc == 0:
        print(f'  stopped a running {APP_NAME}.exe instance '
              '(it was locking dist/)')


def clean_previous_output() -> None:
    print()
    print('=' * 70)
    print('  Cleaning previous build output')
    print('=' * 70)
    _kill_lingering_instances()
    for folder in ('build', 'dist'):
        path = os.path.join(ROOT, folder)
        if os.path.isdir(path):
            print(f'  removing {path}')
            shutil.rmtree(path)
    spec = os.path.join(ROOT, f'{APP_NAME}.spec')
    if os.path.isfile(spec):
        print(f'  removing {spec}')
        os.remove(spec)


def verify_exe(exe_path: str) -> bool:
    """Boot the freshly built exe and check it serves the current UI.

    Runs the exe with --no-browser on VERIFY_PORT, polls until the index
    page answers, then asserts every UI_MARKERS string is in the HTML.
    Catches the classic onefile failure mode: an exe that builds fine but
    embeds stale templates or dies on a missing hidden import.
    """
    print()
    print('=' * 70)
    print('  Step 4/4: Smoke-testing the executable')
    print('=' * 70)
    print(f'  launching {exe_path} on port {VERIFY_PORT} (no browser)')

    env = os.environ.copy()
    env['WSC_PORT'] = str(VERIFY_PORT)
    env['WSC_NO_BROWSER'] = '1'
    proc = subprocess.Popen([exe_path, '--no-browser'], cwd=ROOT, env=env)
    try:
        url = f'http://127.0.0.1:{VERIFY_PORT}/'
        html = None
        deadline = time.time() + VERIFY_TIMEOUT_SECONDS
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f'  FAIL: exe exited early with code {proc.returncode}')
                return False
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    html = resp.read().decode('utf-8', 'replace')
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        if html is None:
            print(f'  FAIL: no response on {url} '
                  f'after {VERIFY_TIMEOUT_SECONDS}s')
            return False

        missing = [m for m in UI_MARKERS if m not in html]
        if missing:
            print('  FAIL: served page is missing expected UI markers '
                  '(stale templates bundled?):')
            for m in missing:
                print(f'    - {m}')
            return False

        print(f'  OK: server answered on {url} and all '
              f'{len(UI_MARKERS)} UI markers are present.')
        return True
    finally:
        _kill_process_tree(proc)


def main() -> int:
    if sys.version_info < (3, 10):
        print('ERROR: Python 3.10+ is required (project targets 3.12).')
        return 1

    skip_deps = '--skip-deps' in sys.argv[1:]
    no_verify = '--no-verify' in sys.argv[1:]

    py = sys.executable
    print(f'Building {APP_NAME} v{APP_VERSION}')
    print(f'Using Python interpreter: {py}')

    if skip_deps:
        print('Skipping dependency installation (--skip-deps).')
    else:
        # 1. Project dependencies (for users building from a clean machine)
        run_step('Step 1/4: Installing project requirements',
                 [py, '-m', 'pip', 'install', '-r',
                  os.path.join(ROOT, 'requirements.txt')])

    # 2. Clean old artifacts so the result is reproducible
    clean_previous_output()

    # 3. Build the single-file executable
    templates_src = os.path.join(ROOT, 'comparator', 'templates')
    if not os.path.isdir(templates_src):
        print(f'ERROR: templates directory not found: {templates_src}')
        return 1
    add_data = f'{templates_src}{os.pathsep}comparator{os.sep}templates'

    cmd = [
        py, '-m', 'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onefile',                    # ONE self-contained exe
        '--name', APP_NAME,
        '--paths', ROOT,                # so project packages are importable
        '--add-data', add_data,         # inline-HTML templates (the whole UI)
        '--collect-submodules', 'django',  # string-loaded Django internals
        '--exclude-module', 'tkinter',
    ]
    for mod in HIDDEN_IMPORTS:
        cmd += ['--hidden-import', mod]
    cmd.append(ENTRY_SCRIPT)

    run_step('Step 3/4: Running PyInstaller (this takes a few minutes)', cmd)

    exe_path = os.path.join(ROOT, 'dist', f'{APP_NAME}.exe')
    if not os.path.isfile(exe_path):
        print('\nBUILD FAILED: expected output not found: ' + exe_path)
        return 1

    # 5. Prove the exe actually serves the current UI
    if no_verify:
        print('\nSkipping post-build smoke test (--no-verify).')
    elif not verify_exe(exe_path):
        print('\nBUILD FAILED: executable did not pass the smoke test.')
        return 1

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    print()
    print('=' * 70)
    print('  BUILD SUCCESSFUL')
    print('=' * 70)
    print(f'  Executable : {exe_path}')
    print(f'  Version    : {APP_VERSION}')
    print(f'  Size       : {size_mb:.1f} MB')
    print()
    print('  Copy this single file to any Windows machine and run it.')
    print('  It starts the server on http://127.0.0.1:9000/ and opens the')
    print('  default browser 5 seconds after the server is up.')
    print()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f'\nBUILD FAILED: command exited with code {exc.returncode}')
        sys.exit(exc.returncode)
