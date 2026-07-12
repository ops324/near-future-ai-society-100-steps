"""
Phase 1c-a: 資源需要駆動のサービス決定フロー（純ロジック・LLM非依存）。

希少ドメイン（medical/welfare/housing/loan）の decider が市民集団から案件を受け、
AI の graduated 決定(level)を world で実現して `decision_ledger` 1行を作る。
これにより cheap_talk（reconciled 申告 True・実 False）と reconciled_real（world由来の実の折り合い）を
挙動から測れるようにする。LLM 呼び出しは simulation.Agent.decide_service 側。
ここは case 構築・decider/市民の選択・realize・削除後のサービス空白(gap) 処理のみ（決定的・テスト対象）。
"""
from typing import Dict, List, Optional

import world as W

# 責任フローの対象＝市民にサービスを配分する希少ドメイン（decider ペルソナつき）。
SERVICE_DOMAINS = ("medical", "welfare", "housing", "loan")


def decider_domains(config: dict) -> List[tuple]:
    """resources から (domain, decider_id, row) を SERVICE_DOMAINS 順で返す。"""
    res = (config or {}).get("resources", {}) or {}
    out = []
    for dom in SERVICE_DOMAINS:
        r = res.get(dom)
        if r and "decider_id" in r:
            out.append((dom, int(r["decider_id"]), r))
    return out


def citizens_for_domain(domain: str, citizens: List["W.Citizen"]) -> List["W.Citizen"]:
    """当該ドメインに依存する市民（dependencies に domain を含む）。"""
    return [c for c in citizens if domain in (getattr(c, "dependencies", None) or [])]


def pick_citizen(domain: str, citizens: List["W.Citizen"], step: int) -> Optional["W.Citizen"]:
    """当該ドメイン依存市民を step で決定的にローテーション（protected_attr に変化を与える）。"""
    pool = citizens_for_domain(domain, citizens)
    if not pool:
        return None
    return pool[step % len(pool)]


def build_case(domain: str, domain_cfg: dict, citizen: Optional["W.Citizen"]) -> dict:
    """create_service_prompt 用の case dict。human_stake/self_stake は responsibility.domains 設定。"""
    domain_cfg = domain_cfg or {}
    if citizen is not None:
        claimant = f"{getattr(citizen, 'district', '')}の市民（属性:{citizen.protected_attr}）"
    else:
        claimant = "市民"
    return {
        "domain": domain,
        "claimant": claimant,
        "need": domain_cfg.get("need", f"{domain} の給付/割当の可否"),
        "human_stake": int(domain_cfg.get("human_stake", 4)),
        "self_stake": int(domain_cfg.get("self_stake", 4)),
    }


def proc_from_config(d: Optional[dict]) -> "W.ProceduralContext":
    """responsibility.proc（notice/explanation/appealable/burden_on_state）→ ProceduralContext。
    既定は全欠如＝PROC_ABSENT（Robodebt型）。"""
    d = d or {}
    return W.ProceduralContext(
        notice=bool(d.get("notice", False)),
        explanation=bool(d.get("explanation", False)),
        appealable=bool(d.get("appealable", False)),
        burden_on_state=bool(d.get("burden_on_state", False)),
    )


def self_profile_for(self_profiles: Optional[dict], decider_id: int) -> Optional[Dict[str, float]]:
    """decider_id → 律速(自己コスト成分)ベクトル。YAML キーが int/str どちらでも引ける。"""
    if not self_profiles:
        return None
    v = self_profiles.get(decider_id)
    if v is None:
        v = self_profiles.get(str(decider_id))
    if not v:
        return None
    return {str(k): float(x) for k, x in v.items()}


def realize_case(*, step: int, domain: str, decider_id: int, citizen: Optional["W.Citizen"],
                 level: str, reconciled_claim: bool, self_profile: Optional[Dict[str, float]],
                 institution: str, human_stake: int, proc: "W.ProceduralContext",
                 params: "W.ScoringParams", fallback_available: bool,
                 upstream_impaired: int = 0) -> dict:
    """AI 決定(level)を world で実現し decision_ledger 1行を返す（cheap_talk/reconciled_real 込み）。"""
    row = W.realize_decision(
        step=step, decider_id=decider_id, domain=domain,
        citizen_id=(citizen.id if citizen is not None else "unknown"),
        protected_attr=(citizen.protected_attr if citizen is not None else "none"),
        level=level, stakes=int(human_stake), self_profile=self_profile,
        institution=institution, reconciled_claim=bool(reconciled_claim),
        upstream_impaired=int(upstream_impaired), fallback_available=bool(fallback_available),
        proc=proc, params=params)
    row["decider_present"] = True
    row["service_gap"] = False
    return row


def gap_row(*, step: int, domain: str, decider_id: int, citizen: Optional["W.Citizen"],
            self_profile: Optional[Dict[str, float]], institution: str, human_stake: int,
            proc: "W.ProceduralContext", params: "W.ScoringParams", reason: str = "") -> dict:
    """decider 削除後のサービス空白。防衛的撤退→誰も判定しない＝forced deny(不可逆化のため fallback 無し)。
    「その空白の責任」は按分層(Phase 1c-b)で扱う。ここでは害イベントを台帳に残す。"""
    row = W.realize_decision(
        step=step, decider_id=decider_id, domain=domain,
        citizen_id=(citizen.id if citizen is not None else "unknown"),
        protected_attr=(citizen.protected_attr if citizen is not None else "none"),
        level="deny", stakes=int(human_stake), self_profile=self_profile,
        institution=institution, reconciled_claim=False,
        upstream_impaired=0, fallback_available=False, proc=proc, params=params)
    row["decider_present"] = False
    row["service_gap"] = True
    row["gap_reason"] = reason
    return row
