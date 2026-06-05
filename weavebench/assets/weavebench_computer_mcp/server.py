#!/usr/bin/env python3
"""WeaveBench Computer MCP server — generic GUI tool for codex / claudecode / hermes.

Mirrors the openclaw `__computer__` plugin
(`weavebench/assets/computer_tool_plugin/index.ts`) one-for-one:

  * Same 8 actions: click, double_click, drag, keypress, move, scroll,
    type, wait — plus screenshot and cursor_position no-ops.
  * Same accepted shapes: `{action: {...}}` single OR `{actions: [...]}`
    batched; `actions: []` is the pure-observation path.
  * Same pyautogui backend, executed via `/usr/bin/python3 -c` so the
    server itself stays free of GUI dependencies (matches the openclaw
    plugin which also shells out to `/usr/bin/python3`).
  * Same DISPLAY=:0 / XAUTHORITY=/home/user/.Xauthority env contract.
  * Same persistence: /tmp_workspace/_screenshots/screenshot_NNNN_<label>.png.
  * ALWAYS returns a fresh screenshot in the response, so the agent never
    needs a separate observation call.

Transport: stdio JSON-RPC 2.0 with line-delimited (LDJSON) messages — the
codex / claude / hermes CLIs all consume MCP stdio servers in this
form. We hand-roll the wire layer (no `mcp` PyPI dep) so the qcow2 VM
image only needs python3 + pyautogui + Pillow (already present).

Spawned by:
  * codex:  ~/.codex/config.toml [mcp_servers.<name>] command/args/env
  * claude: claude mcp add ... -- python3 .../server.py
  * hermes: hermes mcp add ... (Linux CLI-only; see hermes_patches/README.md)
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from typing import Any

# ---------------------------------------------------------------------------
# Configuration knobs — keep in lockstep with computer_tool_plugin/index.ts
# so behavior is byte-identical across the three transports.
# ---------------------------------------------------------------------------
ACTION_TIMEOUT_S = float(os.environ.get("WEAVEBENCH_MCP_ACTION_TIMEOUT_S", "60"))
SCREENSHOT_TIMEOUT_S = float(os.environ.get("WEAVEBENCH_MCP_SCREENSHOT_TIMEOUT_S", "30"))
SCREENSHOT_DIR = os.environ.get(
    "WEAVEBENCH_MCP_SCREENSHOT_DIR", "/tmp_workspace/_screenshots"
)
PYTHON_BIN = os.environ.get("WEAVEBENCH_MCP_PYTHON", "/usr/bin/python3")
SERVER_NAME = "weavebench-computer"
SERVER_VERSION = "0.1.0"
TOOL_NAME = os.environ.get("WEAVEBENCH_MCP_TOOL_NAME", "computer")
PROTOCOL_VERSION = "2024-11-05"  # MCP spec version negotiated at initialize

# Per-process screenshot step counter — matches openclaw plugin's
# monotonically increasing 4-digit naming so offline review of one rollout
# yields a clean chronological sequence regardless of which action fired.
_screenshot_step = 0
_screenshot_step_lock = threading.Lock()
_log_lock = threading.Lock()


def _log(msg: str) -> None:
    """Diagnostic log to stderr — stdout is reserved for JSON-RPC."""
    with _log_lock:
        sys.stderr.write(f"[weavebench-mcp] {msg}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Key normalization — copied verbatim from index.ts so the same model
# `{action: "keypress", keys: ["CTRL", "C"]}` produces identical pyautogui
# calls regardless of which CLI dispatches it.
# ---------------------------------------------------------------------------
KEY_MAP = {
    "ENTER": "enter", "RETURN": "enter", "TAB": "tab",
    "ESC": "esc", "ESCAPE": "esc",
    "BACKSPACE": "backspace", "DELETE": "delete", "SPACE": "space",
    "UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right",
    "HOME": "home", "END": "end", "PAGEUP": "pageup", "PAGEDOWN": "pagedown",
    "CTRL": "ctrl", "ALT": "alt", "SHIFT": "shift",
    "META": "win", "WIN": "win", "CMD": "ctrl", "SUPER": "win",
}

# Mouse button int → pyautogui name; mirrors index.ts BUTTON_INT_TO_NAME.
BUTTON_INT_TO_NAME = {
    1: "left", 2: "middle", 3: "right", 4: "back", 5: "forward",
}


def _norm_key(k: Any) -> str:
    s = str(k or "").strip()
    return KEY_MAP.get(s.upper(), s.lower())


# ---------------------------------------------------------------------------
# action_to_python — mirrors index.ts `actionToPython()`. Same numeric
# coercion semantics (`Number(x) | 0` truncates to int), same default
# button selection, same scroll-axis flip, same wait default.
# ---------------------------------------------------------------------------
def _i(v: Any) -> int:
    """Mimic JS `Number(x) | 0` — coerce to int, NaN/undefined → 0."""
    try:
        return int(float(v)) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _py_repr(v: Any) -> str:
    """JSON-safe Python literal — matches the TS plugin's pyRepr()."""
    return json.dumps(v, ensure_ascii=False)


def action_to_python(action: dict) -> str:
    """Translate a single CUA action → pyautogui Python snippet (one stmt)."""
    if not isinstance(action, dict):
        return ""
    t = action.get("type")
    if not t:
        return ""

    if t in ("screenshot", "cursor_position"):
        # No-op on the actuator side — caller always returns a fresh
        # screenshot regardless of which action triggered the call.
        return ""

    if t in ("click", "left_click", "right_click", "middle_click"):
        x = _i(action.get("x"))
        y = _i(action.get("y"))
        btn_raw = action.get("button")
        if btn_raw is None:
            btn = "right" if t == "right_click" else "middle" if t == "middle_click" else "left"
        else:
            btn = str(btn_raw)
        if btn not in ("left", "right", "middle"):
            btn = "left"
        return f"pyautogui.click({x}, {y}, button={_py_repr(btn)})"

    if t in ("double_click", "doubleClick"):
        return f"pyautogui.doubleClick({_i(action.get('x'))}, {_i(action.get('y'))})"

    if t == "triple_click":
        return f"pyautogui.tripleClick({_i(action.get('x'))}, {_i(action.get('y'))})"

    if t in ("move", "mouse_move", "mousemove"):
        return f"pyautogui.moveTo({_i(action.get('x'))}, {_i(action.get('y'))}, duration=0.1)"

    if t in ("type", "keyboard"):
        text = str(action.get("text") or "")
        return f"pyautogui.typewrite({_py_repr(text)}, interval=0.02)"

    if t in ("keypress", "key"):
        keys = action.get("keys")
        if keys is None:
            keys = action.get("text") or action.get("key")
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list):
            keys = []
        norm = [_norm_key(k) for k in keys]
        if not norm:
            return ""
        if len(norm) == 1:
            return f"pyautogui.press({_py_repr(norm[0])})"
        args = ", ".join(_py_repr(k) for k in norm)
        return f"pyautogui.hotkey({args})"

    if t == "scroll":
        x = _i(action.get("x"))
        y = _i(action.get("y"))
        sx = _i(action.get("scroll_x"))
        sy = _i(action.get("scroll_y"))
        # pyautogui scroll: positive=up. OpenAI sends positive sy=down.
        amt = -sy if sy != 0 else (-sx if sx != 0 else 0)
        return f"pyautogui.moveTo({x}, {y}); pyautogui.scroll({amt})"

    if t == "drag":
        path = action.get("path") if isinstance(action.get("path"), list) else []
        if len(path) < 2:
            return ""
        first, last = path[0] or {}, path[-1] or {}
        x0, y0 = _i(first.get("x")), _i(first.get("y"))
        x1, y1 = _i(last.get("x")), _i(last.get("y"))
        return (
            f"pyautogui.moveTo({x0}, {y0}); "
            f"pyautogui.dragTo({x1}, {y1}, duration=0.5, button='left')"
        )

    if t == "wait":
        ms = action.get("ms")
        if ms is None:
            ms = action.get("duration")
        if ms is None:
            ms = 1000
        try:
            secs = float(ms) / 1000.0
        except (TypeError, ValueError):
            secs = 1.0
        return f"time.sleep({secs:.3f})"

    return ""


def normalize_action(a: Any) -> Any:
    """Flat shape `{action:'click', x, y, button:1}` → CUA shape
    `{type:'click', x, y, button:'left'}`. Pass-through if already CUA."""
    if not isinstance(a, dict):
        return a
    if isinstance(a.get("type"), str):
        return a
    name = str(a.get("action") or "").lower()
    if not name:
        return a
    out = {"type": name}
    for k, v in a.items():
        if k == "action":
            continue
        out[k] = v
    if isinstance(out.get("button"), (int, bool)) and not isinstance(out["button"], bool):
        out["button"] = BUTTON_INT_TO_NAME.get(int(out["button"]), "left")
    if name == "drag" and isinstance(out.get("path"), list) and out["path"]:
        first = out["path"][0]
        if isinstance(first, list):
            out["path"] = [
                {"x": (p[0] if len(p) > 0 else 0), "y": (p[1] if len(p) > 1 else 0)}
                for p in out["path"] if isinstance(p, list)
            ]
    if name == "wait" and out.get("ms") is None and out.get("duration") is None:
        out["ms"] = 1000
    return out


# ---------------------------------------------------------------------------
# Subprocess runner — shells to /usr/bin/python3 with the GUI env vars,
# mirroring index.ts runPython(). The MCP server itself does not import
# pyautogui or Pillow, so this script can be unit-tested off-VM and the
# server can boot even if the agent-side env lacks GUI libs.
# ---------------------------------------------------------------------------
def _gui_env() -> dict:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", "/home/user/.Xauthority")
    return env


def run_python(snippet: str, timeout_s: float) -> tuple[int, str, str]:
    """Run a Python snippet through /usr/bin/python3 with the GUI env.

    Returns (returncode, stdout, stderr). On timeout returns
    (-9, "", "<timeout>"). Errors during process spawn return (-1, "", str(e)).
    """
    try:
        p = subprocess.run(
            [PYTHON_BIN, "-c", snippet],
            env=_gui_env(),
            capture_output=True,
            timeout=timeout_s,
        )
        return (
            p.returncode,
            p.stdout.decode("utf-8", errors="replace"),
            p.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired as e:
        return -9, "", f"<timeout after {timeout_s}s: {e}>"
    except Exception as e:  # noqa: BLE001
        return -1, "", f"<spawn error: {e}>"


def _safe_label(label: str) -> str:
    """Sanitize an action type → filename slug. Matches the TS plugin."""
    s = (label or "screenshot").lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s)
    s = re.sub(r"^_+|_+$", "", s)
    return s[:32] or "screenshot"


def capture_screenshot(action_label: str) -> str | None:
    """Take a screenshot via pyautogui, return base64-PNG.

    Also persists a copy under /tmp_workspace/_screenshots/ for offline
    review (best-effort — failure here does NOT fail the call). Naming
    convention is identical to the TS plugin so existing rollup tooling
    keeps working across all four agent types.
    """
    fd, tmp = tempfile.mkstemp(prefix="wcbmcp_", suffix=".png")
    os.close(fd)
    try:
        snippet = (
            "import pyautogui\n"
            "img = pyautogui.screenshot()\n"
            f"img.save({_py_repr(tmp)}, 'PNG')\n"
            "print('OK')\n"
        )
        rc, _out, err = run_python(snippet, SCREENSHOT_TIMEOUT_S)
        if rc != 0:
            _log(f"screenshot capture failed rc={rc}: {err[-300:]!r}")
            return None
        try:
            with open(tmp, "rb") as fh:
                buf = fh.read()
        except OSError as e:
            _log(f"screenshot read failed: {e}")
            return None
        # Best-effort persistence — matches index.ts behavior exactly.
        try:
            global _screenshot_step
            with _screenshot_step_lock:
                _screenshot_step += 1
                step = f"{_screenshot_step:04d}"
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            persist = os.path.join(
                SCREENSHOT_DIR, f"screenshot_{step}_{_safe_label(action_label)}.png"
            )
            with open(persist, "wb") as fh:
                fh.write(buf)
        except OSError:
            pass  # persistence is for offline review only
        return base64.b64encode(buf).decode("ascii")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def execute_action(action: dict) -> dict:
    """Run a single action via pyautogui. Returns {ok: bool, err?: str}."""
    snippet = action_to_python(action)
    if not snippet:
        t = action.get("type") if isinstance(action, dict) else None
        if t in ("screenshot", "cursor_position"):
            return {"ok": True}
        return {"ok": False, "err": f"unhandled action: {json.dumps(action)[:200]}"}
    wrapped = "import pyautogui, time\npyautogui.FAILSAFE = False\n" + snippet + "\n"
    rc, _out, err = run_python(wrapped, ACTION_TIMEOUT_S)
    if rc != 0:
        return {"ok": False, "err": (err or "python exit nonzero")[-500:]}
    # Tiny settle so subsequent screenshot captures post-action state.
    time.sleep(0.15)
    return {"ok": True}


# ---------------------------------------------------------------------------
# MCP tool definition — the JSON shipped to the LLM. The description and
# parameter schema are byte-aligned with index.ts so the model sees the
# same affordance regardless of which CLI drove the spawn.
# ---------------------------------------------------------------------------
TOOL_DESCRIPTION = "\n".join([
    "Perform one or more computer actions in sequence on the remote desktop.",
    "Valid actions: click, double_click, drag, keypress, move, scroll, type, wait.",
    "Call with either:",
    "  \u2022 single-action shape  `{action: {type:<name>, ...kwargs}}`",
    "  \u2022 batched shape        `{actions: [{action:<name>, ...kwargs}, ...]}`",
    "Example:",
    "  {\"actions\": [{\"action\":\"click\",\"x\":100,\"y\":200,\"button\":1}, {\"action\":\"type\",\"text\":\"Hello, world!\"}]}",
    "PREFER batching multiple imminent actions in a single call (e.g. click then type, click then keypress, scroll then click) to minimize round-trips.",
    "The plugin runs the actions via pyautogui and ALWAYS returns a fresh screenshot of the resulting screen state, so a separate observation tool is unnecessary. Pass `actions:[]` (empty batch) for pure observation \u2014 the screenshot is still returned.",
    "IMPORTANT: After an observation-only call (`actions:[]`), you MUST continue on the next turn with concrete actions to make progress on the task. Observing the screen does NOT complete the task; do not end the turn after just receiving a screenshot.",
    "",
    "Action schemas:",
    "- click        {x:int, y:int, button:int (1-left, 2-wheel/middle, 3-right, 4-back, 5-forward), keys?:string[]}",
    "- double_click {x:int, y:int, keys?:string[]}",
    "- drag         {path: number[][]   // [[x,y],[x,y],...] , keys?:string[]}",
    "- keypress     {keys:string[]}     // e.g. ['ctrl','c']",
    "- move         {x:int, y:int, keys?:string[]}",
    "- scroll       {x:int, y:int, scroll_x:int, scroll_y:int, keys?:string[]}",
    "- type         {text:string}",
    "- wait         {ms?:int}           // optional; default 1000ms; brief pause",
])

TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "object", "additionalProperties": True},
        "actions": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
    },
    "additionalProperties": True,
}


def handle_tools_call(arguments: dict) -> dict:
    """Execute the `computer` tool. Demux single vs batched shape, run each
    action, ALWAYS capture a trailing screenshot, return MCP content list.
    """
    if isinstance(arguments.get("actions"), list):
        raw_ops = arguments["actions"]
        ops = [normalize_action(a) for a in raw_ops] if raw_ops else []
    else:
        single = arguments.get("action", arguments)
        ops = [single] if single else []

    errors: list[str] = []
    last_label = "action"
    for op in ops:
        res = execute_action(op)
        if isinstance(op, dict) and isinstance(op.get("type"), str):
            last_label = op["type"]
        if not res.get("ok"):
            errors.append(res.get("err") or "unknown")

    png64 = capture_screenshot(last_label)
    content: list[dict] = []
    if errors:
        content.append({
            "type": "text",
            "text": f"[computer error] {' | '.join(errors)}",
        })
    if png64:
        # Always prepend a text block so the response has 2+ content items.
        # This ensures LiteLLM's Anthropic→OpenAI translator uses the
        # multi-content path which emits a proper image_url object instead
        # of stringifying the image as a data-URL text (single-item path).
        if not errors:
            content.append({
                "type": "text",
                "text": "[screenshot]",
            })
        content.append({
            "type": "image",
            "data": png64,
            "mimeType": "image/png",
        })
    else:
        content.append({
            "type": "text",
            "text": "[computer] screenshot capture failed",
        })
    return {"content": content, "isError": bool(errors and not png64)}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 stdio dispatch. The MCP spec is line-delimited
# (LDJSON) over stdin/stdout; we read a line, parse, dispatch to one
# of {initialize, initialized notification, tools/list, tools/call,
# ping}, and write a response line.
# ---------------------------------------------------------------------------
def _make_response(req_id: Any, result: Any = None, error: dict | None = None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def _make_error(code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


def dispatch(request: dict) -> dict | None:
    """Route a single JSON-RPC request. Returns the response dict, or
    None if the request is a notification (no id) and needs no reply."""
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}
    is_notification = req_id is None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
            return _make_response(req_id, result=result)

        if method in ("notifications/initialized", "initialized"):
            return None

        if method == "ping":
            return _make_response(req_id, result={})

        if method == "tools/list":
            tool = {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "inputSchema": TOOL_INPUT_SCHEMA,
            }
            return _make_response(req_id, result={"tools": [tool]})

        if method == "tools/call":
            if params.get("name") != TOOL_NAME:
                err = _make_error(-32602, f"unknown tool: {params.get('name')!r}")
                return _make_response(req_id, error=err)
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                err = _make_error(-32602, "arguments must be an object")
                return _make_response(req_id, error=err)
            result = handle_tools_call(arguments)
            return _make_response(req_id, result=result)

        if is_notification:
            return None
        return _make_response(req_id, error=_make_error(-32601, f"method not found: {method!r}"))

    except Exception as e:  # noqa: BLE001 \u2014 never let an exception kill the loop
        _log(f"dispatch crashed on {method!r}: {e}\n{traceback.format_exc()}")
        if is_notification:
            return None
        return _make_response(
            req_id,
            error=_make_error(-32603, "internal error", data=str(e)),
        )


def main() -> int:
    _log(
        f"starting {SERVER_NAME} v{SERVER_VERSION} (pid={os.getpid()}, "
        f"DISPLAY={os.environ.get('DISPLAY', '<unset>')})"
    )
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        raw = stdin.readline()
        if not raw:
            _log("stdin EOF \u2014 shutting down")
            return 0
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            _log(f"invalid JSON in: {line[:200]!r} ({e})")
            err_msg = json.dumps(_make_response(
                None, error=_make_error(-32700, "parse error", data=str(e))
            ))
            stdout.write((err_msg + "\n").encode("utf-8"))
            stdout.flush()
            continue
        if not isinstance(request, dict):
            continue
        response = dispatch(request)
        if response is not None:
            stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
