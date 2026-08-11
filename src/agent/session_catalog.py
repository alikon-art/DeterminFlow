"""轻量会话目录。

只保存列表和树结构所需的摘要字段，避免服务启动时把完整消息历史、
LangChain 消息对象和 Graph 一并常驻内存。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.session import AgentSession

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"completed", "error", "stopped", "cancelled"}


def infer_runtime_scope(data: dict[str, Any]) -> str:
    """兼容旧 Session 文件，判断会话属于交互运行时还是 Workflow 运行时。"""
    explicit = data.get("runtime_scope")
    if explicit in {"interactive", "workflow"}:
        return explicit
    if data.get("session_type", "sub") == "sub" and data.get("workflow_id"):
        return "workflow"
    task = str(data.get("task_description") or "").lstrip()
    if data.get("session_type") == "main" and task.startswith("Workflow:"):
        return "workflow"
    return "interactive"


def _normalized_status(
    session_type: str,
    status: str,
    lifecycle_profile: str = "task",
) -> str:
    if (
        lifecycle_profile == "detached_conversation"
        and status in {"running", "streaming"}
    ):
        return "completed"
    if session_type == "main" and status == "streaming":
        return "running"
    if (
        session_type == "sub"
        and status in {"running", "streaming"}
        and lifecycle_profile != "detached_conversation"
    ):
        return "error"
    return status


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    session_type: str
    parent_id: str | None
    status: str
    task: str
    message_count: int
    created_at: str
    updated_at: str
    last_message: str
    agent_type: str
    runtime_scope: str
    persisted_status: str
    workspace_path: str | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    node_id: str | None = None
    lifecycle_profile: str = "task"
    resource_owner: str = ""
    external_ref: str = ""
    scope_hash: str = ""
    project_name: str = "未分类项目"

    @classmethod
    def from_data(cls, data: dict[str, Any], *, fallback_id: str = "") -> "SessionMetadata":
        record = data.get("record")
        messages = record if isinstance(record, list) else []
        message_count = sum(
            1 for message in messages
            if isinstance(message, dict)
            and message.get("type") not in {"system_prompt", "compression_divider"}
        )
        last_message = ""
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("type") != "assistant":
                continue
            content = message.get("content")
            if content:
                last_message = str(content)[:200]
                break

        session_type = str(data.get("session_type") or "sub")
        persisted_status = str(data.get("status") or "completed")
        lifecycle_profile = str(data.get("lifecycle_profile") or "task")
        status = _normalized_status(
            session_type,
            persisted_status,
            lifecycle_profile,
        )
        return cls(
            session_id=str(data.get("session_id") or fallback_id),
            session_type=session_type,
            parent_id=data.get("parent_id"),
            status=status,
            task=str(data.get("task_description") or "")[:100],
            message_count=message_count,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_message=last_message,
            agent_type=str(data.get("agent_type") or "default"),
            runtime_scope=infer_runtime_scope(data),
            persisted_status=persisted_status,
            workspace_path=data.get("workspace_path"),
            workflow_id=data.get("workflow_id"),
            task_id=data.get("task_id"),
            node_id=data.get("node_id"),
            lifecycle_profile=lifecycle_profile,
            resource_owner=str(data.get("resource_owner") or ""),
            external_ref=str(data.get("external_ref") or ""),
            scope_hash=str(data.get("scope_hash") or ""),
            project_name=str(data.get("project_name") or "未分类项目"),
        )

    @classmethod
    def from_session(cls, session: "AgentSession") -> "SessionMetadata":
        summary = session.get_summary()
        return cls(
            session_id=session.session_id,
            session_type=session.session_type,
            parent_id=session.parent_id,
            status=session.status,
            task=summary.get("task", ""),
            message_count=int(summary.get("message_count", 0)),
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_message=summary.get("last_message", ""),
            agent_type=session.agent_type,
            runtime_scope=getattr(session, "runtime_scope", "interactive"),
            persisted_status=session.status,
            workspace_path=session.workspace_path,
            workflow_id=session.workflow_id,
            task_id=session.task_id,
            node_id=session.node_id or None,
            lifecycle_profile=getattr(session, "lifecycle_profile", "task"),
            resource_owner=getattr(session, "resource_owner", ""),
            external_ref=getattr(session, "external_ref", ""),
            scope_hash=getattr(session, "scope_hash", ""),
            project_name=getattr(session, "project_name", "未分类项目"),
        )

    def to_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "session_id": self.session_id,
            "type": self.session_type,
            "parent_id": self.parent_id,
            "status": self.status,
            "task": self.task,
            "message_count": self.message_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_message": self.last_message,
            "agent_type": self.agent_type,
            "project_name": self.project_name,
        }
        for key in ("workspace_path", "workflow_id", "task_id", "node_id"):
            value = getattr(self, key)
            if value:
                summary[key] = value
        return summary


class SessionCatalog:
    """Session JSON 的进程内轻量索引。"""

    def __init__(self) -> None:
        self._entries: dict[str, SessionMetadata] = {}

    def scan(self, sessions_dir: Path) -> dict[str, int]:
        self._entries.clear()
        scanned = 0
        errors = 0
        if not sessions_dir.exists():
            return {"scanned": scanned, "errors": errors}
        for file_path in sorted(sessions_dir.glob("*.json")):
            try:
                with file_path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                metadata = SessionMetadata.from_data(data, fallback_id=file_path.stem)
                if not metadata.session_id:
                    raise ValueError("session_id 为空")
                self._entries[metadata.session_id] = metadata
                scanned += 1
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors += 1
                logger.error("索引 session %s 失败: %s", file_path.stem, exc)
        return {"scanned": scanned, "errors": errors}

    def upsert_session(self, session: "AgentSession") -> None:
        self._entries[session.session_id] = SessionMetadata.from_session(session)

    def get(self, session_id: str) -> SessionMetadata | None:
        return self._entries.get(session_id)

    def remove(self, session_id: str) -> None:
        self._entries.pop(session_id, None)

    def prune_terminal_workflow_history(
        self,
        sessions_dir: Path,
        *,
        max_entries: int,
        protected_session_ids: Collection[str],
        can_delete: Callable[[SessionMetadata], bool],
    ) -> dict[str, Any]:
        """删除超出上限的旧 Workflow Session 文件。"""
        protected = set(protected_session_ids)
        candidates = [
            metadata
            for metadata in self._entries.values()
            if metadata.session_id not in protected
            and metadata.runtime_scope == "workflow"
            and metadata.persisted_status in _TERMINAL_STATUSES
            and can_delete(metadata)
        ]
        candidates.sort(
            key=lambda metadata: (
                metadata.updated_at,
                metadata.created_at,
                metadata.session_id,
            ),
            reverse=True,
        )

        deleted: list[str] = []
        deleted_bytes = 0
        errors = 0
        for metadata in candidates[max(0, max_entries):]:
            file_path = sessions_dir / f"{metadata.session_id}.json"
            try:
                try:
                    deleted_bytes += file_path.stat().st_size
                except FileNotFoundError:
                    pass
                file_path.unlink(missing_ok=True)
            except OSError as exc:
                errors += 1
                logger.error("清理历史 Session %s 失败: %s", metadata.session_id, exc)
                continue
            self.remove(metadata.session_id)
            deleted.append(metadata.session_id)

        return {
            "eligible": len(candidates),
            "deleted": deleted,
            "deleted_bytes": deleted_bytes,
            "errors": errors,
        }

    def values(self) -> list[SessionMetadata]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)
