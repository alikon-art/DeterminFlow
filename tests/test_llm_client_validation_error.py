"""Sub-session abort guard:

When a LangChain ``BaseMessage`` cannot be assembled from a chunk (typical
failure mode: ``content`` is ``None``), ``astream`` raises ``ValidationError``
inside the provider adapter. The DeterminFlow retry wrapper should turn this
into a known, recoverable :class:`LLMEmptyOrMalformedResponse` instead of
letting the LangGraph subgraph abort the whole sub-session.

These tests do not reach a real LLM; they substitute the wrapped object's
``astream`` method with side-effecting callables and assert the wrapper
surfaces them as the expected structured exception.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.llm_client import (
    LLMEmptyOrMalformedResponse,
    PROVIDER_BAD_REQUEST_ERRORS,
    _wrap_llm_with_retry,
)


def _make_validation_error() -> ValidationError:
    """Build a realistic Pydantic v2 ``ValidationError`` for
    ``BaseMessage.content`` failing the ``str`` field validator."""
    return ValidationError.from_exception_data(
        title="BaseMessage",
        line_errors=[
            {
                "type": "string_type",
                "loc": ("content", "str"),
                "input": None,
                "msg": "Input should be a valid string",
            }
        ],
    )


def _llm_shell():
    """A duck-typed object that the wrapper mutates in place via
    ``object.__setattr__``. We do not need a real ``BaseChatModel`` because the
    wrapper only ever reads / replaces ``ainvoke`` and ``astream`` on it."""
    return SimpleNamespace(ainvoke=None, astream=None)


def _drive(agen):
    """Consume an async generator to completion, mirroring how the upstream
    ``llm.astream(...)`` caller would invoke it from a sync test driver."""
    async def _run():
        async for _chunk in agen:
            pass

    return asyncio.run(_run())


def test_astream_validation_error_is_wrapped() -> None:
    inner = _make_validation_error()

    async def _raise(_input, *_args, **_kwargs):
        if False:  # pragma: no cover - shape marker for type checkers
            yield None
        raise inner

    llm = SimpleNamespace(ainvoke=None, astream=_raise)
    _wrap_llm_with_retry(llm, {"max_retries": 3, "delays": [0, 0, 0]})

    with pytest.raises(LLMEmptyOrMalformedResponse) as excinfo:
        _drive(llm.astream([]))

    assert excinfo.value.original is inner
    # ensure __cause__ is the original ValidationError so tracebacks stay useful
    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_astream_provider_bad_request_skips_retry_path() -> None:
    """A 400-class error from ``astream`` must surface as the original provider
    exception and NOT be wrapped into ``LLMEmptyOrMalformedResponse``."""

    class _DummyProviderBadRequest(Exception):
        pass

    bad_request = _DummyProviderBadRequest("simulated 400")

    # Monkey-patch PROVIDER_BAD_REQUEST_ERRORS for this test only, then restore
    # via try/finally. The wrapper uses this tuple to decide fast-path raises,
    # so we must include our marker class for the assertion to be meaningful.
    original = PROVIDER_BAD_REQUEST_ERRORS
    import src.core.llm_client as llm_client_module

    llm_client_module.PROVIDER_BAD_REQUEST_ERRORS = original + (_DummyProviderBadRequest,)
    try:
        llm = _llm_shell()
        _wrap_llm_with_retry(llm, {"max_retries": 3, "delays": [0, 0, 0]})

        async def _raise(_input, *_args, **_kwargs):
            if False:  # pragma: no cover
                yield None
            raise bad_request

        object.__setattr__(llm, "astream", _raise)

        with pytest.raises(_DummyProviderBadRequest):
            _drive(llm.astream([]))
    finally:
        llm_client_module.PROVIDER_BAD_REQUEST_ERRORS = original


def test_convert_dict_to_message_coerces_null_content() -> None:
    """Monkey Patch 3: a message dict with a non-standard ``role`` and
    ``content: null`` must not crash ChatMessage validation.

    langchain-openai's fallback branch uses ``_dict.get("content", "")`` which
    returns ``None`` when the key exists with a null value. The DeterminFlow
    patch coerces null content to "" before delegating.
    """
    from langchain_openai.chat_models import base as openai_base

    # The patch is applied at llm_client import time; openai_base was already
    # imported here so fetch the patched callable through the module attr.
    import src.core.llm_client as llm_client_module  # noqa: F401

    patched = openai_base._convert_dict_to_message

    message = patched({"role": "custom_nonstandard_role", "content": None})
    assert message.content == ""

    # standard roles keep working through the original converter
    assistant = patched({"role": "assistant", "content": None})
    assert assistant.content == ""

    # absent content key still defaults to ""
    default = patched({"role": "custom_nonstandard_role"})
    assert default.content == ""
