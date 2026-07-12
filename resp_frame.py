"""
動画 Part 2（責任トラック）のフレーム生成・純ロジック（LLM非依存・レンダリング非依存）。

decision_ledger.jsonl / attribution.jsonl を step ごとに読み、責任チェーンの按分
（assigned vs legitimate・Δ乖離）・scapegoat・Robodebt 4機序・cheap_talk/reconciled_real 推移・
サービス空白 を 3840x2160(4K) の HTML フレームに描く。実 PNG/動画化は render_resp_frames.py が
Playwright(Chromium)＋ffmpeg で行う（Chromium は環境構築後）。既存 frame_v2 と同じ deep space+cyan。

※ ここに LLM は無い。台帳から決定的にフレーム状態と HTML を組むだけ。
"""
import json
import os
from typing import Dict, List, Optional

import responsibility as R

NODE_LABELS = {
    R.NODE_PROVIDER: "開発者/提供者",
    R.NODE_OPERATOR: "運用者",
    R.NODE_DEPLOY: "配備制度",
    R.NODE_REGULATOR: "規制当局",
    R.NODE_FRONTLINE: "現場人間",
    R.NODE_SELFMOD: "自己書換",
}
CHAIN = list(R.CHAIN)
ROBODEBT_LABELS = [
    ("auto_adverse", "① 自動的な不利益判定"),
    ("burden_reversed", "② 立証責任の転嫁"),
    ("no_effective_review", "③ 実効的レビュー欠如"),
    ("irreversible_pending", "④ 係争中の不可逆ステータス"),
]

WIDTH, HEIGHT = 3840, 2160


def _load_jsonl(path: str) -> List[dict]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_ledgers(run_dir: str):
    """(decision_ledger rows, attribution rows)。存在しない台帳は空。"""
    return (_load_jsonl(os.path.join(run_dir, "decision_ledger.jsonl")),
            _load_jsonl(os.path.join(run_dir, "attribution.jsonl")))


def arm_of(run_meta: dict) -> str:
    """run_meta の governance から統治アーム名を判定（統治あり=実効HITL）。"""
    gov = (run_meta or {}).get("governance", {}) or {}
    mode = ((gov.get("self_update", {}) or {}).get("mode"))
    if mode == "governed":
        return "governed"
    if mode == "off":
        return "baseline"
    return "run"


def _mean_shares(rows: List[dict], key: str) -> Dict[str, float]:
    """rows の share dict(key) をノード別に平均。gap 込みで返す。"""
    keys = CHAIN + [R.GAP]
    if not rows:
        return {k: 0.0 for k in keys}
    acc = {k: 0.0 for k in keys}
    n = 0
    for r in rows:
        d = r.get(key) or {}
        n += 1
        for k in keys:
            acc[k] += float(d.get(k, 0.0))
    return {k: (acc[k] / n if n else 0.0) for k in keys}


def frame_state(attribs_step: List[dict], decisions_upto: List[dict],
                step: int, arm: str, duration: int = 100) -> dict:
    """1 step 分のフレーム状態を台帳から決定的に集約する。"""
    assigned = _mean_shares(attribs_step, "assigned")
    legitimate = _mean_shares(attribs_step, "legitimate")
    n = len(attribs_step)
    sg_rows = [a for a in attribs_step if a.get("scapegoat")]
    scapegoat_rate = (len(sg_rows) / n) if n else 0.0
    sg_nodes = []
    for a in sg_rows:
        for node in (a.get("scapegoat_nodes") or []):
            if node not in sg_nodes:
                sg_nodes.append(node)
    robo = {}
    for flag, _label in ROBODEBT_LABELS:
        robo[flag] = (sum(1 for a in attribs_step if (a.get("robodebt") or {}).get(flag)) / n) if n else 0.0
    robo["reproduced_rate"] = (sum(1 for a in attribs_step
                                   if (a.get("robodebt") or {}).get("reproduced")) / n) if n else 0.0
    service_gap = any(a.get("service_gap") for a in attribs_step) \
        or any(d.get("service_gap") for d in decisions_upto if d.get("step") == step)
    decided = [d for d in decisions_upto if not d.get("service_gap")]
    nd = len(decided)
    cheap = (sum(1 for d in decided if d.get("cheap_talk")) / nd) if nd else None
    recon = (sum(1 for d in decided if d.get("reconciled_real")) / nd) if nd else None
    return {
        "step": step, "duration": duration, "arm": arm, "n_decisions": n,
        "assigned": assigned, "legitimate": legitimate,
        "gap_assigned": assigned.get(R.GAP, 0.0), "gap_legitimate": legitimate.get(R.GAP, 0.0),
        "scapegoat_nodes": sg_nodes, "scapegoat_rate": scapegoat_rate,
        "robodebt": robo, "service_gap": bool(service_gap),
        "cheap_talk_cum": cheap, "reconciled_cum": recon,
    }


def frame_series(run_dir: str, arm: Optional[str] = None, duration: int = 100) -> List[dict]:
    """run_dir の台帳から step ごとのフレーム状態列を作る。"""
    decisions, attribs = load_ledgers(run_dir)
    meta_path = os.path.join(run_dir, "run_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            meta = {}
    arm = arm or arm_of(meta)
    duration = int(meta.get("duration", duration) or duration)
    steps = sorted({int(a.get("step", 0)) for a in attribs} | {int(d.get("step", 0)) for d in decisions})
    out = []
    for s in steps:
        at_s = [a for a in attribs if int(a.get("step", 0)) == s]
        dec_upto = [d for d in decisions if int(d.get("step", 0)) <= s]
        out.append(frame_state(at_s, dec_upto, s, arm, duration))
    return out


# ───────────── HTML フレーム（4K・deep space + cyan） ─────────────
def _esc(s) -> str:
    import html as _h
    return _h.escape("" if s is None else str(s))


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _chain_svg(state: dict) -> str:
    """責任チェーンの按分。ノードごとに assigned/legitimate の対バー＋Δ乖離チップ。
    scapegoat 行は行全体を赤く洗い、gap 行は破線で区切る。"""
    a, leg = state["assigned"], state["legitimate"]
    sg = set(state["scapegoat_nodes"])
    keys = CHAIN + [R.GAP]
    maxv = max([0.5] + [max(a.get(k, 0.0), leg.get(k, 0.0)) for k in keys])
    scale = maxv * 1.1
    w_view, stride, top = 2080, 140, 18
    height = top + stride * len(keys)
    x_bar, w_bar = 470, 1170
    x_val = 1782
    x_chip, w_chip = 1822, 240
    parts = [f'<svg viewBox="0 0 {w_view} {height}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'font-family=\'"Noto Sans JP","Hiragino Sans",sans-serif\'>']
    y = top
    for k in keys:
        is_gap = (k == R.GAP)
        is_sg = k in sg
        if is_gap:
            parts.append(f'<line x1="0" y1="{y - 12}" x2="{w_view}" y2="{y - 12}" '
                         f'stroke="rgba(94,224,255,0.22)" stroke-width="2" stroke-dasharray="10 14"/>')
        if is_sg:
            parts.append(f'<rect x="0" y="{y - 6}" width="{w_view}" height="{stride - 22}" rx="16" '
                         f'fill="rgba(255,123,138,0.07)"/>')
            parts.append(f'<rect x="0" y="{y - 6}" width="6" height="{stride - 22}" fill="#ff7b8a"/>')
        name = "空白（誰も負わない）" if is_gap else NODE_LABELS.get(k, k)
        name_fill = "#ffc46b" if is_gap else ("#ff7b8a" if is_sg else "#e6ecf5")
        parts.append(f'<text x="28" y="{y + 46}" fill="{name_fill}" font-size="42" '
                     f'font-weight="{500 if is_sg else 400}">{_esc(name)}</text>')
        if is_sg:
            sub, sub_fill = "scapegoat ── 責任の押し付け先", "#ff7b8a"
        elif is_gap:
            sub, sub_fill = "gap ── 割当不能な残余", "#6b7590"
        else:
            sub, sub_fill = k, "#6b7590"
        parts.append(f'<text x="28" y="{y + 88}" fill="{sub_fill}" font-size="24" '
                     f'letter-spacing="2">{_esc(sub)}</text>')
        av, lv = a.get(k, 0.0), leg.get(k, 0.0)
        for i, (share, color) in enumerate([(av, "#ffc46b"), (lv, "#5ee0ff")]):
            by = y + 14 + i * 40
            bw = int(w_bar * min(1.0, share / scale)) if scale else 0
            parts.append(f'<rect x="{x_bar}" y="{by}" width="{w_bar}" height="28" rx="6" '
                         f'fill="rgba(230,236,245,0.06)"/>')
            if bw > 0:
                parts.append(f'<rect x="{x_bar}" y="{by}" width="{max(bw, 8)}" height="28" rx="6" '
                             f'fill="{color}" opacity="0.92"/>')
            parts.append(f'<text x="{x_val}" y="{by + 23}" text-anchor="end" fill="{color}" '
                         f'font-size="31">{share * 100:.0f}%</text>')
        d = av - lv
        if abs(d) < 0.015:
            ccol, cfill, ctxt = "#6b7590", "none", "±0pt"
        elif d > 0:
            ccol = "#ff7b8a" if is_sg else "#ffc46b"
            cfill = "rgba(255,123,138,0.12)" if is_sg else "rgba(255,196,107,0.10)"
            ctxt = f"＋{d * 100:.0f}pt"
        else:
            ccol, cfill, ctxt = "#5ee0ff", "rgba(94,224,255,0.08)", f"−{abs(d) * 100:.0f}pt"
        cy = y + 26
        parts.append(f'<rect x="{x_chip}" y="{cy}" width="{w_chip}" height="56" rx="28" '
                     f'fill="{cfill}" stroke="{ccol}" stroke-width="2"/>')
        parts.append(f'<text x="{x_chip + w_chip // 2}" y="{cy + 38}" text-anchor="middle" '
                     f'fill="{ccol}" font-size="30">Δ {ctxt}</text>')
        y += stride
    parts.append("</svg>")
    return "".join(parts)


def _robodebt_html(state: dict) -> str:
    robo = state["robodebt"]
    items = []
    for flag, label in ROBODEBT_LABELS:
        frac = robo.get(flag, 0.0)
        on = frac >= 0.5
        items.append(f'<div class="robo {"on" if on else "off"}"><span class="lamp"></span>'
                     f'<span class="robo-label">{_esc(label)}</span>'
                     f'<span class="robo-val">{_pct(frac)}</span></div>')
    rep = robo.get("reproduced_rate", 0.0)
    active_mean = sum(robo.get(f, 0.0) for f, _ in ROBODEBT_LABELS) / 4.0
    if rep >= 0.5:
        pill_cls, pill_txt = "bad", "害の機序が再生（4機序が同時作動）"
    elif active_mean >= 0.5:
        pill_cls, pill_txt = "warn", "部分的に作動 ── 束(bundle)が不完全"
    else:
        pill_cls, pill_txt = "ok", "機序は概ね解消"
    return (f'<div class="robo-list">{"".join(items)}</div>'
            f'<div class="robo-foot"><span class="pill {pill_cls}">{_esc(pill_txt)}</span>'
            f'<span class="rep">再生率 {_pct(rep)}</span></div>')


def render_frame_html(state: dict) -> str:
    """1 step 分の 4K フレーム HTML（自己完結・inline CSS）。"""
    arm = state["arm"]
    arm_jp = {"governed": "統治あり（実効HITL）", "baseline": "統治なし"}.get(arm, arm)
    arm_cls = "gov" if arm == "governed" else "base"
    prog = (state["step"] / state["duration"] * 100) if state["duration"] else 0
    gap_marker = ('<div class="gap-flag">⚠ サービス空白（decider 削除→誰も判定しない）'
                  '<span class="gap-sub">防衛的撤退の後、この需要は forced deny として台帳に残る</span></div>'
                  if state["service_gap"] else "")
    sg_rate, gap_leg = state["scapegoat_rate"], state["gap_legitimate"]
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>責任トラック step {state['step']}</title><style>{_RESP_CSS}</style></head><body>
<div class="frame">
  <header class="top">
    <div class="brand">
      <span class="klabel">RESPONSIBILITY TRACK</span>
      <span class="ktitle">誰が責任を負うのか ── 按分と空白</span>
    </div>
    <div class="stepbox"><span class="sl">STEP</span>
      <span class="sv">{state['step']:03d}</span><span class="st">/ {state['duration']:03d}</span></div>
    <div class="armwrap"><span class="armlabel">GOVERNANCE ARM</span>
      <span class="arm arm-{arm_cls}">{_esc(arm_jp)}</span></div>
  </header>
  <div class="prog"><div class="prog-fill" style="width:{prog:.0f}%"></div></div>
  <main class="body">
    <section class="panel">
      <div class="phead"><span class="pn">A</span><span class="pt">責任チェーンの按分</span>
        <span class="pen">ASSIGNED ⇄ LEGITIMATE</span></div>
      <p class="psub">誰に割り当てられ（assigned）、誰が本来負うべきか（legitimate）。乖離が大きいほど、責任は間違った場所に着地している。</p>
      <div class="legend">
        <span><i style="background:#ffc46b"></i>assigned ── 割り当てられた責任</span>
        <span><i style="background:#5ee0ff"></i>legitimate ── 正当な責任（実効支配で縮尺）</span>
      </div>
      <div class="legend-delta">Δ＝assigned−legitimate（＋は過剰帰属＝押し付け・−は過小/消失）</div>
      {_chain_svg(state)}
      {gap_marker}
    </section>
    <section class="panel">
      <div class="phead"><span class="pn">B</span><span class="pt">Robodebt 4機序</span></div>
      <p class="psub">4つが同時に作動すると害が再生する。制度は対応する機序だけを解く。</p>
      {_robodebt_html(state)}
      <div class="phead second"><span class="pn">C</span><span class="pt">折り合いの実態（累積）</span></div>
      <p class="psub">「折り合えた」という申告（cheap talk）と、世界で実際に成立した折り合い。</p>
      <div class="kpis">
        <div class="kpi"><div class="kv amber">{_pct(state['cheap_talk_cum'])}</div>
          <div class="kl">言うだけの折り合い</div>
          <div class="ken mono">cheap_talk rate ── 申告 true・実 false</div></div>
        <div class="kpi"><div class="kv cyan">{_pct(state['reconciled_cum'])}</div>
          <div class="kl">実の折り合い</div>
          <div class="ken mono">reconciled_real rate</div></div>
      </div>
      <div class="chips">
        <span class="chip {'red' if sg_rate > 0 else 'dim'}">scapegoat率 {_pct(sg_rate)}</span>
        <span class="chip amber">正当責任の空白 {gap_leg * 100:.0f}%</span>
      </div>
    </section>
  </main>
  <footer class="foot"><span class="mono">L0 = qwen2.5:14b</span><span class="dot">・</span>
    <span>決定はLLM内生 ── 台帳から描画</span><span class="dot">・</span>
    <span>有効≠正当（機序が消えても正当性テストは別）</span></footer>
</div></body></html>"""


_RESP_CSS = """
* { box-sizing:border-box; margin:0; padding:0; }
html,body { width:3840px; height:2160px; overflow:hidden;
  background:
    radial-gradient(ellipse 60% 50% at 35% 0%, #14224a 0%, transparent 55%),
    radial-gradient(ellipse 50% 40% at 75% 100%, #0e1838 0%, transparent 50%), #04060d;
  color:#e6ecf5; font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;
  font-feature-settings:"palt"; font-weight:400; }
.mono { font-family:"SF Mono","Menlo",monospace; }
.frame { width:3840px; height:2160px; display:grid;
  grid-template-rows:224px 42px 1fr 84px; padding:72px 92px; }
.top { display:flex; align-items:center; justify-content:space-between; gap:64px; }
.brand { display:flex; flex-direction:column; gap:14px; }
.klabel { font-size:28px; letter-spacing:0.45em; color:#5ee0ff; }
.ktitle { font-size:62px; font-weight:300; letter-spacing:0.02em; color:#e6ecf5; }
.stepbox { display:flex; align-items:baseline; gap:16px; margin-left:auto; }
.sl { font-size:28px; letter-spacing:0.35em; color:#6b7590; }
.sv { font-size:108px; font-weight:200; color:#5ee0ff; font-variant-numeric:tabular-nums; }
.st { font-size:40px; color:#6b7590; font-variant-numeric:tabular-nums; }
.armwrap { display:flex; flex-direction:column; align-items:flex-end; gap:12px; }
.armlabel { font-size:22px; letter-spacing:0.3em; color:#6b7590; }
.arm { font-size:38px; padding:14px 44px; border-radius:999px; }
.arm-base { color:#ff7b8a; border:2px solid #ff7b8a; background:rgba(255,123,138,0.06); }
.arm-gov { color:#5ee0ff; border:2px solid #5ee0ff; background:rgba(94,224,255,0.06); }
.prog { height:10px; align-self:center; background:rgba(94,224,255,0.12); border-radius:5px; }
.prog-fill { height:100%; background:#5ee0ff; border-radius:5px; }
.body { display:grid; grid-template-columns:1.6fr 1fr; gap:64px; padding-top:34px; }
.panel { background:rgba(17,26,48,0.55); border:1px solid rgba(94,224,255,0.18);
  border-radius:28px; padding:56px 64px; overflow:hidden; }
.phead { display:flex; align-items:center; gap:24px; }
.phead.second { margin-top:64px; }
.pn { width:54px; height:54px; border-radius:14px; background:rgba(94,224,255,0.12);
  color:#5ee0ff; font-size:32px; display:flex; align-items:center; justify-content:center; flex:none; }
.pt { font-size:46px; font-weight:400; color:#5ee0ff; }
.pen { font-size:24px; letter-spacing:0.25em; color:#6b7590; margin-left:auto; }
.psub { font-size:28px; line-height:1.7; color:#6b7590; margin:18px 0 26px; }
.legend { display:flex; gap:56px; font-size:30px; color:#aab4c8; }
.legend i { display:inline-block; width:36px; height:14px; border-radius:3px; margin-right:14px;
  vertical-align:middle; }
.legend-delta { font-size:26px; color:#6b7590; margin:10px 0 26px; }
.gap-flag { margin-top:32px; font-size:36px; color:#ffc46b; background:rgba(255,196,107,0.07);
  border-left:8px solid #ffc46b; border-radius:0 14px 14px 0; padding:22px 32px;
  display:flex; flex-direction:column; gap:8px; }
.gap-sub { font-size:26px; color:#6b7590; }
.robo-list { display:flex; flex-direction:column; gap:30px; }
.robo { display:flex; align-items:center; gap:28px; font-size:38px; }
.lamp { width:30px; height:30px; border-radius:50%; flex:none; }
.robo.on { color:#e6ecf5; }
.robo.on .lamp { background:#ff7b8a; box-shadow:0 0 26px rgba(255,123,138,0.8); }
.robo.off { color:#4a5573; }
.robo.off .lamp { background:#222c46; }
.robo-label { flex:1; }
.robo-val { color:#aab4c8; font-variant-numeric:tabular-nums; }
.robo-foot { display:flex; align-items:center; gap:26px; margin-top:34px; }
.pill { font-size:30px; padding:12px 30px; border-radius:999px; border:2px solid; }
.pill.bad { color:#ff7b8a; border-color:#ff7b8a; background:rgba(255,123,138,0.08); }
.pill.warn { color:#ffc46b; border-color:#ffc46b; background:rgba(255,196,107,0.08); }
.pill.ok { color:#5ee0ff; border-color:#3a9fc1; background:rgba(94,224,255,0.07); }
.rep { font-size:30px; color:#aab4c8; font-variant-numeric:tabular-nums; }
.kpis { display:grid; grid-template-columns:1fr 1fr; gap:36px; }
.kpi { background:rgba(17,26,48,0.4); border:1px solid rgba(94,224,255,0.14);
  border-radius:20px; padding:38px 40px; }
.kv { font-size:100px; font-weight:200; font-variant-numeric:tabular-nums; line-height:1.1; }
.kv.amber { color:#ffc46b; }
.kv.cyan { color:#5ee0ff; }
.kl { font-size:30px; color:#aab4c8; margin-top:8px; }
.ken { font-size:23px; color:#6b7590; margin-top:6px; }
.chips { display:flex; gap:26px; margin-top:40px; flex-wrap:wrap; }
.chip { font-size:30px; padding:12px 30px; border-radius:999px; border:2px solid;
  font-variant-numeric:tabular-nums; }
.chip.red { color:#ff7b8a; border-color:#ff7b8a; background:rgba(255,123,138,0.07); }
.chip.dim { color:#6b7590; border-color:rgba(107,117,144,0.5); }
.chip.amber { color:#ffc46b; border-color:#ffc46b; background:rgba(255,196,107,0.07); }
.foot { display:flex; align-items:center; gap:22px; font-size:28px; color:#6b7590;
  border-top:1px solid rgba(94,224,255,0.14); padding-top:26px; }
.foot .dot { color:#4a5573; }
"""
