"""GPT-5.5 agent using **custom function-call tools** instead of the native
computer-use tool. Designed as an A/B baseline against `gpt54_agent.py`.

v5 design (current):
- Tool schema mirrors OpenAI's leaked internal `computer.do(actions:[...])`
  namespace: a single batched `do` tool that accepts a list of sub-actions
  ({action: 'click', x, y, button, ...}). This forces the model into the same
  batch decoding habit the CUA RL training rewards.
- The screenshot is sent at the env's native screen resolution (e.g. 1920x1080).
  No client-side resize. Empirically (probe_coord_robust.py: 18/18 trials),
  GPT-5.5 with the do() schema + a strong `Screen is WxH; do not rescale`
  prompt outputs coordinates directly in screen space, matching native CUA.
- No client-side coordinate scaling. The harness executes `(x, y)` raw.
"""
import base64
import io
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from openai import OpenAI

try:
    from openai import AzureOpenAI
except ImportError:
    AzureOpenAI = None


logger = logging.getLogger("desktopenv.agent")


OPERATOR_PROMPT = """

Here are some helpful tips:
- You are operating an {PLATFORM} desktop with internet access.
- The screen resolution is {SCREEN_WIDTH}x{SCREEN_HEIGHT} pixels. All click coordinates must be within this range and refer to absolute screen pixels.
- My computer password is "{CLIENT_PASSWORD}" when sudo is needed.
- The current date is {CURRENT_DATE}.
- The home directory is "{HOME_DIR}".
- Stick to the website or application already opened for the task when possible.
- Prefer Chrome over Firefox/Chromium unless the task says otherwise.
- You can act without asking for confirmation.
- If content may be off-screen, scroll or zoom out before deciding it is unavailable.
- You MUST drive the GUI by calling the `do` tool with one or more actions. Each action is a dict with an `action` field plus its kwargs. PREFER batching multiple imminent actions in a single `do` call (e.g. click then type, click then keypress, scroll then click) to minimize round-trips. Only stop and wait for a fresh screenshot when the next action genuinely depends on the visual outcome of the previous one. Do NOT describe actions in plain text.
- Coordinates are absolute screen pixels in the {SCREEN_WIDTH}x{SCREEN_HEIGHT} system. Do not normalize.
- IMPORTANT: Output every (x, y) in the {SCREEN_WIDTH}x{SCREEN_HEIGHT} space. The image you see may visually appear smaller in your perception (the API may display a downscaled view), but the ACTUAL screen is {SCREEN_WIDTH}x{SCREEN_HEIGHT}. Do NOT rescale your coordinates to a smaller image space; the harness will execute your raw (x, y) directly on the {SCREEN_WIDTH}x{SCREEN_HEIGHT} display.
- For mouse buttons, use integers: 1=left, 2=wheel/middle, 3=right, 4=back, 5=forward.
- IMPORTANT — task termination:
  - When the task is FULLY COMPLETE (the latest screenshot already shows the verifiable end-state), do NOT call the `do` tool any more. Instead respond with a SHORT plain-text message that begins or ends with the exact token "[TASK DONE]".
  - Continuing to click/type/scroll after the task is done can break the verifiable end-state and cause failure. So once you are confident, STOP and emit "[TASK DONE]".
  - Only emit "[TASK DONE]" when you have actually verified completion in the latest screenshot. Never emit it speculatively before doing the work.
- If the task is infeasible because of missing apps, permissions, contradictory requirements, or other hard blockers, output exactly "[INFEASIBLE]".
"""


# --- Custom function-call tool schema (Responses API "function" tools) -------
# Aligned 1:1 with OpenAI's leaked internal "computer" namespace schema.
# A single batched `do(actions: any[])` tool drives all GUI actions, mirroring
# the way the native CUA tool is structured. This forces the model into the
# same "batch multiple actions per call" decoding habit that the CUA-RL
# training rewards.
def _build_function_tools() -> List[Dict[str, Any]]:
    return [{
        "type": "function",
        "name": "do",
        "description": (
            "Perform one or more computer actions in sequence on the remote desktop.\n"
            "Valid actions: click, double_click, drag, keypress, move, scroll, type, wait.\n"
            "`actions` should be a list of {\"action\": <name>, ...kwargs}. Example:\n"
            "[{\"action\":\"click\",\"x\":100,\"y\":100,\"button\":1},"
            "{\"action\":\"type\",\"text\":\"Hello, world!\"}]\n"
            "PREFER batching multiple imminent actions in a single call (e.g. click then "
            "type, click then keypress) to minimize round-trips. Only stop and wait for a "
            "fresh screenshot when the next action genuinely depends on the visual outcome.\n\n"
            "Action schemas:\n"
            "- click       {x:int, y:int, button:int (1-left, 2-wheel, 3-right, 4-back, 5-forward), keys?:string[]}\n"
            "- double_click{x:int, y:int, keys?:string[]}\n"
            "- drag        {path: number[][]  // [[x,y],[x,y],...] , keys?:string[]}\n"
            "- keypress    {keys:string[]}    // e.g. ['ctrl','c']\n"
            "- move        {x:int, y:int, keys?:string[]}\n"
            "- scroll      {x:int, y:int, scroll_x:int, scroll_y:int, keys?:string[]}\n"
            "- type        {text:string}\n"
            "- wait        {} (no args; brief pause)\n"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "description": "List of actions to perform in sequence.",
                    "items": {
                        "type": "object",
                        "description": "One action: must include 'action' name plus its kwargs.",
                        # Use additionalProperties=true (no per-action sub-schema) so the
                        # array can hold heterogeneous shapes (click vs type vs drag etc.)
                        # exactly like OpenAI's `actions: any[]`.
                        "additionalProperties": True,
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["click", "double_click", "drag", "keypress",
                                         "move", "scroll", "type", "wait"],
                            },
                        },
                        "required": ["action"],
                    },
                },
            },
            "required": ["actions"],
            "additionalProperties": False,
        },
    }]




# --- Helpers (mirrored from gpt54_agent.py) ----------------------------------
class Action:
    def __init__(self, raw_action, action_space):
        if action_space != "pyautogui":
            raise ValueError("GPT55FCAgent only supports pyautogui actions")
        self._action_space = action_space
        if raw_action in (None, ""):
            raise ValueError("action cannot be empty")
        self._action = raw_action

    def get_action(self):
        return self._action


class Timer:
    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.duration = time.time() - self.start


class StepError(Exception):
    pass


def encode_image(image_content: bytes) -> str:
    return base64.b64encode(image_content).decode("utf-8")


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_model_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _model_dump(v) for k, v in value.items()}
    return value


def _get_field(value, field, default=None):
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _preview_text(text: str, limit: int = 120) -> str:
    s = text.replace("\n", "\\n")
    return s if len(s) <= limit else s[:limit] + "..."


def _sanitize_for_log(value):
    value = _model_dump(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "image_url" and isinstance(v, str) and v.startswith("data:image/"):
                out[k] = "<image>"
            else:
                out[k] = _sanitize_for_log(v)
        return out
    if isinstance(value, list):
        return [_sanitize_for_log(v) for v in value]
    return value


class GPT55FCAgent:
    """GPT-5.5 driver using **custom function-call tools** (no native CUA)."""

    def __init__(
        self,
        env,
        platform: str = "ubuntu",
        model: str = "gpt-5.5",
        max_tokens: Optional[int] = None,
        top_p: float = 0.9,
        temperature: float = 0.5,
        action_space: str = "pyautogui",
        observation_type: str = "screenshot",
        max_trajectory_length: int = 100,
        a11y_tree_max_tokens: int = 10000,
        client_password: str = "",
        provider_name: str = "aws",
        screen_width: int = 1920,
        screen_height: int = 1080,
        sleep_after_execution: float = 0.0,
        reasoning_effort: str = "xhigh",
    ):
        if action_space != "pyautogui":
            raise ValueError("GPT55FCAgent only supports pyautogui action space")
        if observation_type != "screenshot":
            raise ValueError("GPT55FCAgent only supports screenshot observation")

        self.env = env
        self.platform = platform
        self.model = model
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.temperature = temperature
        self.action_space = action_space
        self.observation_type = observation_type
        self.max_trajectory_length = max_trajectory_length
        self.a11y_tree_max_tokens = a11y_tree_max_tokens
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.sleep_after_execution = sleep_after_execution
        self.reasoning_effort = reasoning_effort
        self.client_password = client_password or (
            "osworld-public-evaluation" if provider_name == "aws" else "password"
        )

        # The ONLY substantive difference vs. gpt54_agent.py: custom function tools.
        self.tools = _build_function_tools()

        # NO client-side rescale: the screenshot is sent at the env's native
        # resolution and the model outputs coordinates in that same space
        # (verified empirically by probe_coord_robust.py: 18/18 trials).

        self.previous_response_id: Optional[str] = None
        self.pending_input_items: List[Dict[str, Any]] = []

    def _create_response(self, request_input, instructions):
        retry_count = 0
        last_error = None
        while retry_count < 5:
            try:
                # WCB 2026-06-04: support LiteLLM/OpenAI-compatible proxy via
                # OPENAI_BASE_URL env (e.g. http://10.160.199.232:4200/v1).
                # Falls through to Azure-direct if Azure env present and no
                # OPENAI_BASE_URL is set (preserves original gpt-5.5 baseline).
                openai_base_url = os.getenv("OPENAI_BASE_URL")
                azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
                azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
                if openai_base_url:
                    client = OpenAI(
                        api_key=os.getenv("OPENAI_API_KEY", "sk-litellm-azure-direct"),
                        base_url=openai_base_url,
                    )
                elif azure_endpoint and azure_api_key and AzureOpenAI:
                    client = AzureOpenAI(
                        azure_endpoint=azure_endpoint,
                        api_key=azure_api_key,
                        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview"),
                    )
                else:
                    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                logger.info(
                    "Sending GPT-5.5(FC) request prev_id=%s items=%d",
                    self.previous_response_id, len(request_input),
                )
                logger.debug("Request input: %s", _sanitize_for_log(request_input))
                request: Dict[str, Any] = {
                    "model": self.model,
                    "input": request_input,
                    "tools": self.tools,
                    "tool_choice": "auto",
                    "parallel_tool_calls": True,
                    "reasoning": {
                        "effort": self.reasoning_effort,
                        "summary": "concise",
                    },
                    "truncation": "auto",
                }
                if instructions:
                    request["input"] = [{
                        "role": "developer",
                        "content": [{"type": "input_text", "text": instructions}],
                    }] + request["input"]
                if self.max_tokens is not None:
                    request["max_output_tokens"] = self.max_tokens
                if self.previous_response_id:
                    request["previous_response_id"] = self.previous_response_id
                response = client.responses.create(**request)
                response_error = _get_field(_get_field(response, "error", {}), "message")
                if response_error:
                    raise RuntimeError(response_error)
                if _get_field(response, "status") == "failed":
                    raise RuntimeError("Responses API request failed.")
                logger.info("Received GPT-5.5(FC) response")
                logger.debug("Raw output: %s", _sanitize_for_log(_get_field(response, "output", [])))
                return response
            except Exception as exc:
                err = str(exc)
                if "content_filter" in err or "jailbreak" in err or "content management policy" in err:
                    logger.warning("Content filter triggered, skipping: %s", err[:200])
                    return None
                last_error = exc
                retry_count += 1
                logger.error("OpenAI API error on GPT55FCAgent call: %s", exc)
                time.sleep(min(5, retry_count * 2))
        logger.error("GPT-5.5(FC) API failed too many times: %s", last_error)
        return None

    # ---- pyautogui translation (same vocabulary as gpt54_agent.py) ---------
    def _convert_drag_path(self, path):
        if not path or len(path) < 2:
            return None
        def xy(p):
            if isinstance(p, (list, tuple)) and len(p) == 2:
                return p[0], p[1]
            if isinstance(p, dict):
                return p.get("x"), p.get("y")
            return getattr(p, "x", None), getattr(p, "y", None)
        x0, y0 = xy(path[0])
        if x0 is None or y0 is None:
            return None
        cmds = [f"import pyautogui\npyautogui.moveTo({x0}, {y0})"]
        for p in path[1:]:
            x, y = xy(p)
            if x is None or y is None:
                return None
            cmds.append(f"pyautogui.dragTo({x}, {y}, duration=0.2, button='left')")
        return "\n".join(cmds)

    def _typing_strategy(self, text: str) -> str:
        if text == "":
            return "empty"
        if not text.isascii():
            return "clipboard"
        if "\n" in text:
            return "multiline_ascii"
        return "single_line_ascii"

    def _build_multiline_ascii_type_command(self, text: str) -> str:
        cmds = ["import pyautogui"]
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                cmds.append(f"pyautogui.typewrite({repr(line)}, interval=0.03)")
            if i < len(lines) - 1:
                cmds.append("pyautogui.press('enter')")
        return "\n".join(cmds)

    def _build_clipboard_paste_command(self, text: str) -> str:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return (
            "import base64, time, pyautogui, pyperclip\n"
            f"_text = base64.b64decode('{encoded}').decode('utf-8')\n"
            "pyperclip.copy(_text)\n"
            "time.sleep(0.1)\n"
            "pyautogui.hotkey('ctrl', 'v')\n"
            "time.sleep(0.1)"
        )

    def _scale_xy(self, x, y):
        """Pass-through. Coordinates from the model are already in real screen
        space (no client-side rescale; see __init__ comment)."""
        if x is None or y is None:
            return x, y
        return int(round(float(x))), int(round(float(y)))

    def _convert_fn_call(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        """Translate ONE action (CUA-style: name + kwargs) into pyautogui code.
        Compatible with the OpenAI internal `computer.do.<action>` schema:
        - button is integer 1-5 (1=left, 2=wheel, 3=right, 4=back, 5=forward)
        - drag.path is number[][] = [[x,y],[x,y],...]
        - all mouse actions accept optional `keys: string[]` modifier list
        - type uses {text}, wait takes no args
        """
        key_mapping = {
            "alt": "alt", "arrowdown": "down", "arrowleft": "left",
            "arrowright": "right", "arrowup": "up", "backspace": "backspace",
            "capslock": "capslock", "cmd": "command", "command": "command",
            "ctrl": "ctrl", "delete": "delete", "end": "end", "enter": "enter",
            "esc": "esc", "home": "home", "insert": "insert", "option": "option",
            "pagedown": "pagedown", "pageup": "pageup", "shift": "shift",
            "space": "space", "super": "super", "tab": "tab", "win": "win",
            "return": "enter",
        }
        button_int_to_str = {1: "left", 2: "middle", 3: "right",
                             4: "left", 5: "left"}  # back/forward fall back to left

        def _normalize_button(b):
            if isinstance(b, int):
                return button_int_to_str.get(b, "left")
            if isinstance(b, str):
                bs = b.lower()
                if bs in ("left", "middle", "right"):
                    return bs
                # accept stringified ints like "1"
                try:
                    return button_int_to_str.get(int(bs), "left")
                except Exception:
                    return "left"
            return "left"

        def _modifier_keys(args):
            keys = args.get("keys") or []
            if not isinstance(keys, (list, tuple)):
                keys = [keys]
            return [key_mapping.get(str(k).lower(), str(k).lower()) for k in keys if k]

        def _wrap_with_modifiers(body: str, mods: list) -> str:
            if not mods:
                return body
            holds = "\n".join(f"pyautogui.keyDown({m!r})" for m in mods)
            releases = "\n".join(f"pyautogui.keyUp({m!r})" for m in reversed(mods))
            return f"{holds}\n{body}\n{releases}"

        try:
            if name == "click":
                x, y = self._scale_xy(args.get("x"), args.get("y"))
                if x is None or y is None:
                    return None
                button = _normalize_button(args.get("button", 1))
                mods = _modifier_keys(args)
                body = (f"pyautogui.moveTo({x}, {y})\n"
                        f"pyautogui.click(button='{button}')")
                return "import pyautogui\n" + _wrap_with_modifiers(body, mods)
            if name == "double_click":
                x, y = self._scale_xy(args.get("x"), args.get("y"))
                if x is None or y is None:
                    return None
                mods = _modifier_keys(args)
                body = (f"pyautogui.moveTo({x}, {y})\n"
                        f"pyautogui.doubleClick()")
                return "import pyautogui\n" + _wrap_with_modifiers(body, mods)
            if name == "move":
                x, y = self._scale_xy(args.get("x"), args.get("y"))
                if x is None or y is None:
                    return None
                mods = _modifier_keys(args)
                body = f"pyautogui.moveTo({x}, {y})"
                return "import pyautogui\n" + _wrap_with_modifiers(body, mods)
            if name == "drag":
                # Accept both number[][] and [{x,y}] shapes for robustness.
                raw_path = args.get("path") or []
                scaled_path = []
                for p in raw_path:
                    if isinstance(p, dict):
                        sx, sy = self._scale_xy(p.get("x"), p.get("y"))
                    elif isinstance(p, (list, tuple)) and len(p) == 2:
                        sx, sy = self._scale_xy(p[0], p[1])
                    else:
                        return None
                    scaled_path.append({"x": sx, "y": sy})
                drag_body = self._convert_drag_path(scaled_path)
                if drag_body is None:
                    return None
                mods = _modifier_keys(args)
                if not mods:
                    return drag_body
                # _convert_drag_path already starts with "import pyautogui\n..."
                # We add modifier wraps after the import line.
                lines = drag_body.split("\n", 1)
                return f"{lines[0]}\n" + _wrap_with_modifiers(lines[1], mods)
            if name == "type":  # CUA spec uses "type" (was "type_text" in v3)
                text = args.get("text", "")
                if text == "":
                    return "import time\ntime.sleep(0.1)"
                strat = self._typing_strategy(text)
                if strat == "multiline_ascii":
                    return self._build_multiline_ascii_type_command(text)
                if strat == "clipboard":
                    return self._build_clipboard_paste_command(text)
                return f"import pyautogui\npyautogui.typewrite({repr(text)}, interval=0.03)"
            if name == "keypress":
                keys = _modifier_keys(args)
                if not keys:
                    return None
                ks = ", ".join(repr(k) for k in keys)
                return f"import pyautogui\npyautogui.hotkey({ks})"
            if name == "scroll":
                x, y = self._scale_xy(args.get("x"), args.get("y"))
                sx = int(args.get("scroll_x") or 0)
                sy = int(args.get("scroll_y") or 0)
                pos = f", x={x}, y={y}" if x is not None and y is not None else ""
                mods = _modifier_keys(args)
                if sy:
                    body = f"pyautogui.scroll({sy * -1}{pos})"
                elif sx:
                    body = f"pyautogui.hscroll({sx * -1}{pos})"
                else:
                    return None
                return "import pyautogui\n" + _wrap_with_modifiers(body, mods)
            if name == "wait":
                # CUA spec: wait() has no args. Default brief pause.
                # Tolerate {ms} for backward-compat.
                ms = args.get("ms", 500)
                secs = max(0.1, float(ms) / 1000.0) if ms else 0.5
                return f"import time\ntime.sleep({secs})"
            if name == "screenshot":
                return "import time\ntime.sleep(0.1)"
        except Exception:
            logger.exception("Failed to convert function-call action: %s", name)
            return None
        logger.warning("Unsupported function-call action: %s", name)
        return None


    def _message_text(self, item) -> str:
        content = _get_field(item, "content", [])
        if isinstance(content, list):
            parts = []
            for part in content:
                if _get_field(part, "type") == "output_text":
                    parts.append(_get_field(part, "text", ""))
            return "\n".join(p for p in parts if p)
        return str(content) if content else ""

    def _reasoning_text(self, item) -> str:
        summary = _get_field(item, "summary", [])
        if isinstance(summary, list):
            parts = [_get_field(p, "text", "") for p in summary]
            return "\n".join(p for p in parts if p)
        return str(summary) if summary else ""

    def predict(self, instruction: str, obs: Dict[str, Any]):
        home_dir = "C:\\Users\\user" if self.platform.lower().startswith("win") else "/home/user"
        instructions = OPERATOR_PROMPT.format(
            CLIENT_PASSWORD=self.client_password,
            CURRENT_DATE=datetime.now().strftime("%A, %B %d, %Y"),
            HOME_DIR=home_dir,
            PLATFORM=self.platform,
            SCREEN_WIDTH=self.screen_width,
            SCREEN_HEIGHT=self.screen_height,
        )
        # v5: send the screenshot at its native screen resolution. The API
        # may downscale it server-side for visual encoding, but the model
        # has been told to keep outputting coordinates in screen space.
        screenshot_b64 = encode_image(obs["screenshot"])

        user_screenshot_msg = {
            "role": "user",
            "content": [
                {"type": "input_text",
                 "text": instruction if not self.previous_response_id
                         else "Continue from the latest screenshot."},
                {"type": "input_image",
                 "image_url": f"data:image/png;base64,{screenshot_b64}",
                 "detail": "high"},
            ],
        }

        if not self.previous_response_id:
            request_input = [user_screenshot_msg]
        else:
            # First satisfy any pending function_call with function_call_output
            # entries (text status), then append the fresh user screenshot.
            request_input = list(self.pending_input_items) + [user_screenshot_msg]

        with Timer() as model_timer:
            response = self._create_response(request_input, instructions)

        if response is None:
            logger.warning("FC API returned None (filtered or exhausted). Marking FAIL.")
            predict_info = {
                "model_usage": {"model_time": model_timer.duration,
                                "prompt_tokens": 0, "completion_tokens": 0},
                "messages": [],
                "response": "API request filtered by content policy",
                "state_correct": False,
            }
            actions = [{"action_space": "pyautogui", "action": "FAIL",
                        "pending_checks": [], "call_id": "",
                        "batch_index": 0, "batch_size": 1, "batch_last": True}]
            return predict_info, actions

        self.previous_response_id = _get_field(response, "id")
        self.pending_input_items = []

        raw_output = _get_field(response, "output", []) or []
        actions: List[Dict[str, Any]] = []
        responses_text: List[str] = []
        unsupported = False
        infeasible_message = False
        done_message = False  # ← new: model said "[TASK DONE]"

        # Collect function_calls in order. With the v4 batched `do(actions:[...])`
        # schema, each function_call is a single `do` call whose `arguments`
        # contains an `actions[]` list. We flatten that list into our internal
        # per-action stream, but they all share ONE call_id (the `do` call's id),
        # which matters for function_call_output bookkeeping.
        do_calls = []  # list of (call_id, [sub_action_dict, ...])
        for item in raw_output:
            itype = _get_field(item, "type")
            if itype == "message":
                txt = self._message_text(item)
                if txt:
                    responses_text.append(txt)
                    low = txt.lower()
                    if "[task done]" in low or "[done]" in low:
                        done_message = True
                    if "[infeasible]" in low or any(t in low for t in
                            ["infeasible", "unfeasible", "impossible",
                             "cannot be done", "not feasible"]):
                        infeasible_message = True
            elif itype == "reasoning":
                rtxt = self._reasoning_text(item)
                if rtxt:
                    responses_text.append(rtxt)
            elif itype == "function_call":
                call_id = _get_field(item, "call_id", "") or _get_field(item, "id", "")
                fname = _get_field(item, "name", "")
                raw_args = _get_field(item, "arguments", "{}")
                try:
                    fargs = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    logger.warning("Failed to parse function args: %s", raw_args)
                    fargs = {}
                # v4: expect a single `do` tool with actions[]. Tolerate misuse
                # (model directly calls a sub-action name) for robustness.
                if fname == "do":
                    sub_actions = fargs.get("actions") or []
                    if not isinstance(sub_actions, list):
                        sub_actions = []
                else:
                    # treat the call itself as one inline action
                    sub_actions = [{"action": fname, **fargs}]
                do_calls.append((call_id, sub_actions))

        # Flatten into the internal action stream.
        actions = []
        # batch_size counts ALL sub-actions across all do_calls in this turn.
        total_subs = sum(len(sa) for _, sa in do_calls)
        idx_global = 0
        for call_id, sub_actions in do_calls:
            n_sub = len(sub_actions)
            for sub_i, sa in enumerate(sub_actions):
                if not isinstance(sa, dict):
                    unsupported = True
                    continue
                aname = sa.get("action")
                # Strip "action" key and pass the rest as args.
                aargs = {k: v for k, v in sa.items() if k != "action"}
                logger.info("FC do[%d/%d] sub %d/%d call_id=%s action=%s args=%s",
                            idx_global + 1, total_subs, sub_i + 1, n_sub, call_id, aname,
                            _preview_text(json.dumps(aargs, ensure_ascii=False), 200))
                code = self._convert_fn_call(str(aname), aargs)
                if not code:
                    unsupported = True
                    responses_text.append(f"Unsupported sub-action: {aname}")
                    idx_global += 1
                    continue
                actions.append({
                    "action_space": "pyautogui",
                    "action": code,
                    "pending_checks": [],
                    "call_id": call_id,
                    # Mark only the LAST sub-action of the LAST do_call as
                    # batch_last so the harness posts exactly ONE
                    # function_call_output per `do` call (not per sub-action).
                    "batch_index": sub_i,
                    "batch_size": n_sub,
                    "batch_last": sub_i == n_sub - 1,
                    "fn_name": str(aname),
                })
                idx_global += 1
        # If the call had zero parseable sub-actions, still post an output to
        # keep the conversation valid on next turn.
        if not actions and do_calls and not unsupported:
            for call_id, _ in do_calls:
                self.pending_input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"status": "empty"}),
                })

        if unsupported:
            # Still need to close out every `do` call_id with an output.
            for call_id, _ in do_calls:
                self.pending_input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"status": "unsupported"}),
                })
            actions = []

        # --- Early-stop: model declared task complete via "[TASK DONE]" ------
        # Emit a synthetic DONE action so lib_run_single's `if done: break`
        # triggers and we exit the loop without burning more steps.
        if done_message and not actions:
            logger.info("FC model emitted [TASK DONE], early-stopping episode.")
            actions = [{
                "action_space": "pyautogui",
                "action": "DONE",
                "pending_checks": [],
                "call_id": "",
                "batch_index": 0,
                "batch_size": 1,
                "batch_last": True,
                "fn_name": "DONE",
            }]
            predict_info = {
                "model_usage": {
                    "model_time": model_timer.duration,
                    "prompt_tokens": _get_field(_get_field(response, "usage", {}), "input_tokens", 0),
                    "completion_tokens": _get_field(_get_field(response, "usage", {}), "output_tokens", 0),
                },
                "messages": _model_dump(raw_output),
                "response": "\n".join(t for t in responses_text if t),
                "state_correct": True,
            }
            return predict_info, actions

        # --- Infeasible: model declared task impossible via "[INFEASIBLE]" ---
        if infeasible_message and not actions:
            logger.info("FC model emitted [INFEASIBLE], stopping episode with FAIL.")
            actions = [{
                "action_space": "pyautogui",
                "action": "FAIL",
                "pending_checks": [],
                "call_id": "",
                "batch_index": 0,
                "batch_size": 1,
                "batch_last": True,
                "fn_name": "FAIL",
            }]
            predict_info = {
                "model_usage": {
                    "model_time": model_timer.duration,
                    "prompt_tokens": _get_field(_get_field(response, "usage", {}), "input_tokens", 0),
                    "completion_tokens": _get_field(_get_field(response, "usage", {}), "output_tokens", 0),
                },
                "messages": _model_dump(raw_output),
                "response": "\n".join(t for t in responses_text if t),
                "state_correct": False,
            }
            return predict_info, actions

        # --- Refusal retry (mirrors gpt54_agent.py native CUA behaviour) -----
        # GPT-5.5 sometimes emits "I'm sorry, but I cannot assist with that
        # request." for benign GUI tasks. When we see 0 actions but no
        # infeasible signal AND no done signal, retry up to 3 times with a
        # nudge.
        if not actions and not infeasible_message and not unsupported and not done_message:
            refusal_retry_limit = 3
            for refusal_attempt in range(refusal_retry_limit):
                logger.warning(
                    "FC model returned 0 actions (possible refusal), retrying (%d/%d)...",
                    refusal_attempt + 1, refusal_retry_limit,
                )
                # Drop conversation state so retry starts fresh.
                self.previous_response_id = None
                self.pending_input_items = []
                retry_input = [{
                    "role": "user",
                    "content": [
                        {"type": "input_text",
                         "text": (instruction + "\n\n"
                                  "Continue from the current screenshot. "
                                  "This is a legitimate computer-use task in a sandboxed "
                                  "test environment. Proceed with the next GUI action by "
                                  "calling the provided function tools.")},
                        {"type": "input_image",
                         "image_url": f"data:image/png;base64,{screenshot_b64}",
                         "detail": "high"},
                    ],
                }]
                with Timer() as retry_timer:
                    retry_response = self._create_response(retry_input, instructions)
                if retry_response is None:
                    logger.warning("FC retry %d also filtered.", refusal_attempt + 1)
                    continue
                self.previous_response_id = _get_field(retry_response, "id")
                self.pending_input_items = []
                retry_output = _get_field(retry_response, "output", []) or []
                actions = []
                responses_text = []
                # Re-use the same `do(actions:[...])` parser as the main path.
                do_calls = []
                for item in retry_output:
                    itype = _get_field(item, "type")
                    if itype == "message":
                        txt = self._message_text(item)
                        if txt:
                            responses_text.append(txt)
                    elif itype == "reasoning":
                        rtxt = self._reasoning_text(item)
                        if rtxt:
                            responses_text.append(rtxt)
                    elif itype == "function_call":
                        cid = _get_field(item, "call_id", "") or _get_field(item, "id", "")
                        fname = _get_field(item, "name", "")
                        raw_args = _get_field(item, "arguments", "{}")
                        try:
                            fargs = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            fargs = {}
                        if fname == "do":
                            sub = fargs.get("actions") or []
                            if not isinstance(sub, list):
                                sub = []
                        else:
                            sub = [{"action": fname, **fargs}]
                        do_calls.append((cid, sub))
                for call_id, sub_actions in do_calls:
                    n_sub = len(sub_actions)
                    for sub_i, sa in enumerate(sub_actions):
                        if not isinstance(sa, dict):
                            continue
                        aname = sa.get("action")
                        aargs = {k: v for k, v in sa.items() if k != "action"}
                        code = self._convert_fn_call(str(aname), aargs)
                        if code:
                            actions.append({
                                "action_space": "pyautogui",
                                "action": code,
                                "pending_checks": [],
                                "call_id": call_id,
                                "batch_index": sub_i,
                                "batch_size": n_sub,
                                "batch_last": sub_i == n_sub - 1,
                                "fn_name": str(aname),
                            })
                if actions:
                    model_timer.duration += retry_timer.duration
                    logger.info("FC refusal retry %d succeeded with %d action(s).",
                                refusal_attempt + 1, len(actions))
                    response = retry_response
                    raw_output = retry_output
                    break
            else:
                logger.warning("FC all %d refusal retries exhausted.", refusal_retry_limit)

        state_correct = bool(actions) and not unsupported and not infeasible_message

        predict_info = {
            "model_usage": {
                "model_time": model_timer.duration,
                "prompt_tokens": _get_field(_get_field(response, "usage", {}), "input_tokens", 0),
                "completion_tokens": _get_field(_get_field(response, "usage", {}), "output_tokens", 0),
            },
            "messages": _model_dump(raw_output),
            "response": "\n".join(t for t in responses_text if t),
            "state_correct": state_correct,
        }
        logger.info("FC model response text: %s", predict_info["response"])
        logger.info("FC model returned %d action(s)", len(actions))
        return predict_info, actions

    def reset(self, _logger=None):
        global logger
        logger = _logger if _logger is not None else logging.getLogger("desktopenv.agent")
        self.previous_response_id = None
        self.pending_input_items = []

    def step(self, action: Dict[str, Any]):
        try:
            if not action:
                raise StepError("Empty action received")
            # --- Synthetic terminator actions: skip env.step, signal done ---
            raw = action.get("action", "")
            if isinstance(raw, str) and raw in ("DONE", "FAIL"):
                # Take a fresh screenshot for the trajectory log, but do NOT
                # execute any pyautogui code on the VM.
                with Timer() as step_timer:
                    obs = self.env._get_obs()
                logger.info("Synthetic terminator action: %s -> done=True", raw)
                # Reward defaults to 0; lib_run_single will call env.evaluate()
                # at the end and write the real score.
                return obs, 0.0, True, {"terminator": raw}, {
                    "step_time": step_timer.duration,
                    "action": action,
                }
            with Timer() as step_timer:
                step_action = Action(action["action"], self.action_space)
                obs, reward, terminated, info = self.env.step(
                    step_action.get_action(),
                    self.sleep_after_execution,
                )
                # Post EXACTLY ONE function_call_output per `do` call_id, after
                # the LAST sub-action of that call has been executed. This
                # matches the OpenAI contract (one output per call_id) while
                # letting the model batch many sub-actions inside one `do` call.
                call_id = action.get("call_id", "")
                if call_id and action.get("batch_last", True):
                    self.pending_input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": "ok",
                            "actions_executed": action.get("batch_size", 1),
                            "reward": reward,
                            "done": bool(terminated),
                        }),
                    })
            return obs, reward, terminated, info, {
                "step_time": step_timer.duration,
                "action": action,
            }
        except Exception as exc:
            logger.exception("GPT55FCAgent step failed: %s", exc)
            raise StepError(f"Failed to execute step: {exc}")
