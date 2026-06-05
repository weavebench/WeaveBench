// computer-tool plugin: registers ONE GUI agent tool (`__computer__`) that
// patched @mariozechner/pi-ai exposes to OpenAI Responses as a standard
// function tool. The plugin runs INSIDE the openclaw VM with DISPLAY=:0;
// it dispatches each action via local pyautogui and ALWAYS returns a fresh
// PNG screenshot back to the agent loop after the action batch completes.
//
// History:
//   2026-05-07 native OpenAI computer_use_preview path removed
//   2026-05-07 split-mode `do_actions` peer removed (`__computer__` already
//              accepts batched `actions[]` and auto-screenshots, so a
//              separate act-only tool was redundant)
//   2026-05-07 auxiliary `screenshot` peer removed (`__computer__` already
//              auto-screenshots after every batch, so a separate observe-
//              only tool was redundant; if the model wants to re-observe
//              without acting, it can simply call __computer__ with an
//              empty `actions:[]` or with `{action:{type:"screenshot"}}`)
//
// Loaded by openclaw via jiti from
//   /home/user/.openclaw/extensions/computer-tool/index.ts
// No npm dependencies — only Node built-ins. Calls /usr/bin/python3 with a
// dynamically generated snippet using the system's pyautogui+Pillow.

import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const ACTION_TIMEOUT_MS = 60 * 1000;
const SCREENSHOT_TIMEOUT_MS = 30 * 1000;
const MAX_BUFFER = 32 * 1024 * 1024;

// Persist every captured screenshot under the shared workspace so the eval
// harness can rsync them back next to chat.jsonl. We use a monotonically
// increasing 4-digit step counter (per-process); index 0001 is the first
// screenshot, regardless of which CUA action triggered it.
const SCREENSHOT_DIR = "/tmp_workspace/_screenshots";
let screenshotStep = 0;

const KEY_MAP: Record<string, string> = {
  ENTER: "enter", RETURN: "enter", TAB: "tab", ESC: "esc", ESCAPE: "esc",
  BACKSPACE: "backspace", DELETE: "delete", SPACE: "space",
  UP: "up", DOWN: "down", LEFT: "left", RIGHT: "right",
  HOME: "home", END: "end", PAGEUP: "pageup", PAGEDOWN: "pagedown",
  CTRL: "ctrl", ALT: "alt", SHIFT: "shift", META: "win", WIN: "win",
  CMD: "ctrl", SUPER: "win",
};

function normKey(k: any): string {
  const s = String(k ?? "").trim();
  return KEY_MAP[s.toUpperCase()] || s.toLowerCase();
}

function pyRepr(v: any): string {
  return JSON.stringify(v);
}

// Translate an OpenAI CUA action → an executable Python snippet using pyautogui.
function actionToPython(action: any): string {
  const t = action?.type;
  if (!t) return "";
  switch (t) {
    case "screenshot":
      // No-op on the actuator side; caller always returns a fresh screenshot.
      return "";
    case "click":
    case "left_click":
    case "right_click":
    case "middle_click": {
      const x = Number(action.x ?? 0) | 0;
      const y = Number(action.y ?? 0) | 0;
      let btn = String(action.button || (t === "right_click" ? "right" : t === "middle_click" ? "middle" : "left"));
      if (!["left", "right", "middle"].includes(btn)) btn = "left";
      return `pyautogui.click(${x}, ${y}, button=${pyRepr(btn)})`;
    }
    case "double_click":
    case "doubleClick": {
      const x = Number(action.x ?? 0) | 0;
      const y = Number(action.y ?? 0) | 0;
      return `pyautogui.doubleClick(${x}, ${y})`;
    }
    case "triple_click": {
      const x = Number(action.x ?? 0) | 0;
      const y = Number(action.y ?? 0) | 0;
      return `pyautogui.tripleClick(${x}, ${y})`;
    }
    case "move":
    case "mouse_move":
    case "mousemove": {
      const x = Number(action.x ?? 0) | 0;
      const y = Number(action.y ?? 0) | 0;
      return `pyautogui.moveTo(${x}, ${y}, duration=0.1)`;
    }
    case "type":
    case "keyboard": {
      const text = String(action.text ?? "");
      return `pyautogui.typewrite(${pyRepr(text)}, interval=0.02)`;
    }
    case "keypress":
    case "key": {
      let keys: any = action.keys;
      if (keys == null) keys = action.text || action.key;
      if (typeof keys === "string") keys = [keys];
      if (!Array.isArray(keys)) keys = [];
      const norm = keys.map(normKey);
      if (norm.length === 0) return "";
      if (norm.length === 1) return `pyautogui.press(${pyRepr(norm[0])})`;
      return `pyautogui.hotkey(${norm.map(pyRepr).join(", ")})`;
    }
    case "scroll": {
      const x = Number(action.x ?? 0) | 0;
      const y = Number(action.y ?? 0) | 0;
      const sx = Number(action.scroll_x ?? 0) | 0;
      const sy = Number(action.scroll_y ?? 0) | 0;
      // pyautogui scroll: positive = up. OpenAI sends positive sy = scroll down.
      const amt = sy !== 0 ? -sy : sx !== 0 ? -sx : 0;
      return `pyautogui.moveTo(${x}, ${y}); pyautogui.scroll(${amt})`;
    }
    case "drag": {
      const path = Array.isArray(action.path) ? action.path : [];
      if (path.length < 2) return "";
      const x0 = Number(path[0]?.x ?? 0) | 0;
      const y0 = Number(path[0]?.y ?? 0) | 0;
      const x1 = Number(path[path.length - 1]?.x ?? 0) | 0;
      const y1 = Number(path[path.length - 1]?.y ?? 0) | 0;
      return `pyautogui.moveTo(${x0}, ${y0}); pyautogui.dragTo(${x1}, ${y1}, duration=0.5, button="left")`;
    }
    case "wait": {
      const ms = Number(action.ms ?? action.duration ?? 1000);
      return `time.sleep(${(ms / 1000).toFixed(3)})`;
    }
    case "cursor_position":
      // Reported in screenshot; nothing to actuate.
      return "";
    default:
      return "";
  }
}

function runPython(snippet: string, timeoutMs: number): Promise<{ stdout: string; stderr: string; code: number | null }> {
  return new Promise((resolve) => {
    const env = {
      ...process.env,
      DISPLAY: process.env.DISPLAY || ":0",
      XAUTHORITY: process.env.XAUTHORITY || "/home/user/.Xauthority",
    };
    execFile(
      "/usr/bin/python3",
      ["-c", snippet],
      { env, timeout: timeoutMs, maxBuffer: MAX_BUFFER },
      (err: any, stdout, stderr) => {
        resolve({
          stdout: String(stdout || ""),
          stderr: String(stderr || ""),
          code: err && typeof err.code === "number" ? err.code : err ? -1 : 0,
        });
      },
    );
  });
}

async function captureScreenshot(actionLabel: string): Promise<string | null> {
  // Write to /tmp file to dodge stdout binary issues, then read & base64 it.
  const tmp = join(tmpdir(), `cua_${process.pid}_${Date.now()}.png`);
  const py = `
import pyautogui
img = pyautogui.screenshot()
img.save(${pyRepr(tmp)}, "PNG")
print("OK")
`;
  const r = await runPython(py, SCREENSHOT_TIMEOUT_MS);
  if (r.code !== 0) {
    return null;
  }
  try {
    const buf = await fs.readFile(tmp);
    // Persist into the shared workspace screenshots dir for the eval harness.
    // Best-effort: if mkdir/writeFile fails (e.g. /tmp_workspace not present
    // outside an eval), we still return the base64 to the agent.
    try {
      screenshotStep += 1;
      const stepStr = String(screenshotStep).padStart(4, "0");
      const safeLabel = (actionLabel || "screenshot")
        .toString()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 32) || "screenshot";
      await fs.mkdir(SCREENSHOT_DIR, { recursive: true });
      const persistPath = join(SCREENSHOT_DIR, `screenshot_${stepStr}_${safeLabel}.png`);
      await fs.writeFile(persistPath, buf);
    } catch {
      // ignore — persistence is for offline review only.
    }
    await fs.unlink(tmp).catch(() => {});
    return buf.toString("base64");
  } catch {
    return null;
  }
}

async function executeAction(action: any): Promise<{ ok: boolean; err?: string }> {
  const snippet = actionToPython(action);
  if (!snippet) {
    if (action?.type === "screenshot" || action?.type === "cursor_position") {
      return { ok: true };
    }
    return { ok: false, err: `unhandled action: ${JSON.stringify(action).slice(0, 200)}` };
  }
  const py = `import pyautogui, time\npyautogui.FAILSAFE = False\n${snippet}\n`;
  const r = await runPython(py, ACTION_TIMEOUT_MS);
  if (r.code !== 0) {
    return { ok: false, err: (r.stderr || r.stdout || "python exit nonzero").slice(-500) };
  }
  // Tiny settle so subsequent screenshot captures the post-action state.
  await new Promise((res) => setTimeout(res, 150));
  return { ok: true };
}

// Mouse button code (int 1-5) → pyautogui button name. The integer encoding
// follows the OpenAI computer-use namespace convention (1=left, 2=wheel/
// middle, 3=right, 4=back, 5=forward).
const BUTTON_INT_TO_NAME: Record<number, string> = {
  1: "left",
  2: "middle",
  3: "right",
  4: "back",
  5: "forward",
};

// Translate a flat action shape ({action:"click", x, y, button:1, ...}) into
// the internal CUA-style action shape ({type:"click", x, y, button:"left",
// ...}) that `actionToPython` understands. Pass-through if the action is
// already CUA-shaped (has `type`).
function normalizeAction(a: any): any {
  if (!a || typeof a !== "object") return a;
  // Already CUA-shaped: passthrough.
  if (typeof a.type === "string") return a;
  const name = String(a.action || "").toLowerCase();
  if (!name) return a;
  const out: any = { type: name };
  for (const k of Object.keys(a)) {
    if (k === "action") continue;
    out[k] = a[k];
  }
  // Button as int (1=left, 2=wheel, 3=right, 4=back, 5=forward).
  if (typeof out.button === "number") {
    out.button = BUTTON_INT_TO_NAME[out.button] || "left";
  }
  // Drag path may arrive as number[][] (e.g. [[x,y],[x,y]]); the executor
  // expects [{x,y},{x,y}].
  if (name === "drag" && Array.isArray(out.path) && out.path.length > 0
      && Array.isArray(out.path[0])) {
    out.path = out.path.map((p: any[]) => ({ x: p?.[0] ?? 0, y: p?.[1] ?? 0 }));
  }
  // `wait` may carry no args; default to ~1s pause.
  if (name === "wait" && out.ms == null && out.duration == null) {
    out.ms = 1000;
  }
  // `keypress` uses {keys: string[]}; actionToPython handles it directly.
  return out;
}

// (SCREENSHOT_DESCRIPTION removed 2026-05-07 along with the standalone
// `screenshot` tool; __computer__ already auto-screenshots after every
// batch, so a separate observe-only tool was redundant.)


export default function register(api: any) {
  // ---- __computer__ -------------------------------------------------------
  // The single GUI tool. A fat function tool that accepts either a
  // single-action shape `{action:{...}}` or a batched shape
  // `{actions:[...]}`, executes each step via pyautogui, and ALWAYS returns
  // a fresh screenshot. To re-observe without acting, call with
  // `{actions:[]}` or `{action:{type:"screenshot"}}` — both are no-op
  // execution paths that still trigger the trailing screenshot capture.
  api.registerTool({
    name: "__computer__",
    description: [
      "Perform one or more computer actions in sequence on the remote desktop.",
      "Valid actions: click, double_click, drag, keypress, move, scroll, type, wait.",
      "Call with either:",
      "  • single-action shape  `{action: {type:<name>, ...kwargs}}`",
      "  • batched shape        `{actions: [{action:<name>, ...kwargs}, ...]}`",
      "Example:",
      "  {\"actions\": [{\"action\":\"click\",\"x\":100,\"y\":200,\"button\":1}, {\"action\":\"type\",\"text\":\"Hello, world!\"}]}",
      "PREFER batching multiple imminent actions in a single call (e.g. click then type, click then keypress, scroll then click) to minimize round-trips.",
      "The plugin runs the actions via pyautogui and ALWAYS returns a fresh screenshot of the resulting screen state, so a separate observation tool is unnecessary. Pass `actions:[]` (empty batch) for pure observation — the screenshot is still returned.",
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
    ].join("\n"),
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "object",
          additionalProperties: true,
        },
        actions: {
          type: "array",
          items: {
            type: "object",
            additionalProperties: true,
          },
        },
      },
      // NOTE: neither `action` nor `actions` is required at the framework
      // level — the plugin executor demultiplexes both shapes. Without
      // this loosening, batched `{actions:[...]}` calls would be rejected
      // by openclaw's local schema validator before reaching execute().
      additionalProperties: true,
    },
    async execute(_id: string, params: any) {
      // Demux: prefer batched `actions[]` when present, else fall back to
      // the legacy single `action` shape. An empty `actions:[]` skips the
      // executeAction loop entirely and goes straight to the trailing
      // screenshot capture (pure-observation path).
      let ops: any[];
      if (Array.isArray(params?.actions)) {
        ops = params.actions.length > 0 ? params.actions.map(normalizeAction) : [];
      } else {
        const single = params?.action ?? params;
        ops = single ? [single] : [];
      }

      const errors: string[] = [];
      let lastLabel = "action";
      for (const op of ops) {
        const res = await executeAction(op);
        if (op && typeof op === "object" && typeof op.type === "string") {
          lastLabel = String(op.type);
        }
        if (!res.ok) {
          errors.push(res.err || "unknown");
          // Continue executing remaining actions in the batch — best-effort:
          // a partial failure still returns the resulting screenshot so the
          // model can recover.
        }
      }
      const png64 = await captureScreenshot(lastLabel);
      const content: any[] = [];
      if (errors.length > 0) {
        content.push({
          type: "text",
          text: `[__computer__ error] ${errors.join(" | ")}`,
        });
      }
      if (png64) {
        content.push({ type: "image", mimeType: "image/png", data: png64 });
      } else {
        content.push({ type: "text", text: "[__computer__] screenshot capture failed" });
      }
      return { content };
    },
  });
}
