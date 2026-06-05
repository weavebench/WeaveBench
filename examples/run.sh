#!/usr/bin/env bash
# End-to-end smoke: one harness, one task, agent + grader + agent-as-judge.
# Usage:  bash examples/run.sh [openclaw|codex|claudecode|hermes]
#
# Prereqs (besides Python deps from `pip install -e .`):
#   1. $OPENROUTER_API_KEY in your shell
#   2. KVM/Docker host + OSWorld-compatible qcow2 (see README)
#   3. Host-side openclaw + template profile for --judge (see docs/AGENT_JUDGE.md)
set -euo pipefail
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY in your shell}"

HARNESS="${1:-openclaw}"
case "$HARNESS" in
  openclaw)   MODEL="${MODEL:-openai/gpt-5.5}" ;;
  codex)      MODEL="${MODEL:-openai/gpt-5.5}" ;;
  claudecode) MODEL="${MODEL:-openai/gpt-5.5}" ;;
  hermes)     MODEL="${MODEL:-openai/gpt-5.5}" ;;
  *) echo "unknown harness: $HARNESS" >&2; exit 1 ;;
esac

# Sensible defaults for the host-side judge (override per your install).
export AJ_OPENCLAW_BIN="${AJ_OPENCLAW_BIN:-/usr/local/bin/openclaw}"
export AJ_TEMPLATE_PROFILE="${AJ_TEMPLATE_PROFILE:-$HOME/judge_agent_test/template_profile}"
export AJ_TEMPLATE_WORKSPACE="${AJ_TEMPLATE_WORKSPACE:-$HOME/judge_agent_test/template_workspace}"

weavebench-run --harness "$HARNESS" --model "$MODEL" \
    --tasks_root "${WEAVEBENCH_TASKS_ROOT:-./cache/tasks}" \
    --domains WEB --task_filter task_1_ \
    --max_steps 100 \
    --result_dir "./results/${HARNESS}_smoke"
