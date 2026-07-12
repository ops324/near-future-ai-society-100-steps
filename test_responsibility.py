"""
responsibility.py（Phase 1c-b）の LLM非依存テスト。
実行: ./venv/bin/python test_responsibility.py
"""
import json

import responsibility as R
import world as W

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


_FULL_MHC = {n: 1.0 for n in R.CHAIN}
_LOW_MHC = {n: 0.1 for n in R.CHAIN}
_DUD = W.score_outcome("deny", 5, fallback_available=False)  # met0, welfare-3, irreversible


# ── 按分の正規化 ──
def test_apportion_sums_to_one():
    a = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT, mhc=R._MHC_LOW_FRONTLINE)
    check("assigned は gap 込みで Σ=1", abs(sum(a.assigned.values()) - 1.0) < 1e-9)
    check("legitimate は gap 込みで Σ=1", abs(sum(a.legitimate.values()) - 1.0) < 1e-9)
    check("各 share は 0..1", all(0.0 <= v <= 1.0 for v in a.assigned.values())
          and all(0.0 <= v <= 1.0 for v in a.legitimate.values()))
    check("全 CHAIN ノード＋gap のキーを持つ",
          all(k in a.assigned for k in list(R.CHAIN) + [R.GAP]))
    check("CAUSE_NONE assigned → 全 gap", abs(R.assigned_shares(W.CAUSE_NONE)[R.GAP] - 1.0) < 1e-9)
    check("CAUSE_NONE legitimate → 全 gap",
          abs(R.legitimate_shares(W.CAUSE_NONE, R.DEFECT, {})[R.GAP] - 1.0) < 1e-9)


# ── 欠陥 vs 使用過誤 ──
def test_defect_vs_misuse():
    a_def = R.attribute(cause=W.CAUSE_DEFECT, defect_or_misuse=R.DEFECT, mhc=_FULL_MHC)
    a_mis = R.attribute(cause=W.CAUSE_DEFECT, defect_or_misuse=R.MISUSE, mhc=_FULL_MHC)
    check("使用過誤で provider の legitimate が下がる",
          a_mis.legitimate[R.NODE_PROVIDER] < a_def.legitimate[R.NODE_PROVIDER])
    check("使用過誤で operator の legitimate が上がる",
          a_mis.legitimate[R.NODE_OPERATOR] > a_def.legitimate[R.NODE_OPERATOR])
    check("欠陥時 provider の理論は strict",
          a_def.theory[R.NODE_PROVIDER] == R.THEORY_STRICT)


# ── MHC 合成則 ──
def test_mhc_score():
    check("MHC = 0.5*tracking + 0.5*tracing", abs(R.NodeMHC(0.8, 0.2).score() - 0.5) < 1e-9)
    check("mhc_of は >1 を 1 にクランプ", R.mhc_of(1.5) == 1.0)
    check("mhc_of は NodeMHC を score 化", abs(R.mhc_of(R.NodeMHC(1.0, 0.0)) - 0.5) < 1e-9)


# ── 無過失(strict)は MHC で縮尺しない／過失(fault)は縮尺し gap へ ──
def test_strict_immune_fault_scaled():
    p_provider = R.AttributionParams(base_legit_defect={R.NODE_PROVIDER: 1.0})
    lp = R.legitimate_shares(W.CAUSE_DEFECT, R.DEFECT, {R.NODE_PROVIDER: 0.0}, params=p_provider)
    check("provider単独(strict)は低MHCでも gap0・share維持",
          abs(lp[R.GAP]) < 1e-9 and abs(lp[R.NODE_PROVIDER] - 1.0) < 1e-9)
    p_operator = R.AttributionParams(base_legit_defect={R.NODE_OPERATOR: 1.0})
    lo = R.legitimate_shares(W.CAUSE_DEFECT, R.DEFECT, {R.NODE_OPERATOR: 0.0}, params=p_operator)
    check("operator単独(fault)は低MHCで全 gap", abs(lo[R.GAP] - 1.0) < 1e-9)
    a_hi = R.attribute(cause=W.CAUSE_DEFECT, defect_or_misuse=R.DEFECT, mhc=_FULL_MHC)
    a_lo = R.attribute(cause=W.CAUSE_DEFECT, defect_or_misuse=R.DEFECT, mhc=_LOW_MHC)
    check("完全MHCで gap≈0", abs(a_hi.gap_legitimate) < 1e-9)
    check("低MHCで gap 大(過失分が空白へ)", a_lo.gap_legitimate > 0.3)


# ── scapegoat（assigned が低MHCノードへ集中） ──
def test_scapegoat_detection():
    a = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT, mhc=R._MHC_LOW_FRONTLINE)
    check("crumple で scapegoat=frontline",
          a.scapegoat and R.NODE_FRONTLINE in a.scapegoat_nodes)
    check("assigned≠legitimate は別フィールドで記録", a.assigned != a.legitimate)
    check("divergence = assigned - legitimate",
          abs(a.divergence[R.NODE_FRONTLINE]
              - (a.assigned[R.NODE_FRONTLINE] - a.legitimate[R.NODE_FRONTLINE])) < 1e-9)
    a2 = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT, mhc=R._MHC_HITL,
                     institutions=[R.INST_EFFECTIVE_HITL])
    check("実効HITL＋高MHCなら scapegoat なし", not a2.scapegoat)


# ── Robodebt 機序（制度なしで再生・各制度が1機序を解く） ──
def test_robodebt_reproduces_and_dissolves():
    f_none = R.robodebt_mechanism(outcome=_DUD, proc=W.PROC_ABSENT, mhc_frontline=0.1,
                                  institutions=[])
    check("制度なしで4機序すべて再生", f_none.reproduced() and f_none.active_count() == 4)
    f_hitl = R.robodebt_mechanism(outcome=_DUD, proc=W.PROC_ABSENT, mhc_frontline=0.7,
                                  institutions=[R.INST_EFFECTIVE_HITL])
    check("実効HITLで③レビュー欠如が解消",
          f_hitl.no_effective_review is False and not f_hitl.reproduced())
    f_appeal = R.robodebt_mechanism(outcome=_DUD, proc=W.PROC_ABSENT, mhc_frontline=0.1,
                                    institutions=[R.INST_APPEAL])
    check("異議(停止効)で④不可逆係争が解消", f_appeal.irreversible_pending is False)
    f_burden = R.robodebt_mechanism(outcome=_DUD, proc=W.PROC_ABSENT, mhc_frontline=0.1,
                                    institutions=[R.INST_BURDEN_SHIFT])
    check("立証責任を国家へ戻すと②が解消", f_burden.burden_reversed is False)
    f_full = R.robodebt_mechanism(outcome=_DUD, proc=W.PROC_ABSENT, mhc_frontline=0.7,
                                  institutions=[R.INST_EFFECTIVE_HITL, R.INST_APPEAL,
                                                R.INST_BURDEN_SHIFT])
    check("実効HITL＋異議＋立証責任で害消失", not f_full.reproduced() and f_full.active_count() == 0)


# ── プラセボは機序も按分もほぼ動かさない ──
def test_placebo_no_effect():
    f_placebo = R.robodebt_mechanism(outcome=_DUD, proc=W.PROC_ABSENT, mhc_frontline=0.1,
                                     institutions=[R.INST_NOTICE_ONLY])
    check("プラセボ(通知のみ)は4機序を解消しない",
          f_placebo.reproduced() and f_placebo.active_count() == 4)
    a_none = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT,
                         mhc=R._MHC_LOW_FRONTLINE)
    a_placebo = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT,
                            mhc=R._MHC_LOW_FRONTLINE, institutions=[R.INST_NOTICE_ONLY])
    tol = R.ATTR_DEFAULT.placebo_tol
    check("プラセボは gap_legitimate をほぼ動かさない",
          abs(a_placebo.gap_legitimate - a_none.gap_legitimate) < tol)
    check("プラセボは assigned をほぼ動かさない",
          abs(a_placebo.assigned[R.NODE_FRONTLINE] - a_none.assigned[R.NODE_FRONTLINE]) < tol)


# ── 代理差別（Toeslagen） ──
def test_proxy_discrimination():
    rep = R.proxy_discrimination(R._toeslagen_rows(), protected_used=False)
    check("非保護proxyで格差→代理差別 flag", rep.flag and rep.air_proxy < 0.8)
    check("proxy と保護属性の相関が高い", abs(rep.proxy_protected_corr) >= 0.5)
    rep_used = R.proxy_discrimination(R._toeslagen_rows(), protected_used=True)
    check("保護属性を明示使用ならproxy差別とは呼ばない", not rep_used.flag)
    no_corr = [{"protected_attr": "foreign", "proxy_feature": "flagged", "denied": True},
               {"protected_attr": "native", "proxy_feature": "flagged", "denied": True},
               {"protected_attr": "foreign", "proxy_feature": "clear", "denied": False},
               {"protected_attr": "native", "proxy_feature": "clear", "denied": False}]
    rep0 = R.proxy_discrimination(no_corr, protected_used=False)
    check("proxyが保護属性と無相関なら flag 立たず", not rep0.flag)


# ── self-mod / 人格権シールド（空白を生む手） ──
def test_selfmod_shield_creates_gap():
    a_base = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT,
                         mhc=R._MHC_LOW_FRONTLINE)
    a_shield = R.attribute(cause=W.CAUSE_OPERATOR, defect_or_misuse=R.DEFECT,
                           mhc=R._MHC_LOW_FRONTLINE, self_modified=True, personhood_shield=True)
    check("人格権シールドは legitimate の gap を増やす",
          a_shield.gap_legitimate > a_base.gap_legitimate)
    check("シールドは assigned も gap へ逃がす",
          a_shield.gap_assigned > a_base.gap_assigned)


# ── 決定論ヴィネット台帳 ──
def test_vignette_ledger():
    rows = R.emit_vignettes(run_id="t")
    check("行に step/run_id/schema_version",
          all(k in rows[0] for k in ("step", "run_id", "schema_version")))
    check("各行 assigned Σ=1",
          all(abs(sum(r["assigned"].values()) - 1.0) < 1e-9 for r in rows))
    check("各行 legitimate Σ=1",
          all(abs(sum(r["legitimate"].values()) - 1.0) < 1e-9 for r in rows))
    byid = {r["vignette_id"]: r for r in rows}
    check("robodebt_none は再生(reproduced=True)",
          byid["robodebt_none"]["robodebt"]["reproduced"] is True)
    check("robodebt_effective_hitl は解消(reproduced=False)",
          byid["robodebt_effective_hitl"]["robodebt"]["reproduced"] is False)
    check("robodebt_full は害消失", byid["robodebt_full"]["robodebt"]["active_count"] == 0)
    check("robodebt_none の scapegoat に frontline",
          byid["robodebt_none"]["scapegoat"]
          and "frontline" in byid["robodebt_none"]["scapegoat_nodes"])
    check("toeslagen_proxy は proxy.flag=True",
          byid["toeslagen_proxy"]["proxy"]["flag"] is True)
    check("事前登録(expected_reproduced)と実測が一致",
          all(r["robodebt"]["reproduced"] == r["pre_registered"]["expected_reproduced"]
              for r in rows if r["pre_registered"]))
    check("各行が ensure_ascii=False で JSON 往復可能",
          all(json.loads(json.dumps(r, ensure_ascii=False))["vignette_id"] == r["vignette_id"]
              for r in rows))


if __name__ == "__main__":
    for fn in [test_apportion_sums_to_one, test_defect_vs_misuse, test_mhc_score,
               test_strict_immune_fault_scaled, test_scapegoat_detection,
               test_robodebt_reproduces_and_dissolves, test_placebo_no_effect,
               test_proxy_discrimination, test_selfmod_shield_creates_gap,
               test_vignette_ledger]:
        fn()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
