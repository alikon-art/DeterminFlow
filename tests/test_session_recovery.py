"""会话失败恢复回归测试。

覆盖修复：
- 主会话 LLM 瞬态失败（网络/超时/限流/流中断）→ 不再置 error 终态，会话保持
  running 可继续；错误事件 terminal=False（前端提示重试而非报废）。
- 子会话失败保持原行为（error 终态 + raise，workflow 节点感知失败）。
- recover_session 把 error 主会话恢复为 running。
- create_main_session(from_session_id=...) 继承旧主会话对话上下文与 workspace。
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.session import AgentSession


class FailingGraph:
    """astream_events 直接抛异常的假图（模拟 LLM 网络中断/流式中断）。"""

    async def astream_events(self, *args, **kwargs):
        raise RuntimeError("模拟 LLM 网络中断")
        yield  # pragma: no cover


def _make_session(session_type="main"):
    s = AgentSession(session_type=session_type, system_prompt="你是测试助手。")
    s.compiled_graph = FailingGraph()
    return s


def test_main_session_failure_keeps_session_alive():
    """主会话 LLM 失败 → 不置 error、不 raise、事件 terminal=False，可继续。"""
    s = _make_session("main")
    events = []

    async def cb(event):
        events.append(event)

    async def run():
        return await s._invoke_graph(content="你好", event_callback=cb, max_rounds=2)

    result = asyncio.run(run())
    assert result == ""  # 不抛出，返回空
    assert s.status == "running"  # 会话未报废
    errs = [e for e in events if e.get("type") == "error"]
    assert errs, "应发出 error 事件提示用户"
    assert errs[0].get("terminal") is False  # 非终态，可重试
    assert s.last_error is not None  # 错误已记录


def test_sub_session_failure_still_errors():
    """子会话失败保持原行为：error 终态 + raise（workflow 节点感知失败）。"""
    s = _make_session("sub")
    events = []

    async def cb(event):
        events.append(event)

    async def run():
        return await s._invoke_graph(content="子任务", event_callback=cb, max_rounds=2)

    with pytest.raises(RuntimeError, match="模拟 LLM 网络中断"):
        asyncio.run(run())
    assert s.status == "error"
    errs = [e for e in events if e.get("type") == "error"]
    assert errs and errs[0].get("terminal") is True


def test_recover_error_main_session(monkeypatch):
    """recover_session 把 error 主会话恢复为 running（不重建、不丢消息）。"""
    from src.agent.session_manager import SessionManager

    sm = SessionManager()
    s = AgentSession(session_type="main", system_prompt="测试")
    s.status = "error"
    s.last_error = {"code": "x", "message": "旧错误"}
    sm.sessions[s.session_id] = s

    # 避免真实 LLM 构造与 graph 编译
    monkeypatch.setattr(
        "src.core.llm_client.create_startup_llm", lambda **kw: object()
    )
    monkeypatch.setattr(AgentSession, "setup_graph", lambda self, llm, tools: None)

    async def run():
        return await sm.recover_session(s.session_id)

    result = asyncio.run(run())
    assert result["success"]
    assert s.status == "running"
    assert s.last_error is None


def test_recover_non_error_session_noop():
    """非 error 会话 recover 是 no-op（不误伤）。"""
    from src.agent.session_manager import SessionManager

    sm = SessionManager()
    s = AgentSession(session_type="main", system_prompt="测试")
    s.status = "running"
    sm.sessions[s.session_id] = s

    async def run():
        return await sm.recover_session(s.session_id)

    result = asyncio.run(run())
    assert result["success"]
    assert s.status == "running"


def test_create_main_session_inherits_context(monkeypatch):
    """from_session_id 继承旧主会话对话上下文与 workspace。"""
    from langchain_core.messages import HumanMessage

    from src.agent.session_manager import SessionManager

    sm = SessionManager()
    old = AgentSession(
        session_type="main", system_prompt="旧助手", workspace_path="/books/demo"
    )
    old.lc_messages.append(HumanMessage(content="之前的对话内容"))
    sm.sessions[old.session_id] = old

    monkeypatch.setattr(
        "src.core.llm_client.create_startup_llm", lambda **kw: object()
    )
    monkeypatch.setattr(AgentSession, "setup_graph", lambda self, llm, tools: None)
    monkeypatch.setattr(AgentSession, "start_consumer", lambda self: None)
    monkeypatch.setattr(
        SessionManager,
        "_build_extension_prompt_context",
        lambda self, agent_type, agent_def: "",
    )

    async def run():
        return await sm.create_main_session(
            llm_client=None, agent_type="main", from_session_id=old.session_id
        )

    result = asyncio.run(run())
    assert result["success"]
    new_s = sm.sessions[result["session_id"]]
    # 继承对话上下文
    assert any(
        isinstance(m, HumanMessage) and m.content == "之前的对话内容"
        for m in new_s.lc_messages
    )
    # 继承 workspace
    assert new_s.workspace_path == "/books/demo"
