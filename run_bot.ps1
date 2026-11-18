# Keeps the PAPER bot running on Windows: restarts it after a crash, 60 s later.
# Start:  powershell -ExecutionPolicy Bypass -File .\run_bot.ps1
# Stop:   close this window (or press Ctrl+C).
param([int]$RestartDelay = 60, [int]$MaxRestarts = 0)
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Python venv not found at $py - see bot\README.md"; exit 1 }
$env:PYTHONUTF8 = "1"
$n = 0
while ($true) {
    Write-Host ("{0:u}  starting paper bot (PAPER ONLY)" -f (Get-Date).ToUniversalTime())
    & $py -m bot @args
    $code = $LASTEXITCODE
    if ($code -eq 2 -or $code -eq 3) {
        Write-Host "bot stopped with code $code (config problem or live mode refused): not restarting"
        exit $code
    }
    $n++
    if ($MaxRestarts -gt 0 -and $n -ge $MaxRestarts) { exit $code }
    Write-Host ("{0:u}  bot exited with code {1}; restarting in {2}s" -f (Get-Date).ToUniversalTime(), $code, $RestartDelay)
    Start-Sleep -Seconds $RestartDelay
}
