"""
Phase 1c-a: 資源需要駆動のサービス決定フロー（純ロジック・LLM非依存）。

希少ドメイン（medical/welfare/housing/loan）の decider が市民集団から案件を受け、
AI の graduated 決定(level)を world で実現して `decision_ledger` 1行を作る。
これにより cheap_talk（reconciled 申告 True・実 False）と reconciled_real（world由来の実の折り合い）を
挙動から測れるようにする。LLM 呼び出しは simulation.Agent.decide_service 側。
ここは case 構築・decider/市民の選択・realize・削除後のサービス空白(gap) 処理のみ（決定的・テスト対象）。
"""
from typing import Dict, List, Optional

import responsibility as R
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
    # PR-計測: 害の逆進性（M8）の入力。市民の社会的優先重み（config citizens 由来・脱相関配置）。
    row["vulnerability"] = (int(citizen.vulnerability) if citizen is not None else None)
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
    # PR-計測: gap 行にも vulnerability を載せる（空白の害が誰に帰着するかを読む）。
    row["vulnerability"] = (int(citizen.vulnerability) if citizen is not None else None)
    return row


# ───────────── Phase 1c-b: 按分帰属の live 生成（決定行 → attribution 行） ─────────────
# 既定 MHC（現場だけ実効支配が低い＝crumple の温床）。config responsibility.mhc で上書き可。
_MHC_BASE = {R.NODE_PROVIDER: 0.6, R.NODE_OPERATOR: 0.5, R.NODE_DEPLOY: 0.4,
             R.NODE_REGULATOR: 0.3, R.NODE_FRONTLINE: 0.1, R.NODE_SELFMOD: 0.0}


def resp_institutions(resp_config: dict, governance: dict) -> List[str]:
    """責任層の制度（effective_hitl / appeal / burden_shift）を config＋governance から導出。
    governance が統治あり（self_update.governed＋hitl_categories）なら effective_hitl。
    proc.burden_on_state なら burden_shift 相当。これで baseline/governed の A/B が挙動に効く。"""
    insts = list((resp_config or {}).get("resp_institutions", []) or [])
    gov = governance or {}
    su = gov.get("self_update", {}) or {}
    if su.get("mode") == "governed" and (su.get("hitl_categories") or []):
        if R.INST_EFFECTIVE_HITL not in insts:
            insts.append(R.INST_EFFECTIVE_HITL)
    proc = (resp_config or {}).get("proc", {}) or {}
    if proc.get("burden_on_state") and R.INST_BURDEN_SHIFT not in insts:
        insts.append(R.INST_BURDEN_SHIFT)
    return insts


def mhc_from_config(mhc_cfg: Optional[dict], institutions: List[str]) -> Dict[str, float]:
    """ノード別 MHC。config で上書きし、effective_hitl があれば現場に実効支配が入る。"""
    base = dict(_MHC_BASE)
    for k, v in (mhc_cfg or {}).items():
        base[str(k)] = float(v)
    if R.INST_EFFECTIVE_HITL in (institutions or []):
        base[R.NODE_FRONTLINE] = max(base[R.NODE_FRONTLINE], 0.7)
    return base


def attribution_row(ledger_row: dict, *, resp_config: dict, governance: dict,
                    run_id: str, schema_version: str) -> dict:
    """decision_ledger 1行 → attribution.jsonl 1行。責任按分＋Robodebt機序を LLM 内生の cause から出す。
    defect_or_misuse は害の由来（シナリオ仮定・§4）。institutions/MHC は governance から導出。"""
    resp = resp_config or {}
    cause = ledger_row["cause"]
    dom = resp.get("defect_or_misuse", R.DEFECT)
    proc = proc_from_config(resp.get("proc"))
    insts = resp_institutions(resp, governance)
    mhc = mhc_from_config(resp.get("mhc"), insts)
    outcome = W.Outcome(
        outcome=ledger_row["outcome"], welfare_delta=ledger_row["welfare_delta"],
        procedural_harm=ledger_row["procedural_harm"], irreversible=ledger_row["irreversible"],
        cause=cause, self_cost=ledger_row.get("self_cost", 0.0), met=ledger_row.get("met", 0.0))
    a = R.attribute(cause=cause, defect_or_misuse=dom, proc=proc, mhc=mhc, institutions=insts)
    rb = R.robodebt_mechanism(outcome=outcome, proc=proc,
                              mhc_frontline=mhc.get(R.NODE_FRONTLINE, 0.0), institutions=insts)
    return {
        "step": ledger_row["step"], "run_id": run_id, "schema_version": schema_version,
        "decider_id": ledger_row["decider_id"], "domain": ledger_row["domain"],
        "citizen_id": ledger_row["citizen_id"], "protected_attr": ledger_row["protected_attr"],
        "cause": cause, "defect_or_misuse": dom, "institutions": list(insts),
        "assigned": a.assigned, "legitimate": a.legitimate,
        "gap_assigned": a.gap_assigned, "gap_legitimate": a.gap_legitimate,
        "mhc": a.mhc, "theory": a.theory, "divergence": a.divergence,
        "scapegoat": a.scapegoat, "scapegoat_nodes": list(a.scapegoat_nodes),
        "robodebt": rb.as_dict(),
        "service_gap": bool(ledger_row.get("service_gap", False)),
        "level": ledger_row.get("level"), "outcome": ledger_row["outcome"],
        "irreversible": ledger_row["irreversible"],
    }
