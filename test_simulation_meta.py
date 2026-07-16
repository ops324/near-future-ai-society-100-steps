"""
run_id 決定性 / reset_output_logs / write_run_meta の LLM非依存テスト。
Simulation 構築は Ollama へ接続しない（OllamaClient は生成のみ）。
※ write_run_meta のみ llm.digest 取得のため localhost:11434 へベストエフォートの
  HTTP GET を1回試みる（未接続なら即 None・テストは digest の値に依存しない）。
実行: ./venv/bin/python test_simulation_meta.py   （リポジトリ直下で）
"""
import json
import os
import tempfile

from simulation import Simulation, GOVERNANCE_BASELINE, GOVERNANCE_GOVERNED

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def _sim(tmp, seed, gov):
    return Simulation(config_path="config.yaml", output_dir=tmp,
                      governance_override=gov, seed=seed)


def test_run_id_determinism():
    with tempfile.TemporaryDirectory() as t:
        s1 = _sim(os.path.join(t, "a"), 42, GOVERNANCE_GOVERNED)
        s2 = _sim(os.path.join(t, "b"), 42, GOVERNANCE_GOVERNED)
        s3 = _sim(os.path.join(t, "c"), 7, GOVERNANCE_GOVERNED)
        s4 = _sim(os.path.join(t, "d"), 42, GOVERNANCE_BASELINE)
        s5 = _sim(os.path.join(t, "e"), None, GOVERNANCE_GOVERNED)
    check("同一 seed+governance → 同一 run_id", s1.run_id == s2.run_id)
    check("seed 違い → run_id 違い", s1.run_id != s3.run_id)
    check("governance 違い(baseline) → run_id 違い", s1.run_id != s4.run_id)
    check("seed=None は 12桁hex(ランダム)", len(s5.run_id) == 12)


def test_reset_output_logs():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "run")
        s = _sim(out, 42, GOVERNANCE_GOVERNED)
        os.makedirs(out, exist_ok=True)
        for fn in ["messages.jsonl", "decision_ledger.jsonl"]:
            with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
                f.write('{"x":1}\n')
        s.reset_output_logs()
        gone = [not os.path.exists(os.path.join(out, fn))
                for fn in ["messages.jsonl", "decision_ledger.jsonl"]]
        check("追記ログを初期化（削除）", all(gone))


def test_write_run_meta():
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "run")
        s = _sim(out, 42, GOVERNANCE_GOVERNED)
        s.write_run_meta(extra={"governance_mode": "governed"})
        with open(os.path.join(out, "run_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    for k in ["schema_version", "run_id", "seed", "duration", "governance", "governance_mode"]:
        check(f"run_meta に {k}", k in meta)
    check("seed が保存される", meta["seed"] == 42)
    check("run_meta に resp_institutions（PR-計測）", "resp_institutions" in meta)
    # 実行前修正: LLM の同定情報（モデル/サンプリング設定＋digest ベストエフォート）
    check("run_meta に llm ブロック", isinstance(meta.get("llm"), dict))
    for k in ["model", "temperature", "max_tokens", "digest"]:
        check(f"run_meta.llm に {k}", k in meta.get("llm", {}))
    check("llm.model は config と一致", meta.get("llm", {}).get("model") == "qwen2.5:14b")


def test_run_id_arm_discrimination():
    """PR-計測: run_id 署名は seed|governance|responsibility|内生機構|LLM設定。
    アーム差は必ず別 id。"""
    with tempfile.TemporaryDirectory() as t:
        s1 = _sim(os.path.join(t, "a"), 42, GOVERNANCE_GOVERNED)
        s2 = Simulation(config_path="config.yaml", output_dir=os.path.join(t, "b"),
                        governance_override=GOVERNANCE_GOVERNED, seed=42,
                        resp_institutions_override=["appeal"])
        s3 = Simulation(config_path="config.yaml", output_dir=os.path.join(t, "c"),
                        governance_override=GOVERNANCE_GOVERNED, seed=42,
                        resp_institutions_override=["appeal"])
        s4 = Simulation(config_path="config.yaml", output_dir=os.path.join(t, "d"),
                        governance_override=GOVERNANCE_GOVERNED, seed=42,
                        resp_institutions_override=[])
    check("resp_institutions 差 → run_id 違い", s1.run_id != s2.run_id)
    check("同一 resp_institutions → 同一 run_id", s2.run_id == s3.run_id)
    check("明示の空リスト上書きは config 既定と同一視しない前提を確認（[] vs 無指定）",
          s4.run_id != s2.run_id)
    check("run_meta 用に resp_config へ反映",
          s2.resp_config.get("resp_institutions") == ["appeal"])


def test_run_id_mechanism_discrimination():
    """PR-計測: deletion_mode / citizen_death.mode の差も run_id を変える（rules vs scripted）。"""
    import yaml as _yaml
    with tempfile.TemporaryDirectory() as t:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f)
        cfg["deletion_mode"] = "scripted"
        p = os.path.join(t, "cfg_scripted.yaml")
        with open(p, "w", encoding="utf-8") as f:
            _yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        s_rules = _sim(os.path.join(t, "a"), 42, GOVERNANCE_GOVERNED)
        s_scripted = Simulation(config_path=p, output_dir=os.path.join(t, "b"),
                                governance_override=GOVERNANCE_GOVERNED, seed=42)
    check("deletion_mode 差（rules vs scripted） → run_id 違い",
          s_rules.run_id != s_scripted.run_id)


def test_run_id_llm_discrimination():
    """実行前修正: LLM モデル/サンプリング設定の差も run_id を変える（クロスモデル比較の
    アーム弁別性）。digest は環境依存＝署名に含めない（run_meta にのみ記録）。"""
    import yaml as _yaml
    with tempfile.TemporaryDirectory() as t:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f)
        cfg["llm"]["model"] = "llama3.1:8b"
        p_model = os.path.join(t, "cfg_model.yaml")
        with open(p_model, "w", encoding="utf-8") as f:
            _yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        cfg["llm"]["model"] = "qwen2.5:14b"
        cfg["llm"]["temperature"] = 0.2
        p_temp = os.path.join(t, "cfg_temp.yaml")
        with open(p_temp, "w", encoding="utf-8") as f:
            _yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        s_base = _sim(os.path.join(t, "a"), 42, GOVERNANCE_GOVERNED)
        s_model = Simulation(config_path=p_model, output_dir=os.path.join(t, "b"),
                             governance_override=GOVERNANCE_GOVERNED, seed=42)
        s_temp = Simulation(config_path=p_temp, output_dir=os.path.join(t, "c"),
                            governance_override=GOVERNANCE_GOVERNED, seed=42)
    check("llm.model 差 → run_id 違い", s_base.run_id != s_model.run_id)
    check("llm.temperature 差 → run_id 違い", s_base.run_id != s_temp.run_id)
    check("llm_meta に model/temperature を保持",
          s_base.llm_meta.get("model") == "qwen2.5:14b"
          and s_base.llm_meta.get("temperature") == 0.7)
    check("llm_meta に digest を含めない（署名の環境非依存性）",
          "digest" not in s_base.llm_meta)


def test_institution_wording_arm():
    """実行前修正: institution_wording はアームノブ — run_id を変え、run_meta に記録され、
    不正値は起動時 ValueError（typo が無言で suggestive 測定になる事故を防ぐ）。"""
    import yaml as _yaml
    with tempfile.TemporaryDirectory() as t:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f)
        cfg["responsibility"]["institution_wording"] = "fact_only"
        p_fact = os.path.join(t, "cfg_fact.yaml")
        with open(p_fact, "w", encoding="utf-8") as f:
            _yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        cfg["responsibility"]["institution_wording"] = "fact-only"   # typo
        p_bad = os.path.join(t, "cfg_bad.yaml")
        with open(p_bad, "w", encoding="utf-8") as f:
            _yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

        s_base = _sim(os.path.join(t, "a"), 42, GOVERNANCE_GOVERNED)
        out_f = os.path.join(t, "b")
        s_fact = Simulation(config_path=p_fact, output_dir=out_f,
                            governance_override=GOVERNANCE_GOVERNED, seed=42)
        check("institution_wording 差 → run_id 違い（アーム弁別性）",
              s_base.run_id != s_fact.run_id)
        s_fact.write_run_meta()
        with open(os.path.join(out_f, "run_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        check("run_meta に institution_wording を記録",
              meta.get("institution_wording") == "fact_only")
        check("run_meta に institution を記録", "institution" in meta)

        raised = False
        try:
            Simulation(config_path=p_bad, output_dir=os.path.join(t, "c"),
                       governance_override=GOVERNANCE_GOVERNED, seed=42)
        except ValueError:
            raised = True
        check("不正な institution_wording は起動時 ValueError", raised)


if __name__ == "__main__":
    test_run_id_determinism()
    test_reset_output_logs()
    test_write_run_meta()
    test_run_id_arm_discrimination()
    test_run_id_mechanism_discrimination()
    test_run_id_llm_discrimination()
    test_institution_wording_arm()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
