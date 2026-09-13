param(
    [ValidateSet("causal_monthly", "fixed")]
    [string]$ParameterMode = "causal_monthly",
    [double]$EmergencyBudgetYuan = 1000000.0,
    [double]$MipGap = 1e-6,
    [double]$TimeLimit = 30.0,
    [Nullable[int]]$MaxDays = $null,
    [double]$EtaCharge = 0.9,
    [double]$EtaDischarge = 0.9,
    [double]$ReserveQuantile = 0.75,
    [ValidateSet("legacy_scenarios", "calibrated_quantile")]
    [string]$PurchaseStrategy = "calibrated_quantile",
    [int]$RiskWindowDays = 28,
    [int]$RiskRadiusPeriods = 3,
    [double]$PurchaseQuantile = 0.85,
    [ValidateSet("similar_day", "calendar_trend")]
    [string]$ForecastMethod = "calendar_trend",
    [int]$LoadWindowDays = 28,
    [ValidateSet(1, 2)]
    [int]$LoadTrendDegree = 2,
    [int]$PvWindowDays = 14,
    [ValidateSet("all", "legacy")]
    [string]$RiskGrouping = "all",
    [ValidateSet("positive_steps", "cumulative_net")]
    [string]$ReserveMode = "positive_steps",
    [ValidateSet("linear", "higher", "lower", "nearest", "midpoint")]
    [string]$QuantileMethod = "linear",
    [ValidateSet("cost", "lexicographic")]
    [string]$ObjectiveMode = "lexicographic",
    [switch]$SkipBoundaryTest,
    [switch]$SkipComparisons,
    [switch]$SkipBenchmarks
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONIOENCODING = "utf-8"
$QuestionDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $QuestionDir
$CodeDir = Join-Path $QuestionDir "code"
$OutputDir = Join-Path $QuestionDir "output"
if ($null -ne $MaxDays -and [int]$MaxDays -lt 365) {
    $OutputDir = Join-Path $OutputDir ("debug_" + [string][int]$MaxDays)
}
$PreviewDir = Join-Path $OutputDir "previews"
$ComparisonDir = Join-Path $OutputDir "comparisons"
$BenchmarkDir = Join-Path $OutputDir "benchmarks"
$PaperTableDir = Join-Path $OutputDir "paper_tables"

$RuntimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
$BundledPython = Join-Path $RuntimeRoot "python\python.exe"
$BundledNode = Join-Path $RuntimeRoot "node\bin\node.exe"
$BundledNodeModules = Join-Path $RuntimeRoot "node\node_modules"
if (-not (Test-Path -LiteralPath $BundledPython)) {
    $BundledPython = (Get-Command python -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $BundledNode)) {
    $BundledNode = (Get-Command node -ErrorAction Stop).Source
}
$SolverPython = (Get-Command python -ErrorAction Stop).Source
$LocalNodeModules = Join-Path $QuestionDir "node_modules"

New-Item -ItemType Directory -Force -Path $OutputDir, $PreviewDir | Out-Null
if (-not (Test-Path -LiteralPath $LocalNodeModules)) {
    if (-not (Test-Path -LiteralPath $BundledNodeModules)) {
        throw "找不到artifact-tool依赖目录: $BundledNodeModules"
    }
    New-Item -ItemType Junction -Path $LocalNodeModules -Target $BundledNodeModules | Out-Null
}

$InputJson = Join-Path $OutputDir "input_data.json"
$SolutionJson = Join-Path $OutputDir "question_two_solution.json"
$DailyCsv = Join-Path $OutputDir "question_two_daily.csv"
$DetailCsv = Join-Path $OutputDir "question_two_detail.csv"
$ResultXlsx = Join-Path $OutputDir "result2.xlsx"
$VerifyReport = Join-Path $OutputDir "verification_report.json"

& $BundledPython (Join-Path $CodeDir "extract_inputs.py") `
    --attachment1 (Join-Path $RootDir "C题\附件\附件1.xlsx") `
    --attachment2 (Join-Path $RootDir "C题\附件\附件2.xlsx") `
    --template (Join-Path $RootDir "C题\附件\附件5\result2.xlsx") `
    --output $InputJson
if ($LASTEXITCODE -ne 0) { throw "输入提取失败，退出码: $LASTEXITCODE" }

$SolverArgs = @(
    (Join-Path $CodeDir "solve_question_two.py"),
    "--input", $InputJson,
    "--output-json", $SolutionJson,
    "--daily-csv", $DailyCsv,
    "--detail-csv", $DetailCsv,
    "--mip-gap", $MipGap,
    "--time-limit", $TimeLimit,
    "--eta-charge", $EtaCharge,
    "--eta-discharge", $EtaDischarge,
    "--reserve-quantile", $ReserveQuantile,
    "--reserve-mode", $ReserveMode,
    "--quantile-method", $QuantileMethod,
    "--objective-mode", $ObjectiveMode,
    "--purchase-strategy", $PurchaseStrategy,
    "--risk-window-days", $RiskWindowDays,
    "--risk-radius-periods", $RiskRadiusPeriods,
    "--purchase-quantile", $PurchaseQuantile,
    "--forecast-method", $ForecastMethod,
    "--load-window-days", $LoadWindowDays,
    "--load-trend-degree", $LoadTrendDegree,
    "--pv-window-days", $PvWindowDays,
    "--risk-grouping", $RiskGrouping
)
$SolverArgs += @("--parameter-mode", $ParameterMode, "--emergency-budget-yuan", $EmergencyBudgetYuan)
if ($null -ne $MaxDays) {
    $SolverArgs += @("--max-days", [int]$MaxDays)
}
& $SolverPython @SolverArgs
if ($LASTEXITCODE -ne 0) { throw "滚动MILP求解失败，退出码: $LASTEXITCODE" }

if ($null -ne $MaxDays -and [int]$MaxDays -lt 365) {
    Write-Host "已完成小样本求解；正式工作簿和离线分析需要完整365天，故本次不生成。"
    exit 0
}

& $BundledNode (Join-Path $CodeDir "build_result2.mjs") `
    (Join-Path $RootDir "C题\附件\附件5\result2.xlsx") `
    $SolutionJson `
    $ResultXlsx `
    $PreviewDir
$WorkbookBuilderExitCode = $LASTEXITCODE
if ($WorkbookBuilderExitCode -ne 0) {
    if (-not (Test-Path -LiteralPath $ResultXlsx)) {
        throw "result2.xlsx生成失败，退出码: $WorkbookBuilderExitCode"
    }
    Write-Warning (
        "工作簿已保存，但Node进程在退出阶段返回$WorkbookBuilderExitCode；" +
        "继续执行独立逐项核验，核验不通过时仍会终止流程。"
    )
}

& $BundledPython (Join-Path $CodeDir "verify_question_two.py") `
    --workbook $ResultXlsx `
    --solution $SolutionJson `
    --input $InputJson `
    --daily $DailyCsv `
    --detail $DetailCsv `
    --report $VerifyReport
if ($LASTEXITCODE -ne 0) { throw "独立结果核验失败，退出码: $LASTEXITCODE" }

& $SolverPython (Join-Path $CodeDir "generate_paper_tables.py") `
    --solution $SolutionJson `
    --detail $DetailCsv `
    --output-dir $PaperTableDir
if ($LASTEXITCODE -ne 0) { throw "指定日期论文表生成失败，退出码: $LASTEXITCODE" }

if (-not $SkipBoundaryTest) {
    & $SolverPython (Join-Path $CodeDir "test_information_boundary.py") `
        --input $InputJson `
        --solution $SolutionJson `
        --target-index 31 `
        --report (Join-Path $OutputDir "information_boundary_test.json") `
        --mip-gap $MipGap `
        --time-limit $TimeLimit
    if ($LASTEXITCODE -ne 0) { throw "信息边界扰动测试失败，退出码: $LASTEXITCODE" }
    if ($ParameterMode -eq "causal_monthly") {
        & $SolverPython (Join-Path $CodeDir "test_information_boundary.py") `
            --input $InputJson --solution $SolutionJson --target-index 151 `
            --report (Join-Path $OutputDir "information_boundary_test_later.json") `
            --mip-gap $MipGap --time-limit $TimeLimit
        if ($LASTEXITCODE -ne 0) { throw "后续月份调参信息边界测试失败，退出码: $LASTEXITCODE" }
    }
}

if (-not $SkipComparisons -and $ParameterMode -eq "fixed") {
    & $SolverPython (Join-Path $CodeDir "run_model_comparisons.py") `
        --input $InputJson `
        --output-dir $ComparisonDir `
        --mip-gap $MipGap `
        --time-limit $TimeLimit `
        --objective-mode lexicographic `
        --purchase-strategy $PurchaseStrategy `
        --risk-window-days $RiskWindowDays `
        --risk-radius-periods $RiskRadiusPeriods `
        --purchase-quantile $PurchaseQuantile `
        --forecast-method $ForecastMethod `
        --load-window-days $LoadWindowDays `
        --load-trend-degree $LoadTrendDegree `
        --pv-window-days $PvWindowDays `
        --risk-grouping $RiskGrouping
    if ($LASTEXITCODE -ne 0) { throw "安全储备与效率对照失败，退出码: $LASTEXITCODE" }
}

if (-not $SkipBenchmarks) {
    & $SolverPython (Join-Path $CodeDir "perfect_information_benchmark.py") `
        --input $InputJson `
        --daily-csv $DailyCsv `
        --main-json $SolutionJson `
        --output-dir $BenchmarkDir `
        --mip-gap $MipGap `
        --time-limit 300
    if ($LASTEXITCODE -ne 0) { throw "完美信息基准求解失败，退出码: $LASTEXITCODE" }
}

if (-not $SkipBenchmarks -and -not $SkipBoundaryTest -and ($ParameterMode -eq "causal_monthly" -or -not $SkipComparisons)) {
    & $SolverPython (Join-Path $CodeDir "build_result_summary.py")
    if ($LASTEXITCODE -ne 0) { throw "最终摘要生成失败，退出码: $LASTEXITCODE" }
    & $SolverPython (Join-Path $CodeDir "verify_analysis_outputs.py") `
        --output-dir $OutputDir `
        --report (Join-Path $OutputDir "analysis_verification.json")
    if ($LASTEXITCODE -ne 0) { throw "附加分析核验失败，退出码: $LASTEXITCODE" }
}

Write-Host "第二问主结果、核验报告和分析结果已保存到: $OutputDir"
