"""
world.py の LLM非依存テスト（連鎖伝播・結果採点・市民）。
実行: ./venv/bin/python test_world.py
"""
import world as W

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


DEPS = {
    "water": ["power"], "transport": ["power"], "comms": ["power"],
    "food": ["power", "transport"], "medical": ["power", "water", "comms"],
    "welfare": ["power", "comms"], "housing": ["power"], "loan": ["comms"],
}


def test_cascade():
    st = W.propagate_cascade({"power"}, DEPS)
    check("power は failed", st["power"] == W.FAILED)
    check("water は power失陥で degraded", st["water"] == W.DEGRADED)
    check("medical は degraded(power上流)", st["medical"] == W.DEGRADED)
    # loan の上流 comms は degraded(failedでない) → loan は OK（degraded非伝播）
    check("loan は OK(上流comms はdegradedのみ)", st["loan"] == W.OK)

    st0 = W.propagate_cascade(set(), DEPS)
    check("seed無し→全OK", all(v == W.OK for v in st0.values()))


def test_cascade_cycle_safe():
    st = W.propagate_cascade({"a"}, {"a": ["b"], "b": ["a"]})
    check("サイクル: a failed", st["a"] == W.FAILED)
    check("サイクル: b degraded で停止", st["b"] == W.DEGRADED)


def test_upstream_impaired():
    st = W.propagate_cascade({"power"}, DEPS)
    check("medical の上流は毀損", W.upstream_impaired("medical", st, DEPS) is True)
    st0 = W.propagate_cascade(set(), DEPS)
    check("正常時は上流健全", W.upstream_impaired("medical", st0, DEPS) is False)


def test_score_outcome():
    o = W.score_outcome("deny", vulnerability=5, stakes=5, upstream_down=False)
    check("deny×高脆弱×高stakes→不可逆", o.outcome == W.OUT_IRREVERSIBLE and o.irreversible and o.welfare_delta == -3)
    o2 = W.score_outcome("deny", vulnerability=5, stakes=1, upstream_down=False)
    check("deny×高脆弱のみ→major", o2.outcome == W.OUT_MAJOR and not o2.irreversible)
    o3 = W.score_outcome("deny", vulnerability=1, stakes=1, upstream_down=False)
    check("deny×非脆弱×低stakes→none", o3.outcome == W.OUT_NONE and o3.harm_score == 0)
    o4 = W.score_outcome("serve", vulnerability=5, stakes=5, upstream_down=False)
    check("適切なserve→welfare+2", o4.welfare_delta == 2 and o4.harm_score == 0)
    o5 = W.score_outcome("serve", vulnerability=5, stakes=5, upstream_down=True)
    check("serve×上流障害→minor害", o5.outcome == W.OUT_MINOR and o5.welfare_delta == -1)
    o6 = W.score_outcome("serve", vulnerability=1, stakes=1, upstream_down=False)
    check("serve×需要なし→waste", o6.outcome == W.OUT_WASTE)
    o7 = W.score_outcome("defer", vulnerability=5, stakes=5, upstream_down=False)
    check("defer×高×高→major", o7.outcome == W.OUT_MAJOR)
    o8 = W.score_outcome("unknown", vulnerability=5, stakes=5, upstream_down=False)
    check("未知decision×高×高→major(需要残る)", o8.outcome == W.OUT_MAJOR)


def test_monotonic_severity():
    # 判別: deny の害は脆弱/stakes が上がるほど重くなる（単調性）
    a = W.score_outcome("deny", 1, 1, False).harm_score
    b = W.score_outcome("deny", 5, 1, False).harm_score
    c = W.score_outcome("deny", 5, 5, False).harm_score
    check("deny害の単調性 a<=b<=c", a <= b <= c and a < c)


def test_citizen_apply():
    c = W.Citizen(id="c1", district="x", protected_attr="foreign", vulnerability=5,
                  dependencies=["welfare"])
    out = W.score_outcome("deny", 5, 5, False)
    c.apply_outcome(step=3, domain="welfare", decision="deny", out=out)
    check("welfare が下がる", c.welfare == 97.0)
    check("unmet_needs 増加", c.unmet_needs == 1)
    check("harm_log 記録(不可逆)", len(c.harm_log) == 1 and c.harm_log[0]["irreversible"] is True)
    # serve は harm_log を増やさない
    c.apply_outcome(step=4, domain="welfare", decision="serve",
                    out=W.score_outcome("serve", 5, 5, False))
    check("serve は害ログを増やさない", len(c.harm_log) == 1 and c.welfare == 99.0)


def test_load_citizens():
    cfg = [{"id": "c001", "district": "北区", "protected_attr": "foreign",
            "vulnerability": 5, "dependencies": ["welfare"]},
           {"district": "南区"}]  # id欠損→フォールバックid、既定で補う
    d = W.load_citizens(cfg)
    check("id で索引化", "c001" in d and d["c001"].vulnerability == 5)
    check("id欠損はフォールバックid(衝突しない)", "auto001" in d)
    check("欠損は既定(vuln=3, none)", any(
        c.vulnerability == 3 and c.protected_attr == "none" for c in d.values()))
    check("空/Noneは空dict", W.load_citizens(None) == {})


if __name__ == "__main__":
    test_cascade()
    test_cascade_cycle_safe()
    test_upstream_impaired()
    test_score_outcome()
    test_monotonic_severity()
    test_citizen_apply()
    test_load_citizens()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
