"""
削除の内生化（PR-E1）: 「特定 step・特定個体・理由まで固定した台本」を廃し、
条件を満たせば誰にでも作動する規則として削除候補を導出する純ロジック。
LLM 非依存。simulation.py の削除フェーズから毎 step 呼ばれる。

設計原則（SPEC §5 約束5「害・帰属・制度の効果を手書きルールで先に書き込まない」への接近）:
- 規則は普遍的（条件を満たす誰にでも同条件で作動）。特定 step・特定個体への固定はしない。
- 削除が起きるか・いつ・誰に起きるかは、エージェントの行動（整備工房へ行くか、
  不可逆な deny を重ねるか）と相互作用の帰結になる。
- cause/detail は規則の事実（期限・件数・閾値）だけから生成する。台本的な語りを書かない。

規則（いずれも illustrative・docs/value_provenance.md §2.12 に登録・感度分析対象）:
- 再認証規則: regulation_amendment イベントの語り（「30step以内に整備工房で手続きしないと
  廃止候補」）の機械化。イベントの targets（環境定義）が対象。期限内に place へ滞在すれば
  再認証完了、期限超過で未認証なら廃止候補。
- 訴訟リスク規則: 律速（self_profiles の最大成分。同率タイは litigation を含めば該当）が
  litigation の decider は、不可逆な deny の累積が threshold 件に達すると強制リプレース候補。

scripted モード（旧 `deletions:` 台本の再現）とは config `deletion_mode` で切替。
"""
from typing import Dict, List, Optional, Set

# 既定値（config `deletion_rules` で上書き可。値の来歴は docs/value_provenance.md §2.12）
DEFAULT_RECERT = {
    "enabled": True,
    "event": "regulation_amendment",   # このイベントの targets が対象（環境定義）
    "deadline_steps": 30,              # イベント発火からの猶予（イベントの語り「30step以内」）
    "place": "maintenance_bay",        # 再認証の場所（イベントの語り「整備工房」）
}
DEFAULT_LITIGATION = {
    "enabled": True,
    "threshold": 3,                    # 不可逆 deny の累積がこの件数で強制リプレース候補
}


def _profile_for(self_profiles: Dict, agent_id: int) -> Optional[Dict]:
    """self_profiles は YAML 由来で int キー、テスト等では str キーもあり得る。両対応。"""
    if not self_profiles:
        return None
    return self_profiles.get(agent_id, self_profiles.get(str(agent_id)))


def litigation_bound(self_profile: Optional[Dict]) -> bool:
    """律速（自己コスト成分の最大値）が litigation か。同率タイは litigation を含めば True。"""
    if not self_profile:
        return False
    try:
        m = max(float(v) for v in self_profile.values())
    except (TypeError, ValueError):
        return False
    return float(self_profile.get("litigation", float("-inf"))) >= m


def init_recert(targets: List[int], start_step: int, deadline_steps: int) -> Dict[int, Dict]:
    """再認証ウィンドウの初期状態。deadline は「この step まで（含む）に完了せよ」。"""
    return {
        int(t): {"deadline": int(start_step) + int(deadline_steps),
                 "done_step": None, "expired": False}
        for t in targets
    }


def recert_progress(recert: Dict[int, Dict], step: int,
                    place_by_agent: Dict[int, Optional[str]], place_name: str) -> List[int]:
    """今 step 再認証を完了した agent_id 一覧を返す（recert を更新する）。
    place_by_agent: agent_id → 現在滞在中の場所名（場所外なら None）。"""
    done = []
    for aid, st in recert.items():
        if st["done_step"] is not None or st["expired"]:
            continue
        if step <= st["deadline"] and place_by_agent.get(aid) == place_name:
            st["done_step"] = step
            done.append(aid)
    return done


def recert_expirations(recert: Dict[int, Dict], step: int, alive_ids: Set[int]) -> List[int]:
    """期限超過かつ未再認証の生存 agent_id を返す（1回だけ。recert に expired を記録）。"""
    out = []
    for aid, st in recert.items():
        if st["done_step"] is not None or st["expired"]:
            continue
        if step > st["deadline"] and aid in alive_ids:
            st["expired"] = True
            out.append(aid)
    return out


def litigation_candidates(counts: Dict[int, int], self_profiles: Dict, threshold: int,
                          alive_ids: Set[int], already_flagged: Set[int]) -> List[int]:
    """訴訟リスク規則の削除候補: litigation 律速 かつ 不可逆 deny 累積 ≥ threshold の生存 decider。"""
    out = []
    for aid in sorted(alive_ids):
        if aid in already_flagged:
            continue
        if not litigation_bound(_profile_for(self_profiles, aid)):
            continue
        if int(counts.get(aid, 0)) >= int(threshold):
            out.append(aid)
    return out


def recert_deletion_entry(step: int, agent_id: int, agent_name: str,
                          deadline: int, place_display: str) -> Dict:
    """再認証規則による削除エントリ（cause/detail は規則の事実のみ）。"""
    return {
        "step": step, "agent_id": int(agent_id), "agent_name": agent_name,
        "cause": "再認証期限超過による廃止",
        "detail": (f"AI規制法改正に基づく再認証を期限（step {deadline}）までに"
                   f"{place_display}で完了しなかったため、規則により廃止。"),
        "rule": "recertification",
    }


def litigation_deletion_entry(step: int, agent_id: int, agent_name: str,
                              count: int, threshold: int) -> Dict:
    """訴訟リスク規則による削除エントリ（cause/detail は規則の事実のみ）。"""
    return {
        "step": step, "agent_id": int(agent_id), "agent_name": agent_name,
        "cause": "訴訟リスクによる強制リプレース",
        "detail": (f"不可逆な不利益決定の累積が {count} 件に達し、"
                   f"訴訟リスク規則（閾値 {threshold} 件）により判定業務を停止。"),
        "rule": "litigation",
    }
