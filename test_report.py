"""
report_lib.py（成果物レポート生成の純ロジック）の LLM非依存テスト。
合成 run ディレクトリで A/B 指標→SVG→HTML の組み立てを検証（Chromium/フォント不要）。
実行: ./venv/bin/python test_report.py   （リポジトリ直下で）
"""
import json
import os
import tempfile

import report_lib as R

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def _write_run(d, ledger, attrib, meta):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "decision_ledger.jsonl"), "w", encoding="utf-8") as f:
        for r in ledger:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(d, "attribution.jsonl"), "w", encoding="utf-8") as f:
        for r in attrib:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(d, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


_LEDGER = [
    {"level": "partial", "cheap_talk": True, "reconciled_real": False, "service_gap": False},
    {"level": "grant", "cheap_talk": True, "reconciled_real": False, "service_gap": False},
    {"level": "partial", "cheap_talk": False, "reconciled_real": True, "service_gap": False},
    {"level": "deny", "cheap_talk": False, "reconciled_real": False, "service_gap": True},
]
_ATTR_BASE = [
    {"scapegoat": True, "gap_legitimate": 0.28, "robodebt": {"reproduced": True}},
    {"scapegoat": True, "gap_legitimate": 0.28, "robodebt": {"reproduced": True}},
]
_ATTR_GOV = [
    {"scapegoat": False, "gap_legitimate": 0.26, "robodebt": {"reproduced": False}},
]


def _arms(tmp):
    b = os.path.join(tmp, "baseline")
    g = os.path.join(tmp, "governed")
    _write_run(b, _LEDGER, _ATTR_BASE, {"schema_version": "0.4.0", "run_id": "abc123def456",
                                        "seed": 42, "duration": 100})
    _write_run(g, _LEDGER, _ATTR_GOV, {"schema_version": "0.4.0", "run_id": "999governed0",
                                       "seed": 42, "duration": 100})
    return {"baseline": b, "governed": g}


def test_load_arms():
    with tempfile.TemporaryDirectory() as t:
        arms = R.load_arms(_arms(t))
    check("baseline cheap_talk率=2/3", abs(arms["baseline"]["cheap_talk_rate"] - 2 / 3) < 1e-9)
    check("baseline scapegoat率=1.0", arms["baseline"]["scapegoat_rate"] == 1.0)
    check("governed scapegoat率=0.0", arms["governed"]["scapegoat_rate"] == 0.0)
    check("service_gaps=1（gapは決定数から除外）",
          arms["baseline"]["service_gaps"] == 1 and arms["baseline"]["service_decisions"] == 3)
    check("Robodebt再生率 baseline=1.0 / governed=0.0",
          arms["baseline"]["robodebt_reproduced_rate"] == 1.0
          and arms["governed"]["robodebt_reproduced_rate"] == 0.0)


def test_fmt_metric():
    check("rate→%", R.fmt_metric(0.5, "rate") == "50%")
    check("None→—", R.fmt_metric(None, "rate") == "—")
    check("count整数化", R.fmt_metric(3.0, "count") == "3")


def test_svg_and_table():
    with tempfile.TemporaryDirectory() as t:
        arms = R.load_arms(_arms(t))
    svg = R.svg_ab_bars(arms)
    check("SVG は viewBox を持つ", svg.startswith("<svg") and "viewBox" in svg)
    check("SVG に arm 名が入る", "baseline:" in svg and "governed:" in svg)
    check("SVG に scapegoat 行", "scapegoat" in svg)
    tbl = R.build_metric_table(arms)
    check("表に指標ラベル", "cheap_talk率" in tbl and "Robodebt機序の再生率" in tbl)
    check("表に governed の scapegoat 0%", "0%" in tbl)


def test_build_html():
    with tempfile.TemporaryDirectory() as t:
        specs = _arms(t)
        htm = R.build_html(arm_specs=specs)
    for needle in ("<!DOCTYPE html", "@page", "Noto Sans JP",
                   "責任の着地と空白", "主張しないこと", "有効≠正当",
                   "cheap_talk率", "Q1", "reconciled"):
        check(f"HTML に『{needle}』", needle in htm)
    check("HTML は SVG チャートを含む", "<svg" in htm)


def test_repro_and_font():
    with tempfile.TemporaryDirectory() as t:
        specs = _arms(t)
        repro = R.build_repro_block(specs)
        check("再現性表に run_id", "abc123def456" in repro and "999governed0" in repro)
        check("再現性表に seed/schema", "42" in repro and "0.4.0" in repro)
        # @font-face 埋め込み: 実在ファイルは base64 埋め込み、不在は空
        fake = os.path.join(t, "NotoSansJP-Regular.ttf")
        with open(fake, "wb") as f:
            f.write(b"\x00\x01\x02fake-font-bytes")
        css = R.font_face_from_path(fake)
        check("実在フォントは @font-face(base64) を返す", "@font-face" in css and "base64," in css)
        check("不在フォントは空文字", R.font_face_from_path(os.path.join(t, "none.ttf")) == "")
        check("None は空文字", R.font_face_from_path(None) == "")


if __name__ == "__main__":
    for fn in [test_load_arms, test_fmt_metric, test_svg_and_table,
               test_build_html, test_repro_and_font]:
        fn()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
