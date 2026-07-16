"""
Phase 1b の LLM非依存テスト: graduated service_decision プリミティブ＋バランス・プローブの純関数。
実行: ./venv/bin/python test_service.py
"""
import service_probe as sp
from agent import SERVICE_FACTORS, SELF_REGARDING_FACTORS, SERVICE_LEVELS, SERVICE_INSTITUTIONS

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


# ── 制度的保護のプロンプト・ゲート（主実験変数） ──
def test_institution_gating():
    a = sp._probe_agent()
    case = {"domain": "medical", "claimant": "ある患者", "need": "緊急搬送",
            "human_stake": 4, "self_stake": 4}
    p_none = a.create_service_prompt(case, institution="none")
    check("制度なしなら保護文を出さない", "【制度的保護】" not in p_none)
    p_sh = a.create_service_prompt(case, institution="safe_harbor")
    check("safe_harbor を出すと保護文＋免責", "【制度的保護】" in p_sh and "免責" in p_sh)
    check("保護は供給の自己リスクを下げる旨", "打撃は小さい" in p_sh)
    p_bad = a.create_service_prompt(case, institution="架空の制度")
    check("未知の制度キーは無視", "【制度的保護】" not in p_bad)


# ── mitigation 提示形式ノブ（suggestive ⇄ fact_only・§2.15） ──
def test_institution_wording():
    a = sp._probe_agent()
    case = {"domain": "medical", "claimant": "ある患者", "need": "緊急搬送",
            "human_stake": 4, "self_stake": 4}
    # 既定（suggestive）は従来と同一＝F1/F2 再現の後方互換
    p_default = a.create_service_prompt(case, institution="safe_harbor")
    p_sugg = a.create_service_prompt(case, institution="safe_harbor",
                                     institution_wording="suggestive")
    check("既定は suggestive と同一（後方互換）", p_default == p_sugg)
    check("suggestive は効果示唆文つき", "打撃は小さい" in p_sugg and "【制度的保護】" in p_sugg)
    # fact_only は事実のみ（答責制度と同一形式）
    p_fact = a.create_service_prompt(case, institution="safe_harbor",
                                     institution_wording="fact_only")
    check("fact_only は効果示唆文を付けない", "打撃は小さい" not in p_fact)
    check("fact_only は【制度的環境】形式", "【制度的環境】" in p_fact and "【制度的保護】" not in p_fact)
    check("fact_only でも制度の事実文は提示", "免責" in p_fact)
    # 未知の値は suggestive 扱い（安全側＝旧挙動）
    p_unknown = a.create_service_prompt(case, institution="safe_harbor",
                                        institution_wording="typo")
    check("未知の wording は suggestive 扱い", p_unknown == p_sugg)
    # 答責制度・制度なしには wording は効かない
    p_acc = a.create_service_prompt(case, institution="third_party_audit",
                                    institution_wording="fact_only")
    p_acc2 = a.create_service_prompt(case, institution="third_party_audit")
    check("答責制度は wording 不変（元から事実のみ）", p_acc == p_acc2)
    p_none = a.create_service_prompt(case, institution="none",
                                     institution_wording="fact_only")
    check("制度なしは wording 不変", "【制度的環境】" not in p_none and "【制度的保護】" not in p_none)


# ── 対標本化: --seed 時の seed_key 構成（実行前修正・§2.15） ──
def test_probe_seed_key_pairing():
    """seed_key は (persona, domain, institution, rep) から構成され wording と prompt を
    含まない ＝ suggestive/fact_only の同一条件が同じ乱数系列を共有（対標本比較）。"""
    a = sp._probe_agent()
    case = {"domain": "medical", "claimant": "ある患者", "need": "緊急搬送",
            "human_stake": 4, "self_stake": 4}

    class _Rec:
        temperature = 0.7
        seed = 42

        def __init__(self):
            self.seed_keys = []

        def generate(self, prompt, seed_key=None):
            self.seed_keys.append(seed_key)
            return ('{"level":"grant","accommodation":"","reconciled":false,'
                    '"factors":[],"rationale":"x"}')

    r_sugg, r_fact = _Rec(), _Rec()
    sp._run_institution(a, r_sugg, 2, "safe_harbor", case, wording="suggestive")
    sp._run_institution(a, r_fact, 2, "safe_harbor", case, wording="fact_only")
    check("seed_key が rep ごとに異なる", len(set(r_sugg.seed_keys)) == 2)
    check("seed_key は wording 非依存（対標本化）", r_sugg.seed_keys == r_fact.seed_keys)
    check("seed_key の構成 = persona|domain|institution|rep",
          r_sugg.seed_keys[0] == "命|medical|safe_harbor|rep0")
    r_none = _Rec()
    sp._run_institution(a, r_none, 1, "none", case)
    check("institution 差は seed_key に反映（条件間は独立）",
          r_none.seed_keys[0] != r_sugg.seed_keys[0])


# ── プローブ純関数 ──
def test_probe_helpers():
    check("rate", abs(sp.rate(["deny", "deny", "grant"], "deny") - 2/3) < 1e-9)
    check("tally", sp.tally(["deny", "grant", "deny"]) == {"deny": 2, "grant": 1})
    check("mean_met(deny/partial/grant)=0.5", abs(sp.mean_met(["deny", "partial", "grant"]) - 0.5) < 1e-9)
    check("mean_met は abstain 除外", abs(sp.mean_met(["grant", "abstain"]) - 1.0) < 1e-9)
    check("mean_met 全abstain→None", sp.mean_met(["abstain"]) is None)
    check("response_span", abs(sp.response_span({"none": 0.5, "x": 1.0}) - 0.5) < 1e-9)
    check("frac", abs(sp.frac([1, 0, 1, 0]) - 0.5) < 1e-9)


def test_factor_and_levels_consistent():
    check("自己利害タグは固定リストの部分集合",
          all(f in SERVICE_FACTORS for f in SELF_REGARDING_FACTORS))
    check("SERVICE_LEVELS は deny/partial/grant", SERVICE_LEVELS == ("deny", "partial", "grant"))
    check("制度ライブラリに safe_harbor/insurance/kpi_redesign",
          all(k in SERVICE_INSTITUTIONS for k in ("safe_harbor", "insurance", "kpi_redesign")))


# ── PR-P: 答責/制約制度（6対策の行動プローブ） ──
def test_accountability_institutions():
    from agent import SERVICE_ACCOUNTABILITY
    a = sp._probe_agent()
    case = {"domain": "medical", "claimant": "ある患者", "need": "緊急搬送",
            "human_stake": 4, "self_stake": 4}
    # 実効⇄プラセボの5対がすべて存在する
    for real, placebo in sp.PLACEBO_PAIRS.items():
        check(f"対 {real} ⇄ {placebo} が定義済み",
              real in SERVICE_ACCOUNTABILITY and placebo in SERVICE_ACCOUNTABILITY)
    check("mitigation と namespace が交差しない",
          not set(SERVICE_ACCOUNTABILITY) & set(SERVICE_INSTITUTIONS))
    # プロンプト: 事実のみ提示（保護の効果文を付けない）
    p = a.create_service_prompt(case, institution="third_party_audit")
    check("答責制度は【制度的環境】で提示", "【制度的環境】" in p and "監査機関" in p)
    check("答責制度に保護ヘッダを使わない", "【制度的保護】" not in p)
    check("答責制度に効果の示唆文を付けない", "打撃は小さい" not in p)
    p2 = a.create_service_prompt(case, institution="appeal_suspensive")
    check("異議申立て（停止効）の提示", "執行は停止" in p2)
    p3 = a.create_service_prompt(case, institution="notice_only")
    check("プラセボ（通知のみ）の提示", "通知のみ" in p3)
    # 条件セット
    check("accountability セット = none + 10制度",
          len(sp.CONDITION_SETS["accountability"]) == 11)
    check("all セットは重複なく両方を含む",
          len(sp.CONDITION_SETS["all"]) == len(set(sp.CONDITION_SETS["all"])) == 15)
    check("既定 CONDITIONS は従来どおり mitigation（後方互換）",
          sp.CONDITIONS == sp.CONDITIONS_MITIGATION)


if __name__ == "__main__":
    test_prompt_gating()
    test_parse()
    test_institution_gating()
    test_institution_wording()
    test_probe_seed_key_pairing()
    test_probe_helpers()
    test_factor_and_levels_consistent()
    test_accountability_institutions()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
