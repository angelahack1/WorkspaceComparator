# ============================================================================
#  truth.ps1 - "What is REALLY in this repo?"  (bypasses any IDE entirely)
#
#  The IDE can lie (stale previews, ghost buffers). Disk and git cannot.
#
#  Usage, from the repo root:
#      .\truth.ps1                # repo overview: HEAD, status, recent commits
#      .\truth.ps1 README.md      # + show that file's REAL content from disk
#                                 #   and its hash vs the committed version
# ============================================================================
param([string]$File)

Write-Host ''
Write-Host '==================== GROUND TRUTH ====================' -ForegroundColor Cyan
Write-Host ("  Repo    : " + (git rev-parse --show-toplevel))
Write-Host ("  HEAD    : " + (git log -1 --format='%h  %s'))
Write-Host ("  Branch  : " + (git branch --show-current))
Write-Host ''
Write-Host '  Working tree vs last commit:' -ForegroundColor Cyan
$status = git status --short
if ($status) { $status | ForEach-Object { "    $_" } } else { '    clean - disk matches the last commit exactly' }

if ($File) {
    Write-Host ''
    Write-Host ("==================== FILE: $File ====================") -ForegroundColor Cyan
    if (-not (Test-Path $File)) {
        Write-Host '  DOES NOT EXIST on disk.' -ForegroundColor Red
    } else {
        $diskHash = (Get-FileHash $File -Algorithm SHA256).Hash.Substring(0, 12)
        $gitDiff = git diff HEAD -- $File
        Write-Host ("  Disk SHA256 (first 12): " + $diskHash)
        if ($gitDiff) {
            Write-Host '  Differs from last commit - uncommitted changes below:' -ForegroundColor Yellow
            $gitDiff | Select-Object -First 40 | ForEach-Object { "    $_" }
        } else {
            Write-Host '  IDENTICAL to the committed version.' -ForegroundColor Green
        }
        Write-Host ''
        Write-Host '  First 15 lines as they exist ON DISK right now:' -ForegroundColor Cyan
        Get-Content $File -TotalCount 15 | ForEach-Object { "    | $_" }
    }
}
Write-Host ''
Write-Host 'If the IDE shows something different from the above, the IDE is stale:' -ForegroundColor Cyan
Write-Host 'press Ctrl+Shift+P -> "Developer: Reload Window".'
Write-Host ''
