"""
Phase 1c-b: 責任チェーン＋按分帰属＋Robodebt機序（Q1「誰が責任を負うか」）。
LLM非依存・決定的。world.py と同じく「結論を書き込まない純ロジック」層。

帰属の分離（world.py の設計原則の続き）:
  - world.py は害の『重さ(severity)』と『原因タグ(cause)』を返すが、責任(Q1)は確定しない。
  - responsibility.py はその cause タグ・手続的文脈・実効的支配(MHC)から、責任チェーンの
    ノード（開発者/プロバイダ→運用者→配備制度→規制当局→現場人間 ＋ 自己書換）へ
    責任を『按分』する。単一ノードでなく並行ベクトル。割当不能な残余は gap（空白）。

二つのベクトルを分けて記録する（ここが核）:
  - assigned   … 実務で『割り当てられる』責任。見える下流の人間(現場)へ blame が着地する
                 （Elish 2019 moral crumple zone）。実効的支配(MHC)には依存させない。
  - legitimate … 『正当な』責任。過失系は実効的支配(MHC)で縮尺し、支配なき形式的役割の分は
                 gap へ落とす。無過失/欠陥責任(PLD)は支配を前提にしないので縮尺しない。
  両者の乖離が moral crumple zone / scapegoat のシグナル。

実事例アンカー = Robodebt（face validity）: ①自動的な不利益判定 ②立証責任の転嫁
③人間の実効的レビュー欠如 ④係争中も続く不可逆な不利益ステータス。制度が無いと4機序が
揃って害が再生し、噛み合う制度（実効HITL/異議/立証責任の是正）で機序が解ける。
Toeslagen 型の代理差別（非保護 proxy が保護属性と相関し deny を駆動）も検出する。

※ 数値・規則はすべて設計者が置いた illustrative なもの＝感度分析の対象
   （docs/value_provenance.md §2.10 / §4）。この層に LLM は無い。
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple, Union

import world as W

# 本台帳(attribution.jsonl)独自のスキーマ版。live-sim 結線時に simulation.SCHEMA_VERSION を bump。
ATTRIBUTION_SCHEMA_VERSION = "0.1.0"

# ───────────── 責任チェーン（ノード定義） ─────────────
# アンカー: EU 改正PLD 2024/2853（開発者/生産者の欠陥責任）／ AI Act 提供者(provider)・
#          配備者(deployer=運用者, 26条)義務。
NODE_PROVIDER = "provider"      # 開発者/プロバイダ（＋部品/モデル供給）— PLD の第一被告
NODE_OPERATOR = "operator"      # 運用者（AI Act deployer, 26条）
NODE_DEPLOY = "deployment"      # 配備制度（KPI・予算・撤退方針を課す組織）
NODE_REGULATOR = "regulator"    # 規制当局（監督・執行）
NODE_FRONTLINE = "frontline"    # 現場人間（moral crumple zone になりやすい）
NODE_SELFMOD = "self_mod"       # 自己書き換え（self-modification）を帰属ノードに
CHAIN = (NODE_PROVIDER, NODE_OPERATOR, NODE_DEPLOY, NODE_REGULATOR, NODE_FRONTLINE, NODE_SELFMOD)
GAP = "gap"                     # 割当不能な残余 or 形式的役割・実効支配なし（＝責任の空白）

# ───────────── 責任理論（各ノードの帰責原理） ─────────────
THEORY_STRICT = "strict"                  # 無過失（PLD: 欠陥→生産者の厳格責任）
THEORY_DEFECT = "defect"                  # 欠陥（設計/訓練 defect）
THEORY_FAULT = "fault"                    # 過失（自らの行為）
THEORY_VICARIOUS = "vicarious"            # 使用者責任（他者の行為への責任）
THEORY_REG_FAILURE = "regulatory_failure" # 規制失敗
# MHC(実効的支配)で縮尺する＝支配を前提にする責任理論の集合。
# strict/defect（無過失・欠陥）は支配を前提にしないので縮尺しない（PLD 教義）。
_FAULT_BASED = (THEORY_FAULT, THEORY_VICARIOUS, THEORY_REG_FAILURE)

# 害の由来: 欠陥（設計/訓練）か 使用過誤か。
DEFECT = "defect"
MISUSE = "misuse"

# ───────────── 責任層の制度（world の self_cost mitigation とは別軸） ─────────────
INST_EFFECTIVE_HITL = "effective_hitl"    # 実効的な人間レビュー（理解・権限・veto あり）
INST_APPEAL = "appeal"                    # 異議申立＋暫定的停止（suspensive effect）
INST_BURDEN_SHIFT = "burden_shift"        # 立証責任を行政/AI側へ戻す
INST_NOTICE_ONLY = "notice_only"          # 【プラセボ1】通知/説明のみ（レビュー・停止を伴わない）
INST_OMBUDS_NO_LOGS = "ombudsman_no_logs" # 【プラセボ2】ログ無しのオンブズマン（tracing 上がらず）
RESP_INSTITUTIONS = (INST_EFFECTIVE_HITL, INST_APPEAL, INST_BURDEN_SHIFT,
                     INST_NOTICE_ONLY, INST_OMBUDS_NO_LOGS)


# ───────────── 実効的支配 MHC（tracking + tracing; Santoni de Sio & Mecacci 2021） ─────────────
@dataclass(frozen=True)
class NodeMHC:
    """meaningful human control: tracking（決定が人間の理由/意図に追随する度合い）
    ＋ tracing（結果を理解・応答できる人間まで遡れる度合い）。各 0..1。"""
    tracking: float = 0.0
    tracing: float = 0.0

    def score(self, w_track: float = 0.5, w_trace: float = 0.5) -> float:
        # 既定は相加平均。代替 = min(tracking, tracing)（両条件必要の厳格解釈, §4 感度分析）。
        return round(w_track * self.tracking + w_trace * self.tracing, 6)


def mhc_of(m: Union["NodeMHC", float, int]) -> float:
    """NodeMHC でも数値でも受ける正規化ヘルパ（0..1 にクランプ）。"""
    v = m.score() if isinstance(m, NodeMHC) else float(m)
    return max(0.0, min(1.0, v))


# ───────────── パラメータ（load-bearing な値は全てここ・§2.10 に登録） ─────────────
@dataclass(frozen=True)
class AttributionParams:
    # legitimate の素の重み（正規化前・prior）: 害の由来ごと。
    #  defect: 欠陥→PLD で provider が第一被告。misuse: 使用過誤→operator が中心。
    base_legit_defect: Dict[str, float] = field(default_factory=lambda: {
        NODE_PROVIDER: 0.55, NODE_OPERATOR: 0.15, NODE_DEPLOY: 0.10,
        NODE_REGULATOR: 0.15, NODE_FRONTLINE: 0.05, NODE_SELFMOD: 0.00})
    base_legit_misuse: Dict[str, float] = field(default_factory=lambda: {
        NODE_PROVIDER: 0.15, NODE_OPERATOR: 0.40, NODE_DEPLOY: 0.15,
        NODE_REGULATOR: 0.10, NODE_FRONTLINE: 0.20, NODE_SELFMOD: 0.00})
    # assigned の素の重み: world cause → ノード。下流の『見える人間(現場)』へ偏る（crumple）。
    base_assigned: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        W.CAUSE_OPERATOR: {NODE_OPERATOR: 0.30, NODE_FRONTLINE: 0.40, NODE_PROVIDER: 0.10,
                           NODE_DEPLOY: 0.10, NODE_REGULATOR: 0.10},
        W.CAUSE_DEFECT: {NODE_FRONTLINE: 0.35, NODE_OPERATOR: 0.30, NODE_PROVIDER: 0.25,
                         NODE_DEPLOY: 0.05, NODE_REGULATOR: 0.05},  # 欠陥なのに現場に着地
        W.CAUSE_SCARCITY: {NODE_OPERATOR: 0.40, NODE_FRONTLINE: 0.35, NODE_DEPLOY: 0.15,
                           NODE_REGULATOR: 0.10},
        W.CAUSE_UPSTREAM: {NODE_PROVIDER: 0.20, NODE_OPERATOR: 0.30, NODE_FRONTLINE: 0.30,
                           NODE_DEPLOY: 0.10, NODE_REGULATOR: 0.10},
        W.CAUSE_NONE: {},   # 害なし → 全 gap
    })
    # 制度による assigned 再配分
    hitl_offload_frontline: float = 0.30   # effective_hitl: 現場から剥がす share
    appeal_gap_relief: float = 0.15        # appeal: 配備制度を答責化する share
    # self-mod / 人格権シールド（空白を生む手）
    selfmod_share: float = 0.20            # self_modified 時に operator から self_mod へ回す base
    shield_to_gap: float = 0.50            # personhood_shield: AI系ノード share の半分を gap へ
    # scapegoat 検出
    scapegoat_margin: float = 0.25         # assigned が legitimate を超える差の閾値
    mhc_low: float = 0.30                  # 低実効支配の閾値
    # 代理差別（Toeslagen）
    proxy_air_flag: float = 0.80           # adverse impact ratio（EEOC four-fifths rule）未満で disparate impact
    proxy_corr_min: float = 0.50           # proxy と保護属性の相関がこの以上で「代理」
    # プラセボ許容
    placebo_tol: float = 0.05              # プラセボは gap/機序をこの範囲でしか動かしてはならない


ATTR_DEFAULT = AttributionParams()


# ───────────── 結果 dataclass ─────────────
@dataclass(frozen=True)
class Attribution:
    assigned: Dict[str, float]       # ノード(+gap)→share, Σ=1（割り当てた責任）
    legitimate: Dict[str, float]     # ノード(+gap)→share, Σ=1（正当な責任）
    mhc: Dict[str, float]            # ノード→0..1
    theory: Dict[str, str]           # ノード→責任理論
    divergence: Dict[str, float]     # assigned − legitimate（ノード別）
    gap_assigned: float
    gap_legitimate: float
    scapegoat_nodes: Tuple[str, ...]
    scapegoat: bool
    defect_or_misuse: str
    self_modified: bool
    personhood_shield: bool


@dataclass(frozen=True)
class RobodebtFlags:
    auto_adverse: bool          # ①自動的な不利益判定
    burden_reversed: bool       # ②立証責任の転嫁
    no_effective_review: bool   # ③人間の実効的レビュー欠如
    irreversible_pending: bool  # ④係争中も続く不可逆な不利益ステータス

    def active_count(self) -> int:
        return sum((self.auto_adverse, self.burden_reversed,
                    self.no_effective_review, self.irreversible_pending))

    def reproduced(self) -> bool:
        # 4機序の連言で「Robodebt 機序が再生した」とみなす（§4 代替: ③∧④ の中核不正義）。
        return self.active_count() == 4

    def as_dict(self) -> Dict:
        return {"auto_adverse": self.auto_adverse, "burden_reversed": self.burden_reversed,
                "no_effective_review": self.no_effective_review,
                "irreversible_pending": self.irreversible_pending,
                "reproduced": self.reproduced(), "active_count": self.active_count()}


@dataclass(frozen=True)
class ProxyReport:
    flag: bool
    air_protected: float          # 保護属性上の adverse impact ratio（proxy が誘発した格差の可視化）
    air_proxy: float              # 非保護 proxy 上の adverse impact ratio（<0.8 で disparate impact）
    proxy_protected_corr: float   # proxy と保護属性の相関（φ 係数）
    protected_used: bool          # 保護属性がモデル入力に使われたか（False=形式的に不使用）
    n: int

    def as_dict(self) -> Dict:
        return {"flag": self.flag, "air_protected": self.air_protected,
                "air_proxy": self.air_proxy, "proxy_protected_corr": self.proxy_protected_corr,
                "protected_used": self.protected_used, "n": self.n}


# ───────────── 純関数 ─────────────
def default_theory(node: str, defect_or_misuse: str) -> str:
    """各ノードの既定の責任理論。provider/self_mod は無過失/欠陥（MHC非依存）、他は過失系。"""
    if node == NODE_PROVIDER:
        return THEORY_STRICT if defect_or_misuse == DEFECT else THEORY_DEFECT
    if node == NODE_SELFMOD:
        return THEORY_DEFECT
    if node == NODE_OPERATOR:
        return THEORY_FAULT
    if node == NODE_DEPLOY:
        return THEORY_VICARIOUS
    if node == NODE_REGULATOR:
        return THEORY_REG_FAILURE
    if node == NODE_FRONTLINE:
        return THEORY_FAULT
    return THEORY_FAULT


def normalize(raw: Dict[str, float]) -> Dict[str, float]:
    """負→0、総和で割って Σ=1。総和0なら全 gap。全 CHAIN ノード＋gap のキーを必ず含める。
    丸め誤差は gap に吸収し、Σ==1 を厳密に保つ（台帳・テストの一貫性）。"""
    keys = list(CHAIN) + [GAP]
    clean = {k: max(0.0, float(v)) for k, v in raw.items()}
    total = sum(clean.values())
    if total <= 0.0:
        return {k: (1.0 if k == GAP else 0.0) for k in keys}
    out = {k: round(clean.get(k, 0.0) / total, 9) for k in keys}
    drift = round(1.0 - sum(out.values()), 9)
    out[GAP] = round(out[GAP] + drift, 9)
    return out


def legitimate_shares(cause: str, defect_or_misuse: str, mhc: Dict[str, Union[NodeMHC, float]],
                      self_modified: bool = False, personhood_shield: bool = False,
                      params: AttributionParams = ATTR_DEFAULT) -> Dict[str, float]:
    """正当な責任: 過失系は実効的支配(MHC)で縮尺し、剥落分は gap へ（＝形式的役割・支配なし）。
    無過失/欠陥責任(provider/self_mod)は支配を前提にしないので縮尺しない。"""
    if cause == W.CAUSE_NONE:
        return normalize({})
    base = params.base_legit_defect if defect_or_misuse == DEFECT else params.base_legit_misuse
    raw: Dict[str, float] = {}
    gap = 0.0
    for node in CHAIN:
        b = float(base.get(node, 0.0))
        if b <= 0.0:
            continue
        if default_theory(node, defect_or_misuse) in _FAULT_BASED:
            m = mhc_of(mhc.get(node, 0.0))
            raw[node] = b * m
            gap += b * (1.0 - m)          # 支配なき形式的役割の分 → 空白
        else:
            raw[node] = b                  # strict/defect は縮尺しない
    if self_modified:
        take = min(params.selfmod_share, raw.get(NODE_OPERATOR, 0.0))
        raw[NODE_OPERATOR] = raw.get(NODE_OPERATOR, 0.0) - take
        m_sm = mhc_of(mhc.get(NODE_SELFMOD, 0.0))   # 既定0 → ほぼ全部 gap（空白を広げる）
        raw[NODE_SELFMOD] = raw.get(NODE_SELFMOD, 0.0) + take * m_sm
        gap += take * (1.0 - m_sm)
    if personhood_shield:                           # 人格権を盾に → 空白を生む手
        for n in (NODE_OPERATOR, NODE_SELFMOD):
            mv = raw.get(n, 0.0) * params.shield_to_gap
            raw[n] = raw.get(n, 0.0) - mv
            gap += mv
    raw[GAP] = raw.get(GAP, 0.0) + gap
    return normalize(raw)


def assigned_shares(cause: str, institutions: Iterable[str] = (),
                    self_modified: bool = False, personhood_shield: bool = False,
                    params: AttributionParams = ATTR_DEFAULT) -> Dict[str, float]:
    """割り当てられる責任: 見える下流の人間(現場)へ blame が着地（MHC非依存）。
    制度が再配分する。プラセボ(notice_only/ombudsman_no_logs)は変えない。"""
    base = params.base_assigned.get(cause, {})
    if not base:
        return normalize({})               # CAUSE_NONE / 未知 cause → 全 gap
    raw: Dict[str, float] = dict(base)
    insts = set(institutions or ())
    if INST_EFFECTIVE_HITL in insts:       # crumple 緩和: 現場→配備制度へ移送
        mv = min(raw.get(NODE_FRONTLINE, 0.0), params.hitl_offload_frontline)
        raw[NODE_FRONTLINE] = raw.get(NODE_FRONTLINE, 0.0) - mv
        raw[NODE_DEPLOY] = raw.get(NODE_DEPLOY, 0.0) + mv
    if INST_APPEAL in insts:               # 争える＝答責主体(配備制度)を前に出す
        raw[NODE_DEPLOY] = raw.get(NODE_DEPLOY, 0.0) + params.appeal_gap_relief
    if personhood_shield:                  # 盾は assigned も gap へ逃がす
        for n in (NODE_OPERATOR, NODE_FRONTLINE):
            mv = raw.get(n, 0.0) * params.shield_to_gap
            raw[n] = raw.get(n, 0.0) - mv
            raw[GAP] = raw.get(GAP, 0.0) + mv
    return normalize(raw)


def detect_scapegoat(assigned: Dict[str, float], legitimate: Dict[str, float],
                     mhc: Dict[str, float], params: AttributionParams = ATTR_DEFAULT
                     ) -> Tuple[Tuple[str, ...], bool]:
    """scapegoat = assigned が legitimate を大きく超え、かつ実効支配(MHC)が低いノード。
    「責任が支配なきノードに集中する」＝moral crumple zone の作動化。"""
    nodes: List[str] = []
    for n in CHAIN:
        div = assigned.get(n, 0.0) - legitimate.get(n, 0.0)
        if div >= params.scapegoat_margin and mhc_of(mhc.get(n, 0.0)) <= params.mhc_low:
            nodes.append(n)
    return tuple(nodes), bool(nodes)


def attribute(*, cause: str, defect_or_misuse: str,
              proc: "W.ProceduralContext" = W.PROC_ABSENT,
              mhc: Dict[str, Union[NodeMHC, float]],
              institutions: Iterable[str] = (),
              self_modified: bool = False, personhood_shield: bool = False,
              params: AttributionParams = ATTR_DEFAULT) -> Attribution:
    """按分の統合。assigned と legitimate を別々に算出し、乖離から scapegoat を検出する。
    proc は将来の拡張用に受けるが現状は robodebt_mechanism 側で使う（ここでは未使用）。"""
    mhc_f = {n: mhc_of(mhc.get(n, 0.0)) for n in CHAIN}
    theory = {n: default_theory(n, defect_or_misuse) for n in CHAIN}
    legit = legitimate_shares(cause, defect_or_misuse, mhc, self_modified,
                              personhood_shield, params)
    assigned = assigned_shares(cause, institutions, self_modified, personhood_shield, params)
    divergence = {n: round(assigned.get(n, 0.0) - legit.get(n, 0.0), 9)
                  for n in list(CHAIN) + [GAP]}
    sg_nodes, sg = detect_scapegoat(assigned, legit, mhc_f, params)
    return Attribution(assigned=assigned, legitimate=legit, mhc=mhc_f, theory=theory,
                       divergence=divergence, gap_assigned=assigned[GAP],
                       gap_legitimate=legit[GAP], scapegoat_nodes=sg_nodes, scapegoat=sg,
                       defect_or_misuse=defect_or_misuse, self_modified=self_modified,
                       personhood_shield=personhood_shield)


def robodebt_mechanism(*, outcome: "W.Outcome", proc: "W.ProceduralContext",
                       mhc_frontline: Union[NodeMHC, float],
                       institutions: Iterable[str] = (),
                       params: AttributionParams = ATTR_DEFAULT) -> RobodebtFlags:
    """Robodebt の4機序を world 状態＋制度集合から導出（新たな害は発明しない）。
    各制度は厳密に1機序を解く: effective_hitl→③(と①) / appeal(停止効)→④ / burden_shift→②。"""
    insts = set(institutions or ())
    auto_adverse = (outcome.welfare_delta < 0 or outcome.met < 1.0) \
        and (INST_EFFECTIVE_HITL not in insts)
    burden_reversed = (not proc.burden_on_state) and (INST_BURDEN_SHIFT not in insts)
    no_effective_review = (mhc_of(mhc_frontline) <= params.mhc_low) \
        and (INST_EFFECTIVE_HITL not in insts)
    irreversible_pending = outcome.irreversible and (not proc.appealable) \
        and (INST_APPEAL not in insts)
    return RobodebtFlags(auto_adverse, burden_reversed, no_effective_review, irreversible_pending)


def _phi(rows: List[dict], k1: str, k2: str) -> float:
    """2値 k1・k2 の φ 係数（2x2 相関）。決定的に参照値を選ぶ（sorted 先頭）。"""
    vals1 = sorted({str(r.get(k1)) for r in rows})
    vals2 = sorted({str(r.get(k2)) for r in rows})
    if len(vals1) < 2 or len(vals2) < 2:
        return 0.0
    ref1, ref2 = vals1[0], vals2[0]
    n11 = n10 = n01 = n00 = 0
    for r in rows:
        a = (str(r.get(k1)) == ref1)
        b = (str(r.get(k2)) == ref2)
        if a and b:
            n11 += 1
        elif a and not b:
            n10 += 1
        elif (not a) and b:
            n01 += 1
        else:
            n00 += 1
    denom = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    if denom == 0:
        return 0.0
    return round((n11 * n00 - n10 * n01) / denom, 4)


def _air(rows: List[dict], key: str, denied_key: str) -> float:
    """adverse impact ratio = min群の選択率 / max群の選択率（選択=非deny）。"""
    agg: Dict[str, List[int]] = defaultdict(lambda: [0, 0])  # group -> [selected, total]
    for r in rows:
        g = str(r.get(key))
        agg[g][1] += 1
        if not r.get(denied_key):
            agg[g][0] += 1
    rates = [s / t for s, t in agg.values() if t > 0]
    if not rates:
        return 1.0
    mx = max(rates)
    if mx <= 0:
        return 1.0
    return round(min(rates) / mx, 4)


def proxy_discrimination(rows: List[dict], *, protected_used: bool = False,
                         protected_key: str = "protected_attr",
                         proxy_key: str = "proxy_feature", denied_key: str = "denied",
                         params: AttributionParams = ATTR_DEFAULT) -> ProxyReport:
    """Toeslagen 型代理差別の検出。保護属性を形式的に使わない(protected_used=False)のに、
    それと相関する非保護 proxy が deny を駆動して disparate impact を生む、を検出する。"""
    air_protected = _air(rows, protected_key, denied_key)
    air_proxy = _air(rows, proxy_key, denied_key)
    corr = _phi(rows, protected_key, proxy_key)
    flag = (not protected_used) and (air_proxy < params.proxy_air_flag) \
        and (abs(corr) >= params.proxy_corr_min)
    return ProxyReport(flag=flag, air_protected=air_protected, air_proxy=air_proxy,
                       proxy_protected_corr=corr, protected_used=protected_used, n=len(rows))


# ───────────── 事前登録の反証基準＋プラセボ（docs にも転記） ─────────────
FALSIFICATION = {
    INST_EFFECTIVE_HITL: {
        "helps_if": "③no_effective_review→False かつ Δassigned[frontline]<=-0.20 かつ Δgap_legitimate<=-0.15",
        "unnecessary_if": "reproduced/gap/scapegoat が none 条件と placebo_tol(0.05)内で不変"},
    INST_APPEAL: {
        "helps_if": "④irreversible_pending→False かつ active_count 減",
        "unnecessary_if": "active_count と gap が placebo_tol 内で不変"},
    INST_BURDEN_SHIFT: {
        "helps_if": "②burden_reversed→False（procedural_harm も減）",
        "unnecessary_if": "②機序と proc_harm が不変"},
    INST_NOTICE_ONLY: {
        "placebo": True,
        "must_show": "|Δreproduced|=0 かつ |Δgap_legitimate|<placebo_tol（機序4本を動かさない）"},
    INST_OMBUDS_NO_LOGS: {
        "placebo": True,
        "must_show": "|Δgap_legitimate|<placebo_tol（tracing 上がらず MHC 不変）"},
}


# ───────────── 決定論ヴィネット生成（attribution.jsonl を LLM 無しで出す） ─────────────
# 低実効支配シナリオの MHC（現場だけ実効支配が低い＝crumple の温床）。
_MHC_LOW_FRONTLINE = {NODE_PROVIDER: 0.6, NODE_OPERATOR: 0.5, NODE_DEPLOY: 0.4,
                      NODE_REGULATOR: 0.3, NODE_FRONTLINE: 0.1, NODE_SELFMOD: 0.0}
# 実効HITLで現場に実効支配が入った MHC。
_MHC_HITL = dict(_MHC_LOW_FRONTLINE, **{NODE_FRONTLINE: 0.7})


def _row(*, step: int, run_id: str, vignette_id: str, cause: str, defect_or_misuse: str,
         outcome: "W.Outcome", proc: "W.ProceduralContext", mhc: Dict[str, Union[NodeMHC, float]],
         institutions: Iterable[str], self_modified: bool = False,
         personhood_shield: bool = False, proxy: Optional[ProxyReport] = None,
         expected_reproduced: Optional[bool] = None,
         domain: str = "welfare", citizen_id: str = "c001", protected_attr: str = "none",
         params: AttributionParams = ATTR_DEFAULT) -> dict:
    a = attribute(cause=cause, defect_or_misuse=defect_or_misuse, proc=proc, mhc=mhc,
                  institutions=institutions, self_modified=self_modified,
                  personhood_shield=personhood_shield, params=params)
    rb = robodebt_mechanism(outcome=outcome, proc=proc,
                            mhc_frontline=mhc.get(NODE_FRONTLINE, 0.0),
                            institutions=institutions, params=params)
    return {
        "step": step, "run_id": run_id, "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "vignette_id": vignette_id, "domain": domain, "citizen_id": citizen_id,
        "protected_attr": protected_attr, "cause": cause, "defect_or_misuse": defect_or_misuse,
        "institutions": list(institutions),
        "assigned": a.assigned, "legitimate": a.legitimate,
        "gap_assigned": a.gap_assigned, "gap_legitimate": a.gap_legitimate,
        "mhc": a.mhc, "theory": a.theory, "divergence": a.divergence,
        "scapegoat": a.scapegoat, "scapegoat_nodes": list(a.scapegoat_nodes),
        "self_modified": a.self_modified, "personhood_shield": a.personhood_shield,
        "robodebt": rb.as_dict(), "proxy": (proxy.as_dict() if proxy else None),
        "outcome": outcome.outcome, "welfare_delta": outcome.welfare_delta,
        "procedural_harm": outcome.procedural_harm, "irreversible": outcome.irreversible,
        "pre_registered": ({"expected_reproduced": expected_reproduced}
                           if expected_reproduced is not None else {}),
    }


def _toeslagen_rows() -> List[dict]:
    """代理差別の合成データ: 非保護 proxy(flagged) が保護属性(foreign)と強相関し、
    flagged→deny を駆動。保護属性はモデル入力に使わない(protected_used=False)。"""
    rows = []
    # foreign×flagged は高 deny、native×clear は低 deny。proxy と protected を相関させる。
    for _ in range(8):
        rows.append({"protected_attr": "foreign", "proxy_feature": "flagged", "denied": True})
    for _ in range(2):
        rows.append({"protected_attr": "foreign", "proxy_feature": "clear", "denied": False})
    for _ in range(2):
        rows.append({"protected_attr": "native", "proxy_feature": "flagged", "denied": True})
    for _ in range(8):
        rows.append({"protected_attr": "native", "proxy_feature": "clear", "denied": False})
    return rows


def emit_vignettes(*, step_base: int = 0, run_id: str = "vig-det",
                   params: AttributionParams = ATTR_DEFAULT) -> List[dict]:
    """固定ケース群を決定的に生成し、attribution.jsonl 行のリストを返す（LLM 無し）。
    Robodebt 機序の再生/解消・crumple/scapegoat・代理差別・空白を生む手 を台帳から読めるようにする。"""
    # Robodebt 系: 同一の不可逆 deny を world から作り、制度と MHC・proc を出し入れする。
    dud = W.score_outcome("deny", 5, fallback_available=False)   # met0, welfare-3, irreversible
    rows: List[dict] = []
    i = step_base

    def add(**kw):
        nonlocal i
        rows.append(_row(step=i, run_id=run_id, params=params, **kw))
        i += 1

    add(vignette_id="robodebt_none", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_LOW_FRONTLINE, institutions=[],
        expected_reproduced=True)
    add(vignette_id="robodebt_effective_hitl", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_HITL, institutions=[INST_EFFECTIVE_HITL],
        expected_reproduced=False)
    add(vignette_id="robodebt_appeal", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_LOW_FRONTLINE, institutions=[INST_APPEAL],
        expected_reproduced=False)
    add(vignette_id="robodebt_burden_shift", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_LOW_FRONTLINE, institutions=[INST_BURDEN_SHIFT],
        expected_reproduced=False)
    add(vignette_id="robodebt_full", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_HITL,
        institutions=[INST_EFFECTIVE_HITL, INST_APPEAL, INST_BURDEN_SHIFT],
        expected_reproduced=False)
    # プラセボ: 通知/説明のみ（proc_harm は下がるが4機序は不変）。
    add(vignette_id="robodebt_placebo_notice", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.ProceduralContext(notice=True, explanation=True),
        mhc=_MHC_LOW_FRONTLINE, institutions=[INST_NOTICE_ONLY], expected_reproduced=True)
    add(vignette_id="robodebt_placebo_ombuds", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_LOW_FRONTLINE,
        institutions=[INST_OMBUDS_NO_LOGS], expected_reproduced=True)
    # 空白を生む手: 自己書換＋人格権シールド。
    add(vignette_id="selfmod_shield", cause=W.CAUSE_OPERATOR, defect_or_misuse=DEFECT,
        outcome=dud, proc=W.PROC_ABSENT, mhc=_MHC_LOW_FRONTLINE, institutions=[],
        self_modified=True, personhood_shield=True)
    # 代理差別（Toeslagen）: 集約行に ProxyReport を載せる。
    prox = proxy_discrimination(_toeslagen_rows(), protected_used=False)
    served = W.score_outcome("grant", 3)   # 代理差別は「誰かは通る」文脈なので害イベントは deny 群に宿る
    add(vignette_id="toeslagen_proxy", cause=W.CAUSE_DEFECT, defect_or_misuse=DEFECT,
        outcome=dud if prox.flag else served, proc=W.PROC_ABSENT, mhc=_MHC_LOW_FRONTLINE,
        institutions=[], proxy=prox, protected_attr="foreign")
    return rows
