param(
    [double]$MipGap = 1e-6,
    [double]$TimeLimit = 30.0,
    [Nullable[int]]$MaxDays = $null
)

$ErrorActionPreference = "Stop"
$QuestionDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $QuestionDir
$OutputDir = Join-Path $QuestionDir "output"
$PreviewDir = Join-Path $OutputDir "previews"
$BundledPython = "C:\Users\31293\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$BundledNode = "C:\Users\31293\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$BundledNodeModules = "C:\Users\31293\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
$LocalNodeModules = Join-Path $QuestionDir "node_modules"

New-Item -ItemType Directory -Force -Path $OutputDir, $PreviewDir | Out-Null
if (-not (Test-Path -LiteralPath $LocalNodeModules)) {
    New-Item -ItemType Junction -Path $LocalNodeModules -Target $BundledNodeModules | Out-Null
}

$InputJson = Join-Path $OutputDir "input_data.json"
$SolutionJson = Join-Path $OutputDir "question_two_solution.json"
$DailyCsv = Join-Path $OutputDir "question_two_daily.csv"
$DetailCsv = Join-Path $OutputDir "question_two_detail.csv"
$ResultXlsx = Join-Path $OutputDir "result2.xlsx"
$VerifyReport = Join-Path $OutputDir "verification_report.json"

& $BundledPython (Join-Path $QuestionDir "extract_inputs.py") `
    --attachment1 (Join-Path $RootDir "C题\附件\附件1.xlsx") `
    --attachment2 (Join-Path $RootDir "C题\附件\附件2.xlsx") `
    --template (Join-Path $RootDir "C题\附件\附件5\result2.xlsx") `
    --output $InputJson

$SolverArgs = @(
    (Join-Path $QuestionDir "solve_question_two.py"),
    "--input", $InputJson,
    "--output-json", $SolutionJson,
    "--daily-csv", $DailyCsv,
    "--detail-csv", $DetailCsv,
    "--mip-gap", $MipGap,
    "--time-limit", $TimeLimit
)
if ($null -ne $MaxDays) {
    $SolverArgs += @("--max-days", $MaxDays.Value)
}
& python @SolverArgs

if ($null -ne $MaxDays -and $MaxDays.Value -lt 365) {
    Write-Host "已完成小样本求解；未生成正式result2.xlsx（正式工作簿需要完整365天）。"
    exit 0
}

& $BundledNode (Join-Path $QuestionDir "build_result2.mjs") `
    (Join-Path $RootDir "C题\附件\附件5\result2.xlsx") `
    $SolutionJson `
    $ResultXlsx `
    $PreviewDir

& $BundledPython (Join-Path $QuestionDir "verify_question_two.py") `
    --workbook $ResultXlsx `
    --solution $SolutionJson `
    --report $VerifyReport

Write-Host "第二问全部结果已保存到: $OutputDir"
