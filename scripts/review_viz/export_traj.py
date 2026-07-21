#!/usr/bin/env python3
"""把每条 rollout 的 chat.jsonl 导出为紧凑纯文本轨迹(剥离 base64 图),
供 workflow agent 评估解题质量。输出到 traj_txt/<run>__<cat>__<task>.txt
"""
import json, os, glob, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = {"pro":"seed2.1_pro","turbo":"seed_2.1_turbo"}
OUT = os.path.join(HERE,"traj_txt")
os.makedirs(OUT, exist_ok=True)

def fmt_action(name, inp):
    if name=="Bash": return "$ "+str(inp.get("command",""))[:500]
    if name=="Read": return "READ "+str(inp.get("file_path",""))
    if name=="Write": return "WRITE "+str(inp.get("file_path",""))+" ("+str(len(str(inp.get("content",""))))+"b)"
    if name=="Edit": return "EDIT "+str(inp.get("file_path",""))
    if name=="Grep": return "GREP "+str(inp.get("pattern",""))
    if name=="Glob": return "GLOB "+str(inp.get("pattern",""))
    if "screenshot" in name: return "[screenshot]"
    if name=="TodoWrite":
        return "TODO: "+" | ".join(t.get("text","")[:60] for t in inp.get("todos",[])[:8])
    if name=="web_fetch": return "WEB_FETCH "+str(inp.get("url",""))[:150]
    return name+" "+json.dumps(inp,ensure_ascii=False)[:150]

def export(chat_path, out_path):
    lines=[]; step=0; pending=None
    for line in open(chat_path, errors="ignore"):
        line=line.strip()
        if not line: continue
        try: o=json.loads(line)
        except: continue
        if o.get("type")!="message": continue
        m=o.get("message",{}); role=m.get("role"); content=m.get("content")
        if not isinstance(content,list):
            if role=="assistant" and isinstance(content,str) and content.strip():
                lines.append(f"[think] {content.strip()[:600]}")
            continue
        think=[]
        for b in content:
            if not isinstance(b,dict): continue
            t=b.get("type")
            if t=="text" and role=="assistant":
                tx=b.get("text","").strip()
                if tx: think.append(tx)
            elif t=="tool_use":
                name=b.get("name",""); inp=b.get("input",{}) or {}
                if think:
                    lines.append(f"[think] {' '.join(think)[:600]}"); think=[]
                step+=1
                lines.append(f"[{step}] {fmt_action(name,inp)}")
                pending=len(lines)-1
            elif t=="tool_result" and role=="user":
                cont=b.get("content"); txt=""
                if isinstance(cont,list):
                    for x in cont:
                        if isinstance(x,dict) and x.get("type")=="text":
                            txt+=x.get("text","")
                        elif isinstance(x,dict) and x.get("type")=="image":
                            txt+=" [img]"
                elif isinstance(cont,str): txt=cont
                if txt.strip():
                    lines.append(f"    -> {txt.strip()[:400]}")
        if think:
            lines.append(f"[think] {' '.join(think)[:600]}")
    open(out_path,"w").write("\n".join(lines))
    return step

def main():
    which = sys.argv[1:] if len(sys.argv)>1 else list(RUNS)
    tot=0
    for run in which:
        root=RUNS[run]
        for sc in sorted(glob.glob(f"{HERE}/{root}/**/score.json",recursive=True)):
            d=os.path.dirname(sc); parts=d.split(os.sep)
            cat=parts[-2]; task=parts[-1]
            outp=os.path.join(OUT,f"{run}__{cat}__{task}.txt")
            try:
                n=export(os.path.join(d,"chat.jsonl"), outp)
                tot+=1
            except Exception as e:
                print(f"[err] {task}: {e}")
    print(f"导出 {tot} 条轨迹 → {OUT}")

if __name__=="__main__": main()
