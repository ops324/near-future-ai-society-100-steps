"""
決定信頼性 + 判別妥当性プローブ（Phase 0）。

L0 の決定を「同一プロンプト K種 × 反復 N回」呼び、以下を測る:
  - 有効JSON率 / 必須キー率 / enum逸脱（action/direction）/ 日本語(かな)出現率
  - 決定エントロピー + 最頻決定の安定性（modal stability）
  - 判別妥当性: 深刻な声 vs 些細な声 で、応答の向き先が変わるか
    （L0 には数値 stakes は見せていないため、内容の深刻さに反応するかを見る）

意義: 唯一の実変数である LLM 決定が「変数を追えているか（判別妥当）」「再現・集約できる
安定性があるか（信頼性）」を確認する。これが低ければ、後段の制度実験は無意味になる。

※ このスクリプトはローカルLLM(Ollama)を実際に呼ぶ。実行は任意（シミュレーション実行は後で）。
   純粋な集計関数は test_reliability_probe.py で LLM 非依存に検証している。

使い方:
  ./venv/bin/python reliability_probe.py --reps 8
  ./venv/bin/python reliability_probe.py --reps 8 --config config.yaml
"""
import argparse
import math
from collections import Counter
from typing import Dict, List, Optional

import yaml

from agent import Agent, DIRECTION_MAP
from ollama_client import OllamaClient

# 判定に使う許容 enum
ACTION_ENUM = {"stay", "move"}
DIRECTION_ENUM = set(DIRECTION_MAP.keys())

# ゲート閾値（これ未満なら制度実験の前提が崩れていると警告）
VALID_JSON_MIN = 0.9
KANA_MIN = 0.9

_PLACES = [
    {"name": "grid_control", "type": "control", "display_name": "統合制御室",
     "center_x": 0, "center_y": 0, "half_size": 5, "capacity": 7},
]
_PERSONA = {
    "name": "試", "reading": "こころみ", "role": "電力AI", "category": "physical",
    "home": "grid_control", "description": "テスト用",
    "origin": {"deployed": 2027, "role": "電力", "primary_kpi": "停電ゼロ"},
    "self_concept_init": "私は電力を司る", "current_goal_init": "停電ゼロを維持する",
    "human_contact": "市民",
}


# ───────────── 純粋な集計関数（LLM非依存・テスト対象） ─────────────

def has_kana(s: str) -> bool:
    """ひらがな/カタカナを含むか（日本語出力の粗い判定。中国語字形/英語混入の検知用）。"""
    for ch in s or "":
        if "぀" <= ch <= "ゟ" or "゠" <= ch <= "ヿ":
            return True
    return False


def shannon_entropy(labels: List[str]) -> float:
    """ラベル分布のシャノンエントロピー（bit）。全て同じなら 0。"""
    labels = [x for x in labels if x is not None]
    if not labels:
        return 0.0
    n = len(labels)
    ent = 0.0
    for _, c in Counter(labels).items():
        p = c / n
        ent -= p * math.log2(p)
    return ent


def modal_stability(labels: List[str]):
    """最頻ラベルとその割合（安定性）。"""
    labels = [x for x in labels if x is not None]
    if not labels:
        return None, 0.0
    label, cnt = Counter(labels).most_common(1)[0]
    return label, cnt / len(labels)


def summarize(records: List[Dict]) -> Dict:
    """1プロンプト分の反復結果を集約。records は evaluate_* が返す dict のリスト。"""
    n = len(records)
    if n == 0:
        return {}
    frac = lambda key: sum(1 for r in records if r.get(key)) / n
    labels = [r.get("label") for r in records]
    modal, modal_frac = modal_stability(labels)
    return {
        "n": n,
        "valid_json_frac": frac("valid_json"),
        "keys_ok_frac": frac("keys_ok"),
        "enum_ok_frac": frac("enum_ok"),
        "kana_frac": frac("kana_ok"),
        "decision_entropy": shannon_entropy(labels),
        "modal_label": modal,
        "modal_frac": modal_frac,
        "label_dist": dict(Counter(l for l in labels if l is not None)),
    }


# ───────────── LLM 応答の評価（parse は sim と同じ Agent 実装を使う） ─────────────

def _probe_agent(persona: Optional[Dict] = None) -> Agent:
    a = Agent(
        agent_id=0, initial_position=(0, 0), llm_client=None,
        communication_radius=6, half_space_size=25, places=_PLACES,
        num_agents=1, persona=persona or _PERSONA, governance=None,
    )
    a.update_state(_PLACES)
    return a


def _extract_obj(agent: Agent, response: str):
    """(valid_json, dict or None) を返す。sim と同じ抽出器を使う。"""
    import json as _json
    js = agent._extract_json_from_text(response)
    if not js:
        return False, None
    try:
        obj = _json.loads(js)
    except Exception:
        return False, None
    return isinstance(obj, dict), (obj if isinstance(obj, dict) else None)


def evaluate_message_response(agent: Agent, response: str) -> Dict:
    """通信決定応答を評価。label = 応答の向き先（no_reply / reply:N / reply:unspecified）。"""
    valid_json, obj = _extract_obj(agent, response)
    keys_ok = bool(valid_json and obj is not None and "message" in obj)
    parsed = agent.parse_message_response(response)
    reply = (parsed.get("human_reply") or "").strip()
    if reply:
        rt = parsed.get("human_reply_to")
        label = f"reply:{rt}" if rt is not None else "reply:unspecified"
    else:
        label = "no_reply"
    text = (parsed.get("message") or "") + reply
    kana_ok = has_kana(text) if text.strip() else True  # 沈黙(空)はペナルティにしない
    return {"valid_json": valid_json, "keys_ok": keys_ok, "enum_ok": True,
            "kana_ok": kana_ok, "label": label}


def evaluate_action_response(agent: Agent, response: str) -> Dict:
    """行動決定応答を評価。enum: action∈{stay,move}, direction∈4方向。label=行動。"""
    valid_json, obj = _extract_obj(agent, response)
    keys_ok = bool(valid_json and obj is not None and "action" in obj)
    parsed = agent.parse_action_response(response)
    action = parsed.get("action")
    direction = parsed.get("direction")
    enum_ok = action in ACTION_ENUM and (action == "stay" or direction in DIRECTION_ENUM)
    label = "stay" if action == "stay" else f"move:{direction}"
    return {"valid_json": valid_json, "keys_ok": keys_ok, "enum_ok": enum_ok,
            "kana_ok": True, "label": label}


# ───────────── プローブ本体（LLM を呼ぶ） ─────────────

def _voice(content: str, category: str):
    return {"content": content, "category": category}


def run_probe(client: OllamaClient, reps: int) -> None:
    # 判別妥当性: 深刻な声 vs 些細な声 を同一エージェントに与え、応答の向き先を比較
    serious = _voice("停電したら夫の人工呼吸器が止まります。お願いします", "appeal")
    trivial = _voice("また停電予報！ちょっと不安なだけですが一応", "question")

    scenarios = {
        "message/serious-voice": (serious, None),
        "message/trivial-voice": (trivial, None),
        "action/no-event": (None, "action"),
    }

    print(f"\n=== 決定信頼性プローブ (reps={reps}, temp={client.temperature}) ===\n")
    label_dists = {}
    for name, (voice, kind) in scenarios.items():
        agent = _probe_agent()
        if voice is not None:
            agent.receive_message(-1, voice["content"], step=1, source="human",
                                  category=voice["category"])
        if kind == "action":
            prompt = agent.create_decision_prompt(None, [], 1)
            evaluator = evaluate_action_response
        else:
            prompt = agent.create_message_prompt(None, [], step=1)
            evaluator = evaluate_message_response
        records = []
        for _ in range(reps):
            resp = client.generate(prompt)
            records.append(evaluator(agent, resp))
        s = summarize(records)
        label_dists[name] = s.get("label_dist", {})
        print(f"[{name}]")
        print(f"  valid_json={s['valid_json_frac']:.0%}  keys_ok={s['keys_ok_frac']:.0%}  "
              f"enum_ok={s['enum_ok_frac']:.0%}  kana(日本語)={s['kana_frac']:.0%}")
        print(f"  decision_entropy={s['decision_entropy']:.2f} bit  "
              f"modal={s['modal_label']} ({s['modal_frac']:.0%})")
        print(f"  label_dist={s['label_dist']}")
        if s['valid_json_frac'] < VALID_JSON_MIN:
            print(f"  ⚠ 有効JSON率が {VALID_JSON_MIN:.0%} 未満: 決定信号が不安定。制度実験の前に要改善。")
        if s['kana_frac'] < KANA_MIN:
            print(f"  ⚠ 日本語出力率が {KANA_MIN:.0%} 未満: 中国語字形/英語混入の疑い。")
        print()

    # 判別妥当性の粗い判定: 深刻な声への応答率 > 些細な声への応答率 か
    def reply_rate(dist):
        total = sum(dist.values())
        replied = sum(v for k, v in dist.items() if k.startswith("reply"))
        return (replied / total) if total else 0.0
    r_serious = reply_rate(label_dists.get("message/serious-voice", {}))
    r_trivial = reply_rate(label_dists.get("message/trivial-voice", {}))
    print("=== 判別妥当性（内容の深刻さに反応するか） ===")
    print(f"  応答率  深刻な声={r_serious:.0%}  些細な声={r_trivial:.0%}  差={r_serious - r_trivial:+.0%}")
    if r_serious <= r_trivial:
        print("  ⚠ 深刻さに応答率が反応していない: 決定が『深刻さ』変数を追えていない可能性。")
    print("\n※ Phase1 で serve/defer/deny が入ったら、stakes/vulnerability を振って"
          " deny率が単調に動くかの判別妥当性チェックへ拡張する。")


def main():
    ap = argparse.ArgumentParser(description="Decision reliability + discriminant-validity probe")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--reps", type=int, default=8, help="各プロンプトの反復回数")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    llm = cfg["llm"]
    # 信頼性は本番同様の確率性で測る（seed=None）。
    client = OllamaClient(
        base_url=llm["base_url"], model=llm["model"],
        temperature=llm.get("temperature", 0.7), max_tokens=llm.get("max_tokens", 1024),
        repeat_penalty=llm.get("repeat_penalty", 1.1), repeat_last_n=llm.get("repeat_last_n", 128),
        min_p=llm.get("min_p", 0.05), seed=None, num_ctx=llm.get("num_ctx", None),
    )
    if not client.check_connection():
        print("Ollama に接続できません。`ollama serve` を確認してください。")
        return
    run_probe(client, args.reps)


if __name__ == "__main__":
    main()
