param(
    [string]$BundleRoot = "G:\PresentAgent\paper_url_to_source_document_batch\presentation_top20_pipeline_bundle",
    [string]$OutputRoot = "G:\PresentAgent\presentation_top20_ppts",
    [string]$TemplatePptx = "G:\PresentAgent\finalppt_build_effective_agents_qwen35_filtered\build_effective_agents.pptx",
    [string]$NotesModes = "single_presentation",
    [int]$NumSlides = 10
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "activate_presentagent_local.ps1")

if (-not $env:OPENAI_API_KEY -and -not $env:API_KEY) {
    throw "Set OPENAI_API_KEY or API_KEY before running this script."
}
if (-not $env:API_BASE) {
    throw "Set API_BASE before running this script."
}
if (-not $env:LANGUAGE_MODEL) {
    throw "Set LANGUAGE_MODEL before running this script."
}
if (-not $env:VISION_MODEL) {
    throw "Set VISION_MODEL before running this script."
}
if (-not $env:TEXT_MODEL) {
    throw "Set TEXT_MODEL before running this script."
}

$runScript = Join-Path $PSScriptRoot "run_document_to_ppt_local.ps1"
$bundleDirs = Get-ChildItem -LiteralPath $BundleRoot -Directory | Sort-Object Name
$results = @()

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

foreach ($dir in $bundleDirs) {
    $paperName = $dir.Name
    $docJson = Join-Path $dir.FullName "source_to_document\refined_doc.json"
    $imageDir = Join-Path $dir.FullName "url_to_source"
    $paperOut = Join-Path $OutputRoot $paperName
    $finalPptx = Join-Path $paperOut "single_presentation\final_single_presentation.pptx"

    if (-not (Test-Path $docJson)) {
        $results += [pscustomobject]@{
            paper_dir = $paperName
            status = "missing_refined_doc"
            final_pptx = ""
            output_dir = $paperOut
            notes = "Missing refined_doc.json"
        }
        continue
    }

    if (Test-Path $finalPptx) {
        $results += [pscustomobject]@{
            paper_dir = $paperName
            status = "already_exists"
            final_pptx = $finalPptx
            output_dir = $paperOut
            notes = ""
        }
        continue
    }

    Write-Host "Generating PPT for $paperName ..." -ForegroundColor Cyan
    try {
        & $runScript `
            -DocumentJson $docJson `
            -ImageDir $imageDir `
            -TemplatePptx $TemplatePptx `
            -OutputDir $paperOut `
            -NotesModes $NotesModes `
            -NumSlides $NumSlides

        $results += [pscustomobject]@{
            paper_dir = $paperName
            status = if (Test-Path $finalPptx) { "success" } else { "missing_final_pptx" }
            final_pptx = $finalPptx
            output_dir = $paperOut
            notes = ""
        }
    }
    catch {
        $results += [pscustomobject]@{
            paper_dir = $paperName
            status = "failed"
            final_pptx = ""
            output_dir = $paperOut
            notes = $_.Exception.Message
        }
    }
}

$jsonPath = Join-Path $OutputRoot "batch_results.json"
$csvPath = Join-Path $OutputRoot "batch_results.csv"
$results | ConvertTo-Json -Depth 5 | Out-File -LiteralPath $jsonPath -Encoding utf8
$results | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8

Write-Host "Batch summary written to:" -ForegroundColor Green
Write-Host $jsonPath
Write-Host $csvPath
