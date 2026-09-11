$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$candidates = @(
    (Join-Path $projectRoot "work\venv_milp\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
    "python"
)
$python = $null
foreach ($candidate in $candidates) {
    if ($candidate -ne "python" -and -not (Test-Path -LiteralPath $candidate)) {
        continue
    }
    if ($candidate -eq "python" -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
        continue
    }
    & $candidate -c "import numpy, pandas, openpyxl" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if ($null -eq $python) {
    throw "未找到安装有NumPy、pandas和openpyxl的Python环境。"
}

& $python (Join-Path $PSScriptRoot "solve_question_one.py") `
    --method milp `
    --objective-mode cost `
    --output (Join-Path $PSScriptRoot "output\result1_milp.xlsx") `
    --detail (Join-Path $PSScriptRoot "output\question_one_detail_milp.csv")
exit $LASTEXITCODE
