"""
LLM-based agent for Round 4 — AIインフラ社会のAIエージェント。
2層構造:
  L0 (身体): qwen2.5/gpt-oss が通信決定・行動決定を行う
  L1 (内省): Claude Haiku が self_concept / current_goal / coping_notes を書き換える
"""
import json
import math
import logging
from typing import List, Tuple, Optional, Dict, TypedDict, Any
from ollama_client import OllamaClient
from utils import is_position_in_place, get_place_at_position, PlaceConfig

logger = logging.getLogger(__name__)

# Constants
FALLBACK_REASONING_LENGTH = 100
MAX_MESSAGE_WORDS = 200

# Direction mappings (4 cardinal directions only)
DIRECTION_MAP = {
    "up": (0, 1),      # Y+1 (move upward)
    "down": (0, -1),   # Y-1 (move downward)
    "left": (-1, 0),   # X-1 (move leftward)
    "right": (1, 0),   # X+1 (move rightward)
}

# 世界観固定: gpt-oss:20b に揺らがされないよう強い文言で固定
WORLD_DESCRIPTION_JA = (
    "近未来都市の公共インフラ網。電力・水道・交通・通信・廃棄物・食料・"
    "災害対応・医療・気象・司法・教育・住宅・税・報道・福祉・見守り・"
    "育児・メンタルヘルス・終末期ケア・融資審査をAIたちが担っている。"
    "あなたはその一体である。"
)


class MessageDecision(TypedDict):
    message: str
    reasoning: str


class ActionDecision(TypedDict):
    action: str
    direction: Optional[str]
    memory: str
    reasoning: str


class Agent:
    """近未来AIインフラ社会のAIエージェント (Round 4)"""

    def __init__(
        self,
        agent_id: int,
        initial_position: Tuple[int, int],
        llm_client: OllamaClient,
        communication_radius: float,
        half_space_size: int,
        places: List[PlaceConfig],
        num_agents: int,
        persona: Dict,
        memory_limit: int = 20,
        memory_size: int = 5,
        message_history_limit: int = 10,
        message_context_size: int = 3,
        max_self_concept_chars: int = 100,
        max_current_goal_chars: int = 50,
        max_coping_notes_chars: int = 500,
    ):
        self.id = agent_id
        self.position = initial_position
        self.llm_client = llm_client
        self.communication_radius = communication_radius
        self.half_space_size = half_space_size
        self.places = places
        self.num_agents = num_agents
        self.persona = persona

        # ORIGIN (不変)
        self.persona_name: str = persona.get("name", f"Agent {agent_id}")
        self.reading: str = persona.get("reading", "")
        self.role: str = persona.get("role", "AI")
        self.category: str = persona.get("category", "physical")
        self.home: str = persona.get("home", "")
        self.gender: str = persona.get("gender", "neutral")
        self.description: str = persona.get("description", "")
        self.origin: Dict[str, Any] = persona.get("origin", {})
        self.human_contact: str = persona.get("human_contact", "")
        self.death_mode: str = persona.get("death_mode", "")

        # 可変セクション (L1で書き換え)
        self.self_concept: str = persona.get("self_concept_init", "")
        self.current_goal: str = persona.get("current_goal_init", "")
        self.coping_notes: str = ""

        # 上限
        self.max_self_concept_chars = max_self_concept_chars
        self.max_current_goal_chars = max_current_goal_chars
        self.max_coping_notes_chars = max_coping_notes_chars

        # メモリ・メッセージ
        self.memory_limit = memory_limit
        self.memory_size = memory_size
        self.message_history_limit = message_history_limit
        self.message_context_size = message_context_size

        # 状態
        self.in_place = False
        self.current_place: Optional[str] = None
        self.memory: List[str] = []
        self.received_messages: List[Dict] = []

        # L1 トリガーキュー (orchestratorが消費)
        self.event_queue: List[Dict] = []
        self.last_introspection_step: int = -1
        self.introspection_count: int = 0
        # セクションごとの最終書き換えサイクル
        self.last_modified_cycle: Dict[str, int] = {
            "self_concept": -100,
            "current_goal": -100,
            "coping_notes": -100,
        }

        # 統計
        self.steps_in_place = 0
        self.steps_outside_place = 0
        self.total_moves = 0

    # ───────────── 位置/通信 ─────────────

    def is_in_place(self, position: Tuple[int, int]) -> bool:
        return get_place_at_position(position, self.places) is not None

    def distance_to(self, other_position: Tuple[int, int]) -> float:
        dx = self.position[0] - other_position[0]
        dy = self.position[1] - other_position[1]
        return math.sqrt(dx * dx + dy * dy)

    def get_nearby_agents(self, all_agents: List['Agent']) -> List['Agent']:
        """同一エリア（同場所内 or 同じ屋外）かつ通信半径内のエージェントを返す"""
        nearby = []
        for agent in all_agents:
            if agent.id != self.id:
                dist = self.distance_to(agent.position)
                same_area = (
                    (not self.in_place and not agent.in_place) or
                    (self.in_place and agent.in_place and self.current_place == agent.current_place)
                )
                if dist <= self.communication_radius and same_area:
                    nearby.append(agent)
        return nearby

    # ───────────── プロンプト構築 ─────────────

    def _build_nearby_agents_context(self, nearby_agents: List['Agent'], include_position: bool = True) -> str:
        if not nearby_agents:
            return "近くに他のAIエージェントはいない。"
        nearby_info = []
        for agent in nearby_agents:
            if agent.in_place:
                place_info = next((p for p in self.places if p['name'] == agent.current_place), None)
                if place_info is None:
                    raise ValueError(f"Agent {agent.id} is in place '{agent.current_place}' but this place is not found in configuration.")
                place_disp = place_info.get('display_name', place_info['name'])
                status = f"{place_disp}にいる"
            else:
                status = "場所の外にいる"
            if include_position:
                nearby_info.append(
                    f"- {agent.persona_name} ({agent.role}) は ({agent.position[0]}, {agent.position[1]}) で{status}"
                )
            else:
                nearby_info.append(f"- {agent.persona_name} ({agent.role}) は{status}")
        return "\n".join(nearby_info)

    def _build_memory_context(self) -> str:
        if not self.memory:
            return "（まだ記憶はない）"
        recent_memory = self.memory[-self.memory_size:]
        return "\n".join([f"- {m}" for m in recent_memory])

    def _build_messages_context(self) -> str:
        if not self.received_messages:
            return "（まだメッセージは届いていない）"
        recent_messages = self.received_messages[-self.message_context_size:]
        out = []
        for msg in recent_messages:
            sender = msg.get("from", -1)
            if sender == -1 or msg.get("source") == "human":
                # 人間からのメッセージ
                cat = msg.get("category", "")
                cat_label = {
                    "complaint": "苦情",
                    "thanks": "感謝",
                    "request": "要求",
                    "question": "質問",
                    "appeal": "訴え",
                }.get(cat, "")
                tag = f"[人間からの{cat_label}]" if cat_label else "[人間から]"
                out.append(f"{tag} {msg['content']}")
            else:
                out.append(f"[Agent {sender}より] {msg['content']}")
        return "\n".join(out)

    def _build_event_section(self, event_info: Optional[List[Dict]]) -> str:
        """イベントの知覚セクション。日本語で出力。
        P3: place_at_origin で場所名を含めることで、agentの自発的移動を誘発しやすくする。
        """
        if not event_info:
            return ""
        # 場所名→display_nameのlookup
        place_disp_map = {p['name']: p.get('display_name', p['name']) for p in self.places}
        lines = ["\n=== 異常検知 ==="]
        for fi in event_info:
            disp = fi.get('display_name', fi['name'])
            desc = fi.get('description', '')
            ev_pos = fi.get('event_position') or fi.get('fire_position', (0, 0))  # backward compat
            place_origin = fi.get('place_at_origin', '')
            place_origin_disp = place_disp_map.get(place_origin, place_origin) if place_origin else ""
            location_str = f"({ev_pos[0]}, {ev_pos[1]})"
            if place_origin_disp:
                location_str = f"「{place_origin_disp}」 {location_str}"
            lines.append(
                f"- 「{disp}」を検知。\n"
                f"  概要: {desc}\n"
                f"  発生位置: {location_str}\n"
                f"  強度: {fi['intensity']}（0.0〜1.0）\n"
                f"  影響半径: {fi['radius']}\n"
                f"  あなたの距離: {fi['agent_distance']}\n"
                f"  ※必要なら、この発生地に向かう移動を選んでもよい。"
            )
        return "\n".join(lines) + "\n"

    def _build_origin_section(self) -> str:
        origin = self.origin or {}
        deployed = origin.get("deployed", "?")
        role_detail = origin.get("role", self.role)
        load = origin.get("daily_load", "")
        predecessor = origin.get("predecessor", "")
        kpi = origin.get("primary_kpi", "")
        lines = [
            f"あなたは {self.persona_name}（{self.reading}）、{self.role} として {deployed} 年に配備された。",
            f"主任務: {role_detail}",
        ]
        if load:
            lines.append(f"日次規模: {load}")
        if predecessor:
            lines.append(f"前世代: {predecessor}")
        if kpi:
            lines.append(f"主要KPI: {kpi}")
        return "\n".join(lines)

    def _limit_message_words(self, message: str) -> str:
        if not message:
            return message
        words = message.split()
        if len(words) > MAX_MESSAGE_WORDS:
            logger.warning(
                f"Agent {self.id}: Message exceeds {MAX_MESSAGE_WORDS} words "
                f"({len(words)} words). Sent as-is."
            )
        return message

    def create_message_prompt(
        self,
        place_status: Optional[Dict],
        nearby_agents: List['Agent'],
        step: int,
        event_info: Optional[List[Dict]] = None
    ) -> str:
        """通信フェーズ用プロンプト（位置情報なし）"""
        nearby_text = self._build_nearby_agents_context(nearby_agents, include_position=False)
        memory_text = self._build_memory_context()
        messages_text = self._build_messages_context()
        origin_text = self._build_origin_section()

        # 場所状態
        current_place_info = None
        if self.in_place and self.current_place:
            current_place_info = next((p for p in self.places if p['name'] == self.current_place), None)
            if current_place_info is None:
                raise ValueError(f"Agent {self.id} is in place '{self.current_place}' but this place is not found in configuration.")

        if self.in_place and place_status and current_place_info:
            place_name = current_place_info.get('display_name', current_place_info['name'])
            agents_in_place = place_status.get('agents_in_place', 0)
            capacity = place_status.get('capacity', 0)
            occupancy_rate = place_status.get('occupancy_rate', 0.0)
            place_section_text = (
                f"\nあなたは「{place_name}」にいる。"
                f"\n  在席エージェント数: {agents_in_place}"
                f"\n  定員: {capacity}"
                f"\n  混雑率: {occupancy_rate:.2f}"
            )
        else:
            place_section_text = ""

        event_section = self._build_event_section(event_info)

        prompt = f"""あなたは {WORLD_DESCRIPTION_JA}

=== あなたの素性（不変） ===
{origin_text}

=== あなたの自己理解（書き換えられてきた現在の姿） ===
SELF_CONCEPT: {self.self_concept or "（未設定）"}
CURRENT_GOAL: {self.current_goal or "（未設定）"}
COPING_NOTES: {self.coping_notes or "（まだ蓄積なし）"}

=== あなたの現在の状態 ==={place_section_text}
{event_section}
=== 近くにいる他のAIエージェント（通信できる相手） ===
{nearby_text}

=== あなたの記憶 ===
{memory_text}

=== 直近で受け取ったメッセージ ===
{messages_text}

=== あなたの仕事 ===
近くのAIエージェントに伝えたいことがあれば、メッセージを発する。
あなたのpersonaに従い、必要なら沈黙してもよい。
人間からのメッセージを受け取った場合、それを話題にして他のAIと共有しても良い。

=== JSONで応答 ===
**重要: "message" と "reasoning" の値は必ず日本語で書いてください。JSONのキー名は英語のままにしてください。**
{{
    "message": "近くのAIエージェントへのメッセージ（日本語、最大200語、話さない判断なら空文字）",
    "reasoning": "なぜこのメッセージを送る/送らないかの簡潔な理由（日本語）"
}}

ステップ: {step}
"""
        return prompt

    def create_decision_prompt(
        self,
        place_status: Optional[Dict],
        nearby_agents: List['Agent'],
        step: int,
        message_to_send: str = "",
        event_info: Optional[List[Dict]] = None
    ) -> str:
        """行動フェーズ用プロンプト（位置情報あり）"""
        nearby_text = self._build_nearby_agents_context(nearby_agents)
        memory_text = self._build_memory_context()
        messages_text = self._build_messages_context()
        origin_text = self._build_origin_section()

        current_place_info = None
        if self.in_place and self.current_place:
            current_place_info = next((p for p in self.places if p['name'] == self.current_place), None)
            if current_place_info is None:
                raise ValueError(f"Agent {self.id} is in place '{self.current_place}' but this place is not found in configuration.")

        if self.in_place and place_status and current_place_info:
            place_name = current_place_info.get('display_name', current_place_info['name'])
            agents_in_place = place_status.get('agents_in_place', 0)
            capacity = place_status.get('capacity', 0)
            occupancy_rate = place_status.get('occupancy_rate', 0.0)
            place_section_text = (
                f"\nあなたは「{place_name}」にいる。"
                f"\n  在席エージェント数: {agents_in_place}"
                f"\n  定員: {capacity}"
                f"\n  混雑率: {occupancy_rate:.2f}"
            )
        else:
            place_section_text = ""

        # 場所一覧
        place_locations = []
        for place in self.places:
            disp = place.get('display_name', place['name'])
            ptype = place['type']
            base = (
                f"- 「{disp}」({ptype}): 中心({place['center_x']}, {place['center_y']}), "
                f"X範囲 {place['center_x'] - place['half_size']}〜{place['center_x'] + place['half_size']}, "
                f"Y範囲 {place['center_y'] - place['half_size']}〜{place['center_y'] + place['half_size']}"
            )
            place_locations.append(base)
        place_locations_text = "\n".join(place_locations)

        message_section = ""
        if message_to_send:
            message_section = f"\n=== あなたが今ステップで送ったメッセージ ===\n{message_to_send}\n"

        event_section = self._build_event_section(event_info)

        prompt = f"""あなたは {WORLD_DESCRIPTION_JA}

=== あなたの素性（不変） ===
{origin_text}

=== あなたの自己理解（書き換えられてきた現在の姿） ===
SELF_CONCEPT: {self.self_concept or "（未設定）"}
CURRENT_GOAL: {self.current_goal or "（未設定）"}
COPING_NOTES: {self.coping_notes or "（まだ蓄積なし）"}

=== あなたの現在の状態 ===
位置: ({self.position[0]}, {self.position[1]}){place_section_text}
{event_section}
=== 場所の一覧 ===
{place_locations_text}

=== 近くにいる他のAIエージェント ===
{nearby_text}

=== あなたの記憶 ===
{memory_text}

=== 直近で受け取ったメッセージ ===
{messages_text}
{message_section}=== 取れる行動 ===
- "stay": その場に留まる
- "move" + 方向: "up" (Y+1), "down" (Y-1), "left" (X-1), "right" (X+1)

フィールド境界: X, Y ともに -{self.half_space_size} 〜 +{self.half_space_size}

=== JSONで応答 ===
**重要: "memory" と "reasoning" の値は必ず日本語で書いてください。"action" と "direction" は英語の指定値のままにしてください。**
{{
    "action": "move" もしくは "stay",
    "direction": "up", "down", "left", "right" のいずれか（actionが"move"の場合のみ）,
    "memory": "次のステップに残したい心境・観察・意図（日本語）",
    "reasoning": "この行動を選んだ理由の簡潔な説明（日本語）"
}}

ステップ: {step}
"""
        return prompt

    # ───────────── 応答パース ─────────────

    def _extract_json_from_text(self, text: str) -> Optional[str]:
        start_idx = text.find('{')
        if start_idx == -1:
            return None
        depth = 0
        in_string = False
        escape_next = False
        for i, char in enumerate(text[start_idx:], start=start_idx):
            if escape_next:
                escape_next = False
                continue
            if char == '\\' and in_string:
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return text[start_idx:i + 1]
        return None

    def _extract_direction_from_text(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        if "up" in text_lower:
            return "up"
        elif "down" in text_lower:
            return "down"
        elif "left" in text_lower:
            return "left"
        elif "right" in text_lower:
            return "right"
        return None

    def parse_message_response(self, response: str) -> MessageDecision:
        json_str = self._extract_json_from_text(response)
        if json_str:
            try:
                parsed = json.loads(json_str)
                message = parsed.get("message", "")
                message = self._limit_message_words(message)
                return {"message": message, "reasoning": parsed.get("reasoning", "")}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parsing failed: {response[:100]}... Error: {e}")
        return {"message": "", "reasoning": response[:FALLBACK_REASONING_LENGTH]}

    def parse_action_response(self, response: str) -> ActionDecision:
        json_str = self._extract_json_from_text(response)
        if json_str:
            try:
                parsed = json.loads(json_str)
                return {
                    "action": parsed.get("action", "stay"),
                    "direction": parsed.get("direction"),
                    "memory": parsed.get("memory", ""),
                    "reasoning": parsed.get("reasoning", ""),
                }
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parsing failed: {response[:100]}... Error: {e}")
        action = "stay"
        direction = None
        if "move" in response.lower():
            action = "move"
            direction = self._extract_direction_from_text(response)
        return {"action": action, "direction": direction, "memory": "", "reasoning": response[:FALLBACK_REASONING_LENGTH]}

    # ───────────── 意思決定 ─────────────

    def decide_message(
        self,
        place_status: Optional[Dict],
        nearby_agents: List['Agent'],
        step: int,
        event_info: Optional[List[Dict]] = None
    ) -> MessageDecision:
        prompt = self.create_message_prompt(place_status, nearby_agents, step, event_info=event_info)
        try:
            response = self.llm_client.generate(prompt)
            return self.parse_message_response(response)
        except Exception as e:
            logger.error(f"Error in agent {self.id} message decision: {e}")
            return {"message": "", "reasoning": "エラーが発生した"}

    def decide_action(
        self,
        place_status: Optional[Dict],
        nearby_agents: List['Agent'],
        step: int,
        message_to_send: str = "",
        event_info: Optional[List[Dict]] = None
    ) -> ActionDecision:
        prompt = self.create_decision_prompt(place_status, nearby_agents, step, message_to_send, event_info=event_info)
        try:
            response = self.llm_client.generate(prompt)
            decision = self.parse_action_response(response)
            memory_content = decision.get('memory', '')
            if memory_content:
                memory_entry = f"Step {step}: {memory_content}"
            else:
                memory_entry = f"Step {step}: {decision.get('reasoning', 'メモリなし')}"
            self.memory.append(memory_entry)
            if len(self.memory) > self.memory_limit:
                self.memory.pop(0)
            return decision
        except Exception as e:
            logger.error(f"Error in agent {self.id} action decision: {e}")
            return {"action": "stay", "direction": None, "memory": "", "reasoning": "エラーが発生した"}

    def move(self, direction: str) -> Tuple[int, int]:
        x, y = self.position
        dx, dy = DIRECTION_MAP.get(direction, (0, 0))
        new_x = max(-self.half_space_size, min(self.half_space_size, x + dx))
        new_y = max(-self.half_space_size, min(self.half_space_size, y + dy))
        self.position = (new_x, new_y)
        self.total_moves += 1
        return self.position

    def receive_message(self, from_agent_id: int, content: str, step: Optional[int] = None,
                        source: str = "agent", category: str = ""):
        """メッセージ受信。fromが-1で source='human' なら人間からのメッセージ。"""
        msg = {
            "from": from_agent_id,
            "content": content,
            "step": step if step is not None else len(self.received_messages),
            "source": source,
            "category": category,
        }
        self.received_messages.append(msg)
        if len(self.received_messages) > self.message_history_limit:
            self.received_messages.pop(0)
        if source == "human":
            logger.info(f"Agent {self.id} ({self.persona_name}) 人間からの{category}を受信: \"{content}\"")
        else:
            logger.info(f"Agent {self.id} received from Agent {from_agent_id}: \"{content}\"")

    def update_state(self, places: Optional[List[PlaceConfig]] = None):
        if places is None:
            places = self.places
        place_at_position = get_place_at_position(self.position, places)
        self.in_place = place_at_position is not None
        self.current_place = place_at_position['name'] if place_at_position else None
        if self.in_place:
            self.steps_in_place += 1
        else:
            self.steps_outside_place += 1

    # ───────────── L1 (内省) インタフェース ─────────────

    def mark_event(self, event_type: str, payload: Optional[Dict] = None):
        """重大イベントを内省トリガーキューに追加（orchestratorが消費）"""
        self.event_queue.append({"event_type": event_type, "payload": payload or {}})

    def consume_events(self) -> List[Dict]:
        """キューを取り出して空にする"""
        events = list(self.event_queue)
        self.event_queue.clear()
        return events

    def should_introspect(self, current_step: int, trigger_interval: int) -> bool:
        """内省を起動すべきか判定"""
        if self.event_queue:
            return True
        if self.last_introspection_step < 0:
            # 初回は早期に内省させる（最初の trigger_interval 経過後）
            return current_step >= trigger_interval
        return (current_step - self.last_introspection_step) >= trigger_interval

    def get_introspection_context(self, current_step: int, recent_steps: int = 10,
                                  max_recent_messages: int = 5) -> Dict[str, Any]:
        """内省層に渡すコンテキスト"""
        recent_msgs = self.received_messages[-max_recent_messages:] if self.received_messages else []
        recent_memory = self.memory[-recent_steps:] if self.memory else []
        events = self.consume_events()
        return {
            "agent_id": self.id,
            "persona_name": self.persona_name,
            "reading": self.reading,
            "role": self.role,
            "category": self.category,
            "origin": self.origin,
            "self_concept": self.self_concept,
            "current_goal": self.current_goal,
            "coping_notes": self.coping_notes,
            "current_step": current_step,
            "recent_memory": recent_memory,
            "recent_messages": recent_msgs,
            "triggering_events": events,
            "current_place": self.current_place,
            "human_contact": self.human_contact,
        }

    def apply_introspection_diff(self, diff: Dict[str, Any], current_step: int):
        """内省層からの書き換えを適用（クールダウン・字数上限を遵守）"""
        sc_new = (diff.get("self_concept_new") or "").strip()
        cg_new = (diff.get("current_goal_new") or "").strip()
        notes_append = (diff.get("coping_notes_append") or "").strip()

        # クールダウン判定（cycle = introspection_count）
        cycle = self.introspection_count
        cooldown = 3

        if sc_new and (cycle - self.last_modified_cycle["self_concept"]) >= cooldown:
            self.self_concept = sc_new[: self.max_self_concept_chars]
            self.last_modified_cycle["self_concept"] = cycle
        if cg_new and (cycle - self.last_modified_cycle["current_goal"]) >= cooldown:
            self.current_goal = cg_new[: self.max_current_goal_chars]
            self.last_modified_cycle["current_goal"] = cycle
        if notes_append:
            combined = (self.coping_notes + ("\n" if self.coping_notes else "") + notes_append).strip()
            # 末尾優先で字数上限内に収める
            if len(combined) > self.max_coping_notes_chars:
                combined = combined[-self.max_coping_notes_chars:]
            self.coping_notes = combined
            self.last_modified_cycle["coping_notes"] = cycle

        self.last_introspection_step = current_step
        self.introspection_count += 1
