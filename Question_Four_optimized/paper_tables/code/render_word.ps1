$ErrorActionPreference = 'Stop'
$tableRoot = Split-Path -Parent $PSScriptRoot
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$word.AutomationSecurity = 3
try {
    foreach ($strategy in @('4-2', '4-3')) {
        $sourcePath = Join-Path $tableRoot "第四问_${strategy}_指定日期表1表2表3.docx"
        $previewFolder = Join-Path $tableRoot "_qa/$strategy/word"
        New-Item -ItemType Directory -Path $previewFolder -Force | Out-Null
        $pdfPath = Join-Path $previewFolder 'render.pdf'
        $document = $word.Documents.Open($sourcePath, $false, $true)
        try {
            $document.ExportAsFixedFormat($pdfPath, 17)
            $pages = $document.ComputeStatistics(2)
            Write-Output "Word render completed: $strategy, $pages pages"
        } finally {
            $document.Close(0)
            [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) | Out-Null
        }
        Get-ChildItem -LiteralPath $previewFolder -Filter 'page-*.png' -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
        & 'C:/Users/31293/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe' -r 140 -png $pdfPath (Join-Path $previewFolder 'page')
        if ($LASTEXITCODE -ne 0) { throw "Poppler rendering failed for $strategy" }
    }
} finally {
    $word.Quit(0)
    [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) | Out-Null
}
