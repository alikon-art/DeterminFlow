from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.workflow.definition import WorkflowDef, WorkflowNode, WorkflowTask
from src.workflow.engine import WorkflowEngine
from src.workflow.failure_policy import RECOVERY_REISSUE_TRIGGER
from src.workflow.manager import WorkflowManager
from src.workflow.nodes.agent import AgentNode
from src.workflow.nodes.base import NodeContext
from src.workflow.runtime_models import NodeExecutionState


def test_agent_approval_recovery_reissues_without_new_model_session(monkeypatch):
    class RecoverySessionManager:
        def __init__(self):
            self.create_calls = 0
            self.sessions = {
                "session-old": SimpleNamespace(
                    record=[{"type": "assistant", "content": "frozen output"}],
                    get_cumulative_token_usage=lambda: None,
                ),
            }

        async def create_sub_session(self, **_kwargs):
            self.create_calls += 1
            raise AssertionError("恢复审批不应重新调用模型")

    approval_requests: list[str] = []

    async def fake_handle(
        _self,
        _ctx,
        summary,
        _status,
        _error,
        completion_event,
        _session_manager,
    ):
        approval_requests.append(summary)
        completion_event.set()

    monkeypatch.setattr(AgentNode, "_handle_approval", fake_handle)
    manager = RecoverySessionManager()
    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
    )
    state = NodeExecutionState(
        node_id=node.id,
        status="running",
        session_id="session-old",
        summary="generated summary",
        next_attempt_trigger=RECOVERY_REISSUE_TRIGGER,
    )
    result = asyncio.run(
        AgentNode().execute(
            NodeContext(
                definition=WorkflowDef(
                    workflow_id="wf-agent-reissue",
                    nodes=[node],
                ),
                node_def=node,
                node_state=state,
                needs_approval=True,
                session_manager=manager,
            )
        )
    )

    assert result.status == "completed"
    assert result.session_id == "session-old"
    assert result.outputs == {"draft": "frozen output"}
    assert manager.create_calls == 0
    assert approval_requests == ["generated summary"]


def test_agent_auto_flow_requests_approval_after_natural_completion(monkeypatch):
    class AutoFlowSessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "session-auto-flow"
            self.sessions[session_id] = SimpleNamespace(
                record=[{"type": "assistant", "content": "natural output"}],
                get_cumulative_token_usage=lambda: None,
            )
            kwargs["on_auto_complete"](
                session_id,
                "natural summary",
                "success",
                "",
            )
            return {"success": True, "session_id": session_id}

    approval_requests: list[str] = []

    async def fake_handle(
        _self,
        _ctx,
        summary,
        _status,
        _error,
        completion_event,
        _session_manager,
    ):
        approval_requests.append(summary)
        completion_event.set()

    monkeypatch.setattr(AgentNode, "_handle_approval", fake_handle)
    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
        auto_flow=True,
        enable_complete_node_task=False,
    )

    async def scenario():
        return await asyncio.wait_for(
            AgentNode().execute(
                NodeContext(
                    definition=WorkflowDef(
                        workflow_id="wf-agent-auto-flow-approval",
                        nodes=[node],
                    ),
                    node_def=node,
                    node_state=NodeExecutionState(node_id=node.id),
                    needs_approval=True,
                    session_manager=AutoFlowSessionManager(),
                )
            ),
            timeout=0.2,
        )

    result = asyncio.run(scenario())

    assert result.status == "completed"
    assert result.outputs == {"draft": "natural output"}
    assert approval_requests == ["natural summary"]


def test_agent_completion_callbacks_create_only_one_approval(monkeypatch):
    class DualCompletionSessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "session-dual-completion"
            self.sessions[session_id] = SimpleNamespace(
                record=[{"type": "assistant", "content": "final output"}],
                get_cumulative_token_usage=lambda: None,
            )
            kwargs["on_node_complete"](
                session_id,
                "tool summary",
                "success",
                "",
            )
            kwargs["on_auto_complete"](
                session_id,
                "natural summary",
                "success",
                "",
            )
            return {"success": True, "session_id": session_id}

    approval_requests: list[str] = []

    async def fake_handle(
        _self,
        _ctx,
        summary,
        _status,
        _error,
        completion_event,
        _session_manager,
    ):
        approval_requests.append(summary)
        completion_event.set()

    monkeypatch.setattr(AgentNode, "_handle_approval", fake_handle)
    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
    )

    async def scenario():
        return await asyncio.wait_for(
            AgentNode().execute(
                NodeContext(
                    definition=WorkflowDef(
                        workflow_id="wf-agent-single-approval",
                        nodes=[node],
                    ),
                    node_def=node,
                    node_state=NodeExecutionState(node_id=node.id),
                    needs_approval=True,
                    session_manager=DualCompletionSessionManager(),
                )
            ),
            timeout=0.2,
        )

    result = asyncio.run(scenario())

    assert result.status == "completed"
    assert result.outputs == {"draft": "final output"}
    assert approval_requests == ["tool summary"]


def test_agent_file_output_requires_a_final_ai_message(tmp_path):
    class EmptyOutputSessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "session-empty-output"
            self.sessions[session_id] = SimpleNamespace(
                record=[],
                get_cumulative_token_usage=lambda: None,
            )
            kwargs["on_auto_complete"](
                session_id,
                "graph completed without a final assistant message",
                "success",
                "",
            )
            return {"success": True, "session_id": session_id}

    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
        save_output_to_file=True,
        output_file_path="result.json",
        node_params={"output_format": "json"},
    )
    result = asyncio.run(
        AgentNode().execute(
            NodeContext(
                definition=WorkflowDef(
                    workflow_id="wf-agent-empty-output",
                    nodes=[node],
                ),
                node_def=node,
                node_state=NodeExecutionState(node_id=node.id),
                shared_ws=tmp_path,
                session_manager=EmptyOutputSessionManager(),
            )
        )
    )

    assert result.status == "failed"
    assert ("LLM 输出为空" in result.error or "没有最终 AI 输出" in result.error)
    assert result.outputs == {}
    assert not (tmp_path / "result.json").exists()


def test_agent_output_gate_does_not_fall_back_to_older_non_empty_message(tmp_path):
    class EmptyLatestOutputSessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "session-empty-latest-output"
            self.sessions[session_id] = SimpleNamespace(
                record=[
                    {"type": "assistant", "content": '{"body":"旧正文"}'},
                    {"type": "assistant", "content": ""},
                ],
                get_cumulative_token_usage=lambda: None,
            )
            kwargs["on_auto_complete"](
                session_id,
                "graph completed with an empty final message",
                "success",
                "",
            )
            return {"success": True, "session_id": session_id}

    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
        save_output_to_file=True,
        output_file_path="result.json",
        require_non_empty_output=True,
    )
    result = asyncio.run(
        AgentNode().execute(
            NodeContext(
                definition=WorkflowDef(
                    workflow_id="wf-agent-output-gate",
                    nodes=[node],
                ),
                node_def=node,
                node_state=NodeExecutionState(node_id=node.id),
                shared_ws=tmp_path,
                session_manager=EmptyLatestOutputSessionManager(),
            )
        )
    )

    assert result.status == "failed"
    assert "LLM 输出为空" in result.error
    assert result.outputs == {}
    assert not (tmp_path / "result.json").exists()


def test_agent_json_field_gate_fails_before_output_file_write(tmp_path):
    class ShortJsonOutputSessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "session-short-json-output"
            self.sessions[session_id] = SimpleNamespace(
                record=[{"type": "assistant", "content": '{"body":"太短"}'}],
                get_cumulative_token_usage=lambda: None,
            )
            kwargs["on_auto_complete"](
                session_id,
                "graph completed with short JSON body",
                "success",
                "",
            )
            return {"success": True, "session_id": session_id}

    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
        save_output_to_file=True,
        output_file_path="result.json",
        json_output_field="body",
        json_output_field_min_chars=10,
    )
    result = asyncio.run(
        AgentNode().execute(
            NodeContext(
                definition=WorkflowDef(
                    workflow_id="wf-agent-json-field-gate",
                    nodes=[node],
                ),
                node_def=node,
                node_state=NodeExecutionState(node_id=node.id),
                shared_ws=tmp_path,
                session_manager=ShortJsonOutputSessionManager(),
            )
        )
    )

    assert result.status == "failed"
    assert "字段 'body' 字数为 2，必须大于 10" in result.error
    assert result.outputs == {}
    assert not (tmp_path / "result.json").exists()


def test_agent_approval_recovery_route_failure_fails_without_waiting():
    class RecoverySessionManager:
        def __init__(self):
            self.route_calls = []
            self.sessions = {
                "main-current": SimpleNamespace(),
                "session-old": SimpleNamespace(
                    record=[{"type": "assistant", "content": "frozen output"}],
                    get_cumulative_token_usage=lambda: None,
                ),
            }

        async def create_sub_session(self, **_kwargs):
            raise AssertionError("恢复审批不应重新调用模型")

        async def route_message(self, **kwargs):
            self.route_calls.append(kwargs)
            return {
                "success": False,
                "message": "旧子会话与新 main 不在同一棵树",
            }

    manager = RecoverySessionManager()
    WorkflowEngine(manager)
    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
    )
    state = NodeExecutionState(
        node_id=node.id,
        status="running",
        session_id="session-old",
        summary="generated summary",
        next_attempt_trigger=RECOVERY_REISSUE_TRIGGER,
    )
    checkpoint_statuses: list[str] = []

    async def checkpoint():
        checkpoint_statuses.append(state.status)

    async def scenario():
        return await asyncio.wait_for(
            AgentNode().execute(
                NodeContext(
                    definition=WorkflowDef(
                        workflow_id="wf-agent-route-failure",
                        nodes=[node],
                    ),
                    node_def=node,
                    node_state=state,
                    needs_approval=True,
                    parent_id="main-current",
                    workflow_id="wf-agent-route-failure",
                    task_id="task-agent-route-failure",
                    session_manager=manager,
                    checkpoint=checkpoint,
                )
            ),
            timeout=0.2,
        )

    result = asyncio.run(scenario())

    assert result.status == "failed"
    assert "旧子会话与新 main 不在同一棵树" in result.error
    assert result.outputs == {}
    assert state.status == "failed"
    assert checkpoint_statuses == ["waiting_approval", "failed"]
    assert manager.route_calls == [
        {
            "from_session_id": "session-old",
            "to_session_id": "main-current",
            "content": manager.route_calls[0]["content"],
        }
    ]


def test_agent_approval_waiter_exists_before_request_is_routed():
    class ImmediateApprovalSessionManager:
        def __init__(self):
            self.engine = None
            self.resolutions = []
            self.sessions = {
                "main-current": SimpleNamespace(),
                "session-old": SimpleNamespace(
                    record=[{"type": "assistant", "content": "frozen output"}],
                    get_cumulative_token_usage=lambda: None,
                ),
            }

        async def create_sub_session(self, **_kwargs):
            raise AssertionError("恢复审批不应重新调用模型")

        async def route_message(self, **_kwargs):
            self.resolutions.append(
                self.engine.resolve_approval(
                    "wf-agent-approval-race",
                    "task-agent-approval-race",
                    "writer",
                    approved=True,
                )
            )
            return {"success": True, "message": "sent"}

    manager = ImmediateApprovalSessionManager()
    manager.engine = WorkflowEngine(manager)
    node = WorkflowNode(
        id="writer",
        node_type="agent",
        first_message="write",
        output_variable="draft",
    )
    state = NodeExecutionState(
        node_id=node.id,
        status="running",
        session_id="session-old",
        summary="generated summary",
        next_attempt_trigger=RECOVERY_REISSUE_TRIGGER,
    )

    async def scenario():
        return await asyncio.wait_for(
            AgentNode().execute(
                NodeContext(
                    definition=WorkflowDef(
                        workflow_id="wf-agent-approval-race",
                        nodes=[node],
                    ),
                    node_def=node,
                    node_state=state,
                    needs_approval=True,
                    parent_id="main-current",
                    workflow_id="wf-agent-approval-race",
                    task_id="task-agent-approval-race",
                    session_manager=manager,
                )
            ),
            timeout=0.2,
        )

    result = asyncio.run(scenario())

    assert result.status == "completed"
    assert result.outputs == {"draft": "frozen output"}
    assert len(manager.resolutions) == 1
    assert manager.resolutions[0]["success"] is True


def test_run_task_rejects_replaying_completed_task():
    definition = WorkflowDef(
        workflow_id="wf-terminal",
        nodes=[WorkflowNode(id="writer", node_type="agent")],
    )
    task = WorkflowTask(
        task_id="task-terminal",
        workflow_id=definition.workflow_id,
        status="completed",
        snapshot_definition=definition.to_dict(),
        node_states={
            "writer": NodeExecutionState(
                node_id="writer",
                status="completed",
                outputs={"draft": "stable"},
            ),
        },
    )
    manager = WorkflowManager.__new__(WorkflowManager)
    manager._extension_manager = None
    manager._running_tasks = {}
    manager._load_task = lambda *_args: task

    result = asyncio.run(
        manager.run_task(
            definition.workflow_id,
            task.task_id,
            from_node_id="writer",
        )
    )

    assert result["success"] is False
    assert result["error"] == "task_state_conflict"
    assert task.status == "completed"
    assert task.node_states["writer"].outputs == {"draft": "stable"}
