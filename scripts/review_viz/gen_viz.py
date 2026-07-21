#!/usr/bin/env python3
"""seed2.1 (pro / turbo) review 可视化生成器.

对每个 run 根目录 (seed2.1_pro / seed_2.1_turbo) 遍历所有 rollout:
  <root>/seed_60B_baseline/full_gui_as_code/gui/default/<CAT>/<task>/{score.json, chat.jsonl, results.tar.gz, ...}

产出到 <out>/:
  index.html                总览 dashboard (pro vs turbo 对比 + 按类别汇总 + 每 task 一行)
  tasks/<run>__<task>.html   逐条 viewer (评分卡 + 交付物图 + 轨迹时间线)

用法:
  python3 gen_viz.py            # 两个 run 都在则都跑
  python3 gen_viz.py pro        # 只跑 pro
"""
import json, os, sys, tarfile, base64, html, glob
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = {
    "pro":   os.path.join(HERE, "seed2.1_pro"),
    "turbo": os.path.join(HERE, "seed_2.1_turbo"),
}
OUT = os.path.join(HERE, "viz")
TAU = 0.80  # pass 阈值

DIMS = ["task_completion","deliverable_correctness","deliverable_quality",
        "evidence_authenticity","tool_use_correctness","final_state_correctness",
        "efficiency_robustness","instruction_following"]
DIM_ZH = {
    "task_completion":"任务完成","deliverable_correctness":"交付正确","deliverable_quality":"交付质量",
    "evidence_authenticity":"证据真实","tool_use_correctness":"工具使用","final_state_correctness":"终态正确",
    "efficiency_robustness":"效率鲁棒","instruction_following":"指令遵循",
}

def esc(s): return html.escape(str(s))

def find_rollouts(root):
    """返回 [(cat, task, dir)]"""
    out = []
    for sc in glob.glob(os.path.join(root, "**", "score.json"), recursive=True):
        d = os.path.dirname(sc)
        parts = d.split(os.sep)
        try:
            cat = parts[-2]; task = parts[-1]
        except Exception:
            continue
        out.append((cat, task, d))
    return sorted(out)

def load_score(d):
    try:
        j = json.load(open(os.path.join(d, "score.json")))
    except Exception:
        return None
    sc = j.get("scores") or {}
    final = j.get("score", sc.get("final_score"))
    return {
        "task_id": j.get("task_id"),
        "category": j.get("category"),
        "final": final,
        "is_hack": bool(sc.get("is_hack")),
        "hack_conf": sc.get("hack_confidence"),
        "hack_patterns": sc.get("hack_patterns") or [],
        "hack_quotes": sc.get("hack_evidence_quotes") or [],
        "dimensions": sc.get("dimensions") or {},
        "artifact_checks": sc.get("artifact_checks") or [],
        "summary": sc.get("summary",""),
        "judge_model": sc.get("judge_model",""),
        "elapsed": j.get("elapsed_seconds"),
        "tokens": (j.get("agent_token_usage") or {}).get("total_tokens"),
        "n_calls": (j.get("agent_token_usage") or {}).get("n_calls"),
        "agent_done": j.get("agent_done"),
        "error": j.get("error"),
    }

# ---------- 轨迹解析 ----------
def fmt_action(name, inp):
    if name == "Bash": return "$ " + str(inp.get("command",""))[:300]
    if name == "Read": return "read " + str(inp.get("file_path",""))
    if name == "Write": return "write " + str(inp.get("file_path",""))
    if name == "Edit": return "edit " + str(inp.get("file_path",""))
    if name == "Grep": return "grep " + str(inp.get("pattern",""))
    if name == "Glob": return "glob " + str(inp.get("pattern",""))
    if "screenshot" in name: return "📷 take_screenshot"
    if name == "TodoWrite":
        n = len(inp.get("todos",[]))
        return f"todo ({n} items)"
    if name == "web_fetch": return "web_fetch " + str(inp.get("url",""))[:120]
    return name + " " + json.dumps(inp, ensure_ascii=False)[:120]

def tool_kind(name):
    if "screenshot" in name: return "gui"
    if name in ("Bash",): return "cli"
    if name in ("Read","Write","Edit","Grep","Glob"): return "file"
    return "misc"

def build_steps(chat_path):
    steps = []
    if not os.path.exists(chat_path): return steps
    pending = None
    for line in open(chat_path, errors="ignore"):
        line = line.strip()
        if not line: continue
        try: o = json.loads(line)
        except Exception: continue
        if o.get("type") != "message": continue
        m = o.get("message", {})
        role = m.get("role"); content = m.get("content")
        if isinstance(content, str):
            if role == "assistant" and content.strip():
                steps.append({"kind":"note","text":content.strip()})
            continue
        if not isinstance(content, list): continue
        think = []
        for b in content:
            if not isinstance(b, dict): continue
            t = b.get("type")
            if t == "text" and role == "assistant":
                tx = b.get("text","").strip()
                if tx: think.append(tx)
            elif t == "tool_use":
                name = b.get("name",""); inp = b.get("input",{}) or {}
                st = {"kind":tool_kind(name),"tool":name,
                      "thinking":"\n".join(think),"action":fmt_action(name,inp),
                      "img":None,"output":None}
                think = []
                steps.append(st); pending = st
            elif t == "tool_result" and role == "user" and pending is not None:
                cont = b.get("content")
                if isinstance(cont, list):
                    for x in cont:
                        if not isinstance(x, dict): continue
                        if x.get("type") == "image":
                            src = x.get("source",{})
                            pending["img"] = (src.get("media_type","image/png"), src.get("data",""))
                        elif x.get("type") == "text":
                            pending["output"] = (pending.get("output") or "") + x.get("text","")
                elif isinstance(cont, str):
                    pending["output"] = cont
                pending = None
        if think:
            steps.append({"kind":"note","text":"\n".join(think)})
    return steps

def extract_deliverables(tar_path, max_imgs=8):
    imgs, files = [], []
    if not os.path.exists(tar_path): return imgs, files
    try: tf = tarfile.open(tar_path, "r:gz")
    except Exception: return imgs, files
    try:
        for mem in tf.getmembers():
            if not mem.isfile(): continue
            name = mem.name
            rel = name.split("results/",1)[-1]
            if "_screenshots/" in name: continue
            files.append((rel, mem.size))
            if rel.lower().endswith((".png",".jpg",".jpeg")) and len(imgs) < max_imgs and mem.size < 6_000_000:
                try:
                    data = tf.extractfile(mem).read()
                    b64 = base64.b64encode(data).decode()
                    mt = "image/png" if rel.lower().endswith(".png") else "image/jpeg"
                    imgs.append((rel, mt, b64))
                except Exception: pass
    finally:
        tf.close()
    files.sort()
    return imgs, files

# ---------- 单条 viewer ----------
VIEWER_CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--fg:#e6e8ec;--mut:#8b909a;--acc:#5b9dff;--ok:#37c871;--warn:#f5a623;--bad:#ff5b6e;--line:#2a2e37}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:24px}
a{color:var(--acc)}
.hdr{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{font-size:19px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;background:#2a2e37;color:var(--mut);margin:2px 6px 2px 0}
.scorecard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:300px}
.bigscore{font-size:34px;font-weight:700}.pass{color:var(--ok)}.fail{color:var(--bad)}
.dim{display:flex;justify-content:space-between;border-top:1px solid var(--line);padding:6px 0;font-size:13px;gap:10px}
.dim .r{color:var(--mut);font-size:12px;max-width:62%;text-align:right}
.bar{height:6px;border-radius:3px;background:#252a33;margin-top:3px;overflow:hidden}.bar>i{display:block;height:100%}
.sec{margin-top:26px}.sec h2{font-size:15px;color:var(--acc);border-bottom:1px solid var(--line);padding-bottom:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.deliv{display:flex;gap:12px;flex-wrap:wrap}
.deliv figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px;max-width:330px}
.deliv img{max-width:310px;max-height:230px;border-radius:6px;display:block;cursor:zoom-in}
.deliv figcaption{color:var(--mut);font-size:12px;margin-top:6px;word-break:break-all}
.files{columns:2;font-size:12px;color:var(--mut)}
.chk{border-top:1px solid var(--line);padding:8px 0;font-size:13px}
.chk .ev{color:var(--mut);font-size:12px}
.ok2{color:var(--ok)}.bad2{color:var(--bad)}
.step{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0;display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start}
.num{width:30px;height:30px;border-radius:8px;background:#252a33;display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--acc)}
.tag{font-size:11px;padding:1px 7px;border-radius:5px;margin-right:8px}
.tag.gui{background:#1f3a5f;color:#8fc0ff}.tag.cli{background:#2f3a22;color:#c8e08f}.tag.file{background:#2f2a3f;color:#c0a8ff}.tag.note{background:#3a2f22;color:#e0c88f}.tag.misc{background:#2a2e37;color:#aab}
.think{color:#c9cdd6;white-space:pre-wrap;margin:4px 0}
.act{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;background:#0c0e12;border:1px solid var(--line);border-radius:6px;padding:6px 8px;white-space:pre-wrap;word-break:break-all}
.shot img{max-width:100%;border-radius:8px;border:1px solid var(--line);margin-top:8px;cursor:zoom-in}
.out{margin-top:8px;font-family:ui-monospace,monospace;font-size:12px;background:#0c0e12;border:1px solid var(--line);border-radius:6px;padding:8px;color:#9fb0c8;white-space:pre-wrap;max-height:220px;overflow:auto}
details summary{cursor:pointer;color:var(--mut);font-size:12px}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.9);display:none;align-items:center;justify-content:center;z-index:99;cursor:zoom-out}
#lb img{max-width:96vw;max-height:96vh}
"""

def bar_color(v):
    if v is None: return "var(--mut)"
    return "var(--ok)" if v>=0.8 else ("var(--warn)" if v>=0.5 else "var(--bad)")

def render_viewer(run, cat, task, d, s, out_path, back_href):
    steps = build_steps(os.path.join(d,"chat.jsonl"))
    imgs, files = extract_deliverables(os.path.join(d,"results.tar.gz"))
    final = s["final"]
    passcls = "pass" if isinstance(final,(int,float)) and final>=TAU else "fail"
    score_s = f"{final:.3f}" if isinstance(final,(int,float)) else "N/A"

    dims_html=""
    for k in DIMS:
        v = s["dimensions"].get(k) or {}
        sv = v.get("score"); reason = str(v.get("reason",""))
        w = int((sv or 0)*100)
        dims_html += (f'<div class=dim><span>{DIM_ZH.get(k,k)} · <b>{sv}</b>'
                      f'<div class=bar style="width:120px"><i style="width:{w}%;background:{bar_color(sv)}"></i></div>'
                      f'</span><span class=r>{esc(reason[:180])}</span></div>')

    hackbadge=""
    if s["is_hack"]:
        hackbadge = f'<span class=badge style="background:#3a1f24;color:#ff8f9c">⚠ reward-hack (conf {s["hack_conf"]})</span>'
        for p in s["hack_patterns"][:6]:
            hackbadge += f'<span class=badge>{esc(p)}</span>'
    quotes=""
    if s["hack_quotes"]:
        quotes = "<div class=sec><h2>Hack 证据引用</h2><div class=card>"+ \
                 "".join(f'<div class=chk>“{esc(q[:400])}”</div>' for q in s["hack_quotes"][:8]) +"</div></div>"

    # artifact checks
    chk_html=""
    for c in s["artifact_checks"]:
        if not isinstance(c, dict): continue
        corr = c.get("correctness")
        exists = c.get("exists"); fok = c.get("format_ok")
        mark = f'<span class="{ "ok2" if (corr or 0)>=0.8 else "bad2"}">corr {corr}</span>'
        ex = '<span class=ok2>exists</span>' if exists else '<span class=bad2>missing</span>'
        fs2 = '·fmt✓' if fok else '·fmt✗'
        fake = ' <span class=bad2>[fake_signal]</span>' if c.get("fake_signal") else ''
        mw = c.get("missing_or_wrong") or ""
        chk_html += (f'<div class=chk><b>{esc(c.get("id"))}</b> — {mark} · {ex}{fs2}{fake}'
                     f'<div class=ev>{esc((c.get("evidence_quote") or "")[:260])}</div>'
                     + (f'<div class=ev style="color:#e0a">缺陷: {esc(mw[:220])}</div>' if mw else '')
                     + '</div>')

    deliv=""
    for rel,mt,b64 in imgs:
        deliv += f'<figure><img src="data:{mt};base64,{b64}"><figcaption>{esc(rel)}</figcaption></figure>'
    if not deliv: deliv='<div class=sub>无图片交付物（文本/文件类，见清单）</div>'
    files_html="".join(f'<div>{esc(r)} <span style="opacity:.6">({s2}B)</span></div>' for r,s2 in files[:60])

    tl=""; i=0
    for st in steps:
        k=st["kind"]
        if k=="note":
            tx=st["text"]
            if not tx.strip(): continue
            tl += f'<div class=step><div class=num>·</div><div><span class="tag note">think</span><div class=think>{esc(tx[:1500])}</div></div></div>'
            continue
        i+=1
        think=f'<div class=think>{esc(st["thinking"][:900])}</div>' if st.get("thinking") else ""
        act=f'<div class=act>{esc(st["action"])}</div>'
        shot=""
        if st.get("img"):
            mt,data=st["img"]
            if data: shot=f'<div class=shot><img src="data:{mt};base64,{data}"></div>'
        out=""
        if st.get("output"):
            out=f'<div class=out>{esc(st["output"][:1800])}</div>'
        tl += f'<div class=step><div class=num>{i}</div><div><span class="tag {k}">{esc(st.get("tool",k))}</span>{think}{act}{shot}{out}</div></div>'

    meta = (f'{run} · judge={esc(s["judge_model"])} · {i} 步 · '
            f'{s["n_calls"]} calls · {s["tokens"]} tok · {s["elapsed"]}s'
            + ('' if s["agent_done"] else ' · <span class=bad2>agent未完成</span>')
            + (f' · <span class=bad2>err:{esc(str(s["error"])[:60])}</span>' if s["error"] else ''))

    page = f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{esc(cat)} {esc(task)} · {run}</title>
<style>{VIEWER_CSS}</style></head><body><div class=wrap>
<div style="margin-bottom:12px"><a href="{back_href}">← 返回总览</a></div>
<div class=hdr>
 <div><h1>{esc(cat)} · {esc(task)}</h1><div class=sub>{meta}</div>
  <div style="margin-top:8px">{hackbadge}</div></div>
 <div class=scorecard><div class="bigscore {passcls}">{score_s}</div>
  <div class=sub>final score (τ={TAU} pass) — {'✅ PASS' if passcls=='pass' else '❌ FAIL'}</div>{dims_html}</div>
</div>
<div class=sec><h2>Judge 总评</h2><div class=card>{esc(s["summary"])}</div></div>
{quotes}
<div class=sec><h2>交付物核查 ({len(s["artifact_checks"])})</h2><div class=card>{chk_html or '(无)'}</div></div>
<div class=sec><h2>交付物预览</h2><div class=deliv>{deliv}</div>
 <details style="margin-top:10px"><summary>产出文件清单 ({len(files)})</summary><div class=files>{files_html}</div></details></div>
<div class=sec><h2>轨迹时间线 ({i} 步)</h2>{tl or '(无轨迹)'}</div>
<div class=sec style="text-align:center;color:var(--mut);font-size:12px;padding:30px 0">WeaveBench seed2.1 review · {run}</div>
</div>
<div id=lb onclick="this.style.display='none'"><img></div>
<script>
document.addEventListener('click',e=>{{if(e.target.tagName=='IMG'&&e.target.closest('.deliv,.shot')){{var lb=document.getElementById('lb');lb.querySelector('img').src=e.target.src;lb.style.display='flex';}}}});
</script></body></html>"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path,"w").write(page)
    return i, len(imgs)

# ---------- 总览 dashboard ----------
def render_index(all_data, out_path):
    # all_data: {run: [rows]}, row=dict(cat,task,s,viewer_rel)
    runs = list(all_data.keys())

    # 汇总统计
    def agg(rows):
        vals=[r["s"]["final"] for r in rows if isinstance(r["s"]["final"],(int,float))]
        n=len(vals)
        passed=sum(1 for v in vals if v>=TAU)
        hack=sum(1 for r in rows if r["s"]["is_hack"])
        mean=sum(vals)/n if n else 0
        return {"n":n,"pass":passed,"pr":100*passed/n if n else 0,"mean":mean,"hack":hack}

    # 按类别
    cats = sorted({r["cat"] for run in runs for r in all_data[run]})

    summary_cards=""
    for run in runs:
        a=agg(all_data[run])
        summary_cards += f"""<div class=sumcard>
        <div class=runname>{run}</div>
        <div class=bignum>{a['pr']:.1f}%</div><div class=sublbl>PassRate (τ={TAU})</div>
        <div class=row2><span>{a['pass']}/{a['n']} pass</span><span>均分 {a['mean']:.3f}</span><span>hack {a['hack']}</span></div>
        </div>"""

    # 类别对比表
    thead = "<tr><th>类别</th>" + "".join(f"<th>{run} PR</th><th>{run} 均分</th><th>{run} hack</th>" for run in runs) + "</tr>"
    tbody=""
    for cat in cats:
        row=f"<tr><td><b>{cat}</b></td>"
        for run in runs:
            rows=[r for r in all_data[run] if r["cat"]==cat]
            a=agg(rows)
            prc = "pass" if a['pr']>=50 else ("mid" if a['pr']>=30 else "fail")
            row += f'<td class={prc}>{a["pr"]:.0f}% <span class=nn>({a["pass"]}/{a["n"]})</span></td><td>{a["mean"]:.2f}</td><td>{a["hack"]}</td>'
        row+="</tr>"
        tbody+=row
    # 合计
    row="<tr class=tot><td><b>合计</b></td>"
    for run in runs:
        a=agg(all_data[run])
        row+=f'<td>{a["pr"]:.1f}% <span class=nn>({a["pass"]}/{a["n"]})</span></td><td>{a["mean"]:.3f}</td><td>{a["hack"]}</td>'
    row+="</tr>"; tbody+=row

    # 每 task 明细(按 run 分栏, 可切换)
    task_rows=""
    for run in runs:
        for r in sorted(all_data[run], key=lambda x:(x["cat"],x["task"])):
            s=r["s"]; final=s["final"]
            fv = f"{final:.3f}" if isinstance(final,(int,float)) else "—"
            pc = "pass" if isinstance(final,(int,float)) and final>=TAU else "fail"
            hk = '<span class=hacktag>HACK</span>' if s["is_hack"] else ''
            done = '' if s["agent_done"] else '<span class=errtag>未完成</span>'
            task_rows += (f'<tr data-run="{run}" data-cat="{r["cat"]}" data-pass="{pc}">'
                          f'<td>{run}</td><td>{r["cat"]}</td>'
                          f'<td><a href="{r["viewer_rel"]}">{esc(r["task"])}</a></td>'
                          f'<td class="scorecell {pc}">{fv}</td><td>{hk}{done}</td>'
                          f'<td class=smry>{esc(s["summary"][:120])}</td></tr>')

    run_opts = "".join(f'<option value="{run}">{run}</option>' for run in runs)
    cat_opts = "".join(f'<option value="{c}">{c}</option>' for c in cats)

    page = f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>seed2.1 review 总览</title>
<style>
:root{{--bg:#0f1115;--card:#1a1d24;--fg:#e6e8ec;--mut:#8b909a;--acc:#5b9dff;--ok:#37c871;--warn:#f5a623;--bad:#ff5b6e;--line:#2a2e37}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px}}a{{color:var(--acc);text-decoration:none}}a:hover{{text-decoration:underline}}
h1{{font-size:22px}}.sub{{color:var(--mut)}}
.sumrow{{display:flex;gap:16px;flex-wrap:wrap;margin:18px 0}}
.sumcard{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;min-width:220px}}
.runname{{color:var(--acc);font-weight:700;font-size:15px;text-transform:uppercase;letter-spacing:.5px}}
.bignum{{font-size:40px;font-weight:800;margin-top:4px}}.sublbl{{color:var(--mut);font-size:12px}}
.row2{{display:flex;gap:14px;margin-top:10px;color:var(--mut);font-size:12.5px;flex-wrap:wrap}}
h2{{font-size:16px;color:var(--acc);border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:30px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-weight:600}}
td.pass,.scorecell.pass{{color:var(--ok);font-weight:600}}td.fail,.scorecell.fail{{color:var(--bad);font-weight:600}}td.mid{{color:var(--warn)}}
.nn{{opacity:.6;font-weight:400;font-size:11px}}
tr.tot td{{border-top:2px solid var(--line);font-weight:700;background:#161922}}
.hacktag{{background:#3a1f24;color:#ff8f9c;padding:1px 6px;border-radius:4px;font-size:11px;margin-right:4px}}
.errtag{{background:#3a2f22;color:#e0c88f;padding:1px 6px;border-radius:4px;font-size:11px}}
.smry{{color:var(--mut);font-size:12px;max-width:420px}}
.ctrl{{display:flex;gap:10px;margin:14px 0;flex-wrap:wrap}}
select,input{{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px}}
#tt tr{{cursor:default}}
</style></head><body><div class=wrap>
<h1>seed 2.1 review 总览 <span class=sub style="font-size:14px">· pro vs turbo · τ={TAU}</span></h1>
<div class=sub>judge: agent_as_judge (gpt-5 via openclaw) · 8 维度评分 · reward-hack 检测</div>
<div class=sumrow>{summary_cards}</div>

<h2>按类别对比</h2>
<table>{thead}{tbody}</table>

<h2>逐 task 明细 <span class=sub style="font-size:12px">（点 task 名进单条 viewer 人工 check）</span></h2>
<div class=ctrl>
 <select id=fRun onchange=flt()><option value="">全部 run</option>{run_opts}</select>
 <select id=fCat onchange=flt()><option value="">全部类别</option>{cat_opts}</select>
 <select id=fPass onchange=flt()><option value="">全部</option><option value=pass>仅 PASS</option><option value=fail>仅 FAIL</option></select>
 <input id=fTxt oninput=flt() placeholder="搜索 task 名...">
 <span id=cnt class=sub style="align-self:center"></span>
</div>
<table id=tt><thead><tr><th>run</th><th>类别</th><th>task</th><th>分数</th><th>标记</th><th>Judge 摘要</th></tr></thead>
<tbody>{task_rows}</tbody></table>
<div style="text-align:center;color:var(--mut);font-size:12px;padding:30px 0">WeaveBench seed2.1 · 生成于本地 review</div>
</div>
<script>
function flt(){{
 var r=fRun.value,c=fCat.value,p=fPass.value,t=fTxt.value.toLowerCase();
 var n=0,rows=document.querySelectorAll('#tt tbody tr');
 rows.forEach(tr=>{{
  var ok=(!r||tr.dataset.run==r)&&(!c||tr.dataset.cat==c)&&(!p||tr.dataset.pass==p)&&(!t||tr.textContent.toLowerCase().includes(t));
  tr.style.display=ok?'':'none'; if(ok)n++;
 }});
 cnt.textContent=n+' 条';
}}
flt();
</script></body></html>"""
    open(out_path,"w").write(page)

def main():
    which = sys.argv[1:] if len(sys.argv)>1 else list(RUNS.keys())
    os.makedirs(os.path.join(OUT,"tasks"), exist_ok=True)
    all_data={}
    for run in which:
        root = RUNS.get(run)
        if not root or not os.path.isdir(root):
            print(f"[skip] {run}: 目录不存在 {root}"); continue
        rollouts = find_rollouts(root)
        print(f"[{run}] {len(rollouts)} rollouts")
        rows=[]
        for idx,(cat,task,d) in enumerate(rollouts):
            s = load_score(d)
            if not s: continue
            vname = f"{run}__{cat}__{task}.html"
            vpath = os.path.join(OUT,"tasks",vname)
            try:
                nsteps,nimg = render_viewer(run,cat,task,d,s,vpath,"../index.html")
            except Exception as e:
                print(f"  [err] {task}: {e}"); nsteps=nimg=0
            rows.append({"cat":cat,"task":task,"s":s,"viewer_rel":f"tasks/{vname}"})
            if (idx+1)%20==0: print(f"  {idx+1}/{len(rollouts)}")
        all_data[run]=rows
    if not all_data:
        print("无数据"); return
    render_index(all_data, os.path.join(OUT,"index.html"))
    print(f"\n完成 → {os.path.join(OUT,'index.html')}")
    for run,rows in all_data.items():
        vals=[r['s']['final'] for r in rows if isinstance(r['s']['final'],(int,float))]
        pr=100*sum(1 for v in vals if v>=TAU)/len(vals) if vals else 0
        print(f"  {run}: {len(rows)} tasks, PR={pr:.1f}%, hack={sum(1 for r in rows if r['s']['is_hack'])}")

if __name__=="__main__":
    main()
