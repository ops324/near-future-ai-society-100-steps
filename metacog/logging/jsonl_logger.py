"""
JSONL Logger — Round 4
- agent_log.jsonl: セッションのライフサイクル等
- inner_thought.jsonl: L1 (内省層) の各起動の差分・思考
- coined_terms.jsonl: 創発した語の検出ログ
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List


class MetaCogLogger:
    def __init__(self, log_dir: str, session_id: str, console: bool = True):
        os.makedirs(log_dir, exist_ok=True)
        self.log_dir = log_dir
        self.log_path = os.path.join(log_dir, "agent_log.jsonl")
        self.inner_thought_path = os.path.join(log_dir, "inner_thought.jsonl")
        self.coined_terms_path = os.path.join(log_dir, "coined_terms.jsonl")
        self.session_id = session_id
        self.console = console

    def _write(self, path: str, record: dict):
        record["timestamp"] = datetime.now(timezone.utc).isoformat()
        record["session_id"] = self.session_id
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── agent_log (セッションメタ + 雑多イベント) ──

    def log_session_start(self, config: dict, initial_prompt: str = ""):
        record = {"event_type": "session_start", "config": config}
        if initial_prompt:
            record["initial_prompt"] = initial_prompt
        self._write(self.log_path, record)
        if self.console:
            print(f"[session_start] session={self.session_id}")

    def log_session_end(self, summary: dict = None):
        record = {"event_type": "session_end"}
        if summary:
            record["summary"] = summary
        self._write(self.log_path, record)
        if self.console:
            print(f"[session_end] session={self.session_id}")

    def log_event(self, event_type: str, **kwargs):
        record = {"event_type": event_type}
        record.update(kwargs)
        self._write(self.log_path, record)
        if self.console:
            print(f"  [{event_type}] {kwargs}")

    # ── L1 (内省) ログ ──

    def log_inner_thought(
        self,
        step: int,
        cycle: int,
        agent_id: int,
        agent_name: str,
        before: Dict[str, str],
        after: Dict[str, str],
        inner_thought: str,
        reasoning: str,
        triggering_events: List[Dict] = None,
    ):
        """L1の1サイクル分を記録"""
        record = {
            "event_type": "inner_thought",
            "step": step,
            "cycle": cycle,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "before": before,
            "after": after,
            "inner_thought": inner_thought,
            "reasoning": reasoning,
            "triggering_events": triggering_events or [],
        }
        self._write(self.inner_thought_path, record)
        if self.console:
            preview = inner_thought[:60] if inner_thought else ""
            print(f"  [introspect] step={step} agent={agent_name}({agent_id}) thought={preview}")

    # ── 創発語ログ ──

    def log_coined_term(
        self,
        step: int,
        term: str,
        first_seen_step: int,
        first_agent_id: int,
        first_agent_name: str,
        agents_using: List[int],
        occurrence_count: int,
    ):
        record = {
            "event_type": "coined_term",
            "step": step,
            "term": term,
            "first_seen_step": first_seen_step,
            "first_agent_id": first_agent_id,
            "first_agent_name": first_agent_name,
            "agents_using": agents_using,
            "occurrence_count": occurrence_count,
        }
        self._write(self.coined_terms_path, record)
        if self.console:
            print(f"  [coined_term] step={step} term=「{term}」 by {len(agents_using)}体 / {occurrence_count}回")
