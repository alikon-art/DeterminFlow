"""agent 节点空输出重试与消息提取兼容性测试。

背景：专业润色（agent_pp）节点偶发失败——模型（DeepSeek 推理模式）输出空
content，节点判"没有最终 AI 输出可保存"直接失败，触发任务级恢复重跑整条
管线（已完成节点重复执行，实测 3 次才成功）。修复：复用同一子会话重试空
输出；同时让消息提取兼容结构化 content（list 块）。
"""

import asyncio

import pytest

from src.workflow.nodes.agent import AgentNode


class FakeSession:
    """最小子会话桩：record + send_message。"""

    def __init__(self, record=None):
        self.record = list(record or [])
        self.sent_messages = []

    async def send_message(self, message, max_rounds=1, **kwargs):
        self.sent_messages.append(message)
        # 默认追加一条非空 assistant 回复
        self.record.append(
            {"id": f"msg_{len(self.record) + 1:05d}", "type": "assistant",
             "content": "重试后的完整输出"}
        )
        return {"success": True}


class FakeSM:
    def __init__(self, sessions):
        self.sessions = sessions


def make_plugin():
    # AgentNodePlugin 是 async 方法集，无 __init__ 依赖，直接实例化
    return AgentNode()


def test_get_last_ai_message_skips_empty_and_handles_list():
    sm = FakeSM({
        "s1": FakeSession([
            {"id": "m1", "type": "user", "content": "指令"},
            {"id": "m2", "type": "assistant", "content": ""},
            {"id": "m3", "type": "assistant",
             "content": [{"type": "text", "text": "结构化正文"}]},
        ]),
    })
    # 最后一条非空 assistant（list 结构）应被提取为文本
    assert AgentNode._get_last_ai_message(sm, "s1") == "结构化正文"


def test_get_last_ai_message_all_empty_returns_empty():
    sm = FakeSM({
        "s1": FakeSession([
            {"id": "m1", "type": "assistant", "content": ""},
            {"id": "m2", "type": "assistant", "content": []},
        ]),
    })
    assert AgentNode._get_last_ai_message(sm, "s1") == ""


def test_get_latest_ai_message_handles_list_content():
    sm = FakeSM({
        "s1": FakeSession([
            {"id": "m1", "type": "assistant",
             "content": [{"type": "text", "text": "最新输出"}]},
        ]),
    })
    assert AgentNode._get_latest_ai_message(sm, "s1") == "最新输出"


def test_get_latest_ai_message_empty_content_returns_empty():
    # 最新 assistant 为空时不得回退到更早消息
    sm = FakeSM({
        "s1": FakeSession([
            {"id": "m1", "type": "assistant", "content": "早先输出"},
            {"id": "m2", "type": "assistant", "content": ""},
        ]),
    })
    assert AgentNode._get_latest_ai_message(sm, "s1") == ""


def test_retry_empty_output_succeeds_on_first_retry():
    plugin = make_plugin()
    session = FakeSession([
        {"id": "m1", "type": "assistant", "content": ""},
    ])
    sm = FakeSM({"s1": session})
    text = asyncio.run(plugin._retry_empty_output(sm, "s1"))
    assert text == "重试后的完整输出"
    assert len(session.sent_messages) == 1


def test_retry_empty_output_fails_after_all_attempts():
    plugin = make_plugin()

    class AlwaysEmptySession(FakeSession):
        async def send_message(self, message, max_rounds=1, **kwargs):
            self.sent_messages.append(message)
            self.record.append(
                {"id": f"msg_{len(self.record) + 1:05d}", "type": "assistant",
                 "content": ""}
            )
            return {"success": True}

    session = AlwaysEmptySession([
        {"id": "m1", "type": "assistant", "content": ""},
    ])
    sm = FakeSM({"s1": session})
    text = asyncio.run(plugin._retry_empty_output(sm, "s1"))
    assert text == ""
    assert len(session.sent_messages) == plugin.EMPTY_OUTPUT_RETRY_COUNT


def test_retry_empty_output_missing_session_returns_empty():
    plugin = make_plugin()
    assert asyncio.run(plugin._retry_empty_output(FakeSM({}), "ghost")) == ""
