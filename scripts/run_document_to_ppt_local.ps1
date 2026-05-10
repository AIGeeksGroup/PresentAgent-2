param(
    [Parameter(Mandatory = $true)]
    [string]$DocumentJson,

    [Parameter(Mandatory = $true)]
    [string]$ImageDir,

    [Parameter(Mandatory = $true)]
    [string]$TemplatePptx,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$NotesModes = "single_presentation",
    [int]$NumSlides = 8
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot "activate_presentagent_local.ps1")

$Python = Join-Path $RepoRoot ".venv-presentagent\Scripts\python.exe"

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

$env:PRESENTAGENT_DEEPRESEARCH_ROOT = ""
$env:PRESENTAGENT_ENABLE_DOCUMENT_MEDIA_RESEARCH = "0"

& $Python (Join-Path $RepoRoot "test\test_document_to_ppt.py") `
    --document-json $DocumentJson `
    --image-dir $ImageDir `
    --template-pptx $TemplatePptx `
    --output-dir $OutputDir `
    --notes-modes $NotesModes `
    --num-slides $NumSlides

