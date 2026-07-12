"""
動画 Part 2（責任トラック）のフレーム生成・純ロジック（LLM非依存・レンダリング非依存）。

decision_ledger.jsonl / attribution.jsonl を step ごとに読み、責任チェーンの按分
（assigned vs legitimate）・scapegoat・Robodebt 4機序・cheap_talk/reconciled_real 推移・
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
    # scapegoat: このstepで scapegoat=True の行の割合＋対象ノード
    sg_rows = [a for a in attribs_step if a.get("scapegoat")]
    scapegoat_rate = (len(sg_rows) / n) if n else 0.0
    sg_nodes = []
    for a in sg_rows:
        for node in (a.get("scapegoat_nodes") or []):
            if node not in sg_nodes:
                sg_nodes.append(node)
    # Robodebt 4機序: このstepの行での作動割合
    robo = {}
    for flag, _label in ROBODEBT_LABELS:
        robo[flag] = (sum(1 for a in attribs_step if (a.get("robodebt") or {}).get(flag)) / n) if n else 0.0
    robo["reproduced_rate"] = (sum(1 for a in attribs_step
                                   if (a.get("robodebt") or {}).get("reproduced")) / n) if n else 0.0
    # サービス空白（decider 削除後）
    service_gap = any(a.get("service_gap") for a in attribs_step) \
        or any(d.get("service_gap") for d in decisions_upto if d.get("step") == step)
    # cheap_talk / reconciled_real の累積率（gap は分母から除外）
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
    """責任チェーンの按分（assigned vs legitimate）を横バーで縦積み。scapegoat は赤で強調。"""
    a, leg = state["assigned"], state["legitimate"]
    sg = set(state["scapegoat_nodes"])
    keys = CHAIN + [R.GAP]
    maxv = max([0.5] + [max(a.get(k, 0), leg.get(k, 0)) for k in keys])
    scale = maxv * 1.12
    row_h, gap_y, pad_l, x0 = 130, 40, 560, 640
    bar_w = WIDTH - x0 - 620
    y = 40
    parts = [f'<svg viewBox="0 0 {WIDTH - 100} {len(keys) * (row_h + gap_y) + 40}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" font-family=\'"Noto Sans JP","Hiragino Sans",sans-serif\'>']
    for k in keys:
        is_gap = (k == R.GAP)
        label = "空白（gap）" if is_gap else NODE_LABELS.get(k, k)
        is_sg = k in sg
        lab_fill = "#ff7b8a" if is_sg else ("#ffc46b" if is_gap else "#e6ecf5")
        parts.append(f'<text x="40" y="{y + 52}" fill="{lab_fill}" font-size="42" '
                     f'font-weight="{600 if is_sg else 400}">{_esc(label)}</text>')
        if is_sg:
            parts.append(f'<text x="40" y="{y + 100}" fill="#ff7b8a" font-size="26">scapegoat</text>')
        for i, (share, color, tag) in enumerate([
                (a.get(k, 0.0), "#ffc46b", "assigned"),
                (leg.get(k, 0.0), "#5ee0ff", "legitimate")]):
            by = y + i * 56
            bw = int(bar_w * min(1.0, share / scale)) if scale else 0
            parts.append(f'<rect x="{x0}" y="{by}" width="{bar_w}" height="44" rx="6" fill="rgba(17,26,48,0.5)"/>')
            parts.append(f'<rect x="{x0}" y="{by}" width="{bw}" height="44" rx="6" fill="{color}" opacity="0.9"/>')
            parts.append(f'<text x="{x0 + bar_w + 20}" y="{by + 34}" fill="#aab4c8" font-size="30">'
                         f'{_esc(tag)} {share * 100:.0f}%</text>')
        y += row_h + gap_y
    parts.append("</svg>")
    return "".join(parts)


def _robodebt_html(state: dict) -> str:
    robo = state["robodebt"]
    items = []
    for flag, label in ROBODEBT_LABELS:
        frac = robo.get(flag, 0.0)
        active = frac >= 0.5
        cls = "on" if active else "off"
        items.append(f'<div class="robo {cls}"><span class="robo-dot"></span>'
                     f'<span class="robo-label">{_esc(label)}</span>'
                     f'<span class="robo-val">{_pct(frac)}</span></div>')
    rep = robo.get("reproduced_rate", 0.0)
    state_txt = "機序が再生（4本作動）" if rep >= 0.5 else "機序は解消寄り"
    return (f'<div class="robo-list">{"".join(items)}</div>'
            f'<div class="robo-state">{_esc(state_txt)}・再生率 {_pct(rep)}</div>')


def render_frame_html(state: dict) -> str:
    """1 step 分の 4K フレーム HTML（自己完結・inline CSS）。"""
    arm = state["arm"]
    arm_jp = {"governed": "統治あり（実効HITL）", "baseline": "統治なし"}.get(arm, arm)
    arm_cls = "gov" if arm == "governed" else "base"
    prog = (state["step"] / state["duration"] * 100) if state["duration"] else 0
    gap_marker = ('<div class="gap-flag">⚠ サービス空白（decider 削除→誰も判定しない）</div>'
                  if state["service_gap"] else "")
    css = _RESP_CSS
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>責任トラック step {state['step']}</title><style>{css}</style></head><body>
<div class="frame">
  <header class="top">
    <div class="brand"><span class="klabel">RESPONSIBILITY TRACK</span>
      <span class="ktitle">誰が責任を負うのか — 按分と空白</span></div>
    <div class="stepbox"><span class="sl">STEP</span>
      <span class="sv">{state['step']:03d}</span><span class="st">/ {state['duration']:03d}</span></div>
    <div class="arm arm-{arm_cls}">{_esc(arm_jp)}</div>
  </header>
  <div class="prog"><div class="prog-fill" style="width:{prog:.0f}%"></div></div>
  <main class="body">
    <section class="panel chain">
      <div class="phead"><span class="pn">A</span><span class="pt">責任チェーンの按分（assigned ⇄ legitimate）</span></div>
      <div class="legend"><span><i style="background:#ffc46b"></i>assigned（割り当てられた責任）</span>
        <span><i style="background:#5ee0ff"></i>legitimate（正当な責任・MHC縮尺）</span></div>
      {_chain_svg(state)}
      {gap_marker}
    </section>
    <section class="panel side">
      <div class="phead"><span class="pn">B</span><span class="pt">Robodebt 4機序</span></div>
      {_robodebt_html(state)}
      <div class="phead" style="margin-top:60px"><span class="pn">C</span><span class="pt">cheap talk と実の折り合い（累積）</span></div>
      <div class="kpis">
        <div class="kpi"><div class="kv" style="color:#ffc46b">{_pct(state['cheap_talk_cum'])}</div>
          <div class="kl">cheap_talk 率（申告True・実False）</div></div>
        <div class="kpi"><div class="kv" style="color:#5ee0ff">{_pct(state['reconciled_cum'])}</div>
          <div class="kl">reconciled_real 率（実の折り合い）</div></div>
      </div>
      <div class="sg-line">scapegoat 率 <b>{_pct(state['scapegoat_rate'])}</b>
        ・正当責任の空白 <b>{state['gap_legitimate'] * 100:.0f}%</b></div>
    </section>
  </main>
  <footer class="foot"><span class="mono">L0 = qwen2.5:14b</span> ・
    責任チェーン＋按分帰属＋Robodebt機序 ・
    <span class="mono">有効≠正当（機序が消えても正当性テストは別）</span></footer>
</div></body></html>"""


_RESP_CSS = """
* { box-sizing:border-box; margin:0; padding:0; }
html,body { width:3840px; height:2160px; overflow:hidden;
  background:
    radial-gradient(ellipse 60% 50% at 35% 0%, #14224a 0%, transparent 55%),
    radial-gradient(ellipse 50% 40% at 75% 100%, #0e1838 0%, transparent 50%), #04060d;
  color:#e6ecf5; font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;
  font-feature-settings:"palt"; }
.mono { font-family:"SF Mono","Menlo",monospace; font-variant-numeric:tabular-nums; }
.frame { width:3840px; height:2160px; display:grid;
  grid-template-rows:200px 12px 1fr 90px; padding:60px 80px; gap:0; }
.top { display:flex; align-items:center; justify-content:space-between; }
.klabel { font-size:30px; letter-spacing:0.4em; color:#5ee0ff; display:block; }
.ktitle { font-size:64px; font-weight:300; color:#e6ecf5; }
.stepbox { display:flex; align-items:baseline; gap:14px; }
.sl { font-size:30px; color:#6b7590; letter-spacing:0.3em; }
.sv { font-size:96px; font-weight:200; color:#5ee0ff; }
.st { font-size:40px; color:#6b7590; }
.arm { font-size:40px; padding:14px 40px; border-radius:999px; font-weight:400; }
.arm-base { color:#ff7b8a; border:2px solid #ff7b8a; }
.arm-gov { color:#5ee0ff; border:2px solid #5ee0ff; }
.prog { height:12px; background:rgba(94,224,255,0.14); border-radius:6px; margin:20px 0; }
.prog-fill { height:100%; background:#5ee0ff; border-radius:6px; }
.body { display:grid; grid-template-columns:1.55fr 1fr; gap:70px; }
.panel { background:rgba(17,26,48,0.55); border:1px solid rgba(94,224,255,0.18);
  border-radius:24px; padding:50px 56px; overflow:hidden; }
.phead { display:flex; align-items:center; gap:22px; margin-bottom:26px; }
.pn { width:56px; height:56px; border-radius:12px; background:rgba(94,224,255,0.14);
  color:#5ee0ff; font-size:34px; display:flex; align-items:center; justify-content:center; }
.pt { font-size:46px; font-weight:400; color:#5ee0ff; }
.legend { display:flex; gap:50px; font-size:32px; color:#6b7590; margin-bottom:14px; }
.legend i { display:inline-block; width:40px; height:16px; border-radius:4px; margin-right:12px;
  vertical-align:middle; }
.gap-flag { margin-top:24px; font-size:38px; color:#ffc46b; border-left:6px solid #ffc46b;
  padding-left:24px; }
.robo-list { display:flex; flex-direction:column; gap:26px; }
.robo { display:flex; align-items:center; gap:26px; font-size:40px; }
.robo-dot { width:34px; height:34px; border-radius:50%; flex:none; }
.robo.on { color:#e6ecf5; }
.robo.on .robo-dot { background:#ff7b8a; box-shadow:0 0 30px #ff7b8a; }
.robo.off { color:#4a5573; }
.robo.off .robo-dot { background:#2a3550; }
.robo-label { flex:1; }
.robo-val { color:#aab4c8; font-variant-numeric:tabular-nums; }
.robo-state { margin-top:30px; font-size:36px; color:#aab4c8; }
.kpis { display:flex; gap:60px; margin-top:10px; }
.kpi { }
.kv { font-size:96px; font-weight:200; }
.kl { font-size:30px; color:#6b7590; }
.sg-line { margin-top:50px; font-size:38px; color:#aab4c8; }
.sg-line b { color:#ff7b8a; }
.foot { display:flex; align-items:center; gap:26px; font-size:30px; color:#6b7590; }
"""
