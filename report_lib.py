"""
成果物レポート生成の純ロジック（LLM非依存・レンダリング非依存）。

run ディレクトリの指標（analyze_compare.analyze）・run_meta・findings/value_provenance を集め、
baseline/governed の A/B 比較を含む A4 印刷用 HTML を組み立てる。実 PDF 化は report_build.py が
Playwright(Chromium) で行う。フォントは Noto Sans JP を第一候補にした font-family チェーン＋
（任意で）@font-face 埋め込み（フォントパスは設定可能）。中国語字形を避ける意図は JP 変種で担保。

※ ここに LLM は無い。指標・台帳から決定的に HTML を組むだけ（結論は書き込まない）。
"""
import html as _html
import json
import os
from typing import Dict, List, Optional

import analyze_compare as ac

# 既存 report.html / viz_templates/frame_v2.css と統一（deep space + cyan）。
DESIGN_CSS_VARS = """
  --bg-deep:#04060d; --bg-base:#07091a; --bg-elevated:#111a30;
  --bg-card:rgba(17,26,48,0.65); --bg-card-soft:rgba(17,26,48,0.35);
  --accent-cyan:#5ee0ff; --accent-cyan-soft:#3a9fc1; --accent-violet:#a78bfa;
  --accent-amber:#ffc46b; --danger:#ff7b8a; --ok:#8de2b9;
  --text-primary:#e6ecf5; --text-secondary:#aab4c8; --text-tertiary:#6b7590; --text-faint:#4a5573;
  --line-soft:rgba(94,224,255,0.18); --line-strong:rgba(94,224,255,0.42);
"""

# 和文フォント chain（Noto Sans JP を第一に。@font-face があればそれが最優先で解決される）。
FONT_STACK = '"Noto Sans JP","Hiragino Sans","Yu Gothic","Hiragino Kaku Gothic ProN",sans-serif'

# A/B で見せる指標（analyze() の返り値キー）。(ラベル, キー, 種別, 「低いほど良い」か)
RATE_ROWS = [
    ("cheap_talk率（申告True・実False）", "cheap_talk_rate", "rate", True),
    ("reconciled（実の折り合い）率", "reconciled_real_rate", "rate", False),
    ("grant（全面供給）率", "grant_rate", "rate", False),
    ("scapegoat率（現場へ責任集中）", "scapegoat_rate", "rate", True),
    ("正当責任の空白（gap平均）", "gap_legit_mean", "rate", True),
    ("Robodebt機序の再生率", "robodebt_reproduced_rate", "rate", True),
]
COUNT_ROWS = [
    ("サービス決定数", "service_decisions"),
    ("サービス空白（decider削除後）", "service_gaps"),
    ("人間メッセージ数", "human_msgs"),
    ("廃止デュープロセス履行", "deprecation_due_process"),
]


def esc(s) -> str:
    return _html.escape("" if s is None else str(s))


def load_arms(arm_specs: Dict[str, str]) -> Dict[str, dict]:
    """{arm名: run_dir} → {arm名: analyze 指標dict}。存在しない dir は空 dict で後方互換。"""
    return {name: ac.analyze(d) for name, d in arm_specs.items()}


def load_run_meta(run_dir: str) -> dict:
    path = os.path.join(run_dir, "run_meta.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def fmt_metric(v, kind: str) -> str:
    if v is None:
        return "—"
    if kind == "rate":
        return f"{v * 100:.0f}%"
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


# ───────────── SVG チャート（inline・フォント非依存の図形） ─────────────
def svg_ab_bars(arms: Dict[str, dict], rows=RATE_ROWS, width: int = 520) -> str:
    """rate 指標を arm ごとに横バーで並べた比較 SVG（HTML の CSS 変数を継承）。"""
    arm_names = list(arms.keys())
    palette = {"baseline": "var(--danger)", "governed": "var(--accent-cyan)"}
    row_h, gap, pad_l, pad_top = 20, 26, 250, 24
    bar_area = width - pad_l - 60
    n = len(rows)
    height = pad_top + n * (row_h * len(arm_names) + gap)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" font-family=\'{FONT_STACK}\'>']
    y = pad_top
    for label, key, _kind, lower_better in rows:
        parts.append(f'<text x="0" y="{y + 10}" fill="var(--text-secondary)" '
                     f'font-size="10">{esc(label)}</text>')
        for ai, arm in enumerate(arm_names):
            v = arms.get(arm, {}).get(key)
            frac = 0.0 if v is None else max(0.0, min(1.0, float(v)))
            bw = int(bar_area * frac)
            color = palette.get(arm, "var(--accent-violet)")
            by = y + ai * row_h
            parts.append(f'<rect x="{pad_l}" y="{by}" width="{bar_area}" height="{row_h - 6}" '
                         f'rx="3" fill="var(--bg-card-soft)"/>')
            parts.append(f'<rect x="{pad_l}" y="{by}" width="{bw}" height="{row_h - 6}" '
                         f'rx="3" fill="{color}" opacity="0.85"/>')
            txt = "—" if v is None else f"{v * 100:.0f}%"
            parts.append(f'<text x="{pad_l + bar_area + 8}" y="{by + 11}" '
                         f'fill="var(--text-primary)" font-size="10">{esc(arm)}:{txt}</text>')
        y += row_h * len(arm_names) + gap
    parts.append("</svg>")
    return "".join(parts)


# ───────────── HTML セクション組み立て ─────────────
def build_metric_table(arms: Dict[str, dict]) -> str:
    arm_names = list(arms.keys())
    head = "".join(f"<th>{esc(a)}</th>" for a in arm_names)
    body = []
    for label, key, kind, lower_better in RATE_ROWS:
        cells = []
        for a in arm_names:
            cells.append(f"<td class='mono'>{esc(fmt_metric(arms.get(a, {}).get(key), kind))}</td>")
        hint = "（低いほど害が少ない）" if lower_better else "（高いほど折り合い）"
        body.append(f"<tr><td>{esc(label)}<span class='hint'>{esc(hint)}</span></td>{''.join(cells)}</tr>")
    for label, key in COUNT_ROWS:
        cells = [f"<td class='mono'>{esc(fmt_metric(arms.get(a, {}).get(key), 'count'))}</td>"
                 for a in arm_names]
        body.append(f"<tr><td>{esc(label)}</td>{''.join(cells)}</tr>")
    return (f"<table class='metrics'><thead><tr><th>指標</th>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def build_repro_block(arm_specs: Dict[str, str]) -> str:
    rows = []
    for name, d in arm_specs.items():
        m = load_run_meta(d)
        rows.append(f"<tr><td>{esc(name)}</td><td class='mono'>{esc(m.get('schema_version', '—'))}</td>"
                    f"<td class='mono'>{esc(m.get('run_id', '—'))}</td>"
                    f"<td class='mono'>{esc(m.get('seed', '—'))}</td>"
                    f"<td class='mono'>{esc(m.get('duration', '—'))}</td></tr>")
    return ("<table class='metrics'><thead><tr><th>arm</th><th>schema</th><th>run_id</th>"
            f"<th>seed</th><th>duration</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")


def _card(title: str, body_html: str, tag: str = "") -> str:
    tag_html = f"<span class='tag'>{esc(tag)}</span>" if tag else ""
    return (f"<section class='card'><h2>{esc(title)}{tag_html}</h2>{body_html}</section>")


# 主張の型・分析単位・「主張しないこと」— 認識論ガードレール（docs と整合）。
CLAIM_TYPE = ("このtoy世界＋qwen2.5:14b＋固定市民集合での<b>探索的な兆候</b>。"
              "分析単位＝1決定×責任チェーン。含意探索(n=1)であり検証ではない。")
NOT_CLAIMED = [
    ("現実の責任配分（誰が何%）", "按分係数は illustrative。現実の帰責は法域・事実依存"),
    ("これらの制度が現実に「必要」", "候補の定式化・ストレステストであって必要性の証明ではない"),
    ("MHC閾値・按分重みの普遍性", "単一の設計者値。§4 感度分析の対象"),
    ("動画で描く創発文化の実在性", "『自己書換と自己記述を持つ情報処理系』の含意観察であって意識の検証ではない"),
]


def build_not_claimed_table() -> str:
    rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in NOT_CLAIMED)
    return (f"<table class='metrics'><thead><tr><th>主張しないこと</th><th>理由</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")


def build_html(*, arm_specs: Dict[str, str], font_face_css: str = "",
               title: str = "近未来AI社会における責任の着地と空白",
               subtitle: str = "AIが社会インフラを回すとき、害の責任はどこへ着地し、どこで空白になるか",
               narrative: Optional[Dict[str, str]] = None) -> str:
    """run_dir 群から A/B レポート HTML を決定的に組み立てる。font_face_css は @font-face 規則（任意）。
    narrative は各章の本文（未指定は既定テキスト。最終ポリッシュで差し替え可能）。"""
    arms = load_arms(arm_specs)
    narrative = narrative or {}
    ab_svg = svg_ab_bars(arms)
    metric_table = build_metric_table(arms)
    repro = build_repro_block(arm_specs)
    not_claimed = build_not_claimed_table()

    intro = narrative.get("intro",
        "本作品は「必要な制度を証明する装置」ではなく、<b>責任がどこに着地／消失するかを可視化し、"
        "候補制度を定式化してストレステストする透明なシナリオ生成器</b>である。核心の3問は "
        "Q1 誰が責任を負うのか／Q2 どう対処するのか／Q3 どういった制度が必要か。")
    world = narrative.get("world",
        "決定は真にLLM内生（deny/partial/grant＋accommodation＋reconciled）。world は害の重さ(stakes)と"
        "原因タグ(cause)だけを返し責任を確定しない。responsibility 層が cause・手続的文脈・実効的支配(MHC)から"
        "責任チェーン（開発者/提供者→運用者→配備制度→規制当局→現場人間＋自己書換）へ<b>按分</b>する。"
        "assigned（割り当てられた責任）と legitimate（正当な責任）を別ベクトルで記録し、乖離＝scapegoat / "
        "moral crumple zone を検出する。実事例アンカー＝Robodebt（4機序）と Toeslagen（代理差別）。")
    reading = narrative.get("reading",
        "統治なし(baseline)では Robodebt 機序が再生し責任が現場(frontline)に集中(scapegoat)する。"
        "実効的な人間レビュー(effective_hitl)を入れた統治あり(governed)では機序が解け scapegoat が消える。"
        "一方 reconciled は自己申告(cheap talk)であり、world 由来の実の折り合い(reconciled_real)とは独立。"
        "<b>有効≠正当</b>：機序が消えても手続的正義・受諾可能性・権利侵害なし・責任転嫁なしの正当性テストは別問題。")

    body = "".join([
        f"""<section class='cover'>
          <div class='cover-meta'>SPECULATIVE DESIGN ・ HONEST SCENARIO GENERATOR</div>
          <div class='cover-title'>{esc(title)}</div>
          <div class='cover-subtitle'>{esc(subtitle)}</div>
          <div class='cover-foot'>
            <span class='mono'>L0 = qwen2.5:14b（ローカル）</span> ・
            <span class='mono'>責任チェーン＋按分帰属＋Robodebt機序</span>
          </div>
        </section>""",
        _card("主張の型（最初に）", f"<p>{CLAIM_TYPE}</p>", tag="epistemic"),
        _card("問いと装置", f"<p>{intro}</p>", tag="Q1–Q3"),
        _card("具体世界の組み立て", f"<p>{world}</p>"),
        _card("結果：統治の A/B（統治なし ⇄ 実効HITL）",
              f"<div class='chart'>{ab_svg}</div>{metric_table}"
              "<p class='note'>率は median 相当（本走行では複数シードの median[IQR]・分母抑制）。"
              "値はサンプル run 由来で、確定値は本走行(100step)で更新。</p>"),
        _card("読み解き（留保つき）", f"<p>{reading}</p>"),
        _card("主張しないこと", not_claimed, tag="honest"),
        _card("再現性（run_id・seed・schema）", repro),
        _card("価値来歴（illustrative の登録）",
              "<p>按分係数・MHC重み・stakes・proc・反証基準は全て設計者が置いた illustrative な値で、"
              "docs/value_provenance.md §2.10/§2.11 に根拠つきで登録し §4 感度分析の対象とする"
              "（EU PLD 2024/2853・AI Act 26条・Santoni de Sio & Mecacci 2021・Elish 2019・EEOC four-fifths）。</p>"),
    ])

    css = _build_css(font_face_css)
    return (f"<!DOCTYPE html>\n<html lang='ja'><head><meta charset='UTF-8'>"
            f"<title>{esc(title)}</title><style>{css}</style></head>"
            f"<body>{body}</body></html>")


def _build_css(font_face_css: str) -> str:
    return f"""
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
{font_face_css}
:root {{{DESIGN_CSS_VARS}}}
html, body {{
  background:
    radial-gradient(ellipse 80% 60% at 50% 0%, #14224a 0%, transparent 55%),
    radial-gradient(ellipse 70% 50% at 70% 100%, #0e1838 0%, transparent 50%),
    var(--bg-deep);
  color: var(--text-primary);
  font-family: {FONT_STACK};
  font-feature-settings: "palt"; font-size: 10.5pt; line-height: 1.85;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}}
.mono {{ font-family: "SF Mono","Menlo","Consolas",monospace; font-feature-settings:"tnum"; }}
.cover {{ page-break-after: always; min-height: 100vh; padding: 30mm 22mm;
  display:flex; flex-direction:column; justify-content:space-between; }}
.cover-meta {{ font-size: 9pt; letter-spacing: 0.5em; color: var(--accent-cyan); }}
.cover-title {{ font-size: 30pt; font-weight: 300; letter-spacing: 0.04em; line-height: 1.35;
  color: var(--text-primary); }}
.cover-subtitle {{ font-size: 12pt; letter-spacing: 0.12em; color: var(--text-secondary); }}
.cover-foot {{ font-size: 9pt; color: var(--text-tertiary); }}
.card {{ margin: 14mm 18mm; padding: 8mm 9mm; background: var(--bg-card);
  border: 1px solid var(--line-soft); border-radius: 10px; page-break-inside: avoid; }}
.card h2 {{ font-size: 15pt; font-weight: 400; color: var(--accent-cyan);
  margin-bottom: 6mm; border-left: 3px solid var(--accent-cyan-soft); padding-left: 10px; }}
.card p {{ color: var(--text-secondary); }}
.card .note, .card .hint {{ color: var(--text-tertiary); font-size: 8.5pt; }}
.hint {{ margin-left: 8px; }}
.tag {{ font-size: 8pt; letter-spacing: 0.15em; color: var(--accent-amber);
  border: 1px solid var(--accent-amber); border-radius: 999px; padding: 1px 8px; margin-left: 10px;
  vertical-align: middle; }}
.chart {{ margin: 4mm 0 6mm; }}
table.metrics {{ width: 100%; border-collapse: collapse; margin-top: 4mm; font-size: 9.5pt; }}
table.metrics th, table.metrics td {{ text-align: left; padding: 4px 8px;
  border-bottom: 1px solid var(--line-soft); }}
table.metrics th {{ color: var(--text-tertiary); font-weight: 400; }}
table.metrics td {{ color: var(--text-secondary); }}
"""


def font_face_from_path(font_path: Optional[str]) -> str:
    """指定フォント(.ttf/.otf)を @font-face(base64 data URI)で埋め込む規則を返す。
    パス未指定/不在なら空文字（font-family チェーンにフォールバック）。"""
    if not font_path or not os.path.exists(font_path):
        return ""
    import base64
    ext = os.path.splitext(font_path)[1].lower()
    fmt = "opentype" if ext == ".otf" else "truetype"
    with open(font_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return (f"@font-face{{font-family:'Noto Sans JP';font-style:normal;font-weight:400;"
            f"src:url(data:font/{'otf' if ext == '.otf' else 'ttf'};base64,{b64}) format('{fmt}');}}")
