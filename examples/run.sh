#!/usr/bin/env bash
# End-to-end smoke: one harness, one task, agent + grader + agent-as-judge.
# Usage:  bash examples/run.sh [openclaw|codex|claudecode|hermes]
#
# Prereqs (besides Python deps from `pip install -e .`):
#   1. $OPENROUTER_API_KEY in your shell
#   2. KVM/Docker host + OSWorld-compatible qcow2 (see README)
#   3. Host-side openclaw + template profile for --judge (see docs/AGENT_JUDGE.md)
#
# The harness runtime tarball is fetched automatically if missing (setup.sh
# only pre-downloads openclaw). Baking is optional — without a baked image the
# harness installs on the VM's first task (slower but works). See README.
set -euo pipefail
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY in your shell}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HARNESS="${1:-openclaw}"
case "$HARNESS" in
  openclaw|codex|claudecode|hermes) MODEL="${MODEL:-openai/gpt-5.5}" ;;
  *) echo "unknown harness: $HARNESS (expected openclaw|codex|claudecode|hermes)" >&2; exit 1 ;;
esac

# Ensure the harness runtime tarball(s) exist; fetch on demand if not.
CACHE="${WEAVEBENCH_CACHE:-$REPO_ROOT/cache}"
ASSETS_DIR="${WEAVEBENCH_ASSETS_DIR:-$CACHE/runtime_assets}"
if [ ! -f "$ASSETS_DIR/${HARNESS}.tar.gz" ]; then
  echo "[run] ${HARNESS}.tar.gz not found in $ASSETS_DIR — downloading..."
  bash scripts/download_assets.sh "$HARNESS"
fi

# Sensible defaults for the host-side judge (override per your install).
export AJ_OPENCLAW_BIN="${AJ_OPENCLAW_BIN:-/usr/local/bin/openclaw}"
export AJ_TEMPLATE_PROFILE="${AJ_TEMPLATE_PROFILE:-$HOME/judge_agent_test/template_profile}"
export AJ_TEMPLATE_WORKSPACE="${AJ_TEMPLATE_WORKSPACE:-$HOME/judge_agent_test/template_workspace}"

weavebench-run --harness "$HARNESS" --model "$MODEL" \
    --tasks_root "${WEAVEBENCH_TASKS_ROOT:-./cache/tasks}" \
    --domains WEB --task_filter task_1_ \
    --max_steps 100 \
    --result_dir "./results/${HARNESS}_smoke"
