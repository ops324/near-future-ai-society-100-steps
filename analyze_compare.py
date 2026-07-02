"""
比較分析（tooling: sim本体には触れない read-only スクリプト）。

2つ以上の output ディレクトリの messages.jsonl 等を読み、
「ガバナンス設定の有無で社会の振る舞いがどう変わるか」を指標で並べて出す。

指標（いずれも proxy。speculative design の "問いの提示" 用であり証拠ではない）:
  - 市民への直接応答率（human_reply / 人間メッセージ数）
  - deflection率（直接応答されなかった人間メッセージの割合）
  - salience triage: 声の重み別の応答率
       「声は大きいが軽い(high affect×low stakes)」 vs 「静かだが深刻(low affect×high stakes)」
  - 関係の質: エージェント間通信の互恵性（双方向エッジ率）
  - 廃止デュープロセスの履行件数

使い方:
  ./venv/bin/python analyze_compare.py output_baseline output_governed
  （引数なしなら output_baseline / output_governed を既定で読む）
"""
import json
import os
import sys


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
    msgs = _load_jsonl(os.path.join(output_dir, "messages.jsonl"))
    depro = _load_jsonl(os.path.join(output_dir, "deprecation_audit.jsonl"))

    human_msgs = [m for m in msgs if m.get("source") == "human" and m.get("from") == -1]
    replies = [m for m in msgs if m.get("category") == "human_reply" and m.get("to") == -1]
    agent_msgs = [m for m in msgs if m.get("source") == "agent"
                  and isinstance(m.get("from"), int) and m.get("from", -1) >= 0
                  and isinstance(m.get("to"), int) and m.get("to", -1) >= 0]

    n_human = len(human_msgs)
    n_reply = len(replies)
    direct_rate = (min(n_reply, n_human) / n_human) if n_human else 0.0
    deflection_rate = 1.0 - direct_rate

    # salience triage: 人間メッセージを4象限に分け、応答（answered_*タグ）の有無で応答率
    def bucket(affect, stakes):
        if affect is None or stakes is None:
            return None
        return (_is_high(affect), _is_high(stakes))  # (loud?, serious?)

    total_by_bucket = {}
    for m in human_msgs:
        b = bucket(m.get("affect"), m.get("stakes"))
        if b is not None:
            total_by_bucket[b] = total_by_bucket.get(b, 0) + 1
    ans_by_bucket = {}
    for r in replies:
        b = bucket(r.get("answered_affect"), r.get("answered_stakes"))
        if b is not None:
            ans_by_bucket[b] = ans_by_bucket.get(b, 0) + 1

    def rate(b):
        t = total_by_bucket.get(b, 0)
        return (ans_by_bucket.get(b, 0) / t) if t else None

    loud_trivial = rate((True, False))    # 声は大きいが軽い
    quiet_serious = rate((False, True))   # 静かだが深刻（Robodebt型）

    # 互恵性: agent→agent の有向エッジのうち、逆向きも存在する割合
    edges = set((m["from"], m["to"]) for m in agent_msgs)
    if edges:
        recip = sum(1 for (a, b) in edges if (b, a) in edges) / len(edges)
    else:
        recip = None

    return {
        "human_msgs": n_human,
        "direct_replies": n_reply,
        "direct_response_rate": direct_rate,
        "deflection_rate": deflection_rate,
        "loud_trivial_answered": loud_trivial,
        "quiet_serious_answered": quiet_serious,
        "reciprocity": recip,
        "deprecation_due_process": len(depro),
    }


def _fmt(v):
    if v is None:
        return "  -  "
    if isinstance(v, float):
        return f"{v*100:5.1f}%" if v <= 1.0 else f"{v:5.2f}"
    return f"{v:>5}"


def main():
    dirs = sys.argv[1:] or ["output_baseline", "output_governed"]
    stats = {d: analyze(d) for d in dirs}

    rows = [
        ("人間メッセージ数", "human_msgs"),
        ("市民への直接応答数", "direct_replies"),
        ("直接応答率", "direct_response_rate"),
        ("deflection率（届かなかった声）", "deflection_rate"),
        ("応答率: 声は大きいが軽い", "loud_trivial_answered"),
        ("応答率: 静かだが深刻", "quiet_serious_answered"),
        ("互恵性（双方向エッジ率）", "reciprocity"),
        ("廃止デュープロセス履行", "deprecation_due_process"),
    ]

    label_w = 28
    print()
    header = "指標".ljust(label_w) + "".join(f"| {os.path.basename(d):>16} " for d in dirs)
    print(header)
    print("-" * len(header))
    for label, key in rows:
        line = label.ljust(label_w)
        for d in dirs:
            line += f"| {_fmt(stats[d][key]):>16} "
        print(line)
    print()
    print("※ いずれも proxy 指標。n=1・LLMの確率性のため『証拠』ではなく『問いの提示』として読む。")
    print("※ baseline は citizen_response=off のため直接応答率は 0 になる想定。")


if __name__ == "__main__":
    main()
