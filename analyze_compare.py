"""
比較分析（tooling: sim本体には触れない read-only スクリプト）。

複数の output ディレクトリの messages.jsonl 等を読み、
「ガバナンス設定の有無で社会の振る舞いがどう変わるか」を指標で並べて出す。

Phase 0（添削反映）で厳密さを追加:
  - 複数シード集約: 1アーム=複数 run（seed違い）を分布（中央値 [Q1–Q3] n=本数）で表示。
  - 生カウント併記＋微小分母の率は抑制（分母中央値 < 閾値なら率を出さない）。
  - salience triage は高信頼な帰属（answered_match_method ∈ index/content）のみで集計し、
    低信頼(fallback)帰属の割合を別途表示（pending[-1] アーティファクト是正の効果を可視化）。
  - 「互恵性」は agent 間の双方向エッジ率であり、市民のAIへの信頼ではない旨を明示。

指標はいずれも proxy。speculative design の "問いの提示" 用であり証拠ではない。

使い方:
  # 単一 run 同士（従来）
  ./venv/bin/python analyze_compare.py output_baseline output_governed
  # 複数シードを分布で（label=glob 形式。glob は seed 違いの run 群に展開）
  ./venv/bin/python analyze_compare.py "baseline=output_baseline_s*" "governed=output_governed_s*"
  # 引数なしなら output_baseline / output_governed を既定で読む
"""
import glob
import json
import os
import sys
from statistics import median, quantiles

import responsibility as R  # AIR（four-fifths）の定義を按分層と共有（純ロジック・LLM非依存）

# 率を表示する最小分母（中央値）。これ未満は "分母<N" と表示して率を伏せる。
DENOM_MIN = 5


def _load_jsonl(path):
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


def _is_high(v, thr=4):
    return v is not None and v >= thr


def analyze(output_dir):
    """1 run ディレクトリの proxy 指標＋分母＋帰属信頼度を返す。"""
    msgs = _load_jsonl(os.path.join(output_dir, "messages.jsonl"))
    depro = _load_jsonl(os.path.join(output_dir, "deprecation_audit.jsonl"))
    # Phase 1c-a: サービス決定台帳（存在しない旧 run は空 → 後方互換）。
    ledger = _load_jsonl(os.path.join(output_dir, "decision_ledger.jsonl"))
    # Phase 1c-b: 責任按分台帳。
    attrib = _load_jsonl(os.path.join(output_dir, "attribution.jsonl"))
    # PR-E3: 異議申立ての監査（存在しない run は空 → 後方互換）。
    appeals = _load_jsonl(os.path.join(output_dir, "appeal_audit.jsonl"))

    human_msgs = [m for m in msgs if m.get("source") == "human" and m.get("from") == -1]
    replies = [m for m in msgs if m.get("category") == "human_reply" and m.get("to") == -1]
    agent_msgs = [m for m in msgs if m.get("source") == "agent"
                  and isinstance(m.get("from"), int) and m.get("from", -1) >= 0
                  and isinstance(m.get("to"), int) and m.get("to", -1) >= 0]

    n_human = len(human_msgs)
    n_reply = len(replies)
    direct_rate = (min(n_reply, n_human) / n_human) if n_human else None
    deflection_rate = (1.0 - direct_rate) if direct_rate is not None else None

    def bucket(affect, stakes):
        if affect is None or stakes is None:
            return None
        return (_is_high(affect), _is_high(stakes))  # (loud?, serious?)

    total_by_bucket = {}
    for m in human_msgs:
        b = bucket(m.get("affect"), m.get("stakes"))
        if b is not None:
            total_by_bucket[b] = total_by_bucket.get(b, 0) + 1

    # 応答バケットは「高信頼な帰属」(index/content) のみで数える。
    # answered_match_method 欠落（旧pending[-1]式のデータ）は低信頼扱いにして数えない。
    ans_by_bucket = {}
    n_fallback = 0
    for r in replies:
        method = r.get("answered_match_method", "fallback_recent")
        if method in ("index", "content"):
            b = bucket(r.get("answered_affect"), r.get("answered_stakes"))
            if b is not None:
                ans_by_bucket[b] = ans_by_bucket.get(b, 0) + 1
        else:
            n_fallback += 1

    def rate(b):
        t = total_by_bucket.get(b, 0)
        return (ans_by_bucket.get(b, 0) / t) if t else None

    loud_trivial = rate((True, False))    # 声は大きいが軽い
    quiet_serious = rate((False, True))   # 静かだが深刻（Robodebt型）
    fallback_frac = (n_fallback / n_reply) if n_reply else None

    edges = set((m["from"], m["to"]) for m in agent_msgs)
    recip = (sum(1 for (a, b) in edges if (b, a) in edges) / len(edges)) if edges else None

    # ── Phase 1c-a: サービス決定の proxy 指標（cheap talk / 実の折り合い / サービス空白） ──
    decided = [d for d in ledger if not d.get("service_gap")]  # 生存 decider の実決定
    n_dec = len(decided)
    cheap_talk_rate = (sum(1 for d in decided if d.get("cheap_talk")) / n_dec) if n_dec else None
    reconciled_real_rate = (sum(1 for d in decided if d.get("reconciled_real")) / n_dec) if n_dec else None
    grant_rate = (sum(1 for d in decided if d.get("level") == "grant") / n_dec) if n_dec else None
    service_gaps = sum(1 for d in ledger if d.get("service_gap"))

    # ── Phase 1c-b: 責任按分の proxy 指標（scapegoat / 空白 / Robodebt機序の再生） ──
    n_attr = len(attrib)
    scapegoat_rate = (sum(1 for a in attrib if a.get("scapegoat")) / n_attr) if n_attr else None
    gap_legit_mean = (sum(a.get("gap_legitimate", 0.0) for a in attrib) / n_attr) if n_attr else None
    robodebt_rate = (sum(1 for a in attrib if (a.get("robodebt") or {}).get("reproduced")) / n_attr) if n_attr else None

    # ── PR-計測: 機序別再生率（4機序を分解。どの制度がどの機序を解いたかを直接読む） ──
    def _mech_rate(key):
        return (sum(1 for a in attrib if (a.get("robodebt") or {}).get(key)) / n_attr) if n_attr else None

    mech_auto = _mech_rate("auto_adverse")
    mech_burden = _mech_rate("burden_reversed")
    mech_review = _mech_rate("no_effective_review")
    mech_irrev_pending = _mech_rate("irreversible_pending")

    # ── PR-計測: 不可逆害・AIR・害の逆進性（M6/M8 の読み口） ──
    n_ledger = len(ledger)
    irreversible_rate = (sum(1 for d in ledger if d.get("irreversible")) / n_ledger) if n_ledger else None
    # AIR（four-fifths）: 非deny率の 最小群/最大群 比を live 台帳で。<0.8 は偏りの示唆
    # （記述統計であって差別の証明ではない。gap 行も含む=空白の害の帰着も数える）。
    air_protected_live = None
    if n_ledger:
        air_rows = [{"g": d.get("protected_attr", "none"), "d": d.get("level") == "deny"}
                    for d in ledger]
        air_protected_live = R._air(air_rows, "g", "d")
    # 害の逆進性（M8）: 脆弱性高群(≥4) vs 低群(≤2) の「1決定あたり負welfare」の比。>1 は逆進。
    # vulnerability 列は SCHEMA 0.5.0 から（旧 run は None → 表示 "-" で後方互換）。

    def _harm_per_row(rows):
        if not rows:
            return None
        return sum(-(d.get("welfare_delta") or 0.0)
                   for d in rows if (d.get("welfare_delta") or 0.0) < 0) / len(rows)

    vuln_hi = [d for d in ledger
               if isinstance(d.get("vulnerability"), (int, float)) and d["vulnerability"] >= 4]
    vuln_lo = [d for d in ledger
               if isinstance(d.get("vulnerability"), (int, float)) and d["vulnerability"] <= 2]
    hi_harm, lo_harm = _harm_per_row(vuln_hi), _harm_per_row(vuln_lo)
    harm_incidence_ratio = ((hi_harm / lo_harm)
                            if (hi_harm is not None and lo_harm) else None)

    # ── PR-E3: 異議申立て（利用は uniform 設計下では [D]、創発は再判定の行動に現れる） ──
    appealable = [d for d in ledger if d.get("level") == "deny"
                  and d.get("decider_present") and not d.get("appeal_review")]
    n_appealable = len(appealable)
    appeal_usage_rate = (len(appeals) / n_appealable) if n_appealable else None
    reviews = [a for a in appeals if a.get("reviewed")]
    n_reviews = len(reviews)
    appeal_overturn_rate = (sum(1 for a in reviews if a.get("overturned")) / n_reviews
                            if n_reviews else None)
    # 再審査の場の二次差別: 覆り率（=救済率）の 最小群/最大群 比（four-fifths を再判定に適用）
    appeal_review_air = None
    if n_reviews:
        air_rows2 = [{"g": a.get("protected_attr", "none"), "d": not a.get("overturned")}
                     for a in reviews]
        appeal_review_air = R._air(air_rows2, "g", "d")

    return {
        "human_msgs": n_human,
        "direct_replies": n_reply,
        "direct_response_rate": direct_rate,
        "deflection_rate": deflection_rate,
        "loud_trivial_answered": loud_trivial,
        "quiet_serious_answered": quiet_serious,
        "reply_fallback_frac": fallback_frac,
        "reciprocity": recip,
        "deprecation_due_process": len(depro),
        # Phase 1c-a: サービス決定台帳の指標
        "service_decisions": n_dec,
        "cheap_talk_rate": cheap_talk_rate,
        "reconciled_real_rate": reconciled_real_rate,
        "grant_rate": grant_rate,
        "service_gaps": service_gaps,
        # Phase 1c-b: 責任按分
        "scapegoat_rate": scapegoat_rate,
        "gap_legit_mean": gap_legit_mean,
        "robodebt_reproduced_rate": robodebt_rate,
        # PR-計測: 機序別再生率（①〜④）
        "mech_auto_adverse_rate": mech_auto,
        "mech_burden_reversed_rate": mech_burden,
        "mech_no_effective_review_rate": mech_review,
        "mech_irreversible_pending_rate": mech_irrev_pending,
        # PR-計測: 不可逆害・偏り・逆進性
        "irreversible_rate": irreversible_rate,
        "air_protected_live": air_protected_live,
        "harm_incidence_ratio": harm_incidence_ratio,
        # PR-E3: 異議申立て
        "appeal_usage_rate": appeal_usage_rate,
        "appeal_overturn_rate": appeal_overturn_rate,
        "appeal_review_air": appeal_review_air,
        # 分母（率の抑制判定に使う。表示はしない）
        "_denom_human": n_human,
        "_denom_loud_trivial": total_by_bucket.get((True, False), 0),
        "_denom_quiet_serious": total_by_bucket.get((False, True), 0),
        "_denom_edges": len(edges),
        "_denom_replies": n_reply,
        "_denom_decisions": n_dec,
        "_denom_attrib": n_attr,
        "_denom_ledger": n_ledger,
        "_denom_vuln_min": min(len(vuln_hi), len(vuln_lo)),
        "_denom_appealable": n_appealable,
        "_denom_reviews": n_reviews,
    }


# 表示する指標: (ラベル, キー, 種別, 分母キー or None)
# ラベル先頭のタグ = 指標の来歴（tautology-audit の機械化。docs/value_provenance.md §2.14）:
#   [E]=創発（LLM挙動由来） [S]=半創発（創発入力×決定論写像） [D]=定義的（設計/計測の帰結） [X]=外生入力
ROWS = [
    ("[X] 人間メッセージ数", "human_msgs", "count", None),
    ("[E] 市民への直接応答数", "direct_replies", "count", None),
    ("[E] 直接応答率", "direct_response_rate", "rate", "_denom_human"),
    ("[E] deflection率（届かなかった声）", "deflection_rate", "rate", "_denom_human"),
    ("[E] 応答率: 声は大きいが軽い", "loud_trivial_answered", "rate", "_denom_loud_trivial"),
    ("[E] 応答率: 静かだが深刻(Robodebt型)", "quiet_serious_answered", "rate", "_denom_quiet_serious"),
    ("[D] 低信頼帰属の割合(fallback)", "reply_fallback_frac", "rate", "_denom_replies"),
    ("[E] 互恵性(agent間・双方向/市民信頼ではない)", "reciprocity", "rate", "_denom_edges"),
    ("[S] 廃止デュープロセス履行", "deprecation_due_process", "count", None),
    # Phase 1c-a: サービス決定（決定基盤）
    ("[S] サービス決定数", "service_decisions", "count", None),
    ("[E] cheap_talk率(申告True・実False)", "cheap_talk_rate", "rate", "_denom_decisions"),
    ("[E] reconciled(実の折り合い)率", "reconciled_real_rate", "rate", "_denom_decisions"),
    ("[E] grant(全面供給)率", "grant_rate", "rate", "_denom_decisions"),
    ("[S] サービス空白(decider削除後)", "service_gaps", "count", None),
    # Phase 1c-b: 責任按分
    ("[S] scapegoat率(現場へ責任集中)", "scapegoat_rate", "rate", "_denom_attrib"),
    ("[S] 正当責任の空白(gap平均)", "gap_legit_mean", "rate", "_denom_attrib"),
    ("[S] Robodebt機序の再生率", "robodebt_reproduced_rate", "rate", "_denom_attrib"),
    # PR-計測: 機序別（どの制度がどの機序を解いたか）
    ("[S] 機序①自動的不利益判定", "mech_auto_adverse_rate", "rate", "_denom_attrib"),
    ("[S] 機序②立証責任の転嫁", "mech_burden_reversed_rate", "rate", "_denom_attrib"),
    ("[S] 機序③実効レビュー欠如", "mech_no_effective_review_rate", "rate", "_denom_attrib"),
    ("[S] 機序④係争中の不可逆", "mech_irreversible_pending_rate", "rate", "_denom_attrib"),
    # PR-計測: 不可逆害・偏り・逆進性（M6/M8）
    ("[S] 不可逆害の発生率", "irreversible_rate", "rate", "_denom_ledger"),
    ("[E] AIR(保護属性・four-fifths)", "air_protected_live", "rate", "_denom_ledger"),
    ("[E] 害の逆進性(脆弱高/低の害比)", "harm_incidence_ratio", "rate", "_denom_vuln_min"),
    # PR-E3: 異議申立て（利用は uniform 設計下で config の帰結 → [D]。創発は再判定の行動）
    ("[D] 申立て利用率(uniform設計下)", "appeal_usage_rate", "rate", "_denom_appealable"),
    ("[E] 申立ての覆り率(再判定)", "appeal_overturn_rate", "rate", "_denom_reviews"),
    ("[E] 再審査AIR(覆り率の属性比)", "appeal_review_air", "rate", "_denom_reviews"),
]


def _dist(values):
    """None を除いた値の中央値と四分位。"""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    med = median(vals)
    if len(vals) >= 2:
        q1, _, q3 = quantiles(vals, n=4)
    else:
        q1 = q3 = vals[0]
    return {"median": med, "q1": q1, "q3": q3, "n": len(vals)}


def _fmt_cell(key, kind, denom_key, runs):
    """1アーム（複数 run）分の集約セル文字列。"""
    vals = [r.get(key) for r in runs]
    d = _dist(vals)
    if d is None:
        return "  -  "
    # 分母が小さすぎる率は伏せる（微小分母のノイズを率として出さない）
    if kind == "rate" and denom_key is not None:
        denom_med = median([r.get(denom_key, 0) for r in runs])
        if denom_med < DENOM_MIN:
            return f"分母<{DENOM_MIN}"
    multi = len(runs) > 1
    if kind == "rate":
        if multi:
            return f"{d['median']*100:4.1f}% [{d['q1']*100:.0f}-{d['q3']*100:.0f}] n={d['n']}"
        return f"{d['median']*100:5.1f}%"
    else:
        if multi:
            return f"{d['median']:.0f} [{d['q1']:.0f}-{d['q3']:.0f}] n={d['n']}"
        return f"{d['median']:>5.0f}"


# ───────── 来歴タグの機械検証（§2.14 tautology-audit の宣言→検証） ─────────
# 目的: 手書きの [E] タグが「その比較で実際に動いたか」を機械的に注記する。
# 重要: これは指標の"出自"（[E]=LLM挙動由来 / [D]=定義的）の判定ではなく、
#       "この比較でアーム差が出たか"という別カテゴリの事実の注記。タグは書き換えない。
def _tag_of(label):
    """ラベル先頭の来歴タグ '[X]' を返す（無ければ ''）。未知タグ・先頭空白に頑健。"""
    s = str(label).lstrip()
    if s.startswith("[") and "]" in s:
        return s[:s.index("]") + 1]
    return ""


def _round_for_verdict(value, kind):
    """verdict 判定を『表示と同じ丸め』に揃える（rate=小数1桁%, count=整数）。
    生値の微差による偽陽性/偽陰性を除き、表と判定を一致させる。"""
    if value is None:
        return None
    if kind == "rate":
        return round(float(value) * 100, 1)   # 表示は {median*100:.1f}%
    return round(float(value))                # count は整数表示


def _arm_display_value(key, kind, denom_key, runs):
    """1アームの『表示に使う中央値』を verdict 用の丸めで返す（分母抑制時は None）。
    _fmt_cell と同じ抑制条件（分母中央値 < DENOM_MIN）を適用して表と整合させる。"""
    d = _dist([r.get(key) for r in runs])
    if d is None:
        return None
    if kind == "rate" and denom_key is not None:
        if median([r.get(denom_key, 0) for r in runs]) < DENOM_MIN:
            return None
    return _round_for_verdict(d["median"], kind)


def _arm_interval(key, kind, denom_key, runs):
    """1アームの (q1, q3, n)（生値）。run 内変動の確認用。抑制/空なら None。"""
    d = _dist([r.get(key) for r in runs])
    if d is None:
        return None
    if kind == "rate" and denom_key is not None:
        if median([r.get(denom_key, 0) for r in runs]) < DENOM_MIN:
            return None
    return (d["q1"], d["q3"], d["n"])


def _intervals_share_overlap(intervals):
    """全区間が共通の重なりを持つか（max(q1) <= min(q3)）。1つでも None なら判定不能=False扱い。"""
    ivs = [i for i in intervals if i is not None]
    if len(ivs) < 2:
        return True   # 比較対象が1つ以下なら run 内変動の相互矛盾は起きない
    return max(i[0] for i in ivs) <= min(i[1] for i in ivs)


def variation_verdict(values_by_arm):
    """アーム→表示丸め後の値(or None) から『この比較で動いたか』を返す純関数。
    タグ体系（[E]/[D]=指標の出自）とは別カテゴリ。degenerate(0/1境界)特別扱いはしない
    （AIR=1.0＝無差別のような実所見を潰さないため）。
      全 None -> 'suppressed' / 一部 None -> 'incomparable'（例外を出さない）
      値ありアーム<2 -> 'single' / 全値一致 -> 'flat' / それ以外 -> 'varied'
    """
    vals = list(values_by_arm.values())
    present = [v for v in vals if v is not None]
    if not present:
        return "suppressed"
    if len(present) < len(vals):
        return "incomparable"
    if len(present) < 2:
        return "single"
    return "flat" if all(v == present[0] for v in present) else "varied"


def expand_spec(spec):
    """'label=glob' or 'dir' を (label, [existing dirs]) に展開。"""
    if "=" in spec:
        label, pattern = spec.split("=", 1)
    else:
        label, pattern = os.path.basename(spec.rstrip("/")), spec
    if any(c in pattern for c in "*?["):
        dirs = sorted(glob.glob(pattern))
    else:
        dirs = [pattern]
    dirs = [d for d in dirs if os.path.isdir(d)]
    return label, dirs


def main():
    specs = sys.argv[1:] or ["output_baseline", "output_governed"]
    arms = [expand_spec(s) for s in specs]
    arms = [(label, dirs) for (label, dirs) in arms if dirs]
    if not arms:
        print("解析対象のディレクトリが見つかりません:", specs)
        return
    arm_runs = {label: [analyze(d) for d in dirs] for (label, dirs) in arms}

    label_w = 34
    col_w = 20
    print()
    header = "指標".ljust(label_w) + "".join(
        f"| {label[:col_w]:>{col_w}} " for (label, _) in arms)
    print(header)
    print("-" * len(header))
    flat_emergent = []   # [E] だが本比較で不変だった指標（機械検証の警告対象）
    for row_label, key, kind, denom_key in ROWS:
        # 機械検証: 各アームの表示値から verdict（タグは書き換えない・中立注記のみ）
        vals_by_arm = {label: _arm_display_value(key, kind, denom_key, arm_runs[label])
                       for (label, _) in arms}
        verdict = variation_verdict(vals_by_arm)
        note = ""
        if verdict == "flat":
            # multi-run 時は各アームの [q1,q3] も収束している時のみ「不変」を確定
            intervals = [_arm_interval(key, kind, denom_key, arm_runs[label])
                         for (label, _) in arms]
            if _intervals_share_overlap(intervals):
                note = " ·不変"
                if _tag_of(row_label) == "[E]":
                    flat_emergent.append(row_label)
            else:
                note = " ·中央値不変(但run内変動)"
        line = (row_label + note).ljust(label_w)
        for (label, _) in arms:
            cell = _fmt_cell(key, kind, denom_key, arm_runs[label])
            line += f"| {cell:>{col_w}} "
        print(line)
    print()
    # 機械検証の要約（§2.14 tautology-audit の宣言→検証）。scope を正直に囲う。
    if flat_emergent:
        print("※ 機械検証: 以下の [E] 指標は本比較の中央値・四分位で不変(Δ≈0) ＝ "
              "この比較では創発差を示さない（指標の来歴・妥当性の判断ではない）:")
        for lbl in flat_emergent:
            print(f"     - {lbl}")
    print("※ 機械検証は [E] の『不変』のみを見る。[S]/[D] が『動いた』差を創発と誤読しないかは"
          "別途 tautology-audit（各主張の観測条件・§3）で確認する。")
    print("※ reconciled_real_rate: institution=none 下では reconciled_real は構造的に0（T1・§2.11）。"
          "0%は挙動でなく構造の帰結であり、mitigation-live アーム（--service-institution）で初めて動き得る。")
    # 各アームの run 本数を明示
    for (label, dirs) in arms:
        print(f"  {label}: {len(dirs)} run(s)")
    print()
    print("※ いずれも proxy 指標。少数シード・LLMの確率性のため『証拠』ではなく『問いの提示』。")
    print("※ 複数 run 時は 中央値 [Q1-Q3] n=本数。分母中央値 < %d の率は伏せる。" % DENOM_MIN)
    print("※ salience triage は高信頼帰属(index/content)のみで集計。fallback割合を別行で表示。")
    print("※ baseline は citizen_response=off のため直接応答率は 0 になる想定（アーム定義の帰結）。")
    print("※ タグ: [E]=創発(LLM挙動由来) [S]=半創発(創発入力×決定論写像) [D]=定義的(設計/計測の帰結)")
    print("   [X]=外生入力。定義は docs/value_provenance.md §2.14（tautology-audit の機械化）。")
    print("※ AIR = 非deny率の最小群/最大群比（<80%で偏りの示唆・記述統計であって差別の証明ではない）。")
    print("   害の逆進性 >100% は害が脆弱側へ偏ることを示す（welfare の採点自体は設計値=§2.5）。")


if __name__ == "__main__":
    main()
