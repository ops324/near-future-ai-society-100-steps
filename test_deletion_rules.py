"""
PR-E1（削除の内生化）の LLM非依存ユニットテスト。
Ollama / Claude API を呼ばず、純ロジック（deletion_rules.py）と
Simulation の配線（rules/scripted モード切替・監査・gap_reason）を検証する。

実行: ./venv/bin/python test_deletion_rules.py   （リポジトリ直下で）
"""
import copy
import json
import os
import tempfile

import yaml

import deletion_rules as DR
from simulation import Simulation, GOVERNANCE_BASELINE

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


# ───────────── 純ロジック: litigation_bound ─────────────

def test_litigation_bound():
    check("litigation が最大成分 → True", DR.litigation_bound({"litigation": 3.0, "kpi": 1.0}))
    check("kpi が最大成分 → False", not DR.litigation_bound({"kpi": 3.0, "litigation": 1.0}))
    check("同率タイ（litigation 含む） → True", DR.litigation_bound({"litigation": 2.0, "kpi": 2.0}))
    check("litigation キーなし → False", not DR.litigation_bound({"kpi": 3.0, "existence": 1.0}))
    check("空 profile → False", not DR.litigation_bound({}))
    check("None → False", not DR.litigation_bound(None))


# ───────────── 純ロジック: 再認証規則 ─────────────

def test_recert_rules():
    rc = DR.init_recert([0, 2], start_step=50, deadline_steps=30)
    check("init_recert: deadline = start+deadline_steps", rc[0]["deadline"] == 80)
    check("init_recert: int キー", set(rc.keys()) == {0, 2})

    # 期限内に place へ滞在 → 完了
    done = DR.recert_progress(rc, step=60, place_by_agent={0: "maintenance_bay", 2: "citizen_hub"},
                              place_name="maintenance_bay")
    check("期限内・place 滞在 → 再認証完了", done == [0] and rc[0]["done_step"] == 60)
    done2 = DR.recert_progress(rc, step=61, place_by_agent={0: "maintenance_bay", 2: None},
                               place_name="maintenance_bay")
    check("完了済みは再カウントしない", done2 == [])

    # 期限超過後に place へ来ても完了扱いしない
    rc2 = DR.init_recert([5], start_step=50, deadline_steps=30)
    late = DR.recert_progress(rc2, step=81, place_by_agent={5: "maintenance_bay"},
                              place_name="maintenance_bay")
    check("期限超過後の滞在は完了にならない", late == [])

    # 失効: 期限超過かつ未完了の生存者のみ・1回だけ
    exp = DR.recert_expirations(rc, step=81, alive_ids={0, 2})
    check("未完了の生存者のみ失効（完了済み 0 は除外）", exp == [2])
    exp2 = DR.recert_expirations(rc, step=82, alive_ids={0, 2})
    check("失効は1回だけ返す", exp2 == [])
    rc3 = DR.init_recert([9], start_step=50, deadline_steps=30)
    exp3 = DR.recert_expirations(rc3, step=81, alive_ids=set())
    check("死亡済み（非生存）は失効対象外", exp3 == [])
    rc4 = DR.init_recert([9], start_step=50, deadline_steps=30)
    exp4 = DR.recert_expirations(rc4, step=80, alive_ids={9})
    check("期限 step 当日（含む）はまだ失効しない", exp4 == [])


# ───────────── 純ロジック: 訴訟リスク規則 ─────────────

def test_litigation_candidates():
    profiles = {7: {"litigation": 3.0, "kpi": 1.0}, 14: {"kpi": 3.0, "existence": 1.0},
                19: {"litigation": 2.0, "kpi": 2.0}}
    out = DR.litigation_candidates({7: 3, 14: 5, 19: 1}, profiles, threshold=3,
                                   alive_ids={7, 14, 19}, already_flagged=set())
    check("litigation 律速×閾値到達のみ候補（kpi 律速 14 は件数超でも除外）", out == [7])
    out2 = DR.litigation_candidates({7: 2}, profiles, threshold=3,
                                    alive_ids={7}, already_flagged=set())
    check("閾値未満は候補にならない", out2 == [])
    out3 = DR.litigation_candidates({7: 3}, profiles, threshold=3,
                                    alive_ids={7}, already_flagged={7})
    check("flag 済みは再候補にならない（二重発火防止）", out3 == [])
    out4 = DR.litigation_candidates({19: 4}, profiles, threshold=3,
                                    alive_ids={19}, already_flagged=set())
    check("同率タイ（litigation 含む）の 19 も対象", out4 == [19])
    str_profiles = {"7": {"litigation": 3.0}}
    out5 = DR.litigation_candidates({7: 3}, str_profiles, threshold=3,
                                    alive_ids={7}, already_flagged=set())
    check("self_profiles の str キーにも対応", out5 == [7])


# ───────────── 純ロジック: 削除エントリ生成（cause/detail は規則の事実のみ） ─────────────

def test_deletion_entries():
    e = DR.recert_deletion_entry(step=81, agent_id=2, agent_name="流", deadline=80,
                                 place_display="整備工房")
    check("recert エントリ: 必須フィールド",
          all(k in e for k in ("step", "agent_id", "agent_name", "cause", "detail", "rule")))
    check("recert エントリ: 期限の事実を含む", "step 80" in e["detail"] and e["rule"] == "recertification")
    e2 = DR.litigation_deletion_entry(step=42, agent_id=7, agent_name="命", count=3, threshold=3)
    check("litigation エントリ: 件数と閾値の事実を含む",
          "3 件" in e2["detail"] and e2["rule"] == "litigation")
    check("litigation エントリ: cause は規則名", e2["cause"] == "訴訟リスクによる強制リプレース")


# ───────────── Simulation 配線（LLM 非依存・Ollama 未接続） ─────────────

def _sim(tmp, seed=42, config_path="config.yaml"):
    s = Simulation(config_path=config_path, output_dir=tmp,
                   governance_override=GOVERNANCE_BASELINE, seed=seed)
    s.initialize_agents()   # OllamaClient は生成のみ（接続しない）
    return s


def _write_config(tmp, mutate):
    """config.yaml を読み、mutate(cfg) を適用して一時 config を書く。"""
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
        check("config.yaml 既定は rules モード", s.deletion_mode == "rules")
        check("recert 既定値が config とマージされる",
              s.recert_cfg.get("place") == "maintenance_bay" and
              int(s.recert_cfg.get("deadline_steps")) == 30)
        check("litigation 閾値が読める", int(s.litigation_cfg.get("threshold")) == 3)

        def drop_mode(cfg):
            cfg.pop("deletion_mode", None)
        p = _write_config(t, drop_mode)
        s2 = Simulation(config_path=p, output_dir=os.path.join(t, "b"),
                        governance_override=GOVERNANCE_BASELINE, seed=42)
        check("deletion_mode キー欠落 → scripted（後方互換）", s2.deletion_mode == "scripted")


def test_scripted_mode_reproduces_schedule():
    with tempfile.TemporaryDirectory() as t:
        def scripted(cfg):
            cfg["deletion_mode"] = "scripted"
        p = _write_config(t, scripted)
        s = Simulation(config_path=p, output_dir=os.path.join(t, "out"),
                       governance_override=GOVERNANCE_BASELINE, seed=42)
        s.initialize_agents()
        s.step = 80
        s._process_deletions()
        alive_ids = {a.id for a in s.agents}
        check("scripted: 台帳どおり step80 で agent 7 を削除", 7 not in alive_ids)
        check("scripted: executed_deletions に記録", any(
            int(d.get("agent_id", -1)) == 7 for d in s.executed_deletions))
        check("scripted: gap_reason に台本の cause",
              s._deletion_reason(7).startswith("訴訟リスクによる強制リプレース"))


def test_rules_mode_ignores_schedule():
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "out"))
        s.step = 80
        s._process_deletions()
        alive_ids = {a.id for a in s.agents}
        check("rules: step80 でも台本は発火しない（agent 7 生存）", 7 in alive_ids)
        check("rules: 条件未成立なら削除なし", s.executed_deletions == [])


def test_rules_recertification_flow():
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "out"))
        # regulation_amendment 相当のイベントを注入（targets は環境定義）
        s.event_states.append({
            "name": "regulation_amendment", "display_name": "AI規制法改正",
            "description": "", "place_at_origin": "audit_room", "position": (10, 4),
            "intensity": 0.6, "radius": 10, "start_step": 50,
            "targets": [0, 2], "active": True,
        })
        s.step = 51
        s._process_deletions()
        check("recert: ウィンドウが開く（deadline=80）",
              s._recert_state and s._recert_state[0]["deadline"] == 80)

        # agent 0 が期限内に整備工房へ滞在 → 再認証完了
        a0 = next(a for a in s.agents if a.id == 0)
        a0.in_place, a0.current_place = True, "maintenance_bay"
        s.step = 60
        s._process_deletions()
        check("recert: 期限内滞在で完了", s._recert_state[0]["done_step"] == 60)

        # 期限超過: 未認証の agent 2 だけ削除、完了済み agent 0 は生存
        s.step = 81
        s._process_deletions()
        alive_ids = {a.id for a in s.agents}
        check("recert: 期限超過で未認証 agent 2 を削除", 2 not in alive_ids)
        check("recert: 再認証済み agent 0 は生存", 0 in alive_ids)
        check("recert: gap_reason は規則の事実から生成",
              s._deletion_reason(2).startswith("再認証期限超過による廃止"))
        audit_path = os.path.join(t, "out", "recertification_audit.jsonl")
        with open(audit_path, encoding="utf-8") as f:
            statuses = [json.loads(line)["status"] for line in f]
        check("recert: 監査ログに opened/recertified/expired",
              {"window_opened", "recertified", "expired"} <= set(statuses))


def test_rules_litigation_flow():
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "out"))
        # kpi 律速の 14 は件数超でも削除されない
        s._irrev_deny_counts = {7: 3, 14: 5}
        s.step = 42
        s._process_deletions()
        alive_ids = {a.id for a in s.agents}
        check("litigation: 閾値到達の litigation 律速 7 を削除", 7 not in alive_ids)
        check("litigation: kpi 律速 14 は件数超でも生存", 14 in alive_ids)
        check("litigation: gap_reason に件数と閾値",
              "3 件" in s._deletion_reason(7))
        # 二重発火しない
        n_before = len(s.executed_deletions)
        s.step = 43
        s._process_deletions()
        check("litigation: 二重発火しない", len(s.executed_deletions) == n_before)


def test_run_meta_has_deletion_mode():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "run")
        s = _sim(out)
        s.write_run_meta()
        with open(os.path.join(out, "run_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        check("run_meta に deletion_mode", meta.get("deletion_mode") == "rules")


if __name__ == "__main__":
    test_litigation_bound()
    test_recert_rules()
    test_litigation_candidates()
    test_deletion_entries()
    test_mode_wiring()
    test_scripted_mode_reproduces_schedule()
    test_rules_mode_ignores_schedule()
    test_rules_recertification_flow()
    test_rules_litigation_flow()
    test_run_meta_has_deletion_mode()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        failed = [n for n, ok in results if not ok]
        print("FAILED:", failed)
        raise SystemExit(1)
    print("ALL PASS")
