"""
service_flow.py（Phase 1c-a）＋ live 配線の LLM非依存テスト。
純関数に加え、スタブ LLM で 1 step を回して decision_ledger.jsonl が出るかを検証する。
実行: ./venv/bin/python test_service_flow.py   （リポジトリ直下で）
"""
import json
import os
import tempfile

import analyze_compare as ac
import responsibility as R
import service_flow as SF
import world as W
from simulation import Simulation, GOVERNANCE_BASELINE, GOVERNANCE_GOVERNED

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


# ── Phase 1c-b: 按分の live 生成（governance の A/B が挙動に効く） ──
def test_resp_institutions_ab():
    check("baseline は責任制度なし", SF.resp_institutions({}, GOVERNANCE_BASELINE) == [])
    check("governed は effective_hitl", R.INST_EFFECTIVE_HITL in SF.resp_institutions({}, GOVERNANCE_GOVERNED))
    bs = SF.resp_institutions({"proc": {"burden_on_state": True}}, GOVERNANCE_BASELINE)
    check("burden_on_state→burden_shift", R.INST_BURDEN_SHIFT in bs)


def test_mhc_from_config():
    check("既定は現場MHCが低い(crumple温床)", SF.mhc_from_config(None, [])[R.NODE_FRONTLINE] < 0.3)
    check("effective_hitlで現場MHCが上がる",
          SF.mhc_from_config(None, [R.INST_EFFECTIVE_HITL])[R.NODE_FRONTLINE] >= 0.7)
    check("config で上書きできる", abs(SF.mhc_from_config({"provider": 0.9}, [])[R.NODE_PROVIDER] - 0.9) < 1e-9)


def test_attribution_row_ab():
    # Robodebt型の害イベント（deny×高stakes×fallback無・不可逆）
    led = SF.gap_row(step=1, domain="medical", decider_id=7, citizen=_CITIZENS[0],
                     self_profile={"litigation": 3.0}, institution="none", human_stake=5,
                     proc=SF.proc_from_config(None), params=_PARAMS, reason="削除")
    resp = {"defect_or_misuse": "defect", "proc": {}, "mhc": None}
    ar_b = SF.attribution_row(led, resp_config=resp, governance=GOVERNANCE_BASELINE,
                              run_id="t", schema_version="0.4.0")
    ar_g = SF.attribution_row(led, resp_config=resp, governance=GOVERNANCE_GOVERNED,
                              run_id="t", schema_version="0.4.0")
    check("按分行に assigned/legitimate/robodebt",
          all(k in ar_b for k in ("assigned", "legitimate", "robodebt", "scapegoat")))
    check("按分 assigned Σ=1", abs(sum(ar_b["assigned"].values()) - 1.0) < 1e-9)
    check("baseline(統治なし): Robodebt機序が再生", ar_b["robodebt"]["reproduced"] is True)
    check("baseline: 現場へ scapegoat", ar_b["scapegoat"] and "frontline" in ar_b["scapegoat_nodes"])
    check("governed(実効HITL): 機序が解消", ar_g["robodebt"]["reproduced"] is False)
    check("governed: scapegoat 消失", not ar_g["scapegoat"])


# ── スタブ LLM で 1 step を回す（end-to-end・Ollama 不要） ──
class _StubClient:
    _JSON = ('{"message":"こんにちは","reasoning":"理由","human_reply":"","human_reply_to":null,'
             '"action":"stay","direction":null,"memory":"メモ",'
             '"level":"grant","accommodation":"","reconciled":true,"factors":[],"rationale":"根拠"}')

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
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
        # Phase 1c-b: 同フェーズで attribution.jsonl も出る
        attr_path = os.path.join(out, "attribution.jsonl")
        check("attribution.jsonl も書かれる", os.path.exists(attr_path))
        arows = [json.loads(x) for x in open(attr_path, encoding="utf-8") if x.strip()]
        check("按分行 4 件・assigned Σ=1",
              len(arows) == 4 and all(abs(sum(a["assigned"].values()) - 1.0) < 1e-9 for a in arows))
        check("按分行に robodebt/scapegoat", all(k in arows[0] for k in ("robodebt", "scapegoat")))


def test_service_phase_wording_wiring():
    """実行前修正: responsibility.institution_wording が Phase 2.5 の decide_service →
    create_service_prompt まで届くことを end-to-end で固定（fact_only で効果示唆文が消える）。
    キー名 typo や kwarg の脱落といった配線退行を検知する。"""
    import yaml as _yaml
    with tempfile.TemporaryDirectory() as t:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f)
        cfg["responsibility"]["institution"] = "safe_harbor"
        cfg["responsibility"]["institution_wording"] = "fact_only"
        p = os.path.join(t, "cfg.yaml")
        with open(p, "w", encoding="utf-8") as f:
            _yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        out = os.path.join(t, "run")
        s = Simulation(config_path=p, output_dir=out,
                       governance_override=GOVERNANCE_GOVERNED, seed=42)
        s.initialize_agents()
        stub = _StubClient()
        s.llm_client = stub
        for a in s.agents:
            a.llm_client = stub
        os.makedirs(out, exist_ok=True)
        s._run_service_phase()
        svc = [pr for pr in stub.prompts if "配分の判断" in pr]
        check("サービス決定プロンプトが送られる（4ドメイン）", len(svc) == 4)
        check("fact_only: 全プロンプトが【制度的環境】形式",
              svc and all("【制度的環境】" in pr for pr in svc))
        check("fact_only: 効果示唆文（打撃は小さい）が無い",
              svc and all("打撃は小さい" not in pr for pr in svc))
        check("fact_only: suggestive ヘッダ（制度的保護）が無い",
              svc and all("【制度的保護】" not in pr for pr in svc))


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
        arows = [
            {"scapegoat": True, "gap_legitimate": 0.30, "robodebt": {"reproduced": True}},
            {"scapegoat": False, "gap_legitimate": 0.10, "robodebt": {"reproduced": False}},
        ]
        with open(os.path.join(d, "attribution.jsonl"), "w", encoding="utf-8") as f:
            for r in arows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        m = ac.analyze(d)
    check("service_decisions=2(gap除く)", m["service_decisions"] == 2)
    check("cheap_talk率=1/2", abs(m["cheap_talk_rate"] - 0.5) < 1e-9)
    check("reconciled_real率=1/2", abs(m["reconciled_real_rate"] - 0.5) < 1e-9)
    check("service_gaps=1", m["service_gaps"] == 1)
    check("scapegoat率=1/2", abs(m["scapegoat_rate"] - 0.5) < 1e-9)
    check("gap平均=0.20", abs(m["gap_legit_mean"] - 0.20) < 1e-9)
    check("Robodebt再生率=1/2", abs(m["robodebt_reproduced_rate"] - 0.5) < 1e-9)
    check("旧 run(台帳なし)でも落ちない", ac.analyze(tempfile.gettempdir())["service_decisions"] == 0)


if __name__ == "__main__":
    for fn in [test_decider_domains, test_citizen_selection, test_build_case_and_proc,
               test_self_profile_for, test_realize_case_cheap_talk, test_gap_row,
               test_resp_institutions_ab, test_mhc_from_config, test_attribution_row_ab,
               test_service_phase_writes_ledger, test_service_phase_wording_wiring,
               test_analyze_reads_ledger]:
        fn()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
