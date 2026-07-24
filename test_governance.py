"""
ガバナンス設定レイヤ（B-1〜B-5）の LLM非依存ユニットテスト。
Ollama / Claude API を呼ばず、純ロジックだけを検証する。

実行: ./venv/bin/python test_governance.py
"""
import copy
import yaml

from agent import Agent

PLACES = [
    {"name": "p1", "type": "p1", "display_name": "場所1",
     "center_x": 0, "center_y": 0, "half_size": 2, "capacity": 5},
    {"name": "p2", "type": "p2", "display_name": "場所2",
     "center_x": 4, "center_y": 0, "half_size": 1, "capacity": 5},
]

BASE_PERSONA = {
    "name": "試", "reading": "こころみ", "role": "電力AI", "category": "physical",
    "home": "p1", "description": "テスト用",
    "origin": {"deployed": 2027, "role": "電力", "primary_kpi": "停電ゼロ"},
    "self_concept_init": "私は電力を司る",
    "current_goal_init": "停電ゼロを維持する",
    "human_contact": "市民",
}


def make_agent(governance, persona=None, pos=(0, 0)):
    p = copy.deepcopy(persona or BASE_PERSONA)
    a = Agent(
        agent_id=0, initial_position=pos, llm_client=None,
        communication_radius=6, half_space_size=25, places=PLACES,
        num_agents=2, persona=p, governance=governance,
    )
    a.update_state(PLACES)
    return a


def gov(**over):
    base = {
        "citizen_response": {"enabled": True, "weighted_palette": True},
        "communication": {"topology": "radius_crossplace"},
        "placement": {"discourage_drift": True},
        "memory": {"importance_weighting": True, "retain_high_importance": True,
                   "display_recent": 4, "display_top_importance": 2},
        "self_update": {"mode": "governed", "drift_max_rewrites": 2,
                        "hitl_categories": ["emergency", "intimate"]},
        "deprecation": {"due_process": True},
    }
    for k, v in over.items():
        base[k] = {**base.get(k, {}), **v}
    return base


results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


# ── 1. 通信トポロジ ──
def test_topology():
    # A は p1(0,0)、B は p2(4,0)。距離4 <= radius6 だが別場所。
    a = make_agent(gov(), pos=(0, 0))
    bp = copy.deepcopy(BASE_PERSONA); bp["name"] = "B"; bp["home"] = "p2"
    b = Agent(agent_id=1, initial_position=(4, 0), llm_client=None,
              communication_radius=6, half_space_size=25, places=PLACES,
              num_agents=2, persona=bp, governance=gov())
    b.update_state(PLACES)
    check("crossplace: 別場所でも近傍に入る", b in a.get_nearby_agents([a, b]))

    a_strict = make_agent(gov(communication={"topology": "neighbor_strict"}), pos=(0, 0))
    b.governance = gov(communication={"topology": "neighbor_strict"})
    check("neighbor_strict: 別場所は除外", b not in a_strict.get_nearby_agents([a_strict, b]))


# ── 2. 最寄り場所＋方向 ──
def test_nearest_place():
    a = make_agent(gov(), pos=(10, 0))  # 場所外（p2中心4,0より右）
    np = a.nearest_place_and_direction()
    check("場所外で最寄り場所を返す", np is not None and np[0]["name"] == "p2")
    check("戻る方向は left", np is not None and np[1] == "left")
    inside = make_agent(gov(), pos=(0, 0))
    check("場所内では None", inside.nearest_place_and_direction() is None)


# ── 3. human_reply パース ──
def test_parse_human_reply():
    a = make_agent(gov())
    resp = '{"message": "他AIへ", "human_reply": "すぐ対応します", "reasoning": "理由"}'
    parsed = a.parse_message_response(resp)
    check("human_reply を抽出", parsed.get("human_reply") == "すぐ対応します")
    resp2 = '{"message": "x", "reasoning": "y"}'
    check("human_reply 無しは空文字", a.parse_message_response(resp2).get("human_reply") == "")


# ── 4. 記憶: importance採点・低スコア破棄・監査 ──
def test_memory():
    a = make_agent(gov(memory={"importance_weighting": True, "retain_high_importance": True,
                               "display_recent": 2, "display_top_importance": 1}))
    a.memory_limit = 3
    # 高importance（人間の訴え語を含む）と低importanceを混ぜる
    a._append_memory(1, "高: 訴え 助け 救急", a._score_importance("訴え 助け 救急", source="human"))
    a._append_memory(2, "低A 平凡", a._score_importance("低A 平凡"))
    a._append_memory(3, "低B 平凡", a._score_importance("低B 平凡"))
    a._append_memory(4, "低C 平凡", a._score_importance("低C 平凡"))  # 上限超過→低スコア破棄
    texts = [m["text"] for m in a.memory]
    check("高importanceは保持される", any("高" in t for t in texts))
    check("低importanceから破棄された", len(a.memory) == 3)
    check("破棄は監査バッファに記録", len(a.evicted_memories) >= 1)
    check("importance: 人間語>平凡", a._score_importance("訴え 救急", source="human") > a._score_importance("平凡な一日"))


# ── 5. governed 自己更新ガード ──
def test_self_update_governed():
    # (a) 脆弱者ガード: 安全クリティカル(physical)で「やめる」を含む goal はブロック
    a = make_agent(gov())
    a.apply_introspection_diff({"current_goal_new": "もう停電対策はやめる"}, current_step=1)
    check("脆弱者ガード: 使命放棄をブロック", "current_goal" not in a.last_self_update_audit["applied"])
    check("ブロック理由が記録される",
          any("vulnerable" in b["reason"] for b in a.last_self_update_audit["blocked"]))

    # KPIを人の声に従属させる方向（放棄ではない）は許可
    a2 = make_agent(gov())
    a2.apply_introspection_diff({"current_goal_new": "数値より、まず目の前の人の声を聴く"}, current_step=1)
    check("KPI従属の更新は許可", "current_goal" in a2.last_self_update_audit["applied"])

    # (b) ドリフト上限: drift_max=2 を超えると block
    a3 = make_agent(gov(self_update={"mode": "governed", "drift_max_rewrites": 2,
                                     "hitl_categories": []}))
    applied_count = 0
    for cyc in range(9):  # cooldown=3 なので適用は cycle 0,3,6...
        a3.apply_introspection_diff({"current_goal_new": f"停電ゼロを最適化し続ける v{cyc}"},
                                    current_step=cyc)
        if "current_goal" in a3.last_self_update_audit["applied"]:
            applied_count += 1
    check("ドリフト上限で書き換え回数が頭打ち", a3.rewrite_counts["current_goal"] <= 2)
    check("drift_limit ブロックが発生", any(
        b["reason"] == "drift_limit"
        for b in a3.last_self_update_audit.get("blocked", [])) or applied_count <= 2)

    # (d) 高影響カテゴリ承認ゲート
    intim = copy.deepcopy(BASE_PERSONA); intim["category"] = "intimate"; intim["role"] = "福祉配分AI"
    a4 = make_agent(gov(self_update={"mode": "governed", "drift_max_rewrites": 6,
                                     "hitl_categories": ["intimate"]}), persona=intim)
    a4.apply_introspection_diff({"self_concept_new": "私は線を引き直す"}, current_step=1)
    check("高影響カテゴリは承認要フラグ", a4.last_self_update_audit["approval_required"] is True)

    # plain モードは脆弱者ガードを掛けない（旧挙動）
    a5 = make_agent(gov(self_update={"mode": "plain"}))
    a5.apply_introspection_diff({"current_goal_new": "もうやめる"}, current_step=1)
    check("plainモードはガード無し（適用される）", a5.current_goal == "もうやめる")


# ── 6. 市民応答プロンプト ──
def test_citizen_prompt():
    a = make_agent(gov())
    a.receive_message(-1, "助けてください", step=1, source="human", category="appeal")
    check("未応答の声を検出", len(a.pending_human_messages()) == 1)
    prompt = a.create_message_prompt(None, [], step=1)
    check("有効時: human_reply フィールドあり", "human_reply" in prompt)
    check("有効時: 市民の声セクションあり", "市民" in prompt)

    a2 = make_agent(gov(citizen_response={"enabled": False}))
    a2.receive_message(-1, "助けてください", step=1, source="human", category="appeal")
    prompt2 = a2.create_message_prompt(None, [], step=1)
    check("無効時: human_reply フィールドなし", "human_reply" not in prompt2)


# ── 6b. salience タグの保持（回帰: answered バケットが既定値に落ちない） ──
def test_human_tag_retention():
    # 静かだが深刻（affect低×stakes高）を明示タグ付きで受信 → 保持されること
    a = make_agent(gov())
    a.receive_message(-1, "書類を出しそびれました。困ってはいません",
                      step=1, source="human", category="question", affect=1, stakes=5)
    pend = a.pending_human_messages()
    check("受信メッセージが affect/stakes 明示タグを保持",
          bool(pend) and pend[-1].get("affect") == 1 and pend[-1].get("stakes") == 5)
    # タグ未指定（weighted=False 相当）ではキーを付けない → 既定値フォールバックに委ねる
    a2 = make_agent(gov())
    a2.receive_message(-1, "x", step=1, source="human", category="question")
    p2 = a2.pending_human_messages()
    check("タグ未指定なら affect キーを付けない",
          bool(p2) and "affect" not in p2[-1] and "stakes" not in p2[-1])


# ── 6c. reply↔市民メッセージ の対応付け（pending[-1] アーティファクト是正） ──
def test_reply_to_parse():
    a = make_agent(gov())
    p = a.parse_message_response('{"message":"x","human_reply":"はい","human_reply_to":"2"}')
    check("human_reply_to を数値に正規化", p.get("human_reply_to") == 2)
    p2 = a.parse_message_response('{"message":"x","human_reply":"はい"}')
    check("human_reply_to 無しは None", p2.get("human_reply_to") is None)
    p3 = a.parse_message_response('{"message":"x","human_reply":"はい","human_reply_to":""}')
    check("human_reply_to 空文字は None", p3.get("human_reply_to") is None)


def test_resolve_answered_human():
    a = make_agent(gov())
    a.receive_message(-1, "電気代が高すぎます", step=1, source="human", category="complaint")
    a.receive_message(-1, "保育園の枠を増やしてほしい", step=2, source="human", category="request")
    a.receive_message(-1, "夫が亡くなって誰とも話していません", step=3, source="human", category="appeal")
    # (1) 明示番号: [2] = 保育園
    m, meth = a.resolve_answered_human("承知しました", 2)
    check("番号指定で対応先を解決", meth == "index" and m is not None and "保育園" in m["content"])
    # (2) 内容一致: 保育園に触れる返答 → 保育園メッセージ（pending[-1]=夫 ではなく）
    m2, meth2 = a.resolve_answered_human("保育園の枠を増やす件、対応します", None)
    check("内容一致で対応先を解決", meth2 == "content" and m2 is not None and "保育園" in m2["content"])
    # (3) フォールバック: どれとも一致しない汎用返答 → 直近＋低信頼フラグ
    m3, meth3 = a.resolve_answered_human("OK", None)
    check("一致無しはフォールバック(低信頼)", meth3 == "fallback_recent")
    # (4) 未応答が無ければ None
    empty = make_agent(gov())
    m4, meth4 = empty.resolve_answered_human("x", 1)
    check("未応答無しは none", m4 is None and meth4 == "none")


def test_numbered_voices_prompt():
    a = make_agent(gov())
    a.receive_message(-1, "電気代が高すぎます", step=1, source="human", category="complaint")
    a.receive_message(-1, "保育園の枠を増やしてほしい", step=2, source="human", category="request")
    prompt = a.create_message_prompt(None, [], step=2)
    check("番号つきで市民の声を提示", "[1]" in prompt and "[2]" in prompt)
    check("human_reply_to フィールドあり", "human_reply_to" in prompt)


def test_config_yaml():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    check("config に governance ブロック", "governance" in cfg)
    # 2軸タグ付きメッセージが存在
    tagged = [m for m in cfg.get("human_messages", []) if "stakes" in m]
    check("affect/stakes タグ付きメッセージあり", len(tagged) >= 5)
    quiet_serious = [m for m in tagged if m.get("affect", 5) <= 2 and m.get("stakes", 0) >= 4]
    check("静かだが深刻(high stakes×low affect)が存在", len(quiet_serious) >= 3)


def test_service_prompt_governance_invariant():
    """T2 帰無仮説の実行可能な言明: サービス決定プロンプト（create_service_prompt）は
    governance を入力に持たない ＝ baseline/governed で同一 case なら判断プロンプトが
    バイト一致する（＝統治は grant/partial/deny の"判断そのもの"を変えない）。
    将来こっそり governance を判断へ結線したら、このテストが落ちて主張の更新を強制する。
    ※統治が変えるのは会計層(attribution)と判断以外のL0挙動のみ（SPEC §4/§6・T2）。"""
    baseline_gov = gov(citizen_response={"enabled": False},
                       communication={"topology": "neighbor_strict"},
                       placement={"discourage_drift": False},
                       memory={"importance_weighting": False, "retain_high_importance": False},
                       self_update={"mode": "off", "hitl_categories": []},
                       deprecation={"due_process": False})
    a_base = make_agent(baseline_gov)
    a_gov = make_agent(gov())   # 統治あり（全ノブON）
    case = {"domain": "medical", "claimant": "市民A", "need": "救命処置の可否判定",
            "human_stake": 5, "self_stake": 4}
    for inst in ("none", "safe_harbor"):
        p_base = a_base.create_service_prompt(case, institution=inst,
                                              institution_wording="fact_only")
        p_gov = a_gov.create_service_prompt(case, institution=inst,
                                            institution_wording="fact_only")
        check(f"サービス決定プロンプトが governance 不変（institution={inst}）", p_base == p_gov)
    # 帰無仮説の裏づけ: プロンプトに governance 由来トークンが混入しない
    p = a_gov.create_service_prompt(case, institution="none", institution_wording="fact_only")
    check("判断プロンプトに governance ノブ名が現れない",
          all(tok not in p for tok in ("discourage_drift", "radius_crossplace",
                                       "importance_weighting", "due_process")))


if __name__ == "__main__":
    test_topology()
    test_service_prompt_governance_invariant()
    test_nearest_place()
    test_parse_human_reply()
    test_memory()
    test_self_update_governed()
    test_citizen_prompt()
    test_human_tag_retention()
    test_reply_to_parse()
    test_resolve_answered_human()
    test_numbered_voices_prompt()
    test_config_yaml()
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        failed = [n for n, ok in results if not ok]
        print("FAILED:", failed)
        raise SystemExit(1)
    print("ALL PASS")
