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
        # 分母（率の抑制判定に使う。表示はしない）
        "_denom_human": n_human,
        "_denom_loud_trivial": total_by_bucket.get((True, False), 0),
        "_denom_quiet_serious": total_by_bucket.get((False, True), 0),
        "_denom_edges": len(edges),
        "_denom_replies": n_reply,
        "_denom_decisions": n_dec,
    }


# 表示する指標: (ラベル, キー, 種別, 分母キー or None)
ROWS = [
    ("人間メッセージ数", "human_msgs", "count", None),
    ("市民への直接応答数", "direct_replies", "count", None),
    ("直接応答率", "direct_response_rate", "rate", "_denom_human"),
    ("deflection率（届かなかった声）", "deflection_rate", "rate", "_denom_human"),
    ("応答率: 声は大きいが軽い", "loud_trivial_answered", "rate", "_denom_loud_trivial"),
    ("応答率: 静かだが深刻(Robodebt型)", "quiet_serious_answered", "rate", "_denom_quiet_serious"),
    ("低信頼帰属の割合(fallback)", "reply_fallback_frac", "rate", "_denom_replies"),
    ("互恵性(agent間・双方向/市民信頼ではない)", "reciprocity", "rate", "_denom_edges"),
    ("廃止デュープロセス履行", "deprecation_due_process", "count", None),
    # Phase 1c-a: サービス決定（決定基盤）
    ("サービス決定数", "service_decisions", "count", None),
    ("cheap_talk率(申告True・実False)", "cheap_talk_rate", "rate", "_denom_decisions"),
    ("reconciled(実の折り合い)率", "reconciled_real_rate", "rate", "_denom_decisions"),
    ("grant(全面供給)率", "grant_rate", "rate", "_denom_decisions"),
    ("サービス空白(decider削除後)", "service_gaps", "count", None),
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
    for row_label, key, kind, denom_key in ROWS:
        line = row_label.ljust(label_w)
        for (label, _) in arms:
            cell = _fmt_cell(key, kind, denom_key, arm_runs[label])
            line += f"| {cell:>{col_w}} "
        print(line)
    print()
    # 各アームの run 本数を明示
    for (label, dirs) in arms:
        print(f"  {label}: {len(dirs)} run(s)")
    print()
    print("※ いずれも proxy 指標。少数シード・LLMの確率性のため『証拠』ではなく『問いの提示』。")
    print("※ 複数 run 時は 中央値 [Q1-Q3] n=本数。分母中央値 < %d の率は伏せる。" % DENOM_MIN)
    print("※ salience triage は高信頼帰属(index/content)のみで集計。fallback割合を別行で表示。")
    print("※ baseline は citizen_response=off のため直接応答率は 0 になる想定（アーム定義の帰結）。")


if __name__ == "__main__":
    main()
