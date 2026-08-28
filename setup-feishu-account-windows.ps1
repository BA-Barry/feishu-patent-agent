param(
    [Parameter(Position = 0)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Profile = "default"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command lark-cli -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
        throw "未找到 lark-cli 或 npx。请先安装 Node.js，再重试。"
    }
    Write-Host "正在安装飞书官方 CLI；请在浏览器中完成应用配置。"
    & npx --yes '@larksuite/cli@latest' install
    if ($LASTEXITCODE -ne 0) { throw "飞书 CLI 安装失败。" }
}

$ProfileArgs = @()
if ($Profile -ne "default") {
    $Profiles = (& lark-cli profile list | Out-String)
    if ($Profiles -notmatch [regex]::Escape($Profile)) {
        Write-Host "正在创建账号配置 '$Profile'；请在浏览器中完成应用配置。"
        & lark-cli config init --new --name $Profile --brand feishu --lang zh_cn
        if ($LASTEXITCODE -ne 0) { throw "账号配置创建失败。" }
    }
    $ProfileArgs = @("--profile", $Profile)
}

Write-Host "正在为 '$Profile' 申请本工作流所需的最小业务域，请在浏览器中登录对应飞书账号。"
& lark-cli @ProfileArgs auth login --domain base --domain docs --domain drive --domain im --domain task --recommend
if ($LASTEXITCODE -ne 0) { throw "飞书账号授权失败。" }

& lark-cli @ProfileArgs auth status --json --verify
if ($LASTEXITCODE -ne 0) { throw "飞书账号授权状态校验失败。" }
& lark-cli @ProfileArgs whoami --as user
if ($LASTEXITCODE -ne 0) { throw "飞书 user 身份校验失败。" }

Write-Host "账号配置 '$Profile' 已就绪。运行专利工作流时始终传入：--profile $Profile"
