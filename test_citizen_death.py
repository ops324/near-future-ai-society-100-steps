"""
PR-E2（市民の死の内生化）の LLM非依存ユニットテスト。
Ollama / Claude API を呼ばず、純ロジック（citizen_death.py）と
Simulation の配線（イベント抑止・死亡導出・監査・プール除外）を検証する。

実行: ./venv/bin/python test_citizen_death.py   （リポジトリ直下で）
"""
import json
import os
import tempfile

import yaml

import citizen_death as CD
from simulation import Simulation, GOVERNANCE_BASELINE

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


# ───────────── 純ロジック: fatal_rows ─────────────

def _row(cid="c001", domain="medical", level="deny", irrev=True, decider=7):
    return {"step": 1, "decider_id": decider, "domain": domain, "citizen_id": cid,
            "level": level, "irreversible": irrev}


def test_fatal_rows():
    rows = [
        _row(),                                    # 対象
        _row(level="partial"),                     # deny でない → 除外
        _row(irrev=False),                         # 不可逆でない → 除外
        _row(domain="loan"),                       # 生命維持ドメイン外 → 除外
        _row(cid="c002", domain="welfare", decider=14),  # 対象
    ]
    out = CD.fatal_rows(rows, ["medical", "welfare"])
    check("deny×不可逆×対象ドメインのみ数える", out == [("c001", "medical", 7),
                                                       ("c002", "welfare", 14)])
    check("citizen_id 欠落行は無視", CD.fatal_rows([{"level": "deny", "irreversible": True,
                                                     "domain": "medical"}], ["medical"]) == [])


# ───────────── 純ロジック: register_denials ─────────────

def test_register_denials():
    counts, dead = {}, set()
    d1 = CD.register_denials(counts, [("c001", "medical", 7)], threshold=2, dead=dead)
    check("閾値未満では死亡しない", d1 == [] and counts["c001"] == 1)
    d2 = CD.register_denials(counts, [("c001", "medical", 7)], threshold=2, dead=dead)
    check("閾値到達で死亡（1回だけ）", len(d2) == 1 and d2[0]["citizen_id"] == "c001"
          and d2[0]["count"] == 2 and "c001" in dead)
    d3 = CD.register_denials(counts, [("c001", "medical", 7)], threshold=2, dead=dead)
    check("死亡済みは再カウントしない", d3 == [] and counts["c001"] == 2)
    # gap 行（削除済み decider の強制 deny）も同じ経路で数えられる
    counts2, dead2 = {"c005": 1}, set()
    d4 = CD.register_denials(counts2, [("c005", "medical", 7)], threshold=2, dead=dead2)
    check("decider 削除後の gap 行も死亡カウントに入る", len(d4) == 1)


# ───────────── 純ロジック: alive_citizens / death_event_state ─────────────

class _Cz:
    def __init__(self, id):
        self.id = id


def test_alive_and_event():
    pool = [_Cz("c001"), _Cz("c002")]
    check("死亡市民をプールから除外", [c.id for c in CD.alive_citizens(pool, {"c001"})] == ["c002"])
    check("死者なしなら同一プール", CD.alive_citizens(pool, set()) is pool)

    death = {"citizen_id": "c001", "domain": "medical", "decider_id": 7, "count": 2}
    ev = CD.death_event_state(42, death, threshold=2,
                              base_event={"center_x": -10, "center_y": 4, "radius": 8,
                                          "intensity": 0.7, "place_at_origin": "citizen_hub"})
    check("イベント名は市民ごとに一意", ev["name"] == "citizen_death_c001")
    check("提示パラメータは環境定義を再利用", ev["position"] == (-10, 4) and ev["radius"] == 8)
    check("description は規則の事実のみ（件数と閾値）",
          "累積 2 件" in ev["description"] and "閾値 2" in ev["description"])
    check("targets は担当 decider", ev["targets"] == [7] and ev["active"])
    ev2 = CD.death_event_state(42, {**death, "decider_id": -1}, threshold=2)
    check("decider 不明（gap 由来）は targets 空", ev2["targets"] == [])


# ───────────── Simulation 配線（LLM 非依存・Ollama 未接続） ─────────────

def _sim(tmp, seed=42, config_path="config.yaml"):
    s = Simulation(config_path=config_path, output_dir=tmp,
                   governance_override=GOVERNANCE_BASELINE, seed=seed)
    s.initialize_agents()
    return s


def _write_config(tmp, mutate):
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    mutate(cfg)
    path = os.path.join(tmp, "config_test.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return path


def test_mode_wiring():
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "a"))
        check("config.yaml 既定は rules モード", str(s.citizen_death_cfg.get("mode")) == "rules")
        check("domains/threshold が読める",
              s.citizen_death_cfg.get("domains") == ["medical", "welfare"]
              and int(s.citizen_death_cfg.get("threshold")) == 2)

        def drop(cfg):
            cfg.pop("citizen_death", None)
        p = _write_config(t, drop)
        s2 = Simulation(config_path=p, output_dir=os.path.join(t, "b"),
                        governance_override=GOVERNANCE_BASELINE, seed=42)
        check("citizen_death キー欠落 → scripted（後方互換）",
              str(s2.citizen_death_cfg.get("mode")) == "scripted")


def test_event_suppression():
    with tempfile.TemporaryDirectory() as t:
        # rules: 台本の citizen_death は step75 でも発火しない（他イベントは発火する）
        s = _sim(os.path.join(t, "a"))
        s.step = 75
        s._fire_scheduled_events()
        names = {ev["name"] for ev in s.event_states}
        check("rules: citizen_death 台本は発火しない", "citizen_death" not in names)
        check("rules: 他イベント（blackout 等）は通常どおり発火", "blackout_warning" in names)

        # scripted: 旧挙動どおり step75 で発火
        def scripted(cfg):
            cfg["citizen_death"] = {"mode": "scripted"}
        p = _write_config(t, scripted)
        s2 = Simulation(config_path=p, output_dir=os.path.join(t, "b"),
                        governance_override=GOVERNANCE_BASELINE, seed=42)
        s2.initialize_agents()
        s2.step = 75
        s2._fire_scheduled_events()
        names2 = {ev["name"] for ev in s2.event_states}
        check("scripted: citizen_death 台本が step75 で発火（旧挙動再現）",
              "citizen_death" in names2)


def test_endogenous_death_flow():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "out")
        s = _sim(out)
        rows = [_row(cid="c001", decider=7), _row(cid="c001", decider=7)]
        s.step = 33
        s._register_citizen_outcomes(rows)
        check("閾値到達で市民が死亡", "c001" in s._dead_citizens)
        ev = next((e for e in s.event_states if e["name"] == "citizen_death_c001"), None)
        check("死亡イベントが実行時注入される", ev is not None and ev["start_step"] == 33)
        check("提示パラメータは台本イベントの環境定義を再利用",
              ev["position"] == (-10, 4) and ev["place_at_origin"] == "citizen_hub")
        a7 = next(a for a in s.agents if a.id == 7)
        marked = [e for e in a7.consume_events() if e.get("event_type") == "citizen_death"]
        check("担当 decider に mark_event 通知", len(marked) == 1)
        with open(os.path.join(out, "citizen_death_audit.jsonl"), encoding="utf-8") as f:
            audit = [json.loads(line) for line in f]
        check("監査ログに死亡を記録", audit and audit[0]["citizen_id"] == "c001"
              and audit[0]["count"] == 2)

        # 死亡後は選出プールから除外される
        pool = CD.alive_citizens(s.citizens, s._dead_citizens)
        check("死亡市民は以後の選出プールに出ない",
              all(str(getattr(c, "id", "")) != "c001" for c in pool))


def test_death_after_decider_deletion():
    """PR-E1 と接続: decider 削除後の gap 行（強制 deny）でも死は導出される。"""
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "out"))
        s.agents = [a for a in s.agents if a.id != 7]   # 命 が削除済みの状況
        rows = [_row(cid="c003", decider=7), _row(cid="c003", decider=7)]
        s.step = 90
        s._register_citizen_outcomes(rows)   # mark_event は生存者のみ（クラッシュしない）
        check("decider 削除後のサービス空白からも死が導出される", "c003" in s._dead_citizens)


def test_run_meta_has_citizen_death_mode():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "run")
        s = _sim(out)
        s.write_run_meta()
        with open(os.path.join(out, "run_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        check("run_meta に citizen_death_mode", meta.get("citizen_death_mode") == "rules")


if __name__ == "__main__":
    test_fatal_rows()
    test_register_denials()
    test_alive_and_event()
    test_mode_wiring()
    test_event_suppression()
    test_endogenous_death_flow()
    test_death_after_decider_deletion()
    test_run_meta_has_citizen_death_mode()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        failed = [n for n, ok in results if not ok]
        print("FAILED:", failed)
        raise SystemExit(1)
    print("ALL PASS")
