"""
FullCompact策略 - 全量摘要压缩

最核心的压缩手段，通过创建 compressor agent 生成结构化摘要，替换早期上下文。
解决问题：上下文总量即将触及窗口上限时，系统性减少占用。
"""
import asyncio
import logging
import re
from typing import List, Dict, Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage, ToolMessage

from ..config import get_compression_config_manager
from ..utils import get_message_role
from src.core.model_manager import get_model_manager
from src.core.utils import estimate_tokens
from src.prompts.compressor_prompts import build_compressor_prompt

logger = logging.getLogger(__name__)

# 修复：摘要 prompt 单条消息长度上限（字符）。tool 结果上限更严，
# 因为工作流工具结果可能达到数十万字符。
MAX_MESSAGE_CHARS = 3000
MAX_TOOL_RESULT_CHARS = 1500


class FullCompactStrategy:
    """
    FullCompact策略实现

    设计定位：
    最核心的压缩手段，调用模型生成结构化摘要，替换早期上下文。
    解决问题：上下文总量即将触及窗口上限时，系统性减少占用。

    触发条件：
    上下文占用率 > compactionThreshold × modelMaxTokens

    执行逻辑：
    1. 完整消息保存（由TranscriptSaver处理）
    2. 划分可压缩区域
    3. 调用模型生成摘要
    4. 构建新messages
    5. 后处理（由PostProcessor处理）
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()
        self.model_manager = get_model_manager()

    async def execute(
        self,
        messages: List[BaseMessage],
        model_override: str | None = None
    ) -> List[BaseMessage]:
        """
        执行FullCompact压缩

        Args:
            messages: 当前消息列表
            model_override: 模型覆盖

        Returns:
            压缩后的消息列表
        """
        # 获取配置
        full_config = self.config_manager.get_full_compact_config()
        keep_recent_tokens = full_config.get("keepRecentTokens", 51200)
        max_retry_count = full_config.get("maxRetryCount", 2)
        summary_token_budget = full_config.get("summaryTokenBudget", 4096)

        # 划分可压缩区域和最近区域
        compressible_messages, recent_messages = self._split_messages(
            messages, keep_recent_tokens
        )

        # 如果没有可压缩区域，返回原始消息
        if not compressible_messages:
            logger.info("没有可压缩区域，跳过FullCompact")
            return messages

        # 调用模型生成摘要
        summary = await self._generate_summary(
            compressible_messages,
            model_override=model_override,
            max_tokens=summary_token_budget,
            max_retry_count=max_retry_count
        )

        if not summary:
            logger.warning("摘要生成失败，返回原始消息")
            # 修复：失败兜底——截断超长消息后再返回。否则超限上下文原样保留，
            # 下次压缩同样失败，会话因 400 超限报废（曾实测 130 万 token）。
            return self._truncate_oversized(messages)

        # 构建新messages
        new_messages = self._build_new_messages(
            messages, summary, recent_messages
        )

        logger.info(f"FullCompact完成: {len(messages)} -> {len(new_messages)} 条消息")
        return new_messages

    def _split_messages(
        self,
        messages: List[BaseMessage],
        keep_recent_tokens: int
    ) -> tuple[List[BaseMessage], List[BaseMessage]]:
        """
        划分可压缩区域和最近区域

        Args:
            messages: 消息列表
            keep_recent_tokens: 保留最近的token数

        Returns:
            (可压缩区域消息, 最近区域消息)
        """
        # 找到system prompt的位置
        system_index = -1
        for i, msg in enumerate(messages):
            if isinstance(msg, SystemMessage):
                system_index = i
                break

        # 如果没有system prompt，从第一条消息开始
        start_index = system_index + 1 if system_index >= 0 else 0

        # 从尾部开始计算token数，找到分割点
        recent_tokens = 0
        split_index = len(messages)

        for i in range(len(messages) - 1, start_index - 1, -1):
            msg = messages[i]
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            msg_tokens = estimate_tokens(content)

            # 计算tool_calls的token
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    args_str = str(tc.get("args", ""))
                    msg_tokens += estimate_tokens(args_str)

            if recent_tokens + msg_tokens > keep_recent_tokens:
                split_index = i + 1
                break

            recent_tokens += msg_tokens
            split_index = i

        # 确保split_index在有效范围内
        split_index = max(start_index, split_index)

        compressible = messages[start_index:split_index]
        recent = messages[split_index:]

        logger.debug(f"消息划分: 可压缩区域 {len(compressible)} 条, "
                    f"最近区域 {len(recent)} 条, "
                    f"保留token数 {recent_tokens}")

        return compressible, recent

    async def _generate_summary(
        self,
        messages: List[BaseMessage],
        model_override: str | None = None,
        max_tokens: int = 4096,
        max_retry_count: int = 2
    ) -> Optional[str]:
        """
        调用 compressor prompt + LLM 直接生成摘要

        Args:
            messages: 可压缩区域的消息
            model_override: 模型覆盖
            max_tokens: 摘要最大token数
            max_retry_count: 最大重试次数

        Returns:
            生成的摘要文本，失败返回None
        """
        # 准备用户消息内容
        user_content = self._prepare_user_content(messages)

        # 构建 compressor system prompt（从 agent 定义读取 prompt_template）
        # PromptManager 和 agent_def 在重试间不变，提取到循环外避免重复创建
        from src.prompts.manager import PromptManager
        from src.agent.definition import get_agent_definition
        from src.core.llm_client import create_llm
        prompt_mgr = PromptManager()
        compressor_def = get_agent_definition("compressor")
        template_name = compressor_def.prompt_template if compressor_def else "compressor"
        compressor_sections = prompt_mgr.get_sections(template_name)
        system_prompt = build_compressor_prompt(config_sections=compressor_sections)

        # 重试逻辑
        for attempt in range(max_retry_count + 1):
            try:
                logger.info(f"调用 compressor LLM 生成摘要 (尝试 {attempt + 1}/{max_retry_count + 1})")

                # 创建 LLM 实例（非流式）
                llm = create_llm(model_override=model_override, streaming=False)

                # 直接调用 LLM
                response = await llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_content)
                ])

                # 提取摘要
                summary = self._extract_summary(response.content)

                if summary:
                    logger.info(f"摘要生成成功，长度: {len(summary)} 字符")
                    return summary
                else:
                    logger.warning(f"摘要提取失败 (尝试 {attempt + 1})")

            except Exception as e:
                logger.error(f"摘要生成失败 (尝试 {attempt + 1}): {e}")

                if attempt < max_retry_count:
                    # 指数退避：1s, 2s, 4s, ...
                    await asyncio.sleep(2 ** attempt)

        logger.error(f"摘要生成失败，已重试 {max_retry_count} 次")
        return None

    def _prepare_user_content(self, messages: List[BaseMessage]) -> str:
        """
        准备发送给模型的用户消息内容

        Args:
            messages: 可压缩区域的消息

        Returns:
            格式化的用户消息内容
        """
        content_parts = []

        for msg in messages:
            role = self._get_message_role(msg)
            msg_content = msg.content if isinstance(msg.content, str) else str(msg.content)

            # 修复：摘要 prompt 截断超长消息——此前巨型 tool 结果（可达 60 万字符）
            # 全文拼进摘要 prompt，使摘要生成请求本身超限（实测 165 万 token > 104 万
            # 上限）→ 压缩失败 → 上下文永远无法瘦身 → 会话 400 超限报废。
            if role == "tool" and len(msg_content) > MAX_TOOL_RESULT_CHARS:
                msg_content = (
                    msg_content[:MAX_TOOL_RESULT_CHARS]
                    + f"\n...[工具结果过长已截断，原 {len(msg_content)} 字符]"
                )
            elif len(msg_content) > MAX_MESSAGE_CHARS:
                msg_content = (
                    msg_content[:MAX_MESSAGE_CHARS]
                    + f"\n...[消息过长已截断，原 {len(msg_content)} 字符]"
                )

            # 添加角色标识
            if role == "user":
                content_parts.append(f"[用户消息]\n{msg_content}")
            elif role == "assistant":
                content_parts.append(f"[助手消息]\n{msg_content}")

                # 如果有tool_calls，也包含进去
                if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_name = tc.get("name", "unknown")
                        tool_args = str(tc.get("args", ""))
                        content_parts.append(f"[工具调用] {tool_name}\n{tool_args}")
            elif role == "tool":
                content_parts.append(f"[工具结果]\n{msg_content}")
            elif role == "system":
                # system消息通常不包含在可压缩区域中
                continue
            else:
                content_parts.append(f"[{role}]\n{msg_content}")

        return "\n\n".join(content_parts)

    def _get_message_role(self, msg: BaseMessage) -> str:
        """获取消息角色（委托给公共函数）"""
        return get_message_role(msg)

    def _truncate_oversized(
        self, messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        """摘要失败兜底：把超长消息截断后返回，让上下文能瘦下来。"""
        import copy as _copy

        truncated_count = 0
        cleaned: List[BaseMessage] = []
        for msg in messages:
            role = self._get_message_role(msg)
            content = msg.content if isinstance(msg.content, str) else None
            limit = (
                MAX_TOOL_RESULT_CHARS if role == "tool" else MAX_MESSAGE_CHARS
            )
            if content is not None and len(content) > limit:
                copy = _copy.copy(msg)
                copy.content = (
                    content[:limit]
                    + f"\n...[内容过长已截断，原 {len(content)} 字符]"
                )
                cleaned.append(copy)
                truncated_count += 1
            else:
                cleaned.append(msg)
        if truncated_count:
            logger.warning(
                "摘要失败兜底截断 %d 条超长消息（合计约 %d 字符）",
                truncated_count,
                sum(
                    len(m.content) if isinstance(m.content, str) else 0
                    for m in messages
                ),
            )
        return cleaned

    def _extract_summary(self, content: str) -> Optional[str]:
        """
        从模型响应中提取<summary>内容

        Args:
            content: 模型响应内容

        Returns:
            提取的摘要内容，失败返回None
        """
        # 使用正则表达式提取<summary>...</summary>
        pattern = r'<summary>(.*?)</summary>'
        match = re.search(pattern, content, re.DOTALL)

        if match:
            summary = match.group(1).strip()
            if summary:
                # 防御性清洗：LLM 可能在内容中误嵌套了 <summary>/</summary> 标签文本
                if summary.startswith("<summary>"):
                    summary = summary[len("<summary>"):].strip()
                if summary.endswith("</summary>"):
                    summary = summary[:-len("</summary>")].strip()
                if summary:
                    return summary

        # 如果没有找到<summary>标签，尝试提取整个内容
        # 但排除<analysis>部分
        analysis_pattern = r'<analysis>.*?</analysis>'
        content_without_analysis = re.sub(analysis_pattern, '', content, flags=re.DOTALL).strip()

        if content_without_analysis:
            logger.warning("未找到<summary>标签，使用去除<analysis>后的内容")
            return content_without_analysis

        return None

    def _build_new_messages(
        self,
        original_messages: List[BaseMessage],
        summary: str,
        recent_messages: List[BaseMessage]
    ) -> List[BaseMessage]:
        """
        构建新的消息列表

        Args:
            original_messages: 原始消息列表
            summary: 生成的摘要
            recent_messages: 最近区域的消息

        Returns:
            新的消息列表
        """
        new_messages = []

        # 1. 保留所有 SystemMessage（可能有多个，如注入的边界消息、规则消息等）
        for msg in original_messages:
            if isinstance(msg, SystemMessage):
                new_messages.append(msg)

        # 2. 插入摘要消息
        summary_message = AIMessage(content=f"<summary>\n{summary}\n</summary>")
        new_messages.append(summary_message)

        # 3. 保留最近区域的消息（过滤 SystemMessage 避免与步骤 1 重复注入）
        recent_non_system = [m for m in recent_messages if not isinstance(m, SystemMessage)]
        new_messages.extend(recent_non_system)

        return new_messages

    def get_compression_stats(
        self,
        original_messages: List[BaseMessage],
        compressed_messages: List[BaseMessage]
    ) -> Dict[str, Any]:
        """获取压缩统计信息"""
        from src.compression.utils import calc_compression_stats
        return calc_compression_stats(original_messages, compressed_messages)


# 全局实例
_full_compact_strategy: Optional[FullCompactStrategy] = None


def get_full_compact_strategy() -> FullCompactStrategy:
    """获取全局 FullCompactStrategy 实例"""
    global _full_compact_strategy
    if _full_compact_strategy is None:
        _full_compact_strategy = FullCompactStrategy()
    return _full_compact_strategy