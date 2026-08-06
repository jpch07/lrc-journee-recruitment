param(
    [string]$AdminPassword = "lrcadmin",
    [int]$Port = 8001,
    [bool]$EnableTestTools = $true
)

$env:LRC_JOURNEE_ADMIN_PASSWORD = $AdminPassword
$env:LRC_JOURNEE_TEST_TOOLS = if ($EnableTestTools) { "true" } else { "false" }
$env:LRC_JOURNEE_ENV = "development"

Write-Host "LRC Journee Recruitment: http://127.0.0.1:$Port/admin"
Write-Host "Evaluation testing tools enabled: $EnableTestTools"
python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
