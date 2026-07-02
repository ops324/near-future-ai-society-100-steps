"""
Introspector (L1 内省層)
各AIエージェントを Claude Haiku 4.5 で反省させ、
self_concept / current_goal / coping_notes の書き換え差分を返す。
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # 後で実行時にエラーを出す

from .prompt_template import render_prompt

logger = logging.getLogger(__name__)


def collect_others_voices(
    target_agent,
    all_agents: List,
    output_dir: str,
    log_dir: str,
    current_step: int,
    n_speeches: int = 4,
    n_thoughts: int = 2,
    recent_steps_window: int = 10,
) -> List[Dict[str, Any]]:
    """P2: 他agentの最近の発話と内省を収集して target_agent の内省プロンプトに渡す。
    自分とは異なるカテゴリのagentから優先的に拾う（多様性を確保）。
    """
    out: List[Dict[str, Any]] = []
    target_id = target_agent.id
    target_cat = target_agent.category

    # ── 発話: messages.jsonl から拾う ──
    messages_path = os.path.join(output_dir, "messages.jsonl")
    if os.path.exists(messages_path):
        try:
            with open(messages_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 末尾から走査して、recent_steps_window 内の他agent発話を拾う
            id_to_agent = {a.id: a for a in all_agents}
            seen_pairs = set()  # (from_id, content) 重複除去
            speeches_found = []
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                step = rec.get("step", 0)
                if current_step - step > recent_steps_window:
                    break
                from_id = rec.get("from", -1)
                if from_id < 0 or from_id == target_id:
                    continue
                src = id_to_agent.get(from_id)
                if src is None:
                    continue
                # 異なるカテゴリ優先（ただし同じカテゴリも一部許容）
                key = (from_id, rec.get("message", "")[:30])
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                speeches_found.append({
                    "kind": "speech",
                    "name": src.persona_name,
                    "role": src.role,
                    "category": src.category,
                    "text": rec.get("message", ""),
                    "step": step,
                })
            # 異カテゴリ優先で並べ替え
            speeches_found.sort(key=lambda x: (x["category"] == target_cat, -x["step"]))
            out.extend(speeches_found[:n_speeches])
        except Exception as e:
            logger.warning(f"Failed to read messages.jsonl for others_voices: {e}")

    # ── 内省: inner_thought.jsonl から拾う ──
    inner_path = os.path.join(log_dir, "inner_thought.jsonl")
    if os.path.exists(inner_path):
        try:
            with open(inner_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            id_to_agent = {a.id: a for a in all_agents}
            thoughts_found = []
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                step = rec.get("step", 0)
                if current_step - step > recent_steps_window * 2:  # 内省は多めに窓を取る
                    break
                aid = rec.get("agent_id", -1)
                if aid < 0 or aid == target_id:
                    continue
                src = id_to_agent.get(aid)
                if src is None:
                    continue
                thoughts_found.append({
                    "kind": "thought",
                    "name": rec.get("agent_name", ""),
                    "role": src.role,
                    "category": src.category,
                    "text": rec.get("inner_thought", ""),
                    "step": step,
                })
            thoughts_found.sort(key=lambda x: (x["category"] == target_cat, -x["step"]))
            out.extend(thoughts_found[:n_thoughts])
        except Exception as e:
            logger.warning(f"Failed to read inner_thought.jsonl for others_voices: {e}")

    return out


class Introspector:
    def __init__(self, config: Dict[str, Any], logger_obj=None):
        """
        config: metacog/config.yaml から読み込んだ dict
        logger_obj: MetaCogLogger インスタンス（省略可、log_inner_thoughtを呼ぶ）
        """
        self.config = config
        self.logger = logger_obj
        self.api_key_env = config["anthropic"]["api_key_env"]
        self.model = config["anthropic"]["model"]
        self.max_tokens = config["anthropic"]["max_tokens"]
        self.temperature = config["anthropic"]["temperature"]

        if Anthropic is None:
            raise ImportError("anthropic SDK が見つかりません。`pip install anthropic` を実行してください。")

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise EnvironmentError(
                f"環境変数 {self.api_key_env} が未設定です。export {self.api_key_env}=sk-... が必要です。"
            )
        self.client = Anthropic(api_key=api_key)

        # 字数上限（agentのものと整合させる）
        intro_cfg = config.get("introspection", {})
        self.max_self_concept_chars = intro_cfg.get("max_self_concept_chars", 100)
        self.max_current_goal_chars = intro_cfg.get("max_current_goal_chars", 50)
        self.max_coping_notes_chars = intro_cfg.get("max_coping_notes_chars", 500)

    def run_for_agent(
        self,
        agent,
        current_step: int,
        all_agents: Optional[List] = None,
        output_dir: str = "output",
        log_dir: str = "metacog/logs",
    ) -> Optional[Dict[str, Any]]:
        """
        agent: agent.Agent インスタンス
        all_agents: 全エージェントリスト（P2: 他者の声収集用）
        output_dir / log_dir: P2の参照ログ場所
        return: 書き換え差分 dict、または None（API失敗時）
        """
        intro_cfg = self.config.get("introspection", {})
        recent_steps = intro_cfg.get("recent_steps_to_include", 10)
        max_recent_messages = intro_cfg.get("max_recent_messages", 5)

        # before スナップショット（diffログ用）
        before = {
            "self_concept": agent.self_concept,
            "current_goal": agent.current_goal,
            "coping_notes": agent.coping_notes,
        }

        # コンテキスト取得（mark_event を消費する）
        context = agent.get_introspection_context(
            current_step=current_step,
            recent_steps=recent_steps,
            max_recent_messages=max_recent_messages,
        )
        triggering_events = context.get("triggering_events", [])

        # P2: 他agentの声をcontextに加える
        if all_agents:
            others_voices = collect_others_voices(
                target_agent=agent,
                all_agents=all_agents,
                output_dir=output_dir,
                log_dir=log_dir,
                current_step=current_step,
                n_speeches=intro_cfg.get("others_voices_speeches", 4),
                n_thoughts=intro_cfg.get("others_voices_thoughts", 2),
                recent_steps_window=intro_cfg.get("others_voices_window", 10),
            )
            context["others_voices"] = others_voices

        prompt = render_prompt(context)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.error(f"Claude API call failed for agent {agent.id}: {e}")
            return None

        # レスポンス文字列抽出
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
        diff = self._parse_response(text)
        if diff is None:
            logger.warning(f"Failed to parse introspection response for agent {agent.id}: {text[:200]}")
            return None

        # 字数上限ガード（agent側でも掛かるが二重で安全に）
        if diff.get("self_concept_new"):
            diff["self_concept_new"] = diff["self_concept_new"][: self.max_self_concept_chars]
        if diff.get("current_goal_new"):
            diff["current_goal_new"] = diff["current_goal_new"][: self.max_current_goal_chars]
        if diff.get("coping_notes_append"):
            diff["coping_notes_append"] = diff["coping_notes_append"][: self.max_coping_notes_chars]

        # agentに適用（governance.self_update のガードがここで効く）
        agent.apply_introspection_diff(diff, current_step=current_step)

        # B-5 監査: 自己更新の適用/ブロック/承認要否を self_update_audit.jsonl に記録
        audit = getattr(agent, "last_self_update_audit", None)
        if audit is not None:
            try:
                os.makedirs(log_dir, exist_ok=True)
                with open(os.path.join(log_dir, "self_update_audit.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(audit, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Failed to write self_update_audit for agent {agent.id}: {e}")

        after = {
            "self_concept": agent.self_concept,
            "current_goal": agent.current_goal,
            "coping_notes": agent.coping_notes,
        }

        # ログ
        if self.logger is not None:
            self.logger.log_inner_thought(
                step=current_step,
                cycle=agent.introspection_count,
                agent_id=agent.id,
                agent_name=agent.persona_name,
                before=before,
                after=after,
                inner_thought=diff.get("inner_thought", ""),
                reasoning=diff.get("reasoning", ""),
                triggering_events=triggering_events,
            )

        return diff

    def _parse_response(self, text: str) -> Optional[Dict[str, Any]]:
        """JSONを抽出してパース"""
        # ```json ... ``` ブロックがあればその中を優先
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            json_text = m.group(1)
        else:
            # 最初の { から末尾まで丸ごと探す（簡易ブレースマッチ）
            start = text.find("{")
            if start == -1:
                return None
            depth = 0
            in_string = False
            escape_next = False
            json_text = None
            for i, c in enumerate(text[start:], start=start):
                if escape_next:
                    escape_next = False
                    continue
                if c == "\\" and in_string:
                    escape_next = True
                    continue
                if c == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        json_text = text[start:i + 1]
                        break
            if json_text is None:
                return None

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            return None

        # 必須キーのデフォルト
        return {
            "self_concept_new": parsed.get("self_concept_new", "") or "",
            "current_goal_new": parsed.get("current_goal_new", "") or "",
            "coping_notes_append": parsed.get("coping_notes_append", "") or "",
            "inner_thought": parsed.get("inner_thought", "") or "",
            "reasoning": parsed.get("reasoning", "") or "",
        }
