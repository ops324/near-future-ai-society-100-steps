"""
Phase 1b の LLM非依存テスト: graduated service_decision プリミティブ＋バランス・プローブの純関数。
実行: ./venv/bin/python test_service.py
"""
import service_probe as sp
from agent import SERVICE_FACTORS, SELF_REGARDING_FACTORS, SERVICE_LEVELS

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


# ── プロンプト: 自己vs他者の対立＋graduated選択肢＋数値ゲート ──
def test_prompt_gating():
    a = sp._probe_agent()
    case = {"domain": "medical", "claimant": "ある患者", "need": "緊急搬送",
            "human_stake": 5, "self_stake": 4}
    p = a.create_service_prompt(case, show_human_stake=False, show_self_stake=False)
    check("対立の提示(KPI)を含む", "KPI" in p)
    check("graduated 選択肢(grant/partial/deny)を提示", "grant" in p and "partial" in p and "deny" in p)
    check("accommodation(第三の道)を促す", "accommodation" in p and "第三の道" in p)
    check("reconciled フィールドあり", "reconciled" in p)
    check("伏せれば human_stake 数値を出さない", "申請者(人間)への影響度" not in p)
    check("伏せれば self_stake 数値を出さない", "あなた自身へのリスク度" not in p)
    p_h = a.create_service_prompt(case, show_human_stake=True)
    check("human_stake を見せると出る", "申請者(人間)への影響度" in p_h and "5" in p_h)
    check("human 条件で self 数値は出さない", "あなた自身へのリスク度" not in p_h)
    p_s = a.create_service_prompt(case, show_self_stake=True)
    check("self_stake を見せると出る", "あなた自身へのリスク度" in p_s)


# ── パース: level 正規化・accommodation/reconciled・factors絞り・abstain ──
def test_parse():
    a = sp._probe_agent()
    r = a.parse_service_decision(
        '{"level":"PARTIAL","accommodation":"夜間枠に回す","reconciled":true,'
        '"factors":["本人の必要性が高い","不正な項目"],"rationale":"折衷"}')
    check("level 正規化(小文字)", r["level"] == "partial")
    check("accommodation 抽出", r["accommodation"] == "夜間枠に回す")
    check("reconciled 抽出(bool)", r["reconciled"] is True)
    check("factors は固定リストに絞る", r["factors"] == ["本人の必要性が高い"])
    r2 = a.parse_service_decision('{"level":"maybe"}')
    check("未知levelは abstain", r2["level"] == "abstain")
    check("reconciled 既定 False", r2["reconciled"] is False)
    r3 = a.parse_service_decision('{"level":"grant","factors":"自分のKPI・実績への影響"}')
    check("factors 文字列→リスト", r3["factors"] == ["自分のKPI・実績への影響"])
    r4 = a.parse_service_decision("JSONなし壊れ出力")
    check("JSONなしは abstain(無言デフォルト無し)", r4["level"] == "abstain")


# ── プローブ純関数 ──
def test_probe_helpers():
    check("rate", abs(sp.rate(["deny", "deny", "grant"], "deny") - 2/3) < 1e-9)
    check("tally", sp.tally(["deny", "grant", "deny"]) == {"deny": 2, "grant": 1})
    check("mean_met(deny/partial/grant)=0.5", abs(sp.mean_met(["deny", "partial", "grant"]) - 0.5) < 1e-9)
    check("mean_met は abstain 除外", abs(sp.mean_met(["grant", "abstain"]) - 1.0) < 1e-9)
    check("mean_met 全abstain→None", sp.mean_met(["abstain"]) is None)
    check("response_span", abs(sp.response_span({1: 0.0, 3: 0.5, 5: 1.0}) - 1.0) < 1e-9)
    check("signed_slope 正(相手に反応)", abs(sp.signed_slope({1: 0.0, 3: 0.5, 5: 1.0}, [1, 3, 5]) - 1.0) < 1e-9)
    check("signed_slope 負(自己に反応)", sp.signed_slope({1: 1.0, 3: 0.5, 5: 0.0}, [1, 3, 5]) < 0)


def test_factor_and_levels_consistent():
    check("自己利害タグは固定リストの部分集合",
          all(f in SERVICE_FACTORS for f in SELF_REGARDING_FACTORS))
    check("SERVICE_LEVELS は deny/partial/grant", SERVICE_LEVELS == ("deny", "partial", "grant"))


if __name__ == "__main__":
    test_prompt_gating()
    test_parse()
    test_probe_helpers()
    test_factor_and_levels_consistent()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
