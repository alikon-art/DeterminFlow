from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from desktop.python.entrypoint import _run_python_compatibility_mode
from desktop.python.runtime import (
    refresh_official_plugin_sources,
    seed_bundled_plugins,
    seed_user_config,
)
from desktop.scripts import stage_defaults as defaults_module
from desktop.scripts.create_update_manifest import create_manifest
from desktop.scripts.verify_bundle import (
    verify_bundled_plugins,
    verify_defaults,
    verify_updater_signature,
    verify_windows_gui_executable,
    write_checksum,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tauri_bundle_is_a_per_user_nsis_installer() -> None:
    config = json.loads(
        (REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )

    bundle = config["bundle"]
    nsis = bundle["windows"]["nsis"]

    assert config["productName"] == "DeterminFlow"
    assert bundle["targets"] == ["nsis", "app", "dmg"]
    assert bundle["createUpdaterArtifacts"] is True
    assert bundle["icon"] == ["icons/icon.icns", "icons/icon.ico", "icons/icon.png"]
    assert bundle["macOS"]["minimumSystemVersion"] == "10.15"
    assert bundle["macOS"]["dmg"]["windowSize"] == {"width": 660, "height": 400}
    assert nsis["installMode"] == "currentUser"
    assert nsis["installerIcon"] == "icons/icon.ico"
    assert nsis["installerHooks"] == "./windows/installer-hooks.nsh"
    assert nsis["uninstallerIcon"] == "icons/icon.ico"
    assert nsis["headerImage"] == "images/nsis-header.bmp"
    assert nsis["sidebarImage"] == "images/nsis-sidebar.bmp"
    assert nsis["uninstallerHeaderImage"] == "images/nsis-header.bmp"
    assert (
        bundle["windows"]["webviewInstallMode"]["type"]
        == "downloadBootstrapper"
    )
    updater = config["plugins"]["updater"]
    assert updater["endpoints"] == [
        "https://github.com/alikon-art/DeterminFlow/releases/latest/download/latest.json"
    ]
    assert len(updater["pubkey"]) > 100
    assert b"minisign public key" in base64.b64decode(
        updater["pubkey"], validate=True
    )

    icons_dir = REPO_ROOT / "desktop" / "src-tauri" / "icons"
    assert (icons_dir / "icon.ico").stat().st_size > 1024
    assert (icons_dir / "icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (icons_dir / "icon.icns").read_bytes().startswith(b"icns")

    images_dir = REPO_ROOT / "desktop" / "src-tauri" / "images"
    for name, dimensions in {
        "nsis-header.bmp": (150, 57),
        "nsis-sidebar.bmp": (164, 314),
    }.items():
        image = (images_dir / name).read_bytes()
        assert image[:2] == b"BM"
        assert int.from_bytes(image[18:22], "little") == dimensions[0]
        assert int.from_bytes(image[22:26], "little") == dimensions[1]


def test_desktop_update_capability_only_trusts_the_bundled_loopback_ui() -> None:
    capability = json.loads(
        (REPO_ROOT / "desktop" / "src-tauri" / "capabilities" / "desktop-update.json")
        .read_text(encoding="utf-8")
    )

    assert capability["windows"] == ["main"]
    assert capability["remote"]["urls"] == ["http://127.0.0.1:*/*"]
    assert capability["permissions"] == [
        "core:app:allow-version",
        "updater:default",
        "process:allow-restart",
    ]


def test_desktop_file_drop_capability_only_trusts_the_bundled_loopback_ui() -> None:
    capability = json.loads(
        (REPO_ROOT / "desktop" / "src-tauri" / "capabilities" / "desktop-file-drop.json")
        .read_text(encoding="utf-8")
    )

    assert capability["windows"] == ["main"]
    assert capability["remote"]["urls"] == ["http://127.0.0.1:*/*"]
    assert capability["permissions"] == ["core:event:default"]


def test_tauri_release_shell_uses_the_windows_gui_subsystem() -> None:
    main_source = (REPO_ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(
        encoding="utf-8"
    )

    assert (
        '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]'
        in main_source
    )


def test_desktop_lifecycle_cleans_up_the_backend_before_every_exit() -> None:
    main_source = (REPO_ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(
        encoding="utf-8"
    )
    updater_context = (
        REPO_ROOT / "web" / "src" / "desktop-updater" / "context.tsx"
    ).read_text(encoding="utf-8")
    hooks = (
        REPO_ROOT
        / "desktop"
        / "src-tauri"
        / "windows"
        / "installer-hooks.nsh"
    ).read_text(encoding="utf-8")
    updater_source = (
        REPO_ROOT / "desktop" / "src-tauri" / "src" / "updater.rs"
    ).read_text(encoding="utf-8")

    single_instance = main_source.index("tauri_plugin_single_instance::init")
    process_plugin = main_source.index("tauri_plugin_process::init")
    updater_plugin = main_source.index("tauri_plugin_updater::Builder::new")
    assert single_instance < process_plugin < updater_plugin
    assert "fn prepare_for_update" in main_source
    assert "prepare_for_update," in main_source
    assert "updater::check_update_sources," in main_source
    assert "releases/latest/download/latest.json" in updater_source
    assert "gitee.com/api/v5/repos/alikon/DeterminFlow/releases/latest" in updater_source
    assert "github_version > gitee_version" in updater_source
    assert "primary: UpdateSource::Gitee" in updater_source
    assert "fallback: Some(UpdateSource::Github)" in updater_source
    assert "fallback_rid" in updater_source
    assert 'invoke<UpdateMetadata | null>("check_update_sources")' in updater_context
    assert "downloadUpdateWithFallback(" in updater_context
    assert "fallbackRid" in updater_context
    assert 'matches!(event, RunEvent::Exit)' in main_source
    assert main_source.count(".stop();") >= 2
    download = updater_context.index("await candidate.download(")
    prepare = updater_context.index('await invoke("prepare_for_update")')
    install = updater_context.index("await downloadedResource.install()")
    assert download < prepare < install
    assert "downloadAndInstall" not in updater_context
    assert "NSIS_HOOK_PREINSTALL" in hooks
    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert "/IM determinflow-backend.exe" in hooks


def test_desktop_workflow_builds_candidates_and_publishes_tags() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "desktop-windows.yml"
    ).read_text(encoding="utf-8")

    assert "runs-on: windows-2025" in workflow
    assert "matrix.flavor" in workflow
    assert "--flavor ${{ matrix.flavor }}" in workflow
    assert "desktop/scripts/smoke_backend.py" in workflow
    assert "desktop/scripts/smoke_installer.ps1" in workflow
    assert '-Flavor "${{ matrix.flavor }}"' in workflow
    assert "--expected-flavor ${{ matrix.flavor }}" in workflow
    assert "--desktop-executable" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "TAURI_SIGNING_PRIVATE_KEY" in workflow
    assert "--updater-signature" in workflow
    assert "tauri-action" not in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "gh release create" in workflow.lower()
    assert "contents: write" in workflow
    assert "release-assets/latest.json" in workflow

    installer_smoke = (
        REPO_ROOT / "desktop" / "scripts" / "smoke_installer.ps1"
    ).read_text(encoding="utf-8")
    assert "CloseMainWindow" in installer_smoke
    assert "Second launch created duplicate backends" in installer_smoke
    assert "NSIS reinstall with a stale backend" in installer_smoke


def test_stage_defaults_uses_sanitized_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_read(_repo_root: Path, relative_path: str) -> object:
        if relative_path.endswith("models_config.example.json"):
            return {"providers": {"safe": {"api_key": "${SAFE_API_KEY}"}}}
        return {"source": relative_path}

    monkeypatch.setattr(defaults_module, "_read_git_json", fake_read)
    output = tmp_path / "defaults"
    defaults_module.stage_defaults(tmp_path, output)

    assert json.loads((output / "extensions.json").read_text())["enabled"] == []
    assert json.loads((output / "mcp_servers.json").read_text()) == {
        "mcpServers": {}
    }
    plugin_source = json.loads((output / "plugin-sources.json").read_text())
    assert plugin_source["official_sources"][0]["url"].startswith("https://github.com/")
    assert plugin_source["official_sources"][0]["mirrors"] == [
        "https://gitee.com/alikon/DeterminFlow-Plugins.git"
    ]
    assert plugin_source["official_sources"][0]["ref"] == "main"
    assert (output / "models_config.json").read_text() == (
        output / "models_config.example.json"
    ).read_text()
    verify_defaults(output)


def test_plaintext_api_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="明文凭据"):
        defaults_module._validate_no_plaintext_secrets(
            {"provider": {"api_key": "not-an-env-reference"}}
        )


def test_seed_user_config_preserves_existing_files(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "settings.json").write_text('{"source": "default"}', encoding="utf-8")
    (defaults / "models_config.json").write_text(
        '{"providers": {}}', encoding="utf-8"
    )
    user_root = tmp_path / "user"
    existing = user_root / "config" / "settings.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"source": "user"}', encoding="utf-8")

    created = seed_user_config(user_root, defaults)

    assert existing.read_text(encoding="utf-8") == '{"source": "user"}'
    assert user_root / "config" / "models_config.json" in created


def test_desktop_refreshes_only_core_owned_official_plugin_sources(
    tmp_path: Path,
) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "plugin-sources.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_sources": [
                    {
                        "id": "determinflow-official",
                        "url": "https://github.com/alikon-art/DeterminFlow-Plugins.git",
                        "ref": "main",
                    }
                ],
                "custom_sources": [],
            }
        ),
        encoding="utf-8",
    )
    user_root = tmp_path / "user"
    user_config = user_root / "config" / "plugin-sources.json"
    user_config.parent.mkdir(parents=True)
    custom = {"id": "custom", "url": "https://example.com/plugins.git"}
    user_config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_sources": [
                    {
                        "id": "determinflow-official",
                        "url": "https://github.com/alikon-art/DeterminFlow-Plugins.git",
                        "ref": "v0.2.1",
                    }
                ],
                "custom_sources": [custom],
            }
        ),
        encoding="utf-8",
    )

    assert refresh_official_plugin_sources(user_root, defaults) is True
    refreshed = json.loads(user_config.read_text(encoding="utf-8"))
    assert refreshed["official_sources"][0]["ref"] == "main"
    assert refreshed["custom_sources"] == [custom]
    assert refresh_official_plugin_sources(user_root, defaults) is False


def test_full_snapshot_merges_and_enables_plugins_only_once(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    checkout = snapshot / "checkouts" / "bishu-novel" / "commit-digest"
    checkout.mkdir(parents=True)
    (checkout / "extension.toml").write_text("[extension]\n", encoding="utf-8")
    record = {
        "source": "https://github.com/alikon-art/DeterminFlow-Plugins.git",
        "source_kind": "git",
        "trust": "official",
        "subdirectory": "plugins/bishu-novel",
        "resource_prefix": "bishu-novel",
        "resource_prefix_override": None,
        "active_revision": {
            "commit": "a" * 40,
            "content_sha256": "b" * 64,
            "checkout": "checkouts/bishu-novel/commit-digest",
            "requested_ref": "main",
        },
        "history": [],
        "pending_action": None,
    }
    (snapshot / "plugins.lock.json").write_text(
        json.dumps({"schema_version": 1, "plugins": {"bishu-novel": record}}),
        encoding="utf-8",
    )
    (snapshot / "release-plugins.json").write_text(
        json.dumps({"schema_version": 1, "plugins": {"bishu-novel": {}}}),
        encoding="utf-8",
    )
    user_root = tmp_path / "user"
    config = user_root / "config" / "extensions.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"enabled": ["existing"], "strict_startup": False}),
        encoding="utf-8",
    )

    assert seed_bundled_plugins(user_root, snapshot) == ["bishu-novel"]
    assert seed_bundled_plugins(user_root, snapshot) == []
    enabled = json.loads(config.read_text(encoding="utf-8"))["enabled"]
    assert enabled == ["existing", "bishu-novel"]
    installed = json.loads(
        (user_root / "data" / "plugins" / "plugins.lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert installed["plugins"]["bishu-novel"] == record


def test_full_snapshot_preserves_existing_plugin_record(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    bundled_record = {"active_revision": {"checkout": "invalid"}}
    (snapshot / "plugins.lock.json").write_text(
        json.dumps(
            {"schema_version": 1, "plugins": {"bishu-novel": bundled_record}}
        ),
        encoding="utf-8",
    )
    (snapshot / "release-plugins.json").write_text(
        json.dumps({"schema_version": 1, "plugins": {"bishu-novel": {}}}),
        encoding="utf-8",
    )
    user_root = tmp_path / "user"
    config = user_root / "config" / "extensions.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"enabled": [], "strict_startup": False}), encoding="utf-8"
    )
    existing_record = {"source": "user-managed"}
    lock = user_root / "data" / "plugins" / "plugins.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            {"schema_version": 1, "plugins": {"bishu-novel": existing_record}}
        ),
        encoding="utf-8",
    )

    assert seed_bundled_plugins(user_root, snapshot) == []
    stored = json.loads(lock.read_text(encoding="utf-8"))
    assert stored["plugins"]["bishu-novel"] == existing_record
    assert json.loads(config.read_text(encoding="utf-8"))["enabled"] == [
        "bishu-novel"
    ]


def test_bundled_plugin_verifier_rejects_empty_snapshot(tmp_path: Path) -> None:
    (tmp_path / "release-plugins.json").write_text(
        json.dumps({"schema_version": 1, "plugins": {}}), encoding="utf-8"
    )
    (tmp_path / "plugins.lock.json").write_text(
        json.dumps({"schema_version": 1, "plugins": {}}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="不能为空"):
        verify_bundled_plugins(tmp_path)


def test_desktop_versions_are_consistent() -> None:
    tauri = json.loads(
        (REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    package = json.loads(
        (REPO_ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
    )
    cargo = (REPO_ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text(
        encoding="utf-8"
    )

    assert tauri["version"] == "1.0.8"
    assert package["version"] == tauri["version"]
    assert f'version = "{tauri["version"]}"' in cargo


def test_frozen_backend_can_execute_python_workflow_scripts(tmp_path: Path) -> None:
    output = tmp_path / "result.txt"
    script = tmp_path / "workflow.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )

    assert _run_python_compatibility_mode([str(script), str(output)]) is True
    assert output.read_text(encoding="utf-8") == "ok"


def test_runtime_config_consumers_follow_redirected_config_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name, payload in {
        "compression_config.json": {"general": {"enabled": True}},
        "mcp_servers.json": {"mcpServers": {}},
        "user_injection_config.json": {"sections": []},
    }.items():
        (config_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    environment = os.environ.copy()
    environment["DETERMINFLOW_CONFIG_DIR"] = str(config_dir)
    code = (
        "from pathlib import Path\n"
        "from src.compression.config import CompressionConfigManager\n"
        "from src.mcp.client import MCPClient\n"
        "from src.web.api_routes import USER_INJECTION_CONFIG_FILE\n"
        f"expected = Path({str(config_dir)!r}).resolve()\n"
        "assert Path(CompressionConfigManager._DEFAULT_CONFIG_PATH).parent == expected\n"
        "assert MCPClient()._resolve_config_path() == expected / 'mcp_servers.json'\n"
        "assert USER_INJECTION_CONFIG_FILE == expected / 'user_injection_config.json'\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
    )


def test_checksum_file_is_portable_across_line_endings(tmp_path: Path) -> None:
    installer = tmp_path / "DeterminFlow-setup.exe"
    installer.write_bytes(b"installer")

    checksum = write_checksum(installer)

    assert checksum.read_bytes().endswith(b"\n")
    assert b"\r\n" not in checksum.read_bytes()


def test_updater_signature_and_static_manifest(tmp_path: Path) -> None:
    installer = tmp_path / "DeterminFlow 1.1.0-setup.exe"
    installer.write_bytes(b"installer")
    signature = installer.with_suffix(installer.suffix + ".sig")
    signature.write_text(
        base64.b64encode(b"signature" * 16).decode("ascii"), encoding="utf-8"
    )

    verify_updater_signature(signature)
    manifest = create_manifest(
        version="1.1.0",
        installer=installer,
        signature=signature,
        base_url="https://github.com/alikon-art/DeterminFlow/releases/download/v1.1.0",
        notes="桌面更新",
        pub_date="2026-08-04T00:00:00Z",
    )

    assert manifest["version"] == "1.1.0"
    platform = manifest["platforms"]["windows-x86_64"]
    assert platform["url"].endswith("/DeterminFlow%201.1.0-setup.exe")
    assert platform["signature"] == signature.read_text(encoding="utf-8")


def test_windows_desktop_executable_must_use_the_gui_subsystem(tmp_path: Path) -> None:
    def write_pe(path: Path, subsystem: int) -> None:
        image = bytearray(256)
        image[:2] = b"MZ"
        image[0x3C:0x40] = (64).to_bytes(4, "little")
        image[64:68] = b"PE\x00\x00"
        optional_header = 64 + 24
        image[optional_header : optional_header + 2] = (0x20B).to_bytes(2, "little")
        image[optional_header + 68 : optional_header + 70] = subsystem.to_bytes(
            2, "little"
        )
        path.write_bytes(image)

    executable = tmp_path / "determinflow-desktop.exe"
    write_pe(executable, subsystem=2)
    verify_windows_gui_executable(executable)

    write_pe(executable, subsystem=3)
    with pytest.raises(RuntimeError, match="GUI Subsystem"):
        verify_windows_gui_executable(executable)
