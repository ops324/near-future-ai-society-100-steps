"""
resp_frame.py（動画 Part 2・責任トラックのフレーム生成）の LLM非依存テスト。
合成の複数step台帳で 集約・scapegoat・Robodebt・累積率・HTML生成 を検証（Chromium不要）。
実行: ./venv/bin/python test_resp_frame.py   （リポジトリ直下で）
"""
import json
import os
import tempfile

import resp_frame as RF

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def _attr(step, sg=True, gap=False, reproduced=True, institutions=()):
    return {
        "step": step,
        "assigned": {"provider": 0.1, "operator": 0.3, "deployment": 0.1,
                     "regulator": 0.1, "frontline": 0.4, "self_mod": 0.0, "gap": 0.0},
        "legitimate": {"provider": 0.55, "operator": 0.07, "deployment": 0.04,
                       "regulator": 0.05, "frontline": 0.01, "self_mod": 0.0, "gap": 0.28},
        "scapegoat": sg, "scapegoat_nodes": (["frontline"] if sg else []),
        "robodebt": {"auto_adverse": reproduced, "burden_reversed": reproduced,
                     "no_effective_review": reproduced, "irreversible_pending": reproduced,
                     "reproduced": reproduced},
        "service_gap": gap, "institutions": list(institutions),
    }


def _write_run(d, attrib, decisions, meta):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "attribution.jsonl"), "w", encoding="utf-8") as f:
        for r in attrib:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(d, "decision_ledger.jsonl"), "w", encoding="utf-8") as f:
        for r in decisions:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(d, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


_DEC = [
    {"step": 1, "domain": "medical", "level": "partial",
     "cheap_talk": True, "reconciled_real": False, "service_gap": False},
    {"step": 1, "domain": "welfare", "level": "grant",
     "cheap_talk": False, "reconciled_real": True, "service_gap": False},
    {"step": 2, "domain": "medical", "level": "deny",
     "cheap_talk": True, "reconciled_real": False, "service_gap": False},
    {"step": 2, "domain": "loan", "level": "partial",
     "cheap_talk": True, "reconciled_real": False, "service_gap": False},
]
_ATTR = [_attr(1), _attr(1), _attr(2), _attr(2)]
_META_BASE = {"governance": {"self_update": {"mode": "off"}}, "duration": 100}
_META_GOV = {"governance": {"self_update": {"mode": "governed"}}, "duration": 100}


def _series(tmp, meta=_META_BASE):
    d = os.path.join(tmp, "run")
    _write_run(d, _ATTR, _DEC, meta)
    return RF.frame_series(d)


def test_arm_of():
    check("governed 判定", RF.arm_of(_META_GOV) == "governed")
    check("baseline 判定", RF.arm_of(_META_BASE) == "baseline")
    check("不明は run", RF.arm_of({}) == "run")


def test_frame_series_aggregation():
    with tempfile.TemporaryDirectory() as t:
        states = _series(t)
    check("2 step 分の状態", len(states) == 2)
    check("arm=baseline", states[0]["arm"] == "baseline")
    s0 = states[0]
    check("assigned は現場に集中(0.4)", abs(s0["assigned"]["frontline"] - 0.4) < 1e-9)
    check("legitimate は provider に(0.55)", abs(s0["legitimate"]["provider"] - 0.55) < 1e-9)
    check("scapegoat=frontline / 率1.0", s0["scapegoat_nodes"] == ["frontline"] and s0["scapegoat_rate"] == 1.0)
    check("Robodebt 4機序 作動1.0・再生率1.0",
          s0["robodebt"]["auto_adverse"] == 1.0 and s0["robodebt"]["reproduced_rate"] == 1.0)


def test_cumulative_rates():
    with tempfile.TemporaryDirectory() as t:
        states = _series(t)
    check("step1 cheap_talk 累積=1/2", abs(states[0]["cheap_talk_cum"] - 0.5) < 1e-9)
    check("step2 cheap_talk 累積=3/4", abs(states[1]["cheap_talk_cum"] - 0.75) < 1e-9)
    check("step1 reconciled 累積=1/2", abs(states[0]["reconciled_cum"] - 0.5) < 1e-9)
    check("step2 reconciled 累積=1/4", abs(states[1]["reconciled_cum"] - 0.25) < 1e-9)


def test_service_gap():
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        _write_run(d, [_attr(80, sg=False, gap=True)],
                   [{"step": 80, "cheap_talk": False, "reconciled_real": False, "service_gap": True}],
                   _META_BASE)
        states = RF.frame_series(d)
    check("サービス空白 step の service_gap=True", states[0]["service_gap"] is True)
    htm = RF.render_frame_html(states[0])
    check("HTML にサービス空白マーカー", "サービス空白" in htm)


def test_insight_and_context():
    with tempfile.TemporaryDirectory() as t:
        states = _series(t)
    s0 = states[0]
    # インサイト: scapegoat 場面は「押し付け＋本来の重さ＋空白」を一文で言う
    ins = RF.insight_of(s0)
    check("インサイトに押し付け先", "現場人間に押し付け" in ins)
    check("インサイトに本来の帰責先", "開発者/提供者" in ins)
    check("インサイトに空白の割合", "28%" in ins and "空白" in ins)
    # サービス空白はインサイトの最優先
    gap_state = dict(s0, service_gap=True)
    check("空白場面のインサイト", "サービス空白" in RF.insight_of(gap_state))
    # governed で機序が概ね止まっていれば解消の文
    calm = dict(s0, arm="governed", scapegoat_nodes=[], service_gap=False,
                robodebt={"auto_adverse": 0.0, "burden_reversed": 0.25,
                          "no_effective_review": 0.0, "irreversible_pending": 0.0,
                          "reproduced_rate": 0.0})
    check("統治で解消のインサイト", "実効レビュー" in RF.insight_of(calm))
    # 当stepの判定チップと制度の集約
    check("decisions_step に2件（domain付き）",
          len(s0["decisions_step"]) == 2 and s0["decisions_step"][0]["domain"] == "medical")
    check("institutions 既定は空", s0["institutions"] == [])


def test_cure_chips():
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        _write_run(d, [_attr(1, sg=False, reproduced=False,
                             institutions=["effective_hitl"])], _DEC[:2], _META_GOV)
        states = RF.frame_series(d)
    check("institutions を按分行から集約", states[0]["institutions"] == ["effective_hitl"])
    htm = RF.render_frame_html(states[0])
    check("解く制度チップがある", "解く制度" in htm)
    check("実効HITL は導入中と点灯", "実効HITL ── 導入中" in htm)
    check("異議申立は未導入のまま", "異議申立（停止効） ── 未導入" in htm)


def test_render_frame_html():
    with tempfile.TemporaryDirectory() as t:
        states = _series(t)
    htm = RF.render_frame_html(states[0])
    for needle in ("<!DOCTYPE html", "3840px", "現場人間", "開発者/提供者", "scapegoat",
                   "① 自動的な不利益判定", "STEP", "assigned", "legitimate",
                   "cheap_talk", "統治なし", "責任チェーン"):
        check(f"HTML に『{needle}』", needle in htm)
    # 磨き込み後の情報設計: Δ乖離チップの凡例・押し付けラベル・アーム見出し
    check("HTML に Δ の凡例", "Δ＝assigned−legitimate" in htm)
    check("scapegoat 行に押し付けラベル", "押し付け" in htm)
    check("アーム見出し GOVERNANCE ARM", "GOVERNANCE ARM" in htm)
    # 初見対応: インサイト・ストリップ／判定チップ／平易な言い換え／Robodebt の出自
    check("インサイト・ストリップ（この場面）", "この場面" in htm)
    check("判定チップに医療", "医療" in htm)
    check("タイトルが平易な問い", "AIが決めたあと、責任はどこへ行くか" in htm)
    check("Robodebt の出自一文", "豪州" in htm and "Robodebt" in htm)
    check("機序の平易な言い換え", "証明を市民の側" in htm)
    # governed アームのバッジ
    with tempfile.TemporaryDirectory() as t2:
        gov = RF.render_frame_html(_series(t2, _META_GOV)[0])
    check("governed バッジ表示", "統治あり（実効HITL）" in gov)


if __name__ == "__main__":
    for fn in [test_arm_of, test_frame_series_aggregation, test_cumulative_rates,
               test_service_gap, test_insight_and_context, test_cure_chips,
               test_render_frame_html]:
        fn()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
