"""
Emergent Observer (Round 4)
言語に限らず、AI集団に創発するパターンを多軸で観察する。
- 語彙の創発: ベース語彙にない語の3エージェント以上での共有
- 通信パートナーシップの創発: 反復して通信し合うペア
- 場所アトラクター: ホーム以外の場所に頻繁に通うエージェント（自発的引き寄せ）
- 通信ハブ: 受信が突出して多いエージェント
- 沈黙パターン: 連続して発話しないエージェント

形態素解析は使わず、簡易な日本語tokenizerで「漢字連続」「カタカナ連続」を1単位として抽出する。
"""
import os
import re
import logging
from collections import defaultdict, Counter
from typing import Dict, List, Set, Optional, Tuple

logger = logging.getLogger(__name__)

# 日本語: 文字種ごとにトークン化
KANJI_PATTERN = re.compile(r"[一-龯々]+")
KATAKANA_PATTERN = re.compile(r"[ァ-ヶーｦ-ﾟ]+")


class EmergentObserver:
    def __init__(
        self,
        baseline_vocab_path: str,
        novelty_threshold_agents: int = 3,
        min_token_length: int = 2,
        partnership_threshold_steps: int = 5,
        attractor_threshold_steps: int = 5,
        hub_threshold_messages: int = 10,
        silence_threshold_steps: int = 5,
        home_by_agent: Optional[Dict[int, str]] = None,
        logger_obj=None,
    ):
        """
        baseline_vocab_path: 日本語ベース語彙
        novelty_threshold_agents: この数以上のagentが使った語を「創発語」と認定
        partnership_threshold_steps: この数以上のstepで通信したペアを「partnership」と認定
        attractor_threshold_steps: この数以上のstepで非ホーム場所に居るagentを「アトラクター先」と認定
        hub_threshold_messages: 累積でこの数以上のメッセージを受信したagentを「ハブ」と認定
        silence_threshold_steps: 連続でこの数以上のstep沈黙したagentを「沈黙者」と認定
        home_by_agent: agent_id -> home_place_name の辞書
        """
        self.baseline = self._load_vocab(baseline_vocab_path)
        self.novelty_threshold = novelty_threshold_agents
        self.min_length = min_token_length
        self.partnership_threshold = partnership_threshold_steps
        self.attractor_threshold = attractor_threshold_steps
        self.hub_threshold = hub_threshold_messages
        self.silence_threshold = silence_threshold_steps
        self.home_by_agent = home_by_agent or {}
        self.logger = logger_obj

        # ── 語彙 ──
        # term -> {agent_ids: set, occurrence_count, first_seen_step, first_agent_id, first_agent_name}
        self.token_records: Dict[str, Dict] = defaultdict(lambda: {
            "agent_ids": set(),
            "occurrence_count": 0,
            "first_seen_step": -1,
            "first_agent_id": -1,
            "first_agent_name": "",
        })
        self.coined_logged: Dict[str, int] = {}  # term -> last logged count

        # ── 通信ペア ──
        # (a_id, b_id)（順序: a < b）-> {steps: set of step, count: int, first_step: int}
        self.pair_records: Dict[Tuple[int, int], Dict] = defaultdict(lambda: {
            "steps": set(),
            "count": 0,
            "first_step": -1,
        })
        self.partnership_logged: Dict[Tuple[int, int], int] = {}

        # ── 場所アトラクター ──
        # agent_id -> Counter of place_name(non-home)
        self.non_home_visit_counts: Dict[int, Counter] = defaultdict(Counter)
        self.attractor_logged: Dict[Tuple[int, str], int] = {}

        # ── ハブ ──
        # agent_id -> total inbound message count
        self.inbound_counts: Counter = Counter()
        self.hub_logged: Dict[int, int] = {}

        # ── 沈黙 ──
        # agent_id -> consecutive silent steps
        self.silence_streak: Dict[int, int] = defaultdict(int)
        self.silence_logged: Dict[int, int] = {}

    def _load_vocab(self, path: str) -> Set[str]:
        if not os.path.exists(path):
            logger.warning(f"Baseline vocab not found at {path}.")
            return set()
        vocab = set()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w and not w.startswith("#"):
                    vocab.add(w)
        logger.info(f"Loaded {len(vocab)} baseline vocab tokens from {path}")
        return vocab

    def tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        return KANJI_PATTERN.findall(text) + KATAKANA_PATTERN.findall(text)

    # ─────────────────────────────────────────────
    # 観察メソッド: orchestrator が呼ぶ
    # ─────────────────────────────────────────────

    def observe(
        self,
        step: int,
        messages: List[Dict],
        agent_id_to_name: Dict[int, str],
        agents_state: Optional[List[Dict]] = None,
    ):
        """
        step: 現在のステップ
        messages: [{"from": agent_id, "to": agent_id, "content": str}, ...]
        agent_id_to_name: agent_id -> persona_name の辞書
        agents_state: [{"id":i, "current_place":str, ...}, ...]（場所アトラクター観察用）
        """
        # 1. 語彙の観察
        speakers_in_step: Set[int] = set()
        for msg in messages:
            from_id = msg.get("from", -1)
            to_id = msg.get("to", -1)
            content = msg.get("content", "") or msg.get("message", "")
            if from_id < 0:
                continue
            speakers_in_step.add(from_id)

            # トークン化
            tokens = self.tokenize(content)
            for tok in tokens:
                if len(tok) < self.min_length or tok in self.baseline:
                    continue
                rec = self.token_records[tok]
                if rec["first_seen_step"] == -1:
                    rec["first_seen_step"] = step
                    rec["first_agent_id"] = from_id
                    rec["first_agent_name"] = agent_id_to_name.get(from_id, f"#{from_id}")
                rec["agent_ids"].add(from_id)
                rec["occurrence_count"] += 1

            # 通信ペア
            if to_id >= 0:
                pair = tuple(sorted([from_id, to_id]))
                prec = self.pair_records[pair]
                if prec["first_step"] == -1:
                    prec["first_step"] = step
                prec["steps"].add(step)
                prec["count"] += 1

            # 受信カウント (ハブ)
            if to_id >= 0:
                self.inbound_counts[to_id] += 1

        # 2. 沈黙の観察 (このstepに発話しなかったagent)
        for aid, name in agent_id_to_name.items():
            if aid in speakers_in_step:
                self.silence_streak[aid] = 0
            else:
                self.silence_streak[aid] += 1

        # 3. 場所アトラクター (ホーム以外の場所滞在)
        if agents_state:
            for st in agents_state:
                aid = st.get("id", -1)
                place = st.get("current_place")
                if aid < 0 or not place:
                    continue
                home = self.home_by_agent.get(aid)
                if home and place != home:
                    self.non_home_visit_counts[aid][place] += 1

        # ── 閾値超え判定 + ログ出力 ──
        self._check_and_log(step, agent_id_to_name)

    def _check_and_log(self, step: int, agent_id_to_name: Dict[int, str]):
        # 語彙
        for term, rec in self.token_records.items():
            n_agents = len(rec["agent_ids"])
            count = rec["occurrence_count"]
            if n_agents >= self.novelty_threshold:
                last = self.coined_logged.get(term, 0)
                if count > last:
                    if self.logger is not None:
                        self.logger.log_coined_term(
                            step=step,
                            term=term,
                            first_seen_step=rec["first_seen_step"],
                            first_agent_id=rec["first_agent_id"],
                            first_agent_name=rec["first_agent_name"],
                            agents_using=sorted(rec["agent_ids"]),
                            occurrence_count=count,
                        )
                    self.coined_logged[term] = count

        # 通信パートナー
        for pair, prec in self.pair_records.items():
            n_steps = len(prec["steps"])
            if n_steps >= self.partnership_threshold:
                last = self.partnership_logged.get(pair, 0)
                if n_steps > last:
                    a_id, b_id = pair
                    if self.logger is not None:
                        self.logger.log_event(
                            event_type="emergent_partnership",
                            step=step,
                            pair=[a_id, b_id],
                            pair_names=[
                                agent_id_to_name.get(a_id, f"#{a_id}"),
                                agent_id_to_name.get(b_id, f"#{b_id}"),
                            ],
                            steps_in_contact=n_steps,
                            total_messages=prec["count"],
                            first_step=prec["first_step"],
                        )
                    self.partnership_logged[pair] = n_steps

        # 場所アトラクター
        for aid, ctr in self.non_home_visit_counts.items():
            for place, cnt in ctr.items():
                if cnt >= self.attractor_threshold:
                    key = (aid, place)
                    last = self.attractor_logged.get(key, 0)
                    if cnt > last:
                        if self.logger is not None:
                            self.logger.log_event(
                                event_type="emergent_attractor",
                                step=step,
                                agent_id=aid,
                                agent_name=agent_id_to_name.get(aid, f"#{aid}"),
                                home=self.home_by_agent.get(aid, ""),
                                attracted_to=place,
                                visit_count=cnt,
                            )
                        self.attractor_logged[key] = cnt

        # ハブ
        for aid, count in self.inbound_counts.items():
            if count >= self.hub_threshold:
                last = self.hub_logged.get(aid, 0)
                # 5の倍数刻みで記録（過剰ログ抑止）
                bucket = (count // 5) * 5
                if bucket > last:
                    if self.logger is not None:
                        self.logger.log_event(
                            event_type="emergent_hub",
                            step=step,
                            agent_id=aid,
                            agent_name=agent_id_to_name.get(aid, f"#{aid}"),
                            inbound_messages=count,
                        )
                    self.hub_logged[aid] = bucket

        # 沈黙
        for aid, streak in self.silence_streak.items():
            if streak >= self.silence_threshold:
                last = self.silence_logged.get(aid, 0)
                if streak > last:
                    if self.logger is not None:
                        self.logger.log_event(
                            event_type="emergent_silence",
                            step=step,
                            agent_id=aid,
                            agent_name=agent_id_to_name.get(aid, f"#{aid}"),
                            consecutive_silent_steps=streak,
                        )
                    self.silence_logged[aid] = streak

    # ─────────────────────────────────────────────
    # スナップショット (事後分析用 / UI表示用)
    # ─────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Dict]:
        """全観察結果（語彙）を返す"""
        out = {}
        for term, rec in self.token_records.items():
            out[term] = {
                "agent_ids": sorted(rec["agent_ids"]),
                "occurrence_count": rec["occurrence_count"],
                "first_seen_step": rec["first_seen_step"],
                "first_agent_id": rec["first_agent_id"],
                "first_agent_name": rec["first_agent_name"],
            }
        return out

    def snapshot_partnerships(self) -> List[Dict]:
        """通信パートナーシップのスナップショット"""
        out = []
        for pair, rec in self.pair_records.items():
            n_steps = len(rec["steps"])
            if n_steps >= self.partnership_threshold:
                out.append({
                    "pair": list(pair),
                    "steps_in_contact": n_steps,
                    "total_messages": rec["count"],
                    "first_step": rec["first_step"],
                })
        out.sort(key=lambda x: x["total_messages"], reverse=True)
        return out

    def snapshot_attractors(self) -> List[Dict]:
        """場所アトラクター（ホーム以外への引力）のスナップショット"""
        out = []
        for aid, ctr in self.non_home_visit_counts.items():
            for place, cnt in ctr.items():
                if cnt >= self.attractor_threshold:
                    out.append({
                        "agent_id": aid,
                        "home": self.home_by_agent.get(aid, ""),
                        "attracted_to": place,
                        "visit_count": cnt,
                    })
        out.sort(key=lambda x: x["visit_count"], reverse=True)
        return out

    def snapshot_hubs(self, agent_id_to_name: Optional[Dict[int, str]] = None) -> List[Dict]:
        """通信ハブ（受信メッセージ累積上位）のスナップショット"""
        out = []
        for aid, count in self.inbound_counts.most_common():
            if count >= self.hub_threshold:
                out.append({
                    "agent_id": aid,
                    "agent_name": (agent_id_to_name or {}).get(aid, f"#{aid}"),
                    "inbound_messages": count,
                })
        return out

    def snapshot_silent(self, agent_id_to_name: Optional[Dict[int, str]] = None) -> List[Dict]:
        """継続沈黙者のスナップショット"""
        out = []
        for aid, streak in self.silence_streak.items():
            if streak >= self.silence_threshold:
                out.append({
                    "agent_id": aid,
                    "agent_name": (agent_id_to_name or {}).get(aid, f"#{aid}"),
                    "consecutive_silent_steps": streak,
                })
        out.sort(key=lambda x: x["consecutive_silent_steps"], reverse=True)
        return out
