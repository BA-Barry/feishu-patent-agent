#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="${1:-$SCRIPT_DIR}"

if [ ! -d "$PROJECT_ROOT" ]; then
  echo "错误：项目目录不存在：$PROJECT_ROOT" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/skills"
TARGET_DIR="$PROJECT_ROOT/.agents/skills"
RUNTIME_SOURCE="$SCRIPT_DIR/runtime/patent_feishu.py"
RUNTIME_TARGET_DIR="$PROJECT_ROOT/.agents/patent-feishu-runtime"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "错误：安装包缺少 skills 目录：$SOURCE_DIR" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR" "$RUNTIME_TARGET_DIR"

EXPECTED_SKILLS=(
  "feishu-patent-01-controller"
  "feishu-patent-02-intake"
  "feishu-patent-03-router"
  "feishu-patent-04-disclosure"
  "feishu-patent-05-search"
  "feishu-patent-06-invention"
  "feishu-patent-07-abstract"
  "feishu-patent-08-claims"
  "feishu-patent-09-drawings"
  "feishu-patent-10-drawing-review"
  "feishu-patent-11-specification"
  "feishu-patent-12-docx"
)

if [ ! -f "$RUNTIME_SOURCE" ]; then
  echo "错误：安装包缺少飞书运行桥接脚本：$RUNTIME_SOURCE" >&2
  exit 1
fi

for skill_name in "${EXPECTED_SKILLS[@]}"; do
  if [ ! -f "$SOURCE_DIR/$skill_name/SKILL.md" ]; then
    echo "错误：安装包缺少 Skill 或 SKILL.md：$skill_name" >&2
    exit 1
  fi
done

cp "$RUNTIME_SOURCE" "$RUNTIME_TARGET_DIR/patent_feishu.py"

installed_count=0
for skill_name in "${EXPECTED_SKILLS[@]}"; do
  skill_dir="$SOURCE_DIR/$skill_name"
  destination="$TARGET_DIR/$skill_name"
  rm -rf -- "$destination"
  cp -R "$skill_dir" "$destination"
  installed_count=$((installed_count + 1))
done

if [ "$installed_count" -ne 12 ]; then
  echo "错误：应安装 12 个 Skill，实际安装 $installed_count 个。" >&2
  exit 1
fi

echo "飞书专利 Agent 参赛版已安装到当前项目：$TARGET_DIR"
echo "共安装 $installed_count 个 Skill。"
echo "飞书运行桥接脚本：$RUNTIME_TARGET_DIR/patent_feishu.py"
echo "本次安装仅对该项目生效，未写入任何用户全局 Skill 目录。"
