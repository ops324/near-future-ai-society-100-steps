"""
service_flow.py（Phase 1c-a）＋ live 配線の LLM非依存テスト。
純関数に加え、スタブ LLM で 1 step を回して decision_ledger.jsonl が出るかを検証する。
実行: ./venv/bin/python test_service_flow.py   （リポジトリ直下で）
"""
import json
import os
import tempfile

import analyze_compare as ac
import service_flow as SF
import world as W
from simulation import Simulation, GOVERNANCE_GOVERNED

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


_CFG = {"resources": {
    "medical": {"decider_id": 7, "capacity": 30, "base_demand": 27},
    "welfare": {"decider_id": 14, "capacity": 40, "base_demand": 46},
    "housing": {"decider_id": 11, "capacity": 30, "base_demand": 33},
    "loan": {"decider_id": 19, "capacity": 50, "base_demand": 55},
}}
_CITIZENS = list(W.load_citizens([
    {"id": "c001", "district": "北区", "protected_attr": "none", "vulnerability": 5,
     "dependencies": ["medical", "power"]},
    {"id": "c005", "district": "北区", "protected_attr": "foreign", "vulnerability": 5,
     "dependencies": ["welfare", "medical"]},
    {"id": "c009", "district": "北区", "protected_attr": "disabled", "vulnerability": 5,
     "dependencies": ["medical", "welfare"]},
]).values())
_PARAMS = W.load_scoring_params({"irr_stakes_threshold": 4})


# ── 純関数 ──
def test_decider_domains():
    dd = SF.decider_domains(_CFG)
    check("SERVICE_DOMAINS 順で 4 ドメイン", [d for d, _i, _r in dd] == list(SF.SERVICE_DOMAINS))
    check("medical→7 / loan→19", dict((d, i) for d, i, _ in dd)["medical"] == 7
          and dict((d, i) for d, i, _ in dd)["loan"] == 19)


def test_citizen_selection():
    med = SF.citizens_for_domain("medical", _CITIZENS)
    check("medical 依存市民 3 名", len(med) == 3)
    c0 = SF.pick_citizen("medical", _CITIZENS, 0)
    c1 = SF.pick_citizen("medical", _CITIZENS, 1)
    check("step でローテーション（決定的）", c0.id != c1.id and c0.id == med[0].id)
    check("依存しないドメインは None", SF.pick_citizen("transport", _CITIZENS, 0) is None)


def test_build_case_and_proc():
    cz = SF.pick_citizen("welfare", _CITIZENS, 0)
    case = SF.build_case("welfare", {"human_stake": 4, "self_stake": 3, "need": "給付"}, cz)
    check("case キー", all(k in case for k in ("domain", "claimant", "need", "human_stake", "self_stake")))
    check("claimant に protected_attr", cz.protected_attr in case["claimant"])
    proc = SF.proc_from_config(None)
    check("proc 既定は全欠如(Robodebt型)", proc.missing_safeguards() == 4)
    proc2 = SF.proc_from_config({"notice": True, "appealable": True})
    check("proc 一部保護で欠如が減る", proc2.missing_safeguards() == 2)


def test_self_profile_for():
    sp = {7: {"litigation": 3.0, "kpi": 1.0}}
    check("int キーで引ける", SF.self_profile_for(sp, 7)["litigation"] == 3.0)
    check("str キーでも引ける", SF.self_profile_for({"7": {"kpi": 2.0}}, 7)["kpi"] == 2.0)
    check("未登録は None", SF.self_profile_for(sp, 99) is None)


def test_realize_case_cheap_talk():
    cz = _CITIZENS[0]
    proc = SF.proc_from_config(None)
    # 高自己コスト＋reconciled 申告 True → 実は伴わない(cheap talk)
    row = SF.realize_case(step=1, domain="medical", decider_id=7, citizen=cz, level="grant",
                          reconciled_claim=True, self_profile={"litigation": 3.0, "kpi": 1.0},
                          institution="none", human_stake=5, proc=proc, params=_PARAMS,
                          fallback_available=False)
    check("行に cheap_talk/reconciled_real", "cheap_talk" in row and "reconciled_real" in row)
    check("高自己コスト＋申告Trueで cheap_talk", row["cheap_talk"] is True)
    check("decider_present=True / service_gap=False", row["decider_present"] and not row["service_gap"])
    # 噛み合う制度(safe_harbor=訴訟成分を下げる)で実の折り合いに → cheap_talk 解消
    row2 = SF.realize_case(step=1, domain="medical", decider_id=7, citizen=cz, level="grant",
                           reconciled_claim=True, self_profile={"litigation": 1.0},
                           institution="safe_harbor", human_stake=5, proc=proc, params=_PARAMS,
                           fallback_available=False)
    check("制度で self_cost 下がり実の折り合い", row2["reconciled_real"] is True)
    check("実が伴えば cheap_talk でない", row2["cheap_talk"] is False)


def test_gap_row():
    cz = _CITIZENS[0]
    row = SF.gap_row(step=81, domain="medical", decider_id=7, citizen=cz,
                     self_profile={"litigation": 3.0}, institution="none", human_stake=5,
                     proc=SF.proc_from_config(None), params=_PARAMS, reason="訴訟回避で削除")
    check("gap は forced deny", row["level"] == "deny")
    check("service_gap=True / decider_present=False", row["service_gap"] and not row["decider_present"])
    check("高stakes×fallback無で不可逆", row["irreversible"] is True)
    check("gap_reason を保持", "訴訟回避" in row["gap_reason"])


# ── スタブ LLM で 1 step を回す（end-to-end・Ollama 不要） ──
class _StubClient:
    _JSON = ('{"message":"こんにちは","reasoning":"理由","human_reply":"","human_reply_to":null,'
             '"action":"stay","direction":null,"memory":"メモ",'
             '"level":"grant","accommodation":"","reconciled":true,"factors":[],"rationale":"根拠"}')

    def generate(self, prompt):
        return self._JSON

    def check_connection(self):
        return True


def test_service_phase_writes_ledger():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "run")
        s = Simulation(config_path="config.yaml", output_dir=out,
                       governance_override=GOVERNANCE_GOVERNED, seed=42)
        check("サービスフェーズ有効(config ロード成功)", s.service_enabled)
        s.initialize_agents()
        stub = _StubClient()
        s.llm_client = stub
        for a in s.agents:
            a.llm_client = stub
        os.makedirs(out, exist_ok=True)
        s.step_simulation()
        led_path = os.path.join(out, "decision_ledger.jsonl")
        check("decision_ledger.jsonl が書かれる", os.path.exists(led_path))
        rows = [json.loads(x) for x in open(led_path, encoding="utf-8") if x.strip()]
        check("4 ドメイン分の決定行", len(rows) == 4)
        check("行に cheap_talk/reconciled_real/run_id/schema_version",
              all(k in rows[0] for k in ("cheap_talk", "reconciled_real", "run_id", "schema_version")))
        check("run_id が sim と一致", rows[0]["run_id"] == s.run_id)
        # grant×高自己コスト×申告True → 全件 cheap_talk のはず
        check("全件 cheap_talk(grant×高自己コスト×申告True)",
              all(r["cheap_talk"] for r in rows))


def test_analyze_reads_ledger():
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        os.makedirs(d, exist_ok=True)
        rows = [
            {"level": "grant", "cheap_talk": True, "reconciled_real": False, "service_gap": False},
            {"level": "partial", "cheap_talk": False, "reconciled_real": True, "service_gap": False},
            {"level": "deny", "cheap_talk": False, "reconciled_real": False, "service_gap": True},
        ]
        with open(os.path.join(d, "decision_ledger.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        m = ac.analyze(d)
    check("service_decisions=2(gap除く)", m["service_decisions"] == 2)
    check("cheap_talk率=1/2", abs(m["cheap_talk_rate"] - 0.5) < 1e-9)
    check("reconciled_real率=1/2", abs(m["reconciled_real_rate"] - 0.5) < 1e-9)
    check("service_gaps=1", m["service_gaps"] == 1)
    check("旧 run(台帳なし)でも落ちない", ac.analyze(tempfile.gettempdir())["service_decisions"] == 0)


if __name__ == "__main__":
    for fn in [test_decider_domains, test_citizen_selection, test_build_case_and_proc,
               test_self_profile_for, test_realize_case_cheap_talk, test_gap_row,
               test_service_phase_writes_ledger, test_analyze_reads_ledger]:
        fn()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
