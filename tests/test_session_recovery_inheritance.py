"""PR1 评审修复测试：workspace 继承 / 冷加载恢复 / error 恢复 / 降级无 chain_end。

覆盖评审要求：
- from_session_id 工作区继承：真实 WorkspaceManager 注入，继承判断在创建新
  workspace 之前，register_main 不覆盖继承路径
- legacy/cold error Session 自动恢复入口：ensure_graph_ready 重建运行依赖，
  send_message_to_session 对 error 会话显式重试
- error / 降级结束后不再推送 chain_end（单一、可判定结束事件）
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from src.agent.session_manager import SessionManager


class FakeWorkspaceManager:
    """评审要求：真实 WorkspaceManager 注入，记录 create_workspace 调用。"""

    def __init__(self):
        self.created = []

    def create_workspace(self, session_id: str):
        path = f"/fake/ws/{session_id}"
        self.created.append(session_id)
        return path

    def create_workflow_workspace(self, workflow_id: str):
        return f"/fake/ws/{workflow_id}"


class FakeSession:
    """最小 AgentSession 桩：workspace_path / compiled_graph / status / send_message。"""

    def __init__(self, session_id="s1", workspace_path=None, compiled_graph="graph",
                 status="completed"):
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.compiled_graph = compiled_graph
        self.status = status
        self.sent = []
        self.setup_graph_called = False
        self.consumer_started = False
        self.record = []
        self.token_usage = None
        self.model_id = None
        self.model_params = {}
        self.agent_type = "main"
        self.session_type = "main"
        self.parent_id = ""
        self.created_at = self.updated_at = datetime.now(timezone.utc).isoformat()
        self.workflow_id = ""
        self.task_id = ""
        self.node_id = None

    async def send_message(self, content, event_callback=None, max_rounds=None, **kw):
        self.sent.append(content)
        return "ok"

    def setup_graph(self, llm=None, tools=None):
        self.setup_graph_called = True
        self.compiled_graph = "graph"

    def start_consumer(self):
        self.consumer_started = True

    async def async_save(self, *a, **kw):
        return None

    def get_summary(self):
        return {
            "session_id": self.session_id,
            "type": self.session_type,
            "status": self.status,
            "workspace_path": self.workspace_path or "",
            "updated_at": self.updated_at,
        }


# ---------- 1. workspace 继承 ----------

def test_from_session_id_inherits_workspace_and_does_not_create_new(monkeypatch):
    """评审：from_session_id 继承 workspace；继承判断在创建新 workspace 之前。"""
    sm = SessionManager()
    wm = FakeWorkspaceManager()
    sm.inject_dependencies(workspace_manager=wm)
    # create_main_session 内部 create_startup_llm 依赖真实 Provider——mock 掉
    monkeypatch.setattr(
        "src.core.llm_client.create_startup_llm",
        lambda **kw: SimpleNamespace(),
    )
    # register_main 使用 register_runtime_session → _make_event_callback → 事件总线
    from src.agent import session_lifecycle as sl
    monkeypatch.setattr(sl, "_try_emit_event", lambda *a, **k: None)
    # 主会话创建流程会走 _build_extension_prompt_context / prompt builder
    monkeypatch.setattr(
        sm, "_build_extension_prompt_context",
        lambda *a, **k: asyncio.coroutine(lambda: "")(),
    )

    async def scenario():
        # 源会话
        first = await sm.create_main_session(agent_type="main")
        assert first["success"] is True
        src_id = first["session_id"]
        src_ws = sm.get_session(src_id).workspace_path
        assert src_ws == f"/fake/ws/{src_id}"
        assert wm.created == [src_id]

        # 继承会话
        second = await sm.create_main_session(agent_type="main", from_session_id=src_id)
        assert second["success"] is True
        dst = sm.get_session(second["session_id"])
        assert dst.workspace_path == src_ws  # 复用源 workspace
        # create_workspace 未被再次调用（继承不新建目录）
        assert wm.created == [src_id]

    asyncio.run(scenario())


def test_register_main_does_not_override_existing_workspace(monkeypatch):
    """评审：register_main 不得覆盖已继承/已设置的 workspace。"""
    sm = SessionManager()
    wm = FakeWorkspaceManager()
    sm.inject_dependencies(workspace_manager=wm)
    from src.agent import session_lifecycle as sl
    monkeypatch.setattr(sl, "_try_emit_event", lambda *a, **k: None)

    session = FakeSession(session_id="s1", workspace_path="/custom/ws")
    sm.register_main(session)
    assert session.workspace_path == "/custom/ws"
    assert wm.created == []  # 未新建 workspace


# ---------- 2. 冷加载恢复 ----------

def test_ensure_graph_ready_rebuilds_cold_session(monkeypatch):
    """评审：冷加载会话（compiled_graph=None + error 状态）重建运行依赖并恢复。"""
    sm = SessionManager()
    cold = FakeSession(session_id="cold1", compiled_graph=None, status="error")
    monkeypatch.setattr(
        "src.core.llm_client.create_startup_llm",
        lambda **kw: SimpleNamespace(),
    )

    class FakeAssembler:
        def build(self, *a, **kw):
            return ["tool-a"]

    sm._tool_assembler = FakeAssembler()

    async def scenario():
        ready = await sm.ensure_graph_ready(cold)
        assert ready is True
        assert cold.compiled_graph == "graph"
        assert cold.setup_graph_called is True
        assert cold.consumer_started is True
        # error 状态被重置为可运行
        assert cold.status == "completed"

    asyncio.run(scenario())


def test_send_message_to_session_recovers_error_session(monkeypatch):
    """评审：error 会话通过 send_message_to_session 显式重试可恢复。"""
    sm = SessionManager()
    session = FakeSession(session_id="s1", status="error")
    sm.sessions["s1"] = session
    monkeypatch.setattr(sm, "_make_event_callback", lambda *a, **k: None)

    async def scenario():
        result = await sm.send_message_to_session("s1", "重试消息")
        assert result["success"] is True
        assert session.status == "completed"  # error 被重置
        assert session.sent == ["重试消息"]

    asyncio.run(scenario())


def test_send_message_to_session_cold_load_rebuilds_graph(monkeypatch):
    """评审：冷加载会话（compiled_graph=None）发送消息前重建运行依赖。"""
    sm = SessionManager()
    session = FakeSession(session_id="cold2", compiled_graph=None)
    sm.sessions["cold2"] = session
    monkeypatch.setattr(sm, "_make_event_callback", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.core.llm_client.create_startup_llm",
        lambda **kw: SimpleNamespace(),
    )

    class FakeAssembler:
        def build(self, *a, **kw):
            return []

    sm._tool_assembler = FakeAssembler()

    async def scenario():
        result = await sm.send_message_to_session("cold2", "消息")
        assert result["success"] is True
        assert session.setup_graph_called is True
        assert session.sent == ["消息"]

    asyncio.run(scenario())


# ---------- 3. 降级后无 chain_end ----------

def test_execute_with_events_skips_chain_end_when_degraded(monkeypatch):
    """评审：降级结束（_degraded=True）后不得推送 chain_end。"""
    from src.web import ws_handlers

    emitted = []

    class FakeBus:
        async def emit_chat(self, event):
            emitted.append(event)

    monkeypatch.setattr(ws_handlers, "event_bus", FakeBus())

    session = FakeSession(session_id="s1", status="running")
    session._degraded = True
    session.record = [{"id": "m1", "type": "assistant", "content": "部分输出"}]

    async def action():
        return None

    asyncio.run(ws_handlers._execute_with_events(
        session, "s1", action(), "测试",
    ))
    # 降级结束：不发 chain_end（也不发其他 chat 事件）
    assert emitted == []


def test_execute_with_events_skips_chain_end_when_error(monkeypatch):
    """评审：status=error 时不得推送 chain_end（单一结束事件）。"""
    from src.web import ws_handlers

    emitted = []

    class FakeBus:
        async def emit_chat(self, event):
            emitted.append(event)

    monkeypatch.setattr(ws_handlers, "event_bus", FakeBus())

    session = FakeSession(session_id="s1", status="error")
    session.record = [{"id": "m1", "type": "assistant", "content": "x"}]

    async def action():
        return None

    asyncio.run(ws_handlers._execute_with_events(
        session, "s1", action(), "测试",
    ))
    assert emitted == []


def test_execute_with_events_sends_chain_end_on_normal_completion(monkeypatch):
    """正常完成：仍推送 chain_end。"""
    from src.web import ws_handlers

    emitted = []

    class FakeBus:
        async def emit_chat(self, event):
            emitted.append(event)

    monkeypatch.setattr(ws_handlers, "event_bus", FakeBus())

    session = FakeSession(session_id="s1", status="completed")
    session.record = [{"id": "m1", "type": "assistant", "content": "正常输出"}]

    async def action():
        return None

    asyncio.run(ws_handlers._execute_with_events(
        session, "s1", action(), "测试",
    ))
    assert any(e["type"] == "chain_end" for e in emitted)


# ---------- 4. 压缩总上下文上限 ----------

def test_trim_messages_enforces_total_token_budget():
    """评审：上下文兜底按总 token budget 截断（保留 system + 最近消息），
    而非按条 1500/3000 字符硬截断。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.core.utils import trim_langchain_messages

    system = SystemMessage(content="系统提示词")
    messages = [
        HumanMessage(content="最早的消息 " + "字" * 100),
        HumanMessage(content="中间的消息 " + "字" * 100),
        HumanMessage(content="最近的消息 " + "字" * 100),
    ]
    # 总 token 明显超限（每条约 105 token，3 条约 315）
    trimmed = trim_langchain_messages([system, *messages], max_tokens=150)
    assert trimmed[0] == system  # system 保留
    # 最近消息保留（逆序收集，保留最近的）
    assert "最近的消息" in str(trimmed[-1].content)
    # 总 token 不超过上限（含 system）
    from src.core.utils import estimate_tokens
    total = sum(estimate_tokens(str(m.content or "")) for m in trimmed)
    assert total <= 150 + 60  # 单条消息可能略超上限（最近一条必须保留）
    # 最早消息被丢弃
    assert not any("最早的消息" in str(m.content) for m in trimmed)


def test_trim_messages_noop_when_within_budget():
    from langchain_core.messages import HumanMessage

    from src.core.utils import trim_langchain_messages

    messages = [HumanMessage(content="短消息")]
    trimmed = trim_langchain_messages(messages, max_tokens=10_000)
    assert len(trimmed) == 1
