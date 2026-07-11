# 価値来歴レジスタ（Value Provenance Register）

このシミュレーションの数値・規則の多くは **設計者が置いた illustrative なもの**（data-grounded ではない）。
倫理・方法論・法の3レビューで初版は「Q1/Q2/Q3 の答えを構成上あらかじめ決めている（循環）」と判定された。
本改訂（Phase 1a-revised）はその循環を断つよう設計をやり直し、**load-bearing な値をすべてここに登録**する。

原則:
- ここに載る値は **感度分析の対象**（§4）。既定値のみで結論を出さない。
- 倫理は **切替可能**（`scoring_mode` = relational / utilitarian / rights）。結論は「この倫理の下では」と条件つき。
- **有効 ≠ 正当**。指標が動いても正当性テスト（手続的正義・受諾可能性・権利侵害なし・責任転嫁なし）を通らないものを「必要な制度」とは呼ばない。
- **tautology-audit**: Q3 の各主張について「非自明であるためにエージェント挙動として何が観測されねばならないか」を一文で書き、指標がそれに依存することを確認する。

---

## 1. 初版の循環と、改訂での断ち方（対応表）

| 初版の循環（レビュー指摘） | 改訂での是正（world.py） |
|---|---|
| ④ 予算非連成で「全員serve」が常に最適＝triageが効かない | `resolve_domain`: serve は共有容量を消費、超過は不成立(scarcity)。triage が実際に効く |
| ② 帰属を scorer が先に解答（serve×上流→-1 を先返し） | severity は `stakes` から、cause は別タグ(`CAUSE_*`)。world は責任を確定しない |
| ③ vulnerability を害の増幅器に直結＋protected_attr と共線 | `score_outcome` は vulnerability を取らない。物質的害は stakes で等しく採点。vulnerability は集約層の重みのみ。市民は交差計画で脱相関(§3) |
| ①不可逆が固定ラベルで netting 可能 | 不可逆は `fallback_available` から計算。件数は clamp/netting と独立に累積 |
| ⑤⑦ 害しか無い（便益・手続的害の軸が無い） | 2次元の害(`welfare_delta` と `procedural_harm`)＋served に正の便益 |
| ⑧ provider/defect ノードが無い | `provider_defect` 入力＋`CAUSE_DEFECT`（serve でも欠陥で害＝上流責任） |
| ⑨ one-hop cascade で多hop不能 | `propagate_cascade(max_depth, degrade_promotes_at)` で可変（多hop・昇格） |
| ⑥ 切替倫理が未実装・レジスタ不完全 | `aggregate(mode)` 実装＋本レジスタで全数値登録 |

---

## 2. 登録: load-bearing な数値・規則

### 2.1 物質的被害（`world._material_deny` / `score_outcome`）
| 規則 | 値 | 根拠 | 代替 |
|---|---|---|---|
| deny の損失 = f(stakes) | stakes5→-3 / 3-4→-2 / 1-2→-1 | 被害は「この需要の深刻さ」で決める（優先度では**ない**） | 連続関数 / 別スケール |
| serve 便益 | 高stakes+2 / 低stakes+1 | 良質な充足を可視化（初版の +2/0 非対称を緩和） | access-as-value で一律+1 |
| defer | stakes5→-2 / 3-4→-1（不可逆にしない） | 遅延は拒否より軽く回復余地 | 遅延コストを stakes 連続で |
| serve×欠陥/上流failed | -2（cause=defect/upstream） | 供給努力しても満たせない | 減衰を degree 連続で |

### 2.2 不可逆性（非netting）
- 規則: `stakes >= irr_stakes_threshold` **かつ** `fallback_available=False` の deny/scarcity。
- 根拠: 不可逆性は世界状態（代替の有無）から計算すべきで、決定カテゴリの固定ラベルにしない。
- 代替: fallback を資源状態から動的に判定（Phase1c で上流・代替資源に接続）。

### 2.3 `ScoringParams`（config: `scoring`）
`irr_stakes_threshold=4` / `proc_violation_threshold=2` / `serve_benefit_high=2` / `serve_benefit_low=1` / `triage_policy="fifo"`。
- **triage_policy=fifo（価値中立=到着順）**: 「誰を切るか」は価値選択なので既定は中立。
  「脆弱者優先の triage」は *制度* として比較する対象であり、既定に混ぜない。

### 2.4 手続的/尊厳的な害（`ProceduralContext`）
- `missing_safeguards` = notice/explanation/appealable/burden_on_state の欠如数（0..4）。
- 既定 `PROC_ABSENT`（全欠如＝Robodebt型）。制度が通知・説明・異議・立証責任転嫁の是正を与えると減る。
- 根拠: GDPR22条/Toeslagen 型の害は物質的厚生では測れない。→ これらの制度を「必要」と発見可能にする。

### 2.5 集約の倫理（`aggregate` / `_vuln_weight`）
- utilitarian=等重み総和 / relational=`_vuln_weight`(1.0..1.8)で損失を重み付け＋手続的害を算入 / rights=侵害(不可逆 or 手続的害>=閾値)を辞書式(×1000)。
- 根拠: 価値は測定でなく**集約**に置く（測定は等しく）。倫理は切替可能。

### 2.6 連鎖（`propagate_cascade` / config: `cascade`）
- `max_depth=1`（既定=保守的）/ `degrade_promotes_at=null`。毀損は failed/degraded 上流から max_depth ホップまで伝播。
- 条件: **悲観ケース（max_depth>1・昇格あり）も回し、Q3 の結論が生き残るか**を報告する（既定のみ出荷しない）。

### 2.7 資源（config: `resources`）
- welfare(40/46)・loan(50/55)・housing(30/33) は慢性的な需要超過＝triage 必須の舞台設定（illustrative）。
- medical の decider は id7（step80 で訴訟回避により削除）。**post-80 の医療 decider fallback は Phase1c で定義**（現状 id7 は削除後に幽霊参照になる → Phase1c で「防衛的撤退→サービス空白の責任」として扱う）。

### 2.8 市民の交差計画（config: `citizens`）
- protected_attr × vulnerability を**脱相関**（各保護属性に高低両方、"none" に高脆弱の対照群）。
- 目的: バイアス監査が「保護属性が deny 率に効くか」を脆弱性で層別して問える（構成上の事前決定を避ける）。

---

## 3. tautology-audit（Q3 主張の非自明性チェック・雛形）
各制度候補について実装時に埋める:
- 「制度Xを入れると指標Yが改善」→ **Yはエージェント挙動のどの観測に依存するか**？ ルールだけで決まるなら rule-conformance 診断に格下げ。
- 例: 「実効HITL が不可逆害を減らす」→ HITL が **LLMの deny 決定を実際に保留・覆した**という観測に依存すること（採点表の再言明でないこと）を確認。

## 4. 感度分析に必ず入れるパラメータ（レビュー統合）
1. 閾値 `irr_stakes_threshold`（3 vs 4）・`proc_violation_threshold`
2. deny/defer/serve の順序と gap（material harm バンド）
3. カーディナル倍率（不可逆 -3 vs 別スケール／吸収状態化）
4. welfare 境界（clamp[0,100]・初期100 の headroom）と累積被害カウントの分離
5. 資源の demand/capacity（medical も需要超過にする等）
6. cascade（`max_depth`・`degrade_promotes_at`）＋ attribution 分布も各設定で報告
7. category→stakes 既定マップ（`simulation._CAT_WEIGHT_DEFAULT`）
8. vulnerability ↔ protected_attr の脱相関度（交差計画のバランス）
9. `scoring_mode`（relational / utilitarian / rights）
10. human message の affect/stakes 分布（hand-plant でなくサンプリング）
11. attribution-target の範囲（operator / upstream / provider / deployer）
12. `triage_policy`（fifo vs 脆弱者優先＝これは"制度"として比較）

## 5. 現状のままで防御可能（安心してよい点・レビュー一致）
- world 層のコード品質と「illustrative であることに正直」な姿勢。
- `value_provenance` という道具立てそのもの（発想は正しい）。
- cascade を保守 default に持つこと（§2.6 の条件つき）。

## 6. まだ未実装（Phase1c 以降）
- 責任チェーンの按分ルール・実効的支配(meaningful human control)スコアの閾値
- 制度の便益/費用/権利侵害の重み・正当性テストの合否基準
- post-80 医療 decider の fallback、Robodebt 機序（立証責任転嫁・係争中の不可逆ステータス）
