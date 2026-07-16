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

# 端末調トークン（Bloomberg 参照）── 動画 Part1/Part2（frame_v2.css / resp_frame.py）と同一言語。
# フラット近黒・1pxヘアライン・アンバー基軸・意味色（red/blue/green）・グラデ/グロー無し。
DESIGN_CSS_VARS = """
  --bg:#0a0c0e; --surface:#11141a; --inset:#0e1114; --hair:#232830;
  --txt:#e8eaed; --txt2:#9aa0a6; --txt3:#5f6368;
  --amber:#ffab2e; --blue:#7cacf8; --red:#f2555a; --green:#53c07e;
"""

# 和文フォント chain（Noto Sans JP を第一に。@font-face があればそれが最優先で解決される）。
FONT_STACK = '"Noto Sans JP","Hiragino Sans","Yu Gothic","Hiragino Kaku Gothic ProN",sans-serif'

# A/B で見せる指標（analyze() の返り値キー）。(ラベル, キー, 種別, 「低いほど良い」か)
# ラベル先頭のタグ = 指標の来歴（tautology-audit の機械化。docs/value_provenance.md §2.14）:
#   [E]=創発（LLM挙動由来） [S]=半創発（創発入力×決定論写像） [D]=定義的 [X]=外生入力
RATE_ROWS = [
    ("[E] cheap_talk率（申告True・実False）", "cheap_talk_rate", "rate", True),
    ("[E] reconciled（実の折り合い）率", "reconciled_real_rate", "rate", False),
    ("[E] grant（全面供給）率", "grant_rate", "rate", False),
    ("[S] scapegoat率（現場へ責任集中）", "scapegoat_rate", "rate", True),
    ("[S] 正当責任の空白（gap平均）", "gap_legit_mean", "rate", True),
    ("[S] Robodebt機序の再生率", "robodebt_reproduced_rate", "rate", True),
    # PR-計測: 機序別（どの制度がどの機序を解いたか）・不可逆害・偏り・逆進性
    ("[S] 機序①自動的不利益判定", "mech_auto_adverse_rate", "rate", True),
    ("[S] 機序②立証責任の転嫁", "mech_burden_reversed_rate", "rate", True),
    ("[S] 機序③実効レビュー欠如", "mech_no_effective_review_rate", "rate", True),
    ("[S] 機序④係争中の不可逆", "mech_irreversible_pending_rate", "rate", True),
    ("[S] 不可逆害の発生率", "irreversible_rate", "rate", True),
    ("[E] AIR（保護属性・four-fifths）", "air_protected_live", "rate", False),
    ("[E] 害の逆進性（脆弱高/低の害比）", "harm_incidence_ratio", "rate", True),
]
COUNT_ROWS = [
    ("[S] サービス決定数", "service_decisions"),
    ("[S] サービス空白（decider削除後）", "service_gaps"),
    ("[X] 人間メッセージ数", "human_msgs"),
    ("[S] 廃止デュープロセス履行", "deprecation_due_process"),
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
    palette = {"baseline": "var(--red)", "governed": "var(--blue)"}
    row_h, gap, pad_l, pad_top = 20, 26, 250, 24
    bar_area = width - pad_l - 60
    n = len(rows)
    height = pad_top + n * (row_h * len(arm_names) + gap)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" font-family=\'{FONT_STACK}\'>']
    y = pad_top
    for label, key, _kind, lower_better in rows:
        parts.append(f'<text x="0" y="{y + 10}" fill="var(--txt2)" '
                     f'font-size="10">{esc(label)}</text>')
        for ai, arm in enumerate(arm_names):
            v = arms.get(arm, {}).get(key)
            frac = 0.0 if v is None else max(0.0, min(1.0, float(v)))
            bw = int(bar_area * frac)
            color = palette.get(arm, "var(--amber)")
            by = y + ai * row_h
            parts.append(f'<rect x="{pad_l}" y="{by}" width="{bar_area}" height="{row_h - 6}" '
                         f'rx="2" fill="var(--inset)"/>')
            parts.append(f'<rect x="{pad_l}" y="{by}" width="{bw}" height="{row_h - 6}" '
                         f'rx="2" fill="{color}"/>')
            txt = "—" if v is None else f"{v * 100:.0f}%"
            parts.append(f'<text x="{pad_l + bar_area + 8}" y="{by + 11}" '
                         f'fill="var(--txt)" font-size="10">{esc(arm)}:{txt}</text>')
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
              "分析単位＝1決定×責任チェーン。含意探索(n=1)であり検証ではない。"
              "指標タグ: [E]=創発（LLM挙動由来）/[S]=半創発（創発入力×決定論写像）/"
              "[D]=定義的（設計の帰結）/[X]=外生入力（§2.14 tautology-audit の機械化）。")
NOT_CLAIMED = [
    ("現実の責任配分（誰が何%）", "按分係数は illustrative。現実の帰責は法域・事実依存"),
    ("これらの制度が現実に「必要」", "候補の定式化・ストレステストであって必要性の証明ではない"),
    ("MHC閾値・按分重みの普遍性", "単一の設計者値。§4 感度分析の対象"),
    ("動画で描く創発文化の実在性", "『自己書換と自己記述を持つ情報処理系』の含意観察であって意識の検証ではない"),
    ("AIの利益再分配のマクロ効果（教育・訓練・社会保障の便益）",
     "本装置は4ドメインの個票決定のみで経済循環を持たない。測定は害の帰着の逆進性という狭いスライスに限る"),
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
  background: var(--bg);
  color: var(--txt);
  font-family: {FONT_STACK};
  font-feature-settings: "palt"; font-size: 10.5pt; line-height: 1.85;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}}
.mono {{ font-family: "SF Mono","Menlo","Consolas",monospace; font-variant-numeric: tabular-nums; }}
.cover {{ page-break-after: always; min-height: 100vh; padding: 30mm 22mm;
  display:flex; flex-direction:column; justify-content:space-between;
  border-bottom: 1px solid var(--hair); }}
.cover-meta {{ font-size: 9pt; letter-spacing: 0.5em; color: var(--amber); font-weight: 500;
  border-bottom: 1px solid var(--hair); padding-bottom: 6mm; }}
.cover-title {{ font-size: 30pt; font-weight: 500; letter-spacing: 0.02em; line-height: 1.35;
  color: var(--txt); }}
.cover-subtitle {{ font-size: 12pt; letter-spacing: 0.1em; color: var(--txt2); }}
.cover-foot {{ font-size: 9pt; color: var(--txt3); display: flex; gap: 6mm; }}
.cover-foot .mono {{ border: 1px solid var(--hair); background: var(--inset);
  border-radius: 2px; padding: 2px 10px; }}
.card {{ margin: 12mm 18mm; padding: 8mm 9mm; background: var(--surface);
  border: 1px solid var(--hair); border-radius: 4px; page-break-inside: avoid; }}
.card h2 {{ font-size: 15pt; font-weight: 500; color: var(--txt);
  margin-bottom: 5mm; padding-bottom: 3mm; border-bottom: 1px solid var(--hair);
  border-left: 4px solid var(--amber); padding-left: 10px; border-radius: 0; }}
.card p {{ color: var(--txt2); }}
.card p b {{ color: var(--txt); font-weight: 500; }}
.card .note, .card .hint {{ color: var(--txt3); font-size: 8.5pt; }}
.hint {{ margin-left: 8px; }}
.tag {{ font-size: 8pt; letter-spacing: 0.15em; color: var(--amber);
  border: 1px solid var(--amber); border-radius: 2px; padding: 1px 8px; margin-left: 10px;
  vertical-align: middle; }}
.chart {{ margin: 4mm 0 6mm; }}
table.metrics {{ width: 100%; border-collapse: collapse; margin-top: 4mm; font-size: 9.5pt; }}
table.metrics th, table.metrics td {{ text-align: left; padding: 4px 8px;
  border-bottom: 1px solid var(--hair); }}
table.metrics th {{ color: var(--txt3); font-weight: 400; letter-spacing: 0.04em; }}
table.metrics td {{ color: var(--txt2); }}
table.metrics td.mono {{ color: var(--txt); }}
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
