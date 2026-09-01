"""LLM 配置工厂 - 根据 Provider Adapter 创建对应的聊天模型客户端。"""
import asyncio
import logging
from typing import Any, Literal

from anthropic import BadRequestError as AnthropicBadRequestError
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.config import ensure_config, merge_configs
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models import base as openai_base
from openai import BadRequestError as OpenAIBadRequestError
from pydantic import ValidationError

import os
import warnings

logger = logging.getLogger(__name__)
PROVIDER_BAD_REQUEST_ERRORS = (OpenAIBadRequestError, AnthropicBadRequestError)


class LLMEmptyOrMalformedResponse(Exception):
    """Wraps a LangChain/Provider ``ValidationError`` that fires when an
    otherwise-successful HTTP response cannot be assembled into a
    :class:`langchain_core.messages.BaseMessage`.

    The reactive compact loop in :mod:`src.core.graph_builder` consumes this
    exception to discard the oldest message round and retry, instead of
    aborting the active sub-session on what is usually a transient upstream
    truncation or content-filter quirk.
    """

    def __init__(self, original: BaseException) -> None:
        super().__init__(
            f"LLM returned a chunk LangChain could not validate: "
            f"{type(original).__name__}: {original}"
        )
        self.original = original


class ModelCredentialNotConfiguredError(ValueError):
    """The selected model provider has no usable API credential."""


class ModelConfigurationNotAvailableError(ValueError):
    """No configured provider/model is available for an actual LLM call."""


# ============================================================
# Monkey Patch 版本兼容性检查
# ============================================================

_EXPECTED_LANGCHAIN_OPENAI_VERSIONS = {"1.3"}  # 与 requirements.txt 锁定版本一致
try:
    import langchain_openai as _lc_openai_pkg
    _lc_version = getattr(_lc_openai_pkg, "__version__", "unknown")
    _lc_major_minor = ".".join(_lc_version.split(".")[:2]) if _lc_version != "unknown" else "unknown"
    if _lc_major_minor not in _EXPECTED_LANGCHAIN_OPENAI_VERSIONS:
        logger.warning(
            f"langchain_openai {_lc_version} 未在已验证版本 {_EXPECTED_LANGCHAIN_OPENAI_VERSIONS} 中，"
            f"Monkey Patch 可能不兼容。若出现 AttributeError 或行为异常，请降级 langchain-openai。"
        )
    else:
        logger.debug(f"langchain_openai {_lc_version} 版本检查通过")
except ImportError:
    logger.warning("langchain_openai 未安装，Monkey Patch 跳过")


# ============================================================
# Monkey Patch 1: 消息转换 - 将 reasoning_content 传回 API
# ============================================================

_original_convert_message_to_dict = openai_base._convert_message_to_dict


def _patched_convert_message_to_dict(
    message: BaseMessage,
    api: Literal["chat/completions", "responses"] = "chat/completions",
) -> dict:
    """
    修补版本的消息转换函数，支持 DeepSeek V4 的 reasoning_content

    DeepSeek V4 思考模式要求：
    - 在多轮对话中，必须将之前的 reasoning_content 传回 API
    - reasoning_content 应该在 AIMessage 的 additional_kwargs 中
    """
    message_dict = _original_convert_message_to_dict(message, api)

    # 如果是 AIMessage 且包含 reasoning_content，添加到消息字典中
    if isinstance(message, AIMessage) and hasattr(message, "additional_kwargs"):
        reasoning_content = message.additional_kwargs.get("reasoning_content")
        if reasoning_content:
            message_dict["reasoning_content"] = reasoning_content
            logger.debug(f"[PATCH 1] 添加 reasoning_content 到消息字典 (长度: {len(reasoning_content)})")

    return message_dict


openai_base._convert_message_to_dict = _patched_convert_message_to_dict


# ============================================================
# Monkey Patch 2: 响应解析 - 从 API 响应提取 reasoning_content
# ============================================================

_original_convert_delta_to_message_chunk = openai_base._convert_delta_to_message_chunk


def _patched_convert_delta_to_message_chunk(
    _dict: Any,
    default_class: Any,
) -> Any:
    """
    修补版本的 delta 转换函数，支持提取 reasoning_content

    DeepSeek V4 在流式响应中会返回 reasoning_content 字段，
    需要：
    1. 将其作为 chunk 的直接属性（用于流式显示）
    2. 将其添加到 additional_kwargs（用于多轮对话传回）
    """
    chunk = _original_convert_delta_to_message_chunk(_dict, default_class)

    # 如果响应中包含 reasoning_content，进行双重处理
    if "reasoning_content" in _dict:
        reasoning_content = _dict.get("reasoning_content")
        if reasoning_content:
            # 1. 作为 chunk 的直接属性（用于 session.py 中的流式提取）
            # 使用 setattr 防止 frozen/cached_property 对象抛出 AttributeError
            try:
                setattr(chunk, "reasoning_content", reasoning_content)
            except AttributeError:
                pass  # 某些 frozen/cached_property 对象无法设置额外属性

            # 2. 添加到 additional_kwargs（用于多轮对话时传回 API）
            if hasattr(chunk, "additional_kwargs"):
                chunk.additional_kwargs["reasoning_content"] = reasoning_content

    return chunk


openai_base._convert_delta_to_message_chunk = _patched_convert_delta_to_message_chunk


# ============================================================
# Monkey Patch 3: 消息解析 - 兼容非标准 role 下的 content=None
# ============================================================

_original_convert_dict_to_message = openai_base._convert_dict_to_message


def _patched_convert_dict_to_message(_dict):
    """
    修补版本的消息解析函数，兼容 openai_compatible 中转站的 content=None

    langchain-openai 的 _convert_dict_to_message 对 user/assistant/tool 等
    标准 role 做了 content None 防护（assistant 分支为 `or ""`），但对未知
    role 的 fallback 分支使用 `_dict.get("content", "")`——当上游返回
    "content": null（键存在）时 get 返回 None 而非默认值，ChatMessage 随即
    在 Pydantic 校验处抛出 "2 validation errors for ChatMessage"。
    观测到的触发场景：DeepSeek V4 reasoning 响应经中转站返回非标准 role
    且 content 为 null。此处统一把 null content 归一为空字符串。
    """
    if _dict.get("content") is None:
        _dict = {**_dict, "content": ""}
    return _original_convert_dict_to_message(_dict)


openai_base._convert_dict_to_message = _patched_convert_dict_to_message


# ============================================================
# 应用补丁完成
# ============================================================

logger.info("已应用 DeepSeek V4 reasoning_content 支持补丁")


def _resolve_provider_config(
    model_override: str,
    model_manager: Any,
) -> tuple[str, str, str, str, dict]:
    """解析 provider_id:model_name 格式，返回 (provider_id, model_name, api_key, base_url, provider_config)。

    Raises:
        ValueError: provider 不存在或格式不正确
    """
    provider_id, model_name = model_override.split(":", 1)
    provider_config = model_manager.get_provider(provider_id)
    if not provider_config:
        raise ModelConfigurationNotAvailableError(
            f"供应商 {provider_id} 不存在；请在设置页选择可用模型"
        )
    if model_name not in provider_config.get("models", []):
        raise ModelConfigurationNotAvailableError(
            f"模型 {model_override} 未配置；请在设置页选择可用模型"
        )
    api_key = provider_config.get("api_key", "")
    if not api_key:
        raise ModelCredentialNotConfiguredError(
            f"Provider {provider_id} 的 api_key 未配置；"
            "请在模型设置页面填写，或在 models_config.json 中引用环境变量"
        )
    return provider_id, model_name, api_key, provider_config["base_url"], provider_config


def _merge_params(
    model_manager: Any,
    provider_id: str,
    model_name: str,
    provider_config: dict,
    model_params: dict | None,
    kwargs: dict,
) -> dict:
    """合并模型参数和 provider 超参数到 kwargs（原地修改）。

    合并优先级: agent model_params > defaults；provider hyperparams 作为 fallback。

    具体字段选择、校验和 API 参数映射由 Provider Adapter 完成。
    """
    # 合并模型参数：agent model_params > defaults
    defaults = model_manager.get_default_params()
    merged_params = dict(defaults)
    if model_params:
        merged_params.update(
            {
                key: value
                for key, value in model_params.items()
                if value is not None or key == "response_format"
            }
        )

    request = model_manager.build_provider_request(
        provider_id, merged_params, provider_config, model_name=model_name
    )
    for name, value in request.get("client_kwargs", {}).items():
        kwargs.setdefault(name, value)
    if request.get("extra_body"):
        kwargs.setdefault("extra_body", {}).update(request["extra_body"])
    if request.get("model_kwargs"):
        kwargs.setdefault("model_kwargs", {}).update(request["model_kwargs"])
    return merged_params


def _resolve_model_override(model_override: str | None, model_manager) -> str:
    """
    将 model_override 解析为标准 "provider_id:model_name" 格式。

    支持三种输入：
    1. 新格式 "provider_id:model_name" → 直接返回
    2. 纯模型名 → 在所有 provider 的 models 列表中查找匹配，返回 "pid:model_name"
    3. None → 使用默认模型

    Returns:
        "provider_id:model_name" 格式的字符串

    Raises:
        ValueError: 无法解析时抛出
    """
    # 新格式：直接返回
    if model_override and ":" in model_override:
        return model_override

    # 旧格式：纯模型名，按名称查找供应商
    if model_override:
        for pid, pconfig in model_manager.get_all_providers().items():
            if model_override in pconfig.get("models", []):
                return f"{pid}:{model_override}"

    # 使用默认模型（从 agents_config.json main agent 读取）
    default_model = model_manager.get_default_model()
    if default_model and ":" in default_model:
        return default_model

    raise ModelConfigurationNotAvailableError(
        "尚未配置可用模型；请先在设置页添加供应商和模型"
    )


def _wrap_llm_with_retry(llm: BaseChatModel, retry_config: dict) -> BaseChatModel:
    """
    为 ChatOpenAI 实例的 ainvoke 和 astream 方法注入自动重试机制。

    当 LLM 调用失败（服务端错误、限流、网络不可达等），自动按配置的重试次数
    和间隔时间重试，最后一次失败则抛出原始异常。

    Args:
        llm: ChatOpenAI 实例
        retry_config: {"max_retries": 3, "delays": [5, 15, 30]}

    Returns:
        包装后的 ChatOpenAI 实例（就地修改）
    """
    max_retries = retry_config.get("max_retries", 3)
    delays = retry_config.get("delays", [5, 15, 30])

    if max_retries <= 0:
        return llm

    # 确保 delays 长度足够（不足时用最后一个值填充）
    delays = list(delays)
    while len(delays) < max_retries:
        delays.append(delays[-1] if delays else 30)

    # ---- 包装 ainvoke ----
    original_ainvoke = llm.ainvoke

    class _StreamingCallbackTracker(BaseCallbackHandler):
        """Observe chunks emitted by ``ainvoke`` through LangChain callbacks."""

        def __init__(self):
            self.chunk_count = 0

        def on_llm_new_token(self, _token, **_kwargs):
            self.chunk_count += 1

    def _tracked_ainvoke_arguments(args, kwargs, tracker):
        tracked_args = list(args)
        tracked_kwargs = dict(kwargs)
        tracker_config = {"callbacks": [tracker]}
        if tracked_args:
            tracked_args[0] = merge_configs(
                ensure_config(tracked_args[0]),
                tracker_config,
            )
        else:
            tracked_kwargs["config"] = merge_configs(
                ensure_config(tracked_kwargs.get("config")),
                tracker_config,
            )
        return tuple(tracked_args), tracked_kwargs

    def _mark_failed_provider_usage(error, *, partial_stream_emitted):
        # Failed provider calls frequently have no final usage payload. Keep
        # this explicit on the propagated error in addition to the audit log.
        for attribute, value in (
            ("llm_partial_stream_emitted", partial_stream_emitted),
            ("llm_provider_usage_status", "unavailable_on_failed_attempt"),
            ("llm_retry_suppressed", partial_stream_emitted),
        ):
            try:
                setattr(error, attribute, value)
            except (AttributeError, TypeError):
                pass

    async def ainvoke_with_retry(input, *args, **kwargs):
        last_error = None
        for attempt in range(max_retries + 1):
            tracker = _StreamingCallbackTracker()
            tracked_args, tracked_kwargs = _tracked_ainvoke_arguments(
                args,
                kwargs,
                tracker,
            )
            try:
                return await original_ainvoke(
                    input,
                    *tracked_args,
                    **tracked_kwargs,
                )
            except PROVIDER_BAD_REQUEST_ERRORS as error:
                # 400 错误是永久性错误（输入格式非法），重试无意义，直接抛出
                _mark_failed_provider_usage(
                    error,
                    partial_stream_emitted=tracker.chunk_count > 0,
                )
                logger.error(
                    "LLM ainvoke BadRequestError (400)，不重试 "
                    "(provider_usage_status=unavailable_on_failed_attempt)"
                )
                raise
            except Exception as e:
                last_error = e
                partial_stream_emitted = tracker.chunk_count > 0
                _mark_failed_provider_usage(
                    e,
                    partial_stream_emitted=partial_stream_emitted,
                )
                if partial_stream_emitted:
                    logger.error(
                        "LLM ainvoke 在 streaming callback 输出开始后中断；"
                        "拒绝原地重试以避免拼接重复响应 "
                        "(retry_suppressed=partial_stream, "
                        "provider_usage_status=unavailable_on_failed_attempt, "
                        f"chunks={tracker.chunk_count}, "
                        f"error={type(e).__name__}: {e!s})"
                    )
                    raise
                if attempt < max_retries:
                    delay = delays[attempt]
                    logger.warning(
                        f"LLM ainvoke 失败，将重试 "
                        f"(attempt {attempt + 1}/{max_retries}, "
                        f"delay={delay}s, error={type(e).__name__}: {e!s})"
                        "; provider_usage_status=unavailable_on_failed_attempt"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"LLM ainvoke 失败，已耗尽所有重试 "
                        f"(max_retries={max_retries}, "
                        f"error={type(last_error).__name__}: {last_error!s})"
                        "; provider_usage_status=unavailable_on_failed_attempt"
                    )
                    raise

    object.__setattr__(llm, 'ainvoke', ainvoke_with_retry)

    # ---- 包装 astream ----
    original_astream = llm.astream

    async def astream_with_retry(input, *args, **kwargs):
        last_error = None
        for attempt in range(max_retries + 1):
            yielded_chunk = False
            try:
                async for chunk in original_astream(input, *args, **kwargs):
                    yielded_chunk = True
                    yield chunk
                return  # 流正常完成
            except PROVIDER_BAD_REQUEST_ERRORS:
                # 400 错误是永久性错误（输入格式非法），重试无意义，直接抛出
                logger.error(f"LLM astream BadRequestError (400)，不重试")
                raise
            except ValidationError as validation_error:
                # LangChain assembles each chunk into a BaseMessage at the
                # boundary of astream. When the upstream response carries
                # ``content=None``, the Pydantic model raises here even though
                # the HTTP layer succeeded. Surface as a structured exception
                # so the reactive compact loop can recover.
                raise LLMEmptyOrMalformedResponse(validation_error) from validation_error
            except Exception as e:
                last_error = e
                # Once a chunk reached the consumer, restarting this request
                # would append a second response to the first partial one.
                # Let the enclosing Workflow attempt restart from a clean
                # output boundary instead.
                if yielded_chunk:
                    logger.error(
                        "LLM astream 在输出开始后中断；拒绝原地重试以避免拼接重复响应 "
                        f"(error={type(e).__name__}: {e!s})"
                    )
                    raise
                if attempt < max_retries:
                    delay = delays[attempt]
                    logger.warning(
                        f"LLM astream 失败，将重试 "
                        f"(attempt {attempt + 1}/{max_retries}, "
                        f"delay={delay}s, error={type(e).__name__}: {e!s})"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"LLM astream 失败，已耗尽所有重试 "
                        f"(max_retries={max_retries}, "
                        f"error={type(last_error).__name__}: {last_error!s})"
                    )
                    raise

    object.__setattr__(llm, 'astream', astream_with_retry)

    logger.info(f"LLM 重试机制已注入: max_retries={max_retries}, delays={delays[:max_retries]}")
    return llm


class DeferredChatModel:
    """Delay model construction until a request needs the provider credential."""

    def __init__(
        self,
        factory_options: dict[str, Any],
        *,
        tools: list[Any] | None = None,
        bind_options: dict[str, Any] | None = None,
    ) -> None:
        self._factory_options = dict(factory_options)
        self._tools = list(tools) if tools is not None else None
        self._bind_options = dict(bind_options or {})
        self._delegate: Any = None

    def bind_tools(self, tools: list[Any], **kwargs) -> "DeferredChatModel":
        return DeferredChatModel(
            self._factory_options,
            tools=tools,
            bind_options=kwargs,
        )

    def _resolve(self) -> Any:
        if self._delegate is None:
            delegate = create_llm(**self._factory_options)
            if self._tools is not None:
                delegate = delegate.bind_tools(self._tools, **self._bind_options)
            self._delegate = delegate
        return self._delegate

    def invoke(self, *args, **kwargs):
        return self._resolve().invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        return await self._resolve().ainvoke(*args, **kwargs)

    def stream(self, *args, **kwargs):
        yield from self._resolve().stream(*args, **kwargs)

    async def astream(self, *args, **kwargs):
        async for chunk in self._resolve().astream(*args, **kwargs):
            yield chunk


def create_llm(
    model_override: str | None = None,
    model: str | None = None,
    streaming: bool = True,
    model_params: dict | None = None,
    provider_retries_enabled: bool = True,
    **kwargs,
) -> BaseChatModel:
    """
    根据 Provider 声明创建 OpenAI-compatible 或 Anthropic 聊天模型实例。

    Args:
        model_override: 模型覆盖，格式 "provider_id:model_name"（新格式）或纯模型名（旧格式）
        model: 模型名称（旧格式，向后兼容）
        streaming: 是否支持流式输出，默认 True
        model_params: 模型参数字典，包含 thinking_enabled / reasoning_effort / temperature / top_p
                     未设置时从 models_config.json 的 default_params 继承
        provider_retries_enabled: 是否启用 SDK 与 Core Provider 传输重试；探针等一次性调用应关闭
        **kwargs: 传递给 Provider 客户端的额外参数

    Returns:
        配置好的聊天模型实例
    """
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()

    # 第一阶段：解析为标准 "provider_id:model_name" 格式（无递归）
    resolved = _resolve_model_override(model_override, model_manager)

    # 第二阶段：由 Provider Adapter 构造对应协议的客户端
    provider_id, model_name, api_key, base_url, provider_config = _resolve_provider_config(
        resolved, model_manager
    )
    if not provider_retries_enabled:
        # LangChain clients have their own SDK retry policy in addition to the
        # Core wrapper below. One-shot callers must disable both layers.
        kwargs["max_retries"] = 0
    try:
        api_format = model_manager.get_provider_api_format(provider_id, model_name)
        client_base_url = model_manager.get_provider_client_base_url(
            provider_id, base_url, model_name
        )
    except ValueError as exc:
        raise ModelConfigurationNotAvailableError(str(exc)) from exc
    merged_params = _merge_params(
        model_manager,
        provider_id,
        model_name,
        provider_config,
        model_params,
        kwargs,
    )

    logger.info(
        f"使用 ModelManager 配置: provider={provider_id}, model={model_name}, "
        f"base_url={base_url}, thinking={merged_params.get('thinking_enabled')}, "
        f"temperature={merged_params.get('temperature')}"
    )

    # OpenAI-compatible 流式输出需显式请求 token usage。
    if streaming and api_format == "openai":
        model_kwargs = kwargs.get("model_kwargs", {})
        if "stream_options" not in model_kwargs:
            model_kwargs["stream_options"] = {"include_usage": True}
            kwargs["model_kwargs"] = model_kwargs

    if api_format == "anthropic":
        llm: BaseChatModel = ChatAnthropic(
            model=model_name,
            api_key=api_key,
            base_url=client_base_url,
            streaming=streaming,
            stream_usage=True,
            **kwargs,
        )
    else:
        if api_format == "openai_responses":
            kwargs["use_responses_api"] = True
        llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=client_base_url,
            streaming=streaming,
            **kwargs,
        )

    if provider_retries_enabled:
        retry_config = model_manager.get_retry_config()
        _wrap_llm_with_retry(llm, retry_config)

    logger.info(
        f"LLM 实例已创建: model={model_name}, temperature={kwargs.get('temperature', 'default')}, "
        f"streaming={streaming}"
    )
    return llm


def create_startup_llm(
    model_override: str | None = None,
    model: str | None = None,
    streaming: bool = True,
    model_params: dict | None = None,
    **kwargs,
) -> BaseChatModel | DeferredChatModel:
    """Create the startup model, deferring only missing-credential failures."""
    options = {
        "model_override": model_override,
        "model": model,
        "streaming": streaming,
        "model_params": model_params,
        **kwargs,
    }
    try:
        return create_llm(**options)
    except (ModelCredentialNotConfiguredError, ModelConfigurationNotAvailableError):
        logger.warning(
            "模型或凭据尚未配置，服务将继续启动；可在模型设置页面完成配置"
        )
        return DeferredChatModel(options)
