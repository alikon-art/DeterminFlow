from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.core.model_manager import DEFAULT_MAX_CONTEXT_TOKENS, ModelManager


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_example_model_config_uses_one_api_key_field() -> None:
    document = json.loads(
        (REPO_ROOT / "config/models_config.example.json").read_text(encoding="utf-8")
    )

    assert document["providers"]["deepseek"]["api_key"] == "${DEEPSEEK_API_KEY}"
    assert document["providers"]["deepseek"]["base_url"] == (
        "https://api.deepseek.com/v1"
    )
    assert document["providers"]["deepseek"]["maxContextTokens"] == 128000
    assert all(
        "api_key_env" not in provider
        for provider in document["providers"].values()
    )


def test_provider_api_key_is_resolved_without_mutating_config(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "${TEST_PROVIDER_API_KEY}",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "test-key")

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == "test-key"
    assert manager.get_all_providers()["demo"]["api_key"] == "${TEST_PROVIDER_API_KEY}"

    manager.update_provider("demo", {"name": "Renamed"})
    assert manager.get_provider("demo")["name"] == "Renamed"


def test_provider_base_url_is_normalized_on_load(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "base_url": "  https://models.example.test/v1///  ",
                    "models": [],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))
    persisted = json.loads(config_path.read_text(encoding="utf-8"))

    assert manager.get_all_providers()["demo"]["base_url"] == (
        "https://models.example.test/v1"
    )
    assert persisted["providers"]["demo"]["base_url"] == (
        "https://models.example.test/v1"
    )


def test_provider_base_url_is_normalized_when_updated(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({"providers": {"demo": {"base_url": "", "models": []}}}),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    manager.update_provider("demo", {"base_url": "https://custom.example/v1/"})

    assert manager.get_all_providers()["demo"]["base_url"] == (
        "https://custom.example/v1"
    )


def test_provider_base_url_is_normalized_when_added(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    manager.add_provider(
        "custom",
        {"base_url": "https://custom.example/api/", "models": []},
    )

    assert manager.get_all_providers()["custom"]["base_url"] == (
        "https://custom.example/api"
    )


def test_empty_provider_base_url_is_supported(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({"providers": {"demo": {"base_url": "", "models": []}}}),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    manager.update_provider("demo", {"base_url": None})

    assert manager.get_all_providers()["demo"]["base_url"] == ""


def test_provider_context_window_can_be_updated_and_persisted(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    assert manager.get_model_info("demo:demo-model")["maxContextTokens"] == (
        DEFAULT_MAX_CONTEXT_TOKENS
    )

    manager.update_provider("demo", {"maxContextTokens": 64000})

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["providers"]["demo"]["maxContextTokens"] == 64000
    assert manager.get_model_info("demo:demo-model")["maxContextTokens"] == 64000


def test_provider_api_key_does_not_guess_an_environment_variable(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEMO_API_KEY", "fallback-key")

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == ""


def test_provider_api_key_supports_inline_values(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "inline-test-key",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == "inline-test-key"


def test_missing_environment_reference_resolves_to_unconfigured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "${MISSING_PROVIDER_API_KEY}",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_PROVIDER_API_KEY", raising=False)

    manager = ModelManager(str(config_path))

    assert manager.get_provider("demo")["api_key"] == ""
    assert manager.get_all_providers()["demo"]["api_key"] == "${MISSING_PROVIDER_API_KEY}"


def test_legacy_api_key_env_is_migrated_to_the_api_key_expression(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "demo": {
                    "name": "Demo",
                    "api_key": "",
                    "api_key_env": "DEMO_API_KEY",
                    "models": ["demo-model"],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))
    persisted = json.loads(config_path.read_text(encoding="utf-8"))

    assert manager.get_all_providers()["demo"]["api_key"] == "${DEMO_API_KEY}"
    assert "api_key_env" not in manager.get_all_providers()["demo"]
    assert persisted["providers"]["demo"]["api_key"] == "${DEMO_API_KEY}"
    assert "api_key_env" not in persisted["providers"]["demo"]


def test_missing_config_uses_the_default_expression_instead_of_env_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config" / "models_config.json"
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=legacy-test-value\nMODEL_NAME=legacy-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    manager = ModelManager(str(config_path))

    provider = manager.get_all_providers()["deepseek"]
    assert provider["api_key"] == "${DEEPSEEK_API_KEY}"
    assert provider["models"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert manager.config == json.loads(
        (REPO_ROOT / "config/models_config.example.json").read_text(encoding="utf-8")
    )


def test_default_model_config_path_can_use_a_secret_file(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    environment = os.environ.copy()
    environment["DETERMINFLOW_MODELS_CONFIG_FILE"] = str(config_path)

    result = subprocess.run(
        (
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from src.core.model_manager import ModelManager; "
            "manager = ModelManager(); "
            "assert manager.config_path == Path(sys.argv[1])",
            str(config_path),
        ),
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_unspecified_main_uses_first_provider_and_first_model(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "first": {"name": "First", "models": ["model-a", "model-b"]},
                "second": {"name": "Second", "models": ["model-c"]},
            }
        }),
        encoding="utf-8",
    )

    manager = ModelManager(str(config_path))

    assert manager.get_default_model() == "first:model-a"


def test_no_provider_has_no_hardcoded_default(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")

    manager = ModelManager(str(config_path))

    assert manager.get_default_model() is None
    assert manager.get_model_info() == {
        "provider_id": "",
        "model_name": "",
        "maxContextTokens": 128000,
        "provider_name": "",
    }


def test_provider_priority_controls_dynamic_main_default(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(
        json.dumps({
            "providers": {
                "first": {"name": "First", "models": ["model-a"]},
                "second": {"name": "Second", "models": ["model-b"]},
            }
        }),
        encoding="utf-8",
    )
    manager = ModelManager(str(config_path))

    manager.move_provider_to_front("second")

    assert list(manager.get_all_providers()) == ["second", "first"]
    assert manager.get_default_model() == "second:model-b"


def test_provider_templates_expose_reasoning_capabilities(tmp_path: Path) -> None:
    config_path = tmp_path / "models_config.json"
    config_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    manager = ModelManager(str(config_path))

    assert manager.get_provider_schema("openai")["default_base_url"] == (
        "https://api.openai.com/v1"
    )
    assert manager.get_provider_schema("deepseek")["default_base_url"] == (
        "https://api.deepseek.com/v1"
    )
    assert manager.get_provider_capabilities("deepseek") == {
        "reasoning_efforts": ["low", "medium", "high", "max"],
    }
    assert manager.get_provider_capabilities("alibaba") == {
        "reasoning_efforts": [],
    }


def test_default_agent_definitions_inherit_the_main_model() -> None:
    agents_config_path = Path(__file__).parents[1] / "config" / "agents_config.json"
    agents = json.loads(agents_config_path.read_text(encoding="utf-8"))["agents"]

    assert agents["main"]["model"] is None
    assert all(definition["model"] is None for definition in agents.values())
