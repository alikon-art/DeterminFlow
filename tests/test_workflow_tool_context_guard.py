"""上下文防护测试：list_tasks 轻量化 + 压缩摘要截断 + 失败兜底。

背景：list_tasks 曾返回 task.to_dict() 的完整 node_states（含各节点
outputs/stdout/stderr 全文），主会话反复轮询导致上下文被灌到 130 万 token，
超过模型 104 万上限 → 400 超限 → 会话报废（压缩自救也因摘要超限失败）。
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.workflow.tools import _strip_node_outputs


def test_strip_node_outputs_removes_heavy_fields():
    task = {
        "task_id": "t1",
        "workflow_id": "w",
        "status": "running",
        "node_states": {
            "n1": {
                "status": "completed",
                "outputs": "X" * 50_000,
                "stdout": "Y" * 50_000,
                "stderr": "Z" * 50_000,
                "summary": "ok",
                "attempt_count": 1,
                "started_at": "2026-01-01",
                "completed_at": "2026-01-02",
            }
        },
    }
    cleaned = _strip_node_outputs(task)
    node = cleaned["node_states"]["n1"]
    assert "outputs" not in node
    assert "stdout" not in node
    assert "stderr" not in node
    assert node["summary"] == "ok"
    assert node["status"] == "completed"
    assert len(str(cleaned)) < 2_000


def test_strip_node_outputs_preserves_task_meta():
    task = {"task_id": "t1", "workflow_id": "w", "status": "running",
            "node_states": {}, "name": "任务", "created_at": "2026-01-01"}
    cleaned = _strip_node_outputs(task)
    assert cleaned["task_id"] == "t1"
    assert cleaned["workflow_id"] == "w"
    assert cleaned["name"] == "任务"
    assert cleaned["node_states"] == {}


def test_prepare_user_content_truncates_huge_tool_result():
    from src.compression.strategies.full import FullCompactStrategy

    strategy = FullCompactStrategy()
    huge_tool = ToolMessage(content="A" * 60_000, tool_call_id="call_1")
    user = HumanMessage(content="你好")
    content = strategy._prepare_user_content([huge_tool, user])
    # 摘要 prompt 必须远小于巨型工具结果
    assert len(content) < 5_000
    assert "已截断" in content


def test_truncate_oversized_fallback_trims_huge_messages():
    from src.compression.strategies.full import FullCompactStrategy

    strategy = FullCompactStrategy()
    messages = [
        SystemMessage(content="sys"),
        ToolMessage(content="T" * 50_000, tool_call_id="call_1"),
        HumanMessage(content="H" * 50_000),
        AIMessage(content="正常回复"),
    ]
    cleaned = strategy._truncate_oversized(messages)
    # 正常消息不动
    assert cleaned[0].content == "sys"
    assert cleaned[3].content == "正常回复"
    # 超长消息被截断
    assert len(str(cleaned[1].content)) < 3_000
    assert len(str(cleaned[2].content)) < 4_000
    assert "已截断" in str(cleaned[1].content)
    assert "已截断" in str(cleaned[2].content)
