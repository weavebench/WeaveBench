# review_viz — rollout review / trajectory visualization

Static-HTML tooling to human-review WeaveBench rollouts: browse per-task
score cards, deliverable previews, and the full agent trajectory timeline
in a browser. No server required — everything renders to self-contained
HTML files.

## Input layout

Each script walks one or more *run roots*, expecting the standard rollout
layout produced by the evaluator:

```
<run_root>/<...>/gui/<model>/<CATEGORY>/<TASK>/
    score.json          # judge output (scores, dimensions, hack flags)
    chat.jsonl          # agent trajectory
    results.tar.gz      # deliverables (screenshots, files)
```

Edit the `RUNS` dict at the top of `gen_viz.py` / `export_traj.py` to point
at your own run roots (paths are resolved relative to the script by default).

## Scripts

| Script | Output | Purpose |
|---|---|---|
| `gen_viz.py` | `viz/index.html` + `viz/tasks/*.html` | Overview dashboard (per-run PassRate @ τ=0.8, per-category table, per-task rows) plus one detailed viewer per task: score card (8 dimensions), deliverable image previews, and the reconstructed trajectory timeline. |
| `export_traj.py` | `traj_txt/*.txt` | Export each `chat.jsonl` to a compact plain-text trajectory (base64 images stripped) — handy for feeding trajectories to a review agent. |
| `gen_quality_html.py` | `viz/quality.html` | Render a quality-assessment report from `viz/quality_result.json` (independent per-trajectory quality review vs. the official judge). |

## Usage

```bash
# generate the dashboard + per-task viewers for all configured runs
python3 gen_viz.py

# or a single run
python3 gen_viz.py pro

# export plain-text trajectories
python3 export_traj.py

# quality report (needs viz/quality_result.json)
python3 gen_quality_html.py
```

Open `viz/index.html` in a browser to start reviewing.

## Notes

- Pure standard library — no third-party dependencies.
- Deliverable images are inlined as base64, so the generated `viz/` can get
  large (hundreds of MB to several GB depending on rollout count). It is a
  local review artifact; do not commit the generated HTML.
- Pass threshold τ defaults to `0.80` (`TAU` in `gen_viz.py`).
