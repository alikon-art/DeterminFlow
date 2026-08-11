from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import src.agent.session as session_module
import src.agent.session_lifecycle as session_lifecycle_module
import src.agent.session_retention as session_retention_module
import src.workflow.manager as workflow_manager_module
import src.workflow.task_queries as task_queries_module
from src.agent.session import AgentSession
from src.agent.session_manager import SessionManager
from src.web.event_bus import event_bus
from src.workflow.definition import WorkflowDef
from src.workflow.engine import WorkflowEngine
from src.workflow.manager import WorkflowManager
from src.workflow.runtime_models import WorkflowTask


def _write_session(
    sessions_dir: Path,
    session_id: str,
    *,
    session_type: str,
    status: str = "completed",
    parent_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    task_description: str = "",
    runtime_scope: str | None = None,
    content: str = "result",
    updated_at: str | None = None,
) -> None:
    data = {
        "session_id": session_id,
        "session_type": session_type,
        "parent_id": parent_id,
        "status": status,
        "task_description": task_description,
        "system_prompt": "system",
        "agent_type": "main" if session_type == "main" else "writer",
        "created_at": "2026-08-04T00:00:00+00:00",
        "updated_at": updated_at or f"2026-08-04T00:00:{len(session_id):02d}+00:00",
        "record": [
            {"id": "msg_00001", "type": "user", "content": "input"},
            {"id": "msg_00002", "type": "assistant", "content": content},
        ],
        "context": {"messages": []},
    }
    for key, value in {
        "workflow_id": workflow_id,
        "task_id": task_id,
        "runtime_scope": runtime_scope,
    }.items():
        if value is not None:
            data[key] = value
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{session_id}.json").write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_task(
    workflows_dir: Path,
    workflow_id: str,
    task_id: str,
    *,
    status: str,
) -> None:
    task_file = workflows_dir / workflow_id / "tasks" / f"{task_id}.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(
        json.dumps({
            "workflow_id": workflow_id,
            "task_id": task_id,
            "status": status,
        }),
        encoding="utf-8",
    )


@pytest.fixture
def isolated_sessions(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(session_lifecycle_module, "SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", sessions_dir)
    yield sessions_dir
    for file_path in sessions_dir.glob("*.json"):
        session_module._persistence_manager.unregister(file_path.stem)


def test_startup_indexes_history_without_hydrating_terminal_workflow_sessions(
    isolated_sessions,
):
    _write_session(
        isolated_sessions,
        "chat-main",
        session_type="main",
        runtime_scope="interactive",
    )
    _write_session(
        isolated_sessions,
        "wf-finished",
        session_type="main",
        workflow_id="wf-example",
        task_id="task-old",
        task_description="Workflow: wf-example",
    )
    _write_session(
        isolated_sessions,
        "wf-running",
        session_type="main",
        status="running",
        workflow_id="wf-example",
        task_id="task-live",
        task_description="Workflow: wf-example",
    )
    _write_session(
        isolated_sessions,
        "sub-finished",
        session_type="sub",
        parent_id="wf-finished",
        workflow_id="wf-example",
        task_id="task-old",
    )
    _write_session(
        isolated_sessions,
        "sub-interrupted",
        session_type="sub",
        status="streaming",
        parent_id="wf-running",
        workflow_id="wf-example",
        task_id="task-live",
    )

    manager = SessionManager(cold_cache_max_entries=2)
    manager.load_sessions()

    assert set(manager.sessions) == {"chat-main", "wf-running"}
    assert manager.main_session_id == "chat-main"
    assert manager.get_total_session_count() == 5
    summaries = {
        item["session_id"]: item for item in manager.get_session_summaries()
    }
    assert summaries["wf-finished"]["last_message"] == "result"
    assert summaries["sub-interrupted"]["status"] == "error"
    tree = manager.get_session_tree(main_id="wf-finished")
    assert [child["session_id"] for child in tree["children"]] == ["sub-finished"]


def test_deleting_historical_main_cascades_sessions_and_workflow_tasks(
    isolated_sessions,
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(task_queries_module, "WORKFLOWS_DIR", workflows_dir)

    _write_session(
        isolated_sessions,
        "main-current",
        session_type="main",
        runtime_scope="interactive",
    )
    _write_session(
        isolated_sessions,
        "main-history",
        session_type="main",
        status="running",
        runtime_scope="interactive",
    )
    _write_session(
        isolated_sessions,
        "workflow-agent",
        session_type="sub",
        parent_id="main-history",
        workflow_id="wf-demo",
        task_id="task-demo",
    )
    _write_session(
        isolated_sessions,
        "workflow-main",
        session_type="main",
        workflow_id="wf-demo",
        task_id="task-demo",
        task_description="Workflow: wf-demo",
        runtime_scope="workflow",
    )
    _write_session(
        isolated_sessions,
        "workflow-agent-child",
        session_type="sub",
        parent_id="workflow-agent",
        workflow_id="wf-demo",
        task_id="task-demo",
    )

    session_manager = SessionManager()
    session_manager.load_sessions()
    session_manager.main_session_id = "main-current"
    workflow_manager = WorkflowManager(session_manager)
    session_manager.inject_dependencies(workflow_manager=workflow_manager)

    task_path = workflows_dir / "wf-demo" / "tasks" / "task-demo.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_manager._save_task(WorkflowTask(
        workflow_id="wf-demo",
        task_id="task-demo",
        main_session_id="main-history",
        status="completed",
    ))

    result = asyncio.run(session_manager.delete_session("main-history"))

    assert result["success"] is True
    assert set(result["deleted_session_ids"]) == {
        "main-history",
        "workflow-agent",
        "workflow-agent-child",
        "workflow-main",
    }
    assert result["deleted_task_ids"] == ["task-demo"]
    assert not task_path.exists()
    assert (isolated_sessions / "main-current.json").exists()
    assert not (isolated_sessions / "main-history.json").exists()
    assert not (isolated_sessions / "workflow-agent.json").exists()
    assert not (isolated_sessions / "workflow-agent-child.json").exists()

    deleted_current = asyncio.run(session_manager.delete_session("main-current"))
    assert deleted_current["success"] is True
    assert deleted_current["deleted_session_ids"] == ["main-current"]
    assert session_manager.main_session_id is None


def test_historical_session_load_is_bounded_by_lru(isolated_sessions):
    for session_id in ("cold-a", "cold-b", "cold-c"):
        _write_session(
            isolated_sessions,
            session_id,
            session_type="sub",
            workflow_id="wf-example",
            task_id=f"task-{session_id}",
        )

    manager = SessionManager(cold_cache_max_entries=2)
    manager.load_sessions()

    assert manager.get_session("cold-a") is not None
    assert manager.get_session("cold-b") is not None
    assert set(manager.sessions) == {"cold-a", "cold-b"}
    assert manager.get_session("cold-c") is not None
    assert set(manager.sessions) == {"cold-b", "cold-c"}
    assert list(manager._cold_session_lru) == ["cold-b", "cold-c"]


def test_startup_caps_terminal_workflow_history_without_deleting_live_tasks(
    isolated_sessions,
    tmp_path,
    monkeypatch,
):
    workflows_dir = tmp_path / "workflows"
    monkeypatch.setattr(session_lifecycle_module, "WORKFLOWS_DIR", workflows_dir)
    for index in range(3):
        session_id = f"wf-terminal-{index}"
        task_id = f"task-terminal-{index}"
        _write_session(
            isolated_sessions,
            session_id,
            session_type="sub",
            status="completed",
            workflow_id="wf-novel",
            task_id=task_id,
            runtime_scope="workflow",
            updated_at=f"2026-08-04T00:00:0{index}+00:00",
        )
        _write_task(
            workflows_dir,
            "wf-novel",
            task_id,
            status="completed",
        )

    _write_session(
        isolated_sessions,
        "wf-live-task",
        session_type="sub",
        status="completed",
        workflow_id="wf-novel",
        task_id="task-live",
        runtime_scope="workflow",
        updated_at="2026-08-03T00:00:00+00:00",
    )
    _write_task(
        workflows_dir,
        "wf-novel",
        "task-live",
        status="running",
    )
    _write_session(
        isolated_sessions,
        "chat-main",
        session_type="main",
        runtime_scope="interactive",
        updated_at="2026-08-02T00:00:00+00:00",
    )
    _write_session(
        isolated_sessions,
        "wf-recoverable-orphan",
        session_type="main",
        status="running",
        workflow_id="wf-novel",
        task_id="task-missing",
        runtime_scope="workflow",
        updated_at="2026-08-01T00:00:00+00:00",
    )

    manager = SessionManager(history_max_entries=2)
    manager.load_sessions()

    assert not (isolated_sessions / "wf-terminal-0.json").exists()
    assert (isolated_sessions / "wf-terminal-1.json").exists()
    assert (isolated_sessions / "wf-terminal-2.json").exists()
    assert (isolated_sessions / "wf-live-task.json").exists()
    assert (isolated_sessions / "chat-main.json").exists()
    assert (isolated_sessions / "wf-recoverable-orphan.json").exists()
    assert manager.get_total_session_count() == 5


def test_main_summaries_do_not_materialize_every_catalog_entry(
    isolated_sessions,
    monkeypatch,
):
    _write_session(
        isolated_sessions,
        "chat-main",
        session_type="main",
        runtime_scope="interactive",
    )
    _write_session(
        isolated_sessions,
        "workflow-sub",
        session_type="sub",
        workflow_id="wf-novel",
        task_id="task-old",
        runtime_scope="workflow",
    )
    manager = SessionManager(history_max_entries=10)
    manager.load_sessions()
    monkeypatch.setattr(
        manager,
        "get_session_summaries",
        lambda: (_ for _ in ()).throw(AssertionError("不得构造全量摘要")),
    )

    summaries = manager.get_main_session_summaries()

    assert [summary["session_id"] for summary in summaries] == ["chat-main"]


def test_prestart_retention_process_prunes_without_hydrating_sessions(
    isolated_sessions,
    monkeypatch,
):
    for index in range(2):
        _write_session(
            isolated_sessions,
            f"wf-prestart-{index}",
            session_type="sub",
            status="completed",
            workflow_id="wf-novel",
            task_id=f"task-{index}",
            runtime_scope="workflow",
            updated_at=f"2026-08-04T00:00:0{index}+00:00",
        )
    monkeypatch.setattr(session_retention_module, "SESSIONS_DIR", isolated_sessions)
    monkeypatch.setattr(session_lifecycle_module, "SESSIONS_DIR", isolated_sessions)
    monkeypatch.setattr(
        session_retention_module,
        "SessionManager",
        lambda: SessionManager(history_max_entries=1),
    )
    monkeypatch.setattr(
        AgentSession,
        "load",
        lambda _session_id: (_ for _ in ()).throw(
            AssertionError("启动前裁剪不得反序列化完整 Session")
        ),
    )

    session_retention_module.main()

    assert not (isolated_sessions / "wf-prestart-0.json").exists()
    assert (isolated_sessions / "wf-prestart-1.json").exists()


def test_dirty_historical_session_is_flushed_before_shutdown(isolated_sessions):
    _write_session(
        isolated_sessions,
        "cold-a",
        session_type="sub",
        workflow_id="wf-example",
        task_id="task-1",
    )
    manager = SessionManager(cold_cache_max_entries=1)
    manager.load_sessions()
    session = manager.get_session("cold-a")
    assert session is not None
    asyncio.run(session.add_message("assistant", "updated"))

    asyncio.run(manager.shutdown())

    persisted = json.loads(
        (isolated_sessions / "cold-a.json").read_text(encoding="utf-8")
    )
    assert persisted["record"][-1]["content"] == "updated"


def test_terminal_workflow_release_preserves_history_and_interactive_main(
    isolated_sessions,
):
    manager = SessionManager(cold_cache_max_entries=2)
    workflow_session = AgentSession(
        session_id="wf-runtime",
        session_type="sub",
        workflow_id="wf-example",
        task_id="task-1",
        runtime_scope="workflow",
    )
    workflow_session.status = "completed"
    workflow_session.record = [
        {"id": "msg_00001", "type": "assistant", "content": "chapter"},
    ]
    interactive_main = AgentSession(
        session_id="chat-main",
        session_type="main",
        workflow_id="wf-example",
        task_id="task-1",
        runtime_scope="interactive",
    )
    manager.register_runtime_session(workflow_session)
    manager.register_main(interactive_main)

    result = asyncio.run(
        manager.release_workflow_task_sessions("wf-example", "task-1")
    )

    assert result == {"matched": 1, "released": 1, "retained": 0}
    assert set(manager.sessions) == {"chat-main"}
    restored = manager.get_session("wf-runtime")
    assert restored is not None
    assert restored.get_last_assistant_message() == "chapter"
    assert restored.compiled_graph is None
    assert "chat-main" in manager.sessions


def test_terminal_workflow_release_clears_event_bus_state(isolated_sessions):
    _write_session(
        isolated_sessions,
        "wf-runtime-old",
        session_type="sub",
        status="completed",
        workflow_id="wf-novel",
        task_id="task-old",
        runtime_scope="workflow",
        updated_at="2026-08-03T00:00:00+00:00",
    )
    manager = SessionManager(history_max_entries=1)
    manager.load_sessions()
    workflow_session = AgentSession(
        session_id="wf-runtime-event",
        session_type="sub",
        workflow_id="wf-novel",
        task_id="task-1",
        runtime_scope="workflow",
    )
    workflow_session.status = "completed"
    manager.register_runtime_session(workflow_session)
    event_bus._session_revisions[workflow_session.session_id] = 7

    result = asyncio.run(
        manager.release_workflow_task_sessions("wf-novel", "task-1")
    )

    assert result == {"matched": 1, "released": 1, "retained": 0}
    assert event_bus.get_session_revision(workflow_session.session_id) == 0
    assert not (isolated_sessions / "wf-runtime-old.json").exists()
    assert (isolated_sessions / "wf-runtime-event.json").exists()


def test_final_save_failure_retains_workflow_session(isolated_sessions, monkeypatch):
    manager = SessionManager()
    session = AgentSession(
        session_id="wf-runtime",
        session_type="sub",
        workflow_id="wf-example",
        task_id="task-1",
        runtime_scope="workflow",
    )
    session.status = "completed"
    manager.register_runtime_session(session)

    async def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session, "async_save", fail_save)
    result = asyncio.run(
        manager.release_workflow_task_sessions("wf-example", "task-1")
    )

    assert result == {"matched": 1, "released": 0, "retained": 1}
    assert manager.sessions["wf-runtime"] is session


def test_terminal_task_is_saved_before_session_release(tmp_path, monkeypatch):
    workflows_dir = tmp_path / "workflows"
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    observations: list[str] = []

    class RecordingSessionManager:
        sessions: dict = {}

        async def release_workflow_task_sessions(self, workflow_id: str, task_id: str):
            task_file = workflows_dir / workflow_id / "tasks" / f"{task_id}.json"
            observations.append(json.loads(task_file.read_text(encoding="utf-8"))["status"])
            return {"matched": 1, "released": 1, "retained": 0}

    manager = WorkflowManager(RecordingSessionManager())
    task = WorkflowTask(
        workflow_id="wf-example",
        task_id="task-1",
        status="running",
    )

    class CompletedEngine:
        async def execute_task(self, _definition, current, _from_node_id):
            current.status = "completed"
            return current

    manager._engine = CompletedEngine()
    asyncio.run(
        manager._run_task_coroutine(
            "wf-example",
            "task-1",
            WorkflowDef(workflow_id="wf-example"),
            task,
            None,
        )
    )

    assert observations == ["completed"]


@pytest.mark.parametrize("uses_pre_created_session", [False, True])
def test_successful_task_is_not_relabelled_when_terminal_save_fails(
    tmp_path,
    monkeypatch,
    uses_pre_created_session,
):
    workflows_dir = tmp_path / "workflows"
    monkeypatch.setattr(workflow_manager_module, "WORKFLOWS_DIR", workflows_dir)
    released: list[str] = []

    class RecordingSessionManager:
        sessions: dict = {}

        async def release_workflow_task_sessions(self, _workflow_id: str, task_id: str):
            released.append(task_id)
            return {"matched": 1, "released": 1, "retained": 0}

    manager = WorkflowManager(RecordingSessionManager())
    task = WorkflowTask(
        workflow_id="wf-script",
        task_id="task-script",
        status="running",
    )

    class CompletedEngine:
        async def execute_task(self, _definition, current, *args, **kwargs):
            current.status = "completed"
            return current

    manager._engine = CompletedEngine()

    def fail_save(_task):
        raise OSError("temporary file contention")

    monkeypatch.setattr(manager, "_save_task", fail_save)
    if uses_pre_created_session:
        run = manager._run_task_with_session(
            task.workflow_id,
            task.task_id,
            WorkflowDef(workflow_id=task.workflow_id),
            task,
            "workflow-main",
        )
    else:
        run = manager._run_task_coroutine(
            task.workflow_id,
            task.task_id,
            WorkflowDef(workflow_id=task.workflow_id),
            task,
            None,
        )
    asyncio.run(run)

    assert task.status == "completed"
    assert released == []


def test_run_artifact_save_failures_do_not_relabel_successful_task(
    tmp_path,
    monkeypatch,
):
    class FailingWorkflowMain:
        status = "running"

        async def async_save(self):
            raise OSError("session file busy")

    session_manager = type(
        "SessionManagerStub",
        (),
        {"sessions": {"workflow-main": FailingWorkflowMain()}},
    )()
    engine = WorkflowEngine(session_manager)
    engine.set_workspace_manager(
        type(
            "WorkspaceManagerStub",
            (),
            {
                "resolve_workflow_workspace": staticmethod(
                    lambda _workflow_id, override=None: tmp_path
                )
            },
        )()
    )
    run_record_attempts: list[str] = []

    def fail_run_record(_workflow_id, record):
        run_record_attempts.append(record.status)
        raise OSError("run record file busy")

    monkeypatch.setattr(engine, "_save_run_record", fail_run_record)
    task = WorkflowTask(
        workflow_id="wf-script",
        task_id="task-script",
        status="running",
    )

    result = asyncio.run(engine.execute_task(
        WorkflowDef(workflow_id=task.workflow_id),
        task,
        pre_created_session_id="workflow-main",
    ))

    assert result.status == "completed"
    assert run_record_attempts == ["completed"]


def test_retry_waiting_task_keeps_runtime_sessions():
    calls: list[tuple[str, str]] = []

    class RecordingSessionManager:
        sessions: dict = {}

        async def release_workflow_task_sessions(self, workflow_id: str, task_id: str):
            calls.append((workflow_id, task_id))
            return {"matched": 0, "released": 0, "retained": 0}

    manager = WorkflowManager(RecordingSessionManager())
    task = WorkflowTask(
        workflow_id="wf-example",
        task_id="task-1",
        status="retry_waiting",
    )

    asyncio.run(manager._release_terminal_task_sessions(task))

    assert calls == []
