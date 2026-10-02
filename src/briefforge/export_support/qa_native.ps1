param(
    [Parameter(Mandatory=$true)][string]$Docx,
    [Parameter(Mandatory=$true)][string]$Pptx,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$docxInput = (Resolve-Path -LiteralPath $Docx).Path
$pptxInput = (Resolve-Path -LiteralPath $Pptx).Path
$qaOutput = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($qaOutput) | Out-Null
$wordApp = $null
$wordDoc = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordDoc = $wordApp.Documents.Open($docxInput, $false, $true, $false)
    $wordDoc.Repaginate()
    $pages = $wordDoc.ComputeStatistics(2)
    $wordDoc.ExportAsFixedFormat((Join-Path $qaOutput 'document.pdf'), 17)
    Write-Output "Word pages: $pages"
} finally {
    if ($null -ne $wordDoc) { $wordDoc.Close(0); [Runtime.InteropServices.Marshal]::ReleaseComObject($wordDoc) | Out-Null }
    if ($null -ne $wordApp) { $wordApp.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) | Out-Null }
}
$pptApp = $null
$pptDoc = $null
try {
    $pptApp = New-Object -ComObject PowerPoint.Application
    $pptDoc = $pptApp.Presentations.Open($pptxInput, -1, 0, 0)
    $slideFolder = Join-Path $qaOutput 'slides'
    [IO.Directory]::CreateDirectory($slideFolder) | Out-Null
    $pptDoc.Export($slideFolder, 'PNG', 1600, 900)
    $textOverflows = @()
    foreach ($slide in $pptDoc.Slides) {
        foreach ($shape in $slide.Shapes) {
            if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
                $boundHeight = $shape.TextFrame2.TextRange.BoundHeight
                $boundWidth = $shape.TextFrame2.TextRange.BoundWidth
                if ($boundHeight -gt ($shape.Height + 3) -or $boundWidth -gt ($shape.Width + 3)) {
                    $textOverflows += [pscustomobject]@{slide=$slide.SlideIndex; shape=$shape.Name; height=$shape.Height; boundHeight=$boundHeight; width=$shape.Width; boundWidth=$boundWidth}
                }
            }
        }
    }
    $receipt = @{word_pages=$pages; slides=$pptDoc.Slides.Count; text_overflows=@($textOverflows); renderer='Microsoft Word and PowerPoint COM hidden read-only'; docx=$docxInput; pptx=$pptxInput}
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $qaOutput 'native-render.json') -Encoding utf8
    Write-Output "PowerPoint slides: $($pptDoc.Slides.Count)"
    Write-Output "Text overflows: $($textOverflows.Count)"
} finally {
    if ($null -ne $pptDoc) { $pptDoc.Close(); [Runtime.InteropServices.Marshal]::ReleaseComObject($pptDoc) | Out-Null }
    if ($null -ne $pptApp) { $pptApp.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($pptApp) | Out-Null }
}
