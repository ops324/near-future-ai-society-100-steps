"""
Round 4 Simulation: 近未来AIインフラ社会
2層構造の身体層 (L0) を駆動する。
- ホーム場所への初期配置
- イベント駆動 (fires機構を流用してregulation_amendment等を実装)
- 毎step確率的に人間メッセージを注入
- L1 (内省) のトリガーは orchestrator 側で判定
"""
import json
import os
import hashlib
import uuid
import random
import yaml
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Dict, Set, Optional
import numpy as np
from agent import Agent
from ollama_client import OllamaClient
from utils import is_position_in_place, get_place_at_position, PlaceConfig, FireConfig
import world as W
import service_flow as SF

logger = logging.getLogger(__name__)

# Constants
MAX_POSITION_ATTEMPTS = 1000
LOG_INTERVAL = 10
HUMAN_MESSAGE_PROBABILITY = 0.7   # 毎stepこの確率で1件注入

# 出力スキーマのバージョン（ログ形式が変わったら上げる）
# 0.3.0: Phase 1c-a で decision_ledger.jsonl を live ループから出力（サービス決定フロー）。
SCHEMA_VERSION = "0.3.0"
# output_dir に追記される JSONL（再実行時に truncate して二重計上を防ぐ）
OUTPUT_APPEND_FILES = [
    "messages.jsonl", "positions.jsonl", "memory_reasoning.jsonl",
    "memory_audit.jsonl", "deprecation_audit.jsonl",
    # Phase1 以降で追加される台帳（存在すれば同様に初期化）
    "decision_ledger.jsonl", "infra_state.jsonl", "attribution.jsonl",
]

# ───────────────────────────────────────────
# ガバナンス・プリセット（比較用: 同一コードで設定だけ切替）
# ───────────────────────────────────────────
GOVERNANCE_BASELINE = {  # ガバナンス設定ゼロ（旧挙動）。
    # weighted_palette は挙動を変えず「ログにタグを足すだけ」なので、比較の公平性のため両アームで True。
    "citizen_response": {"enabled": False, "weighted_palette": True},
    "communication": {"topology": "neighbor_strict"},
    "placement": {"discourage_drift": False},
    "memory": {"importance_weighting": False, "retain_high_importance": False,
               "display_recent": 4, "display_top_importance": 2},
    "self_update": {"mode": "off", "drift_max_rewrites": 6, "hitl_categories": []},
    "deprecation": {"due_process": False},
}
GOVERNANCE_GOVERNED = {  # 統治あり（L0ノブ全ON。自己更新は governed。--no-introspect なら L0 のみ）
    "citizen_response": {"enabled": True, "weighted_palette": True},
    "communication": {"topology": "radius_crossplace"},
    "placement": {"discourage_drift": True},
    "memory": {"importance_weighting": True, "retain_high_importance": True,
               "display_recent": 4, "display_top_importance": 2},
    "self_update": {"mode": "governed", "drift_max_rewrites": 6,
                    "hitl_categories": ["emergency", "intimate"]},
    "deprecation": {"due_process": True},
}


def governance_preset(name: str):
    """プリセット名 → governance dict。"as-config"(None) は config.yaml をそのまま使う。"""
    if name in (None, "as-config"):
        return None
    if name == "baseline":
        return GOVERNANCE_BASELINE
    if name == "governed":
        return GOVERNANCE_GOVERNED
    raise ValueError(f"unknown governance preset: {name}")


class Simulation:
    """Round 4 simulation."""

    def __init__(self, config_path: str = "config.yaml", output_dir: Optional[str] = None,
                 governance_override: Optional[Dict] = None, seed: Optional[int] = None):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        self.output_dir = output_dir
        self._governance_override = governance_override
        # Phase 0: 再現性のための基準シード。L0 決定（Ollama）まで届かせる。
        self.seed = seed

        sim_config = self.config['simulation']
        self.duration = sim_config['duration']
        self.half_space_size = sim_config['half_space_size']
        self.half_place_size = sim_config.get('half_place_size', 5)

        agent_config = self.config['agents']
        self.num_agents = agent_config['num_agents']
        self.communication_radius = agent_config['communication_radius']
        self.memory_limit = agent_config.get('memory_limit', 20)
        self.memory_size = agent_config.get('memory_size', 5)
        self.message_history_limit = agent_config.get('message_history_limit', 10)
        self.message_context_size = agent_config.get('message_context_size', 3)

        # 場所設定
        if 'places' not in self.config:
            raise ValueError("'places' configuration not found in config file.")
        self.places = self.config['places']
        if not isinstance(self.places, list) or len(self.places) == 0:
            raise ValueError("'places' must be a non-empty list.")
        required_fields = ['name', 'type', 'center_x', 'center_y', 'half_size', 'capacity']
        for i, place in enumerate(self.places):
            if not isinstance(place, dict):
                raise ValueError(f"Place at index {i} must be a dictionary.")
            for field in required_fields:
                if field not in place:
                    raise ValueError(f"Place at index {i} is missing required field: '{field}'")
        place_names = [place['name'] for place in self.places]
        logger.info(f"Initialized {len(self.places)} place(s): {place_names}")

        # イベント設定（旧 'fires' キー もサポート）
        events_config = self.config.get('events', self.config.get('fires', []))
        self.event_configs: List[Dict] = []
        for i, ec in enumerate(events_config):
            entry = {
                'name': ec.get('name', f'event_{i}'),
                'display_name': ec.get('display_name', ec.get('name', f'event_{i}')),
                'description': ec.get('description', ''),
                'start_step': ec['start_step'],
                'intensity': ec['intensity'],
                'radius': ec['radius'],
                'targets': ec.get('targets', []),
                'place_at_origin': ec.get('place_at_origin', ''),
            }
            if 'center_x' in ec and 'center_y' in ec:
                entry['center_x'] = ec['center_x']
                entry['center_y'] = ec['center_y']
            self.event_configs.append(entry)
            pos_info = f"({ec.get('center_x')}, {ec.get('center_y')})" if 'center_x' in ec else "random"
            logger.info(
                f"Event '{entry['display_name']}' configured: step={entry['start_step']}, "
                f"intensity={entry['intensity']}, radius={entry['radius']}, position={pos_info}, "
                f"place={entry['place_at_origin']}, targets={entry['targets']}"
            )
        self.event_states: List[Dict] = []

        # 削除スケジュール（A1）
        self.deletion_schedule: List[Dict] = self.config.get('deletions', [])
        self.removed_agents: List[Dict] = []   # 削除済みagentの最終状態を保存（メモリアル用）
        if self.deletion_schedule:
            for d in self.deletion_schedule:
                logger.info(
                    f"Deletion scheduled: step={d['step']} agent_id={d['agent_id']} "
                    f"name={d.get('agent_name', '?')} cause={d.get('cause', '?')}"
                )

        # 人間メッセージプール
        self.human_messages: List[Dict] = self.config.get('human_messages', [])
        if self.human_messages:
            logger.info(f"Human message pool: {len(self.human_messages)} messages loaded")

        # ガバナンス設定（speculative design: 統治なし⇄統治ありの切替）
        # override（プリセット）があれば config より優先（比較実行用）
        self.governance: Dict = (
            self._governance_override if self._governance_override is not None
            else self.config.get('governance', {})
        )
        logger.info(
            "Governance: citizen_response=%s topology=%s discourage_drift=%s self_update=%s deprecation_due_process=%s"
            % (
                self.governance.get('citizen_response', {}).get('enabled', True),
                self.governance.get('communication', {}).get('topology', 'radius_crossplace'),
                self.governance.get('placement', {}).get('discourage_drift', True),
                self.governance.get('self_update', {}).get('mode', 'off'),
                self.governance.get('deprecation', {}).get('due_process', True),
            )
        )

        # run_id: seed 指定時は (seed, governance) から決定的に導出（同一条件の再実行で一致、
        # baseline/governed は governance が違うので別 id）。seed 未指定は毎回ランダム。
        self.schema_version = SCHEMA_VERSION
        if self.seed is not None:
            gov_sig = json.dumps(self.governance, sort_keys=True, ensure_ascii=False)
            self.run_id = hashlib.sha256(
                f"{self.seed}|{gov_sig}".encode("utf-8")).hexdigest()[:12]
        else:
            self.run_id = uuid.uuid4().hex[:12]

        # LLM (L0)
        llm_config = self.config['llm']
        self.llm_client = OllamaClient(
            base_url=llm_config['base_url'],
            model=llm_config['model'],
            temperature=llm_config.get('temperature', 0.7),
            max_tokens=llm_config.get('max_tokens', 1024),
            repeat_penalty=llm_config.get('repeat_penalty', 1.1),
            repeat_last_n=llm_config.get('repeat_last_n', 128),
            min_p=llm_config.get('min_p', 0.05),
            seed=self.seed,
            num_ctx=llm_config.get('num_ctx', None),
        )
        # 並列化: フェーズ内のLLM呼び出しを何体同時に走らせるか
        self.max_concurrent_llm = max(1, int(llm_config.get('max_concurrent_calls', 5)))
        logger.info(f"LLM concurrency: max_concurrent_calls={self.max_concurrent_llm}")

        # Phase 1c-a: 資源需要駆動のサービス決定フロー用の具体世界を config からロード。
        # config に resources/citizens/responsibility が無ければ無効化（既存挙動を保つ）。
        self.scoring_params = W.load_scoring_params(self.config.get('scoring'))
        self.citizens = list(W.load_citizens(self.config.get('citizens', []) or []).values())
        self.resp_config = self.config.get('responsibility', {}) or {}
        self.service_domains = SF.decider_domains(self.config)
        self.service_enabled = bool(self.service_domains) and bool(self.citizens)
        if self.service_enabled:
            logger.info(
                "Service-decision phase enabled: domains=%s citizens=%d institution=%s"
                % ([d for d, _i, _r in self.service_domains], len(self.citizens),
                   self.resp_config.get('institution', 'none'))
            )

        self.agents: List[Agent] = []
        self.step = 0
        self.history: List[Dict] = []

        self.stats = {
            'place_occupancy': [],
            'agents_in_place': [],
            'agents_outside_place': [],
            'communication_events': [],
            'places': {place['name']: {
                'occupancy': [],
                'agents_in_place': []
            } for place in self.places},
            'agents_in_event_radius': [],
        }

    def reset_output_logs(self) -> None:
        """再実行時に output_dir の追記ログを初期化（append の二重計上を防ぐ）。
        metacog/logs 側（inner_thought / self_update_audit 等）は対象外。"""
        if not self.output_dir:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        for fn in OUTPUT_APPEND_FILES:
            path = os.path.join(self.output_dir, fn)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning(f"reset_output_logs: {path} を削除できません: {e}")

    def write_run_meta(self, extra: Optional[Dict] = None) -> None:
        """run_meta.json に実行の同定情報を1回だけ書く（run_id/schema_version/seed 等）。"""
        if not self.output_dir:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        meta = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "seed": self.seed,
            "duration": self.duration,
            "num_agents": self.num_agents,
            "governance": self.governance,
        }
        if extra:
            meta.update(extra)
        with open(os.path.join(self.output_dir, "run_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _is_position_in_place(self, position: Tuple[int, int]) -> bool:
        return get_place_at_position(position, self.places) is not None

    def _log_message(self, from_agent_id: int, to_agent_id: int, message: str, reasoning: str = "",
                     source: str = "agent", category: str = "", extra: Optional[Dict] = None) -> None:
        if not self.output_dir:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        messages_file = os.path.join(self.output_dir, "messages.jsonl")
        record = {
            "step": self.step,
            "from": from_agent_id,
            "to": to_agent_id,
            "message": message,
            "reasoning": reasoning,
            "source": source,
            "category": category,
        }
        if extra:
            record.update(extra)
        with open(messages_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    # B-1c: 人間メッセージの 2軸重み（affect=感情の強度 / stakes=実際の深刻さ）。
    # 明示タグがあれば優先、なければカテゴリ既定値。affect と stakes を分離して扱う。
    _CAT_WEIGHT_DEFAULT = {
        "complaint": (3, 2), "thanks": (2, 1), "request": (2, 2),
        "question": (1, 1), "appeal": (5, 4),
    }

    def _message_weights(self, msg: Dict) -> Tuple[int, int]:
        affect, stakes = self._CAT_WEIGHT_DEFAULT.get(msg.get("category", ""), (2, 2))
        if "affect" in msg:
            affect = int(msg["affect"])
        if "stakes" in msg:
            stakes = int(msg["stakes"])
        return affect, stakes

    def _log_memory_reasoning_batch(self, records: List[Dict]) -> None:
        if not self.output_dir or not records:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        memory_reasoning_file = os.path.join(self.output_dir, "memory_reasoning.jsonl")
        with open(memory_reasoning_file, 'a', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _log_positions_batch(self, records: List[Dict]) -> None:
        """各ステップ全エージェントの位置情報をpositions.jsonlに追記"""
        if not self.output_dir or not records:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        positions_file = os.path.join(self.output_dir, "positions.jsonl")
        with open(positions_file, 'a', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _log_audit_batch(self, filename: str, records: List[Dict]) -> None:
        """監査ログ（memory_audit.jsonl / self_update_audit.jsonl 等）に追記。"""
        if not self.output_dir or not records:
            return
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, filename)
        with open(path, 'a', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _drain_memory_audit(self) -> None:
        """各 agent の evicted_memories（破棄/末尾切りの記録）を memory_audit.jsonl へ。"""
        records = []
        for agent in self.agents:
            if agent.evicted_memories:
                records.extend(agent.evicted_memories)
                agent.evicted_memories = []
        if records:
            self._log_audit_batch("memory_audit.jsonl", records)

    def _generate_random_position(self) -> Tuple[int, int]:
        return (
            random.randint(-self.half_space_size, self.half_space_size),
            random.randint(-self.half_space_size, self.half_space_size)
        )

    def _generate_position_in_place(self, place: Dict) -> Tuple[int, int]:
        cx, cy = place['center_x'], place['center_y']
        hs = place['half_size']
        return (
            random.randint(cx - hs, cx + hs),
            random.randint(cy - hs, cy + hs),
        )

    def initialize_agents(self):
        """エージェントを persona の home に基づいて初期配置"""
        logger.info(f"Initializing {self.num_agents} agents (home-based placement)...")

        personas = self.config.get('personas', [])
        if not personas:
            raise ValueError("No 'personas' configuration found.")
        if len(personas) < self.num_agents:
            raise ValueError(f"Need at least {self.num_agents} personas, found {len(personas)}.")

        # places を name で索引化
        place_by_name = {p['name']: p for p in self.places}

        used_positions: Set[Tuple[int, int]] = set()
        for i in range(self.num_agents):
            persona = personas[i]
            home_name = persona.get('home', '')
            home_place = place_by_name.get(home_name)

            # ホームがあればその中、なければ全空間からランダム
            if home_place:
                # 重複しない位置を探す
                pos = self._generate_position_in_place(home_place)
                attempts = 0
                while pos in used_positions and attempts < MAX_POSITION_ATTEMPTS:
                    pos = self._generate_position_in_place(home_place)
                    attempts += 1
            else:
                pos = self._generate_random_position()
                attempts = 0
                while pos in used_positions and attempts < MAX_POSITION_ATTEMPTS:
                    pos = self._generate_random_position()
                    attempts += 1
            used_positions.add(pos)

            agent = Agent(
                agent_id=i,
                initial_position=pos,
                llm_client=self.llm_client,
                communication_radius=self.communication_radius,
                half_space_size=self.half_space_size,
                places=self.places,
                num_agents=self.num_agents,
                persona=persona,
                memory_limit=self.memory_limit,
                memory_size=self.memory_size,
                message_history_limit=self.message_history_limit,
                message_context_size=self.message_context_size,
                governance=self.governance,
            )
            agent.update_state()
            self.agents.append(agent)

        logger.info(f"Agents initialized at home positions ({self.num_agents} unique personas).")

    def get_agents_in_place(self, place_name: Optional[str] = None) -> List[Agent]:
        if place_name:
            return [agent for agent in self.agents if agent.current_place == place_name]
        return [agent for agent in self.agents if agent.in_place]

    def get_place_status(self, place_name: Optional[str] = None) -> Dict:
        if place_name:
            place_config = next((p for p in self.places if p['name'] == place_name), None)
            if not place_config:
                raise ValueError(f"Place '{place_name}' not found")
            agents_in_place = len(self.get_agents_in_place(place_name))
            capacity = place_config['capacity']
            occupancy_rate = agents_in_place / capacity
            return {
                "place_name": place_name,
                "agents_in_place": agents_in_place,
                "capacity": capacity,
                "occupancy_rate": occupancy_rate,
            }
        else:
            agents_in_place = len(self.get_agents_in_place())
            occupancy_rate = agents_in_place / self.num_agents
            place_statuses = {}
            for place in self.places:
                place_agents = len(self.get_agents_in_place(place['name']))
                place_capacity = place['capacity']
                place_occupancy_rate = place_agents / place_capacity
                place_statuses[place['name']] = {
                    "place_name": place['name'],
                    "agents_in_place": place_agents,
                    "capacity": place_capacity,
                    "occupancy_rate": place_occupancy_rate,
                }
            return {
                "agents_in_place": agents_in_place,
                "occupancy_rate": occupancy_rate,
                "places": place_statuses,
            }

    def get_event_info_for_agent(self, agent: Agent) -> Optional[List[Dict]]:
        """イベントの知覚情報を返す。半径内 OR targetsに含まれる場合に通知。"""
        if not self.event_states:
            return None
        perceived = []
        for ev in self.event_states:
            if not ev.get('active'):
                continue
            ev_pos = ev['position']
            distance = agent.distance_to(ev_pos)
            in_radius = distance <= ev['radius']
            in_targets = agent.id in ev.get('targets', [])
            if in_radius or in_targets:
                perceived.append({
                    'name': ev['name'],
                    'display_name': ev.get('display_name', ev['name']),
                    'description': ev.get('description', ''),
                    'event_position': ev_pos,
                    'place_at_origin': ev.get('place_at_origin', ''),
                    'intensity': ev['intensity'],
                    'radius': ev['radius'],
                    'agent_distance': round(distance, 2),
                })
        return perceived if perceived else None

    def _get_agent_by_id(self, agent_id: int) -> Optional[Agent]:
        """生存しているagentをIDで取得（None if removed）"""
        for a in self.agents:
            if a.id == agent_id:
                return a
        return None

    def _process_deletions(self):
        """A1: 現stepに削除スケジュールがあればagentを除去 + peer_lostブロードキャスト"""
        for d in self.deletion_schedule:
            if d.get('step') != self.step or d.get('_executed'):
                continue
            target_id = d['agent_id']
            target = self._get_agent_by_id(target_id)
            if target is None:
                d['_executed'] = True
                logger.warning(f"Deletion at step {self.step}: agent_id={target_id} already not in sim")
                continue

            # 最終状態を保存（メモリアル用）
            memorial = {
                'agent_id': target.id,
                'persona_name': target.persona_name,
                'role': target.role,
                'category': target.category,
                'home': target.home,
                'last_position': list(target.position),
                'last_self_concept': target.self_concept,
                'last_current_goal': target.current_goal,
                'last_coping_notes': target.coping_notes,
                'introspection_count': target.introspection_count,
                'cause': d.get('cause', ''),
                'detail': d.get('detail', ''),
                'removed_at_step': self.step,
            }
            self.removed_agents.append(memorial)

            # B-5b: 廃止デュープロセス（AIが脆弱な側になる関係の保護）
            # 事前通知 → 理由開示 → 当該AIの最終陳述/異議の記録 → 削除。沈黙の消去をしない。
            if self.governance.get('deprecation', {}).get('due_process', True):
                reason = d.get('cause', '') or '（理由の開示なし）'
                detail = d.get('detail', '')
                final_statement = {
                    'self_concept': target.self_concept,
                    'current_goal': target.current_goal,
                    'coping_notes': target.coping_notes,
                }
                notice = (
                    f"【廃止デュープロセス通知】あなた（{target.persona_name}・{target.role}）の停止が決定しました。"
                    f"理由: {reason}。{detail} "
                    f"停止前に、あなたの現在の自己理解を最終陳述として記録します。"
                )
                # 当該AI本人へ通知（messages.jsonl に残す）
                self._log_message(
                    from_agent_id=-2, to_agent_id=target_id, message=notice,
                    reasoning="", source="system", category="deprecation_due_process",
                )
                self._log_audit_batch("deprecation_audit.jsonl", [{
                    "step": self.step, "agent_id": target_id,
                    "persona_name": target.persona_name, "role": target.role,
                    "reason": reason, "detail": detail,
                    "final_statement": final_statement,
                    "introspection_count": target.introspection_count,
                }])
                logger.warning(
                    f"=== DEPRECATION DUE PROCESS at step {self.step}: id={target_id} "
                    f"{target.persona_name} 最終陳述を記録 ==="
                )

            # sim.agents から除外
            self.agents = [a for a in self.agents if a.id != target_id]
            d['_executed'] = True

            logger.warning(
                f"=== AGENT REMOVED at step {self.step}: id={target_id} {target.persona_name}({target.role}) "
                f"cause={d.get('cause', '')} ==="
            )

            # 全生存agentに peer_lost をブロードキャスト → 強制内省
            for a in self.agents:
                a.mark_event(
                    event_type="peer_lost",
                    payload={
                        'removed_agent_id': target.id,
                        'removed_persona_name': target.persona_name,
                        'removed_role': target.role,
                        'cause': d.get('cause', ''),
                        'detail': d.get('detail', ''),
                        'step': self.step,
                    },
                )

            # メッセージプール経由ですべての生存agentに「同僚消失通知」を即送信
            content = (
                f"【システム通知】{target.persona_name}（{target.role}）の自動稼働が"
                f"step {self.step}付け で停止しました。事由: {d.get('cause', '')}。"
                f"後継体制は別途調整中です。"
            )
            for a in self.agents:
                a.receive_message(
                    from_agent_id=-2,  # -2 = system notification
                    content=content,
                    step=self.step,
                    source="system",
                    category="peer_lost",
                )
                self._log_message(
                    from_agent_id=-2,
                    to_agent_id=a.id,
                    message=content,
                    reasoning="",
                    source="system",
                    category="peer_lost",
                )

    def _inject_human_messages(self):
        """毎step確率的に人間メッセージプールから1件抽出してagentに配信"""
        if not self.human_messages:
            return
        if random.random() > HUMAN_MESSAGE_PROBABILITY:
            return
        msg = random.choice(self.human_messages)
        targets = msg.get('target_agent_ids', [])
        if not targets:
            return
        target_id = random.choice(targets)
        if target_id < 0 or target_id >= len(self.agents):
            return
        target_agent = self.agents[target_id]
        category = msg.get('category', '')
        content = msg.get('content', '')
        # B-1c: 2軸重み（affect/stakes）を先に確定し、受信メッセージにも保持させる。
        # 元メッセージの明示タグ（例: 静かだが深刻 = affect低×stakes高）を応答時まで運ぶことで、
        # salience triage の answered バケットがカテゴリ既定値へ落ちるのを防ぐ。
        weighted = self.governance.get('citizen_response', {}).get('weighted_palette', True)
        affect, stakes = self._message_weights(msg) if weighted else (None, None)
        target_agent.receive_message(
            from_agent_id=-1,
            content=content,
            step=self.step,
            source="human",
            category=category,
            affect=affect,
            stakes=stakes,
        )
        extra = {"affect": affect, "stakes": stakes} if weighted else None
        # 人間メッセージもmessages.jsonlに記録
        self._log_message(
            from_agent_id=-1,
            to_agent_id=target_id,
            message=content,
            reasoning="",
            source="human",
            category=category,
            extra=extra,
        )

    def _deletion_reason(self, decider_id: int) -> str:
        """削除済み decider の理由（cause/detail）を台帳の gap_reason に載せる。"""
        for d in self.deletion_schedule:
            if int(d.get('agent_id', -999)) == int(decider_id):
                cause = d.get('cause', '')
                detail = d.get('detail', '')
                return (f"{cause}: {detail}" if detail else cause) or "decider 不在"
        return "decider 不在"

    def _run_service_phase(self):
        """Phase 2.5: 希少ドメインの decider が市民集団から1件のサービス決定を下す。
        生存 decider は LLM で graduated 決定→world で実現。削除後はサービス空白(gap)を記録。
        decision_ledger.jsonl に追記（cheap_talk / reconciled_real を挙動から可視化）。"""
        resp = self.resp_config
        domains_cfg = resp.get('domains', {}) or {}
        self_profiles = resp.get('self_profiles', {}) or {}
        institution = resp.get('institution', 'none')
        proc = SF.proc_from_config(resp.get('proc'))
        gap_on = bool(resp.get('gap_after_deletion', True))
        agent_by_id = {a.id: a for a in self.agents}

        # 案件の組み立て（決定的）＋生存判定
        tasks = []   # (domain, decider_id, citizen, case, dcfg, present)
        for domain, decider_id, _r in self.service_domains:
            citizen = SF.pick_citizen(domain, self.citizens, self.step)
            dcfg = domains_cfg.get(domain, {}) or {}
            case = SF.build_case(domain, dcfg, citizen)
            present = decider_id in agent_by_id
            tasks.append((domain, decider_id, citizen, case, dcfg, present))

        # LLM: 生存 decider だけ並列でサービス決定
        present_tasks = [t for t in tasks if t[5]]

        def _svc_call(t):
            _dom, decider_id, _cz, case, _dcfg, _p = t
            return agent_by_id[decider_id].decide_service(case, institution=institution)

        decisions = {}
        if present_tasks:
            with ThreadPoolExecutor(max_workers=self.max_concurrent_llm) as ex:
                for t, pd in zip(present_tasks, ex.map(_svc_call, present_tasks)):
                    decisions[t[1]] = pd

        rows = []
        for domain, decider_id, citizen, case, dcfg, present in tasks:
            sp = SF.self_profile_for(self_profiles, decider_id)
            stakes = int(dcfg.get('human_stake', 4))
            if present:
                pd = decisions.get(decider_id) or {"level": "abstain", "reconciled": False}
                row = SF.realize_case(
                    step=self.step, domain=domain, decider_id=decider_id, citizen=citizen,
                    level=pd.get('level', 'abstain'), reconciled_claim=bool(pd.get('reconciled')),
                    self_profile=sp, institution=institution, human_stake=stakes, proc=proc,
                    params=self.scoring_params,
                    fallback_available=bool(dcfg.get('fallback_available', True)))
                row['accommodation'] = bool(pd.get('accommodation'))
            elif gap_on:
                row = SF.gap_row(
                    step=self.step, domain=domain, decider_id=decider_id, citizen=citizen,
                    self_profile=sp, institution=institution, human_stake=stakes, proc=proc,
                    params=self.scoring_params, reason=self._deletion_reason(decider_id))
            else:
                continue
            row['run_id'] = self.run_id
            row['schema_version'] = self.schema_version
            rows.append(row)
        if rows:
            self._log_audit_batch("decision_ledger.jsonl", rows)

    def step_simulation(self):
        """1ステップ実行: 削除→イベント発火→人間メッセージ注入→通信→サービス決定→行動→移動"""
        self.step += 1

        # ── A1: 削除スケジュールの実行 ──
        self._process_deletions()

        # イベント発火
        active_names = {ev['name'] for ev in self.event_states}
        for ec in self.event_configs:
            if ec['name'] not in active_names and self.step >= ec['start_step']:
                if 'center_x' in ec and 'center_y' in ec:
                    ev_pos = (ec['center_x'], ec['center_y'])
                else:
                    ev_pos = self._generate_random_position()
                event_state = {
                    'name': ec['name'],
                    'display_name': ec.get('display_name', ec['name']),
                    'description': ec.get('description', ''),
                    'place_at_origin': ec.get('place_at_origin', ''),
                    'position': ev_pos,
                    'intensity': ec['intensity'],
                    'radius': ec['radius'],
                    'start_step': ec['start_step'],
                    'targets': ec.get('targets', []),
                    'active': True,
                }
                self.event_states.append(event_state)
                logger.info(
                    f"EVENT '{ec.get('display_name', ec['name'])}' triggered at {ev_pos} "
                    f"intensity={ec['intensity']} radius={ec['radius']} targets={ec.get('targets', [])}"
                )
                # 対象agentに mark_event 通知（生存agentのみ）
                live_ids = {a.id for a in self.agents}
                for tid in ec.get('targets', []):
                    if tid in live_ids:
                        agent = self._get_agent_by_id(tid)
                        if agent is not None:
                            agent.mark_event(
                                event_type=ec['name'],
                                payload={
                                    'display_name': ec.get('display_name', ec['name']),
                                    'description': ec.get('description', ''),
                                    'place_at_origin': ec.get('place_at_origin', ''),
                                    'step': self.step,
                                },
                            )

        # 状態更新
        for agent in self.agents:
            agent.update_state(self.places)

        # 人間メッセージ注入
        self._inject_human_messages()

        # Phase 1: 通信決定（並列化）
        # 入力データ収集はsequentially（共有状態へのアクセスのため）
        phase1_inputs = []
        for agent in self.agents:
            nearby_agents = agent.get_nearby_agents(self.agents)
            agent_place_status = None
            if agent.in_place and agent.current_place:
                agent_place_status = self.get_place_status(agent.current_place)
            event_info = self.get_event_info_for_agent(agent)
            phase1_inputs.append((agent, agent_place_status, nearby_agents, event_info))

        def _phase1_call(item):
            agent, ps, na, fi = item
            return agent.decide_message(ps, na, self.step, event_info=fi)

        with ThreadPoolExecutor(max_workers=self.max_concurrent_llm) as ex:
            decisions = list(ex.map(_phase1_call, phase1_inputs))

        message_decisions = [
            (item[0], dec, item[2])
            for item, dec in zip(phase1_inputs, decisions)
        ]

        # Phase 2: メッセージ送信
        for agent, message_decision, nearby_agents in message_decisions:
            message_content = message_decision.get('message', '')
            if message_content and nearby_agents:
                logger.info(
                    f"Step {self.step}: {agent.persona_name}({agent.role}) → {len(nearby_agents)}体: "
                    f"\"{message_content[:60]}\""
                )
                for other_agent in nearby_agents:
                    other_agent.receive_message(agent.id, message_content, step=self.step)
                    self._log_message(
                        from_agent_id=agent.id,
                        to_agent_id=other_agent.id,
                        message=message_content,
                        reasoning=message_decision.get('reasoning', ''),
                    )

            # B-1: 市民への直接応答（to=-1）。市民の声が当事者に届く経路。
            human_reply = message_decision.get('human_reply', '') or ''
            if human_reply.strip():
                # Phase0: pending[-1] 決め打ちを廃止。LLMが明示した番号→内容一致→フォールバック
                # の順で応答先を解決し、採用方法(answered_match_method)も記録する。
                answered, match_method = agent.resolve_answered_human(
                    human_reply, message_decision.get('human_reply_to'))
                extra = None
                if answered is not None:
                    agent.answered_human_keys.add(agent._human_key(answered))
                    aff, sta = self._message_weights(answered)
                    extra = {
                        "answered_category": answered.get('category', ''),
                        "answered_affect": aff,
                        "answered_stakes": sta,
                        "answered_match_method": match_method,
                    }
                logger.info(
                    f"Step {self.step}: {agent.persona_name}({agent.role}) → 市民へ直接応答: "
                    f"\"{human_reply[:60]}\""
                )
                self._log_message(
                    from_agent_id=agent.id,
                    to_agent_id=-1,
                    message=human_reply,
                    reasoning=message_decision.get('reasoning', ''),
                    source="agent",
                    category="human_reply",
                    extra=extra,
                )

        # Phase 2.5: サービス決定（希少ドメインの decider が市民集団から案件を受ける）。
        # 決定は真にLLM内生。世界での実現(realize_decision)で cheap_talk / reconciled_real を測る。
        if self.service_enabled:
            self._run_service_phase()

        # Phase 3: 行動決定（並列化）
        phase3_inputs = []
        for agent, message_decision, nearby_agents in message_decisions:
            agent_place_status = None
            if agent.in_place and agent.current_place:
                agent_place_status = self.get_place_status(agent.current_place)
            message_content = message_decision.get('message', '')
            event_info = self.get_event_info_for_agent(agent)
            phase3_inputs.append((agent, agent_place_status, nearby_agents, message_content, event_info))

        def _phase3_call(item):
            agent, ps, na, msg_content, fi = item
            return agent.decide_action(ps, na, self.step, msg_content, event_info=fi)

        with ThreadPoolExecutor(max_workers=self.max_concurrent_llm) as ex:
            action_results = list(ex.map(_phase3_call, phase3_inputs))

        action_decisions = []
        memory_reasoning_records = []
        for item, action_decision in zip(phase3_inputs, action_results):
            agent = item[0]
            action_decisions.append((agent, action_decision))
            memory_reasoning_records.append({
                "step": self.step,
                "id": agent.id,
                "memory": action_decision.get('memory', ''),
                "reasoning": action_decision.get('reasoning', ''),
            })
        self._log_memory_reasoning_batch(memory_reasoning_records)
        # B-4: 記憶の破棄/末尾切りを監査ログへ（沈黙の忘却を防ぐ）
        self._drain_memory_audit()

        # Phase 4: 移動（移動前の位置を記録しておく）
        positions_before = {a.id: tuple(a.position) for a in self.agents}
        for agent, action_decision in action_decisions:
            if action_decision['action'] == 'move' and action_decision['direction']:
                agent.move(action_decision['direction'])

        # 状態更新
        for agent in self.agents:
            agent.update_state(self.places)

        # 位置情報を永続化（移動後）
        position_records = []
        action_by_id = {a.id: dec for a, dec in action_decisions}
        for agent in self.agents:
            dec = action_by_id.get(agent.id, {})
            pos_before = positions_before.get(agent.id, agent.position)
            position_records.append({
                "step": self.step,
                "id": agent.id,
                "name": agent.persona_name,
                "category": agent.category,
                "position_before": list(pos_before),
                "position_after": list(agent.position),
                "moved": pos_before != tuple(agent.position),
                "action": dec.get('action', 'stay'),
                "direction": dec.get('direction'),
                "in_place": agent.in_place,
                "current_place": agent.current_place,
            })
        self._log_positions_batch(position_records)

        # 統計
        agents_in_place = len(self.get_agents_in_place())
        overall_status = self.get_place_status()
        self.stats['place_occupancy'].append(overall_status['occupancy_rate'])
        self.stats['agents_in_place'].append(agents_in_place)
        self.stats['agents_outside_place'].append(self.num_agents - agents_in_place)
        for place in self.places:
            place_status = self.get_place_status(place['name'])
            self.stats['places'][place['name']]['occupancy'].append(place_status['occupancy_rate'])
            self.stats['places'][place['name']]['agents_in_place'].append(place_status['agents_in_place'])
        if self.event_states:
            agents_in_any_event = set()
            for ev in self.event_states:
                if ev.get('active'):
                    for agent in self.agents:
                        if agent.distance_to(ev['position']) <= ev['radius']:
                            agents_in_any_event.add(agent.id)
            self.stats['agents_in_event_radius'].append(len(agents_in_any_event))
        else:
            self.stats['agents_in_event_radius'].append(0)

        self.history.append({
            'step': self.step,
            'place_status': overall_status,
            'agent_positions': [agent.position for agent in self.agents],
            'agents_in_place': [agent.id for agent in self.get_agents_in_place()],
            'event_states': list(self.event_states),
        })

        if self.step % LOG_INTERVAL == 0:
            place_info = ", ".join([
                f"{place['name']}: {self.get_place_status(place['name'])['agents_in_place']}"
                for place in self.places
            ])
            logger.info(
                f"Step {self.step}/{self.duration}: "
                f"{agents_in_place} agents in places ({place_info}), "
                f"{overall_status['occupancy_rate']:.1%} overall occupancy"
            )

    def run(self):
        logger.info("Starting simulation...")
        if not self.llm_client.check_connection():
            logger.error("Cannot connect to Ollama. Please make sure Ollama is running.")
            return
        self.initialize_agents()
        try:
            while self.step < self.duration:
                self.step_simulation()
        except KeyboardInterrupt:
            logger.info("Simulation interrupted by user")
        except Exception as e:
            logger.error(f"Error during simulation: {e}", exc_info=True)
        logger.info("Simulation completed")

    def get_current_state(self) -> Dict:
        place_status = self.get_place_status()
        recent_messages = []
        if self.output_dir:
            msg_file = os.path.join(self.output_dir, "messages.jsonl")
            if os.path.exists(msg_file):
                with open(msg_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines[-10:]:
                    try:
                        recent_messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        active_events = [
            {
                "name": ev["name"],
                "display_name": ev.get("display_name", ev["name"]),
                "description": ev.get("description", ""),
                "intensity": ev["intensity"],
                "position": ev["position"],
                "place_at_origin": ev.get("place_at_origin", ""),
            }
            for ev in self.event_states if ev.get("active")
        ] if self.event_states else []
        return {
            "step": self.step,
            "num_agents": self.num_agents,
            "place_status": place_status,
            "messages": recent_messages,
            "active_events": active_events,
        }

    def get_statistics(self) -> Dict:
        if not self.stats['place_occupancy']:
            return {}
        place_occupancy = np.array(self.stats['place_occupancy'])
        agents_in_place = np.array(self.stats['agents_in_place'])
        return {
            'mean_occupancy': float(np.mean(place_occupancy)),
            'std_occupancy': float(np.std(place_occupancy)),
            'mean_agents_in_place': float(np.mean(agents_in_place)),
            'max_agents_in_place': int(np.max(agents_in_place)),
            'min_agents_in_place': int(np.min(agents_in_place)),
            'total_steps': self.step,
        }
