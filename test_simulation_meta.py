"""
run_id 決定性 / reset_output_logs / write_run_meta の LLM非依存テスト。
Simulation 構築は Ollama へ接続しない（OllamaClient は生成のみ）。
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


if __name__ == "__main__":
    test_run_id_determinism()
    test_reset_output_logs()
    test_write_run_meta()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
