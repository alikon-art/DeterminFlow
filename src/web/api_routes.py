"""

REST API 路由 - 提供会话/记忆/提示词/系统状态的全部 REST 端点



所有接口通过 app.state 获取共享实例（MCPClient、SessionManager 等）

"""

import json
import os
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from typing import Any

import httpx


from fastapi import APIRouter, Request, HTTPException, Body

from pydantic import BaseModel, ConfigDict, Field



from src.web.event_bus import event_bus
from src.core.utils import is_visible_to_frontend
from src.config import USER_INJECTION_CONFIG_FILE



logger = logging.getLogger(__name__)



router = APIRouter(prefix="/api")





# ============ 请求模型 ============









class SendMessageRequest(BaseModel):

    message: str


class CreateMainSessionRequest(BaseModel):

    agent_type: str = "main"
    from_session_id: str | None = None


class UpdateSessionModelRequest(BaseModel):

    model_id: str
    reasoning_effort: str | None = None





# ============ 辅助函数 ============



def _get_session_manager(request: Request):

    return request.app.state.session_manager





def _get_mcp_client(request: Request):

    return request.app.state.mcp_client





def _get_prompt_manager(request: Request):

    return request.app.state.prompt_manager



def _get_prompt_manager_by_type(request: Request, prompt_type: str = "main"):
    """获取指定 prompt_type 的提示词管理器（委托给 _get_prompt_manager）。"""
    return _get_prompt_manager(request)











def _get_skill_manager(request: Request):

    return getattr(request.app.state, "skill_manager", None)





# ============ 会话 API ============



@router.get("/sessions")

async def list_sessions(request: Request):

    """获取所有会话列表"""

    sm = _get_session_manager(request)
    return {
        "sessions": sm.get_session_summaries(),
        "active_sub_count": sm.get_active_sub_count(),
        "main_session_id": sm.main_session_id,
    }





@router.get("/sessions/tree")

async def get_session_tree(request: Request, main_id: str | None = None):

    """获取会话树结构。传入 ?main_id= 返回指定 main 的子树，不传则返回所有 main 的树。"""

    sm = _get_session_manager(request)

    return sm.get_session_tree(main_id=main_id)





@router.get("/sessions/{session_id}/system-prompt")

async def get_session_system_prompt(session_id: str, request: Request):

    """获取指定会话的完整 LLM 上下文概览（system_prompt + tools + 消息统计 + 模型配置 + 完整消息历史）"""

    sm = _get_session_manager(request)

    session = sm.get_session(session_id)

    if not session:

        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")



    from src.core.utils import estimate_tokens



    # 工具列表（LLM 通过 bind_tools 看到的 tools schema）

    tools_info = []

    for tool in session.tools:

        tool_data = {"name": tool.name, "description": tool.description or ""}

        # 提取参数 schema

        if hasattr(tool, "args_schema") and tool.args_schema:

            schema = tool.args_schema.model_json_schema()

            props = schema.get("properties", {})

            required = schema.get("required", [])

            tool_data["parameters"] = {

                k: {"type": v.get("type", "string"), "description": v.get("description", ""), "required": k in required}

                for k, v in props.items()

            }

        tools_info.append(tool_data)



    # 消息统计

    msg_counts = {"system": 0, "user": 0, "assistant": 0, "tool": 0}

    for m in session.record:

        role = m.get("type", m.get("role", ""))

        if role in msg_counts:

            msg_counts[role] += 1



    # Token 估算

    system_tokens = estimate_tokens(session.system_prompt) if session.system_prompt else 0

    messages_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in session.record if m.get("role") != "system")



    # 模型配置
    from src.core.model_manager import get_model_manager
    from src.config import MAX_CONTEXT_TOKENS, MAX_TOOL_ROUNDS
    model_manager = get_model_manager()
    # 优先使用会话自身的模型，否则使用动态默认。
    session_model = session.model_id or model_manager.get_default_model()
    provider_id = session_model.split(":", 1)[0] if session_model else ""
    provider_config = model_manager.get_provider(provider_id) or {}
    hyperparams = provider_config.get("hyperparameter_values", {})

    model_config = {
        "model": session_model or "",
        "temperature": hyperparams.get("temperature", 1.0),
        "max_context_tokens": MAX_CONTEXT_TOKENS,
        "max_tool_rounds": MAX_TOOL_ROUNDS,
    }


    return {

        "session_id": session.session_id,

        "agent_type": session.agent_type,

        "system_prompt": session.system_prompt,

        "tools": tools_info,

        "tools_count": len(tools_info),

        "message_counts": msg_counts,

        "token_estimate": {

            "system_prompt": system_tokens,

            "messages": messages_tokens,

            "total": system_tokens + messages_tokens,

        },

        "model_config": model_config,

        "messages": session.record,  # 返回完整的消息历史（LLM 实际看到的）

    }





@router.get("/sessions/{session_id}")

async def get_session_detail(session_id: str, request: Request):

    """获取会话详情（含完整消息历史）"""

    sm = _get_session_manager(request)

    session = sm.get_session(session_id)

    if not session:

        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")



    visible_messages = [m for m in session.record if is_visible_to_frontend(m)]

    result = {

        "session_id": session.session_id,

        "type": session.session_type,

        "parent_id": session.parent_id,

        "status": session.status,

        "task": session.task_description,

        "system_prompt": session.system_prompt,

        "agent_type": session.agent_type,

        "created_at": session.created_at,

        "updated_at": session.updated_at,

        "messages": visible_messages,

        "message_count": len(visible_messages),

        "has_graph": session.compiled_graph is not None,

        "runtime_scope": session.runtime_scope,

        "model_id": session.model_id,

        "model_params": session.model_params,

    }

    if session.workspace_path:
        result["workspace_path"] = session.workspace_path

    if session.token_usage:
        result["token_usage"] = session.token_usage

    last_error = getattr(session, "last_error", None)
    if last_error:
        result["last_error"] = last_error

    return result


@router.put("/sessions/{session_id}/model")
async def update_session_model(
    session_id: str,
    body: UpdateSessionModelRequest,
    request: Request,
):
    """切换交互 Main 会话的供应商、模型与推理强度。"""
    sm = _get_session_manager(request)
    result = await sm.update_session_model(
        session_id,
        model_id=body.model_id,
        reasoning_effort=body.reasoning_effort,
    )
    if not result["success"]:
        status_code = 404 if "未找到会话" in result["message"] else 400
        raise HTTPException(status_code=status_code, detail=result["message"])
    return result





@router.post("/sessions/{session_id}/kill")

async def kill_session(session_id: str, request: Request):

    """终止子会话"""

    sm = _get_session_manager(request)

    result = await sm.kill_session(session_id)



    if result["success"]:

        await event_bus.emit_event({

            "type": "session_update",

            "action": "killed",

            "session_id": session_id,

        })



    return result


@router.post("/sessions/{session_id}/abort")

async def abort_session(session_id: str, request: Request):
    """中止会话的流式输出（主/子会话均可）"""
    sm = _get_session_manager(request)
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")
    result = await session.abort()
    return result


@router.post("/sessions/{session_id}/compress")

async def compress_session(session_id: str, request: Request):
    """手动触发会话的上下文压缩"""
    sm = _get_session_manager(request)
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")
    result = await session.compress()

    # 压缩成功后，通过 WebSocket 推送更新后的消息给前端
    if result.get("success"):
        serialized_messages = [
            m for m in session.record if is_visible_to_frontend(m)
        ]
        await event_bus.emit_chat({
            "type": "chain_end",
            "session_id": session_id,
            "messages": serialized_messages,
        })

    return result




@router.post("/sessions/{session_id}/message")

async def send_to_session(session_id: str, body: SendMessageRequest, request: Request):

    """向任意会话发送消息（统一入口，不再限制为子会话）"""

    sm = _get_session_manager(request)

    result = await sm.send_message_to_session(session_id, body.message)

    return result





@router.delete("/sessions/{session_id}")

async def delete_session(session_id: str, request: Request):

    """删除会话；删除历史 Main 时级联清理其会话树与 Workflow Task。"""

    sm = _get_session_manager(request)

    result = await sm.delete_session(session_id)

    if not result["success"]:

        status_code = 404 if "未找到" in result["message"] else 400

        raise HTTPException(status_code=status_code, detail=result["message"])

    return result





@router.post("/sessions/main/new")
async def create_new_main_session(body: CreateMainSessionRequest, request: Request):
    """创建新的主会话（前端主动触发），支持指定 agent_type 与 workspace 继承。"""
    sm = _get_session_manager(request)
    llm = getattr(request.app.state, "llm", None)
    result = await sm.create_main_session(
        llm_client=llm,
        agent_type=body.agent_type,
        from_session_id=body.from_session_id,
    )
    return result


# ============ 提示词 API ============



@router.get("/prompt")

async def get_prompt(request: Request):

    """获取当前系统提示词"""

    pm = _get_prompt_manager(request)

    return pm.get_prompt()





@router.get("/prompt/history")

async def get_prompt_history(request: Request):

    """获取提示词修改历史"""

    pm = _get_prompt_manager(request)

    return {"history": pm.get_history()}


@router.get("/prompt-templates")
async def get_prompt_templates(request: Request):
    """获取所有可用的提示词模板名称列表（从 prompts_config.json 动态读取）。"""
    pm = _get_prompt_manager(request)
    return {"templates": pm.list_agent_types()}


@router.delete("/prompt-templates/{template_name}")
async def delete_prompt_template(template_name: str, request: Request):
    """删除指定的提示词模板。"""
    pm = _get_prompt_manager(request)
    try:
        success = pm.delete_agent_type(template_name)
        if not success:
            return {"success": False, "message": f"模板 '{template_name}' 不存在"}
        return {"success": True, "message": f"模板 '{template_name}' 已删除"}
    except ValueError as e:
        return {"success": False, "message": str(e)}


@router.get("/prompt-sections")

async def get_prompt_sections(request: Request, include_content: bool = False, prompt_type: str = "main"):

    """

    获取各提示词 section 的名称、token 估算和缓存状态（可观测性接口）。



    体现 Claude Code 提示词工程法则 11（可观测性是一级公民）：

    如果你不能观测 prompt，你就不能优化 prompt。



    Query params:

        include_content: 是否在响应中包含各 section 的完整内容（默认 False）



    返回格式：

    {

        "sections": [

            {

                "name": "intro",

                "token_estimate": 30,

                "cache_break": false,

                "cache_break_reason": "",

                "content_preview": "你是一个能够深度理解用户思维..."

                // 仅当 include_content=true 时包含：

                // "content": "完整内容..."

            }

        ],

        "total_token_estimate": 450,

        "sections_count": 8,

        "priority_in_use": "default_sections"

    }

    """

    pm = _get_prompt_manager_by_type(request, prompt_type)

    # 从统一 PromptManager 获取指定模板的 sections
    # 注意：不按 enabled 过滤，返回所有 sections（含 disabled），
    # 由前端负责在编辑器展示所有、在预览中仅展示 enabled 的。
    from src.core.utils import estimate_tokens
    raw_sections = pm.get_sections(prompt_type)
    sorted_sections = sorted(raw_sections, key=lambda s: s.get("order", 0))

    if not include_content:
        sections = [
            {
                "name": s.get("name", ""),
                "token_estimate": estimate_tokens(s.get("content", "")),
                "cache_break": s.get("cache_break", False),
                "cache_break_reason": s.get("cache_break_reason", ""),
                "enabled": s.get("enabled", True),
                "workflow_only": s.get("workflow_only", False),
                "content_preview": s.get("content", "")[:80],
            }
            for s in sorted_sections
        ]
    else:
        sections = [
            {
                "name": s.get("name", ""),
                "content": s.get("content", ""),
                "token_estimate": estimate_tokens(s.get("content", "")),
                "cache_break": s.get("cache_break", False),
                "cache_break_reason": s.get("cache_break_reason", ""),
                "enabled": s.get("enabled", True),
                "workflow_only": s.get("workflow_only", False),
                "order": s.get("order", i),
            }
            for i, s in enumerate(sorted_sections)
        ]

    return {
        "sections": sections,
        "total_token_estimate": sum(estimate_tokens(s.get("content", "")) for s in sorted_sections),
        "sections_count": len(sections),
        "priority_in_use": f"{prompt_type}_sections",
        "prompt_version": pm.get_version(prompt_type),
    }





# ============ 系统状态 API ============



@router.get("/system/status")

async def get_system_status(request: Request):

    """获取系统全局状态"""

    sm = _get_session_manager(request)

    pm = _get_prompt_manager(request)

    mcp = _get_mcp_client(request)



    prompt_data = pm.get_prompt()

    bus_stats = event_bus.get_stats()
    workflow_manager = getattr(request.app.state, "workflow_manager", None)
    execution_control = (
        workflow_manager.get_execution_control()
        if workflow_manager is not None
        else None
    )



    # 获取 temperature（从 ModelManager 动态获取）
    temperature = None
    try:
        from src.core.model_manager import get_model_manager
        model_manager = get_model_manager()
        default_model = model_manager.get_default_model()
        provider_id = default_model.split(":", 1)[0] if default_model else ""
        provider_config = model_manager.get_provider(provider_id) or {}
        hyperparams = provider_config.get("hyperparameter_values", {})
        temperature = hyperparams.get("temperature", 1.0)
    except Exception as e:
        logger.warning(f"获取 temperature 失败: {e}")

    main_session = sm.get_main_session()
    main_sessions = sm.get_main_session_summaries()

    return {
        "main_session": main_session.get_summary() if main_session else None,
        "main_sessions": main_sessions,
        "active_sub_count": sm.get_active_sub_count(),
        "total_sessions": sm.get_total_session_count(),
        "prompt_version": prompt_data.get("version", 0),
        "prompt_last_modified": prompt_data.get("last_modified", ""),
        "temperature": temperature,
        "mcp_connected": len(mcp.connections) > 0,
        "mcp_servers": list(mcp.connections.keys()),
        "mcp_tools_count": len(mcp.get_tools()),
        "event_bus_stats": bus_stats,
        "execution_control": execution_control,
    }





@router.get("/tools")
async def list_tools(request: Request):
    """获取所有已注册工具列表及分组定义"""
    registry = request.app.state.tool_registry
    all_tools = registry.get_tools()
    return {
        "tools": all_tools,
        "groups": registry.get_groups(),
        "total": len(all_tools),
    }


# ============ Tool Groups CRUD API ============

class CreateToolGroupRequest(BaseModel):
    id: str
    name: str
    description: str = ""


class UpdateToolGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.post("/tools/groups")
async def create_tool_group(body: CreateToolGroupRequest, request: Request):
    """创建新工具分组"""
    registry = request.app.state.tool_registry
    group = registry.add_group(body.id, body.name, body.description)
    if not group:
        raise HTTPException(status_code=400, detail=f"工具分组 '{body.id}' 已存在或创建失败")
    return {"success": True, "group": group}


@router.put("/tools/groups/{group_id}")
async def update_tool_group(group_id: str, body: UpdateToolGroupRequest, request: Request):
    """更新工具分组名称/描述"""
    registry = request.app.state.tool_registry
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    success = registry.update_group(group_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail=f"工具分组 '{group_id}' 不存在")
    return {"success": True}


@router.delete("/tools/groups/{group_id}")
async def delete_tool_group(group_id: str, request: Request):
    """删除工具分组（需保证组内无工具）"""
    registry = request.app.state.tool_registry
    success, msg = registry.delete_group(group_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": f"工具分组 '{group_id}' 已删除"}





@router.get("/agent-definitions")

async def list_agent_definitions():

    """获取所有已注册的 Agent 类型定义"""

    from src.agent.definition import list_agent_types

    return {"agent_types": list_agent_types()}


@router.get("/agent-types")
async def get_agent_types():
    """获取所有 Agent 类型列表（用于下拉选择），含 available_for_sub_session 过滤字段"""
    from src.agent.definition import list_agent_types
    types = list_agent_types()
    return {
        "agent_types": [
            {
                "agent_type": t["agent_type"],
                "description": t["description"],
                "available_for_sub_session": t.get("available_for_sub_session", True),
            }
            for t in types
        ]
    }


class UpdateAgentVisibilityRequest(BaseModel):
    visible_skill_group_ids: list[str] | None = None
    visible_rule_group_ids: list[str] | None = None


@router.put("/agent-definitions/{agent_type}/visibility")
async def update_agent_visibility(agent_type: str, body: UpdateAgentVisibilityRequest, request: Request):
    """更新 Agent 的可见性配置（可见的 skill 组和 rule 组）"""
    from src.agent.config_manager import AgentConfigManager
    acm: AgentConfigManager | None = getattr(request.app.state, "agent_config_manager", None)
    if not acm:
        raise HTTPException(status_code=500, detail="Agent 配置管理器未初始化")

    updates = {}
    if body.visible_skill_group_ids is not None:
        updates["visible_skill_group_ids"] = body.visible_skill_group_ids
    if body.visible_rule_group_ids is not None:
        updates["visible_rule_group_ids"] = body.visible_rule_group_ids

    if not updates:
        raise HTTPException(status_code=400, detail="未提供更新字段")

    success = acm.update_agent(agent_type, updates)
    if not success:
        raise HTTPException(status_code=500, detail="更新可见性配置失败")

    return {"success": True, "agent_type": agent_type, **updates}





@router.get("/graph/structure")

async def get_graph_structure(request: Request):

    """获取 LangGraph 图结构定义（统一 llm ↔ tools 双节点图）"""

    graph = {

        "name": "Agent Graph",

        "description": "所有 Agent 共用的 graph 结构，llm ↔ tools 双节点循环",

        "nodes": [

            {"id": "llm", "label": "LLM", "type": "llm", "description": "调用 LLM 获取响应，绑定所有工具"},

            {"id": "tools", "label": "Tools", "type": "tool", "description": "ToolNode 自动执行所有 tool_calls"},

        ],

        "edges": [

            {"source": "__start__", "target": "llm", "label": "入口"},

            {"source": "llm", "target": "tools", "label": "has tool_calls & remaining > 0", "conditional": True},

            {"source": "llm", "target": "__end__", "label": "no tool_calls / no remaining", "conditional": True},

            {"source": "tools", "target": "llm", "label": "工具执行完成"},

        ],

    }

    return {"graph": graph}





@router.get("/events/recent")

async def get_recent_events(limit: int = 50, event_type: str | None = None):

    """获取最近的事件日志"""

    events = event_bus.get_recent_events(limit=limit, event_type=event_type)

    return {"events": events, "total": len(events)}





# ============ 编排预览 API ============



class OrchestrationPreviewRequest(BaseModel):

    section_names: list[str] = Field(description="按顺序排列的 section 名称列表")

    custom_sections: list[dict] = Field(default_factory=list, description="自定义 section [{name, content}]")





@router.post("/orchestration/preview")

async def orchestration_preview(body: OrchestrationPreviewRequest, request: Request):

    """

    根据前端编排的 section 顺序，组装 effective prompt 并返回预览。



    接收 section 名称列表（顺序即最终 prompt 顺序），

    返回组装后的完整 prompt 文本和 token 统计。

    """

    pm = _get_prompt_manager(request)

    orchestrator = getattr(request.app.state, "prompt_orchestrator", None)

    if orchestrator is None:

        from src.prompts import create_orchestrator

        orchestrator = create_orchestrator(pm)



    # 获取所有默认 section 的完整信息

    all_sections = orchestrator.dump_sections()

    section_map = {s["name"]: s for s in all_sections}



    # 按前端指定的顺序组装

    ordered_parts = []

    section_details = []



    for name in body.section_names:

        if name in section_map:

            sec = section_map[name]

            ordered_parts.append(sec["content"])

            section_details.append({

                "name": sec["name"],

                "token_estimate": sec["token_estimate"],

                "cache_break": sec["cache_break"],

            })



    # 追加自定义 section

    for cs in body.custom_sections:

        content = cs.get("content", "")

        if content.strip():

            from src.core.utils import estimate_tokens

            ordered_parts.append(content)

            section_details.append({

                "name": cs.get("name", "custom"),

                "token_estimate": estimate_tokens(content),

                "cache_break": False,

            })



    effective_prompt = "\n\n".join(ordered_parts)

    from src.core.utils import estimate_tokens

    total_tokens = estimate_tokens(effective_prompt)



    return {

        "effective_prompt": effective_prompt,

        "total_tokens": total_tokens,

        "sections": section_details,

        "sections_count": len(section_details),

    }





# ============ 配置管理 API ============



class UpdateConfigRequest(BaseModel):

    updates: dict[str, Any] = Field(description="要更新的配置项 {key: value}")

    persist: bool = Field(default=True, description="是否持久化到 .env 文件")





@router.get("/config")

async def get_config():

    """获取所有运行时配置项（敏感字段脱敏）"""

    from src.config import get_all_config, CONFIG_ITEMS

    return {

        "config": get_all_config(),

        "meta": CONFIG_ITEMS,

    }





@router.put("/config")

async def update_config_api(body: UpdateConfigRequest, request: Request):

    """批量更新运行时配置项，可选持久化到 .env"""

    from src.config import update_config

    workspace_base = None
    if "CODING_WORKSPACE_BASE" in body.updates:
        from src.core.workspace_manager import resolve_workspace_base_path

        try:
            workspace_base = resolve_workspace_base_path(
                str(body.updates["CODING_WORKSPACE_BASE"])
            )
            workspace_base.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Workspace 存储根目录不可用: {exc}",
            ) from exc



    result = update_config(body.updates, persist=body.persist)

    if workspace_base is not None:
        managers = [getattr(request.app.state, "workspace_manager", None)]
        workflow_manager = getattr(request.app.state, "workflow_manager", None)
        managers.append(getattr(workflow_manager, "_ws_manager", None))
        refreshed: set[int] = set()
        for manager in managers:
            if manager is None or id(manager) in refreshed:
                continue
            manager.set_base_dir(workspace_base)
            refreshed.add(id(manager))



    # 如果更新了 TEMPERATURE，同步到当前 LLM 实例

    if "TEMPERATURE" in body.updates:

        llm = getattr(request.app.state, "llm", None)

        if llm is not None:

            import src.config as cfg

            llm.temperature = cfg.TEMPERATURE

            logger.info(f"LLM 温度已同步更新为: {cfg.TEMPERATURE}")



    return {"success": True, "config": result}





# ============ 审批 API ============



@router.get("/approvals/pending")

async def get_pending_approvals(request: Request):

    """获取所有待审批请求"""

    approval_mgr = getattr(request.app.state, "approval_manager", None)

    if not approval_mgr:

        return {"approvals": [], "total": 0}

    pending = approval_mgr.get_pending()

    return {"approvals": pending, "total": len(pending)}





class ApprovalRejectRequest(BaseModel):

    reason: str = ""





@router.post("/approvals/{request_id}/approve")

async def approve_request(request_id: str, request: Request):

    """批准审批请求"""

    approval_mgr = getattr(request.app.state, "approval_manager", None)

    if not approval_mgr:

        raise HTTPException(status_code=500, detail="审批管理器未初始化")

    success = approval_mgr.approve(request_id)

    if not success:

        raise HTTPException(status_code=404, detail=f"未找到待审批请求: {request_id}")

    return {"success": True, "message": f"请求 {request_id} 已批准"}





@router.post("/approvals/{request_id}/reject")

async def reject_request(request_id: str, body: ApprovalRejectRequest, request: Request):

    """拒绝审批请求"""

    approval_mgr = getattr(request.app.state, "approval_manager", None)

    if not approval_mgr:

        raise HTTPException(status_code=500, detail="审批管理器未初始化")

    success = approval_mgr.reject(request_id, reason=body.reason)

    if not success:

        raise HTTPException(status_code=404, detail=f"未找到待审批请求: {request_id}")

    return {"success": True, "message": f"请求 {request_id} 已拒绝"}


# ============ Skills API ============

@router.get("/skills")
async def list_skills(request: Request, agent_type: str | None = None, category: str | None = None):
    """获取所有 skills 或按条件筛选"""
    sm = _get_skill_manager(request)
    if not sm:
        return {"skills": [], "total": 0}

    if category:
        from src.skills.models import SkillCategory
        try:
            cat = SkillCategory(category)
            skills = sm.list_by_category(cat, enabled_only=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的分类: {category}")
    elif agent_type:
        skills = sm.list_by_agent_type(agent_type, enabled_only=False)
    else:
        skills = sm.list_all(enabled_only=False)

    return {
        "skills": [s.to_dict() for s in skills],
        "total": len(skills),
    }


@router.get("/skills/summary")
async def get_skills_summary(request: Request, agent_type: str | None = None):
    """获取 skills 摘要信息（不含完整内容）"""
    sm = _get_skill_manager(request)
    if not sm:
        return {"skills": [], "total": 0}

    summary = sm.get_skills_summary(agent_type=agent_type)
    return {"skills": summary, "total": len(summary)}


@router.get("/skills/stats")
async def get_skills_stats(request: Request):
    """获取 skills 统计信息"""
    sm = _get_skill_manager(request)
    if not sm:
        return {"total": 0, "enabled": 0, "disabled": 0, "by_category": {}}

    return sm.get_stats()


# ============ Skills Groups API ============

class CreateSkillGroupRequest(BaseModel):
    id: str
    name: str
    description: str = ""


class UpdateSkillGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("/skills/groups")
async def list_skill_groups(request: Request):
    """获取所有技能组"""
    sm = _get_skill_manager(request)
    if not sm or not sm.config_manager:
        return {"groups": []}
    return {"groups": sm.config_manager.get_groups()}


@router.get("/skills/groups/{group_id}/skills")
async def get_skills_in_group(group_id: str, request: Request):
    """获取组内的技能列表"""
    sm = _get_skill_manager(request)
    if not sm or not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    skill_ids = sm.config_manager.get_skills_in_group(group_id)
    return {"skill_ids": skill_ids}


@router.post("/skills/groups")
async def create_skill_group(body: CreateSkillGroupRequest, request: Request):
    """创建新技能组"""
    sm = _get_skill_manager(request)
    if not sm or not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    group = sm.config_manager.create_group(body.model_dump())
    if not group:
        raise HTTPException(status_code=400, detail=f"技能组 {body.id} 已存在或创建失败")

    return {"success": True, "group": group}


@router.put("/skills/groups/{group_id}")
async def update_skill_group(group_id: str, body: UpdateSkillGroupRequest, request: Request):
    """更新技能组"""
    sm = _get_skill_manager(request)
    if not sm or not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    success = sm.config_manager.update_group(group_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail=f"技能组 {group_id} 不存在")

    return {"success": True}


@router.delete("/skills/groups/{group_id}")
async def delete_skill_group(group_id: str, request: Request):
    """删除技能组"""
    sm = _get_skill_manager(request)
    if not sm or not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    success = sm.config_manager.delete_group(group_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"技能组 {group_id} 不存在")

    return {"success": True, "message": f"技能组 {group_id} 已删除"}


class SetSkillGroupsRequest(BaseModel):
    group_ids: list[str]


@router.put("/skills/{skill_id}/groups")
async def set_skill_groups(skill_id: str, body: SetSkillGroupsRequest, request: Request):
    """设置 skill 所属的组列表"""
    sm = _get_skill_manager(request)
    if not sm or not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    success = sm.config_manager.set_skill_group_ids(skill_id, body.group_ids)
    if not success:
        raise HTTPException(status_code=500, detail="设置失败")

    return {"success": True, "skill_id": skill_id, "group_ids": body.group_ids}


@router.get("/skills/{skill_id}")
async def get_skill_detail(skill_id: str, request: Request):
    """获取指定 skill 的详细信息"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=404, detail="Skill 管理器未初始化")

    skill = sm.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"未找到 skill: {skill_id}")

    skill_dict = skill.to_dict()

    # 添加配置信息
    if sm.config_manager:
        skill_dict["auto_inject"] = sm.config_manager.should_auto_inject(skill_id)
        skill_dict["group_ids"] = sm.config_manager.get_skill_group_ids(skill_id)
        skill_dict["config"] = sm.config_manager.get_config(skill_id)
        # config 中的 workflow_only 覆盖 frontmatter 默认值
        wf_only = sm.config_manager.get_workflow_only(skill_id)
        if wf_only is not None:
            skill_dict["workflow_only"] = wf_only
    else:
        skill_dict["auto_inject"] = False
        skill_dict["group_ids"] = []
        skill_dict["config"] = {}

    return skill_dict


class CreateSkillRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    content: str = ""
    category: str = "general"
    agent_types: list[str] = Field(default_factory=list)
    priority: int = 50
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    version: str = "1.0.0"
    author: str = ""


@router.post("/skills")
async def create_skill(body: CreateSkillRequest, request: Request):
    """创建新 skill"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    # 检查 ID 是否已存在
    if sm.get_skill(body.id):
        raise HTTPException(status_code=400, detail=f"Skill ID 已存在: {body.id}")

    skill = sm.create_skill(body.model_dump())
    return {"success": True, "skill": skill.to_dict()}


@router.post("/skills/import")
async def import_skills(request: Request, filename: str = ""):
    """从外部打包导入 skill（Agent Skills 开放标准）。

    body 为文件原始字节；filename 扩展名决定解压方式：
    .zip / .tar.gz / .tgz 解压扫描，其他按单个 SKILL.md 文本处理。
    返回 imported / skipped / conflicts / errors 四类结果。
    """
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="请求体为空")

    result = sm.import_skill_archive(raw, filename=filename)
    return {"success": True, **result}


class UpdateSkillRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    category: str | None = None
    agent_types: list[str] | None = None
    priority: int | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    version: str | None = None
    author: str | None = None


@router.put("/skills/{skill_id}")
async def update_skill(skill_id: str, body: UpdateSkillRequest, request: Request):
    """更新 skill"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    skill = sm.update_skill(skill_id, updates)

    if not skill:
        raise HTTPException(status_code=404, detail=f"未找到 skill: {skill_id}")

    return {"success": True, "skill": skill.to_dict()}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, request: Request):
    """删除 skill"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    success = sm.delete_skill(skill_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到 skill: {skill_id}")

    return {"success": True, "message": f"Skill {skill_id} 已删除"}


@router.post("/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, enabled: bool, request: Request):
    """启用/禁用 skill"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    success = sm.toggle_skill(skill_id, enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到 skill: {skill_id}")

    return {"success": True, "message": f"Skill {skill_id} 已{'启用' if enabled else '禁用'}"}


@router.post("/skills/{skill_id}/workflow-only")
async def toggle_skill_workflow_only(skill_id: str, enabled: bool, request: Request):
    """切换 skill 的工作流专属/通用模式"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    if not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 配置管理器未初始化")

    skill = sm.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"未找到 skill: {skill_id}")

    success = sm.config_manager.set_workflow_only(skill_id, enabled)
    if not success:
        raise HTTPException(status_code=500, detail="设置 workflow_only 失败")

    return {"success": True, "skill_id": skill_id, "workflow_only": enabled}


@router.post("/skills/reload")
async def reload_skills(request: Request):
    """重新加载所有 skills"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    sm.reload()
    stats = sm.get_stats()
    return {"success": True, "message": "Skills 已重新加载", "stats": stats}


# ============ Workspace API ============



@router.get("/workspace/{session_id}/tree")

async def get_workspace_tree(session_id: str, request: Request):

    """获取 session 的 workspace 文件树"""

    sm = _get_session_manager(request)

    session = sm.get_session(session_id)

    if not session:

        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")

    if not session.workspace_path:

        raise HTTPException(status_code=400, detail=f"会话 {session_id} 没有 workspace")



    import os

    workspace = session.workspace_path

    skip_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'dist', 'build'}

    entries = []

    max_entries = 500



    try:

        for root, dirs, files in os.walk(workspace):

            dirs[:] = [d for d in sorted(dirs) if d not in skip_dirs]

            rel_root = os.path.relpath(root, workspace)

            if rel_root == ".":

                rel_root = ""

            for d in dirs:

                rel_path = os.path.join(rel_root, d) if rel_root else d

                entries.append({"path": rel_path, "type": "directory"})

                if len(entries) >= max_entries:

                    break

            for f in sorted(files):

                rel_path = os.path.join(rel_root, f) if rel_root else f

                entries.append({"path": rel_path, "type": "file"})

                if len(entries) >= max_entries:

                    break

            if len(entries) >= max_entries:

                break



        return {"workspace": workspace, "entries": entries, "total": len(entries)}

    except Exception as e:
        logger.exception("获取 workspace 文件列表失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")





@router.get("/workspace/{session_id}/file")

async def get_workspace_file(session_id: str, path: str, request: Request):

    """读取 workspace 中指定文件内容"""

    sm = _get_session_manager(request)

    session = sm.get_session(session_id)

    if not session:

        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")

    if not session.workspace_path:

        raise HTTPException(status_code=400, detail=f"会话 {session_id} 没有 workspace")



    from src.core.workspace_guard import WorkspaceGuard

    guard = WorkspaceGuard()

    result = guard.validate_path(session.workspace_path, path)

    if not result.allowed:

        raise HTTPException(status_code=403, detail=result.reason)



    abs_path = guard.get_effective_path(session.workspace_path, path)

    if not abs_path or not abs_path.exists():

        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    if not abs_path.is_file():

        raise HTTPException(status_code=400, detail=f"不是文件: {path}")



    import src.config as cfg

    if abs_path.stat().st_size > cfg.CODING_MAX_FILE_SIZE:

        raise HTTPException(status_code=413, detail="文件过大")



    try:

        content = abs_path.read_text(encoding="utf-8", errors="replace")

        return {"path": path, "content": content, "size": len(content)}

    except Exception as e:
        logger.exception("读取 workspace 文件失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")



class AssignSkillRequest(BaseModel):
    agent_types: list[str]
    priority: int | None = None


@router.post("/skills/{skill_id}/assign")
async def assign_skill(skill_id: str, body: AssignSkillRequest, request: Request):
    """分配 skill 到 agent 类型"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    if not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 配置管理器未初始化")

    # 检查 skill 是否存在
    skill = sm.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    # 设置 agent_types
    success = sm.config_manager.set_agent_types(skill_id, body.agent_types)
    if not success:
        raise HTTPException(status_code=500, detail="设置 agent_types 失败")

    # 设置 priority（如果提供）
    if body.priority is not None:
        success = sm.config_manager.set_priority(skill_id, body.priority)
        if not success:
            raise HTTPException(status_code=500, detail="设置 priority 失败")

    return {"success": True, "skill_id": skill_id, "agent_types": body.agent_types, "priority": body.priority}


@router.post("/skills/{skill_id}/auto-inject")
async def set_auto_inject(skill_id: str, request: Request, enabled: bool = True):
    """设置 skill 的自动注入配置"""
    sm = _get_skill_manager(request)
    if not sm:
        raise HTTPException(status_code=500, detail="Skill 管理器未初始化")

    if not sm.config_manager:
        raise HTTPException(status_code=500, detail="Skill 配置管理器未初始化")

    # 检查 skill 是否存在
    skill = sm.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    success = sm.config_manager.set_auto_inject(skill_id, enabled)
    if not success:
        raise HTTPException(status_code=500, detail="设置失败")

    return {"success": True, "skill_id": skill_id, "auto_inject": enabled}


# ============ Rules API ============

def _get_rule_manager(request: Request):
    return getattr(request.app.state, "rule_manager", None)


@router.get("/rules/summary")
async def get_rules_summary(request: Request, agent_type: str | None = None):
    """获取 rules 摘要列表"""
    rm = _get_rule_manager(request)
    if not rm:
        return {"rules": []}

    summary = rm.get_rules_summary(agent_type)
    return {"rules": summary}


@router.get("/rules/stats")
async def get_rules_stats(request: Request):
    """获取 rules 统计信息"""
    rm = _get_rule_manager(request)
    if not rm:
        return {"total": 0}

    return rm.get_stats()


# ============ Rules Groups API ============


class CreateRuleGroupRequest(BaseModel):
    id: str
    name: str
    description: str = ""


class UpdateRuleGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("/rules/groups")
async def list_rule_groups(request: Request):
    """获取所有规则组"""
    rm = _get_rule_manager(request)
    if not rm or not rm.config_manager:
        return {"groups": []}
    return {"groups": rm.config_manager.get_groups()}


@router.get("/rules/groups/{group_id}/rules")
async def get_rules_in_group(group_id: str, request: Request):
    """获取组内的规则列表"""
    rm = _get_rule_manager(request)
    if not rm or not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    rule_ids = rm.config_manager.get_rules_in_group(group_id)
    return {"rule_ids": rule_ids}


@router.post("/rules/groups")
async def create_rule_group(body: CreateRuleGroupRequest, request: Request):
    """创建新规则组"""
    rm = _get_rule_manager(request)
    if not rm or not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    group = rm.config_manager.create_group(body.model_dump())
    if not group:
        raise HTTPException(status_code=400, detail=f"规则组 {body.id} 已存在或创建失败")

    return {"success": True, "group": group}


@router.put("/rules/groups/{group_id}")
async def update_rule_group(group_id: str, body: UpdateRuleGroupRequest, request: Request):
    """更新规则组"""
    rm = _get_rule_manager(request)
    if not rm or not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    success = rm.config_manager.update_group(group_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail=f"规则组 {group_id} 不存在")

    return {"success": True}


@router.delete("/rules/groups/{group_id}")
async def delete_rule_group(group_id: str, request: Request):
    """删除规则组"""
    rm = _get_rule_manager(request)
    if not rm or not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    success = rm.config_manager.delete_group(group_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"规则组 {group_id} 不存在")

    return {"success": True, "message": f"规则组 {group_id} 已删除"}


class SetRuleGroupsRequest(BaseModel):
    group_ids: list[str]


@router.put("/rules/{rule_id}/groups")
async def set_rule_groups(rule_id: str, body: SetRuleGroupsRequest, request: Request):
    """设置 rule 所属的组列表"""
    rm = _get_rule_manager(request)
    if not rm or not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    success = rm.config_manager.set_rule_group_ids(rule_id, body.group_ids)
    if not success:
        raise HTTPException(status_code=500, detail="设置失败")

    return {"success": True, "rule_id": rule_id, "group_ids": body.group_ids}


@router.get("/rules/{rule_id}")
async def get_rule_detail(rule_id: str, request: Request):
    """获取指定 rule 的详细信息"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=404, detail="Rule 管理器未初始化")

    rule = rm.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"未找到 rule: {rule_id}")

    rule_dict = rule.to_dict()

    # 所有配置字段统一从 config 读取
    if rm.config_manager:
        rule_configs = rm.config_manager.get_rule_configs()
        rule_config = rule_configs.get(rule_id, {})
        rule_dict["enabled"] = rule_config.get("enabled", True)
        rule_dict["workflow_only"] = rule_config.get("workflow_only", False)
        rule_dict["agent_types"] = rm.config_manager.get_agent_types(rule_id)
        rule_dict["group_ids"] = rm.config_manager.get_rule_group_ids(rule_id)
        rule_dict["config"] = rm.config_manager.get_config(rule_id)
    else:
        rule_dict["enabled"] = True
        rule_dict["workflow_only"] = False
        rule_dict["agent_types"] = []
        rule_dict["group_ids"] = []
        rule_dict["config"] = {}

    return rule_dict


class AssignRuleRequest(BaseModel):
    agent_types: list[str]


@router.post("/rules/{rule_id}/assign")
async def assign_rule(rule_id: str, body: AssignRuleRequest, request: Request):
    """分配 rule 到 agent 类型"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    if not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 配置管理器未初始化")

    # 检查 rule 是否存在
    rule = rm.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} 不存在")

    # 设置 agent_types
    success = rm.config_manager.set_agent_types(rule_id, body.agent_types)
    if not success:
        raise HTTPException(status_code=500, detail="设置 agent_types 失败")

    return {"success": True, "rule_id": rule_id, "agent_types": body.agent_types}


@router.post("/rules/reload")
async def reload_rules(request: Request):
    """重新加载所有 rules"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    rm.reload()
    stats = rm.get_stats()
    return {"success": True, "message": "Rules 已重新加载", "stats": stats}


class CreateRuleRequest(BaseModel):
    id: str
    name: str
    description: str
    content: str
    version: str = "1.0"
    author: str = ""


@router.post("/rules")
async def create_rule(body: CreateRuleRequest, request: Request):
    """创建新 rule"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    # 检查 ID 是否已存在
    if rm.get_rule(body.id):
        raise HTTPException(status_code=400, detail=f"Rule ID 已存在: {body.id}")

    rule = rm.create_rule(body.model_dump())
    return {"success": True, "rule": rule.to_dict()}


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    version: str | None = None
    author: str | None = None


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, body: UpdateRuleRequest, request: Request):
    """更新 rule"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}

    try:
        rule = rm.update_rule(rule_id, updates)
        return {"success": True, "rule": rule.to_dict()}
    except Exception as e:
        logger.exception("更新规则失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request):
    """删除 rule"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    success = rm.delete_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到 rule: {rule_id}")

    return {"success": True, "message": f"Rule {rule_id} 已删除"}


@router.post("/rules/{rule_id}/workflow-only")
async def toggle_rule_workflow_only(rule_id: str, enabled: bool, request: Request):
    """切换 rule 的工作流专属/通用模式"""
    rm = _get_rule_manager(request)
    if not rm:
        raise HTTPException(status_code=500, detail="Rule 管理器未初始化")

    if not rm.config_manager:
        raise HTTPException(status_code=500, detail="Rule 配置管理器未初始化")

    rule = rm.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"未找到 rule: {rule_id}")

    success = rm.config_manager.set_workflow_only(rule_id, enabled)
    if not success:
        raise HTTPException(status_code=500, detail="设置 workflow_only 失败")

    return {"success": True, "rule_id": rule_id, "workflow_only": enabled}


# ============ Sections 配置 API ============

class UpdateSectionRequest(BaseModel):
    content: str | None = None
    enabled: bool | None = None
    order: int | None = None
    cache_break: bool | None = None
    cache_break_reason: str | None = None
    workflow_only: bool | None = None
    reason: str | None = None


class UpdateSectionsRequest(BaseModel):
    sections: list[dict] = Field(description="完整的 sections 列表")
    reason: str | None = None


class AddSectionRequest(BaseModel):
    name: str
    content: str = ""
    enabled: bool = True
    order: int | None = None
    cache_break: bool = False
    cache_break_reason: str = ""


def _get_prompt_manager_for_sections(request: Request, prompt_type: str = "main"):
    """获取统一 PromptManager 实例，各端点自行传递 prompt_type 给方法。"""
    return _get_prompt_manager(request)


def _refresh_main_session_prompt(request: Request) -> None:
    """刷新主会话的 system_prompt（orchestrator 或 main_session 不存在时静默跳过）"""
    orchestrator = getattr(request.app.state, "prompt_orchestrator", None)
    session_mgr = _get_session_manager(request)
    main_session = session_mgr.get_main_session()
    if not (main_session and orchestrator):
        return
    new_system_prompt = orchestrator.build_effective_prompt(
        agent_type="main",
        skills_mode="auto_inject",
        include_rules=True,
        tools=getattr(request.app.state, "all_tools", None),
    )
    from src.prompts.placeholders import build_session_meta_text, render_prompt_template
    new_system_prompt = render_prompt_template(new_system_prompt, {
        "session_meta": build_session_meta_text(main_session),
    })
    main_session.refresh_system_prompt(new_system_prompt)


@router.get("/prompt-sections/config")
async def get_sections_config(request: Request):
    """获取 sections 配置"""
    prompt_type = request.query_params.get("prompt_type", "main")
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    config = pm.get_config(agent_type=prompt_type)
    template_variables = pm.get_template_variables(prompt_type)

    return {
        "config": config,
        "has_config": True,
        "template_variables": template_variables,
    }


class UpdateTemplateVariablesRequest(BaseModel):
    template_variables: list[dict]


@router.put("/prompt-sections/template-variables")
async def update_template_variables(body: UpdateTemplateVariablesRequest, request: Request, prompt_type: str = "main"):
    """更新模板的自定义变量块声明。"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    success = pm.update_template_variables(body.template_variables, prompt_type)
    if not success:
        raise HTTPException(status_code=400, detail="更新 template_variables 失败")

    return {"success": True, "template_variables": body.template_variables}


@router.put("/prompt-sections/{section_name}")
async def update_section(section_name: str, body: UpdateSectionRequest, request: Request, prompt_type: str = "main"):
    """更新单个 section"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    # 提取 reason 参数
    reason = body.reason or ""

    # 从 body 中移除 reason，只保留实际的 section 字段
    updates = {k: v for k, v in body.model_dump().items() if v is not None and k != "reason"}

    success = pm.update_section(section_name, updates, reason=reason, agent_type=prompt_type)

    if not success:
        raise HTTPException(status_code=404, detail=f"Section {section_name} 不存在")

    # 仅在更新主 agent sections 时刷新主会话 system_prompt
    if prompt_type == "main":
        _refresh_main_session_prompt(request)

    return {"success": True, "message": f"Section {section_name} 已更新"}


@router.put("/prompt-sections")
async def update_sections(body: UpdateSectionsRequest, request: Request, prompt_type: str = "main"):
    """批量更新 sections"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    reason = body.reason or ""
    success = pm.update_sections(body.sections, reason=reason, agent_type=prompt_type)

    if not success:
        raise HTTPException(status_code=400, detail="更新 sections 失败")

    if prompt_type == "main":
        _refresh_main_session_prompt(request)

    return {"success": True, "message": f"已更新 {len(body.sections)} 个 sections"}


@router.post("/prompt-sections")
async def add_section(body: AddSectionRequest, request: Request, prompt_type: str = "main"):
    """添加新 section"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    section = body.model_dump()
    success = pm.add_section(section, agent_type=prompt_type)

    if not success:
        raise HTTPException(status_code=400, detail=f"添加 section 失败（可能已存在）")

    # 仅在更新主 agent sections 时刷新主会话 system_prompt
    if prompt_type == "main":
        _refresh_main_session_prompt(request)

    return {"success": True, "message": f"已添加 section {body.name}"}


@router.delete("/prompt-sections/{section_name}")
async def delete_section(section_name: str, request: Request, prompt_type: str = "main"):
    """删除 section"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    success = pm.delete_section(section_name, agent_type=prompt_type)

    if not success:
        raise HTTPException(status_code=404, detail=f"Section {section_name} 不存在")

    # 仅在更新主 agent sections 时刷新主会话 system_prompt
    if prompt_type == "main":
        _refresh_main_session_prompt(request)

    return {"success": True, "message": f"已删除 section {section_name}"}


@router.post("/prompt-sections/reload")
async def reload_sections(request: Request, prompt_type: str = "main"):
    """重新加载 sections 配置"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    pm.reload()

    # 仅在更新主 agent sections 时刷新主会话 system_prompt
    if prompt_type == "main":
        _refresh_main_session_prompt(request)

    return {"success": True, "message": "Sections 配置已重新加载"}


@router.post("/prompt-sections/{section_name}/rename")
async def rename_section(section_name: str, request: Request, new_name: str = Body(..., embed=True), prompt_type: str = "main"):
    """重命名 section"""
    pm = _get_prompt_manager_for_sections(request, prompt_type)
    if not pm:
        raise HTTPException(status_code=500, detail="Prompt 管理器未初始化")

    success = pm.rename_section(section_name, new_name, agent_type=prompt_type)

    if not success:
        raise HTTPException(status_code=400, detail=f"重命名失败：section {section_name} 不存在或新名称 {new_name} 已存在")

    # 仅在更新主 agent sections 时刷新主会话 system_prompt
    if prompt_type == "main":
        _refresh_main_session_prompt(request)

    return {"success": True, "message": f"已将 section {section_name} 重命名为 {new_name}"}


# ============ Agent 定义配置 API ============

class UpdateAgentRequest(BaseModel):
    description: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    system_prompt_template: str | None = None
    copy_main_workspace: bool | None = None
    extension_options: dict[str, dict] | None = None
    visible_skill_group_ids: list[str] | None = None
    visible_rule_group_ids: list[str] | None = None
    model_params: dict | None = None
    prompt_template: str | None = None


class AddAgentRequest(BaseModel):
    agent_type: str
    description: str = ""
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int = 10
    system_prompt_template: str = ""
    copy_main_workspace: bool | None = None
    extension_options: dict[str, dict] | None = None
    model_params: dict | None = None


def _get_agent_config_manager(request: Request):
    return getattr(request.app.state, "agent_config_manager", None)


@router.get("/agent-definitions/config")
async def get_agent_definitions_config(request: Request):
    """获取 agent 定义配置"""
    acm = _get_agent_config_manager(request)
    if not acm:
        raise HTTPException(status_code=500, detail="Agent 配置管理器未初始化")

    return {
        "config": acm.get_config(),
        "has_config": acm.has_config(),
    }


@router.put("/agent-definitions/{agent_type}")
async def update_agent_definition(agent_type: str, body: UpdateAgentRequest, request: Request):
    """更新 agent 定义"""
    acm = _get_agent_config_manager(request)
    if not acm:
        raise HTTPException(status_code=500, detail="Agent 配置管理器未初始化")

    # 使用 exclude_unset=True 只获取前端实际发送的字段
    # 这样可以正确处理 copy_main_workspace=null 的情况
    updates = body.model_dump(exclude_unset=True)
    success = acm.update_agent(agent_type, updates)

    if not success:
        raise HTTPException(status_code=400, detail=f"更新 agent {agent_type} 失败")

    return {"success": True, "message": f"Agent {agent_type} 已更新"}


@router.post("/agent-definitions")
async def add_agent_definition(body: AddAgentRequest, request: Request):
    """添加新 agent 定义"""
    acm = _get_agent_config_manager(request)
    if not acm:
        raise HTTPException(status_code=500, detail="Agent 配置管理器未初始化")

    definition = body.model_dump()
    agent_type = definition.pop("agent_type")
    success = acm.add_agent(agent_type, definition)

    if not success:
        raise HTTPException(status_code=400, detail=f"添加 agent 失败（可能已存在）")

    return {"success": True, "message": f"已添加 agent {agent_type}"}


@router.delete("/agent-definitions/{agent_type}")
async def delete_agent_definition(agent_type: str, request: Request):
    """删除 agent 定义（仅删除配置，内置定义不受影响）"""
    acm = _get_agent_config_manager(request)
    if not acm:
        raise HTTPException(status_code=500, detail="Agent 配置管理器未初始化")

    success = acm.delete_agent(agent_type)

    if not success:
        raise HTTPException(status_code=404, detail=f"Agent {agent_type} 不存在于配置中")

    return {"success": True, "message": f"已删除 agent {agent_type} 的配置"}


@router.post("/agent-definitions/reload")
async def reload_agent_definitions(request: Request):
    """重新加载 agent 定义配置"""
    acm = _get_agent_config_manager(request)
    if not acm:
        raise HTTPException(status_code=500, detail="Agent 配置管理器未初始化")

    acm.reload()

    return {"success": True, "message": "Agent 定义配置已重新加载"}


# ============ 模型供应商 API ============

class UpdateModelProviderRequest(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] | None = None
    maxContextTokens: int | None = None
    models_config: dict[str, Any] | None = None
    hyperparameter_values: dict[str, Any] | None = None
    error_messages: dict[str, str] | None = None
    managed_by: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )


class AddModelProviderRequest(BaseModel):
    provider_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    provider_type: str | None = None
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    maxContextTokens: int = 128000
    models_config: dict[str, Any] = Field(default_factory=dict)
    hyperparameter_values: dict[str, Any] = Field(default_factory=dict)
    error_messages: dict[str, str] | None = None
    managed_by: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )


class DiscoverProviderModelsRequest(BaseModel):
    provider_id: str
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ProbeProviderModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model: str = Field(min_length=3, max_length=512)


_PROVIDER_OWNER_HEADER = "x-determinflow-provider-owner"


def _active_provider_owner(request: Request, provider: dict[str, Any]) -> str | None:
    owner = str(provider.get("managed_by") or "").strip()
    extension_manager = getattr(request.app.state, "extension_manager", None)
    if not owner or extension_manager is None:
        return None
    try:
        return owner if extension_manager.is_enabled(owner) else None
    except (AttributeError, KeyError, TypeError):
        return None


def _require_provider_write_access(
    request: Request,
    provider: dict[str, Any],
) -> None:
    owner = _active_provider_owner(request, provider)
    if owner and request.headers.get(_PROVIDER_OWNER_HEADER) != owner:
        raise HTTPException(status_code=403, detail="该供应商由已启用的插件管理")


@router.get("/model-providers")
async def get_model_providers(request: Request):
    """获取所有模型供应商配置（api_key 脱敏）"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    providers = model_manager.get_all_providers()

    # 脱敏处理
    masked_providers = {}
    for pid, pconfig in providers.items():
        masked = dict(pconfig)
        resolved = model_manager.get_provider(pid) or {}
        masked["api_key"] = "***" if resolved.get("api_key") else ""
        masked["capabilities"] = model_manager.get_provider_capabilities(pid)
        masked["is_managed"] = _active_provider_owner(request, pconfig) is not None
        masked_providers[pid] = masked

    default_model = model_manager.get_default_model()
    default_provider = default_model.split(":", 1)[0] if default_model else None

    return {
        "providers": masked_providers,
        "default_provider": default_provider,
        "default_model": default_model,
    }


@router.put("/model-providers/{provider_id}")
async def update_model_provider(
    provider_id: str,
    body: UpdateModelProviderRequest,
    request: Request,
):
    """更新模型供应商配置"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    configured = model_manager.get_provider(provider_id)
    if configured is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
    _require_provider_write_access(request, configured)

    try:
        updates = body.model_dump(exclude_unset=True)
        requested_owner = updates.get("managed_by")
        if requested_owner and (
            request.headers.get(_PROVIDER_OWNER_HEADER) != requested_owner
            or _active_provider_owner(
                request,
                {"managed_by": requested_owner},
            ) is None
        ):
            raise HTTPException(
                status_code=403,
                detail="只有已启用的所属插件可以接管供应商",
            )
        # 脱敏值或空输入表示保持现有配置；新值会替换环境变量引用或旧 Key。
        if updates.get("api_key") in {"", "***"}:
            del updates["api_key"]
            logger.info(f"保持现有 api_key 配置 | provider={provider_id}")
        model_manager.update_provider(provider_id, updates)
        return {"success": True, "message": f"供应商 {provider_id} 已更新"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/model-providers")
async def add_model_provider(body: AddModelProviderRequest, request: Request):
    """添加新模型供应商"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    if (
        body.managed_by
        and (
            request.headers.get(_PROVIDER_OWNER_HEADER) != body.managed_by
            or _active_provider_owner(
                request,
                {"managed_by": body.managed_by},
            ) is None
        )
    ):
        raise HTTPException(status_code=403, detail="只有所属插件可以创建托管供应商")

    try:
        provider_config = {
            "provider_type": body.provider_type,
            "name": body.name,
            "base_url": body.base_url,
            "api_key": body.api_key,
            "models": body.models,
            "maxContextTokens": body.maxContextTokens,
            "models_config": body.models_config,
            "hyperparameter_values": body.hyperparameter_values,
            **({"managed_by": body.managed_by} if body.managed_by else {}),
        }
        if body.error_messages is not None:
            provider_config["error_messages"] = body.error_messages
        model_manager.add_provider(body.provider_id, provider_config)
        return {"success": True, "message": f"供应商 {body.provider_id} 已添加"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/model-providers/models/discover")
async def discover_provider_models(body: DiscoverProviderModelsRequest):
    """按 Provider 声明的 API 格式从 /models 拉取模型列表。"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    configured = model_manager.get_provider(body.provider_id) or {}
    try:
        provider_type = model_manager.resolve_provider_type(
            body.provider_id,
            body.provider_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    schema = model_manager.get_provider_schema(provider_type) or {}
    base_url = (
        body.base_url
        or configured.get("base_url")
        or schema.get("default_base_url")
        or ""
    ).strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="API 地址必须是有效的 http(s) URL")

    submitted_key = (body.api_key or "").strip()
    api_key = (
        configured.get("api_key", "")
        if submitted_key in {"", "***"}
        else submitted_key
    )
    api_format = schema.get("api_format", "openai")
    if api_format == "anthropic":
        headers = {
            "anthropic-version": "2023-06-01",
            **({"x-api-key": api_key} if api_key else {}),
        }
    else:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    models_url = f"{base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(models_url, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接供应商模型接口: {exc.__class__.__name__}",
        ) from exc

    if response.status_code in {401, 403}:
        raise HTTPException(status_code=400, detail="API Key 无效或无权读取模型列表")
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"供应商模型接口返回 HTTP {response.status_code}",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="供应商模型接口未返回 JSON") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="供应商模型接口缺少 data 列表")
    models = list(dict.fromkeys(
        str(item.get("id", "")).strip()
        for item in data
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ))
    return {"models": models}


@router.post("/model-providers/models/probe")
async def probe_provider_model(body: ProbeProviderModelRequest):
    """使用 Core 已保存的 Provider 配置发起一次最小真实推理。"""
    from src.core.model_probe import ModelProbeError, probe_configured_model

    try:
        return await probe_configured_model(body.model)
    except ModelProbeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.delete("/model-providers/{provider_id}")
async def delete_model_provider(provider_id: str, request: Request):
    """删除模型供应商"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    configured = model_manager.get_provider(provider_id)
    if configured is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
    _require_provider_write_access(request, configured)
    model_manager.delete_provider(provider_id)
    return {"success": True, "message": f"供应商 {provider_id} 已删除"}


@router.get("/model-providers/schemas")
async def get_provider_schemas():
    """获取供应商超参数 schema（供前端渲染表单）"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    return {"schemas": model_manager.get_all_schemas()}


@router.put("/model-providers/{provider_id}/priority")
async def prioritize_model_provider(provider_id: str, request: Request):
    """将供应商移到首位，作为 Main 自动模型来源。"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    configured = model_manager.get_provider(provider_id)
    if configured is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
    _require_provider_write_access(request, configured)
    try:
        model_manager.move_provider_to_front(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "message": f"供应商 {provider_id} 已移到首位"}


@router.get("/models/all")
async def get_all_models():
    """获取所有模型标识（供 Agent 定义下拉选择）"""
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()
    models = model_manager.get_all_models()

    # 构建模型列表，包含显示名称
    model_list = []
    for model_id in models:
        provider_id, model_name = model_id.split(":", 1)
        provider = model_manager.get_provider(provider_id)
        try:
            capabilities = model_manager.get_model_capabilities(model_id)
        except ValueError as exc:
            logger.warning("忽略 Provider 类型无效的模型 %s: %s", model_id, exc)
            continue
        display_name = (
            f"{provider['name']} - {model_name}"
            if provider
            else model_name
        )
        model_list.append({
            "value": model_id,
            "label": display_name,
            "display_name": display_name,
            "provider_id": provider_id,
            "model_name": model_name,
            "provider_type": capabilities["provider_type"] if provider else "",
            "model_params": capabilities["model_params"] if provider else {},
            "reasoning_efforts": capabilities["reasoning_efforts"] if provider else [],
            "category": provider.get("category", "") if provider else "",
        })

    return {"models": model_list, "default": model_manager.get_default_model()}


@router.get("/model-params/defaults")
async def get_default_model_params():
    """获取全局默认模型参数（thinking_enabled / reasoning_effort / temperature / top_p）

    供前端在 agent 定义中未配置 model_params 时作为兜底值展示。
    """
    from src.core.model_manager import get_model_manager
    model_manager = get_model_manager()
    return {"default_params": model_manager.get_default_params()}


@router.put("/model-providers/default")
async def set_default_model(body: dict[str, str]):
    """设置默认模型（已废弃：模型选择由 agents_config.json 的 agent 定义控制）"""
    from src.core.model_manager import get_model_manager

    # 保留空操作向后兼容，实际不再存储全局默认模型
    return {
        "success": True,
        "message": "模型选择已改为由 agents_config.json 的 agent 定义控制，不再支持设置全局默认模型",
    }

# ============ 压缩配置 API ============

@router.get("/compression/config")
async def get_compression_config():
    """获取压缩配置"""
    from src.compression.config import get_compression_config_manager

    config_manager = get_compression_config_manager()
    return config_manager.get_all_config()


@router.put("/compression/config")
async def update_compression_config(body: dict[str, Any]):
    """更新压缩配置"""
    from src.compression.config import get_compression_config_manager

    config_manager = get_compression_config_manager()

    try:
        # 更新各个配置部分
        _SECTIONS = ("general", "micro_compact", "full_compact",
                     "reactive_compact", "post_compact", "transcript")
        for section in _SECTIONS:
            if section in body:
                config_manager.update_section(section, body[section])

        return {"success": True, "message": "压缩配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/compression/stats")
async def get_compression_stats(request: Request):
    """获取压缩统计信息"""
    from src.compression.checker import get_compression_checker

    checker = get_compression_checker()

    # 获取当前主会话的消息
    session_mgr = request.app.state.session_manager
    main_session = session_mgr.get_main_session()

    if main_session:
        messages = main_session.lc_messages
        return checker.get_context_stats(messages=messages)
    else:
        # 如果没有主会话，返回默认统计信息
        return {
            "current_tokens": 0,
            "max_tokens": 1000000,
            "usage_ratio": 0,
            "message_count": 0,
            "tool_result_count": 0,
            "tool_result_tokens": 0,
            "model_info": {
                "provider_id": "deepseek",
                "model_name": "deepseek-v4-flash",
                "maxContextTokens": 1000000,
                "provider_name": "DeepSeek",
            },
        }


@router.get("/compression/logs")
async def get_compression_logs():
    """获取压缩日志"""
    from src.compression.transcript import get_transcript_saver

    saver = get_transcript_saver()
    return {
        "logs": saver.get_all_logs(),
        "summary": saver.get_log_summary()
    }


# ============ 预设短语 API ============


def _load_preset_phrases(request: Request) -> list[dict]:
    return request.app.state.preset_phrases_store.load().get("phrases", [])


def _save_preset_phrases(request: Request, phrases: list[dict]) -> None:
    request.app.state.preset_phrases_store.save({"phrases": phrases})


@router.get("/preset-phrases")
async def list_preset_phrases(request: Request):
    """获取所有预设短语"""
    return _load_preset_phrases(request)


@router.post("/preset-phrases")
async def create_preset_phrase(request: Request, body: dict = Body(...)):
    """新增预设短语"""
    label = body.get("label", "").strip()
    content = body.get("content", "").strip()
    if not label or not content:
        raise HTTPException(status_code=400, detail="label 和 content 不能为空")
    phrases = _load_preset_phrases(request)
    new_phrase = {
        "id": uuid.uuid4().hex[:12],
        "label": label,
        "content": content,
    }
    phrases.append(new_phrase)
    _save_preset_phrases(request, phrases)
    return new_phrase


@router.put("/preset-phrases/{phrase_id}")
async def update_preset_phrase(phrase_id: str, request: Request, body: dict = Body(...)):
    """更新预设短语"""
    phrases = _load_preset_phrases(request)
    for phrase in phrases:
        if phrase.get("id") == phrase_id:
            if "label" in body:
                phrase["label"] = body["label"].strip()
            if "content" in body:
                phrase["content"] = body["content"].strip()
            _save_preset_phrases(request, phrases)
            return phrase
    raise HTTPException(status_code=404, detail=f"未找到预设短语 {phrase_id}")


@router.delete("/preset-phrases/{phrase_id}")
async def delete_preset_phrase(phrase_id: str, request: Request):
    """删除预设短语"""
    phrases = _load_preset_phrases(request)
    new_phrases = [p for p in phrases if p.get("id") != phrase_id]
    if len(new_phrases) == len(phrases):
        raise HTTPException(status_code=404, detail=f"未找到预设短语 {phrase_id}")
    _save_preset_phrases(request, new_phrases)
    return {"success": True}


# ============ 用户消息注入 API ============

def _load_user_injection_config() -> dict:
    """加载用户消息注入配置"""
    if not USER_INJECTION_CONFIG_FILE.exists():
        return {"sections": []}
    try:
        with open(USER_INJECTION_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.warning(f"读取用户消息注入配置文件失败: {USER_INJECTION_CONFIG_FILE}")
        return {"sections": []}


def _save_user_injection_config(config: dict) -> None:
    """保存用户消息注入配置（原子写入）"""
    USER_INJECTION_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = USER_INJECTION_CONFIG_FILE.with_suffix('.json.tmp')
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp_path), str(USER_INJECTION_CONFIG_FILE))
    except (IOError, OSError) as e:
        logger.error(f"保存用户消息注入配置失败: {USER_INJECTION_CONFIG_FILE} | {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@router.get("/user-injection")
async def get_user_injection_sections():
    """获取用户消息注入配置"""
    config = _load_user_injection_config()
    return {
        "sections": config.get("sections", []),
        "version": config.get("version", "1.0"),
        "last_updated": config.get("last_updated", ""),
    }


@router.put("/user-injection")
async def update_user_injection_sections(body: dict[str, Any]):
    """更新用户消息注入配置"""
    sections = body.get("sections", [])

    # 验证sections格式
    for section in sections:
        if "name" not in section:
            raise HTTPException(status_code=400, detail="每个section必须包含name字段")

    # 加载现有配置
    config = _load_user_injection_config()

    # 更新配置
    config["sections"] = sections
    config["last_updated"] = datetime.now(timezone.utc).isoformat()

    # 保存配置
    _save_user_injection_config(config)

    return {"success": True, "message": "用户消息注入配置已更新"}


@router.post("/user-injection/sections")
async def add_user_injection_section(body: dict[str, Any]):
    """添加新的用户消息注入section"""
    name = body.get("name", "").strip()
    content = body.get("content", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="section名称不能为空")

    # 加载现有配置
    config = _load_user_injection_config()
    sections = config.get("sections", [])

    # 检查名称是否已存在
    if any(s.get("name") == name for s in sections):
        raise HTTPException(status_code=400, detail=f"section '{name}' 已存在")

    # 添加新section
    new_section = {
        "name": name,
        "content": content,
        "token_estimate": body.get("token_estimate", 0),
        "cache_break": body.get("cache_break", True),
        "cache_break_reason": body.get("cache_break_reason", "动态内容"),
        "enabled": body.get("enabled", True),
        "order": len(sections),
    }
    sections.append(new_section)

    # 更新配置
    config["sections"] = sections
    config["last_updated"] = datetime.now(timezone.utc).isoformat()

    # 保存配置
    _save_user_injection_config(config)

    return {"success": True, "section": new_section}


@router.delete("/user-injection/sections/{section_name}")
async def delete_user_injection_section(section_name: str):
    """删除用户消息注入section"""
    # 加载现有配置
    config = _load_user_injection_config()
    sections = config.get("sections", [])

    # 查找并删除section
    new_sections = [s for s in sections if s.get("name") != section_name]
    if len(new_sections) == len(sections):
        raise HTTPException(status_code=404, detail=f"未找到section '{section_name}'")

    # 更新配置
    config["sections"] = new_sections
    config["last_updated"] = datetime.now(timezone.utc).isoformat()

    # 保存配置
    _save_user_injection_config(config)

    return {"success": True, "message": f"已删除section '{section_name}'"}


# ============ Cron API ============


def _get_cron_scheduler(request: Request):
    sm = _get_session_manager(request)
    return sm.cron_scheduler


def _get_cron_job_manager(request: Request):
    sm = _get_session_manager(request)
    return sm.cron_job_manager


def _get_cron_instances(request: Request):
    scheduler = _get_cron_scheduler(request)
    job_mgr = _get_cron_job_manager(request)
    if not scheduler or not job_mgr:
        raise HTTPException(status_code=500, detail="Cron 系统未初始化")
    return scheduler, job_mgr


class CronScheduleRequest(BaseModel):
    kind: str = "once"  # "once" | "interval" | "cron"
    at: str | None = None
    every_minutes: int | None = None
    expr: str | None = None


class CreateCronJobRequest(BaseModel):
    name: str
    prompt: str
    schedule: CronScheduleRequest
    agent_type: str = "researcher"
    silent_on_empty: bool = True
    repeat: int | None = None


class UpdateCronJobRequest(BaseModel):
    name: str | None = None
    prompt: str | None = None
    schedule: CronScheduleRequest | None = None
    enabled: bool | None = None
    agent_type: str | None = None
    silent_on_empty: bool | None = None


@router.get("/cron/jobs")
async def list_cron_jobs(request: Request):
    """获取所有 cron job 列表"""
    try:
        _, job_mgr = _get_cron_instances(request)
        jobs = job_mgr.load_jobs()
        return {
            "jobs": [job.to_dict() for job in jobs],
            "total": len(jobs),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取 cron jobs 失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.post("/cron/jobs")
async def create_cron_job(body: CreateCronJobRequest, request: Request):
    """创建新的 cron job"""
    try:
        _, job_mgr = _get_cron_instances(request)

        from src.cron.types import CronJob, CronSchedule

        schedule = CronSchedule(
            kind=body.schedule.kind,
            at=body.schedule.at,
            every_minutes=body.schedule.every_minutes,
            expr=body.schedule.expr,
        )
        new_job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=body.name,
            prompt=body.prompt,
            schedule=schedule,
            agent_type=body.agent_type,
            silent_on_empty=body.silent_on_empty,
            repeat=body.repeat,
        )

        jobs = job_mgr.load_jobs()
        job_mgr.add_job(jobs, new_job)
        job_mgr.save_jobs(jobs)

        return {"success": True, "job": new_job.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建 cron job 失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.post("/cron/jobs/{job_id}/run")
async def run_cron_job_now(job_id: str, request: Request):
    """立即执行指定 cron job"""
    try:
        scheduler, job_mgr = _get_cron_instances(request)

        jobs = job_mgr.load_jobs()
        job = next((j for j in jobs if j.id == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"未找到 job: {job_id}")

        await scheduler.run_job_now(job)
        job_mgr.save_jobs(jobs)

        return {"success": True, "message": f"Job {job_id} 已触发执行"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("执行 cron job 失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.put("/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, body: UpdateCronJobRequest, request: Request):
    """更新 cron job"""
    try:
        _, job_mgr = _get_cron_instances(request)

        jobs = job_mgr.load_jobs()
        job = next((j for j in jobs if j.id == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"未找到 job: {job_id}")

        updates = body.model_dump(exclude_unset=True)

        if "name" in updates:
            job.name = updates["name"]
        if "prompt" in updates:
            job.prompt = updates["prompt"]
        if "enabled" in updates:
            job.enabled = updates["enabled"]
        if "agent_type" in updates:
            job.agent_type = updates["agent_type"]
        if "silent_on_empty" in updates:
            job.silent_on_empty = updates["silent_on_empty"]
        if "schedule" in updates:
            sched = updates["schedule"]
            job.schedule.kind = sched.get("kind", job.schedule.kind)
            job.schedule.at = sched.get("at", job.schedule.at)
            job.schedule.every_minutes = sched.get("every_minutes", job.schedule.every_minutes)
            job.schedule.expr = sched.get("expr", job.schedule.expr)
            # 重新计算 next_run_at
            from datetime import datetime as dt, timezone as tz
            next_dt = job_mgr._compute_next_run(job, base=dt.now(tz))
            if next_dt:
                period_seconds = job_mgr._get_period_seconds(job)
                next_dt = job_mgr._apply_jitter(job, next_dt, period_seconds)
                job.next_run_at = next_dt.isoformat()
            else:
                job.next_run_at = None

        job_mgr.save_jobs(jobs)
        return {"success": True, "job": job.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("更新 cron job 失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.delete("/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str, request: Request):
    """删除 cron job"""
    try:
        _, job_mgr = _get_cron_instances(request)

        jobs = job_mgr.load_jobs()
        job = next((j for j in jobs if j.id == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"未找到 job: {job_id}")

        jobs.remove(job)
        job_mgr.save_jobs(jobs)

        return {"success": True, "message": f"Job {job_id} 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除 cron job 失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.get("/cron/status")
async def get_cron_status(request: Request):
    """获取 cron 调度器状态"""
    try:
        scheduler, _ = _get_cron_instances(request)
        return scheduler.get_status()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取 cron 状态失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")


@router.get("/cron/output/{job_id}")
async def get_cron_output(job_id: str, request: Request, filename: str | None = None):
    """获取 cron job 输出文件列表或文件内容"""
    try:
        _, job_mgr = _get_cron_instances(request)

        if filename:
            content = job_mgr.read_output(job_id, filename)
            if content is None:
                raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
            return {"job_id": job_id, "filename": filename, "content": content}
        else:
            files = job_mgr.get_output(job_id)
            return {"job_id": job_id, "files": files, "total": len(files)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取 cron 输出失败")
        raise HTTPException(status_code=500, detail="内部错误，请查看服务日志")
