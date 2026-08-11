"""Stage an explicit, secret-free configuration set for desktop packaging."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("desktop.stage_defaults")

SOURCE_CONFIG_FILES = (
    "agents_config.json",
    "compression_config.json",
    "llm_pricing.json",
    "preset_phrases.json",
    "prompts_config.json",
    "rules_config.json",
    "settings.json",
    "skills_config.json",
    "tool_groups_config.json",
    "user_injection_config.json",
)

DESKTOP_OVERRIDES: dict[str, Any] = {
    "extensions.json": {"enabled": [], "strict_startup": False},
    "mcp_servers.json": {"mcpServers": {}},
    "plugin-sources.json": {
        "schema_version": 1,
        "official_sources": [
            {
                "id": "determinflow-official",
                "name": "DeterminFlow Official Plugins",
                "url": "https://github.com/alikon-art/DeterminFlow-Plugins.git",
                "mirrors": [
                    "https://gitee.com/alikon/DeterminFlow-Plugins.git"
                ],
                "ref": "main",
            }
        ],
        "custom_sources": [],
    },
}

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
}


def _read_git_json(repo_root: Path, relative_path: str) -> Any:
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{relative_path}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # 非 git 副本（如 OneDrive 同步目录）无 HEAD 可读，回退到工作区文件。
        # 明文密钥校验由调用方的 _validate_no_plaintext_secrets 统一兜底。
        source = repo_root / relative_path
        return json.loads(source.read_text(encoding="utf-8"))


def _validate_no_plaintext_secrets(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key.lower() in SENSITIVE_KEYS and isinstance(child, str):
                if child and not (child.startswith("${") and child.endswith("}")):
                    raise ValueError(f"桌面默认配置包含明文凭据: {child_location}")
            _validate_no_plaintext_secrets(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_plaintext_secrets(child, f"{location}[{index}]")


def build_payloads(repo_root: Path) -> dict[str, Any]:
    payloads = {
        name: _read_git_json(repo_root, f"config/{name}")
        for name in SOURCE_CONFIG_FILES
    }
    payloads.update(DESKTOP_OVERRIDES)
    model_template = _read_git_json(
        repo_root, "config/models_config.example.json"
    )
    payloads["models_config.example.json"] = model_template
    payloads["models_config.json"] = model_template
    for name, payload in payloads.items():
        _validate_no_plaintext_secrets(payload, name)
    return payloads


def stage_defaults(repo_root: Path, output_dir: Path) -> list[Path]:
    payloads = build_payloads(repo_root)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix="default-config-", dir=output_dir.parent)
    )
    try:
        for name, payload in sorted(payloads.items()):
            (temporary / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if output_dir.exists():
            shutil.rmtree(output_dir)
        os.replace(temporary, output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return sorted(path for path in output_dir.iterdir() if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    repo_root = options.repo_root.resolve()
    output = options.output or repo_root / "desktop" / "generated" / "default-config"
    files = stage_defaults(repo_root, output.resolve())
    LOGGER.info("已生成 %d 个桌面默认配置", len(files))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
