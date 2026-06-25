"""Download the WeaveBench task corpus from HuggingFace into ./cache/.

Usage::

    weavebench-download-dataset [--dest ./cache] [--include "tasks/WEB/*"]

The dataset lives at https://huggingface.co/datasets/wanlilll/WeaveBench
under ``tasks/`` and is public — no auth needed for plain downloads.
If you want higher HF rate limits or pin a private fork, you can still
pass ``--token`` / set ``$HF_TOKEN``.

Optional env vars:
  HF_TOKEN / HUGGINGFACE_TOKEN  : auth token (optional — only needed for
                                  higher HF rate limits or private forks)
  HF_ENDPOINT                   : HF mirror (e.g. https://hf-mirror.com for users in China)
  WEAVEBENCH_DATASET_REVISION   : pin to a specific commit SHA for reproducibility
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


_HF_REPO = "wanlilll/WeaveBench"


def _resolve_token(cli_token: str | None) -> str | None:
    return (cli_token
            or os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGINGFACE_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="weavebench-download-dataset",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    ap.add_argument("--dest", default="./cache",
                    help="Local cache root (default ./cache). Tasks land in <dest>/tasks/.")
    ap.add_argument("--include", default="tasks/*",
                    help="Glob pattern to filter what to download "
                         "(default 'tasks/*' — all 8 domains).")
    ap.add_argument("--token", default=None,
                    help="HF token. Falls back to $HF_TOKEN / $HUGGINGFACE_TOKEN. "
                         "Optional — only needed for higher HF rate limits.")
    ap.add_argument("--revision", default=os.environ.get("WEAVEBENCH_DATASET_REVISION"),
                    help="Pin to a specific commit SHA. Falls back to "
                         "$WEAVEBENCH_DATASET_REVISION. Default: latest on main.")
    ap.add_argument("--max-workers", type=int,
                    default=int(os.environ.get("WEAVEBENCH_HF_MAX_WORKERS", "4")),
                    help="Parallel download workers (default 4, or "
                         "$WEAVEBENCH_HF_MAX_WORKERS). Lower it (e.g. 2) if a "
                         "mirror like hf-mirror.com rate-limits you with HTTP 429.")
    args = ap.parse_args(argv)

    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import (
            GatedRepoError, RepositoryNotFoundError, HfHubHTTPError,
        )
    except ImportError:
        print("[error] pip install huggingface_hub", file=sys.stderr)
        return 1

    token = _resolve_token(args.token)
    endpoint = os.environ.get("HF_ENDPOINT")
    if endpoint:
        print(f"[hf] using mirror {endpoint}")

    dest = Path(args.dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    print(f"[fetch] {_HF_REPO} :: {args.include}"
          + (f" @ {args.revision[:8]}" if args.revision else "")
          + f"  (max_workers={args.max_workers})")
    # snapshot_download is resumable: already-downloaded files are skipped on
    # retry. Mirrors (notably hf-mirror.com) rate-limit large fan-outs with
    # 429, so we retry the whole call a few times with backoff — each retry
    # only re-fetches what didn't land yet.
    import time
    local = None
    for attempt in range(1, 6):
        try:
            local = snapshot_download(
                repo_id=_HF_REPO,
                repo_type="dataset",
                allow_patterns=[args.include],
                local_dir=str(dest),
                token=token,
                revision=args.revision,
                max_workers=args.max_workers,
            )
            break
        except GatedRepoError:
            print(f"[error] {_HF_REPO} is gated — request access at "
                  f"https://huggingface.co/datasets/{_HF_REPO}", file=sys.stderr)
            return 2
        except RepositoryNotFoundError:
            print(f"[error] {_HF_REPO} not found OR your token doesn't have access. "
                  "If the dataset is private, ensure your token can read it.",
                  file=sys.stderr)
            return 3
        except HfHubHTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status != 429 or attempt == 5:
                if status == 429:
                    print(f"[error] {_HF_REPO}: still rate-limited (429) after "
                          f"{attempt} attempts. Re-run the command to resume "
                          f"(already-downloaded files are skipped), or lower "
                          f"--max-workers.", file=sys.stderr)
                    return 4
                raise
            backoff = 5 * attempt
            print(f"[retry] HTTP 429 from the Hub/mirror (attempt {attempt}/5); "
                  f"resuming in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)

    print(f"[done] tasks staged under {local}/tasks/")
    print(f"       use --tasks_root {dest / 'tasks'} with weavebench-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
