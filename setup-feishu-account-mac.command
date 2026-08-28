#!/bin/bash
set -euo pipefail

PROFILE="${1:-default}"
if [[ ! "$PROFILE" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "用法：bash setup-feishu-account-mac.command [profile]" >&2
  echo "profile 只允许字母、数字、点、下划线和连字符。" >&2
  exit 2
fi

if ! command -v lark-cli >/dev/null 2>&1; then
  if ! command -v npx >/dev/null 2>&1; then
    echo "错误：未找到 lark-cli 或 npx。请先安装 Node.js，再重试。" >&2
    exit 1
  fi
  echo "正在安装飞书官方 CLI；请在浏览器中完成应用配置。"
  npx --yes @larksuite/cli@latest install
fi

PROFILE_ARGS=()
if [[ "$PROFILE" != "default" ]]; then
  if ! lark-cli profile list | grep -Fq -- "$PROFILE"; then
    echo "正在创建账号配置 '$PROFILE'；请在浏览器中完成应用配置。"
    lark-cli config init --new --name "$PROFILE" --brand feishu --lang zh_cn
  fi
  PROFILE_ARGS=(--profile "$PROFILE")
fi

echo "正在为 '$PROFILE' 申请本工作流所需的最小业务域，请在浏览器中登录对应飞书账号。"
lark-cli "${PROFILE_ARGS[@]}" auth login --domain base --domain docs --domain drive --domain im --domain task --recommend
lark-cli "${PROFILE_ARGS[@]}" auth status --json --verify
lark-cli "${PROFILE_ARGS[@]}" whoami --as user

echo "账号配置 '$PROFILE' 已就绪。运行专利工作流时始终传入：--profile $PROFILE"
