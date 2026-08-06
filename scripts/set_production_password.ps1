$ErrorActionPreference = "Stop"
$hashPath = Join-Path $env:TEMP "lrc-journee-production-admin.hash"
$markerPath = Join-Path $env:TEMP "lrc-journee-hash-path.txt"
Set-Content -LiteralPath $markerPath -Value $hashPath
if (Test-Path -LiteralPath $hashPath) {
    Remove-Item -LiteralPath $hashPath -Force
}

python (Join-Path $PSScriptRoot "hash_password.py") --output $hashPath
if ($LASTEXITCODE -ne 0) {
    throw "Password generation failed."
}

Write-Host ""
Write-Host "Password accepted and securely hashed." -ForegroundColor Green
Write-Host "You can close this window." -ForegroundColor Green
