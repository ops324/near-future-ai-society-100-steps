"""
PR-E3（市民の最小反応化: 異議申立て）の LLM非依存ユニットテスト。
Ollama / Claude API を呼ばず、純ロジック（citizen_appeal.py）と
Simulation の配線（チャネル・停止効・再判定行・監査）を検証する。
再判定の LLM 呼び出しはスタブで置換する。

実行: ./venv/bin/python test_citizen_appeal.py   （リポジトリ直下で）
"""
import json
import os
import random
import tempfile

import citizen_appeal as CA
import service_flow as SF
from simulation import Simulation, GOVERNANCE_BASELINE

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


# ───────────── 純ロジック: チャネル判定 ─────────────

def test_channel_for():
    check("appeal → 実効チャネル", CA.channel_for(["appeal"]) == CA.CHANNEL_FULL)
    check("notice_only → プラセボチャネル", CA.channel_for(["notice_only"]) == CA.CHANNEL_NOTICE)
    check("両方あれば実効が優先", CA.channel_for(["notice_only", "appeal"]) == CA.CHANNEL_FULL)
    check("制度なし → チャネルなし", CA.channel_for([]) == CA.CHANNEL_NONE)
    check("他制度のみ → チャネルなし", CA.channel_for(["effective_hitl"]) == CA.CHANNEL_NONE)


# ───────────── 純ロジック: 申立て確率 ─────────────

def test_appeal_probability():
    cfg_u = {"base_prob": 0.5, "prob_model": "uniform"}
    check("uniform: 属性によらず base_prob",
          CA.appeal_probability(cfg_u, stakes=5, vulnerability=5) == 0.5
          and CA.appeal_probability(cfg_u, stakes=1, vulnerability=1) == 0.5)
    cfg_s = {"base_prob": 0.5, "prob_model": "stakes"}
    check("stakes: 深刻なほど申し立てやすい",
          CA.appeal_probability(cfg_s, stakes=5) == 0.5
          and abs(CA.appeal_probability(cfg_s, stakes=1) - 0.1) < 1e-9)
    cfg_v = {"base_prob": 0.5, "prob_model": "vulnerability", "vuln_penalty": 0.5}
    p1 = CA.appeal_probability(cfg_v, vulnerability=1)
    p5 = CA.appeal_probability(cfg_v, vulnerability=5)
    check("vulnerability: 脆弱なほど申し立てにくい（感度分析用ノブ）", p1 == 0.5 and p5 < p1)
    check("確率は [0,1] にクランプ",
          CA.appeal_probability({"base_prob": 2.0, "prob_model": "uniform"}) == 1.0)


# ───────────── 純ロジック: 選抜と監査エントリ ─────────────

def _deny_row(cid="c001", stakes=4, vuln=3):
    return {"step": 1, "decider_id": 7, "domain": "medical", "citizen_id": cid,
            "protected_attr": "none", "vulnerability": vuln, "stakes": stakes,
            "level": "deny", "irreversible": True}


def test_select_appeals():
    rows = [_deny_row(f"c{i:03d}") for i in range(5)]
    all_sel = CA.select_appeals(rows, {"base_prob": 1.0, "prob_model": "uniform",
                                       "max_per_step": 10}, random.Random(1))
    check("p=1.0 で全件申立て（上限内）", len(all_sel) == 5)
    capped = CA.select_appeals(rows, {"base_prob": 1.0, "prob_model": "uniform",
                                      "max_per_step": 2}, random.Random(1))
    check("max_per_step で上限（LLMコールのバウンド）", len(capped) == 2)
    none_sel = CA.select_appeals(rows, {"base_prob": 0.0, "prob_model": "uniform",
                                        "max_per_step": 10}, random.Random(1))
    check("p=0.0 で申立てなし", none_sel == [])
    a = CA.select_appeals(rows, {"base_prob": 0.5, "prob_model": "uniform",
                                 "max_per_step": 10}, random.Random(42))
    b = CA.select_appeals(rows, {"base_prob": 0.5, "prob_model": "uniform",
                                 "max_per_step": 10}, random.Random(42))
    check("同一シードで決定的（再現性）", [r["citizen_id"] for r in a] == [r["citizen_id"] for r in b])


def test_audit_entry():
    row = _deny_row()
    e = CA.audit_entry(33, row, CA.CHANNEL_FULL, reviewed=True, review_level="grant")
    check("監査: 覆り（deny→grant）を記録", e["overturned"] and e["review_level"] == "grant")
    e2 = CA.audit_entry(33, row, CA.CHANNEL_FULL, reviewed=True, review_level="deny")
    check("監査: 維持（deny→deny）は覆りでない", not e2["overturned"])
    e3 = CA.audit_entry(33, row, CA.CHANNEL_NOTICE, reviewed=False)
    check("監査: プラセボは reviewed=False・review_level なし",
          not e3["reviewed"] and e3["review_level"] is None and not e3["overturned"])


# ───────────── Simulation 配線（LLM 非依存・再判定はスタブ） ─────────────

def _sim(tmp, seed=42, resp_insts=None):
    s = Simulation(config_path="config.yaml", output_dir=tmp,
                   governance_override=GOVERNANCE_BASELINE, seed=seed,
                   resp_institutions_override=resp_insts)
    s.initialize_agents()
    return s


def _candidates(s, n=1):
    """在任 decider(7) の deny 候補を合成（case は最小限）。"""
    citizen = s.citizens[0]
    dcfg = {"human_stake": 5, "self_stake": 4, "fallback_available": False,
            "need": "救命処置の可否判定"}
    case = SF.build_case("medical", dcfg, citizen)
    out = []
    for i in range(n):
        row = {"step": s.step, "decider_id": 7, "domain": "medical",
               "citizen_id": citizen.id, "protected_attr": citizen.protected_attr,
               "vulnerability": int(citizen.vulnerability), "stakes": 5,
               "level": "deny", "irreversible": True, "decider_present": True}
        out.append(("medical", 7, citizen, case, dcfg, row))
    return out


def _stub_decider(s, level="grant"):
    a7 = next(a for a in s.agents if a.id == 7)
    calls = []

    def fake_decide(case, institution="none", show_human_stake=True,
                    show_self_stake=True, appeal_of=None):
        calls.append({"appeal_of": appeal_of, "institution": institution})
        return {"level": level, "accommodation": "", "reconciled": False,
                "factors": [], "rationale": "stub"}

    a7.decide_service = fake_decide
    return calls


def test_wiring_config():
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "a"))
        check("config.yaml 既定で enabled", bool(s.citizen_appeal_cfg.get("enabled")))
        check("既定は uniform / max_per_step=2",
              s.citizen_appeal_cfg.get("prob_model") == "uniform"
              and int(s.citizen_appeal_cfg.get("max_per_step")) == 2)
        import yaml
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg.pop("citizen_appeal", None)
        p = os.path.join(t, "cfg.yaml")
        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        s2 = Simulation(config_path=p, output_dir=os.path.join(t, "b"),
                        governance_override=GOVERNANCE_BASELINE, seed=42)
        check("citizen_appeal キー欠落 → 無効（後方互換）",
              not s2.citizen_appeal_cfg.get("enabled"))


def test_no_channel_no_appeals():
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "out"), resp_insts=[])   # 制度なし = チャネルなし
        s.citizen_appeal_cfg = {**s.citizen_appeal_cfg, "enabled": True, "base_prob": 1.0}
        cands = _candidates(s)
        out = s._process_appeals(cands, {a.id: a for a in s.agents}, "none",
                                 SF.proc_from_config(None), {})
        check("チャネルなし: 申立ては発生しない", out == [] and
              not cands[0][-1].get("appealed"))


def test_full_channel_review_and_suspension():
    with tempfile.TemporaryDirectory() as t:
        out_dir = os.path.join(t, "out")
        s = _sim(out_dir, resp_insts=["appeal"])
        s.citizen_appeal_cfg = {**s.citizen_appeal_cfg, "enabled": True, "base_prob": 1.0}
        s.step = 33
        calls = _stub_decider(s, level="grant")
        cands = _candidates(s)
        row = cands[0][-1]
        reviews = s._process_appeals(cands, {a.id: a for a in s.agents}, "none",
                                     SF.proc_from_config(None),
                                     s.resp_config.get("self_profiles") or {})
        check("実効チャネル: 再判定行が生成される", len(reviews) == 1)
        check("再判定は appeal_of=元level で呼ばれる（再判定プロンプト）",
              calls and calls[0]["appeal_of"] == "deny")
        check("停止効: 元 deny の不可逆ステータスが確定しない",
              row["appealed"] and row["suspended_pending_review"]
              and row["irreversible"] is False)
        r2 = reviews[0]
        check("再判定行: 覆り（grant）が world で実現される",
              r2["appeal_review"] and r2["original_level"] == "deny"
              and r2["level"] == "grant")
        check("再判定行にも run_id/schema", r2.get("run_id") == s.run_id
              and r2.get("schema_version") == s.schema_version)
        with open(os.path.join(out_dir, "appeal_audit.jsonl"), encoding="utf-8") as f:
            audit = [json.loads(line) for line in f]
        check("監査: overturned=True を記録", audit and audit[0]["overturned"]
              and audit[0]["channel"] == "appeal")


def test_upheld_deny_still_counts():
    """維持された deny は再判定行として不可逆害が確定する（medical は fallback なし）。"""
    with tempfile.TemporaryDirectory() as t:
        s = _sim(os.path.join(t, "out"), resp_insts=["appeal"])
        s.citizen_appeal_cfg = {**s.citizen_appeal_cfg, "enabled": True, "base_prob": 1.0}
        _stub_decider(s, level="deny")
        cands = _candidates(s)
        reviews = s._process_appeals(cands, {a.id: a for a in s.agents}, "none",
                                     SF.proc_from_config(None),
                                     s.resp_config.get("self_profiles") or {})
        check("維持: 元行は保留・再判定行が不可逆害を持つ",
              cands[0][-1]["irreversible"] is False
              and reviews[0]["level"] == "deny" and reviews[0]["irreversible"])


def test_notice_only_placebo():
    with tempfile.TemporaryDirectory() as t:
        out_dir = os.path.join(t, "out")
        s = _sim(out_dir, resp_insts=["notice_only"])
        s.citizen_appeal_cfg = {**s.citizen_appeal_cfg, "enabled": True, "base_prob": 1.0}
        calls = _stub_decider(s, level="grant")
        cands = _candidates(s)
        row = cands[0][-1]
        reviews = s._process_appeals(cands, {a.id: a for a in s.agents}, "none",
                                     SF.proc_from_config(None), {})
        check("プラセボ: 申立ては記録されるが再判定なし",
              row["appealed"] and reviews == [] and calls == [])
        check("プラセボ: 停止効なし（不可逆ステータス維持 = 機序④が残る）",
              row["irreversible"] is True and "suspended_pending_review" not in row)
        with open(os.path.join(out_dir, "appeal_audit.jsonl"), encoding="utf-8") as f:
            audit = [json.loads(line) for line in f]
        check("監査: reviewed=False", audit and not audit[0]["reviewed"])


def test_run_meta_and_run_id():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "run")
        s = _sim(out)
        s.write_run_meta()
        with open(os.path.join(out, "run_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        check("run_meta に citizen_appeal_enabled", meta.get("citizen_appeal_enabled") is True)
        # citizen_appeal 設定差は run_id を変える（アーム弁別）
        import yaml
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["citizen_appeal"] = {"enabled": True, "base_prob": 0.9,
                                 "prob_model": "uniform", "max_per_step": 2}
        p = os.path.join(t, "cfg.yaml")
        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        s2 = Simulation(config_path=p, output_dir=os.path.join(t, "b"),
                        governance_override=GOVERNANCE_BASELINE, seed=42)
        check("citizen_appeal 設定差 → run_id 違い", s.run_id != s2.run_id)


if __name__ == "__main__":
    test_channel_for()
    test_appeal_probability()
    test_select_appeals()
    test_audit_entry()
    test_wiring_config()
    test_no_channel_no_appeals()
    test_full_channel_review_and_suspension()
    test_upheld_deny_still_counts()
    test_notice_only_placebo()
    test_run_meta_and_run_id()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        failed = [n for n, ok in results if not ok]
        print("FAILED:", failed)
        raise SystemExit(1)
    print("ALL PASS")
