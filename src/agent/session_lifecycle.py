"""SessionManager 的驻留、查询与销毁生命周期实现。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from src.agent.session import AgentSession
from src.agent.session_catalog import SessionMetadata
from src.config import SESSIONS_DIR, WORKFLOWS_DIR
from src.core.utils import is_visible_to_frontend

logger = logging.getLogger(__name__)

_ACTIVE_SUB_STATUSES = {"running", "streaming"}
_SUB_TERMINAL_STATUSES = {"completed", "error", "stopped", "cancelled"}
_SUB_RESULT_MAX_CHARS = 20_000
_TASK_TERMINAL_STATUSES = {"completed", "failed", "stopped", "cancelled"}
_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _try_emit_event(event: dict) -> None:
    try:
        from src.web.event_bus import event_bus

        loop = asyncio.get_running_loop()
        loop.create_task(event_bus.emit_event(event))
    except RuntimeError:
        logger.debug("无法发送事件：无运行中的事件循环")
    except Exception:
        logger.debug("事件推送失败", exc_info=True)


class SessionLifecycleMixin:
    """把历史目录与热运行时分离，并管理可靠释放边界。"""

    sessions: dict[str, AgentSession]
    main_session_id: str | None
    _cold_cache_max_entries: int
    _workspace_manager: Any

    def _signal_session_update(self, session_id: str) -> None:
        self._session_changes.publish(session_id)

    async def _wait_for_session_update(
        self,
        session_id: str,
        timeout_seconds: float | None,
    ) -> bool:
        return await self._session_changes.wait(session_id, timeout_seconds)

    def register_main(self, session: AgentSession):
        session.session_type = "main"
        session.runtime_scope = "interactive"
        self.register_runtime_session(session)
        self.main_session_id = session.session_id
        if self._workspace_manager:
            ws_path = self._workspace_manager.create_workspace(session.session_id)
            session.workspace_path = str(ws_path)
        logger.info(
            "Main session 已注册: %s, workspace=%s",
            session.session_id,
            session.workspace_path or "none",
        )

    def get_main_session(self) -> AgentSession | None:
        if self.main_session_id:
            return self.sessions.get(self.main_session_id)
        return None

    def get_session(self, session_id: str) -> AgentSession | None:
        session = self.sessions.get(session_id)
        if session is not None:
            if session_id in self._cold_session_lru:
                self._cold_session_lru.move_to_end(session_id)
            return session
        if self._session_catalog.get(session_id) is None:
            return None
        session = AgentSession.load(session_id)
        if session is None:
            return None
        self._normalize_loaded_status(session)
        session._default_event_callback = self._make_event_callback(session_id)
        self.sessions[session_id] = session
        from src.agent.session import _persistence_manager

        _persistence_manager.register(session)
        self._session_catalog.upsert_session(session)
        self._cold_session_lru[session_id] = None
        self._evict_cold_sessions()
        return session

    def register_runtime_session(self, session: AgentSession) -> None:
        """注册需要 Graph/consumer 的热会话。"""
        self._cold_session_lru.pop(session.session_id, None)
        self.sessions[session.session_id] = session
        session._default_event_callback = self._make_event_callback(session.session_id)
        self._session_catalog.upsert_session(session)

    @staticmethod
    def _normalize_loaded_status(session: AgentSession) -> None:
        if session.session_type == "main" and session.status == "streaming":
            session.status = "running"
        elif session.session_type == "sub" and session.status in {"running", "streaming"}:
            session.status = "error"

    def _evict_cold_sessions(self) -> None:
        from src.agent.session import _persistence_manager

        dirty_examined = 0
        while len(self._cold_session_lru) > self._cold_cache_max_entries:
            session_id = next(iter(self._cold_session_lru))
            session = self.sessions.get(session_id)
            if session is not None and (
                session.compiled_graph is not None or session.invocation_active
            ):
                self._cold_session_lru.pop(session_id, None)
                continue
            if session is not None and session._save_dirty:
                self._cold_session_lru.move_to_end(session_id)
                dirty_examined += 1
                if dirty_examined >= len(self._cold_session_lru):
                    break
                continue
            self._cold_session_lru.pop(session_id, None)
            if session is not None:
                self._session_catalog.upsert_session(session)
            self.sessions.pop(session_id, None)
            _persistence_manager.unregister(session_id)

    def get_session_summaries(self) -> list[dict]:
        summaries = {
            metadata.session_id: metadata.to_summary()
            for metadata in self._session_catalog.values()
        }
        summaries.update({
            session_id: session.get_summary()
            for session_id, session in self.sessions.items()
        })
        return sorted(
            summaries.values(),
            key=lambda item: (item.get("updated_at", ""), item["session_id"]),
            reverse=True,
        )

    def get_main_session_summaries(self) -> list[dict]:
        summaries = {
            metadata.session_id: metadata.to_summary()
            for metadata in self._session_catalog.values()
            if metadata.session_type == "main"
        }
        summaries.update({
            session_id: session.get_summary()
            for session_id, session in self.sessions.items()
            if session.session_type == "main"
        })
        return sorted(
            summaries.values(),
            key=lambda item: (item.get("updated_at", ""), item["session_id"]),
            reverse=True,
        )

    def get_total_session_count(self) -> int:
        return len({
            *[metadata.session_id for metadata in self._session_catalog.values()],
            *self.sessions.keys(),
        })

    def get_active_sub_count(self) -> int:
        return sum(
            1 for session in self.sessions.values()
            if session.session_type == "sub"
            and session.status in {"running", "streaming"}
        )

    def get_all_sub_sessions(self) -> list[AgentSession]:
        return [
            session for session in self.sessions.values()
            if session.session_type == "sub"
        ]

    def get_main_sessions(self) -> list[AgentSession]:
        """返回当前热驻留的 main 类型会话。"""
        return [
            session for session in self.sessions.values()
            if session.session_type == "main"
        ]

    def _get_root_main(self, session_id: str) -> str | None:
        hot_session = self.sessions.get(session_id)
        hot_visited: set[str] = set()
        while hot_session and hot_session.session_id not in hot_visited:
            if hot_session.session_type == "main":
                return hot_session.session_id
            hot_visited.add(hot_session.session_id)
            hot_session = self.sessions.get(hot_session.parent_id or "")

        summaries = {
            summary["session_id"]: summary
            for summary in self.get_session_summaries()
        }
        session = summaries.get(session_id)
        visited: set[str] = set()
        while session and session["session_id"] not in visited:
            if session.get("type") == "main":
                return session["session_id"]
            visited.add(session["session_id"])
            session = summaries.get(session.get("parent_id", ""))
        return None

    def _make_event_callback(self, session_id: str):
        """为 Session 创建统一的 Chat/Event 流回调。"""
        async def callback(event: dict):
            from src.web.event_bus import event_bus

            session = self.sessions.get(session_id)
            event["session_id"] = session_id
            event_type = event.get("type", "")
            if event_type in {"stream_start", "stream_end", "error"}:
                self._signal_session_update(session_id)
            if event_type in (
                "stream_start", "stream_end", "token", "reasoning_token",
                "tool_call_delta", "error", "tool_start", "tool_end", "llm_usage",
            ):
                await event_bus.emit_chat(event)
            if event_type == "stream_start":
                await event_bus.emit_event({
                    "type": "session_update",
                    "action": "status_changed",
                    "session_id": session_id,
                    "status": "streaming",
                })
            elif event_type == "stream_end":
                await event_bus.emit_event({
                    "type": "session_update",
                    "action": "status_changed",
                    "session_id": session_id,
                    "status": session.status if session else "completed",
                })
                if session:
                    serialized = [
                        message for message in session.record
                        if is_visible_to_frontend(message)
                    ]
                    await event_bus.emit_chat({
                        "type": "chain_end",
                        "messages": serialized,
                        "session_id": session_id,
                    })
        return callback

    @staticmethod
    def _sub_wait_state(summary: dict, *, task_active: bool) -> tuple[bool, bool]:
        status = str(summary.get("status") or "")
        terminal = status in _SUB_TERMINAL_STATUSES
        attention_required = (
            status == "waiting"
            or (status in _ACTIVE_SUB_STATUSES and not task_active)
        )
        return terminal, attention_required

    def _attach_sub_result(self, summary: dict) -> dict:
        session = self.get_session(summary["session_id"])
        if session is None:
            return summary
        output = session.get_last_assistant_message()
        if not output:
            return summary
        enriched = dict(summary)
        enriched["final_output"] = output[:_SUB_RESULT_MAX_CHARS]
        enriched["final_output_truncated"] = len(output) > _SUB_RESULT_MAX_CHARS
        return enriched

    async def check_sub_progress(
        self,
        session_id: str = "",
        wait_for: str = "none",
        timeout_seconds: float | None = 0,
    ) -> dict:
        if wait_for != "none" and not session_id:
            return {
                "success": False,
                "message": "等待子会话时必须提供 session_id",
                "error": "session_id_required_for_wait",
            }

        started_at = time.monotonic()
        waited_for_change = False
        while True:
            summaries = [
                summary for summary in self.get_session_summaries()
                if summary.get("type") == "sub"
            ]
            summary = next(
                (item for item in summaries if item["session_id"] == session_id),
                None,
            ) if session_id else None

            if session_id and not summary:
                return {"success": False, "message": f"未找到会话 {session_id}"}

            task = self._sub_tasks.get(session_id) if session_id else None
            task_active = task is not None and not task.done()
            terminal, attention_required = (
                self._sub_wait_state(summary, task_active=task_active)
                if summary is not None
                else (False, False)
            )

            if wait_for == "none":
                break
            if terminal or attention_required:
                wait_outcome = "terminal" if terminal else "attention_required"
                break
            if wait_for == "change" and waited_for_change:
                wait_outcome = "changed"
                break

            elapsed = time.monotonic() - started_at
            remaining = (
                None
                if timeout_seconds is None
                else max(0.0, timeout_seconds - elapsed)
            )
            changed = await self._wait_for_session_update(session_id, remaining)
            if not changed:
                wait_outcome = "timeout"
                break
            waited_for_change = True

        if session_id:
            assert summary is not None
            result_summary = (
                self._attach_sub_result(summary)
                if wait_for != "none" and terminal
                else summary
            )
            result = {"success": True, "sessions": [result_summary]}
            if wait_for != "none":
                result.update({
                    "wait_outcome": wait_outcome,
                    "elapsed_seconds": round(time.monotonic() - started_at, 3),
                    "terminal": terminal,
                    "attention_required": attention_required,
                })
            return result

        summaries = [
            summary for summary in self.get_session_summaries()
            if summary.get("type") == "sub"
        ]
        if not summaries:
            return {"success": True, "message": "当前没有子会话", "sessions": []}
        return {
            "success": True,
            "active_count": self.get_active_sub_count(),
            "total_count": len(summaries),
            "sessions": summaries,
        }

    async def check_main_progress(self, session_id: str = "") -> dict:
        summaries = self.get_main_session_summaries()
        if session_id:
            summary = next(
                (item for item in summaries if item["session_id"] == session_id),
                None,
            )
            if not summary:
                return {"success": False, "message": f"未找到会话 {session_id}"}
            return {"success": True, "sessions": [summary]}
        if not summaries:
            return {"success": True, "message": "当前没有主会话", "sessions": []}
        return {
            "success": True,
            "total_count": len(summaries),
            "sessions": summaries,
        }

    async def kill_session(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if not session:
            return {"success": False, "message": f"未找到会话 {session_id}"}
        if session_id == self.main_session_id:
            return {"success": False, "message": "不能终止当前 Chat 活跃的主会话"}
        if session.status not in {"running", "waiting", "completed", "streaming"}:
            return {
                "success": False,
                "message": f"子会话 {session_id} 当前状态为 {session.status}，无法终止",
            }
        stopped = await session.cancel_active_invocation(timeout=5.0)
        if not stopped:
            return {"success": False, "message": f"会话 {session_id} 仍在停止中，请稍后重试"}
        task = self._sub_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        session.status = "error"
        from src.web.event_bus import event_bus

        await event_bus.emit_chat({
            "type": "error",
            "message": f"会话 {session_id} 已被终止",
            "session_id": session_id,
            "terminal": True,
        })
        session.updated_at = datetime.now(timezone.utc).isoformat()
        await session.async_save()
        self._signal_session_update(session_id)
        logger.info("Sub session %s 已被终止", session_id)
        _try_emit_event({
            "type": "session_update",
            "action": "killed",
            "session_id": session_id,
            "status": "error",
        })
        return {"success": True, "message": f"子会话 {session_id} 已终止"}

    async def delete_session(self, session_id: str) -> dict:
        session = self.get_session(session_id)
        if not session:
            return {"success": False, "message": f"未找到会话 {session_id}"}
        if session_id == self.main_session_id:
            return {"success": False, "message": "不能删除当前活跃的主会话"}
        if session.status == "streaming" or session.invocation_active or session._invoke_lock.locked():
            return {"success": False, "message": "会话正在生成中，请先终止后再删除"}
        session.request_termination()
        task = self._sub_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._sub_tasks.pop(session_id, None)
        if self._workspace_manager and session.session_type == "sub":
            self._workspace_manager.cleanup_workspace(session_id)
        from src.agent.session import _persistence_manager

        _persistence_manager.unregister(session_id)
        self.sessions.pop(session_id, None)
        self._cold_session_lru.pop(session_id, None)
        self._session_catalog.remove(session_id)
        self._signal_session_update(session_id)
        from src.web.event_bus import event_bus

        await event_bus.emit_chat({
            "type": "error",
            "message": f"会话 {session_id} 已被删除",
            "session_id": session_id,
            "terminal": True,
        })
        event_bus.clear_session(session_id)
        file_path = SESSIONS_DIR / f"{session_id}.json"
        if file_path.exists():
            file_path.unlink()
        _try_emit_event({
            "type": "session_update",
            "action": "deleted",
            "session_id": session_id,
        })
        logger.info("会话 %s 已删除", session_id)
        return {"success": True, "message": f"会话 {session_id} 已删除"}

    async def delete_sessions(self, session_ids: list[str]) -> dict:
        results = []
        success_count = 0
        fail_count = 0
        for session_id in session_ids:
            result = await self.delete_session(session_id)
            results.append({"session_id": session_id, **result})
            if result["success"]:
                success_count += 1
            else:
                fail_count += 1
        return {
            "success": fail_count == 0,
            "message": f"删除完成: {success_count} 成功, {fail_count} 失败",
            "total": len(session_ids),
            "success_count": success_count,
            "fail_count": fail_count,
            "details": results,
        }

    def get_session_tree(self, main_id: str | None = None) -> dict:
        summaries = self.get_session_summaries()
        by_id = {summary["session_id"]: summary for summary in summaries}

        def root_main_id(session_id: str) -> str | None:
            session = by_id.get(session_id)
            visited: set[str] = set()
            while session and session["session_id"] not in visited:
                if session.get("type") == "main":
                    return session["session_id"]
                visited.add(session["session_id"])
                session = by_id.get(session.get("parent_id", ""))
            return None

        if main_id:
            main = by_id.get(main_id)
            if not main or main.get("type") != "main":
                return {"error": f"未找到主会话 {main_id}"}
            children = [
                summary for summary in summaries
                if summary.get("type") == "sub"
                and root_main_id(summary["session_id"]) == main_id
            ]
            return {"main": main, "children": children}

        mains = [summary for summary in summaries if summary.get("type") == "main"]
        children_by_main: dict[str, list[dict]] = {}
        for summary in summaries:
            if summary.get("type") != "sub":
                continue
            root_id = root_main_id(summary["session_id"])
            if root_id:
                children_by_main.setdefault(root_id, []).append(summary)
        return {
            "trees": [
                {
                    "main": main,
                    "children": children_by_main.get(main["session_id"], []),
                }
                for main in mains
            ],
        }

    def save_all(self):
        for session in self.sessions.values():
            session.save()

    def load_sessions(self):
        scan_result = self._session_catalog.scan(SESSIONS_DIR)
        prune_result = self._prune_terminal_workflow_history()
        hot_metadata = [
            metadata for metadata in self._session_catalog.values()
            if metadata.session_type == "main"
            and (
                metadata.runtime_scope == "interactive"
                or metadata.status == "running"
            )
        ]
        hot_metadata.sort(
            key=lambda metadata: (
                metadata.runtime_scope == "interactive",
                metadata.updated_at,
                metadata.session_id,
            ),
            reverse=True,
        )
        for metadata in hot_metadata:
            try:
                session = AgentSession.load(metadata.session_id)
                if session:
                    self._normalize_loaded_status(session)
                    self.register_runtime_session(session)
                    if session.runtime_scope == "interactive" and self.main_session_id is None:
                        self.main_session_id = session.session_id
            except Exception as exc:
                logger.error("加载 session %s 失败: %s", metadata.session_id, exc)
        logger.info(
            "Session 目录索引完成: scanned=%s total=%s hot=%s errors=%s cache_limit=%s "
            "history_limit=%s pruned=%s freed_bytes=%s",
            scan_result["scanned"],
            self.get_total_session_count(),
            len(self.sessions),
            scan_result["errors"] + prune_result["errors"],
            self._cold_cache_max_entries,
            self._history_max_entries,
            len(prune_result["deleted"]),
            prune_result["deleted_bytes"],
        )

    @staticmethod
    def _task_allows_history_prune(metadata: SessionMetadata) -> bool:
        workflow_id = metadata.workflow_id
        task_id = metadata.task_id
        if not workflow_id or not task_id:
            return True
        if (
            not _SAFE_ID_PATTERN.fullmatch(workflow_id)
            or not _SAFE_ID_PATTERN.fullmatch(task_id)
        ):
            logger.warning(
                "Session 关联了非法 TaskRef，跳过历史清理: session=%s workflow=%r task=%r",
                metadata.session_id,
                workflow_id,
                task_id,
            )
            return False
        task_file = WORKFLOWS_DIR / workflow_id / "tasks" / f"{task_id}.json"
        try:
            task_data = json.loads(task_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return True
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            logger.warning(
                "无法确认 Session 关联 Task 终态，跳过历史清理: session=%s task=%s error=%s",
                metadata.session_id,
                task_id,
                exc,
            )
            return False
        if not isinstance(task_data, dict):
            logger.warning(
                "Session 关联 Task 文件不是对象，跳过历史清理: session=%s task=%s",
                metadata.session_id,
                task_id,
            )
            return False
        return task_data.get("status") in _TASK_TERMINAL_STATUSES

    def _prune_terminal_workflow_history(self) -> dict[str, Any]:
        result = self._session_catalog.prune_terminal_workflow_history(
            SESSIONS_DIR,
            max_entries=self._history_max_entries,
            protected_session_ids=self.sessions,
            can_delete=self._task_allows_history_prune,
        )
        if result["deleted"]:
            from src.web.event_bus import event_bus

            for session_id in result["deleted"]:
                event_bus.clear_session(session_id)
            logger.info(
                "Workflow Session 历史已裁剪: deleted=%s freed_bytes=%s limit=%s",
                len(result["deleted"]),
                result["deleted_bytes"],
                self._history_max_entries,
            )
        return result

    async def release_workflow_task_sessions(
        self,
        workflow_id: str,
        task_id: str,
    ) -> dict[str, int]:
        candidates = [
            session_id
            for session_id, session in list(self.sessions.items())
            if session.workflow_id == workflow_id
            and session.task_id == task_id
            and getattr(session, "runtime_scope", "interactive") == "workflow"
        ]
        released = 0
        retained = 0
        for session_id in candidates:
            if await self._release_runtime_session(session_id):
                released += 1
            else:
                retained += 1
        self._prune_terminal_workflow_history()
        return {"matched": len(candidates), "released": released, "retained": retained}

    async def _release_runtime_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return True
        session.request_termination()
        sub_task = self._sub_tasks.get(session_id)
        current_task = asyncio.current_task()
        if sub_task is not None and sub_task is not current_task and not sub_task.done():
            sub_task.cancel()
            try:
                await asyncio.wait_for(sub_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if not await session.cancel_active_invocation(timeout=5.0):
            logger.error("Session 仍有活跃调用，保留驻留: %s", session_id)
            return False
        await session.stop_consumer()
        if session.status in {"running", "streaming", "waiting"}:
            session.status = "error"
        session.updated_at = datetime.now(timezone.utc).isoformat()
        try:
            await session.async_save(force=True, strict=True)
        except Exception:
            logger.exception("Session 最终保存失败，保留驻留: %s", session_id)
            return False
        self.detach_saved_runtime_session(session_id)
        logger.info("Workflow Session 已释放: %s", session_id)
        return True

    def detach_saved_runtime_session(self, session_id: str) -> None:
        """在调用方完成严格保存后，从运行时注册表中分离 Session。"""
        from src.agent.session import _persistence_manager

        session = self.sessions.get(session_id)
        if session is not None:
            self._session_catalog.upsert_session(session)
        self.sessions.pop(session_id, None)
        self._cold_session_lru.pop(session_id, None)
        self._sub_tasks.pop(session_id, None)
        _persistence_manager.unregister(session_id)
        from src.web.event_bus import event_bus

        event_bus.clear_session(session_id)

    async def shutdown(self):
        from src.agent.session import _persistence_manager

        for session_id in list(self._cold_session_lru):
            session = self.sessions.get(session_id)
            if session is not None and session._save_dirty:
                await session.async_save(force=True)
            self.sessions.pop(session_id, None)
            _persistence_manager.unregister(session_id)
        self._cold_session_lru.clear()
        for session_id, task in list(self._sub_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        for session in self.sessions.values():
            if session.session_type == "main" and session.record:
                await self._notify_session_end(session)
            await session.stop_consumer()
            if session.status in {"running", "streaming"}:
                session.status = "error"
            await session.async_save(force=True)
            _persistence_manager.unregister(session.session_id)
        await _persistence_manager.stop()
        logger.info("SessionManager 已关闭，所有 session 状态已保存")
