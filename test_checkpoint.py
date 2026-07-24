"""
チェックポイント/再開（P1-C）の LLM非依存テスト。Ollama をモックし実 run で検証。
実行: ./venv/bin/python test_checkpoint.py
"""
import json
import os
import random
import tempfile

from simulation import Simulation

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


class MockLLM:
    model = "mock-model"

    def check_connection(self):
        return True

    def check_model_exists(self):
        return True

    def model_digest(self):
        return "mockdigest"

    def generate(self, prompt, temperature=None, max_tokens=None, seed_key=None):
        return ('{"level":"partial","accommodation":"折衷","reconciled":false,'
                '"factors":["需要"],"rationale":"ck"}')


def _sim(tmp, seed, duration):
    s = Simulation(config_path="config.yaml", output_dir=tmp, seed=seed)
    s.llm_client = MockLLM()
    s.duration = duration
    return s


def test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        s1 = _sim(d, 42, 3)
        s1.reset_output_logs()
        s1.run()
        after_run_py = random.getstate()   # 走行直後のグローバル RNG 状態
        s1.save_checkpoint()
        ckpath = os.path.join(d, "checkpoint.json")
        check("checkpoint.json が生成される", os.path.exists(ckpath))

        # 別 Simulation で復元（agents 構築後に load）
        s2 = _sim(os.path.join(t, "run2"), 42, 3)
        s2.initialize_agents()
        loaded = s2.load_checkpoint(path=ckpath)
        check("load_checkpoint が True", loaded is True)
        check("step 復元", s2.step == s1.step == 3)
        check("agent 数一致", len(s2.agents) == len(s1.agents))
        # citizen 可変状態の一致（welfare の合計で照合）
        w1 = round(sum(c.welfare for c in s1.citizens), 4)
        w2 = round(sum(c.welfare for c in s2.citizens), 4)
        check("citizen welfare 合計一致", w1 == w2)
        # 内生機構カウンタの一致
        check("irrev_deny_counts 一致", s2._irrev_deny_counts == s1._irrev_deny_counts)
        check("dead_citizens 一致", s2._dead_citizens == s1._dead_citizens)
        # RNG 復元（load はグローバル random を保存時状態へ戻す）
        check("グローバル RNG 復元（save 時状態に一致）", random.getstate() == after_run_py)
        # agent 個別状態（先頭 agent の self_concept / introspection_count）
        a1, a2 = s1.agents[0], next(a for a in s2.agents if a.id == s1.agents[0].id)
        check("agent self_concept 復元", a2.self_concept == a1.self_concept)
        check("agent introspection_count 復元", a2.introspection_count == a1.introspection_count)


def test_deleted_agent_dropped_on_load():
    """checkpoint に無い agent（削除済み）は load で self.agents から除かれる。"""
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        s1 = _sim(d, 7, 1)
        s1.reset_output_logs()
        s1.run()
        s1.save_checkpoint()
        ckpath = os.path.join(d, "checkpoint.json")
        with open(ckpath, encoding="utf-8") as f:
            ck = json.load(f)
        removed_id = ck["agents"][0]["id"]
        ck["agents"] = ck["agents"][1:]            # 1体を「削除済み」に見立てる
        with open(ckpath, "w", encoding="utf-8") as f:
            json.dump(ck, f, ensure_ascii=False)

        s2 = _sim(os.path.join(t, "run2"), 7, 1)
        s2.initialize_agents()
        n_before = len(s2.agents)
        s2.load_checkpoint(path=ckpath)
        check("削除済み agent は load で除去", len(s2.agents) == n_before - 1)
        check("除去された id は存在しない", all(a.id != removed_id for a in s2.agents))


def test_truncate_outputs_after():
    """出力 jsonl から step>N の行を切り詰め、step フィールドの無い行は残す。"""
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        os.makedirs(d)
        p = os.path.join(d, "decision_ledger.jsonl")
        rows = [{"step": 1, "x": "a"}, {"step": 2, "x": "b"},
                {"step": 3, "x": "c"}, {"step": 4, "x": "partial-crash"}]
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write(json.dumps({"note": "no-step-field"}) + "\n")
        s = _sim(d, 1, 5)
        s.truncate_outputs_after(3)
        with open(p, encoding="utf-8") as f:
            kept = [json.loads(x) for x in f if x.strip()]
        steps = [r.get("step") for r in kept]
        check("step>3 の行は消える", 4 not in steps)
        check("step<=3 の行は残る", steps.count(1) == 1 and steps.count(2) == 1 and steps.count(3) == 1)
        check("step フィールド無しの行は残す", any(r.get("note") == "no-step-field" for r in kept))


def test_resume_continues_not_reexecute():
    """load 後 step=N なら次の step_simulation で N+1 が実行される（N を再実行しない）。"""
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        s1 = _sim(d, 3, 2)
        s1.reset_output_logs()
        s1.run()
        s1.save_checkpoint()
        s2 = _sim(os.path.join(t, "run2"), 3, 5)
        s2.initialize_agents()
        s2.load_checkpoint(path=os.path.join(d, "checkpoint.json"))
        before = s2.step
        s2.step_simulation()
        check("再開後の1 step で step は N→N+1", s2.step == before + 1)


if __name__ == "__main__":
    test_save_load_roundtrip()
    test_deleted_agent_dropped_on_load()
    test_truncate_outputs_after()
    test_resume_continues_not_reexecute()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
