$ErrorActionPreference = 'Stop'
$tableDocPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\第三问优化结果_表1表2表3.docx'))
$tablePdfPath = Join-Path $PSScriptRoot 'word_render.pdf'
$tableWord = $null
$tableDocument = $null
try {
    $tableWord = New-Object -ComObject Word.Application
    $tableWord.Visible = $false
    $tableWord.DisplayAlerts = 0
    $tableDocument = $tableWord.Documents.Open($tableDocPath, $false, $true)
    $tableDocument.ExportAsFixedFormat($tablePdfPath, 17)
    Write-Output $tablePdfPath
} finally {
    if ($null -ne $tableDocument) { $tableDocument.Close(0) }
    if ($null -ne $tableWord) { $tableWord.Quit(0) }
}
