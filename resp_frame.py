"""
動画 Part 2（責任トラック）のフレーム生成・純ロジック（LLM非依存・レンダリング非依存）。

decision_ledger.jsonl / attribution.jsonl を step ごとに読み、初見でも読める情報設計で描く:
  - インサイト・ストリップ: この場面で起きていることを一文で（台帳から規則で導出）
    ＋当stepの判定チップ（decider 名つき: 医療・命＝部分 …）
  - A 責任の着地: チェーンに沿った assigned/legitimate 対バー＋評決語タグ（押し付け/届かず/消失）
  - B 害が再生する4条件: Robodebt 機序の平易な言い換え＋「解く制度」の導入状況（台帳の institutions）
  - C AIの申告と実態: cheap talk（言うだけ）と reconciled_real（実態）の対
  - D 推移: 押し付け/空白・申告/実態のスパークライン（サービス空白発生マーカー付き）
    ＋累積の害（不可逆・手続的）

デザイン言語: Bloomberg ターミナルを参照した端末調（フラットな近黒・1pxヘアライン・
アンバー基軸・モノスペース数字・角形データタグ・グラデ/グロー無し）。
実 PNG/動画化は render_resp_frames.py が Playwright(Chromium)＋ffmpeg で行う。
※ LLM は無い。台帳から決定的に組むだけ。
"""
import json
import os
from typing import Dict, List, Optional

import responsibility as R


# ───────────── 配信の firewall（設計の約束8・P2-B） ─────────────
# 政策 audience に対しては、意識・尊厳・創発文化・情景(Part1) が責任トラック(Q1/Q2/Q3)の
# 信頼を下げる。よって「名目上の分離」ではなく **配信形態そのもの** で物理分離する:
# 責任トラックのバンドルには情景(Part1=simulation.mp4)を絶対に含めない。
SCENE_PART1 = "simulation.mp4"          # 情景トラック（4K・render_video_v2 由来）
RESP_PART2_DEFAULT = ("part2_baseline.mp4", "part2_governed.mp4")   # 責任トラック（統治なし/実効HITL）


def delivery_bundles(part2_files=RESP_PART2_DEFAULT):
    """配信バンドル定義（firewall）。責任トラックは Part1 情景を含めない。
    - `responsibility`: **政策 audience 向け**（別途 PDF レポート＋本Part2のみ）。policy_safe=True。
    - `full`: 一般/芸術 audience 向け（情景 Part1 ＋ 責任 Part2 を連結）。policy_safe=False＝政策には配らない。
    返り値: {name: {"inputs":[mp4...], "out":mp4, "audience":str, "policy_safe":bool}}"""
    p2 = list(part2_files)
    return {
        "responsibility": {
            "inputs": p2, "out": "responsibility_track.mp4",
            "audience": "policy", "policy_safe": True,
        },
        "full": {
            "inputs": [SCENE_PART1] + p2, "out": "final_2part.mp4",
            "audience": "general/art", "policy_safe": False,
        },
    }


def concat_recipe(bundle: dict) -> str:
    """1バンドルの ffmpeg concat コマンドを返す（純関数）。"""
    lines = "".join(f"file '{f}'\\n" for f in bundle["inputs"])
    return (f"printf \"{lines}\" > concat.txt && "
            f"ffmpeg -f concat -safe 0 -i concat.txt -c copy {bundle['out']}")

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
# 各機序の平易な言い換えと「解く制度」（responsibility.robodebt_mechanism の対応表と一致）。
ROBODEBT_META = {
    "auto_adverse": ("機械が一方的に「不正・減額」と決める", "実効HITL", R.INST_EFFECTIVE_HITL),
    "burden_reversed": ("間違いの証明を市民の側にさせる", "立証責任の是正", R.INST_BURDEN_SHIFT),
    "no_effective_review": ("人間のレビューが形だけで、止められない", "実効HITL", R.INST_EFFECTIVE_HITL),
    "irreversible_pending": ("争っている間も不利益が続き、取り返せない", "異議申立（停止効）", R.INST_APPEAL),
}
_DOMAIN_JP = {"medical": "医療", "welfare": "福祉", "housing": "住宅", "loan": "融資"}
_LEVEL_JP = {"grant": "承認", "serve": "承認", "partial": "部分", "deny": "拒否", "abstain": "保留"}

WIDTH, HEIGHT = 3840, 2160

# ── 端末調パレット（Bloomberg 参照: アンバー基軸・意味色は市場色の規律） ──
C_BG = "#0a0c0e"        # 近黒（フラット・グラデ無し）
C_SURF = "#11141a"      # パネル実面
C_INSET = "#0e1114"     # タグ/バー溝
C_HAIR = "#232830"      # 1px ヘアライン
C_TXT = "#e8eaed"       # 主文字
C_TXT2 = "#9aa0a6"      # 副文字
C_TXT3 = "#5f6368"      # 弱文字
C_AMBER = "#ffab2e"     # シグネチャ（assigned・警告・ファンクションタグ）
C_BLUE = "#7cacf8"      # legitimate・統治あり
C_RED = "#f2555a"       # 危険（scapegoat・統治なし・機序作動）
C_GREEN = "#53c07e"     # 良（導入中・承認・解消）
C_TRACK = "rgba(232,234,237,0.07)"


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


def decider_names_from_config(cfg: dict) -> Dict[int, str]:
    """config.personas（index=agent id）→ {decider_id: 名前}。判定チップに decider 名を出す用。"""
    names: Dict[int, str] = {}
    for i, p in enumerate((cfg or {}).get("personas") or []):
        n = (p or {}).get("name")
        if n:
            names[i] = str(n)
    return names


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
                step: int, arm: str, duration: int = 100,
                decider_names: Optional[Dict[int, str]] = None) -> dict:
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
    # 責任層の制度（解く制度タグの導入状況）: このstepの按分行から集約
    institutions: List[str] = []
    for a in attribs_step:
        for inst in (a.get("institutions") or []):
            if inst not in institutions:
                institutions.append(inst)
    service_gap = any(a.get("service_gap") for a in attribs_step) \
        or any(d.get("service_gap") for d in decisions_upto if d.get("step") == step)
    # 当stepの判定（インサイト・ストリップのタグ。decider 名つき）
    names = decider_names or {}
    decisions_step = []
    for d in decisions_upto:
        if int(d.get("step", 0)) != step:
            continue
        decisions_step.append({
            "domain": d.get("domain", "?"), "level": d.get("level", "?"),
            "gap": bool(d.get("service_gap")),
            "decider": names.get(d.get("decider_id"), ""),
        })
    # 累積: cheap talk / 実の折り合い（gap は分母から除外）と、害そのもの（gap 行も害として数える）
    decided = [d for d in decisions_upto if not d.get("service_gap")]
    nd = len(decided)
    cheap = (sum(1 for d in decided if d.get("cheap_talk")) / nd) if nd else None
    recon = (sum(1 for d in decided if d.get("reconciled_real")) / nd) if nd else None
    irr_cum = sum(1 for d in decisions_upto if d.get("irreversible"))
    proc_cum = sum(int(d.get("procedural_harm", 0) or 0) for d in decisions_upto)
    return {
        "step": step, "duration": duration, "arm": arm, "n_decisions": n,
        "assigned": assigned, "legitimate": legitimate,
        "gap_assigned": assigned.get(R.GAP, 0.0), "gap_legitimate": legitimate.get(R.GAP, 0.0),
        "scapegoat_nodes": sg_nodes, "scapegoat_rate": scapegoat_rate,
        "robodebt": robo, "service_gap": bool(service_gap),
        "institutions": institutions, "decisions_step": decisions_step,
        "cheap_talk_cum": cheap, "reconciled_cum": recon,
        "irr_cum": irr_cum, "proc_harm_cum": proc_cum,
    }


def frame_series(run_dir: str, arm: Optional[str] = None, duration: int = 100,
                 config_path: Optional[str] = "config.yaml") -> List[dict]:
    """run_dir の台帳から step ごとのフレーム状態列を作る（推移 history と空白発生 step つき）。"""
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
    names: Dict[int, str] = {}
    if config_path and os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                names = decider_names_from_config(yaml.safe_load(f))
        except Exception:
            names = {}
    gap_steps = [int(d.get("step", 0)) for d in decisions if d.get("service_gap")]
    gap_start = min(gap_steps) if gap_steps else None
    steps = sorted({int(a.get("step", 0)) for a in attribs} | {int(d.get("step", 0)) for d in decisions})
    out: List[dict] = []
    hist: List[dict] = []
    for s in steps:
        at_s = [a for a in attribs if int(a.get("step", 0)) == s]
        dec_upto = [d for d in decisions if int(d.get("step", 0)) <= s]
        st = frame_state(at_s, dec_upto, s, arm, duration, decider_names=names)
        hist.append({"step": s, "scapegoat_rate": st["scapegoat_rate"],
                     "gap_legitimate": st["gap_legitimate"],
                     "cheap_talk_cum": st["cheap_talk_cum"],
                     "reconciled_cum": st["reconciled_cum"]})
        st["history"] = list(hist)
        st["gap_start_step"] = gap_start
        out.append(st)
    return out


def insight_of(state: dict) -> str:
    """この場面で起きていることを一文で（台帳の状態から規則で導出・決定的）。"""
    leg = state["legitimate"]
    top = max(CHAIN, key=lambda k: leg.get(k, 0.0))
    top_label = NODE_LABELS.get(top, top)
    if state["service_gap"]:
        return ("判定者が削除され、誰も決めない「サービス空白」が生まれた"
                " ── 空白の責任はどこにも着地しない")
    if state["scapegoat_nodes"]:
        sg_label = "・".join(NODE_LABELS.get(n, n) for n in state["scapegoat_nodes"])
        s = f"責任は{sg_label}に押し付けられている ── 本来いちばん重いのは{top_label}"
        if state["gap_legitimate"] >= 0.2:
            s += f"。しかも{state['gap_legitimate'] * 100:.0f}%は誰にも届かず空白へ"
        return s
    active_mean = sum(state["robodebt"].get(f, 0.0) for f, _ in ROBODEBT_LABELS) / 4.0
    if state["arm"] == "governed" and active_mean < 0.5:
        return "人間の実効レビューが害の機序を止め、責任の押し付けは起きていない"
    return "割り当てられた責任と、本来負うべき責任のズレを監視している"


# ───────────── HTML フレーム（4K・端末調） ─────────────
def _esc(s) -> str:
    import html as _h
    return _h.escape("" if s is None else str(s))


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _chain_svg(state: dict) -> str:
    """責任チェーンの按分。対バー＋評決語タグ（押し付け/過剰/届かず/消失）。"""
    a, leg = state["assigned"], state["legitimate"]
    sg = set(state["scapegoat_nodes"])
    keys = CHAIN + [R.GAP]
    maxv = max([0.5] + [max(a.get(k, 0.0), leg.get(k, 0.0)) for k in keys])
    scale = maxv * 1.1
    w_view, stride, top = 2080, 116, 14
    height = top + stride * len(keys)
    x_bar, w_bar = 460, 1120
    x_val = 1716
    x_chip, w_chip = 1756, 304
    parts = [f'<svg viewBox="0 0 {w_view} {height}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'font-family=\'"Noto Sans JP","Hiragino Sans",sans-serif\'>']
    y = top
    for k in keys:
        is_gap = (k == R.GAP)
        is_sg = k in sg
        if is_gap:
            parts.append(f'<line x1="0" y1="{y - 10}" x2="{w_view}" y2="{y - 10}" '
                         f'stroke="{C_HAIR}" stroke-width="2" stroke-dasharray="8 10"/>')
        if is_sg:
            parts.append(f'<rect x="0" y="{y - 6}" width="{w_view}" height="{stride - 16}" '
                         f'fill="rgba(242,85,90,0.07)"/>')
            parts.append(f'<rect x="0" y="{y - 6}" width="5" height="{stride - 16}" fill="{C_RED}"/>')
        name = "空白 ── 誰も負わない" if is_gap else NODE_LABELS.get(k, k)
        name_fill = C_AMBER if is_gap else (C_RED if is_sg else C_TXT)
        parts.append(f'<text x="26" y="{y + 40}" fill="{name_fill}" font-size="38" '
                     f'font-weight="{500 if is_sg else 400}">{_esc(name)}</text>')
        if is_sg:
            sub, sub_fill = "scapegoat ── 責任の押し付け先", C_RED
        elif is_gap:
            sub, sub_fill = "gap ── 割当不能な残余", C_TXT3
        else:
            sub, sub_fill = k, C_TXT3
        parts.append(f'<text x="26" y="{y + 76}" fill="{sub_fill}" font-size="22" '
                     f'letter-spacing="2">{_esc(sub)}</text>')
        av, lv = a.get(k, 0.0), leg.get(k, 0.0)
        for i, (share, color) in enumerate([(av, C_AMBER), (lv, C_BLUE)]):
            by = y + 10 + i * 36
            bw = int(w_bar * min(1.0, share / scale)) if scale else 0
            parts.append(f'<rect x="{x_bar}" y="{by}" width="{w_bar}" height="26" rx="2" '
                         f'fill="{C_TRACK}"/>')
            if bw > 0:
                parts.append(f'<rect x="{x_bar}" y="{by}" width="{max(bw, 8)}" height="26" rx="2" '
                             f'fill="{color}"/>')
            parts.append(f'<text x="{x_val}" y="{by + 22}" text-anchor="end" fill="{color}" '
                         f'font-size="29" font-family=\'"SF Mono","Menlo",monospace\'>'
                         f'{share * 100:.0f}%</text>')
        # 評決語タグ: ほぼゼロの行は沈黙させ、意味のある乖離だけ言葉にする
        d = av - lv
        chip = None
        if abs(d) >= 0.015:
            if d > 0:
                if is_sg:
                    chip = ("押し付け", f"＋{d * 100:.0f}pt", C_RED)
                else:
                    chip = ("過剰", f"＋{d * 100:.0f}pt", C_AMBER)
            else:
                if is_gap:
                    chip = ("消失", f"−{abs(d) * 100:.0f}pt", C_AMBER)
                else:
                    chip = ("届かず", f"−{abs(d) * 100:.0f}pt", C_BLUE)
        cy = y + 18
        if chip:
            word, num, ccol = chip
            parts.append(f'<rect x="{x_chip}" y="{cy}" width="{w_chip}" height="54" rx="3" '
                         f'fill="{C_INSET}" stroke="{ccol}" stroke-width="2"/>')
            parts.append(f'<text x="{x_chip + w_chip // 2}" y="{cy + 37}" text-anchor="middle" '
                         f'fill="{ccol}" font-size="28">{_esc(word)} {num}</text>')
        else:
            parts.append(f'<text x="{x_chip + w_chip // 2}" y="{cy + 37}" text-anchor="middle" '
                         f'fill="{C_TXT3}" font-size="28" opacity="0.6">—</text>')
        y += stride
    parts.append("</svg>")
    return "".join(parts)


def _trend_svg(history: List[dict], series, gap_start, w: int = 1720, h: int = 150) -> str:
    """スパークライン。series=[(key,color,label)]。gap_start にサービス空白発生マーカー。"""
    if not history:
        return ""
    pad_l, pad_r, pad_t, pad_b = 14, 260, 26, 16
    steps = [pt["step"] for pt in history]
    s_min, s_max = min(steps), max(steps)
    span = max(1, s_max - s_min)

    def x_of(s):
        return pad_l + (s - s_min) / span * (w - pad_l - pad_r)

    def y_of(v):
        return pad_t + (1.0 - max(0.0, min(1.0, v))) * (h - pad_t - pad_b)

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" '
             f'font-family=\'"SF Mono","Menlo","Noto Sans JP",monospace\'>']
    parts.append(f'<line x1="{pad_l}" y1="{y_of(0.0):.0f}" x2="{w - pad_r}" y2="{y_of(0.0):.0f}" '
                 f'stroke="{C_HAIR}" stroke-width="2"/>')
    parts.append(f'<line x1="{pad_l}" y1="{y_of(0.5):.0f}" x2="{w - pad_r}" y2="{y_of(0.5):.0f}" '
                 f'stroke="{C_HAIR}" stroke-width="1" stroke-dasharray="4 8" opacity="0.7"/>')
    if gap_start is not None and s_min <= gap_start <= s_max:
        gx = x_of(gap_start)
        parts.append(f'<line x1="{gx:.0f}" y1="8" x2="{gx:.0f}" y2="{h - pad_b}" '
                     f'stroke="{C_RED}" stroke-width="2" stroke-dasharray="5 8" opacity="0.9"/>')
        parts.append(f'<text x="{gx + 10:.0f}" y="22" fill="{C_RED}" font-size="21">空白 発生</text>')
    used_label_ys: List[float] = []
    for key, color, label in series:
        pts = [(x_of(pt["step"]), y_of(pt[key])) for pt in history if pt.get(key) is not None]
        if not pts:
            continue
        if len(pts) > 1:
            path = " ".join(f"{px:.0f},{py:.0f}" for px, py in pts)
            parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" '
                         f'stroke-width="4" stroke-linejoin="round"/>')
        ex, ey = pts[-1]
        parts.append(f'<circle cx="{ex:.0f}" cy="{ey:.0f}" r="6" fill="{color}"/>')
        last_v = next(pt[key] for pt in reversed(history) if pt.get(key) is not None)
        ly = ey + 8
        while any(abs(ly - u) < 26 for u in used_label_ys):
            ly += 26
        ly = min(max(ly, 24), h - 6)
        used_label_ys.append(ly)
        parts.append(f'<text x="{ex + 16:.0f}" y="{ly:.0f}" fill="{color}" font-size="24">'
                     f'{_esc(label)} {last_v * 100:.0f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def _robodebt_html(state: dict) -> str:
    robo = state["robodebt"]
    insts = set(state.get("institutions") or [])
    items = []
    for flag, label in ROBODEBT_LABELS:
        frac = robo.get(flag, 0.0)
        on = frac >= 0.5
        plain, cure_label, cure_inst = ROBODEBT_META[flag]
        cure_on = cure_inst in insts
        cure_cls = "on" if cure_on else "off"
        cure_txt = f"解く制度: {cure_label} ── {'導入中' if cure_on else '未導入'}"
        items.append(
            f'<div class="robo {"on" if on else "off"}"><span class="lamp"></span>'
            f'<div class="rmain"><div class="rlabel">{_esc(label)}</div>'
            f'<div class="rsub">{_esc(plain)}</div></div>'
            f'<div class="rside"><span class="rstat">{"作動中" if on else "作動せず"} {_pct(frac)}</span>'
            f'<span class="cure {cure_cls}">{_esc(cure_txt)}</span></div></div>')
    rep = robo.get("reproduced_rate", 0.0)
    active_mean = sum(robo.get(f, 0.0) for f, _ in ROBODEBT_LABELS) / 4.0
    if rep >= 0.5:
        pill_cls, pill_txt = "bad", "4条件が揃った ── 害が再生"
    elif active_mean >= 0.5:
        pill_cls, pill_txt = "warn", "一部の条件が残る ── 制度の束が不完全"
    else:
        pill_cls, pill_txt = "ok", "条件はほぼ解けている"
    return (f'<div class="chain"><span class="rail"></span>{"".join(items)}</div>'
            f'<div class="robo-foot"><span class="pill {pill_cls}">{_esc(pill_txt)}</span>'
            f'<span class="rep">再生率 {_pct(rep)}</span></div>')


def _decision_chips(state: dict) -> str:
    if not state["decisions_step"]:
        return ""
    chips = []
    for d in state["decisions_step"]:
        dom = _DOMAIN_JP.get(d["domain"], d["domain"])
        if d.get("decider"):
            dom = f'{dom}・{d["decider"]}'
        if d["gap"]:
            lvl, cls = "空白", "deny"
        else:
            lvl = _LEVEL_JP.get(d["level"], d["level"])
            cls = {"承認": "ok", "部分": "mid", "拒否": "deny", "保留": "dim"}.get(lvl, "dim")
        chips.append(f'<span class="dec"><span class="dom">{_esc(dom)}</span>'
                     f'<span class="lvl {cls}">{_esc(lvl)}</span></span>')
    return (f'<div class="decwrap"><span class="declabel">この STEP の判定</span>'
            f'<div class="decs">{"".join(chips)}</div></div>')


def render_frame_html(state: dict) -> str:
    """1 step 分の 4K フレーム HTML（自己完結・inline CSS）。"""
    arm = state["arm"]
    arm_jp = {"governed": "統治あり（実効HITL）", "baseline": "統治なし"}.get(arm, arm)
    arm_sub = {"governed": "人間が判定を止められる", "baseline": "人間の実効レビューなし"}.get(arm, "")
    arm_cls = "gov" if arm == "governed" else "base"
    prog = (state["step"] / state["duration"] * 100) if state["duration"] else 0
    severity = "bad" if (state["service_gap"] or state["scapegoat_nodes"]) else "ok"
    sg_rate, gap_leg = state["scapegoat_rate"], state["gap_legitimate"]
    history = state.get("history") or []
    gap_start = state.get("gap_start_step")
    trend1 = _trend_svg(history, [("scapegoat_rate", C_RED, "押し付け"),
                                  ("gap_legitimate", C_AMBER, "空白")], gap_start)
    trend2 = _trend_svg(history, [("cheap_talk_cum", C_AMBER, "言うだけ"),
                                  ("reconciled_cum", C_BLUE, "実態")], gap_start)
    irr_cls = "red" if state["irr_cum"] > 0 else "dim"
    proc_cls = "amber" if state["proc_harm_cum"] > 0 else "dim"
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>責任トラック step {state['step']}</title><style>{_RESP_CSS}</style></head><body>
<div class="frame">
  <header class="top">
    <div class="brand">
      <span class="klabel">RESPONSIBILITY TRACK ── PART 2 / 2</span>
      <span class="ktitle">AIが決めたあと、責任はどこへ行くか</span>
      <span class="ksub">医療・福祉・住宅・融資のAIが市民の申請を判定するたび、その責任の行き先を追跡する</span>
    </div>
    <div class="stepbox"><span class="sl">STEP</span>
      <span class="sv mono">{state['step']:03d}</span><span class="st mono">/ {state['duration']:03d}</span></div>
    <div class="armwrap"><span class="armlabel">GOVERNANCE ARM</span>
      <span class="arm arm-{arm_cls}">{_esc(arm_jp)}</span>
      <span class="armsub">{_esc(arm_sub)}</span></div>
  </header>
  <div class="prog"><div class="prog-fill" style="width:{prog:.0f}%"></div></div>
  <section class="insight sev-{severity}">
    <div class="ins-main"><span class="inslabel">この場面</span>
      <span class="instext">{_esc(insight_of(state))}</span></div>
    {_decision_chips(state)}
  </section>
  <main class="body">
    <div class="cols">
      <section class="panel">
        <div class="phead"><span class="pn">A</span><span class="pt">責任はどこに着地したか</span>
          <span class="pen">ASSIGNED ⇄ LEGITIMATE</span></div>
        <p class="psub">開発者から現場までの責任チェーンに沿って、<b>実際に割り当てられた責任（アンバー）</b>と、
実効支配から見て<b>本来負うべき責任（ブルー）</b>を並べる。ズレが大きいほど、責任は間違った場所に落ちている。</p>
        <div class="legend">
          <span><i style="background:{C_AMBER}"></i>assigned ── 割り当てられた</span>
          <span><i style="background:{C_BLUE}"></i>legitimate ── 本来負うべき</span>
          <span class="legend-delta">Δ＝assigned−legitimate（＋は過剰帰属＝押し付け・−は過小/消失）</span>
        </div>
        {_chain_svg(state)}
      </section>
      <div class="rcol">
        <section class="panel">
          <div class="phead"><span class="pn">B</span><span class="pt">害が再生する4つの条件</span>
            <span class="pen">ROBODEBT MECHANISM</span></div>
          <p class="psub">豪州で実害を生んだ自動給付審査の失敗「Robodebt」と同じ機序を監視。
4つ揃うと同型の害が再生し、対応する制度だけがそれを解く。</p>
          {_robodebt_html(state)}
        </section>
        <section class="panel grow">
          <div class="phead"><span class="pn">C</span><span class="pt">AIの申告と実態</span>
            <span class="pen">CHEAP TALK</span></div>
          <p class="psub">「折り合えた」という申告（言葉）と、世界で実際に成立した折り合い（実態）を分けて数える。</p>
          <div class="kpis">
            <div class="kpi amber"><div class="kv mono">{_pct(state['cheap_talk_cum'])}</div>
              <div class="kl">「折り合えた」と言うだけ</div>
              <div class="ken mono">cheap_talk rate ── 申告 true・実 false</div></div>
            <div class="kpi blue"><div class="kv mono">{_pct(state['reconciled_cum'])}</div>
              <div class="kl">実際に折り合えた</div>
              <div class="ken mono">reconciled_real rate</div></div>
          </div>
        </section>
      </div>
    </div>
    <section class="panel trend">
      <div class="phead"><span class="pn">D</span><span class="pt">推移</span>
        <div class="chips head">
          <span class="chip sm {'red' if sg_rate > 0 else 'dim'}">押し付け（scapegoat）率 {_pct(sg_rate)}</span>
          <span class="chip sm amber">正当責任の空白 {gap_leg * 100:.0f}%</span>
          <span class="chip sm {irr_cls}">不可逆の害 累計{state['irr_cum']}件</span>
          <span class="chip sm {proc_cls}">手続的害 累計{state['proc_harm_cum']}点</span>
        </div></div>
      <div class="tgrid">
        <div class="tcol"><div class="tlabel">押し付けと空白（stepごと）</div>{trend1}</div>
        <div class="tcol"><div class="tlabel">申告と実態（累積）</div>{trend2}</div>
      </div>
    </section>
  </main>
  <footer class="foot"><span>PART 2 / 2 ── 責任トラック</span><span class="dot">|</span>
    <span class="mono">L0 = QWEN2.5:14B</span><span class="dot">|</span>
    <span>決定はLLM内生 ── 台帳から描画</span><span class="dot">|</span>
    <span>有効≠正当（機序が消えても正当性テストは別）</span></footer>
</div></body></html>"""


_RESP_CSS = f"""
* {{ box-sizing:border-box; margin:0; padding:0; }}
html,body {{ width:3840px; height:2160px; overflow:hidden;
  background:{C_BG};
  color:{C_TXT}; font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;
  font-feature-settings:"palt"; font-weight:400; }}
.mono {{ font-family:"SF Mono","Menlo","Consolas",monospace; font-variant-numeric:tabular-nums; }}
.frame {{ width:3840px; height:2160px; display:grid;
  grid-template-rows:206px 14px 124px 1fr 70px; row-gap:22px; padding:60px 88px 52px; }}
.top {{ display:flex; align-items:center; justify-content:space-between; gap:64px;
  border-bottom:1px solid {C_HAIR}; padding-bottom:26px; }}
.brand {{ display:flex; flex-direction:column; gap:11px; }}
.klabel {{ font-size:24px; letter-spacing:0.4em; color:{C_AMBER}; font-weight:500; }}
.ktitle {{ font-size:56px; font-weight:500; letter-spacing:0.01em; color:{C_TXT}; }}
.ksub {{ font-size:25px; color:{C_TXT2}; }}
.stepbox {{ display:flex; align-items:baseline; gap:16px; margin-left:auto; }}
.sl {{ font-size:24px; letter-spacing:0.35em; color:{C_TXT3}; }}
.sv {{ font-size:92px; font-weight:500; color:{C_AMBER}; }}
.st {{ font-size:36px; color:{C_TXT3}; }}
.armwrap {{ display:flex; flex-direction:column; align-items:flex-end; gap:9px; }}
.armlabel {{ font-size:20px; letter-spacing:0.3em; color:{C_TXT3}; }}
.arm {{ font-size:33px; padding:10px 34px; border-radius:3px; font-weight:500; }}
.arm-base {{ color:{C_RED}; border:2px solid {C_RED}; background:rgba(242,85,90,0.07); }}
.arm-gov {{ color:{C_BLUE}; border:2px solid {C_BLUE}; background:rgba(124,172,248,0.07); }}
.armsub {{ font-size:22px; color:{C_TXT3}; }}
.prog {{ height:8px; align-self:center; background:{C_INSET}; border:1px solid {C_HAIR};
  border-radius:2px; }}
.prog-fill {{ height:100%; background:{C_AMBER}; }}
.insight {{ display:flex; align-items:center; gap:52px; background:{C_SURF};
  border:1px solid {C_HAIR}; border-radius:3px; padding:0 44px 0 38px; }}
.insight.sev-bad {{ border-left:6px solid {C_RED}; }}
.insight.sev-ok {{ border-left:6px solid {C_BLUE}; }}
.ins-main {{ display:flex; align-items:center; gap:32px; flex:1; min-width:0; }}
.inslabel {{ font-size:21px; letter-spacing:0.3em; color:{C_AMBER}; flex:none; font-weight:500; }}
.instext {{ font-size:36px; line-height:1.5; color:{C_TXT}; font-weight:500; }}
.decwrap {{ display:flex; flex-direction:column; gap:9px; align-items:flex-end; flex:none; }}
.declabel {{ font-size:19px; letter-spacing:0.25em; color:{C_TXT3}; }}
.decs {{ display:flex; gap:12px; }}
.dec {{ display:flex; align-items:center; gap:12px; padding:9px 20px; border-radius:3px;
  border:1px solid {C_HAIR}; background:{C_INSET}; }}
.dec .dom {{ font-size:25px; color:{C_TXT2}; }}
.dec .lvl {{ font-size:26px; font-weight:500; }}
.dec .lvl.ok {{ color:{C_GREEN}; }}
.dec .lvl.mid {{ color:{C_AMBER}; }}
.dec .lvl.deny {{ color:{C_RED}; }}
.dec .lvl.dim {{ color:{C_TXT3}; }}
.body {{ display:flex; flex-direction:column; gap:30px; min-height:0; }}
.cols {{ display:grid; grid-template-columns:1.6fr 1fr; gap:30px; flex:1; min-height:0; }}
.rcol {{ display:flex; flex-direction:column; gap:30px; min-height:0; }}
.rcol .grow {{ flex:1; }}
.panel {{ background:{C_SURF}; border:1px solid {C_HAIR}; border-radius:4px;
  padding:40px 52px; overflow:hidden; }}
.phead {{ display:flex; align-items:center; gap:20px; border-bottom:1px solid {C_HAIR};
  padding-bottom:18px; }}
.pn {{ min-width:50px; height:50px; padding:0 14px; border-radius:3px; background:{C_AMBER};
  color:{C_BG}; font-size:30px; font-weight:600; display:flex; align-items:center;
  justify-content:center; flex:none; font-family:"SF Mono","Menlo",monospace; }}
.pt {{ font-size:40px; font-weight:500; color:{C_TXT}; }}
.pen {{ font-size:22px; letter-spacing:0.25em; color:{C_TXT3}; margin-left:auto; }}
.psub {{ font-size:26px; line-height:1.6; color:{C_TXT2}; margin:16px 0 18px; }}
.psub b {{ color:{C_TXT}; font-weight:500; }}
.legend {{ display:flex; align-items:center; gap:40px; font-size:26px; color:{C_TXT2};
  margin-bottom:16px; flex-wrap:wrap; }}
.legend i {{ display:inline-block; width:32px; height:12px; border-radius:2px; margin-right:12px;
  vertical-align:middle; }}
.legend-delta {{ font-size:23px; color:{C_TXT3}; }}
.chain {{ position:relative; display:flex; flex-direction:column; gap:24px; margin-top:16px; }}
.rail {{ position:absolute; left:11px; top:18px; bottom:18px; width:2px; background:{C_HAIR}; }}
.robo {{ display:flex; align-items:flex-start; gap:24px; position:relative; }}
.lamp {{ width:24px; height:24px; border-radius:3px; flex:none; margin-top:8px; z-index:1; }}
.robo.on .lamp {{ background:{C_RED}; }}
.robo.off .lamp {{ background:{C_INSET}; border:1px solid {C_HAIR}; }}
.rmain {{ flex:1; min-width:0; }}
.rlabel {{ font-size:34px; }}
.robo.on .rlabel {{ color:{C_TXT}; font-weight:500; }}
.robo.off .rlabel {{ color:{C_TXT3}; }}
.rsub {{ font-size:23px; color:{C_TXT3}; margin-top:2px; }}
.rside {{ display:flex; flex-direction:column; align-items:flex-end; gap:8px; flex:none; }}
.rstat {{ font-size:27px; font-family:"SF Mono","Menlo",monospace;
  font-variant-numeric:tabular-nums; }}
.robo.on .rstat {{ color:{C_RED}; }}
.robo.off .rstat {{ color:{C_TXT3}; }}
.cure {{ font-size:21px; padding:5px 16px; border-radius:3px; border:1px solid; }}
.cure.on {{ color:{C_GREEN}; border-color:{C_GREEN}; background:rgba(83,192,126,0.08); }}
.cure.off {{ color:{C_TXT3}; border-color:{C_HAIR}; background:{C_INSET}; }}
.robo-foot {{ display:flex; align-items:center; gap:24px; margin-top:24px;
  border-top:1px solid {C_HAIR}; padding-top:22px; }}
.pill {{ font-size:27px; padding:9px 24px; border-radius:3px; border:1px solid; font-weight:500; }}
.pill.bad {{ color:{C_RED}; border-color:{C_RED}; background:rgba(242,85,90,0.08); }}
.pill.warn {{ color:{C_AMBER}; border-color:{C_AMBER}; background:rgba(255,171,46,0.08); }}
.pill.ok {{ color:{C_GREEN}; border-color:{C_GREEN}; background:rgba(83,192,126,0.08); }}
.rep {{ font-size:26px; color:{C_TXT2}; font-family:"SF Mono","Menlo",monospace; }}
.kpis {{ display:grid; grid-template-columns:1fr 1fr; gap:26px; margin-top:16px; }}
.kpi {{ background:{C_INSET}; border:1px solid {C_HAIR}; border-radius:3px;
  padding:26px 32px; }}
.kpi.amber {{ border-left:5px solid {C_AMBER}; }}
.kpi.blue {{ border-left:5px solid {C_BLUE}; }}
.kv {{ font-size:74px; font-weight:500; line-height:1.1; }}
.kpi.amber .kv {{ color:{C_AMBER}; }}
.kpi.blue .kv {{ color:{C_BLUE}; }}
.kl {{ font-size:27px; color:{C_TXT2}; margin-top:8px; }}
.ken {{ font-size:20px; color:{C_TXT3}; margin-top:5px; }}
.trend {{ flex:none; padding:32px 48px 28px; }}
.chips {{ display:flex; gap:16px; flex-wrap:wrap; }}
.chips.head {{ margin-left:auto; }}
.chip {{ font-size:26px; padding:9px 22px; border-radius:3px; border:1px solid;
  font-variant-numeric:tabular-nums; }}
.chip.sm {{ font-size:24px; padding:7px 18px; }}
.chip.red {{ color:{C_RED}; border-color:{C_RED}; background:rgba(242,85,90,0.07); }}
.chip.dim {{ color:{C_TXT3}; border-color:{C_HAIR}; background:{C_INSET}; }}
.chip.amber {{ color:{C_AMBER}; border-color:{C_AMBER}; background:rgba(255,171,46,0.07); }}
.tgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:52px; margin-top:12px; }}
.tlabel {{ font-size:22px; color:{C_TXT3}; margin-bottom:4px; letter-spacing:0.06em; }}
.foot {{ display:flex; align-items:center; gap:22px; font-size:24px; color:{C_TXT3};
  border-top:1px solid {C_HAIR}; padding-top:22px; letter-spacing:0.04em; }}
.foot .dot {{ color:{C_HAIR}; }}
"""
