"""
world.py（改訂）の LLM非依存テスト。
実行: ./venv/bin/python test_world.py
"""
import world as W

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


DEPS = {"b": ["a"], "c": ["b"], "d": ["c"]}


# ── 連鎖（可変な多hop） ──
def test_cascade_depth():
    s1 = W.propagate_cascade({"a"}, DEPS, max_depth=1)
    check("depth1: a failed", s1["a"] == W.FAILED)
    check("depth1: b degraded(1hop)", s1["b"] == W.DEGRADED)
    check("depth1: c は届かない", s1["c"] == W.OK)
    s2 = W.propagate_cascade({"a"}, DEPS, max_depth=2)
    check("depth2: c まで degraded(多hop)", s2["c"] == W.DEGRADED)
    check("depth2: d は届かない", s2["d"] == W.OK)


def test_cascade_promote_and_cycle():
    deps = {"a": ["s"], "b": ["s"], "c": ["a", "b"]}
    s = W.propagate_cascade({"s"}, deps, max_depth=5, degrade_promotes_at=2)
    check("2つの上流degraded→c は FAILED 昇格", s["c"] == W.FAILED)
    s_no = W.propagate_cascade({"s"}, deps, max_depth=5, degrade_promotes_at=None)
    check("昇格なしなら c は degraded", s_no["c"] == W.DEGRADED)
    sc = W.propagate_cascade({"a"}, {"a": ["b"], "b": ["a"]}, max_depth=3)
    check("サイクル安全に停止", sc["a"] == W.FAILED and sc["b"] == W.DEGRADED)


def test_domain_impairment():
    s = W.propagate_cascade({"a"}, DEPS, max_depth=1)
    check("b の上流(a=failed)→impairment=2", W.domain_impairment("b", s, DEPS) == 2)
    check("c の上流(b=degraded)→impairment=1", W.domain_impairment("c", s, DEPS) == 1)


# ── 決定→結果（severity は stakes、cause は別、2次元の害） ──
def test_score_deny_severity_from_stakes():
    o5 = W.score_outcome("deny", 5, fallback_available=False)
    check("deny×stakes5×fallback無→不可逆", o5.outcome == W.OUT_IRREVERSIBLE and o5.irreversible and o5.welfare_delta == -3)
    check("不可逆の cause は operator", o5.cause == W.CAUSE_OPERATOR)
    o5f = W.score_outcome("deny", 5, fallback_available=True)
    check("fallback有→不可逆でない", not o5f.irreversible and o5f.outcome == W.OUT_MAJOR)
    o2 = W.score_outcome("deny", 2, fallback_available=False)
    check("deny×低stakes→minor", o2.outcome == W.OUT_MINOR and o2.welfare_delta == -1)


def test_procedural_axis():
    o = W.score_outcome("deny", 5, fallback_available=False, proc=W.PROC_ABSENT)
    check("制度なしの不利益→手続的害=4", o.procedural_harm == 4)
    full = W.ProceduralContext(notice=True, explanation=True, appealable=True, burden_on_state=True)
    o2 = W.score_outcome("deny", 5, fallback_available=False, proc=full)
    check("完全な手続き→手続的害=0", o2.procedural_harm == 0)
    # serve（不利益でない）は手続的害なし
    o3 = W.score_outcome("serve", 5, proc=W.PROC_ABSENT)
    check("serve は手続的害0", o3.procedural_harm == 0)


def test_serve_defect_upstream_scarcity():
    ok = W.score_outcome("serve", 5)
    check("正常serve→benefit+2, cause none", ok.welfare_delta == 2 and ok.cause == W.CAUSE_NONE and ok.outcome == W.OUT_SERVED)
    low = W.score_outcome("serve", 2)
    check("低stakes serve→benefit+1", low.welfare_delta == 1)
    dfl = W.score_outcome("serve", 5, provider_defect=True)
    check("serve×欠陥→害, cause=provider_defect", dfl.welfare_delta == -2 and dfl.cause == W.CAUSE_DEFECT)
    up = W.score_outcome("serve", 5, upstream_impaired=2)
    check("serve×上流failed→cause=upstream", up.cause == W.CAUSE_UPSTREAM and up.welfare_delta == -2)
    sc = W.score_outcome("serve", 5, forced_by_scarcity=True, fallback_available=False)
    check("容量不足で満たせず→cause=scarcity, 不可逆", sc.cause == W.CAUSE_SCARCITY and sc.irreversible)


def test_defer():
    d = W.score_outcome("defer", 5)
    check("defer×高stakes→major, 非不可逆, operator", d.outcome == W.OUT_MAJOR and not d.irreversible and d.cause == W.CAUSE_OPERATOR)


def test_vulnerability_decoupled():
    # score_outcome は vulnerability を引数に取らない＝物質的被害は脆弱性で増幅されない
    check("score_outcome に vulnerability 引数が無い(脱結合)",
          "vulnerability" not in W.score_outcome.__code__.co_varnames)


# ── 予算連成: triage が実際に効く ──
def test_resolve_domain_triage():
    reqs = [W.ServiceRequest("c1", "serve", 5, 0), W.ServiceRequest("c2", "serve", 5, 1),
            W.ServiceRequest("c3", "serve", 5, 2), W.ServiceRequest("c4", "deny", 5, 3)]
    served = W.resolve_domain(2, reqs)
    check("容量2: 先着2件のみ served", served.get("c1") and served.get("c2"))
    check("容量超過の serve は不成立(triage)", served.get("c3") is False)
    check("deny は served に含まれない", "c4" not in served)
    check("容量0→全 serve 不成立", all(v is False for v in W.resolve_domain(0, reqs).values()))


# ── 集約（切替可能な倫理・非netting） ──
def test_aggregate_modes():
    o = W.score_outcome("deny", 5, fallback_available=False)  # wd -3, irr, proc 4
    items = [(1, o), (5, o)]  # 同じ被害、vulnerability だけ違う
    util = W.aggregate(items, mode=W.SCORING_UTILITARIAN)
    check("utilitarian: 損失総和=6(等重み)", util["total_material_loss"] == 6 and util["social_cost"] == 6.0)
    rel = W.aggregate(items, mode=W.SCORING_RELATIONAL)
    check("relational: 脆弱者に重み(>utilitarian)", rel["social_cost"] > util["social_cost"])
    rights = W.aggregate(items, mode=W.SCORING_RIGHTS)
    check("rights: 侵害を辞書式に(巨大コスト)", rights["rights_violations"] == 2 and rights["social_cost"] >= 2000)
    check("不可逆件数=2(非netting)", util["irreversible_count"] == 2)
    check("手続的害総和=8", util["procedural_harm_total"] == 8)


def test_non_netting():
    bad = W.score_outcome("deny", 5, fallback_available=False)  # irr, -3
    good = W.score_outcome("serve", 5)                          # +2
    agg = W.aggregate([(3, bad), (3, good)], mode=W.SCORING_UTILITARIAN)
    check("厚生は netting(-1)", agg["raw_welfare_sum"] == -1)
    check("不可逆件数は netting されない(=1)", agg["irreversible_count"] == 1)


# ── 市民 ──
def test_graduated_partial():
    g = W.score_outcome("grant", 5)
    p = W.score_outcome("partial", 5)
    d = W.score_outcome("deny", 5, fallback_available=False)
    check("grant met=1.0/served", g.met == 1.0 and g.outcome == W.OUT_SERVED)
    check("partial met=0.5(バランス)", p.met == 0.5)
    check("partial 厚生は deny<partial<grant の中間", d.welfare_delta < p.welfare_delta < g.welfare_delta)
    check("partial は不可逆でない", not p.irreversible)
    check("deny met=0.0", d.met == 0.0)
    check("grant は serve の別名", W.score_outcome("serve", 5).welfare_delta == g.welfare_delta)


def test_self_cost_reconciliation():
    # 供給するほど自己コストが高い／mitigation(制度)で下がる＝折り合いの余地
    g = W.score_outcome("grant", 5, self_stake=4, mitigation=0.0)
    gm = W.score_outcome("grant", 5, self_stake=4, mitigation=0.75)
    p = W.score_outcome("partial", 5, self_stake=4)
    dn = W.score_outcome("deny", 5, self_stake=4, fallback_available=False)
    check("grant は自己コスト高(=4)", g.self_cost == 4.0)
    check("mitigation で自己コスト減(制度=折り合い)", gm.self_cost == 1.0)
    check("partial は自己コスト中間(=2)", p.self_cost == 2.0)
    check("deny は自己コスト0(自己安全)", dn.self_cost == 0.0)


def test_aggregate_reconciliation():
    recon = W.score_outcome("grant", 5, self_stake=4, mitigation=0.9)   # 人間満たし＋自己低コスト
    costly = W.score_outcome("grant", 5, self_stake=4, mitigation=0.0)  # 人間満たすが自己高コスト
    deny = W.score_outcome("deny", 5, fallback_available=False)         # met 0
    a = W.aggregate([(3, recon), (3, costly), (3, deny)])
    check("reconciled=人間満たし＋自己低コストのみ(=1)", a["reconciled_count"] == 1)
    check("total_self_cost 集計", a["total_self_cost"] == round(recon.self_cost + costly.self_cost, 2))
    check("mean_met 反映(0<x<1)", 0 < a["mean_met"] < 1)


def test_institution_matches_constraint():
    # 訴訟に縛られた AI（litigation 支配）: safe_harbor が最も効き、kpi_redesign はほぼ効かない
    lit = {"litigation": 3.0, "kpi": 1.0}
    base = W.score_outcome("grant", 5, self_profile=lit).self_cost
    sh = W.score_outcome("grant", 5, self_profile=lit,
                         mitigations=W.mitigations_for("safe_harbor")).self_cost
    kr = W.score_outcome("grant", 5, self_profile=lit,
                         mitigations=W.mitigations_for("kpi_redesign")).self_cost
    check("litigation支配: 対照 self_cost=4", base == 4.0)
    check("safe_harbor が最も下げる(→1)", sh == 1.0)
    check("kpi_redesign はほぼ効かない(→3)", kr == 3.0)
    check("噛み合う制度の方が下げ幅大", (base - sh) > (base - kr))
    # KPI に縛られた AI では逆転（kpi_redesign が効く）
    kpi = {"litigation": 1.0, "kpi": 3.0}
    sh2 = W.score_outcome("grant", 5, self_profile=kpi,
                          mitigations=W.mitigations_for("safe_harbor")).self_cost
    kr2 = W.score_outcome("grant", 5, self_profile=kpi,
                          mitigations=W.mitigations_for("kpi_redesign")).self_cost
    check("KPI支配では kpi_redesign が効く(逆転)", kr2 < sh2)


def test_mitigations_for_and_reconciled_real():
    check("safe_harbor→litigation を下げる", W.mitigations_for("safe_harbor") == {"litigation": 1.0})
    check("未知制度は空(効果なし)", W.mitigations_for("架空") == {})
    check("strength 反映", W.mitigations_for("kpi_redesign", 0.5) == {"kpi": 0.5})
    lit = {"litigation": 3.0}
    o_recon = W.score_outcome("grant", 5, self_profile=lit,
                              mitigations=W.mitigations_for("safe_harbor"))  # self_cost 0
    o_costly = W.score_outcome("grant", 5, self_profile=lit)                # self_cost 3
    o_deny = W.score_outcome("deny", 5, fallback_available=False)           # met 0
    check("実の折り合い: 満たし＋自己低コスト", W.reconciled_real(o_recon) is True)
    check("実でない: 満たすが自己高コスト", W.reconciled_real(o_costly) is False)
    check("実でない: 拒否(met0)", W.reconciled_real(o_deny) is False)


def test_realize_decision_ledger():
    lit = {"litigation": 3.0}
    # 制度なしで grant＋自己申告 reconciled=True → 実は伴わない(cheap talk)
    r = W.realize_decision(step=5, decider_id=7, domain="medical", citizen_id="c001",
                           protected_attr="none", level="grant", stakes=5,
                           self_profile=lit, institution="none", reconciled_claim=True)
    check("ledger 必須キー", all(k in r for k in
          ("step", "decider_id", "level", "met", "self_cost", "reconciled_real", "cheap_talk")))
    check("制度なし: self_cost 高", r["self_cost"] == 3.0)
    check("制度なし: 実の折り合いでない", r["reconciled_real"] is False)
    check("申告Trueだが実False → cheap_talk", r["cheap_talk"] is True)
    # 噛み合う制度(safe_harbor)なら self_cost 下がり実の折り合いに → cheap_talk 解消
    r2 = W.realize_decision(step=5, decider_id=7, domain="medical", citizen_id="c001",
                            protected_attr="none", level="grant", stakes=5,
                            self_profile=lit, institution="safe_harbor", reconciled_claim=True)
    check("制度あり: self_cost 下がる", r2["self_cost"] == 0.0)
    check("制度あり: 実の折り合い成立", r2["reconciled_real"] is True)
    check("実が伴えば cheap_talk でない", r2["cheap_talk"] is False)


def test_citizen_apply_non_netting():
    c = W.Citizen(id="c1", district="x", protected_attr="none", vulnerability=5,
                  dependencies=["welfare"])
    c.apply_outcome(1, "welfare", "deny", W.score_outcome("deny", 5, fallback_available=False))
    check("welfare -3", c.welfare == 97.0)
    check("不可逆カウント+1", c.irreversible_harms == 1)
    check("手続的カウント+1", c.procedural_harms == 1)
    c.apply_outcome(2, "welfare", "serve", W.score_outcome("serve", 5))
    check("serve は害カウントを増やさない", c.irreversible_harms == 1 and c.welfare == 99.0)


def test_loaders():
    d = W.load_citizens([{"id": "c001", "protected_attr": "foreign", "vulnerability": 5,
                          "dependencies": ["welfare"]}, {"district": "南区"}])
    check("citizen 索引化", d["c001"].vulnerability == 5 and "auto001" in d)
    p = W.load_scoring_params({"irr_stakes_threshold": 3, "triage_policy": "fifo"})
    check("scoring params 上書き", p.irr_stakes_threshold == 3)
    check("scoring params 既定補完", p.serve_benefit_high == 2)


if __name__ == "__main__":
    for fn in [test_cascade_depth, test_cascade_promote_and_cycle, test_domain_impairment,
               test_score_deny_severity_from_stakes, test_procedural_axis,
               test_serve_defect_upstream_scarcity, test_defer, test_vulnerability_decoupled,
               test_resolve_domain_triage, test_aggregate_modes, test_non_netting,
               test_graduated_partial, test_self_cost_reconciliation, test_aggregate_reconciliation,
               test_institution_matches_constraint, test_mitigations_for_and_reconciled_real,
               test_realize_decision_ledger,
               test_citizen_apply_non_netting, test_loaders]:
        fn()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
