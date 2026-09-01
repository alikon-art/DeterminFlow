"""
LangGraph 图构建器 - 构建统一的 Agent StateGraph

所有 Agent（Main/Sub）共用 llm ↔ tools 双节点简单图。
鉴权层通过 ToolNode 的 awrap_tool_call 机制实现。
"""
import logging
import warnings
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from src.core.state import AgentState
from src.core.tool_guard import RoundsGuard, make_guarded_wrapper

# 导入 llm_client 以确保 monkey patch 被应用（必须在使用 ChatOpenAI 之前）
import src.core.llm_client  # noqa: F401
from src.core.llm_client import LLMEmptyOrMalformedResponse

logger = logging.getLogger(__name__)


# ============================================================
# 条件路由函数
# ============================================================

def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """
    条件路由：检查 LLM 是否返回了 tool_calls，且轮次未耗尽。

    当 remaining_rounds <= 0 时，即使 LLM 返回 tool_calls 也直接终止，
    避免 Guard 拦截 → LLM 重试 → 再拦截的无限循环导致触发 recursion_limit。
    """
    messages = state["messages"]

    if not messages:
        return "__end__"

    last_message = messages[-1]

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        remaining = state.get("remaining_rounds") or 0
        if remaining <= 0:
            # 提取详细的追踪信息
            session_id = state.get("session_id", "unknown")
            agent_type = state.get("agent_type", "unknown")
            tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
            metadata = state.get("metadata", {})
            max_rounds = metadata.get("max_rounds", "unknown")

            logger.warning(
                f"轮次已耗尽，终止工具调用循环 | "
                f"session_id={session_id} | "
                f"agent_type={agent_type} | "
                f"max_rounds={max_rounds} | "
                f"remaining_rounds={remaining} | "
                f"pending_tools={tool_names}"
            )
            return "__end__"
        return "tools"

    return "__end__"


# ============================================================
# 节点函数工厂
# ============================================================

def _make_llm_node(llm: BaseChatModel, tools: list[BaseTool]):
    """创建 LLM 节点函数。

    注意：流式 token 推送不再在此处通过 event_bus 硬编码，
    而是由上层 session.send_message() 通过 astream_events 统一处理。
    此节点仅负责调用 LLM 并返回完整响应。
    """
    if tools:
        llm_with_tools = llm.bind_tools(tools, strict=True)
    else:
        llm_with_tools = llm

    async def llm_node(state: AgentState) -> dict:
        messages = state["messages"]
        session_id = state.get("session_id", "unknown")
        agent_type = state.get("agent_type", "unknown")

        # [防御层] 调用 LLM 前清理不完整的 tool_calls/tool 配对。
        # 注意：_sanitize_tool_pairs 仅处理「全局 ID 匹配」——即 AIMessage 的 tool_call_id
        # 在整个消息列表中是否至少有一个对应的 ToolMessage。但它不检查「ToolMessage 是否紧跟在
        # 对应的 AIMessage 之后」——OpenAI API 严格要求 tool 消息必须直接跟在有 tool_calls 的
        # assistant 消息之后，中间不能插入其他 AIMessage 或 HumanMessage。
        # 因此此处还需要额外做顺序校验 + 修复。
        from src.core.utils import _sanitize_tool_pairs, _sanitize_tool_pairs_strict, _diff_sanitize
        messages_before = messages
        messages = _sanitize_tool_pairs_strict(messages)
        # 诊断：记录首次 sanitize 触发（同一 session 只记录一次详情，避免刷屏）
        removed_count = len(messages_before) - len(messages)
        if removed_count > 0:
            warn_key = f"_sanitize_warned_{session_id}"
            if not getattr(_make_llm_node, warn_key, False):
                setattr(_make_llm_node, warn_key, True)
                removed_desc = _diff_sanitize(messages_before, messages)
                logger.warning(
                    f"[防御层] strict sanitize 移除了 {removed_count} 条消息（后续同类告警仅计数）| "
                    f"session_id={session_id} | agent_type={agent_type} | "
                    f"removed={removed_desc}"
                )
            else:
                logger.debug(
                    f"[防御层] strict sanitize 移除了 {removed_count} 条消息 | "
                    f"session_id={session_id} | agent_type={agent_type}"
                )

        from src.compression.checker import CompressionStrategy, get_compression_checker
        from src.compression.config import get_compression_config_manager
        from src.compression.strategies.micro import MicroCompactStrategy
        from src.compression.strategies.reactive import ReactiveCompactStrategy
        from src.compression.utils import estimate_messages_tokens
        from src.core.model_manager import DEFAULT_MAX_CONTEXT_TOKENS, get_model_manager

        metadata = state.get("metadata", {})
        model_override = metadata.get("model_id")
        model_info = get_model_manager().get_model_info(model_override)
        max_context_tokens = model_info.get(
            "maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS
        )
        micro_strategy = MicroCompactStrategy()
        reactive_strategy = ReactiveCompactStrategy()
        checker = get_compression_checker()

        is_compressor = agent_type == "compressor"

        request_messages = list(messages)
        if estimate_messages_tokens(request_messages) > max_context_tokens:
            request_messages = micro_strategy.compact_for_request(
                request_messages,
                max_context_tokens=max_context_tokens,
            )

        max_retries = get_compression_config_manager().get_reactive_compact_config().get(
            "maxRetryCount", 5
        )
        retry_count = 0
        while True:
            try:
                response = await llm_with_tools.ainvoke(request_messages)
                break
            except LLMEmptyOrMalformedResponse as exc:
                # The LLM returned a chunk whose content is missing or shaped
                # in a way LangChain cannot assemble into a BaseMessage. The
                # call itself succeeded at the HTTP layer, so this is usually
                # a transient truncation. Drop the oldest message round and
                # retry once; if compaction cannot shrink further, escalate.
                logger.warning(
                    "[LLMEmptyOrMalformedResponse] attempting recovery | "
                    "session_id=%s agent_type=%s retry=%s/%s",
                    session_id,
                    agent_type,
                    retry_count + 1,
                    max_retries,
                )
                if is_compressor or retry_count >= max_retries:
                    raise
                next_messages = reactive_strategy.discard_oldest_round(
                    request_messages
                )
                if next_messages == request_messages:
                    raise exc
                retry_count += 1
                request_messages = next_messages
                continue
            except Exception as exc:
                decision = checker.error_check(exc, request_messages)
                if (
                    decision.strategy != CompressionStrategy.REACTIVE
                    or is_compressor
                    or retry_count >= max_retries
                ):
                    raise

                # 错误后的第一优先级仍是工具结果裁剪；只有没有更多工具内容
                # 可裁时，才从 checkpoint 后逐次丢弃一个完整历史轮次。
                next_messages = micro_strategy.compact_for_request(
                    request_messages,
                    max_context_tokens=max_context_tokens,
                )
                if next_messages == request_messages:
                    next_messages = reactive_strategy.discard_oldest_round(
                        request_messages
                    )
                if next_messages == request_messages:
                    raise

                retry_count += 1
                request_messages = next_messages
                logger.warning(
                    "LLM 上下文超限，仅重试当前请求: session_id=%s "
                    "agent_type=%s retry=%s/%s messages=%s",
                    session_id,
                    agent_type,
                    retry_count,
                    max_retries,
                    len(request_messages),
                )

        remaining = state.get("remaining_rounds") or 0
        if isinstance(response, AIMessage) and response.tool_calls:
            # [源头层] remaining_rounds 递减后 ≤ 0 时，剥离 tool_calls 只保留文本
            # 避免 should_continue 路由到 __end__ 后产生无 ToolMessage 响应的孤儿 AIMessage
            if remaining <= 1:  # 递减后将变为 0
                tool_names = [tc.get("name", "unknown") for tc in response.tool_calls]
                logger.warning(
                    f"轮次即将耗尽，剥离 tool_calls | "
                    f"session_id={session_id} | agent_type={agent_type} | "
                    f"remaining_rounds={remaining} | tools={tool_names}"
                )
                new_content = response.content or "[工具调用已因轮次耗尽被跳过，请继续对话]"
                # 保留 additional_kwargs（含 reasoning_content）、usage_metadata 等关键字段
                response = AIMessage(
                    content=new_content,
                    additional_kwargs=response.additional_kwargs,
                    response_metadata=getattr(response, "response_metadata", {}),
                    usage_metadata=getattr(response, "usage_metadata", None),
                )
                remaining = 0
            else:
                tool_names = [tc.get("name", "unknown") for tc in response.tool_calls]
                logger.debug(
                    f"LLM 请求工具调用 | "
                    f"session_id={session_id} | "
                    f"agent_type={agent_type} | "
                    f"remaining_rounds={remaining} | "
                    f"tools={tool_names}"
                )
                remaining = remaining - 1

        return {
            "messages": [response],
            "remaining_rounds": remaining,
        }

    return llm_node


# ============================================================
# 统一 Graph 构建
# ============================================================

def build_graph(
    llm: BaseChatModel,
    tools: list[BaseTool],
    max_rounds: int = 10,
) -> StateGraph:
    """
    统一构建 Agent 的 StateGraph（llm ↔ tools 双节点图）。

    所有 Agent 共用同一 Graph 结构，不再区分 Main/Sub。

    Args:
        llm: LLM 客户端
        tools: 绑定的工具列表
        max_rounds: 最大工具调用轮次（信息性参数，实际由 state.remaining_rounds 控制）

    Returns:
        未编译的 StateGraph，调用方需 .compile() 后使用
    """
    # 工具去重：确保工具名称唯一，避免 API 400 错误
    unique_tools = {}
    for tool in tools:
        if tool.name in unique_tools:
            logger.warning(f"检测到重复工具: {tool.name}，已跳过")
            continue
        unique_tools[tool.name] = tool

    deduplicated_tools = list(unique_tools.values())

    if len(deduplicated_tools) < len(tools):
        logger.warning(f"工具去重：原始 {len(tools)} 个，去重后 {len(deduplicated_tools)} 个")

    llm_node = _make_llm_node(llm, deduplicated_tools)

    # 使用 ToolNode 原生的 awrap_tool_call 机制注入鉴权层
    guarded_wrapper = make_guarded_wrapper([RoundsGuard()])
    tool_node = ToolNode(deduplicated_tools, awrap_tool_call=guarded_wrapper)

    graph = StateGraph(AgentState)

    graph.add_node("llm", llm_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("llm")

    graph.add_conditional_edges(
        "llm",
        should_continue,
        {
            "tools": "tools",
            "__end__": END,
        },
    )

    # 统一双节点图：tools → llm
    graph.add_edge("tools", "llm")

    logger.info(f"Agent Graph 已构建: {len(deduplicated_tools)} 个工具, 最大 {max_rounds} 轮")

    return graph


# ============================================================
# 兼容性别名（供渐进式迁移使用，后续可移除）
# ============================================================

def build_main_graph(
    llm: BaseChatModel,
    tools: list[BaseTool],
    max_rounds: int = 5,
) -> StateGraph:
    """兼容性别名 → build_graph()"""
    warnings.warn(
        "build_main_graph() 已弃用，请使用 build_graph()",
        DeprecationWarning,
        stacklevel=2,
    )
    return build_graph(llm=llm, tools=tools, max_rounds=max_rounds)


def build_sub_graph(
    llm: BaseChatModel,
    tools: list[BaseTool],
    max_rounds: int = 10,
) -> StateGraph:
    """兼容性别名 → build_graph()"""
    warnings.warn(
        "build_sub_graph() 已弃用，请使用 build_graph()",
        DeprecationWarning,
        stacklevel=2,
    )
    return build_graph(llm=llm, tools=tools, max_rounds=max_rounds)
