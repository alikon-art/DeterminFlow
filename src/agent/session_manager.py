"""
会话管理器 - 统一管理多个 main session 和所有 sub sessions 的生命周期、消息路由和持久化

支持多 main 并存（如 chat main + workflow main），每个 main 拥有独立的 subtree。
所有会话共享统一的对话驱动（session.send_message），
主/子会话的区别仅在上层控制（工具集、通信工具、通知机制）。
"""
import asyncio
from collections import OrderedDict
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from src.config import (
    MAX_SUB_SESSIONS,
    SESSION_CACHE_MAX_ENTRIES,
    SESSION_HISTORY_MAX_ENTRIES,
    SUB_AGENT_MAX_ROUNDS,
)
from src.agent.session import AgentSession
from src.agent.session_catalog import SessionCatalog
from src.agent.session_lifecycle import SessionLifecycleMixin
from src.core.change_broadcaster import ChangeBroadcaster
from langgraph.errors import GraphRecursionError

from src.core.utils import is_visible_to_frontend
from src.extension_api import PromptContextRequest

logger = logging.getLogger(__name__)


class NotificationBroadcaster:
    """通知广播器：将通知 fan-out 到所有订阅者。

    替代单一 asyncio.Queue，解决 Chat WS 和 Events WS
    两个消费者竞争同一队列导致通知丢失的问题。
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """注册一个新订阅者，返回其专属队列。"""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """取消订阅。"""
        self._subscribers.discard(q)

    async def put(self, notification: dict):
        """广播通知到所有订阅者。"""
        for q in self._subscribers:
            try:
                q.put_nowait(notification)
            except asyncio.QueueFull:
                logger.warning("通知队列已满，丢弃通知")


def _try_emit_event(event: dict):
    """尝试向事件总线发送事件（非阻塞，不影响核心逻辑）"""
    try:
        from src.web.event_bus import event_bus
        loop = asyncio.get_running_loop()
        loop.create_task(event_bus.emit_event(event))
    except RuntimeError:
        # 无运行中的事件循环（如清理线程），静默忽略
        logger.debug("无法发送事件：无运行中的事件循环")
    except Exception:
        logger.debug("事件推送失败", exc_info=True)


class SessionManager(SessionLifecycleMixin):
    """多会话管理器。

    职责：
    - 会话生命周期管理（创建、注册、删除、终止）
    - 跨会话消息路由（route_message / send_message）
    - 通知队列管理
    - 会话持久化
    - Workspace 管理（编码工具集成）

    不再承担 Graph 执行和消息累积的职责，这些已下沉到 AgentSession。
    """

    def __init__(
        self,
        cold_cache_max_entries: int | None = None,
        history_max_entries: int | None = None,
    ):
        self.sessions: dict[str, AgentSession] = {}
        self._session_catalog = SessionCatalog()
        self._cold_session_lru: OrderedDict[str, None] = OrderedDict()
        self._cold_cache_max_entries = max(
            1,
            cold_cache_max_entries
            if cold_cache_max_entries is not None
            else SESSION_CACHE_MAX_ENTRIES,
        )
        self._history_max_entries = max(
            0,
            history_max_entries
            if history_max_entries is not None
            else SESSION_HISTORY_MAX_ENTRIES,
        )
        self.main_session_id: str | None = None
        self.notification_queue: asyncio.Queue = asyncio.Queue()  # 兼容旧接口（已被 broadcaster 取代）
        self.notification_broadcaster: NotificationBroadcaster = NotificationBroadcaster()
        self._session_changes = ChangeBroadcaster()
        self._sub_tasks: dict[str, asyncio.Task] = {}
        # Workspace 管理器（延迟初始化，在 web_server lifespan 中设置）
        self._workspace_manager = None
        # 审批管理器（延迟初始化，在 web_server lifespan 中设置）
        self._approval_manager = None
        # Workflow 管理器（延迟初始化，用于 workflow 工具注入）
        self._workflow_manager = None
        self._extension_manager = None
        self._agent_config_manager = None

    def inject_dependencies(
        self,
        workspace_manager=None,
        approval_manager=None,
        mcp_client=None,
        workflow_manager=None,
        cron_scheduler=None,
        cron_job_manager=None,
        extension_manager=None,
        agent_config_manager=None,
    ):
        """注入延迟初始化的依赖项（由 web_server lifespan 调用）。"""
        if workspace_manager is not None:
            self._workspace_manager = workspace_manager
        if approval_manager is not None:
            self._approval_manager = approval_manager
        if mcp_client is not None:
            self._mcp_client = mcp_client
        if workflow_manager is not None:
            self._workflow_manager = workflow_manager
        if cron_scheduler is not None:
            self._cron_scheduler = cron_scheduler
        if cron_job_manager is not None:
            self._cron_job_manager = cron_job_manager
        if extension_manager is not None:
            self._extension_manager = extension_manager
        if agent_config_manager is not None:
            self._agent_config_manager = agent_config_manager

    @property
    def cron_scheduler(self):
        """获取定时任务调度器（可能为 None，依赖 inject_dependencies 初始化）"""
        return getattr(self, "_cron_scheduler", None)

    @property
    def cron_job_manager(self):
        """获取定时任务 JobManager（可能为 None，依赖 inject_dependencies 初始化）"""
        return getattr(self, "_cron_job_manager", None)

    # ============================================================
    # 会话注册与查询
    # ============================================================

    # ============================================================
    # Builder / Assembler 注入
    # ============================================================

    def set_builders(self, prompt_builder, tool_assembler):
        """注入 PromptBuilder 和 ToolAssembler 实例。"""
        self._prompt_builder = prompt_builder
        self._tool_assembler = tool_assembler

    def get_effective_agent_definition(
        self,
        agent_type: str,
        *,
        model_override: str | None = None,
    ) -> dict | None:
        """Return the current execution identity through public dependencies."""
        builder = getattr(self, "_prompt_builder", None)
        prompt_manager = getattr(builder, "prompt_manager", None)
        if prompt_manager is None:
            raise RuntimeError(
                "SessionManager 缺少进程级 layered PromptManager；"
                "拒绝生成 Workflow Agent 运行身份"
            )
        from src.workflow.runtime import build_effective_agent_definition

        return build_effective_agent_definition(
            agent_type,
            prompt_manager=prompt_manager,
            prompt_builder=builder,
            model_override=model_override,
        )

    def verify_effective_agent_definition(
        self,
        agent_type: str,
        *,
        expected_sha256: str,
        model_override: str | None = None,
    ) -> None:
        """Fail closed when Agent prompt/model semantics changed after Task creation."""
        from src.workflow.runtime import effective_agent_definition_sha256

        current = self.get_effective_agent_definition(
            agent_type,
            model_override=model_override,
        )
        if current is None:
            raise RuntimeError(f"Workflow Agent 定义不存在: {agent_type}")
        actual_sha256 = effective_agent_definition_sha256(current)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                "Workflow Agent 运行身份已漂移，拒绝启动新 session: "
                f"agent_type={agent_type}, expected={expected_sha256}, "
                f"actual={actual_sha256}"
            )

    # ============================================================
    # Sub Session 创建（预编译 Graph + 自动发首条消息）
    # ============================================================

    async def _build_extension_prompt_context(
        self,
        agent_type: str,
        agent_def,
        *,
        session_type: str = "main",
        workflow_id: str = "",
    ) -> str:
        """Collect optional prompt fragments without knowing extension implementations."""
        if self._extension_manager is None:
            return ""
        return await self._extension_manager.build_prompt_context(PromptContextRequest(
            agent_type=agent_type,
            agent_definition=agent_def,
            session_type=session_type,
            workflow_id=workflow_id,
        ))

    async def create_main_session(
        self,
        llm_client=None,
        agent_type: str = "main",
        from_session_id: str | None = None,
    ) -> dict:
        """创建新的主会话（用于前端主动创建新会话）。

        支持指定任意 agent_type（如 "coder"、"researcher" 等），
        非 main 类型使用 subagent 路径构建提示词和工具集。

        from_session_id: 可选，从既有会话继承 workspace（新会话复用其
            workspace 目录，而不是新建空目录）。继承判断必须在创建新
            workspace 之前完成，且 register_main 不会覆盖继承的路径。
        """
        from src.agent.definition import get_agent_definition

        agent_def = get_agent_definition(agent_type)

        # 每个新 Main 都按当前配置创建模型，避免复用服务启动时的旧 Provider。
        from src.core.llm_client import create_startup_llm
        model = agent_def.model if agent_def else None
        agent_params = agent_def.model_params if agent_def else None
        llm_client = create_startup_llm(
            model_override=model,
            streaming=True,
            model_params=agent_params,
        )

        # 1. 创建 session（自动生成 UUID）
        new_session = AgentSession(
            session_type="main",
            agent_type=agent_type,
            model_params=agent_params,
        )

        # 只记录用户或 Agent 明确选择的模型。null 表示调用时跟随当前默认模型，
        # 避免把 Provider 尚未确认时的临时回退固化给后续 Sub Session。
        new_session.model_id = model

        # 2. 分配 workspace（必须在 prompt 构建前，因为 session_meta 需要 workspace_path）。
        #    支持 from_session_id 继承：复用源会话的 workspace，而不是新建空目录。
        #    继承判断必须发生在 create_workspace 之前（评审：此前新 Session 先创建了
        #    自己的 workspace，导致 `if not new_session.workspace_path` 永远为 false）。
        inherited_workspace = ""
        if from_session_id:
            src_session = self.get_session(from_session_id)
            if src_session is None:
                return {
                    "success": False,
                    "message": f"未找到待继承会话 {from_session_id}",
                    "error": "from_session_not_found",
                }
            inherited_workspace = src_session.workspace_path or ""
        if self._workspace_manager:
            if inherited_workspace:
                new_session.workspace_path = inherited_workspace
                logger.info(
                    "新主会话 %s 继承 workspace: %s (from %s)",
                    new_session.session_id, inherited_workspace, from_session_id,
                )
            else:
                ws_path = self._workspace_manager.create_workspace(new_session.session_id)
                new_session.workspace_path = str(ws_path)

        # 加载 agent 定义并解析 prompt_template
        agent_def = get_agent_definition(agent_type)
        if agent_def is None:
            logger.warning(f"未知 agent_type: {agent_type}，回退到 default")
            from src.agent.definition import get_default_agent_definition
            agent_def = get_default_agent_definition()
        prompt_template = agent_def.prompt_template if agent_def else "subagent"

        # 2. 收集可选扩展提供的 Prompt 上下文（仅 main 模板代理）
        extension_context = ""
        if prompt_template == "main":
            extension_context = await self._build_extension_prompt_context(agent_type, agent_def)

        # 3. 构建提示词（builder 内部根据 prompt_template 路由）
        builder = getattr(self, '_prompt_builder', None)
        if builder:
            if prompt_template == "main":
                new_session.system_prompt = builder.build(agent_type, session=new_session,
                                                          is_workflow=False, extension_context=extension_context)
            else:
                combined_append = agent_def.system_prompt_template if agent_def else ""
                new_session.system_prompt = builder.build(
                    agent_type, session=new_session,
                    custom_append=combined_append,
                    is_workflow=False,
                )
        else:
            new_session.system_prompt = self._build_main_prompt_fallback(new_session)

        # 4. 注册 session
        self.register_main(new_session)

        # 5. 构建工具并初始化 Graph（assembler 内部根据 prompt_template 路由）
        assembler = getattr(self, '_tool_assembler', None)
        if assembler:
            if prompt_template == "main":
                tools = assembler.build(agent_type, llm_client=llm_client)
            else:
                tools = assembler.build(
                    agent_type,
                    agent_definition=agent_def,
                    workspace_path=new_session.workspace_path or "",
                )
        else:
            tools = []

        # 6. Graph 初始化
        new_session.setup_graph(llm=llm_client, tools=tools)
        new_session.start_consumer()

        logger.info(f"新主会话 {new_session.session_id} 已创建 (agent_type={agent_type})")
        _try_emit_event({
            "type": "session_update",
            "action": "created",
            "session_id": new_session.session_id,
            "session_type": "main",
            "agent_type": agent_type,
            "status": "completed"
        })

        return {
            "success": True,
            "session_id": new_session.session_id,
            "message": f"新主会话 {new_session.session_id} 已创建 (agent_type={agent_type})"
        }

    async def update_session_model(
        self,
        session_id: str,
        *,
        model_id: str,
        reasoning_effort: str | None,
    ) -> dict:
        """更新交互 Main 的会话级模型并重编译 Graph。"""
        session = self.get_session(session_id)
        if session is None:
            return {"success": False, "message": f"未找到会话 {session_id}"}
        if session.session_type != "main" or session.runtime_scope != "interactive":
            return {"success": False, "message": "仅支持切换交互 Main 会话的模型"}

        from src.core.model_manager import get_model_manager
        from src.core.llm_client import create_startup_llm

        model_manager = get_model_manager()
        if model_id not in model_manager.get_all_models():
            return {"success": False, "message": f"模型未配置: {model_id}"}

        supported_efforts = model_manager.get_model_capabilities(model_id)[
            "reasoning_efforts"
        ]
        if reasoning_effort is not None and reasoning_effort not in supported_efforts:
            return {
                "success": False,
                "message": f"模型 {model_id} 不支持推理强度 {reasoning_effort}",
            }

        async with session._invoke_lock:
            if session.invocation_active:
                return {"success": False, "message": "会话正在生成中，请稍后切换模型"}

            model_params = dict(session.model_params)
            if reasoning_effort is None:
                model_params.pop("reasoning_effort", None)
            else:
                model_params["reasoning_effort"] = reasoning_effort
                model_params["thinking_enabled"] = True

            llm = create_startup_llm(
                model_override=model_id,
                streaming=True,
                model_params=model_params,
            )

            agent_config_manager = self._agent_config_manager
            if agent_config_manager is not None:
                current_main = agent_config_manager.get_agent_config("main") or {}
                synced_model_params = dict(current_main.get("model_params") or {})
                synced_model_params.update(model_params)
                if reasoning_effort is None:
                    synced_model_params.pop("reasoning_effort", None)
                if not agent_config_manager.update_agent("main", {
                    "model": model_id,
                    "model_params": synced_model_params,
                }):
                    return {"success": False, "message": "同步 Main 模型覆盖失败"}

            session.setup_graph(llm=llm, tools=session.tools)
            session.model_id = model_id
            session.model_params = model_params
            session.updated_at = datetime.now(timezone.utc).isoformat()
            await session.async_save()

        _try_emit_event({
            "type": "session_update",
            "action": "model_changed",
            "session_id": session_id,
            "model_id": model_id,
            "reasoning_effort": reasoning_effort,
        })
        return {
            "success": True,
            "message": "会话模型已更新",
            "model_id": model_id,
            "model_params": model_params,
        }

    async def init_workflow_main(
        self,
        llm_client,
        workflow_id: str,
        task_description: str = "",
        task_id: str | None = None,
    ) -> "AgentSession":
        """创建 workflow main session（供 engine.py 调用）。

        Chat main 和 workflow main 走同一流程，差异由 PromptBuilder 的
        is_workflow 参数和 ToolAssembler 的 agent_type="main" 自动处理。

        Returns:
            已初始化 Graph 并启动 consumer 的 AgentSession（未注册到 sessions dict）
        """
        from src.agent.session import AgentSession

        # 1. 创建 session
        session = AgentSession(
            session_type="main", agent_type="main",
            task_description=task_description or f"Workflow: {workflow_id}",
            workflow_id=workflow_id,
            task_id=task_id,
            runtime_scope="workflow",
        )

        # 2. 设置 workspace_path（共享 workflow workspace: data/workspaces/{workflow_id}/）
        if self._workspace_manager:
            ws_path = self._workspace_manager.create_workflow_workspace(workflow_id)
            session.workspace_path = str(ws_path)

        # 3. 收集扩展 Prompt 上下文
        agent_type = session.agent_type
        from src.agent.definition import get_agent_definition
        agent_def = get_agent_definition(agent_type)
        extension_context = await self._build_extension_prompt_context(
            agent_type,
            agent_def,
            session_type="workflow-main",
            workflow_id=workflow_id,
        )

        # 4. 构建提示词（is_workflow=True，workflow_only sections 自动生效）
        builder = getattr(self, '_prompt_builder', None)
        if builder:
            session.system_prompt = builder.build(agent_type, session=session, is_workflow=True,
                                                   extension_context=extension_context)

        # 5. 构建工具
        assembler = getattr(self, '_tool_assembler', None)
        wf_mgr = getattr(self, '_workflow_manager', None)
        tools = assembler.build(agent_type, is_workflow=True) if assembler else []

        # 6. 初始化 Graph
        session.setup_graph(llm=llm_client, tools=tools)
        session.start_consumer()
        session._default_event_callback = self._make_event_callback(session.session_id)

        logger.info(f"Workflow main 已创建: {session.session_id} (workflow={workflow_id})")
        return session

    async def init_workflow_main_for_pre_start(
        self,
        llm_client,
        workflow_id: str,
        task_id: str,
        definition: "WorkflowDef",
        parameter_values: dict[str, str],
    ) -> "AgentSession":
        """为预启动模式创建 workflow main session。

        与 init_workflow_main 的区别：
        - 注入完整的 workflow 上下文（通过 prompts_config.json workflow_only sections 管理）
        - 设置 task_id 到 session 上下文
        - 提前创建工作区目录
        """
        from src.agent.session import AgentSession

        # 1. 创建 session
        session = AgentSession(
            session_type="main", agent_type="main",
            task_description=f"Workflow: {workflow_id} (pre-start)",
            workflow_id=workflow_id,
            task_id=task_id,
        )

        # 2. 设置 workspace_path（共享 workflow workspace: data/workspaces/{workflow_id}/）
        if self._workspace_manager:
            ws_path = self._workspace_manager.create_workflow_workspace(workflow_id)
            session.workspace_path = str(ws_path)

        # 3. 收集扩展 Prompt 上下文
        agent_type = session.agent_type
        from src.agent.definition import get_agent_definition
        agent_def = get_agent_definition(agent_type)
        extension_context = await self._build_extension_prompt_context(
            agent_type,
            agent_def,
            session_type="workflow-main",
            workflow_id=workflow_id,
        )

        # 4. 构建系统提示词（is_workflow=True，workflow 上下文通过 prompts_config.json 管理）
        builder = getattr(self, '_prompt_builder', None)
        if builder:
            session.system_prompt = builder.build(
                agent_type, session=session, is_workflow=True,
                extension_context=extension_context,
                workflow_definition=definition,
            )
        else:
            from src.workflow.prompt_injector import (
                build_workflow_overview,
                build_workflow_structure,
                build_workflow_definition_json,
            )
            fallback = self._build_main_prompt_fallback(session)
            fallback += "\n\n" + build_workflow_overview(definition)
            fallback += "\n\n" + build_workflow_structure(definition)
            fallback += "\n\n" + build_workflow_definition_json(definition)
            session.system_prompt = fallback

        # 5. 构建工具（is_workflow=True 自动注入 workflow 工具）
        assembler = getattr(self, '_tool_assembler', None)
        tools = assembler.build(agent_type, is_workflow=True) if assembler else []

        # 6. 初始化 Graph
        session.setup_graph(llm=llm_client, tools=tools)
        session.start_consumer()
        session._default_event_callback = self._make_event_callback(session.session_id)

        logger.info(f"Workflow main (pre-start) 已创建: {session.session_id}")
        return session

    def _build_main_prompt_fallback(self, session: "AgentSession") -> str:
        """Fallback：没有 PromptBuilder 时手动构建 main prompt。"""
        from src.prompts import create_orchestrator
        from src.prompts.manager import PromptManager
        prompt_mgr = PromptManager()
        skill_mgr = getattr(self, '_skill_manager', None)
        rule_mgr = getattr(self, '_rule_manager', None)
        orche = create_orchestrator(
            prompt_manager=prompt_mgr,
            skill_manager=skill_mgr,
            rule_manager=rule_mgr,
        )
        prompt = orche.build_effective_prompt(
            agent_type="main",
            skills_mode="auto_inject",
            include_rules=True,
        )
        from src.prompts.placeholders import build_session_meta_text, render_prompt_template
        return render_prompt_template(prompt, {"session_meta": build_session_meta_text(session)})

    def _build_sub_prompt_fallback(self, session, agent_def, agent_type, combined_append,
                                    is_workflow_node, upstream_summary):
        """Fallback：没有 PromptBuilder 时手动构建 sub prompt。"""
        from src.prompts import create_orchestrator
        from src.prompts.manager import PromptManager
        prompt_mgr = PromptManager()
        prompt_template = agent_def.prompt_template if agent_def else "subagent"

        if prompt_template == "compressor":
            from src.prompts.compressor_prompts import build_compressor_prompt
            config_sections = prompt_mgr.get_sections("compressor")
            return build_compressor_prompt(config_sections)

        skill_mgr = getattr(self, '_skill_manager', None)
        rule_mgr = getattr(self, '_rule_manager', None)
        orche = create_orchestrator(
            prompt_manager=prompt_mgr,
            skill_manager=skill_mgr,
            rule_manager=rule_mgr,
        )
        extra_tool_names = agent_def.tools if agent_def.tools and agent_def.tools != ["*"] else None
        prompt = orche.build_sub_agent_prompt(
            custom_append=combined_append,
            agent_type=agent_type,
            include_skills=(agent_def.include_skills if agent_def else True),
            skills_mode="discovery",
            include_rules=(agent_def.include_rules if agent_def else True),
            is_workflow_node=is_workflow_node,
            upstream_summary=upstream_summary,
            prompt_template=prompt_template,
            extra_tool_names=extra_tool_names,
        )
        from src.prompts.placeholders import build_session_meta_text, render_prompt_template
        return render_prompt_template(prompt, {"session_meta": build_session_meta_text(session)})

    async def _notify_session_end(self, session: "AgentSession"):
        """Notify optional extensions after a session ends."""
        if self._extension_manager is not None:
            await self._extension_manager.notify_session_end(session)

    async def create_sub_session(self, task_description: str, custom_prompt: str = "",
                                  llm_client=None, agent_type: str = "default", model_override: str | None = None,
                                  workspace_path: str | None = None, is_workflow_node: bool = False,
                                  on_node_complete=None, parent_id: str | None = None,
                                  workflow_id: str | None = None,
                                  task_id: str | None = None,
                                  upstream_summary: str = "",
                                  node_id: str = "",
                                  auto_flow: bool = False,
                                  enable_complete_node_task: bool = True,
                                  on_auto_complete=None,
                                  template_vars: dict[str, str] | None = None,
                                  on_reject_upstream=None) -> dict:
        active_count = self.get_active_sub_count()
        if active_count >= MAX_SUB_SESSIONS:
            return {"success": False, "session_id": "", "message": f"已达到最大并发 sub session 数量 ({MAX_SUB_SESSIONS})"}

        from src.agent.definition import get_agent_definition, get_default_agent_definition
        agent_def = get_agent_definition(agent_type)
        if agent_def is None:
            logger.warning(f"未知 agent_type: {agent_type}，回退到 default")
            agent_def = get_default_agent_definition()

        # 构建 custom_append（合并 agent 模板 + 调用方自定义）
        combined_append = ""
        if agent_def.system_prompt_template:
            combined_append += agent_def.system_prompt_template
        if custom_prompt:
            if combined_append:
                combined_append += "\n\n"
            combined_append += custom_prompt

        # 1. 创建 session
        session = AgentSession(
            session_type="sub", parent_id=parent_id or self.main_session_id,
            task_description=task_description, system_prompt="",  # prompt 由 builder 后续设置
            agent_type=agent_type, workflow_id=workflow_id, task_id=task_id,
        )
        session.node_id = node_id
        session._auto_flow = auto_flow
        session._on_auto_complete = on_auto_complete

        # workflow 实时推送：注册 _on_record_append 回调
        if is_workflow_node and workflow_id and node_id:
            async def _push_wf_message(msg_entry: dict):
                from src.web.event_bus import event_bus
                await event_bus.emit_event({
                    "type": "wf_node_message",
                    "workflow_id": workflow_id,
                    "node_id": node_id,
                    "session_id": session.session_id,
                    "message": msg_entry,
                })
            session._on_record_append = _push_wf_message

        # 2. 分配 workspace（必须在 prompt 构建前，因为 session_meta 需要 workspace_path）
        if workspace_path:
            session.workspace_path = workspace_path
            logger.info(f"Sub session {session.session_id} workspace (workflow): {workspace_path}")
        elif self._workspace_manager:
            ws_path = self._workspace_manager.create_workspace(session.session_id)
            session.workspace_path = str(ws_path)
            logger.info(f"Sub session {session.session_id} workspace: {ws_path}")

        self.register_runtime_session(session)

        # 解析 prompt_template 用于路由
        prompt_template = agent_def.prompt_template if agent_def else "subagent"

        # 3. 构建提示词（含 session_meta，由 builder 内部根据 prompt_template 路由）
        builder = getattr(self, '_prompt_builder', None)
        if builder:
            if prompt_template == "compressor":
                full_prompt = builder.build(agent_type)
            else:
                full_prompt = builder.build(
                    agent_type, session=session,
                    custom_append=combined_append,
                    is_workflow=is_workflow_node,
                    upstream_summary=upstream_summary,
                    template_vars=template_vars,
                )
        else:
            full_prompt = self._build_sub_prompt_fallback(
                session, agent_def, agent_type, combined_append,
                is_workflow_node, upstream_summary,
            )
        session.system_prompt = full_prompt

        # 4. 存储 on_node_complete 和 on_reject_upstream（_invoke_graph 通过 set_session_context 注入）
        session._on_node_complete = on_node_complete
        session._on_reject_upstream = on_reject_upstream

        # 5. 构建工具（assembler 内部根据 prompt_template 路由）
        assembler = getattr(self, '_tool_assembler', None)
        if prompt_template == "compressor":
            sub_tools = []
        elif assembler:
            sub_tools = assembler.build(
                agent_type,
                is_workflow_node=is_workflow_node,
                agent_definition=agent_def,
                workspace_path=session.workspace_path or "",
                enable_complete_node_task=enable_complete_node_task,
                enable_reject_upstream=on_reject_upstream is not None,
            )
        else:
            sub_tools = []

        # 6. Graph 初始化
        max_rounds = agent_def.max_turns if agent_def else SUB_AGENT_MAX_ROUNDS
        from src.core.llm_client import create_llm
        if model_override:
            final_model = model_override
        elif agent_def and agent_def.model:
            final_model = agent_def.model
        elif parent_id:
            # model=null -> 继承父会话的模型
            parent_session = self.sessions.get(parent_id)
            if parent_session and parent_session.model_id:
                final_model = parent_session.model_id
                logger.info(
                    f"Sub session {session.session_id} 继承父会话 {parent_id} 的模型: {final_model}"
                )
            else:
                final_model = None
                logger.warning(
                    f"Sub session {session.session_id} 未配置模型且父会话 {parent_id} 无 model_id"
                )
        else:
            final_model = None
        agent_params = agent_def.model_params if agent_def else None
        sub_llm = create_llm(model_override=final_model, streaming=True, model_params=agent_params)
        session.model_id = final_model  # 记录模型标识到 session
        session.model_params = dict(agent_params or {})
        session.setup_graph(llm=sub_llm, tools=sub_tools)
        session.start_consumer()

        await session.async_save()
        logger.info(f"Sub session {session.session_id} 已创建并预编译 Graph，agent_type={agent_type}")

        _try_emit_event({"type": "session_update", "action": "created", "session_id": session.session_id, "session_type": "sub", "task": task_description[:100], "status": "running"})

        # 异步发送首条消息（task_description 作为第一轮对话的 HumanMessage）
        async def _auto_first_message():
            try:
                event_callback = self._make_event_callback(session.session_id)
                await session.send_message(
                    content=task_description,
                    event_callback=event_callback,
                    max_rounds=max_rounds,
                )
                _try_emit_event({"type": "session_update", "action": "status_changed", "session_id": session.session_id, "status": session.status})
            except asyncio.CancelledError:
                session.status = "error"
                logger.info(f"Sub session {session.session_id} 被取消")
            except GraphRecursionError:
                # 已在 session._invoke_graph 中记录上下文日志，此处只标记状态
                session.status = "error"
            except Exception as e:
                session.status = "error"
                logger.error(f"Sub session {session.session_id} 首条消息执行异常: {e}", exc_info=True)
                await self.notification_broadcaster.put({
                    "type": "error", "from": session.session_id,
                    "content": f"子会话 {session.session_id} 运行出错: {str(e)}",
                    "status": "error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            finally:
                # graph 完全结束后通知 agent.py，确保 record 已包含所有消息
                if session._on_auto_complete:
                    last_msg = session.get_last_assistant_message() if hasattr(session, 'get_last_assistant_message') else ""
                    if session.status == "completed":
                        session._on_auto_complete(
                            session.session_id, last_msg, "success", "",
                        )
                    elif session.status == "error":
                        error_msg = "Agent 执行异常结束（可能是达到轮次上限）"
                        session._on_auto_complete(
                            session.session_id, last_msg, "failure", error_msg,
                        )
                    else:
                        # 审查拦截等非错误非成功状态（如 ContentFilter/Risk 被捕获后 session 保持 running）
                        error_msg = f"Agent 执行被中断（状态: {session.status}）"
                        session._on_auto_complete(
                            session.session_id, last_msg, "failure", error_msg,
                        )

                session.updated_at = datetime.now(timezone.utc).isoformat()
                await session.async_save()
                self._sub_tasks.pop(session.session_id, None)
                self._signal_session_update(session.session_id)

        # 使用全新的空 context 创建 task，防止 LangChain callback manager
        # 通过 contextvars 穿透——否则子会话的 astream_events 事件会被主会话的
        # callback 捕获，导致子会话 token 串流到主会话的 WebSocket
        import contextvars
        clean_ctx = contextvars.Context()  # 全新的空 context

        task = asyncio.create_task(
            _auto_first_message(), name=f"sub-{session.session_id}", context=clean_ctx
        )
        self._sub_tasks[session.session_id] = task

        return {"success": True, "session_id": session.session_id, "message": f"子会话 {session.session_id} 已创建并开始执行"}

    # ============================================================
    # 统一消息发送入口
    # ============================================================

    async def ensure_graph_ready(self, session) -> bool:
        """确保会话具备可运行的 Graph；冷加载会话重建完整运行依赖。

        从磁盘冷加载（AgentSession.load）的会话 compiled_graph 为 None 且无
        tools——直接使用会报"Graph 未初始化"。这里按 agent_type 重建
        llm/tools/graph/consumer，并将冷加载的 error 状态重置为可运行，
        使 legacy/cold error Session 的自动恢复入口可达。

        Returns:
            bool: 会话现在可运行（graph 已就绪）
        """
        if getattr(session, "compiled_graph", None) is not None:
            return True
        try:
            from src.core.llm_client import create_startup_llm

            llm = create_startup_llm(
                model_override=session.model_id or None,
                streaming=True,
                model_params=session.model_params,
            )
            tools: list = []
            assembler = getattr(self, "_tool_assembler", None)
            if assembler is not None and hasattr(assembler, "build"):
                from src.agent.definition import get_agent_definition

                agent_def = get_agent_definition(session.agent_type)
                tools = assembler.build(
                    session.agent_type,
                    agent_definition=agent_def,
                    workspace_path=session.workspace_path or "",
                )
            session.setup_graph(llm=llm, tools=tools)
            session.start_consumer()
            if session.status == "error":
                session.status = "completed"
            logger.info(
                "冷加载会话已重建运行依赖: session=%s agent=%s",
                session.session_id, session.agent_type,
            )
            return True
        except Exception:
            logger.exception(
                "重建会话运行依赖失败: session=%s agent=%s",
                getattr(session, "session_id", "?"), getattr(session, "agent_type", "?"),
            )
            return False

    async def send_message_to_session(
        self,
        session_id: str,
        message: str,
        event_callback: Callable[[dict], Awaitable[None]] | None = None,
        max_rounds: int | None = None,
    ) -> dict:
        """
        向任意会话发送消息（统一入口）。

        无论目标是 main session 还是 sub session，
        都通过 session.send_message() 走相同的 graph invoke 流程。

        Args:
            session_id: 目标会话 ID
            message: 消息内容
            event_callback: 可选的流式事件回调
            max_rounds: 可选的最大工具调用轮次

        Returns:
            {"success": bool, "message": str, "reply": str}
        """
        session = self.get_session(session_id)
        if not session:
            return {"success": False, "message": f"未找到会话 {session_id}"}

        # 冷加载/legacy error 会话：先重建运行依赖（编译图检查前先恢复，
        # 否则 error Session 的自动恢复入口不可达）
        if session.compiled_graph is None and not await self.ensure_graph_ready(session):
            return {"success": False, "message": f"会话 {session_id} 无法初始化运行依赖"}

        if session.status == "error":
            # 用户显式重试：重置为可运行状态（error 会话恢复入口）
            session.status = "completed"

        # 如果当前正在 streaming，等待一下（或拒绝）
        if session.status == "streaming":
            return {"success": False, "message": f"会话 {session_id} 正在处理中，请稍后再试"}

        try:
            # 按 session 类型分发默认 callback：
            # - main session → chat 通道（前端 chat WS 可接收流式 token）
            # - sub session → events 通道（sub_ 前缀，与 chat 通道隔离）
            if event_callback is not None:
                cb = event_callback
            else:
                cb = self._make_event_callback(session_id)
            reply = await session.send_message(content=message, event_callback=cb, max_rounds=max_rounds)
            return {"success": True, "message": f"消息已处理", "reply": reply[:500]}
        except Exception as e:
            logger.error(f"向会话 {session_id} 发送消息失败: {e}", exc_info=True)
            return {"success": False, "message": f"消息处理失败: {str(e)}"}

    async def edit_message_to_session(
        self,
        session_id: str,
        message_id: str,
        new_content: str,
        event_callback: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """
        编辑会话中的指定消息并重新发送。

        Args:
            session_id: 目标会话 ID
            message_id: 要编辑的消息 ID（如 "msg_00003"）
            new_content: 编辑后的消息内容
            event_callback: 可选的流式事件回调

        Returns:
            {"success": bool, "message": str, "reply": str}
        """
        session = self.get_session(session_id)
        if not session:
            return {"success": False, "message": f"未找到会话 {session_id}"}

        # 冷加载/legacy error 会话：先重建运行依赖（评审：编译图检查前先恢复）
        if session.compiled_graph is None and not await self.ensure_graph_ready(session):
            return {"success": False, "message": f"会话 {session_id} 无法初始化运行依赖"}

        if session.status == "error":
            # 用户显式重试：重置为可运行状态（error 会话恢复入口）
            session.status = "completed"

        if session.status == "streaming":
            return {"success": False, "message": f"会话 {session_id} 正在流式输出中，无法编辑消息"}

        try:
            if event_callback is not None:
                cb = event_callback
            else:
                cb = self._make_event_callback(session_id)
            reply = await session.edit_message_and_resend(
                message_id=message_id,
                new_content=new_content,
                event_callback=cb,
            )
            return {"success": True, "message": "消息已编辑并重新处理", "reply": reply[:500] if reply else ""}
        except Exception as e:
            logger.error(f"编辑会话 {session_id} 的消息 {message_id} 失败: {e}", exc_info=True)
            return {"success": False, "message": f"编辑消息失败: {str(e)}"}

    # ============================================================
    # 统一消息路由
    # ============================================================

    def _format_agent_header(self, from_session_id: str, task: str = "") -> str:
        """使用可配置模板格式化 Agent 间消息标头。"""
        from src.config import AGENT_MESSAGE_HEADER
        session = self.sessions.get(from_session_id)
        task_info = f" | 任务: {task}" if task else ""
        return AGENT_MESSAGE_HEADER.format(
            source_type="会话",
            source_id=from_session_id,
            task_info=task_info,
        )

    async def route_message(self, from_session_id: str, to_session_id: str, content: str) -> dict:
        """统一消息路由：将消息从 from_session 发送到 to_session。

        所有 session 平等对待，不做方向校验（Main/Sub 区分）。
        仅负责格式化消息头并入队到目标 session。

        Args:
            from_session_id: 发送方 session_id
            to_session_id: 接收方 session_id
            content: 消息内容

        Returns:
            {"success": bool, "message": str}
        """
        from_session = self.get_session(from_session_id)
        if not from_session:
            return {"success": False, "message": f"未找到发送方会话 {from_session_id}"}

        to_session = self.get_session(to_session_id)
        if not to_session:
            return {"success": False, "message": f"未找到目标会话 {to_session_id}"}

        # 冷加载目标会话：先重建运行依赖（评审：agent 间通信的冷恢复入口）
        if to_session.compiled_graph is None and not await self.ensure_graph_ready(to_session):
            return {"success": False, "message": f"目标会话 {to_session_id} 无法初始化运行依赖"}

        # Scope 隔离：校验两个 session 是否在同一棵树内
        from_root = self._get_root_main(from_session_id)
        to_root = self._get_root_main(to_session_id)
        if from_root != to_root or from_root is None:
            return {"success": False, "message": f"跨主会话通信不被允许 ({from_session_id[:8]} 与 {to_session_id[:8]} 不在同一棵树)"}

        # 格式化消息头 + 内容
        task = from_session.task_description[:100] if from_session.task_description else ""
        header = self._format_agent_header(from_session_id, task)
        full_content = f"{header}\n\n{content}"

        # 入队到目标 session（按目标类型选择事件通道：main→chat，sub→events）
        await to_session.enqueue(
            content=full_content,
            priority=1,
            source=f"agent:{from_session_id}",
            source_name=f"agent_{from_session_id}",
            event_callback=(
                self._make_main_auto_callback(to_session_id)
                if to_session.session_type == "main"
                else self._make_event_callback(to_session_id)
            ),
        )

        logger.info(f"{from_session_id[:8]} -> {to_session_id[:8]}: {content[:80]}")
        return {"success": True, "message": f"消息已发送到会话 {to_session_id}"}

    # ============================================================
    # 废弃接口（保留兼容，内部转发到 route_message）
    # ============================================================

    async def send_to_sub(self, session_id: str, message: str) -> dict:
        """[废弃] 向子会话发送消息，请使用 route_message()。"""
        import warnings
        warnings.warn("send_to_sub is deprecated, use route_message() instead", DeprecationWarning, stacklevel=2)
        return await self.route_message(
            from_session_id=self.main_session_id or "",
            to_session_id=session_id,
            content=message,
        )

    async def report_to_main(self, from_session_id: str, content: str, status: str = "progress") -> dict:
        """[废弃] 向主代理上报进展，请使用 route_message()。"""
        import warnings
        warnings.warn("report_to_main is deprecated, use route_message() instead", DeprecationWarning, stacklevel=2)
        return await self.route_message(
            from_session_id=from_session_id,
            to_session_id=self.main_session_id or "",
            content=content,
        )

    async def send_to_main(self, from_session_id: str, message: str) -> dict:
        """[废弃] 向主代理发送消息，请使用 route_message()。"""
        import warnings
        warnings.warn("send_to_main is deprecated, use route_message() instead", DeprecationWarning, stacklevel=2)
        return await self.route_message(
            from_session_id=from_session_id,
            to_session_id=self.main_session_id or "",
            content=message,
        )

    def _make_main_auto_callback(self, session_id: str | None = None) -> Callable[[dict], Awaitable[None]]:
        """为 Agent 间自动触发的 main session 对话创建事件回调。

        事件通过 event_bus 广播，前端能实时看到目标 main session 的自动回复。

        Args:
            session_id: 目标 main session ID，为 None 时回退到 Chat WS 默认 main
        """
        target_session = self.sessions.get(session_id) if session_id else self.get_main_session()
        _sid = target_session.session_id if target_session else ""

        async def callback(event: dict):
            from src.web.event_bus import event_bus
            event["session_id"] = _sid
            event_type = event.get("type", "")
            # 推送到 chat 通道（前端 chat WS 能收到）
            await event_bus.emit_chat(event)
            # 状态事件也推送到 events 通道
            if event_type == "stream_start":
                await event_bus.emit_event({
                    "type": "session_update", "action": "status_changed",
                    "session_id": _sid, "status": "streaming",
                })
            elif event_type == "stream_end":
                await event_bus.emit_event({
                    "type": "session_update", "action": "status_changed",
                    "session_id": _sid, "status": "completed",
                })
                if target_session:
                    serialized = [m for m in target_session.record if is_visible_to_frontend(m)]
                    await event_bus.emit_chat({
                        "type": "chain_end",
                        "messages": serialized,
                        "session_id": _sid,
                    })
        return callback
