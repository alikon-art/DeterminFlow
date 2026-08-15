"""Agent 节点输出路径集成测试（空输出重试全覆盖）。

覆盖评审要求：
- require_non_empty_output + 最新为空但更早非空 → 重试成功
- 仅 output_variable（无文件、无校验）→ 空输出重试 → outputs 写回
- 文件 + 变量 → 重试结果写回 outputs[var] 与 _output_file
- 重试 token 用量计入 NodeResult.token_usage
- retry_empty_output=False 时空输出直接判失败
- 重试全部失败 → failed，不返回更早的中间回复
"""
from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from src.workflow.nodes.agent import AgentNode


class FakeSession:
    """最小子会话桩：record + send_message + token 累计。"""

    def __init__(self, record=None, retry_result="重试后的完整输出", token_step=100):
        self.record = list(record or [])
        self.retry_result = retry_result
        self._token_calls = 0
        self.token_step = token_step
        self.sent_messages = []

    async def send_message(self, message, max_rounds=1, **kwargs):
        self.sent_messages.append(message)
        self.record.append(
            {"id": f"msg_{len(self.record) + 1:05d}", "type": "assistant",
             "content": self.retry_result}
        )
        return {"success": True}

    def get_cumulative_token_usage(self):
        # 每次读取 token 都会"增长"——用于验证重试后 token 重新读取
        self._token_calls += 1
        return {"model-x": {"total_tokens": 100 * self._token_calls,
                            "prompt_tokens": 50 * self._token_calls,
                            "completion_tokens": 50 * self._token_calls}}


class FakeSM:
    def __init__(self, session):
        self.sessions = {session.session_id: session} if getattr(session, "session_id", None) else {"s1": session}
        self.session = session

    async def create_sub_session(self, **kwargs):
        # 模拟子会话立即自动完成（graph 结束 → on_auto_complete）
        on_auto_complete = kwargs.get("on_auto_complete")
        if on_auto_complete:
            on_auto_complete("s1", "子会话完成", "success", "")
        return {"success": True, "session_id": "s1"}


def _node(**overrides) -> SimpleNamespace:
    fields = {
        "id": "writer",
        "agent_type": "agent_pp",
        "system_prompt_template": "你是润色专家",
        "first_message": "请完成润色任务",
        "output_variable": "",
        "save_output_to_file": False,
        "output_file_path": "",
        "require_non_empty_output": False,
        "json_output_field": "",
        "json_output_field_min_chars": 0,
        "auto_flow": False,
        "enable_complete_node_task": True,
        "enable_reject_upstream": False,
        "max_reject_count": 3,
        "model_override": "",
        "node_params": {},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _ctx(node_def, shared_ws: str = "", sm=None) -> SimpleNamespace:
    return SimpleNamespace(
        session_manager=sm,
        node_def=node_def,
        workflow_id="wf-test",
        task_id="task-test",
        parent_id="",
        upstream_summary="",
        on_reject_upstream=None,
        needs_approval=False,
        shared_ws=Path(shared_ws) if shared_ws else None,
        definition=SimpleNamespace(variables={}),
        parameter_values={},
        node_state=SimpleNamespace(
            session_id="", next_attempt_trigger="", summary="", status="", error="",
        ),
        checkpoint=None,
    )


async def _run(node_def, session, monkeypatch, shared_ws: str = ""):
    from src.web.event_bus import event_bus

    monkeypatch.setattr(event_bus, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(event_bus, "emit_chat", lambda *a, **k: None)
    sm = FakeSM(session)
    ctx = _ctx(node_def, shared_ws, sm=sm)
    return await AgentNode().execute(ctx)


def test_require_non_empty_retries_when_latest_empty(monkeypatch):
    """评审：require_non_empty_output 开启时，最新为空但更早非空 → 复用子会话重试。"""
    node = _node(require_non_empty_output=True, output_variable="result")
    session = FakeSession([
        {"id": "m1", "type": "assistant", "content": "更早的中间回复"},
        {"id": "m2", "type": "assistant", "content": ""},
    ])
    result = asyncio.run(_run(node, session, monkeypatch))
    assert result.status == "completed"
    assert result.outputs["result"] == "重试后的完整输出"
    assert len(session.sent_messages) >= 1  # 确实发生了重试


def test_output_variable_only_retries_empty(monkeypatch):
    """评审：仅 output_variable（无文件、无校验）→ 空输出也走重试并写回。"""
    node = _node(output_variable="result")
    session = FakeSession([{"id": "m1", "type": "assistant", "content": ""}])
    result = asyncio.run(_run(node, session, monkeypatch))
    assert result.status == "completed"
    assert result.outputs["result"] == "重试后的完整输出"


def test_file_and_variable_retry_writes_back(monkeypatch):
    """评审：文件 + 变量 → 重试结果写回 outputs[var] 与 _output_file。"""
    with tempfile.TemporaryDirectory() as tmp:
        node = _node(
            output_variable="result",
            save_output_to_file=True,
            output_file_path="out.txt",
        )
        session = FakeSession([{"id": "m1", "type": "assistant", "content": ""}])
        result = asyncio.run(_run(node, session, monkeypatch, shared_ws=tmp))
        assert result.status == "completed"
        assert result.outputs["result"] == "重试后的完整输出"
        assert "_output_file" in result.outputs
        written = Path(result.outputs["_output_file"]).read_text(encoding="utf-8")
        assert written == "重试后的完整输出"


def test_retry_token_usage_accounted(monkeypatch):
    """评审：重试消耗的模型调用必须计入 NodeResult.token_usage。"""
    node = _node(output_variable="result")
    session = FakeSession([{"id": "m1", "type": "assistant", "content": ""}])
    result = asyncio.run(_run(node, session, monkeypatch))
    assert result.status == "completed"
    # 首次读取（100）→ 重试后重读（200），token_usage 应为重试后的值
    assert result.token_usage == {"model-x": {
        "total_tokens": 200, "prompt_tokens": 100, "completion_tokens": 100,
    }}


def test_retry_disabled_fails_on_empty(monkeypatch):
    """评审：retry_empty_output=False 时，空输出直接判失败（显式控制重试）。"""
    node = _node(
        output_variable="result",
        require_non_empty_output=True,
        node_params={"retry_empty_output": False},
    )
    session = FakeSession([{"id": "m1", "type": "assistant", "content": ""}])
    result = asyncio.run(_run(node, session, monkeypatch))
    assert result.status == "failed"
    assert "校验失败" in (result.error or "")


def test_retry_all_fail_returns_failed_not_older_reply(monkeypatch):
    """评审：重试全部失败 → failed，不得返回更早的中间回复。"""
    node = _node(output_variable="result")
    session = FakeSession(
        [{"id": "m1", "type": "assistant", "content": "更早的中间回复"},
         {"id": "m2", "type": "assistant", "content": ""}],
        retry_result="",  # 重试也一直空
    )
    result = asyncio.run(_run(node, session, monkeypatch))
    assert result.status == "failed"
    assert result.outputs.get("result") is None
