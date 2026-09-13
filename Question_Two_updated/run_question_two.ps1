param(
    [double]$EmergencyBudgetYuan = 1000000.0,
    [double]$MipGap = 1e-6,
    [double]$TimeLimit = 30.0,
    [Nullable[int]]$MaxDays = $null,
    [string]$SolverPython = "",
    [string]$ExcelPython = "",
    [string]$NodeExecutable = "",
    [switch]$SkipBoundaryTest,
    [switch]$SkipBenchmarks
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONIOENCODING = "utf-8"
$QuestionDir = $PSScriptRoot
$CodeDir = Join-Path $QuestionDir "code"
$DataDir = Join-Path $QuestionDir "data"
$OutputDir = Join-Path $QuestionDir "output"
if ($null -ne $MaxDays) {
    if ([int]$MaxDays -lt 32 -or [int]$MaxDays -gt 365) {
        throw "MaxDays must be between 32 and 365."
    }
    if ([int]$MaxDays -lt 365) {
        $OutputDir = Join-Path $OutputDir ("debug_" + [string][int]$MaxDays)
    }
}

$RuntimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
if (-not $SolverPython) { $SolverPython = (Get-Command python -ErrorAction Stop).Source }
if (-not $ExcelPython) {
    $ExcelPython = Join-Path $RuntimeRoot "python\python.exe"
    if (-not (Test-Path -LiteralPath $ExcelPython)) { $ExcelPython = $SolverPython }
}
& $SolverPython -c "import numpy, scipy.optimize"
if ($LASTEXITCODE -ne 0) { throw "SolverPython needs numpy and scipy; see requirements.txt." }
& $ExcelPython -c "import numpy, openpyxl"
if ($LASTEXITCODE -ne 0) { throw "ExcelPython needs numpy and openpyxl; see requirements.txt." }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$InputJson = Join-Path $OutputDir "input_data.json"
$SolutionJson = Join-Path $OutputDir "question_two_solution.json"
$DailyCsv = Join-Path $OutputDir "question_two_daily.csv"
$DetailCsv = Join-Path $OutputDir "question_two_detail.csv"
$ResultXlsx = Join-Path $OutputDir "result2.xlsx"
$TemplateXlsx = Join-Path $DataDir "result2_template.xlsx"
$PreviewDir = Join-Path $OutputDir "previews"
$BenchmarkDir = Join-Path $OutputDir "benchmarks"

& $ExcelPython (Join-Path $CodeDir "extract_inputs.py") `
    --attachment1 (Join-Path $DataDir "附件1.xlsx") `
    --attachment2 (Join-Path $DataDir "附件2.xlsx") `
    --template $TemplateXlsx --output $InputJson
if ($LASTEXITCODE -ne 0) { throw "Input extraction failed." }

$SolverArgs = @(
    (Join-Path $CodeDir "solve_question_two.py"),
    "--input", $InputJson, "--output-json", $SolutionJson,
    "--daily-csv", $DailyCsv, "--detail-csv", $DetailCsv,
    "--parameter-mode", "causal_monthly", "--emergency-budget-yuan", $EmergencyBudgetYuan,
    "--mip-gap", $MipGap, "--time-limit", $TimeLimit,
    "--eta-charge", 0.9, "--eta-discharge", 0.9,
    "--purchase-strategy", "calibrated_quantile", "--forecast-method", "calendar_trend",
    "--risk-grouping", "all", "--reserve-mode", "positive_steps",
    "--quantile-method", "linear", "--objective-mode", "lexicographic"
)
if ($null -ne $MaxDays) { $SolverArgs += @("--max-days", [int]$MaxDays) }
& $SolverPython @SolverArgs
if ($LASTEXITCODE -ne 0) { throw "Rolling optimization failed." }
if ($null -ne $MaxDays -and [int]$MaxDays -lt 365) {
    Write-Host "Debug run complete: $OutputDir"
    exit 0
}

if (-not $NodeExecutable) {
    $NodeExecutable = Join-Path $RuntimeRoot "node\bin\node.exe"
    if (-not (Test-Path -LiteralPath $NodeExecutable)) { $NodeExecutable = (Get-Command node -ErrorAction Stop).Source }
}
$LocalNodeModules = Join-Path $QuestionDir "node_modules"
if (-not (Test-Path -LiteralPath $LocalNodeModules)) {
    $BundledNodeModules = Join-Path $RuntimeRoot "node\node_modules"
    if (-not (Test-Path -LiteralPath $BundledNodeModules)) {
        throw "Workbook generation needs @oai/artifact-tool; see docs/运行环境.md."
    }
    New-Item -ItemType Junction -Path $LocalNodeModules -Target $BundledNodeModules | Out-Null
}
New-Item -ItemType Directory -Force -Path $PreviewDir | Out-Null
& $NodeExecutable (Join-Path $CodeDir "build_result2.mjs") $TemplateXlsx $SolutionJson $ResultXlsx $PreviewDir
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-Path -LiteralPath $ResultXlsx)) { throw "Workbook generation failed." }
    Write-Warning "Workbook saved; Node returned a nonzero exit code. Independent verification follows."
}

& $ExcelPython (Join-Path $CodeDir "verify_question_two.py") `
    --workbook $ResultXlsx --solution $SolutionJson --input $InputJson `
    --daily $DailyCsv --detail $DetailCsv --report (Join-Path $OutputDir "verification_report.json")
if ($LASTEXITCODE -ne 0) { throw "Independent verification failed." }
& $SolverPython (Join-Path $CodeDir "generate_paper_tables.py") `
    --solution $SolutionJson --detail $DetailCsv --output-dir (Join-Path $OutputDir "paper_tables")
if ($LASTEXITCODE -ne 0) { throw "Paper tables failed." }

if (-not $SkipBoundaryTest) {
    foreach ($TargetIndex in @(31, 151)) {
        $BoundaryFile = if ($TargetIndex -eq 31) { "information_boundary_test.json" } else { "information_boundary_test_later.json" }
        & $SolverPython (Join-Path $CodeDir "test_information_boundary.py") `
            --input $InputJson --solution $SolutionJson --target-index $TargetIndex `
            --report (Join-Path $OutputDir $BoundaryFile) --mip-gap $MipGap --time-limit $TimeLimit
        if ($LASTEXITCODE -ne 0) { throw "Information boundary test failed at index $TargetIndex." }
    }
}
if (-not $SkipBenchmarks) {
    & $SolverPython (Join-Path $CodeDir "perfect_information_benchmark.py") `
        --input $InputJson --daily-csv $DailyCsv --main-json $SolutionJson `
        --output-dir $BenchmarkDir --mip-gap $MipGap --time-limit 300
    if ($LASTEXITCODE -ne 0) { throw "Offline benchmark failed." }
}
if (-not $SkipBenchmarks -and -not $SkipBoundaryTest) {
    & $SolverPython (Join-Path $CodeDir "build_result_summary.py") --output-dir $OutputDir
    if ($LASTEXITCODE -ne 0) { throw "Summary generation failed." }
    & $SolverPython (Join-Path $CodeDir "verify_analysis_outputs.py") --output-dir $OutputDir
    if ($LASTEXITCODE -ne 0) { throw "Analysis verification failed." }
}
Write-Host "Completed: $OutputDir"
