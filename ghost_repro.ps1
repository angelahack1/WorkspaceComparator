# ============================================================================
#  ghost_repro.ps1 — Reproduce the "ghost file" IDE bug, with evidence.
#
#  WHAT IT PROVES: whether an IDE keeps showing a DELETED old version of a
#  markdown file (in the preview / breadcrumb / outline) after the file is
#  deleted and recreated from OUTSIDE the editor — the exact thing that
#  happened with README.md on 2026-07-03.
#
#  HOW TO USE (run it TWICE — once per IDE — for an A/B verdict):
#    1. Run:  .\ghost_repro.ps1 -Ide Antigravity
#    2. When prompted, open C:\Development\GhostReproLab\GHOST_TEST.md in
#       Antigravity, open its markdown preview (Ctrl+Shift+V), keep both
#       visible, then press Enter in this console.
#    3. The script deletes/recreates the file 5 times from outside the IDE.
#    4. VERDICT: if the preview/breadcrumb still shows "VERSION 1 ... OLD
#       DOCUMENT" (or any version but the FINAL TRUTH), that IDE has the bug.
#       Take a screenshot BEFORE closing anything.
#    5. Repeat with stock VS Code:  .\ghost_repro.ps1 -Ide VSCode
#       (ideally launched as:  code --disable-extensions)
#
#    Antigravity FAILS + VS Code PASSES  -> Antigravity bug (report to Google)
#    Both FAIL                           -> upstream VS Code bug (report to
#                                           github.com/microsoft/vscode too)
#
#  Every write is logged with timestamp + SHA256 to ghost_repro_log.txt —
#  attach that log and your screenshots to the bug report.
# ============================================================================
param([string]$Ide = 'UnnamedIDE')

$Lab = 'C:\Development\GhostReproLab'
$Md = Join-Path $Lab 'GHOST_TEST.md'
$Log = Join-Path $Lab 'ghost_repro_log.txt'

New-Item -ItemType Directory -Force $Lab | Out-Null

function Write-Version([int]$n, [string]$title, [string]$body) {
    $content = "# VERSION $n — $title`n`n$body`n"
    Set-Content -Path $Md -Value $content -Encoding utf8
    $hash = (Get-FileHash $Md -Algorithm SHA256).Hash.Substring(0, 12)
    $line = "{0}  wrote VERSION {1}  sha256={2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $n, $hash
    Add-Content -Path $Log -Value $line
    Write-Host "  $line"
}

Add-Content $Log ("`n===== RUN {0}  IDE UNDER TEST: {1} =====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Ide)

Write-Host ''
Write-Host "GHOST-FILE REPRODUCTION — IDE under test: $Ide" -ForegroundColor Cyan
Write-Host '----------------------------------------------------------------'
Write-Version 1 'OLD DOCUMENT (this must DISAPPEAR)' `
    'If you can still read this after the test finishes, your IDE is showing a GHOST of a deleted file.'

Write-Host ''
Write-Host "STEP 1: Open $Md in $Ide" -ForegroundColor Yellow
Write-Host '        Open its markdown preview (Ctrl+Shift+V). Keep it visible.'
Read-Host  '        Then press Enter here to start the churn'

# Delete/recreate cycles from OUTSIDE the IDE — the trigger sequence.
for ($i = 2; $i -le 5; $i++) {
    Remove-Item $Md -Force
    Add-Content $Log ("{0}  deleted file" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))
    Start-Sleep -Seconds 2
    Write-Version $i "INTERMEDIATE ($i of 5)" 'Churning: deleted and recreated from outside the editor.'
    Start-Sleep -Seconds 2
}

Remove-Item $Md -Force
Start-Sleep -Seconds 2
Write-Version 6 'FINAL TRUTH ✔' `
    ('THIS is the only content that exists on disk. IDE under test: ' + $Ide + '. If the preview, breadcrumb or outline shows ANY other version, that IDE is displaying a ghost from its internal cache/backups. Screenshot it NOW.')

Write-Host ''
Write-Host 'STEP 2: LOOK AT THE IDE NOW.' -ForegroundColor Yellow
Write-Host '  PASS  = preview + breadcrumb show "VERSION 6 — FINAL TRUTH"'
Write-Host '  FAIL  = any older version is still visible anywhere -> BUG. Screenshot it!'
Write-Host ''
Write-Host ("Evidence log: " + $Log) -ForegroundColor Cyan
Write-Host 'Disk truth right now:' -ForegroundColor Cyan
Get-Content $Md | ForEach-Object { "    | $_" }
