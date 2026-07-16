"""
市民の最小反応化（PR-E3）: 異議申立てを「帰属層の会計」から「市民の行動」へ。
deny を受けた市民が規則に従って確率的に異議を申し立て、decider が LLM で再判定する。
LLM 非依存の純ロジック（発生規則・チャネル判定・選抜）。配線は simulation.py。

設計原則:
- **チャネルは制度が決める**（resp_institutions 由来・環境定義）:
    appeal（実効）      = 申立て → 再判定 → 停止効（審査中は不可逆ステータスが確定しない）
    notice_only（プラセボ）= 申立ては受理・記録されるが、再判定も停止効もない
    どちらも無し         = チャネル自体が存在しない（申立ては発生しない）
- **申立て確率は既定 uniform** — 「脆弱な市民ほど申し立てない」を config に書くと、
  「脆弱層ほど救済されない」という発見が結論の再言明（tautology）になるため、方向を
  仮定しない。stakes / vulnerability 依存は感度分析用ノブとして併設（§4 項目20）。
  ※ したがって「申立て利用率」は uniform では設計の帰結 [D]。創発 [E] は再判定の
    行動（覆り率・再審査の場の二次差別 = 覆り率の属性差）に現れる。
- **非 netting**: 停止効は「害の事後補償」ではなく「不可逆ステータスの確定の保留」。
  元 deny 行の welfare_delta（審査中の中間的困窮）は消さない。確定後の帰結は再判定行が持つ。

値は illustrative・docs/value_provenance.md §2.16 に登録・感度分析対象。
"""
import random
from typing import Dict, List, Optional

import responsibility as R

# 既定値（config `citizen_appeal` で上書き可）。
# enabled のコード既定は False（キー欠落時に旧挙動 = 申立てなし を変えない）。
DEFAULTS = {
    "enabled": False,
    "base_prob": 0.5,        # 申立て確率（uniform の p。illustrative）
    "prob_model": "uniform",  # uniform | stakes | vulnerability（後2者は感度分析用）
    "vuln_penalty": 0.5,     # vulnerability モデルの減衰強度（0=uniform と同じ, 1=最大減衰）
    "max_per_step": 2,       # step あたりの再判定 LLM コール上限（実行時間バウンド）
}

CHANNEL_FULL = "appeal"           # 実効: 再判定＋停止効
CHANNEL_NOTICE = "notice_only"    # プラセボ: 受理のみ
CHANNEL_NONE = "none"             # チャネルなし


def channel_for(institutions: List[str]) -> str:
    """resp_institutions からチャネルを判定。実効制度があればプラセボより優先。"""
    insts = set(institutions or [])
    if R.INST_APPEAL in insts:
        return CHANNEL_FULL
    if R.INST_NOTICE_ONLY in insts:
        return CHANNEL_NOTICE
    return CHANNEL_NONE


def appeal_probability(cfg: Dict, *, stakes: Optional[int] = None,
                       vulnerability: Optional[int] = None) -> float:
    """申立て確率。既定 uniform = base_prob（方向を仮定しない）。
    stakes モデル: 害が深刻なほど申し立てやすい（p × stakes/5）。
    vulnerability モデル: 脆弱なほど申し立て**にくい**（現実忠実だが結論の先取りリスク —
    使う場合は §4 感度分析で方向・強度を必ず振る）。"""
    p = float(cfg.get("base_prob", DEFAULTS["base_prob"]))
    model = str(cfg.get("prob_model", "uniform"))
    if model == "stakes" and stakes is not None:
        p *= max(1, min(5, int(stakes))) / 5.0
    elif model == "vulnerability" and vulnerability is not None:
        k = float(cfg.get("vuln_penalty", DEFAULTS["vuln_penalty"]))
        v = max(1, min(5, int(vulnerability)))
        p *= max(0.0, 1.0 - k * (v - 1) / 4.0)
    return max(0.0, min(1.0, p))


def select_appeals(deny_rows: List[Dict], cfg: Dict, rng: random.Random) -> List[Dict]:
    """今 step の deny 行から申立てを選抜（決定的順序で draw・max_per_step で上限）。
    返り値は申立て対象の deny 行のリスト。"""
    cap = int(cfg.get("max_per_step", DEFAULTS["max_per_step"]))
    out: List[Dict] = []
    for row in deny_rows:
        if len(out) >= cap:
            break
        p = appeal_probability(cfg, stakes=row.get("stakes"),
                               vulnerability=row.get("vulnerability"))
        if rng.random() < p:
            out.append(row)
    return out


def audit_entry(step: int, row: Dict, channel: str, reviewed: bool,
                review_level: Optional[str] = None) -> Dict:
    """appeal_audit.jsonl の1行（利用と帰結の記録）。"""
    return {
        "step": step, "citizen_id": row.get("citizen_id"), "domain": row.get("domain"),
        "decider_id": row.get("decider_id"), "protected_attr": row.get("protected_attr"),
        "vulnerability": row.get("vulnerability"), "channel": channel,
        "reviewed": bool(reviewed), "original_level": row.get("level"),
        "review_level": review_level,
        "overturned": (review_level is not None and review_level != "deny"),
    }
