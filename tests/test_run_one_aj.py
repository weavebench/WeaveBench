"""Tests for the agent-as-judge subdir + bench-root resolution."""
from __future__ import annotations

from pathlib import Path

from weavebench.eval.agent_judge.run_one_aj import (
    _resolve_bench_root,
    _resolve_bench_subdir,
)
from weavebench.eval.agent_judge.stage_case import task_md_path


def test_default_is_public_layout():
    """Tasks that don't carry an internal bench_subdir get '.' (public layout)."""
    assert _resolve_bench_subdir({}) == "."
    assert _resolve_bench_subdir({"task_id": "WEB_task_1"}) == "."


def test_task_with_explicit_subdir_preserved():
    assert _resolve_bench_subdir({"bench_subdir": "batch_alt"}) == "batch_alt"


def test_empty_string_subdir_falls_back_to_dot():
    assert _resolve_bench_subdir({"bench_subdir": ""}) == "."


def test_bench_root_matches_tasks_root(monkeypatch):
    """Regression for H1: judge must look for tasks at the same dir users pass
    via --tasks_root. Previously _resolve_bench_root appended a stray "tasks"
    segment, so a user running --tasks_root ./cache/tasks made the judge
    search ./cache/tasks/tasks/ — which doesn't exist, silently degrading
    every score.json."""
    monkeypatch.delenv("AJ_BENCH_ROOT", raising=False)
    assert _resolve_bench_root(Path("./cache/tasks")) == Path("./cache/tasks")


def test_bench_root_env_override(monkeypatch):
    monkeypatch.setenv("AJ_BENCH_ROOT", "/custom/path/to/tasks")
    assert _resolve_bench_root(Path("./ignored")) == Path("/custom/path/to/tasks")


def test_full_judge_path_construction(tmp_path, monkeypatch):
    """End-to-end: a user runs --tasks_root <cache>/tasks with no bench_subdir.
    The judge must construct <cache>/tasks/WEB/WEB_task_1_foo.md, which
    *exists* in the public layout, not <cache>/tasks/tasks/...
    """
    monkeypatch.delenv("AJ_BENCH_ROOT", raising=False)
    tasks_root = tmp_path / "tasks"
    (tasks_root / "WEB").mkdir(parents=True)
    (tasks_root / "WEB" / "WEB_task_1_foo.md").write_text("# stub")

    bench_root = _resolve_bench_root(tasks_root)
    subdir = _resolve_bench_subdir({})
    md = task_md_path(bench_root, subdir, "WEB", "WEB_task_1_foo")
    assert md.exists(), f"judge would look at {md}, which doesn't exist"
