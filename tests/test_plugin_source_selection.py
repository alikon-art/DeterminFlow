from __future__ import annotations

import pytest

from src.plugin_system import source_selection


def _probe(
    url: str,
    commit: str,
    elapsed: float,
    error: str = "",
) -> source_selection._GitSourceProbe:
    return source_selection._GitSourceProbe(
        url=url,
        commit=commit,
        elapsed_seconds=elapsed,
        error=error,
    )


def test_probe_exact_commit_checks_advertised_refs(monkeypatch) -> None:
    commit = "a" * 40

    def run(args, **kwargs):
        assert "HEAD" in args
        assert "refs/heads/*" in args
        assert "refs/tags/*" in args
        return source_selection.subprocess.CompletedProcess(
            args,
            0,
            stdout=f"{commit}\tHEAD\n{commit}\trefs/heads/main\n",
            stderr="",
        )

    monkeypatch.setattr(source_selection.subprocess, "run", run)

    probe = source_selection._probe_git_source(
        "https://github.example/plugins.git",
        commit,
        git_binary="git",
        timeout_seconds=1,
    )

    assert probe.commit == commit
    assert probe.error == ""


def test_prefers_mirror_at_primary_commit_even_when_primary_is_faster(monkeypatch) -> None:
    commit = "a" * 40
    probes = {
        "https://github.example/plugins.git": _probe(
            "https://github.example/plugins.git", commit, 0.1
        ),
        "https://gitee.example/plugins.git": _probe(
            "https://gitee.example/plugins.git", commit, 0.4
        ),
    }
    monkeypatch.setattr(
        source_selection,
        "_probe_git_source",
        lambda url, ref, **kwargs: probes[url],
    )

    selected = source_selection.select_git_source(probes, "main")

    assert selected.url == "https://gitee.example/plugins.git"
    assert selected.commit == commit


def test_rejects_fast_mirror_when_primary_has_newer_commit(monkeypatch) -> None:
    primary = "b" * 40
    probes = {
        "https://github.example/plugins.git": _probe(
            "https://github.example/plugins.git", primary, 0.4
        ),
        "https://gitee.example/plugins.git": _probe(
            "https://gitee.example/plugins.git", "a" * 40, 0.1
        ),
    }
    monkeypatch.setattr(
        source_selection,
        "_probe_git_source",
        lambda url, ref, **kwargs: probes[url],
    )

    selected = source_selection.select_git_source(probes, "main")

    assert selected.url == "https://github.example/plugins.git"
    assert selected.commit == primary


def test_uses_primary_when_preferred_mirror_is_unavailable(monkeypatch) -> None:
    commit = "b" * 40
    probes = {
        "https://github.example/plugins.git": _probe(
            "https://github.example/plugins.git", commit, 0.2
        ),
        "https://gitee.example/plugins.git": _probe(
            "https://gitee.example/plugins.git", "", 0.1, "offline"
        ),
    }
    monkeypatch.setattr(
        source_selection,
        "_probe_git_source",
        lambda url, ref, **kwargs: probes[url],
    )

    selected = source_selection.select_git_source(probes, "main")

    assert selected.url == "https://github.example/plugins.git"
    assert selected.commit == commit


def test_uses_mirror_when_primary_is_unavailable(monkeypatch) -> None:
    probes = {
        "https://github.example/plugins.git": _probe(
            "https://github.example/plugins.git", "", 0.2, "offline"
        ),
        "https://gitee.example/plugins.git": _probe(
            "https://gitee.example/plugins.git", "c" * 40, 0.3
        ),
    }
    monkeypatch.setattr(
        source_selection,
        "_probe_git_source",
        lambda url, ref, **kwargs: probes[url],
    )

    selected = source_selection.select_git_source(probes, "main")

    assert selected.url == "https://gitee.example/plugins.git"


def test_fails_when_all_sources_are_unavailable(monkeypatch) -> None:
    urls = (
        "https://github.example/plugins.git",
        "https://gitee.example/plugins.git",
    )
    monkeypatch.setattr(
        source_selection,
        "_probe_git_source",
        lambda url, ref, **kwargs: _probe(url, "", 0.1, "offline"),
    )

    with pytest.raises(ValueError, match="所有拉取地址均不可用"):
        source_selection.select_git_source(urls, "main")
