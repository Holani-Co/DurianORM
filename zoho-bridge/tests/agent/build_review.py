#!/usr/bin/env python3
"""Build the mobile-chat review HTML from transcript JSONL dumps."""
import html
import json
import sys
from pathlib import Path

import sys
_runs_root = Path(__file__).resolve().parent / "runs"
RUNS = (_runs_root / sys.argv[1]) if len(sys.argv) > 1 else \
    sorted(d for d in _runs_root.iterdir() if d.is_dir())[-1]
records_by_key = {}
for f, label in (("transcripts_luna.jsonl", "Luna"), ("transcripts_deepseek.jsonl", "DeepSeek")):
    p = RUNS / f
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                r["model_label"] = label
                records_by_key[(label, r["id"])] = r   # rerun keeps latest
records = list(records_by_key.values())

def esc(t):
    return html.escape(str(t or "")).replace("\n", "<br>")

def ctx_badges(rec):
    c = rec.get("context") or {}
    b = []
    if c.get("profile_set"):
        b.append(f'<span class="cb prof">PROFILE SEEDED{" · " + esc(c.get("profile_summary")) if c.get("profile_summary") else ""}</span>')
    if c.get("prior_conversations"):
        b.append(f'<span class="cb">{len(c["prior_conversations"])} PRIOR CONV</span>')
    if c.get("history"):
        b.append(f'<span class="cb">HISTORY · {len(c["history"])} MSGS</span>')
    if c.get("offers"):
        b.append(f'<span class="cb">OFFERS LIVE · {len(c["offers"])}</span>')
    if c.get("crm_fixture"):
        b.append('<span class="cb">CRM RECORD EXISTS</span>')
    if c.get("linked"):
        b.append(f'<span class="cb">{c["linked"]} LINKED IDENTITY</span>')
    if not b:
        b.append('<span class="cb fresh">FRESH CONTACT — NO PRIOR CONTEXT</span>')
    return '<div class="ctxrow">' + "".join(b) + "</div>"

def ctx_thread(rec):
    c = rec.get("context") or {}
    out = []
    for i, pc in enumerate(c.get("prior_conversations") or []):
        kind = "comment thread" if pc.get("comment") else "conversation"
        out.append(f'<div class="divider">earlier {kind}</div>')
        for m in pc["messages"]:
            side = "in" if m.get("who", "customer") == "customer" else "out"
            out.append(f'<div class="row {side} ctx"><div class="bub {side}">{esc(m.get("text"))}</div><div class="meta">{esc(m.get("at",""))}</div></div>')
    if c.get("history"):
        out.append('<div class="divider">this conversation, earlier</div>')
        for m in c["history"]:
            who = m.get("who", "customer")
            side = "in" if who == "customer" else "out"
            tag = " · human agent" if who == "human_agent" else ""
            out.append(f'<div class="row {side} ctx"><div class="bub {side}">{esc(m.get("text"))}</div><div class="meta">{esc(m.get("at",""))}{tag}</div></div>')
    if out:
        out.append('<div class="divider live">test begins</div>')
    return "\n".join(out)

def bubble_thread(rec):
    out = [ctx_thread(rec)]
    for step in rec["steps"]:
        if step.get("caption"):
            out.append(f'<div class="post"><div class="post-tag">Shared post</div>{esc(step["caption"])}</div>')
        if step.get("text") and step["text"].lower() != "shared post":
            out.append(f'<div class="row in"><div class="bub in">{esc(step["text"])}</div><div class="meta">{esc(step.get("at",""))}</div></div>')
        tools = [t.split("(")[0] for t in step.get("tools", [])]
        if tools:
            out.append('<div class="tools">' + " ".join(f'<span class="chip">{esc(t)}</span>' for t in tools) + "</div>")
        for o in step.get("outputs", []):
            if o["type"] == "sent":
                out.append(f'<div class="row out"><div class="bub out">{esc(o["text"])}</div><div class="meta">sent · auto</div></div>')
            elif o["type"] == "card":
                hold = esc(o.get("hold") or "held for review")
                out.append(f'<div class="row out"><div class="bub card">{esc(o["text"])}</div><div class="meta hold">held for agent — {hold}</div></div>')
            elif o["type"] == "offer":
                img = o.get("image_url") or ""
                pic = (f'<img class="pimg" src="{esc(img)}" loading="lazy">'
                       if "durian.in" in img else "")
                tag = "PHOTO" if pic else "OFFER IMAGE"
                kind = "photo" if pic else "offer"
                out.append(f'<div class="row out"><div class="bub offer"><span class="offer-tag">{tag}</span>{pic}{esc(o["text"])}</div><div class="meta">sent · {kind}</div></div>')
        if not step.get("outputs"):
            h = esc(step.get("handled") or "no output")
            out.append(f'<div class="sysline">({h})</div>')
    return "\n".join(out)

phones = []
for rec in records:
    status = "pass" if rec["passed"] else "fail"
    err = ""
    if rec["errors"]:
        err = f'<div class="errbar">{esc(rec["errors"][0][:180])}</div>'
    c = rec.get("context") or {}
    has_ctx = "1" if (c.get("profile_set") or c.get("history")
                      or c.get("prior_conversations") or c.get("offers")
                      or c.get("crm_fixture")) else "0"
    phones.append(f"""
<div class="phone-wrap" data-model="{rec['model_label']}" data-status="{status}" data-ctx="{has_ctx}" data-suite="{rec['suite']}">
  <div class="phone">
    <div class="notch"></div>
    <div class="head">
      <div class="avatar">{rec['model_label'][0]}</div>
      <div class="head-t"><b>{esc(rec['id'].replace('_',' '))}</b><span>{esc(rec['suite'])} · {esc(rec['surface'])} · {rec['model_label']}</span></div>
      <div class="badge {status}">{'PASS' if rec['passed'] else 'FAIL'}</div>
    </div>
    {ctx_badges(rec)}
    <div class="thread">{bubble_thread(rec)}{err}</div>
  </div>
</div>""")

suites = sorted({r["suite"] for r in records})
suite_opts = "".join(f'<option value="{s}">{s}</option>' for s in suites)
n_luna = sum(1 for r in records if r["model_label"] == "Luna")
n_luna_p = sum(1 for r in records if r["model_label"] == "Luna" and r["passed"])
n_ds = sum(1 for r in records if r["model_label"] == "DeepSeek")
n_ds_p = sum(1 for r in records if r["model_label"] == "DeepSeek" and r["passed"])

page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent-mode review — chat transcripts</title>
<style>
:root {{ --bg:#0f1117; --panel:#181b23; --in:#262a35; --out:#3b5bfd; --card:#3a3320;
  --txt:#e8eaf0; --mut:#8b90a0; --ok:#1d9e75; --bad:#e24b4a; }}
* {{ box-sizing:border-box; margin:0; }}
body {{ background:var(--bg); color:var(--txt); font:15px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif; padding:24px; }}
h1 {{ font-size:20px; font-weight:600; margin-bottom:4px; }}
.sub {{ color:var(--mut); margin-bottom:16px; font-size:13px; }}
.filters {{ display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; }}
.fbtn {{ background:var(--panel); color:var(--txt); border:1px solid #2a2e3a; padding:7px 14px;
  border-radius:18px; cursor:pointer; font-size:13px; }}
.fbtn.active {{ background:var(--out); border-color:var(--out); }}
.grid {{ display:flex; flex-wrap:wrap; gap:22px; }}
.phone-wrap {{ width:372px; }}
.phone {{ background:#000; border-radius:38px; padding:10px; border:1px solid #2a2e3a; }}
.notch {{ width:110px; height:20px; background:#000; border-radius:0 0 14px 14px; margin:0 auto 2px; }}
.head {{ display:flex; align-items:center; gap:10px; background:var(--panel);
  border-radius:22px 22px 0 0; padding:12px 14px; }}
.avatar {{ width:34px; height:34px; border-radius:50%; background:linear-gradient(135deg,#7f77dd,#d4537e);
  display:flex; align-items:center; justify-content:center; font-weight:700; }}
.head-t b {{ display:block; font-size:14px; text-transform:capitalize; }}
.head-t span {{ font-size:11px; color:var(--mut); }}
.badge {{ margin-left:auto; font-size:11px; font-weight:700; padding:4px 10px; border-radius:12px; }}
.badge.pass {{ background:rgba(29,158,117,.15); color:var(--ok); }}
.badge.fail {{ background:rgba(226,75,74,.15); color:var(--bad); }}
.thread {{ background:var(--bg); min-height:220px; max-height:560px; overflow-y:auto;
  padding:14px 12px 18px; border-radius:0 0 28px 28px; display:flex; flex-direction:column; gap:8px; }}
.row {{ display:flex; flex-direction:column; max-width:86%; }}
.row.in {{ align-self:flex-start; align-items:flex-start; }}
.row.out {{ align-self:flex-end; align-items:flex-end; }}
.bub {{ padding:9px 13px; border-radius:18px; font-size:13.5px; white-space:pre-wrap; }}
.bub.in {{ background:var(--in); border-bottom-left-radius:5px; }}
.bub.out {{ background:var(--out); border-bottom-right-radius:5px; }}
.bub.card {{ background:transparent; border:1.5px dashed #b8860b; color:#e3c56d; border-bottom-right-radius:5px; }}
.bub.offer {{ background:#20313f; border:1px solid #2e4a61; }}
.offer-tag {{ display:block; font-size:10px; letter-spacing:1px; color:#6fa8dc; margin-bottom:4px; }}
.pimg {{ width:100%; max-width:220px; border-radius:8px; display:block; margin:2px 0 6px; }}
.meta {{ font-size:10.5px; color:var(--mut); margin-top:3px; padding:0 4px; }}
.meta.hold {{ color:#b8860b; }}
.tools {{ display:flex; gap:5px; justify-content:center; flex-wrap:wrap; margin:2px 0; }}
.chip {{ font:10.5px ui-monospace,monospace; background:#1d2330; color:#7fa7d8;
  padding:2.5px 9px; border-radius:10px; border:1px solid #263042; }}
.post {{ align-self:flex-start; max-width:80%; background:#151a26; border:1px solid #263042;
  border-radius:14px; padding:10px 12px; font-size:12.5px; color:#aeb6c8; font-style:italic; }}
.post-tag {{ font-size:10px; letter-spacing:1px; color:#6fa8dc; font-style:normal; margin-bottom:4px; }}
.sysline {{ text-align:center; color:var(--mut); font-size:11px; }}
.ctxrow {{ display:flex; flex-wrap:wrap; gap:5px; background:var(--panel); padding:8px 14px 10px; }}
.cb {{ font-size:9.5px; letter-spacing:.6px; font-weight:600; color:#9aa3b8;
  background:#20242f; border:1px solid #2c3140; border-radius:9px; padding:3px 8px; }}
.cb.prof {{ color:#8fd4b8; border-color:#1d9e7544; }}
.cb.fresh {{ color:#d8b56d; border-color:#b8860b44; }}
.divider {{ text-align:center; color:var(--mut); font-size:10px; letter-spacing:1.2px;
  text-transform:uppercase; margin:6px 0 2px; display:flex; align-items:center; gap:8px; }}
.divider::before, .divider::after {{ content:""; flex:1; border-top:1px solid #262b38; }}
.divider.live {{ color:#6fa8dc; }}
.row.ctx {{ opacity:.5; }}
.filters select {{ background:var(--panel); color:var(--txt); border:1px solid #2a2e3a;
  padding:7px 10px; border-radius:18px; font-size:13px; }}
.errbar {{ background:rgba(226,75,74,.12); border:1px solid rgba(226,75,74,.3); color:#f09595;
  font-size:11.5px; border-radius:10px; padding:8px 10px; margin-top:6px; }}
</style></head><body>
<h1>Agent-mode transcript review</h1>
<div class="sub">Iteration-2 prompt (identity + procedure + mechanical confidence + protocol repair) ·
gpt-5.6-luna {n_luna_p}/{n_luna} · deepseek-v4-flash {n_ds_p}/{n_ds} · hard assertions, judge off ·
blue = auto-sent to customer · dashed amber = held as agent review card · chips = skills called</div>
<div class="filters">
  <button class="fbtn active" data-f="all">All</button>
  <button class="fbtn" data-f="Luna">Luna</button>
  <button class="fbtn" data-f="DeepSeek">DeepSeek</button>
  <button class="fbtn" data-f="fail">Failures only</button>
  <button class="fbtn" data-f="ctx">With prior context</button>
  <button class="fbtn" data-f="fresh">Fresh contact</button>
  <select id="suite"><option value="">All suites</option>{suite_opts}</select>
</div>
<div class="grid">{''.join(phones)}</div>
<script>
let mode = 'all';
const apply = () => {{
  const s = document.getElementById('suite').value;
  document.querySelectorAll('.phone-wrap').forEach(p => {{
    let ok = mode === 'all' || p.dataset.model === mode ||
      (mode === 'fail' && p.dataset.status === 'fail') ||
      (mode === 'ctx' && p.dataset.ctx === '1') ||
      (mode === 'fresh' && p.dataset.ctx === '0');
    if (s && p.dataset.suite !== s) ok = false;
    p.style.display = ok ? '' : 'none';
  }});
}};
document.querySelectorAll('.fbtn').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.fbtn').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); mode = b.dataset.f; apply();
}});
document.getElementById('suite').onchange = apply;
</script></body></html>"""

out = RUNS / "review.html"
out.write_text(page, encoding="utf-8")
print(out, f"| {len(records)} transcripts | luna {n_luna_p}/{n_luna} ds {n_ds_p}/{n_ds}")
