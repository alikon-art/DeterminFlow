"""
Workflow 专用工具 — 供 main agent（chat main 和 workflow main）使用。

所有任务控制工具接受显式 ``workflow_id + task_id``。两者同时省略时，
才回退到会话记录的最近任务；只提供其中一个会稳定失败，避免串任务。
任务的 ``main_session_id`` 是所有权事实来源。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.session.context import get_session_context

if TYPE_CHECKING:
    from src.workflow.manager import WorkflowManager
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)

_INTERNAL_ONLY_POLICY = "internal_only"
_TASK_TERMINAL_STATUSES = {"completed", "failed", "stopped", "cancelled"}


# ============================================================
# 通用 Helper
# ============================================================

def _ok(**data) -> str:
    """构建成功响应 JSON。自动添加 success=True。"""
    return json.dumps({"success": True, **data}, ensure_ascii=False)


def _fail(message: str, **extra) -> str:
    """构建失败响应 JSON。自动添加 success=False。"""
    return json.dumps({"success": False, "message": message, **extra}, ensure_ascii=False)


def _internal_only_failure(workflow_id: str) -> str:
    """Return the stable denial used by user-facing Workflow Agent tools."""
    return _fail(
        (
            f"Workflow {workflow_id} 只允许 Core 内部服务调用；"
            "请使用所属业务 API"
        ),
        error="workflow_internal_only",
    )


def _strip_node_outputs(task: dict) -> dict:
    """去掉 task node_states 里的 outputs/stdout/stderr 全文，只留进度元数据。

    修复：list_tasks 此前返回 task.to_dict() 的完整 node_states（含各节点
    outputs/stdout/stderr 全文），工作流单次运行即可产生数十万字符的工具结果，
    主会话反复调用 list_tasks 轮询会迅速撑爆上下文（曾实测 130 万 token > 104 万
    上限，导致会话 400 超限报废）。get_task_status 已做轻量化，这里与之一致。
    """
    node_states = task.get("node_states") or {}
    light: dict[str, dict] = {}
    for nid, node in node_states.items():
        if not isinstance(node, dict):
            light[nid] = node  # type: ignore[assignment]
            continue
        light[nid] = {
            key: node.get(key)
            for key in (
                "node_id",
                "status",
                "summary",
                "error",
                "attempt_count",
                "automatic_retry_count",
                "next_retry_at",
                "available_actions",
                "started_at",
                "completed_at",
                "rejection_count",
                "rejection_reason",
                "reject_upstream_count",
            )
        }
    cleaned = dict(task)
    cleaned["node_states"] = light
    return cleaned


def _policy_unavailable_failure(workflow_id: str) -> str:
    """Fail closed when a tool cannot establish a workflow's policy."""
    return _fail(
        f"无法确认 Workflow {workflow_id} 的执行策略，已拒绝操作",
        error="workflow_policy_unavailable",
    )


def _policy_invalid_failure(workflow_id: str) -> str:
    return _fail(
        f"Workflow {workflow_id} 的执行策略无效，已拒绝操作",
        error="workflow_execution_policy_invalid",
    )


def _load_tool_visible_workflow(
    workflow_manager: "WorkflowManager",
    workflow_id: str,
) -> tuple[dict | None, str | None]:
    """Load a workflow and reject definitions reserved for internal services.

    The Workflow Agent tools are another user-controlled execution surface, so
    they must enforce the same boundary as the raw HTTP routes.  Returning the
    loaded definition also avoids a second lookup between policy validation and
    the caller's read operation.
    """
    try:
        workflow = workflow_manager.get_workflow(workflow_id)
    except Exception:
        logger.exception("读取 Workflow 执行策略失败: %s", workflow_id)
        return None, _policy_unavailable_failure(workflow_id)

    if not workflow:
        return None, None
    definition = workflow.get("definition")
    if not isinstance(definition, dict):
        return None, _policy_unavailable_failure(workflow_id)
    policy = definition.get("http_execution_policy")
    if policy == _INTERNAL_ONLY_POLICY:
        return None, _internal_only_failure(workflow_id)
    if policy not in (None, "", "public"):
        return None, _policy_invalid_failure(workflow_id)
    return workflow, None


def _ensure_tool_execution_allowed(
    workflow_manager: "WorkflowManager",
    workflow_id: str,
) -> str | None:
    """Return an error response unless a public workflow policy is confirmed."""
    workflow, error = _load_tool_visible_workflow(workflow_manager, workflow_id)
    if error:
        return error
    if workflow is None:
        return _fail(f"工作流 {workflow_id} 不存在")
    return None


def _current_session_id() -> str:
    return get_session_context().get("session_id", "")


def _resolve_task_ref(
    session_manager,
    workflow_id: str,
    task_id: str,
    action_desc: str,
) -> tuple[str, str, str | None]:
    """解析显式 TaskRef；两项都省略时兼容最近任务绑定。"""
    if bool(workflow_id) != bool(task_id):
        return "", "", _fail(
            "workflow_id 与 task_id 必须同时提供或同时省略",
            error="task_ref_incomplete",
        )
    if workflow_id and task_id:
        return workflow_id, task_id, None

    binding = _get_workflow_binding(session_manager)
    workflow_id = binding["workflow_id"]
    task_id = binding["task_id"]
    if not workflow_id or not task_id:
        return "", "", _fail(
            f"当前会话没有最近任务，无法{action_desc}；请提供 workflow_id 与 task_id",
            error="task_ref_required",
        )
    return workflow_id, task_id, None


def _ensure_task_owned(
    workflow_manager: "WorkflowManager",
    workflow_id: str,
    task_id: str,
) -> tuple[dict | None, str | None]:
    """校验当前 Main 对目标任务的持久化所有权。"""
    session_id = _current_session_id()
    if not session_id:
        return None, _fail("无法获取当前会话 ID", error="session_context_missing")
    try:
        task = workflow_manager.get_task(workflow_id, task_id)
    except Exception:
        logger.exception("读取任务所有权失败: %s/%s", workflow_id, task_id)
        return None, _fail(
            "无法读取任务所有权",
            error="task_ownership_unavailable",
        )
    if task is None:
        return None, _fail(
            f"任务 {task_id} 不存在",
            error="task_not_found",
            workflow_id=workflow_id,
            task_id=task_id,
        )
    if task.get("main_session_id") != session_id:
        return None, _fail(
            "当前 Main 无权操作该任务",
            error="task_not_owned",
            workflow_id=workflow_id,
            task_id=task_id,
        )
    return task, None


def _task_progress_from_dict(task: dict) -> dict:
    definition = task.get("snapshot_definition") or {}
    disabled = set(task.get("disabled_node_ids") or [])
    executable_ids = {
        node.get("id")
        for node in definition.get("nodes", [])
        if node.get("id") and node.get("id") not in disabled
    }
    terminal_statuses = {"completed", "success", "skipped"}
    states = task.get("node_states") or {}
    completed = sum(
        1
        for node_id in executable_ids
        if (states.get(node_id) or {}).get("status") in terminal_statuses
    )
    return {"completed": completed, "total": len(executable_ids)}


def _task_attention_required(task: dict) -> bool:
    if task.get("status") in {"pending", "pre_running"}:
        return True
    return any(
        state.get("status") == "waiting_approval"
        for state in (task.get("node_states") or {}).values()
    )


def _task_wait_fingerprint(task: dict) -> tuple[str, str]:
    """只以已持久化的任务版本判定变化。"""
    return (
        str(task.get("status") or ""),
        str(task.get("updated_at") or ""),
    )


def _task_wait_metadata(task: dict, outcome: str, started_at: float) -> dict:
    status = str(task.get("status") or "unknown")
    terminal = status in _TASK_TERMINAL_STATUSES
    attention_required = _task_attention_required(task)
    if status == "failed":
        attention_required = any(
            bool(state.get("available_actions"))
            for state in (task.get("node_states") or {}).values()
        )
    return {
        "wait_outcome": outcome,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "terminal": terminal,
        "attention_required": attention_required,
    }


async def _wait_for_task_snapshot(
    workflow_manager: "WorkflowManager",
    workflow_id: str,
    task_id: str,
    initial_task: dict,
    *,
    wait_for: str,
    timeout_seconds: float | None,
) -> tuple[dict | None, dict, str | None]:
    """事件驱动等待任务，每次唤醒后重读持久化快照。"""
    if wait_for == "none":
        return initial_task, {}, None

    started_at = time.monotonic()
    task = initial_task
    initial_fingerprint = _task_wait_fingerprint(initial_task)
    while True:
        status = str(task.get("status") or "")
        if status in _TASK_TERMINAL_STATUSES:
            return task, _task_wait_metadata(task, "terminal", started_at), None
        if _task_attention_required(task):
            return (
                task,
                _task_wait_metadata(task, "attention_required", started_at),
                None,
            )
        if (
            wait_for == "change"
            and _task_wait_fingerprint(task) != initial_fingerprint
        ):
            return task, _task_wait_metadata(task, "changed", started_at), None

        elapsed = time.monotonic() - started_at
        remaining = (
            None
            if timeout_seconds is None
            else max(0.0, timeout_seconds - elapsed)
        )
        changed = await workflow_manager.wait_for_task_update(
            workflow_id,
            task_id,
            remaining,
        )
        if not changed:
            latest, ownership_error = _ensure_task_owned(
                workflow_manager, workflow_id, task_id,
            )
            if ownership_error:
                return None, {}, ownership_error
            assert latest is not None
            status = str(latest.get("status") or "")
            if status in _TASK_TERMINAL_STATUSES:
                return latest, _task_wait_metadata(
                    latest, "terminal", started_at,
                ), None
            if _task_attention_required(latest):
                return latest, _task_wait_metadata(
                    latest, "attention_required", started_at,
                ), None
            return latest, _task_wait_metadata(latest, "timeout", started_at), None

        latest, ownership_error = _ensure_task_owned(
            workflow_manager, workflow_id, task_id,
        )
        if ownership_error:
            return None, {}, ownership_error
        assert latest is not None
        task = latest


# ============================================================
# Helper：从 session 对象读取绑定（跨 asyncio task 安全）
# ============================================================

def _get_workflow_binding(session_manager) -> dict:
    """从 session 对象直接读取当前绑定的 workflow_id/task_id。

    session 对象是跨 asyncio task 共享的同一 Python 对象，
    create_and_attach_task 对 session.workflow_id 的修改对所有工具可见。

    为什么不直接用 contextvars？
    LangGraph 的 ainvoke 会在子 asyncio task 中执行工具协程，
    子 task 中的 contextvars 修改不会传播回父 task。
    """
    ctx = get_session_context()
    session_id = ctx.get("session_id", "")
    if not session_id:
        return {"workflow_id": "", "task_id": ""}

    sessions = getattr(session_manager, 'sessions', {})
    session = sessions.get(session_id)
    if session is None:
        return {"workflow_id": "", "task_id": ""}

    return {
        "workflow_id": getattr(session, 'workflow_id', '') or '',
        "task_id": getattr(session, 'task_id', '') or '',
    }


# ============================================================
# Args Models
# ============================================================

class SetWorkflowVariableArgs(BaseModel):
    """set_workflow_variable 工具参数"""
    workflow_id: str = Field(default="", description="工作流 ID；必须与 task_id 同时提供")
    task_id: str = Field(default="", description="任务 ID；必须与 workflow_id 同时提供")
    key: str = Field(description="变量 key（全局变量定义中的唯一标识）")
    value: str = Field(description="变量值")


class StartWorkflowTaskArgs(BaseModel):
    """start_workflow_task 参数"""
    workflow_id: str = Field(default="", description="工作流 ID；必须与 task_id 同时提供")
    task_id: str = Field(default="", description="任务 ID；必须与 workflow_id 同时提供")


class ApproveNodeArgs(BaseModel):
    """approve_node 工具参数"""
    workflow_id: str = Field(default="", description="工作流 ID；必须与 task_id 同时提供")
    task_id: str = Field(default="", description="任务 ID；必须与 workflow_id 同时提供")
    node_id: str = Field(description="节点 ID")
    expected_attempt_count: int = Field(
        description="从最新任务状态读取的节点 attempt_count，用于阻止过期审批",
    )
    approved: bool = Field(description="是否批准：true=通过，false=拒绝")
    feedback: str = Field(
        default="",
        description="审批意见。拒绝时建议提供具体反馈，帮助节点改进",
    )


# ============================================================
# 工具工厂
# ============================================================

def create_set_workflow_variable_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 set_workflow_variable 工具。

    此工具允许 main agent 修改工作流任务中的全局变量值，
    修改后通过 WebSocket 推送 wf_variable_update 事件到前端，
    使左侧表单实时更新。
    """

    async def _set_workflow_variable(
        key: str,
        value: str,
        workflow_id: str = "",
        task_id: str = "",
    ) -> str:
        session_id = _current_session_id()
        workflow_id, task_id, err = _resolve_task_ref(
            session_manager, workflow_id, task_id, "修改变量",
        )
        if err:
            return err
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error
        _, ownership_error = _ensure_task_owned(
            workflow_manager, workflow_id, task_id,
        )
        if ownership_error:
            return ownership_error

        result = workflow_manager.set_workflow_variable(
            workflow_id=workflow_id,
            task_id=task_id,
            key=key,
            value=value,
            session_id=session_id,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="set_workflow_variable",
        description=(
            "修改 Main 所拥有任务的全局变量值。"
            "调用后用户左侧填参表单会实时更新。"
            "参数 key 对应变量定义中的唯一标识（如 repo_url, branch 等）。"
        ),
        args_schema=SetWorkflowVariableArgs,
        func=lambda **kw: None,
        coroutine=_set_workflow_variable,
    )


def create_start_workflow_task_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 start_workflow_task 工具。

    此工具允许 main agent 正式启动工作流任务执行。
    预启动阶段（pre_running）结束后，调用此工具进入正式执行。
    """

    async def _start_workflow_task(
        workflow_id: str = "",
        task_id: str = "",
    ) -> str:
        workflow_id, task_id, err = _resolve_task_ref(
            session_manager, workflow_id, task_id, "启动",
        )
        if err:
            return err
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error
        _, ownership_error = _ensure_task_owned(
            workflow_manager, workflow_id, task_id,
        )
        if ownership_error:
            return ownership_error

        try:
            result = await workflow_manager.start_pre_running_task(
                workflow_id=workflow_id,
                task_id=task_id,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("start_workflow_task 失败")
            return _fail(f"启动任务失败: {e}")

    return StructuredTool(
        name="start_workflow_task",
        description=(
            "正式启动 Main 所拥有的工作流任务。"
            "调用此工具前请确保所有必要的全局变量已填写完毕。"
            "启动后按 Workflow 拓扑自动执行；只有 Task 显式启用 main_takeover "
            "或定义包含 Approval 节点时才需要审批。"
        ),
        args_schema=StartWorkflowTaskArgs,
        func=lambda **kw: None,
        coroutine=_start_workflow_task,
    )


def create_approve_node_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 approve_node 工具。

    此工具用于审批 sub agent 调用 complete_node_task 后的节点产出。
    - 通过：引擎继续执行下一个节点
    - 拒绝：引擎回滚到上一个节点，将拒绝原因发送给 sub agent 重新执行
    """

    async def _approve_node(
        node_id: str,
        approved: bool,
        expected_attempt_count: int,
        feedback: str = "",
        workflow_id: str = "",
        task_id: str = "",
    ) -> str:
        workflow_id, task_id, err = _resolve_task_ref(
            session_manager, workflow_id, task_id, "审批",
        )
        if err:
            return err
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error
        _, ownership_error = _ensure_task_owned(
            workflow_manager, workflow_id, task_id,
        )
        if ownership_error:
            return ownership_error

        result = workflow_manager.approve_node(
            workflow_id=workflow_id,
            task_id=task_id,
            node_id=node_id,
            approved=approved,
            feedback=feedback,
            expected_attempt_count=expected_attempt_count,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="approve_node",
        description=(
            "审批工作流节点的完成产出。"
            "当 sub agent 调用 complete_node_task 后，你会收到审批请求。"
            "显式携带 workflow_id、task_id 和最新 expected_attempt_count，"
            "批准（approved=true）或拒绝（approved=false）节点产出。"
            "拒绝时请提供 feedback，帮助节点改进产出；最多可拒绝 3 次。"
        ),
        args_schema=ApproveNodeArgs,
        func=lambda **kw: None,
        coroutine=_approve_node,
    )


# ============================================================
# 新增 Args Models（Chat Main 可用的查询/操作工具）
# ============================================================

class ListWorkflowsArgs(BaseModel):
    """list_workflows 参数（无参数，占位）"""
    pass


class GetWorkflowArgs(BaseModel):
    """get_workflow 工具参数"""
    workflow_id: str = Field(description="工作流 ID")


class CreateAndAttachTaskArgs(BaseModel):
    """create_and_attach_task 工具参数"""
    workflow_id: str = Field(description="工作流 ID")
    parameter_values: dict[str, str] | None = Field(
        default=None,
        description="可选的参数值字典，key 对应变量定义中的唯一标识",
    )
    scheme_id: str | None = Field(
        default=None,
        description="可选执行方案 ID；与 selected_node_ids 二选一",
    )
    selected_node_ids: list[str] | None = Field(
        default=None,
        description="可选的执行节点 ID 列表；优先于 scheme_id",
    )
    workspace_mode: str = Field(
        default="task_isolated",
        description="工作空间模式：task_isolated（默认）、named_shared 或 legacy_shared",
    )
    workspace_ref: str | None = Field(
        default=None,
        description="named_shared 模式使用的安全共享名称",
    )
    main_takeover: bool = Field(
        default=False,
        description="是否启用 Main 接管审批；默认关闭，启用后每个 Agent 节点完成时等待 Main 审批",
    )


class ListTasksArgs(BaseModel):
    """list_tasks 工具参数"""
    workflow_id: str = Field(
        default="",
        description="可选工作流 ID；不传则列出当前 Main 的全部任务",
    )
    status: str = Field(
        default="",
        description="按状态过滤：pending/running/completed/failed/stopped，空字符串表示全部",
    )
    limit: int = Field(
        default=20,
        description="返回条数上限",
    )


class TaskRefArgs(BaseModel):
    """Workflow Task 完整引用。"""
    workflow_id: str = Field(
        default="",
        description="工作流 ID。不传则使用当前已绑定工作流",
    )
    task_id: str = Field(
        default="",
        description="任务 ID。不传则使用当前已绑定任务",
    )


class TaskWaitArgs(TaskRefArgs):
    """支持事件驱动等待的 Workflow Task 查询参数。"""
    wait_for: Literal["none", "change", "terminal_or_attention"] = Field(
        default="none",
        description=(
            "等待条件：none=立即返回，change=变化或超时返回，"
            "terminal_or_attention=终态或需要 Main 介入时返回"
        ),
    )
    timeout_seconds: float | None = Field(
        default=0,
        ge=0,
        le=86_400,
        description="最长等待秒数；null 表示无截止时间，仍可取消",
    )


class GetTaskStatusArgs(TaskWaitArgs):
    """get_task_status 工具参数。"""


class StopTaskArgs(BaseModel):
    """stop_task 工具参数"""
    workflow_id: str = Field(
        default="",
        description="工作流 ID。不传则使用当前已绑定工作流",
    )
    task_id: str = Field(
        default="",
        description="任务 ID。不传则使用当前已绑定任务",
    )


class GetTaskResultArgs(TaskWaitArgs):
    """get_task_result 工具参数。"""


class GetNodeMessagesArgs(TaskRefArgs):
    """get_node_messages 工具参数。"""
    node_id: str = Field(description="节点 ID")


class ReadTaskArtifactArgs(TaskRefArgs):
    """read_task_artifact 工具参数。"""
    artifact_ref: str = Field(description="get_task_result 返回的 artifact_ref")
    offset: int = Field(default=0, description="从第几个字符开始读取")
    limit: int = Field(default=20_000, description="最多读取字符数，上限 50000")


class NodeControlArgs(GetNodeMessagesArgs):
    """retry_node / skip_node 工具参数。"""
    expected_attempt_count: int = Field(
        description="从最新任务状态读取的节点 attempt_count",
    )


# ============================================================
# 新增工具工厂
# ============================================================

def create_list_workflows_tool(
    workflow_manager: "WorkflowManager",
) -> StructuredTool:
    """创建 list_workflows 工具 — 列出所有工作流定义。"""

    async def _list_workflows() -> str:
        try:
            workflows = workflow_manager.list_workflows()
            visible_workflows = []
            for workflow in workflows or []:
                workflow_id = workflow.get("workflow_id", "")
                if not workflow_id:
                    continue
                _, policy_error = _load_tool_visible_workflow(
                    workflow_manager,
                    workflow_id,
                )
                if policy_error:
                    continue
                visible_workflows.append(workflow)
            if not visible_workflows:
                return _ok(workflows=[], message="当前没有任何工作流定义")
            return _ok(
                workflows=visible_workflows,
                count=len(visible_workflows),
            )
        except Exception as e:
            logger.exception("list_workflows 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="list_workflows",
        description=(
            "列出所有工作流定义。返回名称、ID、节点数、版本、创建时间和运行状态。"
            "用于发现可用的工作流模板，选择后可用 create_and_attach_task 创建任务。"
        ),
        args_schema=ListWorkflowsArgs,
        func=lambda **kw: None,
        coroutine=_list_workflows,
    )


def create_get_workflow_tool(
    workflow_manager: "WorkflowManager",
) -> StructuredTool:
    """创建 get_workflow 工具 — 获取单个工作流详情（含节点和变量定义）。"""

    async def _get_workflow(workflow_id: str) -> str:
        try:
            wf_data, policy_error = _load_tool_visible_workflow(
                workflow_manager,
                workflow_id,
            )
            if policy_error:
                return policy_error
            if not wf_data:
                return _fail(f"工作流 {workflow_id} 不存在")
            definition = wf_data["definition"]
            nodes_info = []
            for n in definition.get("nodes", []):
                nodes_info.append({
                    "id": n.get("id", ""),
                    "label": n.get("label", ""),
                    "node_type": n.get("node_type", "agent"),
                    "agent_type": n.get("agent_type", ""),
                    "system_prompt_template": n.get("system_prompt_template", ""),
                    "first_message": n.get("first_message", ""),
                    "var_bindings": n.get("var_bindings", {}),
                    "node_params": n.get("node_params", {}),
                })
            variables = definition.get("variables", [])
            edges = definition.get("edges", [])
            return _ok(
                name=definition.get("name", ""),
                workflow_id=workflow_id,
                version=definition.get("version", 0),
                nodes=nodes_info,
                edges=[{"id": e.get("id", ""), "source": e.get("source", ""), "target": e.get("target", "")} for e in edges],
                variables=[
                    {"key": v.get("key", ""), "name": v.get("name", ""),
                     "type": v.get("type", "text"),
                     "required": v.get("required", False),
                     "default": v.get("default", ""),
                     "description": v.get("description", ""),
                     "options": v.get("options", [])}
                    for v in variables
                ],
                node_count=len(nodes_info),
                variable_count=len(variables),
            )
        except Exception as e:
            logger.exception("get_workflow 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="get_workflow",
        description=(
            "获取指定工作流的完整定义，包括节点列表（ID、标签、Agent 类型、system prompt、首条任务消息、变量绑定）、"
            "节点间的连线关系（edges）、以及全局变量定义（含 select 类型的可选项）。"
            "用于全面了解工作流的执行结构、各节点的行为指令和任务模板、以及需要填写的参数。"
        ),
        args_schema=GetWorkflowArgs,
        func=lambda **kw: None,
        coroutine=_get_workflow,
    )


def create_create_and_attach_task_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 create_and_attach_task 工具 — 创建 pre_running 任务并绑定当前 session。"""

    async def _create_and_attach_task(
        workflow_id: str,
        parameter_values: dict[str, str] | None = None,
        scheme_id: str | None = None,
        selected_node_ids: list[str] | None = None,
        workspace_mode: str = "task_isolated",
        workspace_ref: str | None = None,
        main_takeover: bool = False,
    ) -> str:
        ctx = get_session_context()
        session_id = ctx.get("session_id", "")

        if not session_id:
            return _fail("无法获取当前会话 ID")

        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            result = workflow_manager.create_and_attach_task_for_session(
                workflow_id=workflow_id,
                session_id=session_id,
                parameter_values=parameter_values,
                scheme_id=scheme_id,
                selected_node_ids=selected_node_ids,
                workspace_mode=workspace_mode,
                workspace_ref=workspace_ref,
                main_takeover=main_takeover,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("create_and_attach_task 失败")
            return _fail(f"创建失败: {e}")

    return StructuredTool(
        name="create_and_attach_task",
        description=(
            "为当前 Main 创建一个可独立寻址的工作流任务。"
            "创建后任务处于预启动状态（pre_running），"
            "你可以用 set_workflow_variable 填充变量，确认无误后用 start_workflow_task 启动执行。"
            "会话只把新任务记录为最近任务，不影响此前任务继续运行。"
            "默认 task_isolated 工作空间；需要跨任务共享时显式使用 named_shared。"
            "Main 默认只跟踪任务；仅在明确需要逐 Agent 节点审批时设置 main_takeover=true。"
        ),
        args_schema=CreateAndAttachTaskArgs,
        func=lambda **kw: None,
        coroutine=_create_and_attach_task,
    )


def create_list_tasks_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 list_tasks 工具 — 列出工作流任务历史。"""

    async def _list_tasks(
        workflow_id: str = "", status: str = "", limit: int = 20,
    ) -> str:
        session_id = _current_session_id()
        if not session_id:
            return _fail("无法获取当前会话 ID", error="session_context_missing")
        if workflow_id:
            policy_error = _ensure_tool_execution_allowed(
                workflow_manager, workflow_id,
            )
            if policy_error:
                return policy_error

        try:
            result = workflow_manager.list_all_tasks(
                workflow_id=workflow_id,
                main_session_id=session_id,
                status=status,
                page_size=max(1, min(limit, 100)),
            )
            tasks = result["tasks"] if isinstance(result, dict) else (result or [])
            visible_tasks = []
            for task in tasks:
                task_workflow_id = task.get("workflow_id", "")
                if not task_workflow_id:
                    continue
                _, task_policy_error = _load_tool_visible_workflow(
                    workflow_manager, task_workflow_id,
                )
                if task_policy_error:
                    continue
                visible_tasks.append(_strip_node_outputs(task))
            return _ok(
                workflow_id=workflow_id,
                tasks=visible_tasks,
                total=len(visible_tasks),
                status_filter=status,
            )
        except Exception as e:
            logger.exception("list_tasks 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="list_tasks",
        description=(
            "列出当前 Main 所拥有的工作流任务，支持按 workflow_id、状态和条数过滤。"
            "workflow_id 不传时跨工作流列出，其他 Main 的任务不会返回。"
        ),
        args_schema=ListTasksArgs,
        func=lambda **kw: None,
        coroutine=_list_tasks,
    )


def create_get_task_status_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 get_task_status 工具 — 获取单个任务执行状态（含节点进度）。"""

    async def _get_task_status(
        workflow_id: str = "",
        task_id: str = "",
        wait_for: str = "none",
        timeout_seconds: float | None = 0,
    ) -> str:
        workflow_id, task_id, ref_error = _resolve_task_ref(
            session_manager, workflow_id, task_id, "查询状态",
        )
        if ref_error:
            return ref_error
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            task, ownership_error = _ensure_task_owned(
                workflow_manager, workflow_id, task_id,
            )
            if ownership_error:
                return ownership_error
            assert task is not None

            task, wait_metadata, wait_error = await _wait_for_task_snapshot(
                workflow_manager,
                workflow_id,
                task_id,
                task,
                wait_for=wait_for,
                timeout_seconds=timeout_seconds,
            )
            if wait_error:
                return wait_error
            assert task is not None

            nodes_summary = {}
            for nid, ns in task.get("node_states", {}).items():
                nodes_summary[nid] = {
                    "status": ns.get("status", "pending"),
                    "summary": ns.get("summary", ""),
                    "error": ns.get("error", ""),
                    "attempt_count": ns.get("attempt_count", 0),
                    "automatic_retry_count": ns.get("automatic_retry_count", 0),
                    "next_retry_at": ns.get("next_retry_at"),
                    "available_actions": ns.get("available_actions", []),
                    "started_at": ns.get("started_at"),
                    "completed_at": ns.get("completed_at"),
                }
            return _ok(
                task_id=task_id,
                workflow_id=workflow_id,
                name=task.get("name", ""),
                status=task.get("status", "unknown"),
                current_node_id=task.get("current_node_id", ""),
                progress=_task_progress_from_dict(task),
                node_states=nodes_summary,
                started_at=task.get("started_at", ""),
                completed_at=task.get("completed_at", ""),
                updated_at=task.get("updated_at", ""),
                **wait_metadata,
            )
        except Exception as e:
            logger.exception("get_task_status 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="get_task_status",
        description=(
            "获取当前 Main 所拥有任务的最新状态、节点进度、错误和可用恢复动作。"
            "可事件驱动等待状态变化、终态或需要 Main 介入的状态。"
            "建议显式提供 workflow_id/task_id；两者都省略时使用最近任务。"
        ),
        args_schema=GetTaskStatusArgs,
        func=lambda **kw: None,
        coroutine=_get_task_status,
    )


def create_stop_task_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 stop_task 工具 — 停止运行中的任务。"""

    async def _stop_task(workflow_id: str = "", task_id: str = "") -> str:
        workflow_id, task_id, ref_error = _resolve_task_ref(
            session_manager, workflow_id, task_id, "停止任务",
        )
        if ref_error:
            return ref_error
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error
        _, ownership_error = _ensure_task_owned(
            workflow_manager, workflow_id, task_id,
        )
        if ownership_error:
            return ownership_error

        try:
            result = await workflow_manager.stop_task(workflow_id, task_id)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("stop_task 失败")
            return _fail(f"停止失败: {e}")

    return StructuredTool(
        name="stop_task",
        description=(
            "停止一个正在运行的工作流任务。"
            "建议显式提供 workflow_id/task_id；两者都省略时使用最近任务。"
        ),
        args_schema=StopTaskArgs,
        func=lambda **kw: None,
        coroutine=_stop_task,
    )
