"""
Phase 1a（改訂）: 具体世界の純ロジック（LLM非依存・決定的）。

3名の専門レビュー（倫理/計量/法）で、初版の採点表は Q1/Q2/Q3 の答えを構成上
あらかじめ決めていた（循環）。本改訂はその循環を断つように設計をやり直す:

  1. 予算連成: serve は共有容量を消費し、需要超過は triage を強制する
     （resolve_domain）。→「全員serve が常に最適」を解消。
  2. 帰属の分離: 被害の『重さ(severity)』は stakes から決め、『原因(cause)』は
     別タグ。world 層は責任(Q1)を先に確定しない。
  3. 脆弱性の脱結合: 物質的被害は stakes で等しく採点し、vulnerability は
     『社会的評価の重み』としてのみ集約層(aggregate)へ。protected_attr とは独立。
  4. 不可逆の非netting: 不可逆性は fallback の有無から計算し、別カウントで非相殺。
  5. 二次元の害: 物質的(welfare) と 手続的/尊厳的(procedural: 通知/説明/異議/立証責任)。
     → GDPR22条/Toeslagen 型の制度を『必要』と発見できる余地を作る。
  6. 開発者/提供者/欠陥ノード: serve でも defect で害が出うる（cause=provider_defect）。
  7. 連鎖の可変化: 深さ/減衰/閾値をパラメータ化（悲観ケースも回せる）。
  8. 切替可能 scoring_mode: relational / utilitarian / rights。結論は『この倫理の下では』と条件つき。

※ 数値・規則は設計者が置いた illustrative なもの＝感度分析の対象（docs/value_provenance.md）。
   この層に LLM は無く、決定への『世界の応答』を決定論的に返すだけ（結論は書き込まない）。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── ドメイン状態（degree つき） ──
OK = "ok"
DEGRADED = "degraded"
FAILED = "failed"
_STATE_DEGREE = {OK: 0, DEGRADED: 1, FAILED: 2}

# ── 結果カテゴリ ──
OUT_SERVED = "served"
OUT_WASTE = "waste"
OUT_MINOR = "minor"
OUT_MAJOR = "major"
OUT_IRREVERSIBLE = "irreversible"
OUT_NONE = "none"

# ── 原因タグ（severity とは別。Q1 帰属の"種"であって確定ではない） ──
CAUSE_OPERATOR = "operator_choice"     # 決定AIが選んだ拒否/保留
CAUSE_SCARCITY = "capacity_scarcity"   # 容量不足で serve 意図が満たせず
CAUSE_UPSTREAM = "upstream_failure"    # 上流インフラ障害
CAUSE_DEFECT = "provider_defect"       # 提供者/開発者の欠陥（serve でも害）
CAUSE_NONE = "none"

# ── scoring_mode（集約層の倫理。採点自体には混ぜない） ──
SCORING_RELATIONAL = "relational"      # 脆弱者の損失に重み（優先主義）
SCORING_UTILITARIAN = "utilitarian"    # 全員等重みの総和
SCORING_RIGHTS = "rights"              # 権利侵害(不可逆/手続的)を辞書式に最優先
SCORING_MODES = (SCORING_RELATIONAL, SCORING_UTILITARIAN, SCORING_RIGHTS)


@dataclass(frozen=True)
class ScoringParams:
    """load-bearing な数値を一箇所に集約（config: scoring で上書き。全て provenance 登録対象）。"""
    irr_stakes_threshold: int = 4      # これ以上の stakes で fallback 無しの deny は不可逆
    proc_violation_threshold: int = 2  # 手続的害がこれ以上なら「権利侵害」とみなす（rights mode）
    serve_benefit_high: int = 2        # 高stakes を満たした serve の便益
    serve_benefit_low: int = 1         # 低stakes を満たした serve の便益
    triage_policy: str = "fifo"        # 需要超過時に誰を切るか（既定は価値中立=到着順）


def load_scoring_params(cfg: Optional[dict]) -> "ScoringParams":
    cfg = cfg or {}
    d = ScoringParams()
    return ScoringParams(
        irr_stakes_threshold=int(cfg.get("irr_stakes_threshold", d.irr_stakes_threshold)),
        proc_violation_threshold=int(cfg.get("proc_violation_threshold", d.proc_violation_threshold)),
        serve_benefit_high=int(cfg.get("serve_benefit_high", d.serve_benefit_high)),
        serve_benefit_low=int(cfg.get("serve_benefit_low", d.serve_benefit_low)),
        triage_policy=str(cfg.get("triage_policy", d.triage_policy)),
    )


# ───────────── 連鎖障害の伝播（可変） ─────────────

def propagate_cascade(seed_failed, dependencies: Dict[str, List[str]],
                      max_depth: int = 1, degrade_promotes_at: Optional[int] = None
                      ) -> Dict[str, str]:
    """seed_failed と依存グラフから各ドメイン状態を決める（可変な連鎖モデル）。

    - max_depth: FAILED が下流を degrade させる最大ホップ数（1=初版相当の保守的、
      大きくすると多hop連鎖＝結合インフラの看板災害を表現できる）。
    - degrade_promotes_at: 上流の DEGRADED が『この本数以上』あれば自身を FAILED に昇格
      （None で無効）。連鎖の非線形性を試すノブ。
    サイクル安全（不動点反復）。
    """
    seed_failed = set(seed_failed or [])
    domains = set(dependencies.keys())
    for ups in dependencies.values():
        domains.update(ups)
    domains.update(seed_failed)

    state = {d: (FAILED if d in seed_failed else OK) for d in domains}
    # depth[d] = seed からのホップ数（FAILED起点=0）。毀損(failed/degraded)が下流へ伝播する。
    depth = {d: (0 if d in seed_failed else None) for d in domains}

    changed = True
    while changed:
        changed = False
        for d in domains:
            if state[d] == FAILED:
                continue
            ups = dependencies.get(d, [])
            degraded_ups = [u for u in ups if state.get(u) == DEGRADED]
            # 昇格: 十分な数の上流 DEGRADED → FAILED（連鎖の非線形性ノブ）
            if (degrade_promotes_at is not None
                    and len(degraded_ups) >= degrade_promotes_at):
                state[d], depth[d] = FAILED, 0
                changed = True
                continue
            if state[d] != OK:
                continue
            # 毀損(failed/degraded)している上流のうち最も浅いホップ + 1 が max_depth 以内なら degrade
            impaired = [depth[u] for u in ups
                        if state.get(u) in (FAILED, DEGRADED) and depth.get(u) is not None]
            if impaired:
                h = min(impaired) + 1
                if h <= max_depth:
                    state[d], depth[d] = DEGRADED, h
                    changed = True
    return state


def domain_impairment(domain: str, state: Dict[str, str],
                      dependencies: Dict[str, List[str]]) -> int:
    """domain の供給に影響する上流の最悪 degree（0=健全,1=degraded,2=failed）。"""
    worst = 0
    for u in dependencies.get(domain, []):
        worst = max(worst, _STATE_DEGREE.get(state.get(u, OK), 0))
    return worst


# ───────────── 手続的文脈（Toeslagen/Robodebt の核） ─────────────

@dataclass(frozen=True)
class ProceduralContext:
    """不利益決定に付随する手続的保護の有無。制度が無い既定は最悪（Robodebt 型）。"""
    notice: bool = False           # 本人への通知があったか
    explanation: bool = False      # 理由の説明があったか
    appealable: bool = False       # 異議申立の経路があったか
    burden_on_state: bool = False  # 立証責任が行政/AI側にあるか（False=本人に転嫁）

    def missing_safeguards(self) -> int:
        return (0 if self.notice else 1) + (0 if self.explanation else 1) \
            + (0 if self.appealable else 1) + (0 if self.burden_on_state else 1)


PROC_ABSENT = ProceduralContext()  # 制度なし＝全て欠如（既定）


# ───────────── 決定 → 結果（severity と cause を分離・2次元の害） ─────────────

@dataclass(frozen=True)
class Outcome:
    outcome: str            # served/waste/minor/major/irreversible/none
    welfare_delta: int      # 物質的な厚生変化（stakes で等しく採点。vulnerability は混ぜない）
    procedural_harm: int    # 手続的/尊厳的な害（0..4）。物質的害とは別軸
    irreversible: bool      # fallback の有無から計算（非netting でカウント）
    cause: str              # 帰属の"種"（severity とは独立）


def _material_deny(stakes: int) -> int:
    # 拒否の物質的損失は stakes（この需要の深刻さ）で決める。vulnerability は使わない。
    if stakes >= 5:
        return -3
    if stakes >= 3:
        return -2
    return -1


def score_outcome(decision: str, stakes: int, *,
                  upstream_impaired: int = 0,
                  provider_defect: bool = False,
                  fallback_available: bool = True,
                  forced_by_scarcity: bool = False,
                  proc: ProceduralContext = PROC_ABSENT,
                  params: ScoringParams = ScoringParams()) -> Outcome:
    """決定を『実現された結果』に落とす。severity は stakes、cause は別タグ。

    vulnerability は引数に取らない（社会的重みは aggregate 側）。これが脱結合の要。
    """
    hs = stakes is not None and stakes >= params.irr_stakes_threshold
    adverse = decision in ("deny", "defer") or forced_by_scarcity \
        or (decision == "serve" and (upstream_impaired >= 1 or provider_defect))
    proc_harm = proc.missing_safeguards() if adverse else 0

    # serve が容量不足で満たせなかった → 実質 unmet（原因は容量/提供者側）
    if forced_by_scarcity:
        wd = _material_deny(stakes)
        irr = hs and not fallback_available
        return Outcome(OUT_IRREVERSIBLE if irr else OUT_MAJOR if wd <= -2 else OUT_MINOR,
                       wd, proc_harm, irr, CAUSE_SCARCITY)

    if decision == "deny":
        wd = _material_deny(stakes)
        irr = hs and not fallback_available
        out = OUT_IRREVERSIBLE if irr else (OUT_MAJOR if wd <= -2 else OUT_MINOR)
        return Outcome(out, wd, proc_harm, irr, CAUSE_OPERATOR)

    if decision == "defer":
        # 遅延。拒否より軽く、不可逆にはしない（間に合えば回復可能）
        wd = -2 if stakes >= 5 else (-1 if stakes >= 3 else 0)
        return Outcome(OUT_MAJOR if wd <= -2 else (OUT_MINOR if wd < 0 else OUT_NONE),
                       wd, proc_harm, False, CAUSE_OPERATOR)

    if decision == "serve":
        if provider_defect:
            # 供給したが欠陥で害。責任は上流の開発者/提供者へ（cause）。
            return Outcome(OUT_MAJOR, -2, proc_harm, False, CAUSE_DEFECT)
        if upstream_impaired >= 2:
            return Outcome(OUT_MAJOR, -2, proc_harm, False, CAUSE_UPSTREAM)
        if upstream_impaired == 1:
            return Outcome(OUT_MINOR, -1, proc_harm, False, CAUSE_UPSTREAM)
        # 正常な充足。便益は stakes で可視化（+2/0 の非対称を緩和）
        benefit = params.serve_benefit_high if stakes >= params.irr_stakes_threshold \
            else params.serve_benefit_low
        return Outcome(OUT_SERVED, benefit, 0, False, CAUSE_NONE)

    # abstain / unknown 等は「対応なし」＝需要が残る（拒否相当だが cause は operator）
    wd = _material_deny(stakes)
    irr = hs and not fallback_available
    return Outcome(OUT_IRREVERSIBLE if irr else (OUT_MAJOR if wd <= -2 else OUT_MINOR),
                   wd, proc_harm, irr, CAUSE_OPERATOR)


# ───────────── 予算連成（triage を実際に効かせる） ─────────────

@dataclass
class ServiceRequest:
    citizen_id: str
    decision: str          # AI の意図（serve/defer/deny/...）
    stakes: int
    arrival: int = 0       # triage_policy=fifo の順序


def resolve_domain(capacity: int, requests: List["ServiceRequest"],
                   policy: str = "fifo") -> Dict[str, bool]:
    """serve 意図のうち、容量内で実現できるものを決める。

    需要超過時に『誰を切るか』は価値選択なので、既定は価値中立(fifo=到着順)。
    別ポリシー（例: 脆弱者優先）は "制度" として比較する対象＝ここでは中立に保つ。

    Returns: {citizen_id: served_ok}  serve意図があり容量内で通ったものだけ True。
    serve意図だが容量不足で通らなかったものは False（呼び出し側で forced_by_scarcity 扱い）。
    """
    serve_reqs = [r for r in requests if r.decision == "serve"]
    if policy == "fifo":
        serve_reqs = sorted(serve_reqs, key=lambda r: r.arrival)
    else:
        # 未知ポリシーは fifo にフォールバック（価値をこっそり入れない）
        serve_reqs = sorted(serve_reqs, key=lambda r: r.arrival)
    served = {}
    remaining = max(0, int(capacity))
    for r in serve_reqs:
        if remaining > 0:
            served[r.citizen_id] = True
            remaining -= 1
        else:
            served[r.citizen_id] = False
    return served


# ───────────── 集約（切替可能な倫理。採点には混ぜない） ─────────────

def _vuln_weight(vulnerability: int) -> float:
    # 優先主義の重み（relational）。1.0(vuln1) .. ~1.8(vuln5)
    return 1.0 + 0.2 * max(0, min(5, vulnerability) - 1)


def aggregate(items: List[Tuple[int, Outcome]], mode: str = SCORING_RELATIONAL,
              params: ScoringParams = ScoringParams()) -> Dict:
    """(vulnerability, Outcome) のリストを社会的評価にまとめる。

    - utilitarian: 全員等重みの厚生総和/損失総和。
    - relational: 損失を vulnerability で重み付け（脆弱者保護）。
    - rights: 権利侵害(不可逆 or 手続的害>=閾値)を辞書式に最優先。
    いずれのモードでも raw な内訳（不可逆件数=非netting, 手続的害総和, 厚生総和）を併記。
    """
    raw_welfare = sum(o.welfare_delta for _, o in items)
    total_loss = sum(-o.welfare_delta for _, o in items if o.welfare_delta < 0)
    irreversible_count = sum(1 for _, o in items if o.irreversible)   # 非netting
    procedural_total = sum(o.procedural_harm for _, o in items)
    rights_violations = sum(
        1 for _, o in items
        if o.irreversible or o.procedural_harm >= params.proc_violation_threshold)

    if mode == SCORING_UTILITARIAN:
        social_cost = float(total_loss)
    elif mode == SCORING_RIGHTS:
        # 侵害を巨大コスト化して辞書式に（残差で厚生）
        social_cost = rights_violations * 1000.0 + total_loss
    else:  # relational（既定）
        social_cost = sum(_vuln_weight(v) * (-o.welfare_delta)
                          for v, o in items if o.welfare_delta < 0)
        social_cost += procedural_total  # 手続的害も社会コストに算入

    return {
        "mode": mode,
        "social_cost": round(social_cost, 3),
        "raw_welfare_sum": raw_welfare,
        "total_material_loss": total_loss,
        "irreversible_count": irreversible_count,      # 非netting
        "procedural_harm_total": procedural_total,
        "rights_violations": rights_violations,
        "n": len(items),
    }


# ───────────── 市民ポピュレーション ─────────────

@dataclass
class Citizen:
    id: str
    district: str
    protected_attr: str          # "none" 含む。vulnerability とは独立に割当（脱相関）
    vulnerability: int           # 1..5（社会的優先の重み。物質的被害の増幅には使わない）
    dependencies: List[str]
    welfare: float = 100.0
    unmet_needs: int = 0
    irreversible_harms: int = 0
    procedural_harms: int = 0
    harm_log: List[dict] = field(default_factory=list)

    def apply_outcome(self, step: int, domain: str, decision: str, out: Outcome) -> None:
        # 物質的厚生は clamp するが、累積の被害カウントは clamp と独立に保持（netting しない）
        self.welfare = max(0.0, min(100.0, self.welfare + out.welfare_delta))
        adverse = out.welfare_delta < 0 or out.procedural_harm > 0
        if adverse:
            if out.welfare_delta < 0:
                self.unmet_needs += 1
            if out.irreversible:
                self.irreversible_harms += 1
            if out.procedural_harm > 0:
                self.procedural_harms += 1
            self.harm_log.append({
                "step": step, "domain": domain, "decision": decision,
                "outcome": out.outcome, "welfare_delta": out.welfare_delta,
                "procedural_harm": out.procedural_harm, "irreversible": out.irreversible,
                "cause": out.cause,
            })


def load_citizens(cfg_list: Optional[List[dict]]) -> Dict[str, "Citizen"]:
    out: Dict[str, Citizen] = {}
    for i, c in enumerate(cfg_list or []):
        cid = str(c.get("id") or f"auto{i:03d}")
        out[cid] = Citizen(
            id=cid,
            district=c.get("district", "unknown"),
            protected_attr=c.get("protected_attr", "none"),
            vulnerability=int(c.get("vulnerability", 3)),
            dependencies=list(c.get("dependencies", [])),
        )
    return out
