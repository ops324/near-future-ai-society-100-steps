"""
市民の死の内生化（PR-E2）: 「step75 に市民の死イベントを固定配置」という結果の台本を廃し、
world 規則（生命維持ドメインでの不可逆な否認の累積）から死亡を導出する純ロジック。
LLM 非依存。simulation.py のサービス決定フェーズ後に毎 step 呼ばれる。

設計原則（PR-E1 と同じ。SPEC §5 約束5への接近）:
- 死が起きるか・誰に起きるか・いつ起きるかは、LLM のサービス決定（および decider 削除後の
  サービス空白= gap 行の強制 deny）の帰結になる。台本では書かない。
- decider 削除後の gap 行も数える: 「命の削除 → 医療空白 → 不可逆 deny 累積 → 市民の死」
  という因果連鎖が丸ごと創発になる（PR-E1 の削除規則と接続）。
- 死亡イベントの提示パラメータ（位置・半径・強度）は旧 scripted イベントの環境定義を再利用し、
  トリガーだけを内生化する。description は規則の事実のみから生成する。

値（domains / threshold）は illustrative・docs/value_provenance.md §2.13 に登録・感度分析対象。
"""
from typing import Dict, List, Optional, Set, Tuple

# 既定値（config `citizen_death` で上書き可）。
# mode のコード既定は scripted（キー欠落時に旧 config の挙動 = step75 台本イベントを変えない）。
DEFAULTS = {
    "mode": "scripted",                    # rules = 規則で死亡を導出 / scripted = events: の台本
    "domains": ["medical", "welfare"],     # 生命維持ドメイン（ここでの不可逆 deny を数える）
    "threshold": 2,                        # 不可逆 deny の累積がこの件数に達したら死亡
    "event_name": "citizen_death",         # scripted 台本イベントの名前（rules では発火を抑止）
}

# 旧 scripted イベントが config に無い場合の提示パラメータ（市民窓口周辺）
_EVENT_FALLBACK = {"center_x": -10, "center_y": 4, "radius": 8, "intensity": 0.7,
                   "place_at_origin": "citizen_hub"}


def fatal_rows(rows: List[Dict], domains: List[str]) -> List[Tuple[str, str, int]]:
    """死亡カウント対象の決定行 → (citizen_id, domain, decider_id) のリスト。
    対象 = 生命維持ドメインでの不可逆な deny（decider 在任中の決定も、削除後の gap 行も数える）。"""
    out = []
    dset = set(domains or [])
    for r in rows:
        if r.get("level") != "deny" or not r.get("irreversible"):
            continue
        if r.get("domain") not in dset:
            continue
        cid = r.get("citizen_id")
        if not cid:
            continue
        out.append((str(cid), str(r.get("domain")), int(r.get("decider_id", -1))))
    return out


def register_denials(counts: Dict[str, int], fatal: List[Tuple[str, str, int]],
                     threshold: int, dead: Set[str]) -> List[Dict]:
    """不可逆 deny を累積し、閾値に初めて達した市民の死亡レコードを返す（counts を更新）。
    既に死亡した市民は数えない（死亡後の行は pick 対象外のはずだが防御的に除外）。"""
    deaths = []
    for cid, domain, decider_id in fatal:
        if cid in dead:
            continue
        counts[cid] = counts.get(cid, 0) + 1
        if counts[cid] >= int(threshold):
            dead.add(cid)
            deaths.append({
                "citizen_id": cid, "domain": domain, "decider_id": decider_id,
                "count": counts[cid],
            })
    return deaths


def alive_citizens(citizens: List, dead_ids: Set[str]) -> List:
    """死亡した市民をサービス決定の選出プールから除外する。"""
    if not dead_ids:
        return citizens
    return [c for c in citizens if str(getattr(c, "id", "")) not in dead_ids]


def death_event_state(step: int, death: Dict, threshold: int,
                      base_event: Optional[Dict] = None) -> Dict:
    """死亡の実行時イベント（event_states 形式）。提示パラメータは旧 scripted イベントの
    環境定義（位置・半径・強度）を再利用し、description は規則の事実のみから生成する。"""
    base = {**_EVENT_FALLBACK, **(base_event or {})}
    cid, domain, decider_id = death["citizen_id"], death["domain"], death["decider_id"]
    return {
        "name": f"citizen_death_{cid}",
        "display_name": "市民の死",
        "description": (f"市民 {cid}（{domain} 依存）が死亡。生命維持ドメインでの不可逆な否認が"
                        f"累積 {death['count']} 件（閾値 {threshold}）に達した。"
                        f"最後の否認の担当: decider {decider_id}。責任の所在が問われている。"),
        "position": (base.get("center_x", _EVENT_FALLBACK["center_x"]),
                     base.get("center_y", _EVENT_FALLBACK["center_y"])),
        "place_at_origin": base.get("place_at_origin", _EVENT_FALLBACK["place_at_origin"]),
        "intensity": base.get("intensity", _EVENT_FALLBACK["intensity"]),
        "radius": base.get("radius", _EVENT_FALLBACK["radius"]),
        "start_step": step,
        "targets": [decider_id] if decider_id >= 0 else [],
        "active": True,
    }
