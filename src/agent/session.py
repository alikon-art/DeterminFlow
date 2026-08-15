"""

Agent 会话抽象 - 自包含的对话单元



每个 AgentSession 内置 LangChain 消息累积、预编译 Graph、工具列表，

通过 inbox 消息队列 + 消费循环驱动对话。

主/子会话的底层完全一致，差异由上层控制（工具权限、通信工具等）。

"""

import uuid

import json

import time

import asyncio

import logging

from contextlib import asynccontextmanager

from copy import deepcopy

from dataclasses import dataclass, field

from datetime import datetime, timezone

from decimal import Decimal, InvalidOperation

from typing import Callable, Awaitable, Any, TYPE_CHECKING



from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from openai import BadRequestError, ContentFilterFinishReasonError



import src.config as config

from src.config import SESSIONS_DIR, LANGGRAPH_RECURSION_LIMIT, USER_INJECTION_CONFIG_FILE

from src.core.utils import (
    trim_langchain_messages,
    _sanitize_tool_pairs,
    estimate_tokens,
    is_visible_to_frontend,
    message_content_reasoning,
    message_content_text,
)
from src.compression.checker import get_compression_checker
from src.compression.scheduler import get_compression_scheduler

# ============================================================
# 独立文件 I/O 线程池 — 与默认线程池隔离，避免与 LangGraph ToolNode 竞争
# ============================================================
from concurrent.futures import ThreadPoolExecutor

# I/O 线程池大小：从 settings.json 读取，默认 32
# 百级并发 Agent 时每个 session 频繁 async_save → _flush_to_disk，
# 需要足够线程避免 run_in_executor 阻塞事件循环
import os as _os
_IO_POOL_SIZE = int(_os.getenv("IO_THREAD_POOL_SIZE", "32"))
_io_executor = ThreadPoolExecutor(max_workers=_IO_POOL_SIZE, thread_name_prefix="session-io")

# 并发刷盘上限：限制 flush_all 同时进行的 _flush_to_disk 数量
# 防止 100+ session 同时刷盘时 ThreadPoolExecutor 被瞬间塞满
_IO_FLUSH_SEMAPHORE = int(_os.getenv("IO_FLUSH_SEMAPHORE", "16"))

# 刷盘间隔（秒）— 内存缓存模式下的定时刷盘周期
_PERSIST_INTERVAL = 5.0



if TYPE_CHECKING:

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool



logger = logging.getLogger(__name__)


def _resolve_pending_tool_run_id(
    *,
    actual_tool_call_id: str,
    node_run_id: str,
    tool_name: str,
    pending_by_call_id: dict[str, str],
    pending_by_node_run_id: dict[str, str],
    pending_calls: dict[str, dict],
) -> str:
    """Resolve parallel tool completion by stable IDs, never completion order."""
    unique_run_id = pending_by_call_id.pop(actual_tool_call_id, "")
    if not unique_run_id:
        unique_run_id = pending_by_node_run_id.pop(node_run_id, "")
    if not unique_run_id:
        unique_run_id = next(
            (
                run_id
                for run_id, pending_call in pending_calls.items()
                if pending_call.get("name") == tool_name
            ),
            node_run_id,
        )
    for mapping in (pending_by_call_id, pending_by_node_run_id):
        for key, value in list(mapping.items()):
            if value == unique_run_id:
                mapping.pop(key, None)
    return unique_run_id


def _canonical_tool_args(value: Any) -> str:
    """Normalize tool arguments so callbacks can be matched independent of order."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _build_tool_call_slots(
    output: Any,
    streamed_calls: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build model-order tool slots from the complete response with stream fallback."""
    slots: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for position, tool_call in enumerate(getattr(output, "tool_calls", None) or []):
        if isinstance(tool_call, dict):
            index = tool_call.get("index", position)
            call_id = tool_call.get("id")
            name = tool_call.get("name")
            args = tool_call.get("args", {})
        else:
            index = getattr(tool_call, "index", position)
            call_id = getattr(tool_call, "id", None)
            name = getattr(tool_call, "name", None)
            args = getattr(tool_call, "args", {})
        try:
            normalized_index = int(index)
        except (TypeError, ValueError):
            normalized_index = position
        streamed = streamed_calls.get(normalized_index, {})
        slots.append({
            "index": normalized_index,
            "id": str(call_id or streamed.get("id") or ""),
            "name": str(name or streamed.get("name") or ""),
            "args": args if args is not None else streamed.get("args", {}),
        })
        seen_indices.add(normalized_index)

    for index, streamed in streamed_calls.items():
        if index in seen_indices:
            continue
        slots.append({
            "index": index,
            "id": str(streamed.get("id") or ""),
            "name": str(streamed.get("name") or ""),
            "args": streamed.get("args", {}),
        })
    return slots


def _resolve_tool_start_slot(
    event: dict[str, Any],
    slots: list[dict[str, Any]],
    claimed_indices: set[int],
) -> dict[str, Any]:
    """Resolve a tool-start callback to its model slot without callback ordering."""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    tool_input = data.get("input", {})
    candidates = [
        slot for slot in slots
        if int(slot.get("index", -1)) not in claimed_indices
    ]

    explicit_call_id = next(
        (
            str(source.get(key))
            for source in (event, data, metadata)
            for key in ("tool_call_id", "call_id")
            if source.get(key)
        ),
        "",
    )
    if not explicit_call_id and isinstance(tool_input, dict) and tool_input.get("type") == "tool_call":
        explicit_call_id = str(tool_input.get("id") or "")
    if explicit_call_id:
        matched = next(
            (slot for slot in candidates if slot.get("id") == explicit_call_id),
            None,
        )
        if matched is not None:
            claimed_indices.add(int(matched["index"]))
            return matched

    explicit_index: int | None = None
    for source in (event, data, metadata):
        for key in ("tool_call_index", "index"):
            if source.get(key) is None:
                continue
            try:
                explicit_index = int(source[key])
            except (TypeError, ValueError):
                continue
            break
        if explicit_index is not None:
            break
    if explicit_index is not None:
        matched = next(
            (slot for slot in candidates if int(slot.get("index", -1)) == explicit_index),
            None,
        )
        if matched is not None:
            claimed_indices.add(int(matched["index"]))
            return matched

    tool_name = str(event.get("name") or "unknown")
    input_key = _canonical_tool_args(tool_input)
    exact_matches = [
        slot for slot in candidates
        if slot.get("name") == tool_name
        and _canonical_tool_args(slot.get("args", {})) == input_key
    ]
    named_matches = [slot for slot in candidates if slot.get("name") == tool_name]
    if exact_matches:
        matched = exact_matches[0]
    elif named_matches:
        matched = named_matches[0]
    elif candidates:
        matched = candidates[0]
    else:
        matched = {
            "index": len(claimed_indices),
            "id": "",
            "name": tool_name,
            "args": tool_input,
        }
    claimed_indices.add(int(matched["index"]))
    return matched


def _usage_int(value: Any) -> int | None:
    """严格解析供应商 Usage 字段；无效值留给完整性状态处理。"""
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _usage_field(
    sources: list[tuple[str, dict[str, Any]]],
    field: str,
) -> tuple[int | None, str | None]:
    """按来源优先级逐字段取首个有效 Usage 值。"""
    for source_name, values in sources:
        if field not in values:
            continue
        parsed = _usage_int(values[field])
        if parsed is not None:
            return parsed, source_name
    return None, None





# ============================================================

# 消息数据类（inbox 队列的基本单元）

# ============================================================



@dataclass(order=True)

class SessionMessage:

    """Session inbox 中的消息单元。



    priority: 数字越小优先级越高

        0 = 人类消息（最高）

        1 = Agent 间通信消息

        2 = 系统通知

    """

    priority: int

    content: str = field(compare=False)

    source: str = field(compare=False, default="human")           # "human" | "agent:<session_id>" | "system"

    source_name: str = field(compare=False, default="")           # OpenAI name 字段值

    event_callback: Callable[[dict], Awaitable[None]] | None = field(compare=False, default=None)

    max_rounds: int | None = field(compare=False, default=None)

    timestamp: float = field(compare=False, default_factory=time.time)

    # 用于回传结果给调用方的 Future

    result_future: asyncio.Future | None = field(compare=False, default=None, repr=False)


# ============================================================
# 用户消息注入功能
# ============================================================

_injection_config_cache: tuple[float, dict[str, Any]] = (0.0, {"sections": []})


def _load_user_injection_config() -> dict[str, Any]:
    """加载用户消息注入配置（带 mtime 缓存，仅文件变化时重新读取）"""
    global _injection_config_cache
    try:
        if USER_INJECTION_CONFIG_FILE.exists():
            mtime = USER_INJECTION_CONFIG_FILE.stat().st_mtime
            if mtime == _injection_config_cache[0]:
                return _injection_config_cache[1]
            with open(USER_INJECTION_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _injection_config_cache = (mtime, data)
            return data
    except Exception as e:
        logger.warning(f"加载用户消息注入配置失败: {e}")
    return {"sections": []}


def _replace_placeholders(content: str, metadata: dict[str, Any]) -> str:
    """替换内容中的占位符"""
    from datetime import datetime, timezone, timedelta

    # 北京时间 UTC+8
    beijing_tz = timezone(timedelta(hours=8))
    now = datetime.now(beijing_tz)

    # 默认占位符映射
    placeholders: dict[str, str] = {
        "{{timestamp}}": now.isoformat(),
        "{{timezone}}": "Asia/Shanghai (UTC+8)",
        "{{date}}": now.strftime("%Y-%m-%d"),
        "{{time}}": now.strftime("%H:%M:%S"),
        "{{year}}": str(now.year),
        "{{month}}": str(now.month),
        "{{day}}": str(now.day),
        "{{hour}}": str(now.hour),
        "{{minute}}": str(now.minute),
        "{{second}}": str(now.second),
    }

    # 添加元数据中的占位符
    for key, value in metadata.items():
        placeholders[f"{{{{{key}}}}}"] = str(value)

    # 替换占位符
    for placeholder, value in placeholders.items():
        content = content.replace(placeholder, value)

    return content


def _build_injection_content(metadata: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """
    构建用户消息注入内容

    Returns:
        tuple: (注入内容字符串, 注入元信息列表)
    """
    config = _load_user_injection_config()
    sections = config.get("sections", [])

    # 按order排序，只处理启用的sections
    enabled_sections = sorted(
        [s for s in sections if s.get("enabled", True)],
        key=lambda x: x.get("order", 0)
    )

    injection_parts: list[str] = []
    injection_meta: list[dict[str, Any]] = []

    for section in enabled_sections:
        content = section.get("content", "")
        if content:
            # 替换占位符
            replaced_content = _replace_placeholders(content, metadata or {})
            injection_parts.append(replaced_content)

            # 记录注入元信息
            injection_meta.append({
                "name": section.get("name", "unknown"),
                "content": replaced_content,
                "token_estimate": section.get("token_estimate", 0),
            })

    return "\n".join(injection_parts), injection_meta


def _serialize_tool_calls(msg: BaseMessage) -> list[dict] | None:
    """
    将 AIMessage 的 tool_calls 序列化为 OpenAI 标准格式。

    Returns:
        tool_calls 列表（OpenAI 格式），无 tool_calls 时返回 None
    """
    if not hasattr(msg, "tool_calls") or not msg.tool_calls:
        return None
    return [
        {
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
            },
        }
        for tc in msg.tool_calls
    ]


class AgentSession:

    """自包含的 Agent 会话单元。



    既管理对话数据（消息历史、序列化），也承载对话驱动能力

    （Graph 编译、消息发送、流式事件输出）。

    主/子会话使用相同的底层，区别仅在上层注入的工具集和配置。



    对话驱动模型：

    - 外部通过 enqueue() 向 inbox 投递消息

    - 内部 _consume_loop() 持续消费 inbox，逐条调用 _invoke_graph()

    - 正在处理的消息不会被打断，新消息排队等待

    - 人类消息优先于 Agent 间消息

    """



    def __init__(

        self,

        session_type: str = "main",

        parent_id: str | None = None,

        task_description: str = "",

        system_prompt: str = "",

        session_id: str | None = None,

        agent_type: str = "main",

        workspace_path: str | None = None,

        workflow_id: str | None = None,

        task_id: str | None = None,

        runtime_scope: str | None = None,

        model_params: dict[str, Any] | None = None,

        lifecycle_profile: str = "task",

        resource_owner: str = "",

        external_ref: str = "",

        scope_hash: str = "",

    ):

        self.session_id: str = session_id or uuid.uuid4().hex[:8]

        self.session_type: str = session_type

        self.parent_id: str | None = parent_id

        self.agent_type: str = agent_type

        self.workflow_id: str | None = workflow_id

        self.task_id: str | None = task_id

        self.lifecycle_profile: str = lifecycle_profile or "task"

        self.resource_owner: str = resource_owner

        self.external_ref: str = external_ref

        self.scope_hash: str = scope_hash

        if runtime_scope in {"interactive", "workflow"}:
            self.runtime_scope = runtime_scope
        elif session_type == "sub" and workflow_id:
            self.runtime_scope = "workflow"
        elif session_type == "main" and task_description.lstrip().startswith("Workflow:"):
            self.runtime_scope = "workflow"
        else:
            self.runtime_scope = "interactive"

        self.node_id: str = ""  # workflow 节点 ID（由 engine._execute_node 设置）

        self._on_node_complete: Callable | None = None  # workflow 节点完成回调（由 create_sub_session 设置）

        self._on_auto_complete: Callable | None = None  # auto_flow 自然结束回调
        self._auto_flow: bool = False                   # 是否启用自动流转

        self._on_record_append: Callable | None = None  # record 追加回调（由 create_sub_session 注册，用于 workflow 实时推送）

        self._on_reject_upstream: Callable | None = None  # reject_upstream 回调（由 create_sub_session 设置）

        self.status: str = "running"

        self.task_description: str = task_description

        self._system_prompt: str = system_prompt

        self.workspace_path: str | None = workspace_path

        self.created_at: str = datetime.now(timezone.utc).isoformat()

        self.updated_at: str = self.created_at



        # 会话记录（自有格式，完整历史，仅 append，用于持久化和前端 API）
        # 每条消息: {id, type, content, ...}
        self.record: list[dict] = []

        # 消息 ID 计数器（递增序号，从 1 开始）
        self._msg_counter: int = 0

        # 当前会话使用的模型标识（如 "deepseek:deepseek-v4-pro"）
        # 子会话 model=null 时从此字段继承
        self.model_id: str | None = None

        # 会话级模型参数；对话页切换推理强度后随会话持久化。
        self.model_params: dict[str, Any] = dict(model_params or {})

        # Token 使用监控数据（每次 API 调用覆盖最新值，供前端实时展示）
        self.token_usage: dict | None = None

        # Last terminal failure is persisted for authoritative snapshot recovery.
        self.last_error: dict[str, str] | None = None

        # 累计 Token 使用数据（按 model_id 分组，用于工作流统计）
        # key = model_id (e.g. "deepseek:deepseek-v4-pro")
        # value = {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N,
        #          "cached_tokens": N, "reasoning_tokens": N, "call_count": N}
        self._token_usage_cumulative: dict[str, dict[str, int]] = {}

        # 调用级 Token 账本。每条记录对应一次已完成的 LLM 响应；累计值继续
        # 单独保留，以兼容升级前的 Session 和现有实时 UI。
        self._token_usage_calls: list[dict[str, Any]] = []

        # LLM 调用次数计数器
        self._llm_call_count: int = 0

        # LLM 上下文快照（OpenAI 标准格式，可压缩）
        # 持久化时和 record 一同保存，重启时直接加载避免重新压缩
        self.context: dict = {"messages": []}

        # LangChain 消息列表（BaseMessage 格式，用于 graph invoke）
        self.lc_messages: list[BaseMessage] = []

        # Content Exists Risk 诊断：保存触发审查时的完整消息快照
        self._safety_diagnostic_snapshot: list[BaseMessage] | None = None

        if self._system_prompt:
            self._sync_system_prompt_to_messages()



        # 预编译的 Graph 和工具列表

        self.compiled_graph: Any = None

        self.tools: list["BaseTool"] = []



        # 并发保护锁：防止同一 session 被并发 invoke

        self._invoke_lock: asyncio.Lock = asyncio.Lock()



        # === 消息队列 + 消费循环 ===

        self.inbox: asyncio.PriorityQueue[SessionMessage] = asyncio.PriorityQueue()

        self._consumer_task: asyncio.Task | None = None



        self._abort_requested: bool = False

        # 降级结束标记：本轮以"降级"（递归上限/内容过滤/可恢复 Provider 错误）
        # 而非正常完成结束，外层不得再推送 chain_end（避免同一轮既 error 又
        # 成功结束的协议歧义）。每轮 invoke 开始前重置。
        self._degraded: bool = False

        # 调用生命周期独立于 status；准备消息和压缩发生在 stream_start 之前，
        # 终止/删除也必须能识别并停止这一阶段。
        self._invocation_active: bool = False
        self._invocation_task: asyncio.Task | None = None
        self._invocation_done: asyncio.Event = asyncio.Event()
        self._invocation_done.set()
        self._termination_requested: bool = False

        # async_save 节流标志：避免高频保存占用线程池
        self._save_pending: bool = False
        self._save_dirty: bool = False
        self._save_lock: asyncio.Lock = asyncio.Lock()

        # 当前 _invoke_graph 调用的事件回调（供 _emit_event 使用）
        self._current_event_callback: Callable[[dict], Awaitable[None]] | None = None
        # 由 SessionManager 绑定。任何注册会话即使调用方未显式传 callback，
        # 也必须走统一 chat 流协议。
        self._default_event_callback: Callable[[dict], Awaitable[None]] | None = None

        self._logger = logging.getLogger(f"session.{self.session_id}")



    # ============================================================

    # System Prompt Property（自动同步 lc_messages + context.messages）

    # ============================================================

    @property
    def system_prompt(self) -> str:
        """获取当前系统提示词文本。"""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str):
        """设置系统提示词，自动同步到 lc_messages[0] 和 context.messages 首条。"""
        self._system_prompt = value
        self._sync_system_prompt_to_messages()

    def _sync_system_prompt_to_messages(self) -> None:
        """将 self._system_prompt 同步为 lc_messages[0] 的 SystemMessage，
        并同步写入 context.messages 首条。"""
        if not self._system_prompt:
            return

        sys_msg = SystemMessage(content=self._system_prompt)

        # lc_messages：替换已有 SystemMessage 或插入位置 0
        if self.lc_messages and isinstance(self.lc_messages[0], SystemMessage):
            self.lc_messages[0] = sys_msg
        else:
            self.lc_messages.insert(0, sys_msg)

        # context.messages：替换已有 system role 或插入位置 0
        ctx_msgs = self.context.setdefault("messages", [])
        if ctx_msgs and ctx_msgs[0].get("role") == "system":
            ctx_msgs[0] = {"role": "system", "content": self._system_prompt}
        else:
            ctx_msgs.insert(0, {"role": "system", "content": self._system_prompt})

    # ============================================================

    # Graph 初始化

    # ============================================================



    def setup_graph(

        self,

        llm: "BaseChatModel",

        tools: list["BaseTool"],

    ) -> None:

        """

        初始化会话的 Graph 和工具。预编译 graph 并绑定到 session。

        在 session 创建后、第一次 send_message 前调用。



        Args:

            llm: LLM 客户端

            tools: 绑定的工具列表

        """

        from src.core.graph_builder import build_graph

        self.tools = tools

        graph = build_graph(llm=llm, tools=tools)

        self.compiled_graph = graph.compile()

        # 注册到全局定时刷盘管理器
        _persistence_manager.register(self)

        self._logger.info(f"Graph 已编译: {len(tools)} 个工具")



    # ============================================================

    # 消费循环（核心驱动机制）

    # ============================================================



    def start_consumer(self) -> None:

        """启动 inbox 消费循环。在 setup_graph 后调用。"""

        if self._consumer_task and not self._consumer_task.done():

            return  # 已在运行

        self._consumer_task = asyncio.create_task(

            self._consume_loop(), name=f"consumer-{self.session_id}"

        )

        self._logger.info("消费循环已启动")



    async def stop_consumer(self) -> None:

        """停止消费循环。增加超时保护：5s 内未停止则放弃等待，避免 shutdown 卡死。"""

        if self._consumer_task and not self._consumer_task.done():

            self._consumer_task.cancel()

            try:

                await asyncio.wait_for(self._consumer_task, timeout=5.0)

            except (asyncio.CancelledError, asyncio.TimeoutError):

                pass

        self._consumer_task = None



    async def _consume_loop(self) -> None:

        """持续消费 inbox 中的消息，一条处理完再取下一条。

        使用 300s 超时避免永久阻塞：超时后检查 consumer task 是否被取消，
        若未取消则继续等待，防止因异常导致无限挂起。
        """

        self._logger.info("消费循环开始运行")

        _IDLE_TIMEOUT = 300  # 无消息时最长等待秒数

        try:

            while True:

                try:

                    msg: SessionMessage = await asyncio.wait_for(
                        self.inbox.get(), timeout=_IDLE_TIMEOUT
                    )

                except asyncio.TimeoutError:

                    # 超时无消息，检查是否应退出
                    self._logger.debug(f"消费循环 {_IDLE_TIMEOUT}s 无消息，继续等待")
                    continue

                try:

                    reply = await self._process_message(msg)

                    # 如果调用方需要结果回传

                    if msg.result_future and not msg.result_future.done():

                        msg.result_future.set_result(reply)

                except Exception as e:

                    self._logger.error(f"消费消息失败: {e}", exc_info=True)

                    if msg.result_future and not msg.result_future.done():

                        msg.result_future.set_exception(e)

                finally:

                    self.inbox.task_done()

        except asyncio.CancelledError:

            self._logger.info("消费循环已停止")



    @property
    def invocation_active(self) -> bool:
        """是否存在包含消息准备阶段在内的会话调用。"""
        return self._invocation_active

    @property
    def termination_requested(self) -> bool:
        """会话是否已被生命周期管理器永久封闭。"""
        return self._termination_requested

    def request_termination(self) -> None:
        """封闭会话并请求中止当前调用，防止竞态中的新调用继续启动。"""
        self._termination_requested = True
        self._abort_requested = True

    async def cancel_active_invocation(self, timeout: float = 5.0) -> bool:
        """取消并等待当前调用完成清理；返回是否已安全停止。"""
        self.request_termination()
        task = self._invocation_task
        current = asyncio.current_task()
        if task and task is not current and not task.done():
            task.cancel()
        if not self._invocation_active:
            return True
        try:
            await asyncio.wait_for(self._invocation_done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return not self._invocation_active

    @asynccontextmanager
    async def _invocation_scope(self):
        """串行化调用，并在任务取消时恢复完整的会话消息检查点。"""
        async with self._invoke_lock:
            if self._termination_requested:
                raise RuntimeError(f"Session {self.session_id} 已终止，无法继续执行")

            record_checkpoint = deepcopy(self.record)
            lc_checkpoint = list(self.lc_messages)
            context_checkpoint = deepcopy(self.context)
            self._abort_requested = False
            self._invocation_active = True
            self._invocation_task = asyncio.current_task()
            self._invocation_done.clear()
            try:
                yield
            except asyncio.CancelledError:
                self.record = record_checkpoint
                self.lc_messages = lc_checkpoint
                self.context = context_checkpoint
                self._current_event_callback = None
                self.updated_at = datetime.now(timezone.utc).isoformat()
                try:
                    await asyncio.shield(self.async_save())
                except Exception:
                    self._logger.warning("取消调用后保存会话检查点失败", exc_info=True)
                raise
            finally:
                self._invocation_active = False
                self._invocation_task = None
                self._invocation_done.set()

    async def _process_message(self, msg: SessionMessage) -> str:

        """处理单条 inbox 消息：构造 HumanMessage 并 invoke graph。"""

        if self.compiled_graph is None:

            raise RuntimeError(f"Session {self.session_id} 的 Graph 未初始化")



        async with self._invocation_scope():

            return await self._invoke_graph(

                content=msg.content,

                event_callback=msg.event_callback,

                max_rounds=msg.max_rounds,

                source=msg.source,

                source_name=msg.source_name,

            )



    # ============================================================

    # 消息入队接口

    # ============================================================



    async def enqueue(

        self,

        content: str,

        priority: int = 0,

        source: str = "human",

        source_name: str = "",

        event_callback: Callable[[dict], Awaitable[None]] | None = None,

        max_rounds: int | None = None,

        wait_reply: bool = False,

    ) -> str | None:

        """向 inbox 投递消息。



        Args:

            content: 消息文本

            priority: 优先级（0=人类, 1=agent, 2=system）

            source: 来源标识

            source_name: OpenAI name 字段

            event_callback: 流式事件回调

            max_rounds: 最大工具调用轮次

            wait_reply: 是否等待处理完成并返回回复



        Returns:

            wait_reply=True 时返回 assistant 回复文本，否则返回 None

        """

        future = asyncio.get_running_loop().create_future() if wait_reply else None

        msg = SessionMessage(

            priority=priority,

            content=content,

            source=source,

            source_name=source_name,

            event_callback=event_callback or self._default_event_callback,

            max_rounds=max_rounds,

            result_future=future,

        )

        await self.inbox.put(msg)

        self._logger.debug(f"消息已入队: priority={priority}, source={source}, len={len(content)}")



        if future:

            return await future

        return None



    # ============================================================

    # 兼容接口（保留 send_message 供直接调用场景使用）

    # ============================================================



    async def send_message(

        self,

        content: str,

        event_callback: Callable[[dict], Awaitable[None]] | None = None,

        max_rounds: int | None = None,

        source: str = "human",

        source_name: str = "",

        attachments: list[dict[str, str]] | None = None,

    ) -> str:

        """

        直接发送消息（不经过 inbox 队列）。



        适用于调用方自行管理并发控制的场景（如 ws_handlers 的同步等待、

        子会话的 _auto_first_message）。



        新增 source/source_name 参数用于消息来源标识。

        """

        if self.compiled_graph is None:

            raise RuntimeError(f"Session {self.session_id} 的 Graph 未初始化，请先调用 setup_graph()")



        callback = event_callback or self._default_event_callback
        async with self._invocation_scope():

            return await self._invoke_graph(
                content,
                callback,
                max_rounds,
                source,
                source_name,
                attachments,
            )



    async def abort(self) -> dict:
        """
        请求中止当前流式输出。

        设置 _abort_requested 标志位，_invoke_graph 中的 astream_events
        循环会在每次迭代后检查此标志并提前退出。
        """
        self._abort_requested = True
        self._logger.info(f"会话 {self.session_id} 已请求中止")
        return {"success": True, "message": f"会话 {self.session_id} 已请求中止"}

    async def edit_message_and_resend(
        self,
        message_id: str,
        new_content: str,
        event_callback: Callable[[dict], Awaitable[None]] | None = None,
    ) -> str:
        """
        编辑并重发消息：在 record/lc_messages 中定位原始消息，
        截断该消息及之后所有内容，追加编辑后的新用户消息，重新 invoke graph。

        复用 _invoke_graph 流式流程，通过 event_callback 推送事件。
        """
        if self.compiled_graph is None:
            raise RuntimeError(f"Session {self.session_id} 的 Graph 未初始化，请先调用 setup_graph()")

        if self.status == "streaming":
            raise RuntimeError(f"Session {self.session_id} 正在流式输出中，无法编辑消息")

        callback = event_callback or self._default_event_callback
        async with self._invocation_scope():
            # ---- 1. 在 record 中定位目标消息 ----
            target_idx = None
            for i, m in enumerate(self.record):
                if m.get("id") == message_id and m.get("type") == "user":
                    target_idx = i
                    break

            if target_idx is None:
                raise ValueError(f"未找到 ID 为 {message_id} 的用户消息")

            original_attachments = self.record[target_idx].get("attachments")
            retained_attachments = [
                dict(attachment)
                for attachment in original_attachments
                if isinstance(attachment, dict)
                and isinstance(attachment.get("name"), str)
                and isinstance(attachment.get("absolute_path"), str)
                and attachment["absolute_path"] in new_content
            ] if isinstance(original_attachments, list) else []

            # ---- 2. 对齐 lc_messages：统计 record 中到目标为止的 user 消息数 ----
            user_count = sum(1 for m in self.record[:target_idx + 1] if m.get("type") == "user")

            lc_truncate_idx = None
            h_count = 0
            for i, m in enumerate(self.lc_messages):
                if isinstance(m, HumanMessage):
                    h_count += 1
                    if h_count == user_count:
                        lc_truncate_idx = i
                        break

            if lc_truncate_idx is None:
                raise RuntimeError("无法在 lc_messages 中找到对应的 HumanMessage")

            # ---- 3. 截断 ----
            original_record_len = len(self.record)
            original_lc_len = len(self.lc_messages)
            self.record = self.record[:target_idx]
            self.lc_messages = self.lc_messages[:lc_truncate_idx]
            self._logger.info(
                f"会话 {self.session_id} 编辑消息 {message_id}: "
                f"record {original_record_len}→{len(self.record)}, "
                f"lc_messages {original_lc_len}→{len(self.lc_messages)}"
            )

            # 落盘截断状态
            self._sync_context_snapshot()
            self.updated_at = datetime.now(timezone.utc).isoformat()
            await self.async_save()

            # ---- 4. 重新执行 graph（_invoke_graph 内部会追加新 HumanMessage + record 条目） ----
            return await self._invoke_graph(
                new_content,
                callback,
                max_rounds=None,
                attachments=retained_attachments,
            )

    async def compress(self) -> dict:
        """
        手动触发上下文压缩。

        仅在会话非 streaming 状态时执行，使用 _invoke_lock 保护
        防止与 send_message 并发冲突。

        手动压缩强制执行 FullCompact 策略，不受阈值限制。
        """
        if self.status == "streaming":
            return {"success": False, "message": "会话正在流式输出中，无法压缩"}

        async with self._invocation_scope():
            await self._check_and_compress_messages(force_full=True)
            self._logger.info(f"会话 {self.session_id} 手动压缩完成")
            return {"success": True, "message": "压缩完成"}

    async def _emit_event(self, event: dict, await_send: bool = False):
        """同步推送事件（直接 await 回调，不创建 Task）。

        事件回调内部通过 EventBus 队列投递（put_nowait），
        整个链路没有实际 I/O，直接 await 不会阻塞事件循环。
        """
        cb = self._current_event_callback
        if not cb:
            return

        await cb(event)

    async def _finish_tool_run(
        self,
        *,
        event_callback: Callable[[dict], Awaitable[None]] | None,
        tool_name: str,
        result: str,
        run_id: str,
        tool_status: str,
        tool_call_id: str,
    ) -> None:
        """Emit and persist one terminal tool event through the shared protocol."""
        if event_callback:
            await self._emit_event({
                "type": "tool_end",
                "session_id": self.session_id,
                "name": tool_name,
                "result": result[:2000],
                "run_id": run_id,
                "status": tool_status,
            })
        tool_msg = ToolMessage(
            content=result,
            tool_call_id=tool_call_id,
            status="error" if tool_status in {"failed", "cancelled"} else "success",
            additional_kwargs={"tool_status": tool_status},
        )
        await self._append_to_record(tool_msg)
        self._sync_context_snapshot()
        self.updated_at = datetime.now(timezone.utc).isoformat()
        await self.async_save()

    def _visible_record(self) -> list[dict]:
        """Return an isolated authoritative history for terminal events."""
        return [
            deepcopy(message)
            for message in self.record
            if is_visible_to_frontend(message)
        ]

    async def _finish_pre_stream_abort(self, phase: str) -> str:
        """Rollback a preparation-stage abort without creating a stream lifecycle."""
        self._logger.info(f"会话 {self.session_id} 在{phase}被手动中止")
        self.status = "running" if self.session_type == "main" else "completed"
        await self._rollback_on_error()
        self._current_event_callback = None
        return self.get_last_assistant_message()

    @staticmethod
    def _is_recoverable_error(exc: BaseException) -> bool:
        """判断异常是否为明确可恢复的 Provider/网络错误。

        仅对可恢复的瞬态故障（连接/超时/限流/5xx/流中断）降级为可重试；
        无效参数（BadRequestError）、配置错误与代码缺陷属于不可恢复错误，
        必须终态失败，不得伪装成瞬态故障。

        Returns:
            bool: 可恢复返回 True
        """
        from openai import (
            APIConnectionError,
            APITimeoutError,
            APIStatusError,
            RateLimitError,
        )

        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code >= 500:
            return True
        # 流式中断/重连类错误（openai 无专用异常，按消息特征识别）
        message = str(exc or "").lower()
        if any(token in message for token in (
            "connection reset", "connection refused", "eof", "broken pipe",
            "temporarily unavailable", "timed out", "timeout", "5xx",
        )):
            return True
        return False

    async def _invoke_graph(

        self,

        content: str,

        event_callback: Callable[[dict], Awaitable[None]] | None,

        max_rounds: int | None,

        source: str = "human",

        source_name: str = "",

        attachments: list[dict[str, str]] | None = None,

    ) -> str:

        """内部：执行一轮 graph invoke。"""

        # 每轮开始重置降级结束标记（上一轮降级不得影响本轮结束事件判定）
        self._degraded = False

        # ---- 非阻塞事件推送：fire-and-forget ----
        # 高并发时 event_callback 会经过 event_bus.emit 串行发送到所有 WS 客户端，
        # 如果 await 等待发送完成，LLM 流式输出速度会被 WS 发送速度制约。
        # 事件通过 EventBus 队列投递（put_nowait），不再需要 gather 等待
        self._current_event_callback = event_callback

        self._logger.debug(
            "[SESSION] _invoke_graph 开始: session=%s, agent=%s, max_rounds=%s, source=%s",
            self.session_id, self.agent_type,
            max_rounds, source,
        )

        # 运行时上下文注入（工具通过 get_session_context() 读取）
        from src.session.context import set_session_context
        set_session_context(
            session_id=self.session_id,
            workspace_path=self.workspace_path or "",
            parent_id=self.parent_id,
            agent_type=self.agent_type,
            workflow_id=self.workflow_id or "",
            task_id=self.task_id or "",
            lifecycle_profile=self.lifecycle_profile,
            resource_owner=self.resource_owner,
            external_ref=self.external_ref,
            scope_hash=self.scope_hash,
            on_node_complete=self._on_node_complete,
            on_reject_upstream=self._on_reject_upstream,
        )

        if max_rounds is not None:
            rounds = max_rounds
        else:
            # 未显式指定时，从 agent definition 的 max_turns 获取
            from src.agent.definition import get_agent_definition
            agent_def = get_agent_definition(self.agent_type)
            rounds = agent_def.max_turns if agent_def else config.MAX_TOOL_ROUNDS



        # 构造 HumanMessage，附加 name 和 source 元数据

        msg_kwargs: dict[str, Any] = {}

        if source_name:

            msg_kwargs["name"] = source_name

        if source != "human":

            msg_kwargs["additional_kwargs"] = {"source": source}

        # 用户消息注入：自动注入元信息到用户消息头部
        injection_content, injection_meta = _build_injection_content()
        if injection_content:
            # 将注入内容添加到用户消息头部，使用单尖括号标记
            content = f"<SYSTEM_INJECTION>\n{injection_content}\n<USER_MESSAGE>\n{content}"

            # 记录注入元信息到消息元数据
            if "additional_kwargs" not in msg_kwargs:
                msg_kwargs["additional_kwargs"] = {}
            msg_kwargs["additional_kwargs"]["injection_meta"] = injection_meta

        human_msg = HumanMessage(content=content, **msg_kwargs)

        self.lc_messages.append(human_msg)

        # 序列化消息也附加 name/source

        add_extra = {}

        if source_name:

            add_extra["name"] = source_name

        if source != "human":

            add_extra["source"] = source

        if attachments:

            add_extra["attachments"] = deepcopy(attachments)

        # 序列化消息也附加注入元信息
        if injection_meta:
            add_extra["injection_meta"] = injection_meta

        await self.add_message("user", content, **add_extra)

        if self._abort_requested:
            return await self._finish_pre_stream_abort("准备阶段")



        # 压缩检查（API调用前，先于截断执行）
        await self._check_and_compress_messages()

        if self._abort_requested:
            return await self._finish_pre_stream_abort("压缩阶段")

        # 截断过长上下文（压缩后的兜底机制）
        self.lc_messages = trim_langchain_messages(self.lc_messages, config.MAX_CONTEXT_TOKENS)

        # 构建初始状态

        initial_state = {

            "messages": list(self.lc_messages),

            "session_id": self.session_id,

            "status": "running",

            "remaining_rounds": rounds,

            "metadata": {"max_rounds": rounds},

            "agent_type": self.agent_type,

        }

        if self._abort_requested:
            return await self._finish_pre_stream_abort("流开始前")



        abort_triggered = False



        # 通知流开始

        self.last_error = None
        self.status = "streaming"

        if event_callback:

            await event_callback({
                "type": "stream_start",
                "session_id": self.session_id,
                "baseline_record_length": len(self.record),
            })



        tool_calls_pending: dict[str, dict] = {}

        final_messages = None
        tool_rounds_executed = 0  # 本轮工具调用次数（用于错误追踪）
        incremental_saved_count = 0  # 流式循环中已增量保存到 record 的消息数
        tool_call_streaming: dict[int, dict] = {}  # index → {id, name, args, complete}
        tool_delta_count = 0  # 已发送 tool_call_delta 事件的 tool 索引计数
        tool_call_delta_slots: list[dict[str, Any]] = []
        claimed_tool_call_indices: set[int] = set()
        pending_tool_run_ids_by_call_id: dict[str, str] = {}
        pending_tool_run_ids_by_node_run_id: dict[str, str] = {}
        llm_call_started_at: dict[str, str] = {}

        try:

            async for event in self.compiled_graph.astream_events(

                initial_state,

                config={"recursion_limit": LANGGRAPH_RECURSION_LIMIT},

                version="v2",

            ):

                # 每次迭代检查是否请求了中止

                if self._abort_requested:

                    self._logger.info(f"会话 {self.session_id} 流式输出被手动中止")

                    abort_triggered = True

                    break

                kind = event.get("event", "")



                if kind == "on_chat_model_start":
                    run_id = str(event.get("run_id", ""))
                    if run_id:
                        llm_call_started_at[run_id] = datetime.now(timezone.utc).isoformat()

                elif kind == "on_chat_model_stream":

                    chunk = event.get("data", {}).get("chunk")

                    if chunk:
                        # 处理思维链内容（reasoning_content）
                        # DeepSeek V4 在流式响应中直接返回 reasoning_content 字段（不在 additional_kwargs 中）
                        reasoning_content = None
                        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                            reasoning_content = chunk.reasoning_content
                        elif hasattr(chunk, "additional_kwargs"):
                            reasoning_content = chunk.additional_kwargs.get("reasoning_content")
                        if not reasoning_content:
                            reasoning_content = message_content_reasoning(
                                getattr(chunk, "content", None)
                            )

                        if reasoning_content and event_callback:
                            await self._emit_event({
                                "type": "reasoning_token",
                                "session_id": self.session_id,
                                "content": reasoning_content,
                            })

                        # 处理普通内容
                        if hasattr(chunk, "content") and chunk.content:

                            token = message_content_text(chunk.content)

                            if token and event_callback:

                                await self._emit_event({

                                    "type": "token",

                                    "session_id": self.session_id,

                                    "content": token,

                                })

                        # 处理 tool_call 流式增量（LLM 逐字生成工具调用参数）
                        if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                            for tc_chunk in chunk.tool_call_chunks:
                                idx = getattr(tc_chunk, "index", 0) if hasattr(tc_chunk, "index") else 0
                                tc_id = getattr(tc_chunk, "id", None) if hasattr(tc_chunk, "id") else None
                                tc_name = getattr(tc_chunk, "name", None) if hasattr(tc_chunk, "name") else None
                                tc_args_delta = getattr(tc_chunk, "args", "") if hasattr(tc_chunk, "args") else ""

                                if idx not in tool_call_streaming:
                                    tool_call_streaming[idx] = {
                                        "id": tc_id,
                                        "name": tc_name or "",
                                        "args": "",
                                        "complete": False,
                                    }
                                    tool_delta_count += 1
                                else:
                                    if tc_id and not tool_call_streaming[idx]["id"]:
                                        tool_call_streaming[idx]["id"] = tc_id
                                    if tc_name and not tool_call_streaming[idx]["name"]:
                                        tool_call_streaming[idx]["name"] = tc_name

                                if tc_args_delta:
                                    tool_call_streaming[idx]["args"] += tc_args_delta

                                if event_callback:
                                    await self._emit_event({
                                        "type": "tool_call_delta",
                                        "session_id": self.session_id,
                                        "index": idx,
                                        "id": tc_id,
                                        "name": tc_name,
                                        "args_delta": tc_args_delta,
                                    })



                elif kind == "on_chat_model_end":
                    run_id = str(event.get("run_id", ""))
                    if run_id and any(
                        item.get("run_id") == run_id
                        for item in self._token_usage_calls
                    ):
                        llm_call_started_at.pop(run_id, None)
                        continue
                    # LLM 完成生成：提取完整 AIMessage（含 tool_calls）追加到 record
                    output = event.get("data", {}).get("output")
                    if output and hasattr(output, "content"):
                        # 从 DeepSeek 等模型的流式响应中提取 reasoning_content
                        # reasoning_content 在 on_chat_model_stream 中已逐步收集，
                        # 此处从最终 AIMessage 的 additional_kwargs 提取完整版本
                        await self._append_to_record(output)
                        incremental_saved_count += 1

                        # 提取 API token usage 并构建监控数据
                        await self._extract_and_broadcast_token_usage(
                            output,
                            event_callback,
                            call_timestamp=llm_call_started_at.pop(run_id, None),
                            run_id=run_id or None,
                        )

                        self._sync_context_snapshot()
                        self.updated_at = datetime.now(timezone.utc).isoformat()
                        await self.async_save()
                        # 保存模型 tool call 的稳定 ID/index，用于把并行 tool_end
                        # 精确关联回对应的实时气泡，而不是依赖完成顺序。
                        tool_call_delta_slots = _build_tool_call_slots(
                            output,
                            tool_call_streaming,
                        )
                        tool_call_streaming.clear()
                        claimed_tool_call_indices.clear()

                elif kind == "on_tool_start":

                    tool_input = event.get("data", {}).get("input", {})

                    tool_name = event.get("name", "unknown")

                    node_run_id = str(event.get("run_id", "") or "")
                    slot = _resolve_tool_start_slot(
                        event,
                        tool_call_delta_slots,
                        claimed_tool_call_indices,
                    )
                    model_call_id = str(slot.get("id") or "")
                    unique_run_id = model_call_id or f"{node_run_id}_{uuid.uuid4().hex[:8]}"
                    if model_call_id:
                        pending_tool_run_ids_by_call_id[model_call_id] = unique_run_id
                    if node_run_id:
                        pending_tool_run_ids_by_node_run_id[node_run_id] = unique_run_id

                    tool_rounds_executed += 1

                    tool_calls_pending[unique_run_id] = {

                        "name": tool_name,

                        "args": tool_input if isinstance(tool_input, dict) else {},

                        "tool_call_id": model_call_id,

                    }

                    if event_callback:

                        await self._emit_event({

                            "type": "tool_start",

                            "session_id": self.session_id,

                            "name": tool_name,

                            "args": tool_input if isinstance(tool_input, dict) else {},

                            "run_id": unique_run_id,

                            "index": int(slot.get("index", 0)),

                        })



                elif kind == "on_tool_error":

                    node_run_id = str(event.get("run_id", "") or "")
                    data = event.get("data", {})
                    if not isinstance(data, dict):
                        data = {}
                    actual_tool_call_id = str(data.get("tool_call_id") or "")
                    event_tool_name = str(event.get("name", "unknown"))
                    unique_run_id = _resolve_pending_tool_run_id(
                        actual_tool_call_id=actual_tool_call_id,
                        node_run_id=node_run_id,
                        tool_name=event_tool_name,
                        pending_by_call_id=pending_tool_run_ids_by_call_id,
                        pending_by_node_run_id=pending_tool_run_ids_by_node_run_id,
                        pending_calls=tool_calls_pending,
                    )
                    pending = tool_calls_pending.pop(unique_run_id, None)
                    tool_name = pending["name"] if pending else event_tool_name
                    error = data.get("error", "工具执行失败")
                    result_str = str(error)
                    actual_tool_call_id = actual_tool_call_id or str(
                        (pending or {}).get("tool_call_id")
                        or node_run_id
                        or unique_run_id
                    )
                    await self._finish_tool_run(
                        event_callback=event_callback,
                        tool_name=tool_name,
                        result=result_str,
                        run_id=unique_run_id,
                        tool_status="failed",
                        tool_call_id=actual_tool_call_id,
                    )
                    incremental_saved_count += 1

                elif kind == "on_tool_end":

                    node_run_id = str(event.get("run_id", "") or "")

                    output = event.get("data", {}).get("output", "")

                    if hasattr(output, "content"):

                        result_str = output.content

                    elif isinstance(output, str):

                        result_str = output

                    else:

                        result_str = str(output)

                    actual_tool_call_id = str(getattr(output, "tool_call_id", "") or "")
                    event_tool_name = str(event.get("name", "unknown"))
                    unique_run_id = _resolve_pending_tool_run_id(
                        actual_tool_call_id=actual_tool_call_id,
                        node_run_id=node_run_id,
                        tool_name=event_tool_name,
                        pending_by_call_id=pending_tool_run_ids_by_call_id,
                        pending_by_node_run_id=pending_tool_run_ids_by_node_run_id,
                        pending_calls=tool_calls_pending,
                    )

                    pending = tool_calls_pending.pop(unique_run_id, None)

                    tool_name = pending["name"] if pending else event.get("name", "unknown")
                    raw_tool_status = str(getattr(output, "status", "") or "").lower()
                    tool_status = (
                        "failed"
                        if raw_tool_status in {"error", "failed", "failure"}
                        else "cancelled"
                        if raw_tool_status in {"cancelled", "canceled", "aborted"}
                        else "completed"
                    )



                    actual_tool_call_id = actual_tool_call_id or str(
                        (pending or {}).get("tool_call_id")
                        or node_run_id
                        or unique_run_id
                    )
                    await self._finish_tool_run(
                        event_callback=event_callback,
                        tool_name=tool_name,
                        result=result_str,
                        run_id=unique_run_id,
                        tool_status=tool_status,
                        tool_call_id=actual_tool_call_id,
                    )
                    incremental_saved_count += 1

                elif kind == "on_chain_end":
                    # [事件层] 仅捕获根图级事件（包含完整 messages 状态），
                    # 过滤节点级事件（仅包含该节点新增的消息子集，会导致 _sanitize_tool_pairs 误判）
                    event_tags = event.get("tags", [])
                    # 根图事件无 langgraph_node 标签，节点级事件有
                    # tags 可能是 list（如 ["langgraph_node"]) 或 dict
                    if isinstance(event_tags, dict) and event_tags.get("langgraph_node"):
                        continue
                    elif isinstance(event_tags, list) and "langgraph_node" in event_tags:
                        continue

                    output = event.get("data", {}).get("output")

                    if isinstance(output, dict) and "messages" in output:

                        final_messages = output["messages"]



        except GraphRecursionError:
            # 业务层追踪：打印上下文信息而非代码堆栈
            self._logger.warning(
                f"会话 {self.session_id} 达到递归上限: "
                f"limit={LANGGRAPH_RECURSION_LIMIT}, "
                f"agent={self.agent_type}, "
                f"task={self.task_description[:100] if self.task_description else '无'}, "
                f"tool_rounds={tool_rounds_executed}, "
                f"msg_count={len(self.lc_messages)}, "
                f"record_len={len(self.record)}"
            )

            # 追加递归上限提示消息到 record（仅前端展示，不进入 lc_messages）
            await self._add_display_message(
                "recursion_limit_reached",
                content=f"当前对话已达递归上限（已执行 {tool_rounds_executed} 次工具调用），部分结果可能不完整。您可以继续发送消息继续对话。",
                tool_rounds=tool_rounds_executed,
                limit=LANGGRAPH_RECURSION_LIMIT,
            )

            # 保持会话状态为 running，允许用户继续操作
            self.status = "running"
            # 降级结束标记：外层不得再推送 chain_end（避免"既 error 又成功"歧义）
            self._degraded = True
            await self._rollback_on_error()

            # 事件已通过非阻塞队列投递，无需等待

            if event_callback:
                try:
                    await event_callback({"type": "stream_end", "session_id": self.session_id})
                except Exception:
                    pass

            # 返回空字符串，不抛出异常，让 ws_handlers 正常推送 chain_end
            return ""

        except BadRequestError as e:
            error_msg = str(e)
            if "Content Exists Risk" in error_msg:
                # Content Exists Risk 专项处理：保存消息快照、追加警告 record、不中断会话
                self._logger.warning(
                    f"会话 {self.session_id} 触发 Content Exists Risk 审查拦截 | "
                    f"agent={self.agent_type}"
                )

                # 保存触发审查时的完整消息快照（用于后续诊断）
                self._safety_diagnostic_snapshot = list(self.lc_messages)

                # 追加警告消息到 record（仅前端展示，不进入 lc_messages）
                await self._add_display_message(
                    "content_safety_warning",
                    content="您的请求被 DeepSeek 安全审查拦截（Content Exists Risk），可能是消息内容触发了审查机制。",
                    detail="您可以点击下方按钮运行详细诊断，定位触发审查的具体消息类别。",
                    session_id=self.session_id,
                )

                # 保持会话状态为 running，允许用户继续操作
                self.status = "running"
                await self._rollback_on_error(rollback_record=False)

                # 事件已通过非阻塞队列投递，无需等待

                if event_callback:
                    try:
                        await event_callback({
                            "type": "stream_end",
                            "session_id": self.session_id,
                        })
                    except Exception:
                        pass

                # 返回空字符串，不抛出异常，让 ws_handlers 正常推送 chain_end
                return ""
            else:
                # 其他 BadRequestError，按通用异常处理
                self._logger.debug(
                    "[SESSION] _invoke_graph BadRequestError: session=%s, error=%s",
                    self.session_id, type(e).__name__,
                )
                self._logger.error(f"Graph invoke BadRequestError: {e}", exc_info=True)
                error_message = self._record_terminal_error(e)
                self.status = "error"
                await self._rollback_on_error(sanitize_pairs=True, sync_snapshot=False)
                if event_callback:
                    try:
                        await event_callback({
                            "type": "error",
                            "session_id": self.session_id,
                            "message": error_message,
                            "terminal": True,
                            "messages": self._visible_record(),
                        })
                    except Exception:
                        pass
                raise

        except ContentFilterFinishReasonError as e:
            # 流式响应被内容过滤中断（模型生成过程中被审查拦截）
            self._logger.warning(
                f"会话 {self.session_id} 触发流式内容过滤拦截 | "
                f"agent={self.agent_type} | "
                f"error={e}"
            )

            # 保存触发审查时的完整消息快照（用于后续诊断）
            self._safety_diagnostic_snapshot = list(self.lc_messages)

            # 追加警告消息到 record（仅前端展示，不进入 lc_messages）
            await self._add_display_message(
                "content_filter_warning",
                content="AI 生成内容被安全审查中途拦截（Content Filter），可能是生成的内容触发了审查机制。",
                detail=f"错误详情: {e}",
                session_id=self.session_id,
            )

            # 保持会话状态为 running，允许用户继续操作
            self.status = "running"
            # 降级结束标记：外层不得再推送 chain_end
            self._degraded = True
            await self._rollback_on_error(rollback_record=False)

            if event_callback:
                try:
                    await event_callback({
                        "type": "stream_end",
                        "session_id": self.session_id,
                    })
                except Exception:
                    pass

            # 返回空字符串，不抛出异常
            return ""

        except Exception as e:

            self._logger.debug(
                "[SESSION] _invoke_graph 异常: session=%s, error=%s",
                self.session_id, type(e).__name__,
            )

            if self._is_recoverable_error(e):
                # 明确可恢复的 Provider/网络错误：降级为可重试（不伪装成功，
                # 不发 chain_end），会话保持可运行，用户可继续/重试。
                self._logger.warning(
                    "会话 %s 遭遇可恢复错误（%s: %s），降级为可重试",
                    self.session_id, type(e).__name__, e,
                )
                error_message = self._record_terminal_error(e)
                self.status = "running"
                self._degraded = True
                await self._rollback_on_error(sanitize_pairs=True)
                if event_callback:
                    try:
                        await event_callback({
                            "type": "error",
                            "session_id": self.session_id,
                            "message": f"{error_message}（可恢复，请重试）",
                            "terminal": False,
                            "recoverable": True,
                        })
                    except Exception:
                        pass
                return ""

            self._logger.error(f"Graph invoke 错误: {e}", exc_info=True)

            error_message = self._record_terminal_error(e)
            self.status = "error"

            await self._rollback_on_error(sanitize_pairs=True)

            # 事件已通过非阻塞队列投递，无需等待

            if event_callback:
                try:
                    await event_callback({
                        "type": "error",
                        "session_id": self.session_id,
                        "message": error_message,
                        "terminal": True,
                        "messages": self._visible_record(),
                    })
                except Exception:
                    pass  # event_callback 可能因 WS 断线失败，不影响主流程

            raise



        # 中止处理：回滚用户消息（与其他错误路径保持一致，避免污染后续对话上下文）
        if abort_triggered:
            await self._rollback_on_error()

        # 同步消息（先清理不完整的 tool_calls/tool 配对，避免下轮 API 400 错误）

        if final_messages:

            final_messages = _sanitize_tool_pairs(final_messages)

            await self._sync_messages(final_messages, skip_count=incremental_saved_count)

        # [兜底层] 对 self.lc_messages 最终清理，确保无论 final_messages 是否可用，
        # 都不会残留悬空的 tool_calls 配对
        self.lc_messages = _sanitize_tool_pairs(self.lc_messages)



        # 恢复状态

        if self.status == "streaming":

            self.status = "running" if self.session_type == "main" else "completed"



        self.updated_at = datetime.now(timezone.utc).isoformat()

        msg_count = len(self.record) if self.record else 0
        self._logger.debug(
            "[SESSION] _invoke_graph 正常完成: session=%s, msg_count=%s",
            self.session_id, msg_count,
        )
        await self.async_save()



        # 事件已通过非阻塞队列投递，各 WS 连接的独立消费者负责按序发送。
        # chain_end 在 stream_end 后推送，消费者 FIFO 保证顺序。

        # 通知流结束（必须在 _sync_messages 之后，
        # 因为 event_callback 中可能读取 self.record 构造 chain_end，
        # 需要确保 record 已包含本轮完整的对话内容）

        if event_callback:

            await event_callback({"type": "stream_end", "session_id": self.session_id})

        # 清理当前调用的上下文
        self._current_event_callback = None

        return self.get_last_assistant_message()

    def _record_terminal_error(self, error: Exception) -> str:
        """Persist one user-safe terminal error and return its display message."""
        from src.core.model_manager import get_model_manager
        from src.core.provider_errors import present_session_error

        provider_config = None
        if self.model_id and ":" in self.model_id:
            provider_id = self.model_id.split(":", 1)[0]
            try:
                provider_config = get_model_manager().get_provider(provider_id)
            except (OSError, TypeError, ValueError):
                provider_config = None

        presented = present_session_error(error, provider_config)
        self.last_error = {
            "code": presented.code,
            "message": presented.message,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        return presented.message

    async def _rollback_on_error(
        self,
        *,
        rollback_record: bool = True,
        sanitize_pairs: bool = False,
        sync_snapshot: bool = True,
    ) -> None:
        """
        统一的错误路径清理方法。

        Args:
            rollback_record: 是否同步回滚 self.record 中的末尾用户消息
            sanitize_pairs: 是否清理悬空的 tool_calls/tool 配对
            sync_snapshot: 是否同步上下文快照
        """
        if self.lc_messages and isinstance(self.lc_messages[-1], HumanMessage):
            self.lc_messages.pop()
        if rollback_record and self.record and self.record[-1].get("type") == "user":
            self.record.pop()
        if sanitize_pairs:
            self.lc_messages = _sanitize_tool_pairs(self.lc_messages)
        if sync_snapshot:
            self._sync_context_snapshot()
        self.updated_at = datetime.now(timezone.utc).isoformat()
        await self.async_save()

    def _compute_token_estimates(self) -> dict:
        """使用 estimate_tokens 计算会话的 token 估算值。

        复用与压缩系统相同的估算方式：
        - system_prompt_tokens: 系统提示词的估算 token 数
        - tool_result_tokens: 所有 ToolMessage 内容的估算 token 数
        - total_tokens: 所有 lc_messages（含 SystemMessage）的估算 token 数

        Returns:
            dict: {"system_prompt_tokens", "tool_result_tokens", "total_tokens"}
        """
        system_prompt_tokens = estimate_tokens(self._system_prompt) if self._system_prompt else 0
        tool_result_tokens = 0
        total_tokens = 0

        for msg in self.lc_messages:
            content = message_content_text(msg.content)

            # 统计工具结果 token
            if isinstance(msg, ToolMessage):
                tool_result_tokens += estimate_tokens(content)

            # 统计总 token（包括系统提示词）
            total_tokens += estimate_tokens(content)

            # 统计 tool_calls 参数 token
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    args_str = str(tc.get("args", ""))
                    total_tokens += estimate_tokens(args_str)

        return {
            "system_prompt_tokens": system_prompt_tokens,
            "tool_result_tokens": tool_result_tokens,
            "total_tokens": total_tokens,
        }

    async def _extract_and_broadcast_token_usage(
        self,
        output,
        event_callback,
        call_timestamp: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """从 LLM 响应中提取 token usage 数据并广播给前端。

        Args:
            output: LangChain AIMessage 对象
            event_callback: 事件回调函数
            call_timestamp: 请求开始时间；缺失时回退为响应完成时间
            run_id: LangGraph 模型调用 ID，用于事件重放去重
        """
        try:
            if run_id and any(
                item.get("run_id") == run_id for item in self._token_usage_calls
            ):
                return

            # LangChain 可能同时提供 response_metadata 和 usage_metadata。
            # 两者描述同一次调用，必须逐字段补齐，不能整包覆盖或相加。
            response_usage: dict[str, Any] = {}
            resp_meta = getattr(output, "response_metadata", None) or {}
            token_usage = (
                resp_meta.get("token_usage", {})
                if isinstance(resp_meta, dict)
                else {}
            )
            if isinstance(token_usage, dict):
                for source_field, target_field in (
                    ("prompt_tokens", "prompt_tokens"),
                    ("completion_tokens", "completion_tokens"),
                    ("total_tokens", "total_tokens"),
                ):
                    if source_field in token_usage:
                        response_usage[target_field] = token_usage[source_field]
                prompt_details = token_usage.get("prompt_tokens_details", {})
                if "prompt_cache_hit_tokens" in token_usage:
                    response_usage["cached_tokens"] = token_usage[
                        "prompt_cache_hit_tokens"
                    ]
                elif (
                    isinstance(prompt_details, dict)
                    and "cached_tokens" in prompt_details
                ):
                    response_usage["cached_tokens"] = prompt_details["cached_tokens"]
                elif "cached_tokens" in token_usage:
                    response_usage["cached_tokens"] = token_usage["cached_tokens"]
                completion_details = token_usage.get("completion_tokens_details", {})
                if "reasoning_tokens" in token_usage:
                    response_usage["reasoning_tokens"] = token_usage[
                        "reasoning_tokens"
                    ]
                elif (
                    isinstance(completion_details, dict)
                    and "reasoning_tokens" in completion_details
                ):
                    response_usage["reasoning_tokens"] = completion_details[
                        "reasoning_tokens"
                    ]

            metadata_usage: dict[str, Any] = {}
            usage_meta = getattr(output, "usage_metadata", None) or {}
            if isinstance(usage_meta, dict):
                for source_field, target_field in (
                    ("input_tokens", "prompt_tokens"),
                    ("output_tokens", "completion_tokens"),
                    ("total_tokens", "total_tokens"),
                ):
                    if source_field in usage_meta:
                        metadata_usage[target_field] = usage_meta[source_field]
                input_details = usage_meta.get("input_token_details", {})
                if isinstance(input_details, dict) and "cache_read" in input_details:
                    metadata_usage["cached_tokens"] = input_details["cache_read"]
                output_details = usage_meta.get("output_token_details", {})
                if isinstance(output_details, dict) and "reasoning" in output_details:
                    metadata_usage["reasoning_tokens"] = output_details["reasoning"]

            sources = [
                ("response_metadata.token_usage", response_usage),
                ("usage_metadata", metadata_usage),
            ]
            normalized_usage: dict[str, int] = {}
            usage_sources: dict[str, str] = {}
            missing_usage_fields: list[str] = []
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_tokens",
                "reasoning_tokens",
            ):
                value, source_name = _usage_field(sources, field)
                if value is None:
                    if field in {"prompt_tokens", "completion_tokens"}:
                        missing_usage_fields.append(field)
                    continue
                normalized_usage[field] = value
                usage_sources[field] = source_name

            normalized_usage.setdefault("prompt_tokens", 0)
            normalized_usage.setdefault("completion_tokens", 0)
            normalized_usage.setdefault("cached_tokens", 0)
            normalized_usage.setdefault("reasoning_tokens", 0)
            if "total_tokens" not in normalized_usage:
                normalized_usage["total_tokens"] = (
                    normalized_usage["prompt_tokens"]
                    + normalized_usage["completion_tokens"]
                )
                if not missing_usage_fields:
                    usage_sources["total_tokens"] = "derived"
            usage_errors: list[str] = []
            if not missing_usage_fields:
                if normalized_usage["total_tokens"] != (
                    normalized_usage["prompt_tokens"]
                    + normalized_usage["completion_tokens"]
                ):
                    usage_errors.append("total_tokens_mismatch")
                if normalized_usage["cached_tokens"] > normalized_usage["prompt_tokens"]:
                    usage_errors.append("cached_tokens_exceed_prompt")
                if (
                    normalized_usage["reasoning_tokens"]
                    > normalized_usage["completion_tokens"]
                ):
                    usage_errors.append("reasoning_tokens_exceed_completion")
            usage_status = (
                "incomplete"
                if missing_usage_fields or usage_errors
                else "complete"
            )

            # 规范化 provider/model，账本不能只依赖可变的展示名称。
            model_id = self.model_id or "unknown"
            if ":" in model_id:
                provider_id, model_name = model_id.split(":", 1)
            else:
                provider_id, model_name = "unknown", model_id

            call_completed_at = datetime.now(timezone.utc).isoformat()
            call_timestamp = call_timestamp or call_completed_at
            self._llm_call_count += 1
            call_index = self._llm_call_count

            # 获取 maxContextTokens
            from src.core.model_manager import DEFAULT_MAX_CONTEXT_TOKENS

            max_context_tokens = DEFAULT_MAX_CONTEXT_TOKENS
            if self.model_id:
                try:
                    from src.core.model_manager import get_model_manager
                    model_info = get_model_manager().get_model_info(self.model_id)
                    max_context_tokens = model_info.get(
                        "maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS
                    )
                except Exception:
                    pass

            # 计算估算值
            estimates = self._compute_token_estimates()

            # 构建 token_usage 数据（最后一次调用，供前端实时展示）
            self.token_usage = {
                "api": {
                    **normalized_usage,
                    "usage_status": usage_status,
                    "missing_usage_fields": missing_usage_fields,
                    "usage_errors": usage_errors,
                },
                "estimated": {
                    "system_prompt_tokens": estimates["system_prompt_tokens"],
                    "tool_result_tokens": estimates["tool_result_tokens"],
                    "total_tokens": estimates["total_tokens"],
                },
                "max_context_tokens": max_context_tokens,
                "model_id": self.model_id or "",
                "llm_call_count": self._llm_call_count,
                "updated_at": call_completed_at,
            }

            call_entry = {
                "call_id": f"{self.session_id}:{self._llm_call_count}",
                "timestamp": call_timestamp,
                "completed_at": call_completed_at,
                "provider": provider_id,
                "model": model_name,
                "model_id": model_id,
                **normalized_usage,
                "usage_status": usage_status,
                "missing_usage_fields": missing_usage_fields,
                "usage_errors": usage_errors,
                "usage_sources": usage_sources,
                "call_count": 1,
                "call_index": call_index,
                "session_id": self.session_id,
                "workflow_id": self.workflow_id or "",
                "task_id": self.task_id or "",
                "node_id": self.node_id,
                "agent_type": self.agent_type,
            }
            if run_id:
                call_entry["run_id"] = run_id
            # call_id 在 Session 内稳定，避免恢复或异常事件重放时重复入账。
            is_new_call = not any(
                item.get("call_id") == call_entry["call_id"]
                for item in self._token_usage_calls
            )
            if is_new_call:
                self._token_usage_calls.append(call_entry)

            # 累计 token 使用（按 model_id 分组，用于工作流统计）
            model_key = model_id
            if model_key not in self._token_usage_cumulative:
                self._token_usage_cumulative[model_key] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cached_tokens": 0,
                    "reasoning_tokens": 0,
                    "call_count": 0,
                }
            cum = self._token_usage_cumulative[model_key]
            if is_new_call:
                cum["prompt_tokens"] += normalized_usage["prompt_tokens"]
                cum["completion_tokens"] += normalized_usage["completion_tokens"]
                cum["total_tokens"] += normalized_usage["total_tokens"]
                cum["cached_tokens"] += normalized_usage["cached_tokens"]
                cum["reasoning_tokens"] += normalized_usage["reasoning_tokens"]
                cum["call_count"] += 1

            # 日志记录（仅摘要，不泄露完整内容）
            api = self.token_usage["api"]
            self._logger.debug(
                f"Token usage: prompt={api['prompt_tokens']}, "
                f"completion={api['completion_tokens']}, "
                f"cached={api['cached_tokens']}, "
                f"reasoning={api['reasoning_tokens']}, "
                f"call=#{self._llm_call_count}, "
                f"max_context={max_context_tokens}"
            )

            # 广播给前端
            if event_callback:
                await self._emit_event({
                    "type": "llm_usage",
                    "session_id": self.session_id,
                    "data": self.token_usage,
                })

        except Exception as e:
            self._logger.warning(f"提取 token usage 失败: {e}")

    def get_cumulative_token_usage(self) -> dict[str, dict[str, int]]:
        """返回按 model_id 分组的累计 token 使用数据。

        Returns:
            dict: key = model_id, value = {
                "prompt_tokens", "completion_tokens", "total_tokens",
                "cached_tokens", "reasoning_tokens", "call_count"
            }
        """
        return dict(self._token_usage_cumulative)

    def get_token_usage_calls(self) -> list[dict[str, Any]]:
        """返回调用级 Token 账本的浅拷贝，供 Workflow 节点持久化。"""
        return [dict(item) for item in self._token_usage_calls]

    async def _check_and_compress_messages(self, force_full: bool = False) -> None:
        """
        检查并执行上下文压缩

        在每次API调用前执行，根据压缩检查器的决策执行相应的压缩策略。

        Args:
            force_full: 是否强制执行 FullCompact（用于手动压缩）
        """
        try:
            # 获取压缩检查器和调度器
            checker = get_compression_checker()
            scheduler = get_compression_scheduler()

            # 如果强制全量压缩（手动触发）
            if force_full:
                from src.compression.checker import CompressionStrategy, CompressionDecision
                decision = CompressionDecision(
                    strategy=CompressionStrategy.FULL,
                    reason="手动触发全量压缩"
                )
            else:
                # 执行压缩检查
                decision = checker.pre_check(
                    messages=self.lc_messages,
                    model_override=self.model_id,
                )

            # 如果不需要压缩，直接返回
            if decision.strategy.value == "none":
                return

            self._logger.info(f"触发压缩: {decision.strategy.value}, 原因: {decision.reason}")

            # 保存压缩前消息数量
            original_count = len(self.lc_messages)

            # 执行压缩
            compressed_messages = await scheduler.execute(
                decision=decision,
                messages=self.lc_messages,
                session_id=self.session_id,
                agent_id=self.agent_type,
                model_override=self.model_id
            )

            # 更新消息列表
            if compressed_messages != self.lc_messages:
                self.lc_messages = compressed_messages
                compressed_count = len(self.lc_messages)
                self._logger.info(f"压缩完成: 消息数量从 {original_count} 更新为 {compressed_count}")

                # 更新 context 快照（压缩后的 lc_messages，强制更新因为这是正常的缩短）
                self._sync_context_snapshot(force=True)

                # 在 record 末尾追加压缩标记（仅前端可见，不影响 LLM 上下文）
                await self._insert_compression_marker(
                    strategy=decision.strategy.value,
                    original_count=original_count,
                    compressed_count=compressed_count
                )

                # 实时落盘：压缩后 context + record 均已变更
                await self.async_save()

        except Exception as e:
            self._logger.error(f"压缩检查失败: {e}", exc_info=True)
            # 压缩失败不影响正常流程

    def _serialize_lc_messages(self, lc_msgs: list[BaseMessage]) -> list[dict]:
        """将 LangChain 消息列表序列化为 OpenAI 标准 dict 列表。"""
        result = []
        for msg in lc_msgs:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                entry = {"role": "user", "content": msg.content}
                if hasattr(msg, "name") and msg.name:
                    entry["name"] = msg.name
                result.append(entry)
            elif isinstance(msg, AIMessage):
                entry = {"role": "assistant", "content": msg.content}
                if hasattr(msg, "name") and msg.name:
                    entry["name"] = msg.name
                tc = _serialize_tool_calls(msg)
                if tc:
                    entry["tool_calls"] = tc
                result.append(entry)
            elif isinstance(msg, ToolMessage):
                entry = {
                    "role": "tool",
                    "content": msg.content,
                }
                if hasattr(msg, "tool_call_id") and msg.tool_call_id:
                    entry["tool_call_id"] = msg.tool_call_id
                if getattr(msg, "status", None):
                    entry["status"] = msg.status
                tool_status = getattr(msg, "additional_kwargs", {}).get("tool_status")
                if tool_status:
                    entry["tool_status"] = tool_status
                result.append(entry)
        return result

    def _sync_context_snapshot(self, force: bool = False) -> None:
        """
        将当前 lc_messages 序列化为 context.messages 快照（OpenAI 标准格式）。
        仅在压缩后或对话结束后调用，用于持久化和重启恢复。
        record 不受影响（永不压缩）。

        Args:
            force: 是否强制更新（用于压缩后正常的缩短场景）
        """
        if force:
            self.context["messages"] = self._serialize_lc_messages(self.lc_messages)
            return

        # 只增不减：只有当 lc_messages 比现有 context 更长时才更新
        # 防止异常导致的截断被固化
        current_ctx_count = len(self.context.get("messages", []))
        new_count = len([m for m in self.lc_messages if not isinstance(m, SystemMessage)])
        if new_count >= current_ctx_count:
            self.context["messages"] = self._serialize_lc_messages(self.lc_messages)

    async def _insert_compression_marker(
        self,
        strategy: str,
        original_count: int,
        compressed_count: int
    ) -> None:
        """
        在 record 末尾追加压缩标记消息（仅前端可见，不影响 lc_messages）。
        位置为触发压缩的时间线末尾，新对话自然出现在分隔线之下。

        Args:
            strategy: 压缩策略名 ("full" / "micro" / "reactive")
            original_count: 压缩前 lc_messages 消息数
            compressed_count: 压缩后 lc_messages 消息数
        """
        import re

        # 构建 event 数据
        event_data: dict = {
            "original_count": original_count,
            "compressed_count": compressed_count,
        }

        # FullCompact: 从 lc_messages 中提取摘要内容
        if strategy == "full":
            for msg in self.lc_messages:
                if isinstance(msg, AIMessage):
                    match = re.search(
                        r'<summary>(.*?)</summary>',
                        message_content_text(msg.content),
                        re.DOTALL,
                    )
                    if match:
                        event_data["summary"] = match.group(1).strip()
                    break

        await self._add_display_message("compression_divider",
                                        event=event_data,
                                        strategy=strategy)

    # ============================================================
    # 消息序列化辅助（统一 record 追加逻辑）
    # ============================================================

    async def _append_to_record(self, msg: BaseMessage) -> dict | None:
        """
        将单条 LangChain 消息序列化为 record 条目并追加到 self.record。

        Args:
            msg: AIMessage / ToolMessage / HumanMessage 等
        Returns:
            追加的 record 条目 dict，SystemMessage 返回 None
        """
        if isinstance(msg, SystemMessage):
            return None

        msg_type = "user" if isinstance(msg, HumanMessage) else (
            "assistant" if isinstance(msg, AIMessage) else
            ("tool" if isinstance(msg, ToolMessage) else msg.type)
        )

        self._msg_counter += 1
        entry = {
            "id": f"msg_{self._msg_counter:05d}",
            "type": msg_type,
            "content": message_content_text(msg.content),
        }
        if isinstance(msg.content, list):
            entry["content_blocks"] = deepcopy(msg.content)

        # 提取 name 字段
        if hasattr(msg, "name") and msg.name:
            entry["name"] = msg.name

        # 提取 source / reasoning_content
        if hasattr(msg, "additional_kwargs"):
            source = msg.additional_kwargs.get("source")
            if source:
                entry["source"] = source
            reasoning_content = msg.additional_kwargs.get("reasoning_content")
            if reasoning_content:
                entry["reasoning_content"] = reasoning_content
        if not entry.get("reasoning_content"):
            reasoning_content = message_content_reasoning(msg.content)
            if reasoning_content:
                entry["reasoning_content"] = reasoning_content

        # 提取 tool_calls（AIMessage）
        tc = _serialize_tool_calls(msg)
        if tc:
            entry["tool_calls"] = tc

        # 提取 tool_call_id（ToolMessage）
        if hasattr(msg, "tool_call_id") and msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
            entry["type"] = "tool"
            if getattr(msg, "status", None):
                entry["status"] = msg.status
            tool_status = getattr(msg, "additional_kwargs", {}).get("tool_status")
            if tool_status:
                entry["tool_status"] = tool_status

        self.record.append(entry)

        # workflow 实时推送：通知 _on_record_append 回调
        if self._on_record_append and self.workflow_id and self.node_id:
            try:
                await self._on_record_append(entry)
            except Exception:
                self._logger.exception("_on_record_append 回调失败")

        return entry

    # ============================================================
    # Display 消息管理（仅存在于 record，不进入 lc_messages）
    # ============================================================

    async def _add_display_message(self, display_type: str, **extra_fields) -> dict:
        """
        在 record 末尾追加一条 display 消息。
        record 只增不减，display 消息作为时间线标记插入。

        Args:
            display_type: 类型标识 ("compression_divider", "plan_progress", ...)
            **extra_fields: 类型相关附加字段
        Returns:
            追加的消息 dict
        """
        self._msg_counter += 1
        marker = {
            "id": f"msg_{self._msg_counter:05d}",
            "type": display_type,
            **extra_fields,
        }
        self.record.append(marker)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        await self.async_save()
        return marker

    def _remove_display_messages(self, display_type: str | None = None) -> int:
        """从 record 中移除指定类型（或全部）的 display 消息。返回移除数量。"""
        before = len(self.record)
        if display_type is None:
            self.record = [m for m in self.record if m.get("type") not in (
                "compression_divider", "plan_progress",
                "content_safety_warning", "content_safety_diagnostic",
                "recursion_limit_reached",
            )]
        else:
            self.record = [m for m in self.record if m.get("type") != display_type]
        return before - len(self.record)

    # ============================================================
    # Content Safety 诊断
    # ============================================================

    async def run_content_safety_diagnostic(self) -> dict:
        """在与普通生成相同的会话调用锁内运行内容安全诊断。"""
        async with self._invocation_scope():
            return await self._run_content_safety_diagnostic()

    async def _run_content_safety_diagnostic(self) -> dict:
        """
        运行内容安全诊断：通过二分排除法定位触发 Content Exists Risk 的具体消息。

        使用 self._safety_diagnostic_snapshot 中保存的完整消息快照，
        调用 ContentSafetyDiagnostic 逐子集测试，最后将诊断结果以
        display 消息追加到 record。

        Returns:
            诊断结果 dict，包含 triggered_by / steps 等字段
        """
        from src.agent.content_safety import ContentSafetyDiagnostic
        from src.core.llm_client import create_llm

        snapshot = self._safety_diagnostic_snapshot
        if not snapshot:
            return {
                "success": False,
                "message": "没有可用的消息快照，可能错误已过期或未触发 Content Exists Risk",
            }

        # 创建 LLM 实例用于诊断（与 session 使用相同模型配置）
        try:
            llm = create_llm()
        except Exception as e:
            return {
                "success": False,
                "message": f"无法创建 LLM 实例: {e}",
            }

        diagnostic = ContentSafetyDiagnostic(llm, snapshot)
        result = await diagnostic.diagnose()

        # 将诊断结果追加为 display 消息
        await self._add_display_message(
            "content_safety_diagnostic",
            diagnostic_result={
                "triggered_by": result.triggered_by,
                "identified_message_type": result.identified_message_type,
                "message_preview": result.message_preview,
                "summary": result.summary,
                "diagnostic_steps": result.diagnostic_steps,
            },
        )

        return {
            "success": True,
            "triggered_by": result.triggered_by,
            "summary": result.summary,
        }

    # ============================================================

    # System Prompt 管理

    # ============================================================



    def refresh_system_prompt(self, new_prompt: str) -> None:

        """刷新 system prompt（运行时动态更新，委托给 property setter）。"""

        self.system_prompt = new_prompt



    # ============================================================

    # 消息同步

    # ============================================================



    async def _sync_messages(self, final_messages: list[BaseMessage], skip_count: int = 0) -> None:

        """
        同步消息：更新 lc_messages + 增量追加新消息到 record + 更新 context。

        record 只增不减，从 final_messages 中提取本轮新增的消息
        （最后一个 HumanMessage 之后，跳过已增量保存的 skip_count 条）追加到末尾。
        """

        self.lc_messages = list(final_messages)

        # 找到最后一个 HumanMessage（本轮用户输入）
        new_start_index = len(final_messages)
        for i in range(len(final_messages) - 1, -1, -1):
            if isinstance(final_messages[i], HumanMessage):
                new_start_index = i + 1  # HumanMessage 之后开始
                break

        # 跳过已在流式循环中增量保存的消息
        append_start = new_start_index + skip_count

        # 将新消息序列化并追加到 record（使用统一的辅助方法）
        for msg in final_messages[append_start:]:
            await self._append_to_record(msg)

        # 更新 context 快照（OpenAI 标准格式）
        self._sync_context_snapshot()

        # 实时落盘：LLM 回复已写入 record 和 context
        self.updated_at = datetime.now(timezone.utc).isoformat()
        await self.async_save()

    # ============================================================

    # 原有数据方法（保持向后兼容）

    # ============================================================



    async def add_message(self, role: str, content: str, **extra):

        """在 record 末尾追加一条消息（自动生成递增 ID），异步落盘。"""
        self._msg_counter += 1
        msg = {
            "id": f"msg_{self._msg_counter:05d}",
            "type": role,
            "content": content,
            **extra,
        }
        self.record.append(msg)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        await self.async_save()
        return msg



    def get_last_assistant_message(self) -> str:

        for msg in reversed(self.record):

            if msg.get("type") == "assistant" and msg.get("content"):

                return msg["content"]

        return ""



    def get_summary(self) -> dict:

        msg_count = len([m for m in self.record if m.get("type") not in ("system_prompt", "compression_divider")])

        summary = {

            "session_id": self.session_id,

            "type": self.session_type,

            "parent_id": self.parent_id,

            "status": self.status,

            "task": self.task_description[:100] if self.task_description else "",

            "message_count": msg_count,

            "created_at": self.created_at,

            "updated_at": self.updated_at,

            "last_message": self.get_last_assistant_message()[:200],

            "agent_type": self.agent_type,

        }

        if self.workspace_path:

            summary["workspace_path"] = self.workspace_path

        if self.workflow_id:
            summary["workflow_id"] = self.workflow_id
        if self.task_id:
            summary["task_id"] = self.task_id
        if self.node_id:
            summary["node_id"] = self.node_id

        return summary



    # ============================================================

    # 持久化

    # ============================================================



    def save(self):
        """保存会话到文件（原子写入：先写临时文件再替换，防止崩溃导致数据损坏）"""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        file_path = SESSIONS_DIR / f"{self.session_id}.json"

        try:
            import os
            data = self.to_dict()
            # 整体序列化：不可序列化的值统一转 str
            try:
                serialized = json.dumps(data, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                # 降级：逐字段清理不可序列化的值
                serialized = json.dumps(self._sanitize_dict(data), ensure_ascii=False, indent=2)
            tmp_path = str(file_path) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(tmp_path, file_path)

        except (IOError, OSError) as e:
            self._logger.error(f"保存会话失败: {e}")
            raise

    @staticmethod
    def _sanitize_dict(data: dict) -> dict:
        """降级序列化：将不可 JSON 序列化的值转为 str。"""
        import copy
        result = copy.deepcopy(data)
        def _walk(obj):
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    if isinstance(v, (dict, list)):
                        _walk(v)
                    else:
                        try:
                            json.dumps(v, ensure_ascii=False)
                        except (TypeError, ValueError):
                            obj[k] = str(v)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    if isinstance(v, (dict, list)):
                        _walk(v)
                    else:
                        try:
                            json.dumps(v, ensure_ascii=False)
                        except (TypeError, ValueError):
                            obj[i] = str(v)
        _walk(result)
        return result

    async def async_save(self, force: bool = False, strict: bool = False):
        """标记会话为脏（需持久化），由全局定时刷盘统一处理。

        默认情况下不立即写盘，仅标记 _save_dirty。全局定时器每 5 秒
        扫描所有活跃 session，将脏 session 批量刷盘。这样 100 个并发
        agent 每秒产生的数千次 async_save 调用，实际只产生 ~20 次文件写入。

        Args:
            force: 强制立即刷盘（用于 session 销毁、关键状态变更等场景）
            strict: 写盘失败时向调用方抛出异常。默认保持现有宽容行为，
                仅供销毁前的最终保存等必须 fail-closed 的生命周期边界使用。
        """
        self._save_dirty = True
        if force:
            self._logger.debug("[IO] force save: session=%s", self.session_id)
            await self._flush_to_disk(strict=strict)

    async def _flush_to_disk(self, strict: bool = False):
        """将当前内存状态写入磁盘（使用独立线程池，非默认池）。"""
        async with self._save_lock:
            if not self._save_dirty:
                return
            self._save_dirty = False
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(_io_executor, self.save)
            except asyncio.CancelledError:
                # executor 中的 save 可能仍在收尾；在无法确认落盘完成时保守保留脏位。
                self._save_dirty = True
                raise
            except Exception as e:
                # 写盘失败时必须继续保留脏位，供全局定时器或生命周期调用方重试。
                # strict 模式额外把异常传到调用边界，阻止尚未落盘的 session 被注销。
                self._save_dirty = True
                self._logger.error(f"异步保存会话失败: {e}")
                if strict:
                    raise



    @classmethod

    def load(cls, session_id: str) -> "AgentSession | None":

        file_path = SESSIONS_DIR / f"{session_id}.json"

        if not file_path.exists():

            return None

        try:

            with open(file_path, "r", encoding="utf-8") as f:

                data = json.load(f)

            return cls.from_dict(data)

        except (json.JSONDecodeError, IOError) as e:

            logger.error(f"加载会话 {session_id} 失败: {e}")

            return None



    def to_dict(self) -> dict:

        # 序列化 record（自有格式，完整历史）
        # 优化：不再逐字段 json.dumps 校验可序列化性（高并发时 CPU 开销显著），
        # 改为整体序列化时统一 try/except
        result = {

            "session_id": self.session_id,

            "session_type": self.session_type,

            "parent_id": self.parent_id,

            "status": self.status,

            "task_description": self.task_description,

            "system_prompt": self.system_prompt,

            "agent_type": self.agent_type,

            "runtime_scope": self.runtime_scope,

            "lifecycle_profile": self.lifecycle_profile,

            "resource_owner": self.resource_owner,

            "external_ref": self.external_ref,

            "scope_hash": self.scope_hash,

            "model_id": self.model_id,

            "model_params": self.model_params,

            "created_at": self.created_at,

            "updated_at": self.updated_at,

            "record": self.record,
            "context": self.context,

        }

        if self.token_usage:
            result["token_usage"] = self.token_usage

        if self.last_error:
            result["last_error"] = self.last_error

        # 持久化累计 token 使用数据（按 model_id 分组）
        if self._token_usage_cumulative:
            result["token_usage_cumulative"] = self._token_usage_cumulative

        if self._token_usage_calls:
            result["token_usage_calls"] = self._token_usage_calls

        if self.workspace_path:

            result["workspace_path"] = self.workspace_path

        # Workflow 字段：workflow session 恢复后需要保留这些信息
        if self.workflow_id:
            result["workflow_id"] = self.workflow_id
        if self.task_id:
            result["task_id"] = self.task_id
        if self.node_id:
            result["node_id"] = self.node_id

        return result



    @classmethod
    def from_dict(cls, data: dict) -> "AgentSession":

        session = cls(
            session_type=data.get("session_type", "sub"),
            parent_id=data.get("parent_id"),
            task_description=data.get("task_description", ""),
            system_prompt=data.get("system_prompt", ""),
            session_id=data.get("session_id"),
            agent_type=data.get("agent_type", "default"),
            workspace_path=data.get("workspace_path"),
            workflow_id=data.get("workflow_id"),
            task_id=data.get("task_id"),
            runtime_scope=data.get("runtime_scope"),
            model_params=data.get("model_params"),
            lifecycle_profile=data.get("lifecycle_profile", "task"),
            resource_owner=data.get("resource_owner", ""),
            external_ref=data.get("external_ref", ""),
            scope_hash=data.get("scope_hash", ""),
        )
        session.node_id = data.get("node_id", "")
        session.model_id = data.get("model_id")

        session.status = data.get("status", "completed")
        session.created_at = data.get("created_at", session.created_at)
        session.updated_at = data.get("updated_at", session.updated_at)

        # 恢复 token 监控数据
        session.token_usage = data.get("token_usage")
        raw_last_error = data.get("last_error")
        if isinstance(raw_last_error, dict):
            code = raw_last_error.get("code")
            message = raw_last_error.get("message")
            occurred_at = raw_last_error.get("occurred_at")
            if all(
                isinstance(value, str) and value
                for value in (code, message, occurred_at)
            ):
                session.last_error = {
                    "code": code,
                    "message": message,
                    "occurred_at": occurred_at,
                }
        session._llm_call_count = session.token_usage.get("llm_call_count", 0) if session.token_usage else 0
        # 恢复累计 token 数据（从磁盘恢复的 dict key 可能是 str，需要确保 int 类型）
        raw_cumulative = data.get("token_usage_cumulative", {})
        if isinstance(raw_cumulative, dict):
            session._token_usage_cumulative = {
                model_key: {k: int(v) for k, v in model_data.items()}
                for model_key, model_data in raw_cumulative.items()
                if isinstance(model_data, dict)
            }

        raw_calls = data.get("token_usage_calls", [])
        if isinstance(raw_calls, list):
            session._token_usage_calls = [
                dict(item) for item in raw_calls if isinstance(item, dict)
            ]
        if session._token_usage_calls:
            call_indexes: list[int] = []
            for item in session._token_usage_calls:
                try:
                    call_indexes.append(int(item.get("call_index", 0) or 0))
                except (TypeError, ValueError):
                    continue
            session._llm_call_count = max(
                session._llm_call_count,
                max(call_indexes, default=0),
            )

        # 加载 record（新格式）或 messages（旧格式兼容）
        if "record" in data:
            session.record = data["record"]
            max_id = 0
            for msg in session.record:
                mid = msg.get("id", "")
                if mid.startswith("msg_") and mid[4:].isdigit():
                    max_id = max(max_id, int(mid[4:]))
            session._msg_counter = max_id
        else:
            old_messages = data.get("messages", [])
            session.record = []
            for msg in old_messages:
                role = msg.get("role", "")
                if msg.get("display"):
                    entry = {"id": f"msg_{session._msg_counter + 1:05d}", "type": msg["display"]}
                    if msg.get("compression_event"):
                        entry["event"] = msg["compression_event"]
                    if msg.get("strategy"):
                        entry["strategy"] = msg["strategy"]
                    session._msg_counter += 1
                elif role == "system":
                    if session._msg_counter == 0 and not session.record:
                        continue
                else:
                    session._msg_counter += 1
                    entry = {"id": f"msg_{session._msg_counter:05d}", "type": role}
                    if msg.get("content"):
                        entry["content"] = msg["content"]
                    if msg.get("name"):
                        entry["name"] = msg["name"]
                    if msg.get("source"):
                        entry["source"] = msg["source"]
                    if msg.get("reasoning_content"):
                        entry["reasoning_content"] = msg["reasoning_content"]
                    if msg.get("tool_calls"):
                        entry["tool_calls"] = msg["tool_calls"]
                    if msg.get("tool_call_id"):
                        entry["tool_call_id"] = msg["tool_call_id"]
                    if msg.get("status"):
                        entry["status"] = msg["status"]
                    if msg.get("tool_status"):
                        entry["tool_status"] = msg["tool_status"]
                session.record.append(entry)

        # 优先从 record 重建完整 lc_messages（record 永不压缩，是最可靠的数据源）
        # 只有 record 不可用时才降级到 context.messages
        if "record" in data and data["record"]:
            session.context = {"messages": []}
            session._restore_lc_from_record()
        elif "context" in data and data["context"].get("messages"):
            session.context = data["context"]
            session._restore_lc_from_context()
        else:
            session.context = {"messages": []}

        return session

    def _restore_lc_from_context(self) -> None:
        """从 context.messages 恢复 lc_messages（OpenAI 标准格式）。

        向后兼容：旧磁盘 session 的 context.messages 可能不含 system role 条目，
        此时从 self.system_prompt 补回 SystemMessage 到 lc_messages[0]。
        """
        self.lc_messages = []
        for m in self.context.get("messages", []):
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                self.lc_messages.append(SystemMessage(content=content))
            elif role == "user":
                entry = HumanMessage(content=content)
                if m.get("name"):
                    entry.name = m["name"]
                self.lc_messages.append(entry)
            elif role == "assistant":
                entry = AIMessage(content=content)
                if m.get("name"):
                    entry.name = m["name"]
                if m.get("tool_calls"):
                    entry.tool_calls = [
                        {"id": tc.get("id", ""), "name": tc["function"]["name"],
                         "args": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]}
                        for tc in m["tool_calls"]
                    ]
                self.lc_messages.append(entry)
            elif role == "tool":
                raw_status = m.get("tool_status") or m.get("status") or "success"
                self.lc_messages.append(ToolMessage(
                    content=content,
                    tool_call_id=m.get("tool_call_id", ""),
                    status="error" if raw_status in {"error", "failed", "cancelled"} else "success",
                    additional_kwargs={"tool_status": raw_status},
                ))

        # 向后兼容：旧磁盘 session 的 context.messages 可能不含 system 条目
        if self.system_prompt:
            if not (self.lc_messages and isinstance(self.lc_messages[0], SystemMessage)):
                self.lc_messages.insert(0, SystemMessage(content=self.system_prompt))

        # [持久化层] 从磁盘恢复时清理不完整的 tool_calls/tool 配对
        self.lc_messages = _sanitize_tool_pairs(self.lc_messages)

    def _restore_lc_from_record(self) -> None:
        """从 record 重建 lc_messages（旧格式兼容，跳过 display 和 system_prompt 条目）。"""
        self.lc_messages = []
        if self.system_prompt:
            self.lc_messages.append(SystemMessage(content=self.system_prompt))
        for msg in self.record:
            msg_type = msg.get("type", "")
            if msg_type in (
                "system_prompt", "compression_divider", "plan_progress",
                "content_safety_warning", "content_safety_diagnostic",
                "recursion_limit_reached",
            ):
                continue
            content = msg.get("content_blocks", msg.get("content", ""))
            if msg_type == "user":
                entry = HumanMessage(content=content)
                if msg.get("name"):
                    entry.name = msg["name"]
                if msg.get("source"):
                    entry.additional_kwargs = {"source": msg["source"]}
                self.lc_messages.append(entry)
            elif msg_type == "assistant":
                entry = AIMessage(content=content)
                if msg.get("name"):
                    entry.name = msg["name"]
                if msg.get("reasoning_content"):
                    entry.additional_kwargs = {"reasoning_content": msg["reasoning_content"]}
                if msg.get("tool_calls"):
                    entry.tool_calls = [
                        {"id": tc.get("id", ""), "name": tc["function"]["name"],
                         "args": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]}
                        for tc in msg["tool_calls"]
                    ]
                self.lc_messages.append(entry)
            elif msg_type == "tool":
                raw_status = msg.get("tool_status") or msg.get("status") or "success"
                self.lc_messages.append(ToolMessage(
                    content=content,
                    tool_call_id=msg.get("tool_call_id", ""),
                    status="error" if raw_status in {"error", "failed", "cancelled"} else "success",
                    additional_kwargs={"tool_status": raw_status},
                ))

        # 修复 record 中可能存在的悬挂 tool_calls（服务异常中断导致）
        self.lc_messages = _sanitize_tool_pairs(self.lc_messages)

    def __repr__(self):

        return (

            f"<AgentSession id={self.session_id} type={self.session_type} "

            f"agent_type={self.agent_type} status={self.status} msgs={len(self.record)}>"

        )


# ============================================================
# 全局定时刷盘管理器
# ============================================================

class SessionPersistenceManager:
    """全局定时刷盘：每 _PERSIST_INTERVAL 秒扫描所有活跃 session，
    将标记为脏的 session 批量写入磁盘。

    设计目标：
    - 100+ 并发 agent 时，每秒数千次 async_save() 调用仅标记脏位，
      实际文件写入降至 ~20 次/5秒（所有 session 合计）
    - 使用独立 _io_executor 线程池，不与 LangGraph ToolNode 竞争
    """

    def __init__(self):
        self._sessions: dict[str, AgentSession] = {}
        self._timer_task: asyncio.Task | None = None
        self._flush_semaphore = asyncio.Semaphore(_IO_FLUSH_SEMAPHORE)

    def register(self, session: AgentSession):
        """注册 session 到刷盘管理器。"""
        self._sessions[session.session_id] = session

    def unregister(self, session_id: str):
        """注销 session（销毁前应先强制刷盘）。"""
        self._sessions.pop(session_id, None)

    def start(self):
        """启动定时刷盘循环。"""
        if self._timer_task and not self._timer_task.done():
            return
        self._timer_task = asyncio.create_task(self._flush_loop(), name="session-persistence")

    async def stop(self):
        """停止定时刷盘循环，强制刷盘所有脏 session。"""
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await asyncio.wait_for(self._timer_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._timer_task = None
        # 停止前强制刷盘所有脏 session
        await self.flush_all()

    async def flush_all(self):
        """立即刷盘所有脏 session（Semaphore 限流，防止 ThreadPoolExecutor 饱和）。"""
        dirty_sessions = [s for s in list(self._sessions.values()) if s._save_dirty]
        if not dirty_sessions:
            return
        logger.debug(
            "[IO] flush_all 开始: dirty_sessions=%d, active_sessions=%d",
            len(dirty_sessions), len(self._sessions),
        )
        _ts = time.time()
        # 使用 Semaphore 限制并发刷盘数，防止 N 个 session 同时 run_in_executor
        # 耗尽 _io_executor 线程池，导致事件循环阻塞
        async def _flush_one(session):
            async with self._flush_semaphore:
                await session._flush_to_disk()
        await asyncio.gather(
            *(_flush_one(s) for s in dirty_sessions),
            return_exceptions=True,
        )
        _elapsed = time.time() - _ts
        if _elapsed > 1.0:
            logger.warning(
                "[IO] flush_all 慢刷盘: dirty_sessions=%d, 耗时=%.2fs",
                len(dirty_sessions), _elapsed,
            )
        else:
            logger.debug(
                "[IO] flush_all 完成: dirty_sessions=%d, 耗时=%.2fs",
                len(dirty_sessions), _elapsed,
            )

    async def _flush_loop(self):
        """定时刷盘循环。"""
        try:
            while True:
                await asyncio.sleep(_PERSIST_INTERVAL)
                await self.flush_all()
        except asyncio.CancelledError:
            pass


# 全局单例
_persistence_manager = SessionPersistenceManager()
