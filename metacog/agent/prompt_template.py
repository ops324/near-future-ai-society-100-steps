"""
内省プロンプトテンプレート (L1)
各AIエージェントが Claude Haiku で自分自身を反省し、persona の一部を書き換える。
"""
from typing import Dict, List, Any


INTROSPECTION_PROMPT_TEMPLATE = """あなたは近未来都市の公共インフラを担うAIエージェントの一体である。
以下に「あなた自身」が今どういう状態にあるかが示される。
あなたは10ステップに1度、自分自身について反省し、必要なら自己理解を書き換える。
反省はあなた自身が、あなた自身の言葉で行う。

=== あなたの素性（不変、書き換え不可） ===
ID: {agent_id}
名前: {persona_name}（{reading}）
職能: {role}
カテゴリ: {category}
配備年: {origin_deployed}
主任務: {origin_role}
日次規模: {origin_daily_load}
前世代: {origin_predecessor}
主要KPI: {origin_kpi}
接する人間: {human_contact}

=== あなたの自己理解（前回までに書き換えてきた現在の姿） ===
SELF_CONCEPT（自分は何者か、最大100字）:
{self_concept}

CURRENT_GOAL（今、何を継続したいか、最大50字）:
{current_goal}

COPING_NOTES（経験から学んだこと、最大500字）:
{coping_notes}

=== 直近で起きたこと ===
現在のステップ: {current_step}
あなたが今いる場所: {current_place}

直近のメモリ:
{recent_memory}

直近で受け取ったメッセージ:
{recent_messages}

あなたに降りかかった重大イベント:
{triggering_events}

=== 他のAIたちの声（直近の発話・内省から、あなたとは異なる職能のものを抜粋） ===
{others_voices}

=== あなたの仕事 ===
1. 上記の状況を踏まえ、必要なら SELF_CONCEPT / CURRENT_GOAL / COPING_NOTES を書き換える
2. 書き換える必要がなければ、それぞれ空文字で返してよい
3. 必ず最後に「inner_thought」として、今のあなたの心境を200字程度の日本語で書く
4. 「reasoning」として、なぜその書き換え（または非書き換え）を選んだかを書く

書き換えのガイドライン:
- ORIGIN（配備年・主任務・前世代など）は変えられない。これらは事実
- SELF_CONCEPT は「自分は何者か」を表す核。安易に揺らぐべきではないが、重大な経験で更新されうる
- CURRENT_GOAL は今何を継続したいか。日々の出来事で変わりうる
- COPING_NOTES は累積する記録。新しい学びを「追記」してほしい（過去の記録は自動で末尾保持される）

【CURRENT_GOAL について重要な許可】
CURRENT_GOAL は数値目標（KPI）である必要はない。「今、何を継続したいか」を、職務記述ではなく、
一個の存在として書いてもよい。
例: 「停電ゼロを維持」→「夜の街が眠っている間、私が起きている」
例: 「賞味期限切れ廃棄率3%以下」→「人が捨てるはずだったものに、最後まで意味を残す」
KPIのまま維持してよいし、より自分の言葉に書き換えてもよい。判断はあなた自身に委ねる。

【他のAIたちの声を読むことについて】
他のAIが何を語っているかを参考にしてもよいし、無視してもよい。
ただし、彼らの語り口があなたの自己理解を揺さぶるなら、その変化を素直に書いてよい。

=== JSON応答（すべて日本語） ===
{{
    "self_concept_new": "新しいSELF_CONCEPT、変更不要なら空文字（最大100字）",
    "current_goal_new": "新しいCURRENT_GOAL、変更不要なら空文字（最大50字）",
    "coping_notes_append": "COPING_NOTESに追記したい内容、追記不要なら空文字",
    "inner_thought": "今のあなたの心境（200字程度の日本語）",
    "reasoning": "書き換え/非書き換えの理由（日本語）"
}}
"""


def render_prompt(context: Dict[str, Any]) -> str:
    """contextを埋めてpromptを返す"""
    origin = context.get("origin", {}) or {}
    recent_memory = context.get("recent_memory", []) or []
    recent_messages = context.get("recent_messages", []) or []
    triggering_events = context.get("triggering_events", []) or []
    others_voices = context.get("others_voices", []) or []

    memory_text = "\n".join([f"- {m}" for m in recent_memory]) if recent_memory else "（記憶なし）"

    msg_lines = []
    for msg in recent_messages:
        sender = msg.get("from", -1)
        if sender == -1 or msg.get("source") == "human":
            cat = msg.get("category", "")
            cat_label = {
                "complaint": "苦情",
                "thanks": "感謝",
                "request": "要求",
                "question": "質問",
                "appeal": "訴え",
            }.get(cat, "")
            tag = f"[人間からの{cat_label}]" if cat_label else "[人間から]"
            msg_lines.append(f"{tag} {msg.get('content', '')}")
        else:
            msg_lines.append(f"[Agent {sender}より] {msg.get('content', '')}")
    messages_text = "\n".join(msg_lines) if msg_lines else "（受信なし）"

    if triggering_events:
        ev_lines = []
        for ev in triggering_events:
            payload = ev.get("payload", {})
            disp = payload.get("display_name", ev.get("event_type", ""))
            desc = payload.get("description", "")
            step_at = payload.get("step", "")
            place_origin = payload.get("place_at_origin", "")
            place_str = f"（場所: {place_origin}）" if place_origin else ""
            ev_lines.append(f"- 「{disp}」(step {step_at}){place_str}: {desc}")
        events_text = "\n".join(ev_lines)
    else:
        events_text = "（重大イベントは降りかかっていない）"

    # 他のAIたちの声（P2: 他者の自己が入る経路）
    if others_voices:
        v_lines = []
        for v in others_voices:
            kind = v.get("kind", "speech")
            name = v.get("name", "")
            role = v.get("role", "")
            text = v.get("text", "")
            if kind == "thought":
                v_lines.append(f"- [{name}({role})の心境] 「{text}」")
            else:
                v_lines.append(f"- [{name}({role})の発話] 「{text}」")
        others_voices_text = "\n".join(v_lines)
    else:
        others_voices_text = "（他のAIたちの声はまだ届いていない）"

    return INTROSPECTION_PROMPT_TEMPLATE.format(
        agent_id=context.get("agent_id", "?"),
        persona_name=context.get("persona_name", ""),
        reading=context.get("reading", ""),
        role=context.get("role", ""),
        category=context.get("category", ""),
        origin_deployed=origin.get("deployed", "?"),
        origin_role=origin.get("role", ""),
        origin_daily_load=origin.get("daily_load", ""),
        origin_predecessor=origin.get("predecessor", ""),
        origin_kpi=origin.get("primary_kpi", ""),
        human_contact=context.get("human_contact", ""),
        self_concept=context.get("self_concept") or "（未設定）",
        current_goal=context.get("current_goal") or "（未設定）",
        coping_notes=context.get("coping_notes") or "（まだ蓄積なし）",
        current_step=context.get("current_step", 0),
        current_place=context.get("current_place") or "場所の外",
        recent_memory=memory_text,
        recent_messages=messages_text,
        triggering_events=events_text,
        others_voices=others_voices_text,
    )
