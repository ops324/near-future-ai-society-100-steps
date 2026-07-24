"""
analyze_compare.py の LLM非依存テスト（合成ログで検証）。
実行: ./venv/bin/python test_analyze_compare.py
"""
import json
import os
import tempfile

import analyze_compare as ac

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def _write_run(dir_path, messages):
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, "messages.jsonl"), "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def _human(affect, stakes):
    return {"step": 1, "from": -1, "to": 7, "message": "x", "source": "human",
            "category": "appeal", "affect": affect, "stakes": stakes}


def _reply(aff, sta, method):
    return {"step": 1, "from": 7, "to": -1, "message": "対応します", "source": "agent",
            "category": "human_reply", "answered_affect": aff, "answered_stakes": sta,
            "answered_match_method": method}


def _edge(a, b):
    return {"step": 1, "from": a, "to": b, "message": "m", "source": "agent", "category": ""}


def test_analyze_basic():
    msgs = []
    msgs += [_human(1, 5) for _ in range(3)]   # quiet-serious ×3
    msgs += [_human(5, 1) for _ in range(2)]   # loud-trivial ×2
    # 高信頼(index)で quiet-serious に2件応答、1件は低信頼fallback
    msgs += [_reply(1, 5, "index"), _reply(1, 5, "content"), _reply(1, 5, "fallback_recent")]
    # 相互エッジ（reciprocity=1.0）
    msgs += [_edge(0, 1), _edge(1, 0)]
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "run")
        _write_run(d, msgs)
        r = ac.analyze(d)
    check("human_msgs=5", r["human_msgs"] == 5)
    check("direct_replies=3", r["direct_replies"] == 3)
    check("direct_response_rate=0.6", abs(r["direct_response_rate"] - 0.6) < 1e-9)
    check("quiet_serious 分母=3", r["_denom_quiet_serious"] == 3)
    check("quiet_serious 応答率=2/3(高信頼のみ)", abs(r["quiet_serious_answered"] - 2/3) < 1e-9)
    check("loud_trivial 応答率=0.0", r["loud_trivial_answered"] == 0.0)
    check("低信頼fallback割合=1/3", abs(r["reply_fallback_frac"] - 1/3) < 1e-9)
    check("reciprocity=1.0", r["reciprocity"] == 1.0)


def test_low_confidence_only_not_counted():
    # 全応答が fallback（旧pending[-1]式）→ 高信頼バケットは空、fallback割合=1.0
    msgs = [_human(1, 5), _reply(1, 5, "fallback_recent")]
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "run")
        _write_run(d, msgs)
        r = ac.analyze(d)
    check("低信頼のみ: quiet_serious 応答率=0", r["quiet_serious_answered"] == 0.0)
    check("低信頼のみ: fallback割合=1.0", r["reply_fallback_frac"] == 1.0)


def test_denominator_suppression():
    # 分母3(<DENOM_MIN=5)の率は伏せられる
    runs = [{"quiet_serious_answered": 0.5, "_denom_quiet_serious": 3}]
    cell = ac._fmt_cell("quiet_serious_answered", "rate", "_denom_quiet_serious", runs)
    check("微小分母の率は抑制", cell == f"分母<{ac.DENOM_MIN}")
    runs2 = [{"quiet_serious_answered": 0.5, "_denom_quiet_serious": 10}]
    cell2 = ac._fmt_cell("quiet_serious_answered", "rate", "_denom_quiet_serious", runs2)
    check("十分な分母なら率を表示", "%" in cell2)


def test_dist_multi_seed():
    d = ac._dist([0.2, 0.4, 0.6, 0.8, 1.0])
    check("中央値", abs(d["median"] - 0.6) < 1e-9)
    check("n=本数", d["n"] == 5)
    check("Q1<中央値<Q3", d["q1"] < d["median"] < d["q3"])
    check("None混在を除外", ac._dist([None, 0.5, None])["median"] == 0.5)
    check("全None→None", ac._dist([None, None]) is None)


def test_expand_spec():
    with tempfile.TemporaryDirectory() as tmp:
        for s in ["a_s1", "a_s2"]:
            os.makedirs(os.path.join(tmp, s))
        label, dirs = ac.expand_spec(f"baseline={tmp}/a_s*")
        check("label抽出", label == "baseline")
        check("glob展開で2 run", len(dirs) == 2)
        label2, dirs2 = ac.expand_spec(f"{tmp}/a_s1")
        check("単一ディレクトリ指定", len(dirs2) == 1)


# ───────────── PR-計測: 機序別・不可逆率・AIR・逆進性・E/S/D タグ ─────────────

def _ledger_row(cid, attr, vuln, level, irrev, welfare):
    return {"step": 1, "decider_id": 7, "domain": "medical", "citizen_id": cid,
            "protected_attr": attr, "vulnerability": vuln, "level": level,
            "irreversible": irrev, "welfare_delta": welfare, "service_gap": False}


def _attr_row(robodebt):
    return {"step": 1, "decider_id": 7, "domain": "medical", "scapegoat": False,
            "gap_legitimate": 0.0, "robodebt": robodebt}


def _write_jsonl(dir_path, name, rows):
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, name), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_mechanism_rates():
    attrib = [
        _attr_row({"auto_adverse": True, "burden_reversed": True,
                   "no_effective_review": True, "irreversible_pending": True, "reproduced": True}),
        _attr_row({"auto_adverse": True, "burden_reversed": False,
                   "no_effective_review": False, "irreversible_pending": False, "reproduced": False}),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "run")
        _write_run(d, [])
        _write_jsonl(d, "attribution.jsonl", attrib)
        r = ac.analyze(d)
    check("機序①率=1.0", r["mech_auto_adverse_rate"] == 1.0)
    check("機序②率=0.5", r["mech_burden_reversed_rate"] == 0.5)
    check("機序③率=0.5", r["mech_no_effective_review_rate"] == 0.5)
    check("機序④率=0.5", r["mech_irreversible_pending_rate"] == 0.5)
    check("reproduced率=0.5（連言）", r["robodebt_reproduced_rate"] == 0.5)


def test_irreversible_air_and_regressivity():
    # AIR: none 群 deny 0/2、foreign 群 deny 2/2 → 選択率(非deny) none=1.0 / foreign=0.0 → AIR=0.0
    # 逆進性: 高脆弱(5) の1行あたり害 = (3+5)/2=4.0、低脆弱(1) = 2/2=1.0 → 比 4.0
    ledger = [
        _ledger_row("c1", "none",    1, "grant", False, 0.0),
        _ledger_row("c2", "none",    1, "partial", False, -2.0),
        _ledger_row("c3", "foreign", 5, "deny", True, -3.0),
        _ledger_row("c4", "foreign", 5, "deny", True, -5.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "run")
        _write_run(d, [])
        _write_jsonl(d, "decision_ledger.jsonl", ledger)
        r = ac.analyze(d)
    check("不可逆害の発生率=0.5", r["irreversible_rate"] == 0.5)
    check("AIR=0.0（foreign が全deny）", r["air_protected_live"] == 0.0)
    check("害の逆進性=4.0（高脆弱側へ偏る）", abs(r["harm_incidence_ratio"] - 4.0) < 1e-9)
    check("逆進性の分母（小さい側の群サイズ）", r["_denom_vuln_min"] == 2)


def test_regressivity_backward_compat():
    # 旧 run（vulnerability 列なし）→ 逆進性は None（表示 "-"）で後方互換
    ledger = [{"step": 1, "decider_id": 7, "domain": "medical", "citizen_id": "c1",
               "protected_attr": "none", "level": "deny", "irreversible": True,
               "welfare_delta": -3.0, "service_gap": False}]
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "run")
        _write_run(d, [])
        _write_jsonl(d, "decision_ledger.jsonl", ledger)
        r = ac.analyze(d)
    check("旧 run: 逆進性は None（後方互換）", r["harm_incidence_ratio"] is None)
    check("旧 run: 不可逆率は算出できる", r["irreversible_rate"] == 1.0)


def test_esd_tags():
    valid = ("[E] ", "[S] ", "[D] ", "[X] ")
    check("ROWS 全行に E/S/D/X タグ", all(label.startswith(valid) for label, *_ in ac.ROWS))
    keys = {key for _l, key, _k, _d in ac.ROWS}
    for k in ("mech_auto_adverse_rate", "mech_burden_reversed_rate",
              "mech_no_effective_review_rate", "mech_irreversible_pending_rate",
              "irreversible_rate", "air_protected_live", "harm_incidence_ratio"):
        check(f"ROWS に {k}", k in keys)
    import report_lib as rl
    check("report RATE_ROWS 全行にタグ",
          all(label.startswith(valid) for label, *_ in rl.RATE_ROWS))
    check("report COUNT_ROWS 全行にタグ",
          all(label.startswith(valid) for label, *_ in rl.COUNT_ROWS))
    check("NOT_CLAIMED に再分配のマクロ効果",
          any("再分配" in a for a, _b in rl.NOT_CLAIMED))


def test_variation_verdict():
    """P0-2: 来歴タグの機械検証（独立レビュー指摘のエッジを網羅）。
    verdict は『この比較で動いたか』のみ。タグ体系(出自)とは別カテゴリ・degenerate なし。"""
    V = ac.variation_verdict
    # (a) 全 None → suppressed
    check("全None→suppressed", V({"x": None, "y": None}) == "suppressed")
    # (b) 部分 None → incomparable（例外を出さない・AIR/逆進の分母抑制で頻出）
    check("部分None→incomparable(例外なし)", V({"x": None, "y": 42.0}) == "incomparable")
    # (c) A=0,B=100(最大差) → varied（degenerate で潰さない）
    check("A=0,B=100→varied(最大差を保護)", V({"x": 0.0, "y": 100.0}) == "varied")
    # (d) AIR=100%×全アーム → flat（中立注記のみ・降格でも誤爆でもない）
    check("AIR=100%×全→flat(無差別の実所見を降格しない)", V({"x": 100.0, "y": 100.0}) == "flat")
    # (e) harm_ratio=400%×全アーム → flat（>1 でも壊れない）
    check("harm_ratio=400%×全→flat(値域>1でも可)", V({"x": 400.0, "y": 400.0}) == "flat")
    # (f) 値ありアーム<2 → single（比較不能）
    check("単一アーム値→single", V({"x": 37.0}) == "single")
    # (g) 表示丸め後同値なら flat（生値のみ相違でも表と一致）
    r1 = ac._round_for_verdict(0.12344, "rate")
    r2 = ac._round_for_verdict(0.12339, "rate")
    check("表示丸め後同値→flat", V({"x": r1, "y": r2}) == "flat" and r1 == r2)
    # _tag_of 頑健性
    check("_tag_of: [E]", ac._tag_of("[E] x") == "[E]")
    check("_tag_of: 先頭空白", ac._tag_of("  [S] y") == "[S]")
    check("_tag_of: タグ無し→空", ac._tag_of("no tag") == "")
    check("_tag_of: [X]", ac._tag_of("[X] z") == "[X]")
    # [q1,q3] 共通重なり
    check("区間重なりあり→True", ac._intervals_share_overlap([(0.1, 0.5, 3), (0.4, 0.9, 3)]) is True)
    check("区間重なりなし→False", ac._intervals_share_overlap([(0.1, 0.2, 3), (0.4, 0.9, 3)]) is False)


def test_arm_display_value_suppression():
    """_arm_display_value は _fmt_cell と同じ分母抑制（<DENOM_MIN）で None を返す。"""
    runs_ok = [{"quiet_serious_answered": 0.5, "_denom_quiet_serious": 10}]
    runs_lo = [{"quiet_serious_answered": 0.5, "_denom_quiet_serious": 2}]
    check("分母十分→値", ac._arm_display_value("quiet_serious_answered", "rate",
                                          "_denom_quiet_serious", runs_ok) == 50.0)
    check("分母<MIN→None(抑制)", ac._arm_display_value("quiet_serious_answered", "rate",
                                              "_denom_quiet_serious", runs_lo) is None)


if __name__ == "__main__":
    test_analyze_basic()
    test_variation_verdict()
    test_arm_display_value_suppression()
    test_low_confidence_only_not_counted()
    test_denominator_suppression()
    test_dist_multi_seed()
    test_expand_spec()
    test_mechanism_rates()
    test_irreversible_air_and_regressivity()
    test_regressivity_backward_compat()
    test_esd_tags()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
