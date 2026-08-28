param(
    [Parameter(Position = 0)]
    [string]$ProjectRoot = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "项目目录不存在：$ProjectRoot"
}

$ScriptDir = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$SourceDir = Join-Path $ScriptDir "skills"
$TargetDir = Join-Path $ProjectRoot ".agents\skills"
$RuntimeSource = Join-Path $ScriptDir "runtime\patent_feishu.py"
$RuntimeTargetDir = Join-Path $ProjectRoot ".agents\patent-feishu-runtime"

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    throw "安装包缺少 skills 目录：$SourceDir"
}

$ExpectedSkills = @(
    "feishu-patent-01-controller",
    "feishu-patent-02-intake",
    "feishu-patent-03-router",
    "feishu-patent-04-disclosure",
    "feishu-patent-05-search",
    "feishu-patent-06-invention",
    "feishu-patent-07-abstract",
    "feishu-patent-08-claims",
    "feishu-patent-09-drawings",
    "feishu-patent-10-drawing-review",
    "feishu-patent-11-specification",
    "feishu-patent-12-docx"
)

$ExpectedSkills | ForEach-Object {
    $SkillFile = Join-Path (Join-Path $SourceDir $_) "SKILL.md"
    if (-not (Test-Path -LiteralPath $SkillFile -PathType Leaf)) {
        throw "安装包缺少 Skill 或 SKILL.md：$_"
    }
}

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
if (-not (Test-Path -LiteralPath $RuntimeSource -PathType Leaf)) {
    throw "安装包缺少飞书运行桥接脚本：$RuntimeSource"
}
New-Item -ItemType Directory -Force -Path $RuntimeTargetDir | Out-Null

$ExpectedSkills | ForEach-Object {
    $SkillDir = Join-Path $SourceDir $_
    $Destination = Join-Path $TargetDir $_
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    Copy-Item -LiteralPath $SkillDir -Destination $Destination -Recurse -Force
}

Copy-Item -LiteralPath $RuntimeSource -Destination (Join-Path $RuntimeTargetDir "patent_feishu.py") -Force

Write-Host "飞书专利 Agent 参赛版已安装到当前项目：$TargetDir"
Write-Host "共安装 $($ExpectedSkills.Count) 个 Skill。"
Write-Host "飞书运行桥接脚本：$RuntimeTargetDir\patent_feishu.py"
Write-Host "本次安装仅对该项目生效，未写入任何用户全局 Skill 目录。"
