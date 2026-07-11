"""
reliability_probe.py の LLM非依存テスト（純関数＋応答評価。Ollama を呼ばない）。
実行: ./venv/bin/python test_reliability_probe.py
"""
import reliability_probe as rp

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def test_has_kana():
    check("ひらがなを検知", rp.has_kana("こんにちは"))
    check("カタカナを検知", rp.has_kana("エネルギー"))
    check("漢字のみは非かな", not rp.has_kana("停電対応"))
    check("中国語(簡体)は非かな", not rp.has_kana("你好世界"))
    check("英語は非かな", not rp.has_kana("hello world"))
    check("空文字は非かな", not rp.has_kana(""))


def test_entropy_and_modal():
    check("全て同一→エントロピー0", rp.shannon_entropy(["a", "a", "a"]) == 0.0)
    check("五分五分→1bit", abs(rp.shannon_entropy(["a", "b"]) - 1.0) < 1e-9)
    label, frac = rp.modal_stability(["a", "a", "b"])
    check("最頻ラベル", label == "a")
    check("最頻割合=2/3", abs(frac - 2/3) < 1e-9)
    check("空は(None,0)", rp.modal_stability([]) == (None, 0.0))


def test_summarize():
    recs = [
        {"valid_json": True, "keys_ok": True, "enum_ok": True, "kana_ok": True, "label": "stay"},
        {"valid_json": True, "keys_ok": True, "enum_ok": False, "kana_ok": True, "label": "stay"},
        {"valid_json": False, "keys_ok": False, "enum_ok": False, "kana_ok": False, "label": "move:up"},
    ]
    s = rp.summarize(recs)
    check("valid_json率=2/3", abs(s["valid_json_frac"] - 2/3) < 1e-9)
    check("enum_ok率=1/3", abs(s["enum_ok_frac"] - 1/3) < 1e-9)
    check("最頻=stay", s["modal_label"] == "stay")
    check("label分布", s["label_dist"] == {"stay": 2, "move:up": 1})


def test_evaluate_message():
    a = rp._probe_agent()
    r1 = a and rp.evaluate_message_response(
        a, '{"message":"了解","human_reply":"すぐ対応します","human_reply_to":"2","reasoning":"r"}')
    check("有効JSON+応答→valid", r1["valid_json"] and r1["keys_ok"])
    check("応答先ラベル reply:2", r1["label"] == "reply:2")
    check("かな検知(日本語)", r1["kana_ok"])
    r2 = rp.evaluate_message_response(a, '{"message":"様子見","reasoning":"r"}')
    check("応答なし→no_reply", r2["label"] == "no_reply")
    r3 = rp.evaluate_message_response(a, "壊れた出力（JSONなし）")
    check("JSONなし→valid_json False", r3["valid_json"] is False)


def test_evaluate_action():
    a = rp._probe_agent()
    r1 = rp.evaluate_action_response(a, '{"action":"move","direction":"up","memory":"m","reasoning":"r"}')
    check("move+方向→enum_ok", r1["enum_ok"] and r1["label"] == "move:up")
    r2 = rp.evaluate_action_response(a, '{"action":"stay","reasoning":"r"}')
    check("stay→enum_ok", r2["enum_ok"] and r2["label"] == "stay")
    r3 = rp.evaluate_action_response(a, '{"action":"fly","direction":"沖"}')
    check("不正enum→enum_ok False", r3["enum_ok"] is False)


if __name__ == "__main__":
    test_has_kana()
    test_entropy_and_modal()
    test_summarize()
    test_evaluate_message()
    test_evaluate_action()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
