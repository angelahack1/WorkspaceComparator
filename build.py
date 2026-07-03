"""
Build script for WorkSpaceComparator.exe.

Produces a single, fully self-contained Windows executable with PyInstaller:
    dist/WorkSpaceComparator.exe

The exe embeds Python, Django, the app code and the HTML templates, so it
can be copied to any Windows machine (no Python installed) and just run.

Usage (from the repository root):
    python build.py

Steps performed:
    1. pip install -r requirements.txt   (django, requests)
    2. pip install pyinstaller
    3. clean previous build/ and dist/ output
    4. run PyInstaller in --onefile mode against launcher.py
"""
import os
import shutil
import subprocess
import sys

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


def run_step(title: str, cmd: list) -> None:
    print()
    print('=' * 70)
    print(f'  {title}')
    print('=' * 70)
    print('  $ ' + ' '.join(cmd))
    print()
    subprocess.check_call(cmd, cwd=ROOT)


def clean_previous_output() -> None:
    print()
    print('=' * 70)
    print('  Cleaning previous build output')
    print('=' * 70)
    for folder in ('build', 'dist'):
        path = os.path.join(ROOT, folder)
        if os.path.isdir(path):
            print(f'  removing {path}')
            shutil.rmtree(path)
    spec = os.path.join(ROOT, f'{APP_NAME}.spec')
    if os.path.isfile(spec):
        print(f'  removing {spec}')
        os.remove(spec)


def main() -> int:
    if sys.version_info < (3, 10):
        print('ERROR: Python 3.10+ is required (project targets 3.12).')
        return 1

    py = sys.executable
    print(f'Using Python interpreter: {py}')

    # 1. Project dependencies (for users building from a clean machine)
    run_step('Step 1/4: Installing project requirements',
             [py, '-m', 'pip', 'install', '-r',
              os.path.join(ROOT, 'requirements.txt')])

    # 2. PyInstaller itself
    run_step('Step 2/4: Installing PyInstaller',
             [py, '-m', 'pip', 'install', 'pyinstaller'])

    # 3. Clean old artifacts so the result is reproducible
    clean_previous_output()

    # 4. Build the single-file executable
    templates_src = os.path.join(ROOT, 'comparator', 'templates')
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

    run_step('Step 4/4: Running PyInstaller (this takes a few minutes)', cmd)

    exe_path = os.path.join(ROOT, 'dist', f'{APP_NAME}.exe')
    if not os.path.isfile(exe_path):
        print('\nBUILD FAILED: expected output not found: ' + exe_path)
        return 1

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    print()
    print('=' * 70)
    print('  BUILD SUCCESSFUL')
    print('=' * 70)
    print(f'  Executable : {exe_path}')
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
