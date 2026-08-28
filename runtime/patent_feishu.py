#!/usr/bin/env python3
"""Portable Feishu runtime bridge for the patent skill pack.

The bridge never stores app secrets or access tokens. Authentication remains in
lark-cli's secure profile storage. Local state contains only profile names,
Feishu resource coordinates, record IDs, and non-secret audit metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_TABLE = "专利项目"
STAGE_TABLE = "节点运行"
CONFIRMATION_TABLE = "确认事项"
ARTIFACT_TABLE = "成果索引"

STAGES = [f"{number:02d}" for number in range(1, 13)] + ["END"]
PROJECT_STATUSES = ["待推进", "运行中", "待确认", "已阻断", "已完成"]
GATES = ["pending", "pass", "warning", "fail"]


class BridgeError(RuntimeError):
    def __init__(self, message: str, *, details: Any | None = None, exit_code: int = 1):
        super().__init__(message)
        self.details = details
        self.exit_code = exit_code


def emit(payload: dict[str, Any], exit_code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return slug or "default"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utcish_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def base_datetime() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def parse_json_output(text: str) -> Any:
    stripped = text.lstrip("\ufeff\r\n \t")
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\[{]", stripped):
            try:
                value, _ = decoder.raw_decode(stripped[match.start() :])
                return value
            except json.JSONDecodeError:
                continue
    return None


def find_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item not in (None, "", [], {}):
                return item
        for item in value.values():
            found = find_value(item, keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_value(item, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def lark_prefix() -> list[str]:
    if os.name == "nt":
        script = shutil.which("lark-cli.ps1")
        if script:
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if not powershell:
                raise BridgeError("找到 lark-cli.ps1，但找不到 PowerShell 运行时。")
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
    binary = shutil.which("lark-cli")
    if binary:
        return [binary]
    raise BridgeError(
        "未找到 lark-cli。请先运行 npx --yes @larksuite/cli@latest install。"
    )


class LarkClient:
    def __init__(self, project_root: Path, profile: str | None):
        self.project_root = project_root
        self.profile = profile if profile and profile != "default" else None
        self.prefix = lark_prefix()

    def run(self, args: list[str], *, expect_json: bool = True) -> Any:
        argv = [*self.prefix]
        if self.profile:
            argv.extend(["--profile", self.profile])
        argv.extend(args)
        environment = os.environ.copy()
        environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        environment["PYTHONUTF8"] = "1"
        completed = subprocess.run(
            argv,
            cwd=self.project_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        payload = parse_json_output(stdout) if expect_json else stdout
        error_payload = parse_json_output(stderr)
        if completed.returncode != 0:
            details = error_payload or payload or {"stderr": stderr, "stdout": stdout}
            if completed.returncode == 10:
                raise BridgeError(
                    "飞书 CLI 要求对高风险操作单独确认；不得自动追加 --yes。",
                    details=details,
                    exit_code=10,
                )
            raise BridgeError("飞书 CLI 调用失败。", details=details, exit_code=completed.returncode)
        if expect_json:
            if not isinstance(payload, dict):
                raise BridgeError("飞书 CLI 未返回可解析的 JSON。", details={"stdout": stdout})
            if payload.get("ok") is False:
                raise BridgeError("飞书 CLI 返回失败信封。", details=payload)
        return payload


def project_root_from(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise BridgeError(f"项目目录不存在：{root}")
    return root


def state_path(root: Path, profile: str | None) -> Path:
    key = safe_slug(profile or "default")
    return root / ".patent-feishu" / "accounts" / f"{key}.json"


def load_state(root: Path, profile: str | None) -> dict[str, Any]:
    path = state_path(root, profile)
    if not path.exists():
        return {"schema_version": 1, "profile": profile or "default", "projects": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(root: Path, profile: str | None, state: dict[str, Any]) -> Path:
    path = state_path(root, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utcish_now()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    ignore = root / ".patent-feishu" / ".gitignore"
    if not ignore.exists():
        ignore.write_text("accounts/\ncache/\n", encoding="utf-8")
    return path


def profile_identity(client: LarkClient) -> dict[str, Any]:
    status = client.run(["auth", "status", "--json", "--verify"])
    whoami = client.run(["whoami", "--as", "user"])
    identity = whoami.get("identity")
    if identity not in (None, "user"):
        raise BridgeError("当前有效身份不是 user，不能访问账号自己的飞书资源。", details=whoami)
    identities = status.get("identities") if isinstance(status.get("identities"), dict) else {}
    user_status = identities.get("user") if isinstance(identities.get("user"), dict) else {}
    on_behalf_of = (
        whoami.get("onBehalfOf") if isinstance(whoami.get("onBehalfOf"), dict) else {}
    )
    open_id = (
        user_status.get("openId")
        or user_status.get("open_id")
        or on_behalf_of.get("openId")
        or on_behalf_of.get("open_id")
    )
    token_status = user_status.get("tokenStatus") or user_status.get("token_status")
    verified = user_status.get("verified")
    if verified is None:
        verified = status.get("verified")
    if token_status in {"expired", "invalid", "missing"} or verified is False:
        raise BridgeError("当前飞书用户授权无效或已过期。", details=status)
    if user_status.get("status") != "ready" or not open_id:
        raise BridgeError("当前飞书 Profile 没有可用的 user 身份。", details=status)
    return {
        "profile": client.profile or "default",
        "identity": "user",
        "open_id": open_id,
        "tenant_key": user_status.get("tenantKey")
        or user_status.get("tenant_key")
        or on_behalf_of.get("tenantKey")
        or on_behalf_of.get("tenant_key"),
        "user_name": user_status.get("userName")
        or user_status.get("user_name")
        or on_behalf_of.get("userName")
        or on_behalf_of.get("user_name"),
        "app_id": whoami.get("appId") or whoami.get("app_id") or status.get("appId"),
        "verified": verified,
    }


def option_list(names: list[str]) -> list[dict[str, str]]:
    hues = ["Blue", "Wathet", "Purple", "Orange", "Green", "Red", "Gray"]
    return [
        {"name": name, "hue": hues[index % len(hues)], "lightness": "Lighter"}
        for index, name in enumerate(names)
    ]


def project_fields() -> list[dict[str, Any]]:
    return [
        {"type": "text", "name": "项目键"},
        {"type": "text", "name": "项目名称"},
        {"type": "text", "name": "账号Profile"},
        {"type": "text", "name": "租户键"},
        {"type": "text", "name": "负责人OpenID"},
        {"type": "select", "name": "当前节点", "multiple": False, "options": option_list(STAGES)},
        {
            "type": "select",
            "name": "状态",
            "multiple": False,
            "options": option_list(PROJECT_STATUSES),
        },
        {"type": "text", "name": "下一节点"},
        {"type": "select", "name": "质量门", "multiple": False, "options": option_list(GATES)},
        {"type": "checkbox", "name": "可继续"},
        {"type": "text", "name": "阻断原因"},
        {"type": "text", "name": "材料入口", "style": {"type": "url"}},
        {"type": "text", "name": "成果总览", "style": {"type": "url"}},
        {"type": "text", "name": "本地交接哈希"},
        {"type": "created_at", "name": "创建时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
        {"type": "updated_at", "name": "最近同步", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]


def stage_fields() -> list[dict[str, Any]]:
    return [
        {"type": "text", "name": "运行键"},
        {"type": "text", "name": "项目键"},
        {"type": "select", "name": "节点", "multiple": False, "options": option_list(STAGES[:-1])},
        {"type": "number", "name": "尝试次数", "style": {"type": "plain", "precision": 0}},
        {
            "type": "select",
            "name": "状态",
            "multiple": False,
            "options": option_list(["运行中", "待确认", "已阻断", "已完成"]),
        },
        {"type": "text", "name": "输入摘要"},
        {"type": "text", "name": "输出摘要"},
        {"type": "select", "name": "质量门", "multiple": False, "options": option_list(GATES)},
        {"type": "checkbox", "name": "可继续"},
        {"type": "text", "name": "交接哈希"},
        {"type": "text", "name": "阶段文档", "style": {"type": "url"}},
        {"type": "datetime", "name": "开始时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
        {"type": "datetime", "name": "完成时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]


def confirmation_fields() -> list[dict[str, Any]]:
    return [
        {"type": "text", "name": "确认键"},
        {"type": "text", "name": "项目键"},
        {"type": "select", "name": "节点", "multiple": False, "options": option_list(STAGES[:-1])},
        {"type": "text", "name": "问题"},
        {
            "type": "select",
            "name": "状态",
            "multiple": False,
            "options": option_list(["待确认", "已确认", "已拒绝", "已失效"]),
        },
        {"type": "text", "name": "回应"},
        {"type": "text", "name": "确认人OpenID"},
        {"type": "text", "name": "消息ID"},
        {"type": "text", "name": "任务链接", "style": {"type": "url"}},
        {"type": "created_at", "name": "创建时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
        {"type": "updated_at", "name": "更新时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]


def artifact_fields() -> list[dict[str, Any]]:
    return [
        {"type": "text", "name": "成果键"},
        {"type": "text", "name": "项目键"},
        {"type": "select", "name": "节点", "multiple": False, "options": option_list(STAGES[:-1])},
        {"type": "text", "name": "成果类型"},
        {"type": "text", "name": "名称"},
        {"type": "text", "name": "飞书文档链接", "style": {"type": "url"}},
        {"type": "text", "name": "本地相对路径"},
        {"type": "text", "name": "SHA256"},
        {"type": "number", "name": "版本", "style": {"type": "plain", "precision": 0}},
        {"type": "text", "name": "来源"},
        {
            "type": "select",
            "name": "验证状态",
            "multiple": False,
            "options": option_list(["未验证", "已验证", "待人工复核"]),
        },
        {"type": "created_at", "name": "创建时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]


def create_record(client: LarkClient, base_token: str, table: str, fields: dict[str, Any]) -> dict[str, Any]:
    return client.run(
        [
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            table,
            "--json",
            json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
            "--as",
            "user",
        ]
    )


def update_record(
    client: LarkClient, base_token: str, table: str, record_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    return client.run(
        [
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            table,
            "--record-id",
            record_id,
            "--json",
            json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
            "--as",
            "user",
        ]
    )


def remote_project(client: LarkClient, project: dict[str, Any]) -> dict[str, Any]:
    cache_dir = client.project_root / ".patent-feishu" / "cache" / safe_slug(client.profile or "default")
    cache_dir.mkdir(parents=True, exist_ok=True)
    relative = cache_dir.relative_to(client.project_root) / f"{safe_slug(project['project_key'])}-project.ndjson"
    client.run(
        [
            "base",
            "+record-get",
            "--base-token",
            project["base_token"],
            "--table-id",
            PROJECT_TABLE,
            "--record-id",
            project["project_record_id"],
            "--field-id",
            "项目键",
            "--field-id",
            "当前节点",
            "--field-id",
            "状态",
            "--field-id",
            "质量门",
            "--field-id",
            "可继续",
            "--field-id",
            "阻断原因",
            "--format",
            "ndjson",
            "--output",
            f"./{relative.as_posix()}",
            "--overwrite",
            "--as",
            "user",
        ]
    )
    rows = [json.loads(line) for line in (client.project_root / relative).read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != 1:
        raise BridgeError("飞书项目记录读取数量异常。", details={"records": len(rows)})
    return rows[0]


def select_one(value: Any) -> str | None:
    if isinstance(value, list) and value:
        item = value[0]
        return item.get("name") if isinstance(item, dict) else str(item)
    if isinstance(value, str):
        return value
    return None


def require_project(
    client: LarkClient, state: dict[str, Any], identity: dict[str, Any], project_key: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = state.get("projects", {}).get(project_key)
    if not project:
        raise BridgeError(f"本账号尚未绑定项目：{project_key}。请先执行 bootstrap 或 attach。")
    bound_open_id = project.get("owner_open_id")
    current_open_id = identity.get("open_id")
    if bound_open_id and current_open_id and bound_open_id != current_open_id:
        raise BridgeError(
            "账号身份与本地项目绑定不一致，已阻止跨账号串写。",
            details={"bound_open_id": bound_open_id, "current_open_id": current_open_id},
        )
    remote = remote_project(client, project)
    if remote.get("项目键") != project_key:
        raise BridgeError("飞书项目记录与请求的项目键不一致。", details=remote)
    return project, remote


def command_preflight(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root_from(args.project_root)
    client = LarkClient(root, args.profile)
    identity = profile_identity(client)
    state = load_state(root, args.profile)
    result: dict[str, Any] = {"ok": True, "identity": identity, "state_file": str(state_path(root, args.profile))}
    if args.project_key:
        project, remote = require_project(client, state, identity, args.project_key)
        current_stage = select_one(remote.get("当前节点"))
        if args.expected_stage and current_stage != args.expected_stage:
            raise BridgeError(
                "飞书主账本的当前节点与拟执行节点不一致。",
                details={"expected_stage": args.expected_stage, "current_stage": current_stage},
            )
        result.update({"project": project, "remote_project": remote})
    return result


def command_bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root_from(args.project_root)
    client = LarkClient(root, args.profile)
    identity = profile_identity(client)
    state = load_state(root, args.profile)
    if args.project_key in state.get("projects", {}) and not args.force_new:
        project, remote = require_project(client, state, identity, args.project_key)
        return {"ok": True, "idempotent": True, "identity": identity, "project": project, "remote_project": remote}

    created = client.run(
        [
            "base",
            "+base-create",
            "--name",
            args.base_name or f"{args.project_name}-专利AI主账本",
            "--table-name",
            PROJECT_TABLE,
            "--fields",
            json.dumps(project_fields(), ensure_ascii=False, separators=(",", ":")),
            "--time-zone",
            "Asia/Shanghai",
            "--as",
            "user",
        ]
    )
    base_token = find_value(created, {"base_token", "app_token", "token"})
    if not isinstance(base_token, str):
        raise BridgeError("已创建多维表格，但未能解析 base_token。", details=created)
    base_url = find_value(created, {"url", "base_url"})

    for name, fields in (
        (STAGE_TABLE, stage_fields()),
        (CONFIRMATION_TABLE, confirmation_fields()),
        (ARTIFACT_TABLE, artifact_fields()),
    ):
        client.run(
            [
                "base",
                "+table-create",
                "--base-token",
                base_token,
                "--name",
                name,
                "--fields",
                json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                "--view",
                json.dumps({"name": "全部记录", "type": "grid"}, ensure_ascii=False),
                "--as",
                "user",
            ]
        )

    overview = client.run(
        [
            "docs",
            "+create",
            "--title",
            f"{args.project_name}｜发明专利 AI 成果总览",
            "--doc-format",
            "xml",
            "--as",
            "user",
        ]
    )
    overview_url = find_value(overview, {"url"})
    overview_doc_id = find_value(overview, {"document_id", "docx_token"})
    record = create_record(
        client,
        base_token,
        PROJECT_TABLE,
        {
            "项目键": args.project_key,
            "项目名称": args.project_name,
            "账号Profile": args.profile or "default",
            "租户键": identity.get("tenant_key") or "",
            "负责人OpenID": identity.get("open_id") or "",
            "当前节点": ["01"],
            "状态": ["待推进"],
            "下一节点": "01",
            "质量门": ["pending"],
            "可继续": True,
            "阻断原因": "",
            "材料入口": args.materials_url or "",
            "成果总览": overview_url or "",
            "本地交接哈希": "",
        },
    )
    record_id = find_value(record, {"record_id", "recordId"})
    if not isinstance(record_id, str):
        record_ids = find_value(record, {"record_id_list", "recordIdList"})
        if isinstance(record_ids, list) and len(record_ids) == 1:
            record_id = record_ids[0]
    if not isinstance(record_id, str):
        raise BridgeError("已创建项目记录，但未能解析 record_id。", details=record)

    project = {
        "project_key": args.project_key,
        "project_name": args.project_name,
        "base_token": base_token,
        "base_url": base_url,
        "project_record_id": record_id,
        "overview_doc_id": overview_doc_id,
        "overview_url": overview_url,
        "owner_open_id": identity.get("open_id"),
        "tenant_key": identity.get("tenant_key"),
        "notify_chat_id": args.notify_chat_id,
        "created_at": utcish_now(),
        "last_stage": "01",
    }
    state.setdefault("projects", {})[args.project_key] = project
    state["identity"] = identity
    saved = save_state(root, args.profile, state)
    return {"ok": True, "identity": identity, "project": project, "state_file": str(saved)}


def command_attach(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root_from(args.project_root)
    client = LarkClient(root, args.profile)
    identity = profile_identity(client)
    state = load_state(root, args.profile)
    project = {
        "project_key": args.project_key,
        "project_name": args.project_name,
        "base_token": args.base_token,
        "base_url": args.base_url,
        "project_record_id": args.project_record_id,
        "overview_doc_id": args.overview_doc_id,
        "overview_url": args.overview_url,
        "owner_open_id": identity.get("open_id"),
        "tenant_key": identity.get("tenant_key"),
        "notify_chat_id": args.notify_chat_id,
        "attached_at": utcish_now(),
    }
    remote = remote_project(client, project)
    if remote.get("项目键") != args.project_key:
        raise BridgeError("目标飞书记录的项目键不匹配。", details=remote)
    state.setdefault("projects", {})[args.project_key] = project
    state["identity"] = identity
    saved = save_state(root, args.profile, state)
    return {"ok": True, "identity": identity, "project": project, "remote_project": remote, "state_file": str(saved)}


def command_start_stage(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root_from(args.project_root)
    client = LarkClient(root, args.profile)
    identity = profile_identity(client)
    state = load_state(root, args.profile)
    project, remote = require_project(client, state, identity, args.project_key)
    current_stage = select_one(remote.get("当前节点"))
    if current_stage != args.node:
        raise BridgeError("飞书主账本未授权执行该节点。", details={"requested": args.node, "current": current_stage})
    update_record(
        client,
        project["base_token"],
        PROJECT_TABLE,
        project["project_record_id"],
        {"状态": ["运行中"], "质量门": ["pending"], "可继续": False, "阻断原因": ""},
    )
    return {"ok": True, "identity": identity, "project_key": args.project_key, "node": args.node, "started_at": utcish_now()}


def create_doc_from_file(client: LarkClient, path: Path, title: str) -> tuple[str | None, str | None]:
    root = client.project_root
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise BridgeError("待发布文档必须位于当前专利项目目录内。") from exc
    result = client.run(
        [
            "docs",
            "+create",
            "--title",
            title,
            "--doc-format",
            "markdown",
            "--content",
            f"@./{relative.as_posix()}",
            "--as",
            "user",
        ]
    )
    return find_value(result, {"url"}), find_value(result, {"document_id", "docx_token"})


def command_publish_stage(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root_from(args.project_root)
    client = LarkClient(root, args.profile)
    identity = profile_identity(client)
    state = load_state(root, args.profile)
    project, remote = require_project(client, state, identity, args.project_key)
    current_stage = select_one(remote.get("当前节点"))
    if current_stage != args.node:
        raise BridgeError("发布节点与飞书主账本当前节点不一致。", details={"requested": args.node, "current": current_stage})

    doc_url = None
    doc_id = None
    artifact_hash = None
    relative_path = None
    if args.doc_file:
        doc_path = Path(args.doc_file)
        if not doc_path.is_absolute():
            doc_path = root / doc_path
        if not doc_path.is_file():
            raise BridgeError(f"阶段文档不存在：{doc_path}")
        relative_path = doc_path.resolve().relative_to(root).as_posix()
        artifact_hash = sha256_file(doc_path)
        doc_url, doc_id = create_doc_from_file(
            client, doc_path, args.doc_title or f"{project['project_name']}｜节点{args.node}阶段报告"
        )
        artifact_key = f"{args.project_key}:{args.node}:{artifact_hash[:12]}"
        create_record(
            client,
            project["base_token"],
            ARTIFACT_TABLE,
            {
                "成果键": artifact_key,
                "项目键": args.project_key,
                "节点": [args.node],
                "成果类型": args.artifact_type,
                "名称": args.doc_title or doc_path.name,
                "飞书文档链接": doc_url or "",
                "本地相对路径": relative_path,
                "SHA256": artifact_hash,
                "版本": args.version,
                "来源": args.source,
                "验证状态": [args.validation_status],
            },
        )

    handoff_hash = ""
    if args.handoff_file:
        handoff_path = Path(args.handoff_file)
        if not handoff_path.is_absolute():
            handoff_path = root / handoff_path
        if not handoff_path.is_file():
            raise BridgeError(f"交接文件不存在：{handoff_path}")
        handoff_hash = sha256_file(handoff_path)

    run_key = f"{args.project_key}:{args.node}:{datetime.now().astimezone().strftime('%Y%m%d%H%M%S')}"
    create_record(
        client,
        project["base_token"],
        STAGE_TABLE,
        {
            "运行键": run_key,
            "项目键": args.project_key,
            "节点": [args.node],
            "尝试次数": args.attempt,
            "状态": [args.stage_status],
            "输入摘要": args.input_summary,
            "输出摘要": args.summary,
            "质量门": [args.quality_gate],
            "可继续": args.can_continue,
            "交接哈希": handoff_hash,
            "阶段文档": doc_url or "",
            "开始时间": args.started_at or base_datetime(),
            "完成时间": base_datetime(),
        },
    )

    if args.can_continue:
        next_stage = args.next_stage
        project_status = "已完成" if next_stage == "END" else "待推进"
        reason = ""
    else:
        next_stage = args.node
        project_status = "待确认" if args.stage_status == "待确认" else "已阻断"
        reason = args.block_reason
    update_record(
        client,
        project["base_token"],
        PROJECT_TABLE,
        project["project_record_id"],
        {
            "当前节点": [next_stage],
            "状态": [project_status],
            "下一节点": args.next_stage,
            "质量门": [args.quality_gate],
            "可继续": args.can_continue,
            "阻断原因": reason,
            "本地交接哈希": handoff_hash,
        },
    )
    project.update(
        {
            "last_stage": args.node,
            "current_stage": next_stage,
            "last_stage_doc_url": doc_url,
            "last_handoff_hash": handoff_hash,
            "updated_at": utcish_now(),
        }
    )
    state["projects"][args.project_key] = project
    save_state(root, args.profile, state)
    return {
        "ok": True,
        "identity": identity,
        "project_key": args.project_key,
        "completed_node": args.node,
        "current_stage": next_stage,
        "project_status": project_status,
        "quality_gate": args.quality_gate,
        "can_continue": args.can_continue,
        "stage_doc_url": doc_url,
        "stage_doc_id": doc_id,
        "artifact_sha256": artifact_hash,
        "handoff_sha256": handoff_hash,
        "base_url": project.get("base_url"),
    }


def command_ask(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root_from(args.project_root)
    client = LarkClient(root, args.profile)
    identity = profile_identity(client)
    state = load_state(root, args.profile)
    project, remote = require_project(client, state, identity, args.project_key)
    current_stage = select_one(remote.get("当前节点"))
    if current_stage != args.node:
        raise BridgeError("确认事项节点与飞书主账本当前节点不一致。")

    message_id = ""
    task_url = ""
    if args.send_notification:
        if not args.notification_approved:
            raise BridgeError("发送飞书消息或任务前必须显式传入 --notification-approved。")
        target_chat = args.chat_id or project.get("notify_chat_id")
        message_text = (
            f"## 发明专利项目待确认\n\n- 项目：{project['project_name']}\n"
            f"- 节点：{args.node}\n- 问题：{args.question}\n\n"
            f"请在多维表格“{CONFIRMATION_TABLE}”中填写回应并将状态改为“已确认”或“已拒绝”。"
        )
        if target_chat:
            sent = client.run(
                [
                    "im",
                    "+messages-send",
                    "--chat-id",
                    target_chat,
                    "--markdown",
                    message_text,
                    "--idempotency-key",
                    safe_slug(args.confirmation_key)[:50],
                    "--as",
                    "user",
                ]
            )
            message_id = find_value(sent, {"message_id"}) or ""
        if args.create_task:
            assignee = identity.get("open_id")
            if not assignee:
                raise BridgeError("无法解析当前账号 open_id，不能创建任务。")
            task = client.run(
                [
                    "task",
                    "+create",
                    "--summary",
                    f"确认专利项目 {project['project_name']} 节点 {args.node}",
                    "--description",
                    args.question,
                    "--assignee",
                    assignee,
                    "--idempotency-key",
                    safe_slug(args.confirmation_key)[:50],
                    "--as",
                    "user",
                ]
            )
            task_url = find_value(task, {"url"}) or ""

    create_record(
        client,
        project["base_token"],
        CONFIRMATION_TABLE,
        {
            "确认键": args.confirmation_key,
            "项目键": args.project_key,
            "节点": [args.node],
            "问题": args.question,
            "状态": ["待确认"],
            "回应": "",
            "确认人OpenID": "",
            "消息ID": message_id,
            "任务链接": task_url,
        },
    )
    update_record(
        client,
        project["base_token"],
        PROJECT_TABLE,
        project["project_record_id"],
        {"状态": ["待确认"], "质量门": ["pending"], "可继续": False, "阻断原因": args.question},
    )
    return {
        "ok": True,
        "identity": identity,
        "confirmation_key": args.confirmation_key,
        "status": "待确认",
        "message_id": message_id,
        "task_url": task_url,
        "base_url": project.get("base_url"),
    }


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--profile", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feishu runtime bridge for the patent skill pack")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    add_common(preflight)
    preflight.add_argument("--project-key")
    preflight.add_argument("--expected-stage", choices=STAGES)
    preflight.set_defaults(handler=command_preflight)

    bootstrap = subparsers.add_parser("bootstrap")
    add_common(bootstrap)
    bootstrap.add_argument("--project-key", required=True)
    bootstrap.add_argument("--project-name", required=True)
    bootstrap.add_argument("--base-name")
    bootstrap.add_argument("--materials-url")
    bootstrap.add_argument("--notify-chat-id")
    bootstrap.add_argument("--force-new", action="store_true")
    bootstrap.set_defaults(handler=command_bootstrap)

    attach = subparsers.add_parser("attach")
    add_common(attach)
    attach.add_argument("--project-key", required=True)
    attach.add_argument("--project-name", required=True)
    attach.add_argument("--base-token", required=True)
    attach.add_argument("--project-record-id", required=True)
    attach.add_argument("--base-url")
    attach.add_argument("--overview-doc-id")
    attach.add_argument("--overview-url")
    attach.add_argument("--notify-chat-id")
    attach.set_defaults(handler=command_attach)

    start_stage = subparsers.add_parser("start-stage")
    add_common(start_stage)
    start_stage.add_argument("--project-key", required=True)
    start_stage.add_argument("--node", required=True, choices=STAGES[:-1])
    start_stage.set_defaults(handler=command_start_stage)

    publish = subparsers.add_parser("publish-stage")
    add_common(publish)
    publish.add_argument("--project-key", required=True)
    publish.add_argument("--node", required=True, choices=STAGES[:-1])
    publish.add_argument("--next-stage", required=True, choices=STAGES)
    publish.add_argument("--stage-status", default="已完成", choices=["运行中", "待确认", "已阻断", "已完成"])
    publish.add_argument("--quality-gate", default="pass", choices=GATES)
    publish.add_argument("--can-continue", action=argparse.BooleanOptionalAction, default=True)
    publish.add_argument("--block-reason", default="")
    publish.add_argument("--summary", required=True)
    publish.add_argument("--input-summary", default="")
    publish.add_argument("--doc-file")
    publish.add_argument("--doc-title")
    publish.add_argument("--artifact-type", default="阶段报告")
    publish.add_argument("--version", type=int, default=1)
    publish.add_argument("--source", default="AI专利工作流")
    publish.add_argument(
        "--validation-status", default="已验证", choices=["未验证", "已验证", "待人工复核"]
    )
    publish.add_argument("--handoff-file")
    publish.add_argument("--attempt", type=int, default=1)
    publish.add_argument("--started-at")
    publish.set_defaults(handler=command_publish_stage)

    ask = subparsers.add_parser("ask")
    add_common(ask)
    ask.add_argument("--project-key", required=True)
    ask.add_argument("--node", required=True, choices=STAGES[:-1])
    ask.add_argument("--confirmation-key", required=True)
    ask.add_argument("--question", required=True)
    ask.add_argument("--send-notification", action="store_true")
    ask.add_argument("--notification-approved", action="store_true")
    ask.add_argument("--chat-id")
    ask.add_argument("--create-task", action="store_true")
    ask.set_defaults(handler=command_ask)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return emit(args.handler(args))
    except BridgeError as exc:
        return emit(
            {"ok": False, "error": {"message": str(exc), "details": exc.details}},
            exc.exit_code,
        )
    except Exception as exc:  # defensive boundary for automation callers
        return emit(
            {"ok": False, "error": {"message": f"未处理异常：{exc.__class__.__name__}: {exc}"}},
            1,
        )


if __name__ == "__main__":
    raise SystemExit(main())
