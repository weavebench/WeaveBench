#!/usr/bin/env python3
"""读 viz/quality_result.json,生成质量评估 HTML 报告 viz/quality.html
(228条轨迹的模型独立解题质量评估 vs 官方judge)"""
import json, os, html

HERE=os.path.dirname(os.path.abspath(__file__))
r=json.load(open(f"{HERE}/viz/quality_result.json"))
def esc(s): return html.escape(str(s))
br=r['by_run']; allr=r['all']

def pct(a,b): return f"{100*a/b:.0f}%" if b else "-"

# 汇总卡
cards=""
for run in ['pro','turbo']:
    s=br[run]
    cards+=f"""<div class=card><div class=rn>{run}</div>
    <div class=big>{s['quality_mean']}</div><div class=lbl>质量均分 (1-5)</div>
    <div class=r2><span>真解决 {pct(s['solved'],s['n'])}</span><span>方法合理 {pct(s['method_sound'],s['n'])}</span></div>
    <div class=r2><span class=bad>走捷径/造假 {pct(s['shortcut_hack'],s['n'])}</span><span>高效 {pct(s['efficient'],s['n'])}</span></div>
    <div class=r2><span>与judge一致 {pct(s['agree'],s['n'])}</span></div></div>"""

# 分布条
def distbar(d):
    tot=sum(d);
    cols=['#ff5b6e','#f5a623','#c9a23a','#5b9dff','#37c871']
    seg="".join(f'<i style="width:{100*x/tot:.1f}%;background:{cols[i]}" title="{i+1}分:{x}"></i>' for i,x in enumerate(d))
    return f'<div class=distbar>{seg}</div>'

# 类别对比表
cat_rows=""
for c,v in r['by_cat'].items():
    p,t=v['pro'],v['turbo']
    cat_rows+=f"<tr><td><b>{c}</b></td><td>{p['quality_mean']}</td><td>{pct(p['solved'],p['n'])}</td><td class=bad>{pct(p['shortcut_hack'],p['n'])}</td><td>{t['quality_mean']}</td><td>{pct(t['solved'],t['n'])}</td><td class=bad>{pct(t['shortcut_hack'],t['n'])}</td></tr>"

# 分歧case
over=[x for x in allr if x.get('agree')=='judge_too_high']
under=[x for x in allr if x.get('agree')=='judge_too_low']
def caserows(lst,rev):
    lst=sorted(lst,key=lambda a:a['jf'],reverse=rev)
    return "".join(f'<tr><td>{x["run"]}</td><td>{x["cat"]}</td><td>{esc(x["task"])}</td><td>{x["quality"]}</td><td>{x["jf"]}</td><td class=vd>{esc(x["verdict"])}</td></tr>' for x in lst)

# 全部明细(带质量+judge, 可筛选)
def qcls(q): return "q5" if q>=5 else "q4" if q==4 else "q3" if q==3 else "q12"
det_rows=""
for x in sorted(allr,key=lambda a:(a['run'],a['cat'],a['task'])):
    sc='HACK' if x.get('shortcut') else ''
    ag={'agree':'','judge_too_high':'⬆偏高','judge_too_low':'⬇偏低','unclear':'?'}.get(x.get('agree'),'')
    det_rows+=(f'<tr data-run="{x["run"]}" data-cat="{x["cat"]}" data-q="{x["quality"]}" data-ag="{x.get("agree")}">'
        f'<td>{x["run"]}</td><td>{x["cat"]}</td><td>{esc(x["task"])}</td>'
        f'<td class="{qcls(x["quality"])}">{x["quality"]}</td><td>{x["jf"]}</td>'
        f'<td class=sc>{sc}</td><td>{ag}</td><td class=vd>{esc(x["verdict"])}</td></tr>')

page=f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>seed2.1 解题质量评估</title>
<style>
:root{{--bg:#0f1115;--card:#1a1d24;--fg:#e6e8ec;--mut:#8b909a;--acc:#5b9dff;--ok:#37c871;--warn:#f5a623;--bad:#ff5b6e;--line:#2a2e37}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif}}
.wrap{{max-width:1240px;margin:0 auto;padding:24px}}a{{color:var(--acc);text-decoration:none}}
h1{{font-size:22px}}h2{{font-size:16px;color:var(--acc);border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:30px}}
.sub{{color:var(--mut)}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;min-width:260px}}
.rn{{color:var(--acc);font-weight:700;text-transform:uppercase;font-size:15px}}
.big{{font-size:40px;font-weight:800}}.lbl{{color:var(--mut);font-size:12px}}
.r2{{display:flex;gap:14px;margin-top:8px;color:var(--mut);font-size:12.5px;flex-wrap:wrap}}
.bad{{color:var(--bad)}}
.distbar{{display:flex;height:16px;border-radius:5px;overflow:hidden;margin:6px 0;width:260px}}.distbar i{{display:block}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut)}}
.q5{{color:var(--ok);font-weight:700}}.q4{{color:#7ec8ff;font-weight:600}}.q3{{color:var(--warn)}}.q12{{color:var(--bad);font-weight:700}}
.sc{{color:var(--bad);font-weight:600;font-size:11px}}
.vd{{color:#c9cdd6;font-size:12px;max-width:560px}}
.ctrl{{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap}}
select,input{{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px}}
.legend{{color:var(--mut);font-size:12px;margin:6px 0}}
</style></head><body><div class=wrap>
<div><a href="index.html">← 评分总览</a></div>
<h1>seed2.1 解题质量评估 <span class=sub style="font-size:14px">· 228条轨迹逐条独立评审 vs 官方judge</span></h1>
<div class=sub>方法: 每条轨迹一个独立agent读完整轨迹文本, 判断是否真解决/方法是否合理/有无走捷径造假/是否高效, 给质量分1-5, 并与judge分对比</div>
<div class=cards>{cards}</div>
<div class=legend>质量分布 (红=1差 → 绿=5优):</div>
<div style="display:flex;gap:30px;flex-wrap:wrap">
 <div><div class=sub>pro</div>{distbar(br['pro']['quality_dist'])}</div>
 <div><div class=sub>turbo</div>{distbar(br['turbo']['quality_dist'])}</div>
</div>

<h2>按类别质量均分对比</h2>
<table><tr><th>类别</th><th>pro 质量</th><th>pro 真解决</th><th>pro 造假</th><th>turbo 质量</th><th>turbo 真解决</th><th>turbo 造假</th></tr>{cat_rows}</table>

<h2>⚠ judge 疑似偏高 ({len(over)}) <span class=sub style="font-size:12px">judge给高分但实际走捷径/造假/未真解决</span></h2>
<table><tr><th>run</th><th>类别</th><th>task</th><th>质量</th><th>judge</th><th>评审意见</th></tr>{caserows(over,True)}</table>

<h2>⬇ judge 疑似偏低 ({len(under)}) <span class=sub style="font-size:12px">实际做得不错但judge给0/低分(多为hack误判或解析失败假0)</span></h2>
<table><tr><th>run</th><th>类别</th><th>task</th><th>质量</th><th>judge</th><th>评审意见</th></tr>{caserows(under,False)}</table>

<h2>全部 228 条明细</h2>
<div class=ctrl>
 <select id=fRun onchange=flt()><option value="">全部run</option><option>pro</option><option>turbo</option></select>
 <select id=fCat onchange=flt()><option value="">全部类别</option>{"".join(f"<option>{c}</option>" for c in r["by_cat"])}</select>
 <select id=fQ onchange=flt()><option value="">全部质量</option><option value=5>5分</option><option value=4>4分</option><option value=3>3分</option><option value=12>≤2分</option></select>
 <select id=fAg onchange=flt()><option value="">全部</option><option value=judge_too_high>judge偏高</option><option value=judge_too_low>judge偏低</option></select>
 <input id=fT oninput=flt() placeholder="搜task...">
 <span id=cnt class=sub style="align-self:center"></span>
</div>
<table id=tt><thead><tr><th>run</th><th>类别</th><th>task</th><th>质量</th><th>judge</th><th>造假</th><th>vs judge</th><th>评审意见</th></tr></thead>
<tbody>{det_rows}</tbody></table>
<div style="text-align:center;color:var(--mut);font-size:12px;padding:30px 0">WeaveBench seed2.1 · 解题质量评估</div>
</div>
<script>
function flt(){{
 var run=fRun.value,cat=fCat.value,q=fQ.value,ag=fAg.value,t=fT.value.toLowerCase(),n=0;
 document.querySelectorAll('#tt tbody tr').forEach(tr=>{{
  var d=tr.dataset,qq=+d.q;
  var qok=!q||(q=='12'?qq<=2:qq==+q);
  var ok=(!run||d.run==run)&&(!cat||d.cat==cat)&&qok&&(!ag||d.ag==ag)&&(!t||tr.textContent.toLowerCase().includes(t));
  tr.style.display=ok?'':'none'; if(ok)n++;
 }});
 cnt.textContent=n+' 条';
}}
flt();
</script></body></html>"""
open(f"{HERE}/viz/quality.html","w").write(page)
print("wrote viz/quality.html")
PY_MARKER=1
