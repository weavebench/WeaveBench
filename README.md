# WeaveBench

> **WeaveBench: A Long-Horizon, Real-World Benchmark for Computer-Use Agents with Hybrid Interfaces** — paper code release.

[![CI](https://github.com/weavebench/WeaveBench/actions/workflows/ci.yml/badge.svg)](https://github.com/weavebench/WeaveBench/actions/workflows/ci.yml)
[![arXiv](https://img.shields.io/badge/arXiv-2606.09426-b31b1b.svg)](https://arxiv.org/abs/2606.09426)
[![Website](https://img.shields.io/badge/website-weavebench.github.io-blue)](https://weavebench.github.io)
[![🤗 Dataset](https://img.shields.io/badge/🤗_dataset-wanlilll/WeaveBench-yellow)](https://huggingface.co/datasets/wanlilll/WeaveBench)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![tests](https://img.shields.io/badge/tests-58_passing-brightgreen.svg)](./tests)

**114 long-horizon, real-world tasks** across 8 work domains, where every task requires the agent to **interleave GUI clicks with shell/code** in one trajectory. Each task is scored by a **trajectory-aware Agent-as-Judge** that reads the chat trace + deliverables and emits per-clause evidence — much harder to spoof than file-existence checks.

## 🎬 Demo

<p align="center">
  <a href="https://weavebench.github.io/static/videos/rabbitmq_dlq_topology_mgmt.mp4">
    <img src="https://weavebench.github.io/static/images/casestudy_3panel.png" width="80%" alt="Click to watch: an agent managing a RabbitMQ dead-letter-queue topology end-to-end" />
  </a>
</p>

<p align="center">
  <em>▶ <a href="https://weavebench.github.io/static/videos/rabbitmq_dlq_topology_mgmt.mp4"><b>Watch the demo (RabbitMQ DLQ topology, OPS domain · 110 s)</b></a> · or browse <a href="https://weavebench.github.io">all task demos on the website</a>.</em>
</p>

## Headline results (from the paper)

| Backbone × harness | PassRate ↑ |
|---|---|
| **Claude Opus 4.7 + Claude Code** (best frontier pairing) | **41.2 %** |
| Claude Opus 4.7 + OpenClaw | 35.1 % |
| GPT-5.5 + Codex CLI | 35.1 % |
| GPT-5.5 + OpenClaw | 33.3 % |
| GPT-5.4 + OpenClaw | 22.8 % |
| Gemini 3.1 Pro + OpenClaw | 1.8 % |

> Frontier backbones report >78 % on OSWorld-Verified for comparable models — **WeaveBench is far from saturation**.

Full per-domain breakdowns + cross-harness sweep in [`docs/REPRODUCE.md`](./docs/REPRODUCE.md).

## Try it in 30 seconds (no VM, no API key)

Curious before you commit to the full setup? Install + run the offline demo:

```bash
git clone https://github.com/weavebench/WeaveBench.git && cd WeaveBench
pip install -e .
weavebench-demo
```

This prints a real `score.json` from a paper-canonical Claude Opus 4.7 attempt at `WEB_task_1_mockup_pixel_diff` — judge model, per-artifact evidence quotes, the works. ~1 second, no network calls. Use this to see what scoring looks like before deciding to spin up a 29.5 GB qcow2.

## Requirements (for the full pipeline)

| What | Why |
|---|---|
| Linux host with **KVM** + **Docker** (≥ 8 CPU, ≥ 32 GB RAM, ≥ 150 GB free disk) | Every task spins up an Ubuntu VM (OSWorld-derived) for the agent to act in |
| Python ≥ 3.10.12, Node ≥ 22 | Node powers the host-side OpenClaw judge (`npm install -g openclaw`) |
| **OpenRouter API key** | Used by both the agent and the trajectory-aware judge — both default to `openai/gpt-5.5`. Pay-as-you-go. `export OPENROUTER_API_KEY=...` |

> 🇨🇳 **Users in China**: `export HF_ENDPOINT=https://hf-mirror.com` — all `weavebench-download-*` commands honor it automatically.
> Optionally `export HF_TOKEN=hf_...` if you have an account and want higher HF rate limits (not required — dataset is public).

## Resource budget (read before you commit)

| Resource | Estimate |
|---|---|
| Disk for one-time setup | **~29.5 GB** (207 MB tasks + 852 MB runtime tarballs + 28.46 GB qcow2 + 7 KB judge template) |

Wall time and OpenRouter cost per task depend heavily on your VM host, network, chosen model, and how many parallel envs you run — `--num_envs 8` on a beefy host is roughly an order of magnitude faster than `--num_envs 1` on a laptop-class server. Run one smoke task first to calibrate before kicking off the full sweep.

If you already have an OSWorld-compatible qcow2 locally, use `bash scripts/setup.sh --skip-vm` and save the 28 GB.

> **Note on docker images.** `OSWORLD_LOCAL_QCOW2_PATH` only points the runner at an existing VM *disk* — it does **not** skip the docker *engine* image. The GUI provider boots the qcow2 inside `happysixd/osworld-docker` (and the `--mode cli` ablation uses `weavebench-ubuntu:v1.2`); these are pulled once. `scripts/setup.sh` pre-pulls the GUI engine image for you, so the first task run doesn't stall on a surprise pull.

## One-command setup

```bash
git clone https://github.com/weavebench/WeaveBench.git && cd WeaveBench

export OPENROUTER_API_KEY=sk-or-v1-...
# Optional: export HF_ENDPOINT=https://hf-mirror.com   # if in China
# Optional: export HF_TOKEN=hf_...                     # higher HF rate limits

bash scripts/setup.sh                          # installs + downloads dataset + runtime + 28 GB qcow2
```

`scripts/setup.sh` runs prereq checks → `pip install -e .` → `npm install -g openclaw` → `weavebench-download-{dataset,assets,judge,vm}` and prints the next command. Pass `--skip-vm` if you already have a qcow2 (you'll need to `export OSWORLD_LOCAL_QCOW2_PATH=...`).

## Run

```bash
export OSWORLD_LOCAL_QCOW2_PATH=./cache/vm/Ubuntu.qcow2

# Smoke test: one WEB task (note the trailing _ in --task_filter — a bare
# 'task_1' is a substring match and would also include task_10..task_19)
weavebench-run \
    --harness openclaw \
    --model openai/gpt-5.5 \
    --tasks_root ./cache/tasks \
    --domains WEB \
    --task_filter task_1_ \
    --result_dir ./results/smoke

# Full 114-task sweep (multi-hour; tune --num_envs to your host)
weavebench-run \
    --harness openclaw \
    --model openai/gpt-5.5 \
    --tasks_root ./cache/tasks \
    --num_envs 8 \
    --result_dir ./results/run
```

Swap `--harness` to `codex`, `claudecode`, or `hermes`. Swap `--model` to anything OpenRouter exposes (`anthropic/claude-opus-4.7`, `google/gemini-2.5-pro`, …).

### Optional env vars

```bash
# OpenRouter overrides
export OPENROUTER_BASE_URL=...                       # default https://openrouter.ai/api/v1
export JUDGE_MODEL=anthropic/claude-opus-4.7         # judge defaults to openai/gpt-5.5;
                                                     # set this if you want a different judge

# Reproducibility — pin to the exact dataset commit used for the paper.
# See docs/REPRODUCE.md for the canonical SHA + per-table model snapshot ids.
export WEAVEBENCH_DATASET_REVISION=<full sha from docs/REPRODUCE.md>
```

> ℹ️ **Default model.** Both the agent and the judge default to
> `openai/gpt-5.5` — one model, one billing line, easy to reason about.
> Pass `--model <other>` to change the agent and/or `JUDGE_MODEL=<other>`
> to change just the judge. See [`docs/REPRODUCE.md`](./docs/REPRODUCE.md)
> for the exact backbones used in the paper.

## Output

```
results/run/<mode>/<model>/<DOMAIN>/<task>/
  ├── chat.jsonl            # LLM trace
  ├── agent.log
  ├── results.tar.gz        # files the agent produced
  └── score.json            # trajectory-aware judge: per-clause evidence + overall_score
```

Scoring runs automatically — every task is judged by a separate OpenClaw on the host that reads the deliverables + chat trace. See [`docs/AGENT_JUDGE.md`](./docs/AGENT_JUDGE.md) for the one-time host-side setup.

## Reproducing paper numbers

See [`docs/REPRODUCE.md`](./docs/REPRODUCE.md) for the pinned dataset revision, model snapshot ids, and per-table commands used in the paper.

## More

- OSWorld hybrid-scoring experiment (CLI agent vs vision, native + agent-as-judge): [`experiments/osworld_hybrid/`](./experiments/osworld_hybrid)
- Architecture: [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
- Agent-as-Judge setup: [`docs/AGENT_JUDGE.md`](./docs/AGENT_JUDGE.md)
- Reproducing paper numbers: [`docs/REPRODUCE.md`](./docs/REPRODUCE.md)
- Contributing: [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- Changelog: [`CHANGELOG.md`](./CHANGELOG.md)
- Dataset + runtime + qcow2: [🤗 wanlilll/WeaveBench](https://huggingface.co/datasets/wanlilll/WeaveBench)

## Citation

```bibtex
@article{li2026weavebench,
  title         = {WeaveBench: A Long-Horizon, Real-World Benchmark for Computer-Use Agents with Hybrid Interfaces},
  author        = {Li, Wanli and Zhou, Bowen and Yu, Yunyao and Xu, Zhou and Yang, Yifan and Li, Dongsheng and Shan, Caihua},
  year          = {2026},
  eprint        = {2606.09426},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  url           = {https://arxiv.org/abs/2606.09426},
}
```

## Acknowledgements

Built on [OpenClaw](https://github.com/openclaw/openclaw), [Codex CLI](https://github.com/openai/codex), [Claude Code](https://github.com/anthropics/claude-code), [Hermes-Agent](https://hermes-agent.nousresearch.com), [OSWorld](https://github.com/xlang-ai/OSWorld), and [WildClawBench](https://github.com/internlm/WildClawBench).
