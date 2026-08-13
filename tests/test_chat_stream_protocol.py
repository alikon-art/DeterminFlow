import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi import WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from openai import BadRequestError

from src.agent.session import AgentSession, _resolve_pending_tool_run_id
from src.agent.session_manager import SessionManager
from src.web import ws_handlers
from src.web.event_bus import EventBus, _WsConnection


class _CaptureConnection:
    def __init__(self):
        self.events: list[dict] = []

    def enqueue(self, message: str, _event_type: str) -> bool:
        self.events.append(json.loads(message))
        return True


class _EventGraph:
    def __init__(self, events: list[dict], error: Exception | None = None):
        self.events = events
        self.error = error
        self.called = False

    async def astream_events(self, *_args, **_kwargs):
        self.called = True
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


def test_workflow_sub_session_emits_stream_deltas():
    received: list[dict] = []

    async def callback(event: dict):
        received.append(event)

    session = SimpleNamespace(
        _current_event_callback=callback,
        session_type="sub",
        workflow_id="workflow-1",
    )

    asyncio.run(AgentSession._emit_event(session, {
        "type": "token",
        "content": "partial",
    }))

    assert received == [{"type": "token", "content": "partial"}]


def test_chat_event_fans_out_to_session_and_global_subscribers():
    async def scenario():
        bus = EventBus()
        global_conn = _CaptureConnection()
        session_conn_a = _CaptureConnection()
        session_conn_b = _CaptureConnection()
        bus._connections = {
            1: global_conn,
            2: session_conn_a,
            3: session_conn_b,
        }
        bus._channel_subscribers["chat"] = {1}
        bus._session_subscribers["session-1"] = {1, 2, 3}

        # This used to be discarded for workflow sub-sessions.
        bus._is_workflow_sub_session = lambda _session_id: True
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "hello",
        })

        return global_conn, session_conn_a, session_conn_b

    global_conn, session_conn_a, session_conn_b = asyncio.run(scenario())
    for conn in (global_conn, session_conn_a, session_conn_b):
        assert len(conn.events) == 1
        assert conn.events[0]["content"] == "hello"


def test_roundtable_subscriber_does_not_receive_unrelated_agent_tokens():
    async def scenario():
        bus = EventBus()
        global_conn = _CaptureConnection()
        session_conn = _CaptureConnection()
        roundtable_conn = _CaptureConnection()
        bus._connections = {1: global_conn, 2: session_conn, 3: roundtable_conn}
        bus._channel_subscribers["chat"] = {1}
        bus._session_subscribers["session-1"] = {2}
        bus._roundtable_subscribers["roundtable-1"] = {3}

        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "agent token",
        })
        await bus.emit_chat({
            "type": "rt_token",
            "roundtable_id": "roundtable-1",
            "content": "roundtable token",
        })
        return global_conn, session_conn, roundtable_conn

    global_conn, session_conn, roundtable_conn = asyncio.run(scenario())
    assert [event["content"] for event in global_conn.events] == [
        "agent token", "roundtable token",
    ]
    assert [event["content"] for event in session_conn.events] == ["agent token"]
    assert [event["content"] for event in roundtable_conn.events] == ["roundtable token"]


def test_chat_stream_snapshot_tracks_mid_generation_draft_and_revision():
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({"type": "stream_start", "session_id": "session-1"})
        await bus.emit_chat({
            "type": "reasoning_token",
            "session_id": "session-1",
            "content": "think",
        })
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "answer",
        })
        await bus.emit_chat({
            "type": "tool_call_delta",
            "session_id": "session-1",
            "index": 0,
            "id": "call-1",
            "name": "search",
            "args_delta": '{"q":"x"}',
        })
        return bus.get_session_revision("session-1"), bus.get_active_stream("session-1")

    revision, active = asyncio.run(scenario())

    assert revision == 4
    assert active is not None
    assert active["revision"] == revision
    assert active["generation_id"]
    assert active["segments"] == [
        {"type": "reasoning", "content": "think"},
        {"type": "text", "content": "answer"},
        {
            "type": "tool",
            "tool": {
                "id": "call-1",
                "run_id": "call-1",
                "index": 0,
                "name": "search",
                "args": '{"q":"x"}',
                "status": "building",
            },
        },
    ]


def test_snapshot_excludes_incrementally_persisted_active_generation_messages():
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({
            "type": "stream_start",
            "session_id": "session-1",
            "baseline_record_length": 3,
        })
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "draft answer",
        })
        session = SimpleNamespace(
            session_id="session-1",
            record=[
                {"type": "user", "content": "old question"},
                {"type": "assistant", "content": "old answer"},
                {"type": "user", "content": "new question"},
                {"type": "assistant", "content": "draft answer"},
            ],
            status="streaming",
            token_usage={},
        )
        return ws_handlers._build_session_snapshot(session, bus=bus)

    snapshot = asyncio.run(scenario())

    assert snapshot["messages"] == [
        {"type": "user", "content": "old question"},
        {"type": "assistant", "content": "old answer"},
        {"type": "user", "content": "new question"},
    ]
    assert snapshot["active_stream"]["segments"] == [
        {"type": "text", "content": "draft answer"},
    ]


def test_terminal_snapshot_and_session_restore_keep_user_safe_error():
    session = AgentSession(
        session_type="main",
        task_description="test",
        session_id="failed-session",
    )
    session.status = "error"
    session.record = [{"type": "assistant", "content": "earlier reply"}]
    session.last_error = {
        "code": "quota_exhausted",
        "message": "公益模型额度已用完，请稍后再试",
        "occurred_at": "2026-08-09T12:00:00+00:00",
    }

    snapshot = ws_handlers._build_session_snapshot(session, bus=EventBus())
    restored = AgentSession.from_dict(session.to_dict())

    assert snapshot["last_error"] == session.last_error
    assert restored.last_error == session.last_error


def test_chain_end_clears_active_draft_and_keeps_monotonic_revision():
    async def scenario():
        bus = EventBus()
        conn = _CaptureConnection()
        bus._connections = {1: conn}
        bus._session_subscribers["session-1"] = {1}

        await bus.emit_chat({"type": "stream_start", "session_id": "session-1"})
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "draft",
        })
        await bus.emit_chat({
            "type": "chain_end",
            "session_id": "session-1",
            "messages": [{"type": "assistant", "content": "final"}],
        })
        return bus, conn.events

    bus, events = asyncio.run(scenario())

    assert [event["revision"] for event in events] == [1, 2, 3]
    assert len({event["generation_id"] for event in events}) == 1
    assert bus.get_active_stream("session-1") is None


def test_extra_chat_events_do_not_create_revision_gaps():
    async def scenario():
        bus = EventBus()
        conn = _CaptureConnection()
        bus._connections = {1: conn}
        bus._session_subscribers["session-1"] = {1}

        await bus.emit_chat({"type": "stream_start", "session_id": "session-1"})
        await bus.emit_chat({
            "type": "wf_variable_update",
            "session_id": "session-1",
            "key": "topic",
            "value": "new value",
        })
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "answer",
        })
        return bus, conn.events

    bus, events = asyncio.run(scenario())

    assert events[0]["revision"] == 1
    assert "revision" not in events[1]
    assert events[2]["revision"] == 2
    assert bus.get_active_stream("session-1")["revision"] == 2


def test_nonterminal_command_error_keeps_active_generation_recoverable():
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({"type": "stream_start", "session_id": "session-1"})
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": "before error",
        })
        await bus.emit_chat({
            "type": "error",
            "session_id": "session-1",
            "message": "会话正在处理中",
            "terminal": False,
        })
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-1",
            "content": " after error",
        })
        return bus

    bus = asyncio.run(scenario())

    assert bus.get_session_revision("session-1") == 3
    assert bus.get_active_stream("session-1")["segments"] == [
        {"type": "text", "content": "before error after error"},
    ]


def test_send_message_uses_registered_default_stream_callback():
    received: list[dict] = []

    async def callback(event: dict):
        received.append(event)

    async def invoke_graph(
        content,
        event_callback,
        max_rounds,
        source,
        source_name,
        attachments,
    ):
        assert content == "repair JSON"
        assert max_rounds == 1
        assert source == "workflow_json_retry"
        assert attachments is None
        await event_callback({"type": "stream_start"})
        await event_callback({"type": "token", "content": "{}"})
        return "{}"

    async def scenario():
        session = AgentSession(session_type="sub", agent_type="test")
        session.compiled_graph = object()
        session._default_event_callback = callback
        session._invoke_graph = invoke_graph
        return await AgentSession.send_message(
            session,
            "repair JSON",
            max_rounds=1,
            source="workflow_json_retry",
        )

    assert asyncio.run(scenario()) == "{}"
    assert [event["type"] for event in received] == ["stream_start", "token"]


def test_message_attachment_metadata_is_validated_against_absolute_path_content():
    path = "/private/tmp/workspaces/session/attachments/report final.md"
    assert ws_handlers._validate_message_attachments(
        [{"name": "report final.md", "absolute_path": path}],
        f"检查 {path}",
    ) == [{"name": "report final.md", "absolute_path": path}]

    invalid_cases = [
        ([{"name": "report.md", "absolute_path": "relative/report.md"}], "relative/report.md"),
        ([{"name": "report.md", "absolute_path": "/tmp/report.md"}], "正文没有该路径"),
        ({"name": "report.md", "absolute_path": "/tmp/report.md"}, "/tmp/report.md"),
    ]
    for attachments, content in invalid_cases:
        try:
            ws_handlers._validate_message_attachments(attachments, content)
        except ValueError:
            continue
        raise AssertionError("invalid attachment metadata should be rejected")


def test_sent_attachment_metadata_is_ui_only_and_persisted_in_history():
    async def scenario():
        session = AgentSession(session_type="sub", agent_type="test")
        session.compiled_graph = _EventGraph([])

        async def no_save():
            return None

        async def no_compress(*_args, **_kwargs):
            return None

        session.async_save = no_save
        session._check_and_compress_messages = no_compress
        path = "/Users/me/Documents/report final.md"
        await session.send_message(
            f"检查 {path}",
            max_rounds=1,
            attachments=[{"name": "report final.md", "absolute_path": path}],
        )
        return session

    session = asyncio.run(scenario())
    assert session.record[0]["attachments"] == [
        {
            "name": "report final.md",
            "absolute_path": "/Users/me/Documents/report final.md",
        },
    ]
    assert "attachments" not in session.lc_messages[0].additional_kwargs


def test_reused_tool_index_starts_a_new_snapshot_segment():
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({"type": "stream_start", "session_id": "session-1"})
        await bus.emit_chat({
            "type": "tool_call_delta",
            "session_id": "session-1",
            "index": 0,
            "id": "call-1",
            "name": "first",
            "args_delta": "{}",
        })
        await bus.emit_chat({
            "type": "tool_start",
            "session_id": "session-1",
            "index": 0,
            "run_id": "run-1",
            "name": "first",
            "args": {},
        })
        await bus.emit_chat({
            "type": "tool_end",
            "session_id": "session-1",
            "run_id": "run-1",
            "result": "done",
            "status": "failed",
        })
        await bus.emit_chat({
            "type": "tool_call_delta",
            "session_id": "session-1",
            "index": 0,
            "id": "call-2",
            "name": "second",
            "args_delta": '{"next":true}',
        })
        return bus.get_active_stream("session-1")

    active = asyncio.run(scenario())
    assert active is not None
    tool_segments = [
        segment["tool"]
        for segment in active["segments"]
        if segment["type"] == "tool"
    ]
    assert [tool["run_id"] for tool in tool_segments] == ["run-1", "call-2"]
    assert tool_segments[0]["status"] == "failed"
    assert tool_segments[1]["args"] == '{"next":true}'


def test_failed_tool_status_survives_chain_end_and_history_restore():
    async def scenario():
        session = AgentSession(session_type="sub", agent_type="test")
        await session._append_to_record(AIMessage(
            content="",
            tool_calls=[{"id": "call-failed", "name": "write", "args": {}}],
        ))
        await session._append_to_record(ToolMessage(
            content="partial result",
            tool_call_id="call-failed",
            status="error",
            additional_kwargs={"tool_status": "cancelled"},
        ))
        return session.record

    record = asyncio.run(scenario())
    assert record[-1]["status"] == "error"
    assert record[-1]["tool_status"] == "cancelled"

    restored = AgentSession(session_type="sub", agent_type="test")
    restored.record = record
    restored._restore_lc_from_record()
    tool_message = restored.lc_messages[-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.status == "error"
    assert tool_message.additional_kwargs["tool_status"] == "cancelled"


def test_anthropic_content_blocks_and_signature_survive_history_restore():
    blocks = [
        {"type": "thinking", "thinking": "分析过程", "signature": "sig-123"},
        {"type": "text", "text": "最终回答"},
        {
            "type": "tool_use",
            "id": "toolu-1",
            "name": "lookup",
            "input": {"query": "value"},
        },
    ]

    async def scenario():
        session = AgentSession(session_type="sub", agent_type="test")
        await session._append_to_record(AIMessage(
            content=blocks,
            response_metadata={"model_provider": "anthropic"},
            tool_calls=[{
                "id": "toolu-1",
                "name": "lookup",
                "args": {"query": "value"},
            }],
        ))
        await session._append_to_record(ToolMessage(
            content="result",
            tool_call_id="toolu-1",
        ))
        return session.record

    record = asyncio.run(scenario())
    assert record[0]["content"] == "最终回答"
    assert record[0]["reasoning_content"] == "分析过程"
    assert record[0]["content_blocks"][0]["signature"] == "sig-123"

    restored = AgentSession(session_type="sub", agent_type="test")
    restored.record = record
    restored._restore_lc_from_record()
    assistant = restored.lc_messages[0]
    assert isinstance(assistant, AIMessage)
    assert assistant.content == blocks
    assert assistant.tool_calls[0]["id"] == "toolu-1"


def test_standard_reasoning_content_block_is_displayable():
    from src.core.utils import message_content_reasoning

    assert message_content_reasoning([
        {"type": "reasoning", "reasoning": "标准思考块"},
    ]) == "标准思考块"


def test_parallel_tool_completion_resolves_by_call_id_not_finish_order():
    pending = {
        "call-a": {"name": "lookup"},
        "call-b": {"name": "lookup"},
    }
    by_call = {"call-a": "call-a", "call-b": "call-b"}
    by_node = {"node-a": "call-a", "node-b": "call-b"}

    second = _resolve_pending_tool_run_id(
        actual_tool_call_id="call-b",
        node_run_id="node-b",
        tool_name="lookup",
        pending_by_call_id=by_call,
        pending_by_node_run_id=by_node,
        pending_calls=pending,
    )
    pending.pop(second)
    first = _resolve_pending_tool_run_id(
        actual_tool_call_id="call-a",
        node_run_id="node-a",
        tool_name="lookup",
        pending_by_call_id=by_call,
        pending_by_node_run_id=by_node,
        pending_calls=pending,
    )

    assert (second, first) == ("call-b", "call-a")


def test_parallel_tool_start_resolves_model_slots_when_callbacks_are_reversed():
    async def scenario():
        model_output = AIMessage(
            content="",
            tool_calls=[
                {"id": "call-a", "name": "lookup", "args": {"query": "a"}},
                {"id": "call-b", "name": "lookup", "args": {"query": "b"}},
            ],
        )
        graph = _EventGraph([
            {
                "event": "on_chat_model_end",
                "run_id": "model-run",
                "data": {"output": model_output},
            },
            {
                "event": "on_tool_start",
                "run_id": "node-b",
                "name": "lookup",
                "data": {"input": {"query": "b"}},
            },
            {
                "event": "on_tool_start",
                "run_id": "node-a",
                "name": "lookup",
                "data": {"input": {"query": "a"}},
            },
            {
                "event": "on_tool_end",
                "run_id": "node-b",
                "name": "lookup",
                "data": {
                    "output": ToolMessage(
                        content="result-b",
                        tool_call_id="call-b",
                    ),
                },
            },
            {
                "event": "on_tool_end",
                "run_id": "node-a",
                "name": "lookup",
                "data": {
                    "output": ToolMessage(
                        content="result-a",
                        tool_call_id="call-a",
                    ),
                },
            },
        ])
        session = AgentSession(session_type="sub", agent_type="test")
        session.compiled_graph = graph
        emitted: list[dict] = []

        async def no_save():
            return None

        async def no_compress(*_args, **_kwargs):
            return None

        async def capture(event: dict):
            emitted.append(event)

        session.async_save = no_save
        session._check_and_compress_messages = no_compress
        await session.send_message("run tools", capture, max_rounds=2)
        return emitted, session.record

    emitted, record = asyncio.run(scenario())
    starts = [event for event in emitted if event["type"] == "tool_start"]
    ends = [event for event in emitted if event["type"] == "tool_end"]
    assert [
        (event["run_id"], event["index"], event["args"])
        for event in starts
    ] == [
        ("call-b", 1, {"query": "b"}),
        ("call-a", 0, {"query": "a"}),
    ]
    assert [
        (event["run_id"], event["result"])
        for event in ends
    ] == [("call-b", "result-b"), ("call-a", "result-a")]
    tool_records = [message for message in record if message["type"] == "tool"]
    assert [
        (message["tool_call_id"], message["content"])
        for message in tool_records
    ] == [("call-b", "result-b"), ("call-a", "result-a")]


def test_tool_error_terminates_matching_bubble_and_persists_failure():
    async def scenario():
        model_output = AIMessage(
            content="",
            tool_calls=[
                {"id": "call-failed", "name": "write", "args": {"path": "a.txt"}},
            ],
        )
        graph = _EventGraph([
            {
                "event": "on_chat_model_end",
                "run_id": "model-run",
                "data": {"output": model_output},
            },
            {
                "event": "on_tool_start",
                "run_id": "node-failed",
                "name": "write",
                "data": {"input": {"path": "a.txt"}},
            },
            {
                "event": "on_tool_error",
                "run_id": "node-failed",
                "name": "write",
                "data": {
                    "error": RuntimeError("disk unavailable"),
                    "tool_call_id": "call-failed",
                },
            },
        ])
        session = AgentSession(session_type="sub", agent_type="test")
        session.compiled_graph = graph
        emitted: list[dict] = []

        async def no_save():
            return None

        async def no_compress(*_args, **_kwargs):
            return None

        async def capture(event: dict):
            emitted.append(event)

        session.async_save = no_save
        session._check_and_compress_messages = no_compress
        await session.send_message("write", capture, max_rounds=2)
        return emitted, session.record, session.session_id

    emitted, record, session_id = asyncio.run(scenario())
    tool_end = next(event for event in emitted if event["type"] == "tool_end")
    assert tool_end == {
        "type": "tool_end",
        "session_id": session_id,
        "name": "write",
        "result": "disk unavailable",
        "run_id": "call-failed",
        "status": "failed",
    }
    tool_record = next(message for message in record if message["type"] == "tool")
    assert tool_record["tool_call_id"] == "call-failed"
    assert tool_record["status"] == "error"
    assert tool_record["tool_status"] == "failed"
    assert tool_record["content"] == "disk unavailable"


def test_session_snapshot_always_contains_empty_messages_and_active_stream():
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({"type": "stream_start", "session_id": "session-empty"})
        await bus.emit_chat({
            "type": "token",
            "session_id": "session-empty",
            "content": "joining now",
        })
        session = SimpleNamespace(
            session_id="session-empty",
            record=[],
            status="streaming",
            token_usage={},
        )
        return ws_handlers._build_session_snapshot(session, bus=bus)

    snapshot = asyncio.run(scenario())

    assert snapshot["type"] == "snapshot"
    assert snapshot["session_id"] == "session-empty"
    assert snapshot["messages"] == []
    assert snapshot["status"] == "streaming"
    assert snapshot["revision"] == 2
    assert snapshot["active_stream"]["segments"] == [
        {"type": "text", "content": "joining now"},
    ]


def test_missing_session_snapshot_discards_stale_recovery_state():
    async def scenario():
        bus = EventBus()
        await bus.emit_chat({"type": "stream_start", "session_id": "deleted-session"})
        manager = SimpleNamespace(
            get_session=lambda _session_id: None,
            get_main_session=lambda: None,
        )
        snapshot = ws_handlers._resolve_session_snapshot(
            manager,
            "deleted-session",
            bus=bus,
        )
        return bus, snapshot

    bus, snapshot = asyncio.run(scenario())

    assert snapshot["status"] == "error"
    assert snapshot["active_stream"] is None
    assert snapshot["revision"] == 0
    assert bus.get_active_stream("deleted-session") is None


def test_terminal_event_is_not_dropped_when_connection_queue_is_full():
    class _UnusedWebSocket:
        pass

    conn = _WsConnection(_UnusedWebSocket())
    conn.queue = asyncio.Queue(maxsize=1)

    assert conn.enqueue('{"type":"token"}', "token") is True
    assert conn.enqueue('{"type":"chain_end"}', "chain_end") is True
    assert conn.pending_count == 2


def test_resync_snapshot_is_not_dropped_when_connection_queue_is_full():
    class _UnusedWebSocket:
        pass

    conn = _WsConnection(_UnusedWebSocket())
    conn.queue = asyncio.Queue(maxsize=1)

    assert conn.enqueue('{"type":"token"}', "token") is True
    assert conn.enqueue('{"type":"snapshot"}', "snapshot") is True
    assert conn.pending_count == 2


def test_roundtable_final_turn_is_not_dropped_when_connection_queue_is_full():
    class _UnusedWebSocket:
        pass

    conn = _WsConnection(_UnusedWebSocket())
    conn.queue = asyncio.Queue(maxsize=1)

    assert conn.enqueue('{"type":"rt_token"}', "rt_token") is True
    assert conn.enqueue('{"type":"rt_turn_end"}', "rt_turn_end") is True
    assert conn.pending_count == 2


def test_overflow_is_bounded_and_marks_connection_for_reconnect(monkeypatch):
    monkeypatch.setattr("src.web.event_bus._WS_OVERFLOW_SIZE", 1)

    class _UnusedWebSocket:
        pass

    conn = _WsConnection(_UnusedWebSocket())
    conn.queue = asyncio.Queue(maxsize=1)

    assert conn.enqueue('{"type":"token"}', "token") is True
    assert conn.enqueue('{"type":"chain_end"}', "chain_end") is True
    assert conn.enqueue('{"type":"token"}', "token") is False
    assert conn.pending_count == 2
    assert conn.unhealthy is True


def test_last_nonterminal_event_drop_marks_connection_for_snapshot_reconnect():
    class _UnusedWebSocket:
        pass

    conn = _WsConnection(_UnusedWebSocket())
    conn.queue = asyncio.Queue(maxsize=1)

    assert conn.enqueue('{"type":"rt_token"}', "rt_token") is True
    assert conn.enqueue('{"type":"rt_paused"}', "rt_paused") is False
    assert conn.unhealthy is True


def test_dropping_an_already_closed_connection_does_not_leak_task_errors():
    async def scenario():
        task_errors: list[dict] = []

        class _AlreadyClosedWebSocket:
            async def close(self, code: int):
                assert code == 1013
                raise RuntimeError("close message has already been sent")

        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: task_errors.append(context))
        try:
            bus = EventBus()
            ws = _AlreadyClosedWebSocket()
            bus._connections[id(ws)] = _WsConnection(ws)
            bus._drop_connection(id(ws))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)

        return task_errors

    assert asyncio.run(scenario()) == []


def test_kill_cancels_pre_stream_invocation_and_restores_history_checkpoint():
    async def scenario():
        session = AgentSession(
            session_id="pre-stream-kill",
            session_type="sub",
            agent_type="test",
        )
        session.compiled_graph = object()
        entered_compression = asyncio.Event()
        never_finish = asyncio.Event()

        async def no_save():
            return None

        async def block_compression(*_args, **_kwargs):
            entered_compression.set()
            await never_finish.wait()

        session.async_save = no_save
        session._check_and_compress_messages = block_compression
        task = asyncio.create_task(session.send_message("must rollback"))
        await entered_compression.wait()

        manager = SessionManager()
        manager.sessions[session.session_id] = session
        result = await manager.kill_session(session.session_id)
        try:
            await task
        except asyncio.CancelledError:
            pass
        return session, result

    session, result = asyncio.run(scenario())
    assert result["success"] is True
    assert session.invocation_active is False
    assert session.status == "error"
    assert session.record == []
    assert session.lc_messages == []


def test_kill_cancels_manual_compression_and_restores_history_checkpoint():
    async def scenario():
        session = AgentSession(
            session_id="manual-compression-kill",
            session_type="sub",
            agent_type="test",
        )
        original_record = [{"id": "msg_00001", "type": "user", "content": "old"}]
        session.record = list(original_record)
        session.lc_messages = [HumanMessage(content="old")]
        session.context = {"messages": [{"role": "user", "content": "old"}]}
        entered_compression = asyncio.Event()
        never_finish = asyncio.Event()

        async def no_save():
            return None

        async def block_after_mutation(*_args, **_kwargs):
            session.record.append({
                "id": "compression-divider",
                "type": "compression_divider",
                "content": "new",
            })
            session.lc_messages.append(HumanMessage(content="new"))
            session.context["messages"].append({"role": "user", "content": "new"})
            entered_compression.set()
            await never_finish.wait()

        session.async_save = no_save
        session._check_and_compress_messages = block_after_mutation
        task = asyncio.create_task(session.compress())
        await entered_compression.wait()

        manager = SessionManager()
        manager.sessions[session.session_id] = session
        result = await manager.kill_session(session.session_id)
        try:
            await task
        except asyncio.CancelledError:
            pass
        return session, result, original_record

    session, result, original_record = asyncio.run(scenario())
    assert result["success"] is True
    assert session.invocation_active is False
    assert session.status == "error"
    assert session.record == original_record
    assert [message.content for message in session.lc_messages] == ["old"]
    assert session.context == {"messages": [{"role": "user", "content": "old"}]}


def test_abort_during_pre_stream_compression_never_starts_graph_or_stream():
    async def run_one(session_type: str):
        graph = _EventGraph([])
        session = AgentSession(
            session_id=f"pre-stream-abort-{session_type}",
            session_type=session_type,
            agent_type="test",
        )
        session.compiled_graph = graph
        entered_compression = asyncio.Event()
        release_compression = asyncio.Event()
        emitted: list[dict] = []

        async def no_save():
            return None

        async def block_compression(*_args, **_kwargs):
            entered_compression.set()
            await release_compression.wait()

        async def capture(event: dict):
            emitted.append(event)

        session.async_save = no_save
        session._check_and_compress_messages = block_compression
        task = asyncio.create_task(
            session.send_message("must not reach graph", capture, max_rounds=1)
        )
        await entered_compression.wait()
        abort_result = await session.abort()
        release_compression.set()
        await task
        return session, graph, emitted, abort_result

    async def scenario():
        return [
            await run_one("main"),
            await run_one("sub"),
        ]

    results = asyncio.run(scenario())
    for session, graph, emitted, abort_result in results:
        assert abort_result["success"] is True
        assert graph.called is False
        assert emitted == []
        assert session.record == []
        assert session.lc_messages == []
        expected_status = "running" if session.session_type == "main" else "completed"
        assert session.status == expected_status


def test_errors_emit_authoritative_history_after_rollback():
    """主会话失败（RuntimeError / BadRequestError）不报废：回滚该轮、事件
    terminal=False、状态保持 running，用户可原样重发。"""

    async def scenario(error: Exception):
        partial_chunk = SimpleNamespace(content="partial", additional_kwargs={})
        graph = _EventGraph([
            {
                "event": "on_chat_model_stream",
                "data": {"chunk": partial_chunk},
            },
        ], error=error)
        session = AgentSession(session_type="main", agent_type="test")
        session.compiled_graph = graph
        session.record = [
            {"id": "system", "type": "system_prompt", "content": "hidden"},
            {"id": "old-user", "type": "user", "content": "old question"},
            {"id": "old-answer", "type": "assistant", "content": "old answer"},
        ]
        session._msg_counter = 3
        session.lc_messages = [
            HumanMessage(content="old question"),
            AIMessage(content="old answer"),
        ]
        emitted: list[dict] = []
        record_when_error_emitted: list[list[dict]] = []

        async def no_save():
            return None

        async def no_compress(*_args, **_kwargs):
            return None

        async def capture(event: dict):
            if event["type"] == "error":
                record_when_error_emitted.append(list(session.record))
            emitted.append(event)

        session.async_save = no_save
        session._check_and_compress_messages = no_compress
        await session.send_message("new question", capture, max_rounds=1)
        return session, emitted, record_when_error_emitted

    bad_response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://example.test"),
    )
    errors = [
        RuntimeError("provider failed"),
        BadRequestError("bad request", response=bad_response, body=None),
    ]
    expected_history = [
        {"id": "old-user", "type": "user", "content": "old question"},
        {"id": "old-answer", "type": "assistant", "content": "old answer"},
    ]
    for error in errors:
        session, emitted, record_when_error_emitted = asyncio.run(scenario(error))
        terminal = next(event for event in emitted if event["type"] == "error")
        # 修复后：主会话失败不报废——terminal=False、状态 running、历史已回滚
        assert terminal["terminal"] is False
        assert terminal["messages"] == expected_history
        assert record_when_error_emitted == [[
            {"id": "system", "type": "system_prompt", "content": "hidden"},
            *expected_history,
        ]]
        assert session.record == [
            {"id": "system", "type": "system_prompt", "content": "hidden"},
            *expected_history,
        ]
        assert session.status == "running"


def test_execute_wrapper_does_not_duplicate_session_terminal_error(monkeypatch):
    async def scenario():
        emitted: list[dict] = []

        async def emit_chat(event: dict):
            emitted.append(event)

        async def fail_after_session_terminal():
            raise RuntimeError("provider failed")

        async def no_save():
            return None

        session = SimpleNamespace(
            status="error",
            record=[{"id": "old", "type": "assistant", "content": "stable"}],
            updated_at="",
            async_save=no_save,
        )
        monkeypatch.setattr(ws_handlers.event_bus, "emit_chat", emit_chat)
        await ws_handlers._execute_with_events(
            session,
            "session-1",
            fail_after_session_terminal(),
            "处理消息",
        )
        return emitted

    assert asyncio.run(scenario()) == []
