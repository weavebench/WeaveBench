#!/usr/bin/env bash
# OSWorld test_all.json with GPT-5.5 (pure-vision baseline).
# Mirror of run_osworld_gpt54_fc.sh — same agent code path
# (mm_agents/gpt55_agent_fc.py with --model gpt-5.5), same protocol, same
# OSWorld native evaluator. The only differences vs the 5.4 baseline:
#   1. --model gpt-5.5  (was 5.4)
#   2. --max_steps 300  (was 100, per WCB user request for fair head-room)
#   3. --num_envs 4     (low concurrency — current host already runs the
#                        12-worker CLI sweep; don't disrupt it)
#   4. backend: LiteLLM proxy http://10.160.199.232:4200/v1  (Azure direct
#      key auth disabled as of 2026-06-04; LiteLLM provides the same Azure
#      deployment behind a proxy. Wired in mm_agents/gpt55_agent_fc.py
#      via OPENAI_BASE_URL env.)
#
# Goal: paper-grade GUI-vision baseline for gpt-5.5, comparable to the
# CLI-mode openclaw sweep at the same model id.
#
# Results: ./results_osworld_fc_gpt55/  (kept separate from gpt-5.5 baseline)
set -euo pipefail

echo "=================================================="
echo "  OSWorld test_all: GPT-5.5 vision (paper baseline)"
echo "=================================================="
echo "Start time: $(date)"

cd /mnt/nas_nfs/home/wanli/wen/SimpAgent/GUI-KV/OSWorld

mkdir -p logs results_osworld_fc_gpt55

# LiteLLM proxy serves the same Azure deployment as the direct endpoint
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://10.160.199.232:4200/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-litellm-azure-direct}"

# Preflight: confirm LiteLLM serves gpt-5.5
curl -sf --max-time 5 --noproxy '*' \
  "${OPENAI_BASE_URL}/models" \
  -H "Authorization: Bearer ${OPENAI_API_KEY}" \
  | grep -q '"id":"gpt-5.5"' \
  || { echo "[preflight] FAIL: gpt-5.5 not on $OPENAI_BASE_URL"; exit 1; }
echo "[preflight] gpt-5.5 reachable at $OPENAI_BASE_URL"

PY_BIN="${PY_BIN:-/mnt/nas_nfs/home/wanli/conda_envs/gui0_nas/bin/python}"

$PY_BIN runners/run_multienv_gpt55_fc.py \
  --provider_name docker \
  --headless \
  --action_space pyautogui \
  --observation_type screenshot \
  --model gpt-5.5 \
  --temperature 1.0 \
  --top_p 0.9 \
  --reasoning_effort medium \
  --sleep_after_execution 5 \
  --max_steps "${MAX_STEPS:-300}" \
  --max_trajectory_length "${MAX_STEPS:-300}" \
  --num_envs "${NUM_ENVS:-4}" \
  --client_password password \
  --test_all_meta_path evaluation_examples/test_all.json \
  --result_dir ./results_osworld_fc_gpt55 \
  2>&1 | tee logs/osworld_fc_gpt55_run_$(date +%Y%m%d_%H%M%S).log

echo ""
echo "=================================================="
echo "  Run completed at: $(date)"
echo "  Results saved to: ./results_osworld_fc_gpt55/"
echo "=================================================="
