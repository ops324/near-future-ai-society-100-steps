"""
E2E スモーク（監査 P1）: Ollama をモックし sim→analyze→report→resp_frame を1本通す統合テスト。

既存の 13 スイートは各部品の単体テストで、パイプライン全体が繋がって走るかは検証していない
（監査の最大リスク＝「本走行・結合が一度も通っていない」）。本テストは Chromium/ffmpeg/Ollama
なしで、各段が配線され非空出力を出すかを短時間で確認する（実 PNG/PDF 化＝Chromium は別・環境依存）。

実行: ./venv/bin/python test_pipeline_smoke.py
"""
import os
import tempfile

import analyze_compare as ac
import report_lib as RL
import resp_frame as RF
from simulation import Simulation

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


class MockLLM:
    """決定論モック。全 generate 呼び出しに有効なサービス決定JSONを返す
    （decide_service はパース可能・メッセージ生成でも非空テキストになる）。Ollama 非依存。"""
    model = "mock-model"

    def check_connection(self):
        return True

    def check_model_exists(self):
        return True

    def model_digest(self):
        return "mockdigest"

    def generate(self, prompt, temperature=None, max_tokens=None, seed_key=None):
        return ('{"level":"partial","accommodation":"折衷案を提示",'
                '"reconciled":false,"factors":["需要の深刻さ"],"rationale":"スモーク"}')


def _run_sim(tmp, seed):
    sim = Simulation(config_path="config.yaml", output_dir=tmp, seed=seed)
    sim.llm_client = MockLLM()      # Ollama をモックに差し替え（¥0・非決定性なし）
    sim.duration = 2                # スモークは 2 step
    sim.reset_output_logs()
    sim.run()
    sim.write_run_meta()
    return sim


def test_pipeline_smoke():
    with tempfile.TemporaryDirectory() as t:
        a = os.path.join(t, "arm_a")
        b = os.path.join(t, "arm_b")
        _run_sim(a, 42)
        _run_sim(b, 43)

        # 1) sim 段: サービス決定系は毎 step 決定論的に出る＝非空を要求。
        #    messages.jsonl の中身は創発（通信が起きたか）依存なので「生成された」ことのみ要求。
        for d in (a, b):
            for fn in ("positions.jsonl", "decision_ledger.jsonl",
                       "attribution.jsonl", "run_meta.json"):
                p = os.path.join(d, fn)
                check(f"sim出力 {os.path.basename(d)}/{fn} 非空",
                      os.path.exists(p) and os.path.getsize(p) > 0)
            # messages.jsonl は通信という創発が起きた時のみ遅延生成される（無通信なら不在が正常）
            # ため plumbing 指標にしない。その不在も含め analyze が後方互換に処理することを 2) で確認。

        # 2) analyze 段: 指標 dict を返す
        m = ac.analyze(a)
        check("analyze が指標dictを返す（service_decisions 含む）",
              isinstance(m, dict) and "service_decisions" in m)

        # 3) report 段: HTML を組み立てる（Chromium での PDF 化は別・環境依存）
        htm = RL.build_html(arm_specs={"baseline": a, "governed": b})
        check("report build_html が非空HTML", "<!DOCTYPE html" in htm and len(htm) > 1000)
        check("report に機械検証注記が載る（P0-2 連携）", "機械検証" in htm)

        # 4) 動画 Part2 段: resp_frame の純 HTML 生成（PNG 化=Chromium は別）
        states = RF.frame_series(a, duration=2)
        check("resp_frame frame_series が states を返す", len(states) >= 1)
        fh = RF.render_frame_html(states[0])
        check("resp_frame render_frame_html が非空SVG入りHTML", "<svg" in fh and len(fh) > 500)


if __name__ == "__main__":
    test_pipeline_smoke()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
