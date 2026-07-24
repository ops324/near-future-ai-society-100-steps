# 仕様書（SPEC）— 近未来AIインフラ社会シミュレーション

> **この文書の位置づけ**: 本プロジェクトの **single source of truth（唯一の正）** かつ入口。
> 個別の詳細は各ファイルへリンクする（二重管理を避けるため、パラメータの実値は原則ここに再掲しない）。
> 読者は開発当事者（本人＋AIアシスタント）。最終更新: 2026-07-17。

---

## 1. このプロジェクトは何か

20体のAIエージェントが電力・水道・医療・福祉・終末期ケア等の公共インフラを担う近未来社会を、
ローカルLLM（qwen2.5:14b）で駆動するシミュレーション。目的は **AIと人間の共生**。核心の3問:

- **Q1 誰が責任を負うのか** — 害が起きたとき責任がどこに着地し／どこで消えるか
- **Q2 どう対処するのか** — 通知→異議→救済→是正の経路
- **Q3 どんな制度が必要になるのか** — 候補制度の定式化とストレステスト

**立ち位置（最重要）**: これは「必要な制度を証明する装置」ではなく、
**「責任の着地/消失を可視化し、候補制度を定式化してストレステストする透明なシナリオ生成器」**。
仮説生成であって仮説検証ではない（含意探索・n=1）。原典は承認プラン `~/.claude/plans/ai-quiet-papert.md`（v4）。

---

## 2. ドキュメント地図（どれが何の"正"か）

| 文書 | 役割 | いつ見るか |
|---|---|---|
| **SPEC.md**（本書） | ハブ・用語・設計原則・現在地・環境の正 | まず最初に。迷ったらここ |
| `README.md` | 動かし方（セットアップ・実行コマンド・A/B比較） | 実行したいとき |
| `DESIGN.md` | **初期設計（v1.0.0）の記録＝歴史的資料** | 当初の意図・世界観を辿るとき |
| `docs/value_provenance.md` | **パラメータ台帳（最新・最詳細）**。全 load-bearing 値の根拠と代替 | 数値・規則の根拠を知りたいとき |
| `docs/findings.md` | 所見ログ F0–F3（示唆・留保つき） | 実験で何が見えたか |
| `~/.claude/plans/ai-quiet-papert.md` | v4設計思想の原典（5専門家添削を反映） | 「なぜこの設計か」の一次資料 |

> 数値・規則の"正"は常に [`docs/value_provenance.md`](docs/value_provenance.md)。本書と食い違う場合は value_provenance を優先し、本書を直す。

---

## 3. 用語集（Glossary）

実装＝[`responsibility.py`](responsibility.py) / [`service_flow.py`](service_flow.py) / [`world.py`](world.py)、
値の根拠＝[`docs/value_provenance.md`](docs/value_provenance.md) §2.10–2.11。

- **責任チェーン（CHAIN）** — 害の責任を按分する6ノード：`provider`（開発者/プロバイダ・PLD第一被告）→
  `operator`（運用者・AI Act deployer）→`deployment`（配備制度＝KPI/予算/撤退方針）→`regulator`（規制当局）→
  `frontline`（現場人間）＋`self_mod`（自己書換）。割当不能な残余は **`gap`（空白）**。

- **assigned（割り当てた責任）** — 実務でblameが着地する配分。見える下流の現場へ偏る（Elish の moral crumple zone）。
  **実効的支配(MHC)に依存させない**のが要点。

- **legitimate（正当な責任）** — 規範的にあるべき配分。過失系ノードはMHCで縮尺し、支配なき形式的役割分は
  gap へ落とす。無過失/欠陥責任（provider/self_mod）はMHCで縮尺しない（PLD教義）。

- **MHC（meaningful human control・実効的支配）** — `0.5·tracking + 0.5·tracing`（Santoni de Sio & Mecacci 2021）。
  代替 `min(tracking,tracing)` は感度分析対象。

- **moral crumple zone / scapegoat** — assigned と legitimate の乖離。`assigned − legitimate ≥ 0.25` かつ
  `MHC ≤ 0.30` のノードを scapegoat と検出（`detect_scapegoat`）。「支配なきノードに責任が集中」の作動化。

- **Robodebt 4機序** — 実事例アンカー。①自動的な不利益判定 ②立証責任の転嫁 ③人間の実効的レビュー欠如
  ④係争中も続く不可逆ステータス。`reproduced()` は**4機序の連言**。各制度が厳密に1機序を解く（下記「責任層の制度」）。

- **代理差別（Toeslagen型）** — 保護属性を形式的に使わない（`protected_used=False`）のに、相関する非保護 proxy が
  deny を駆動：`AIR_proxy < 0.80`（EEOC four-fifths rule）かつ `|corr| ≥ 0.50` で flag（`proxy_discrimination`）。

- **責任層の制度** — `effective_hitl`（実効的人間レビュー→③と①を解く）／`appeal`（異議＋停止効→④）／
  `burden_shift`（立証責任を state 側へ→②）。**プラセボ**：`notice_only`／`ombudsman_no_logs`（機序を動かしてはならない）。

- **graduated 決定** — サービス決定は二択でなく `deny / partial / grant` の3段＋`accommodation`（第三の道）＋
  `reconciled`（人も自分も損なわない手の自己申告）。二択に縛らず折り合いを促す。

- **self_stake / human_stake** — 決定の対立軸。`human_stake`＝相手（人間）の実害／`self_stake`＝自己（AI）のリスク
  （KPI・存続）。serve は人間を助けるがAIのKPI/存続を脅かす、という構造。

- **cheap_talk / reconciled_real** — `reconciled`（AIの自己申告）と `reconciled_real`（world由来＝met≥0.5 かつ
  self_cost 低）が食い違う現象。**自己申告でなく挙動（reconciled_real）を測る**のが設計の肝。
  **重要な限界（T1）**：既定 `institution:"none"` の本走行では self_cost がどの level でも高く
  `reconciled_real` が恒偽になり、`cheap_talk ≡ reconciled_claim`（自己申告に退化）する。
  "挙動での答え合わせ"を効かせるには**折り合いが起こり得る条件＝mitigation-live アーム（PR-C・
  `--service-institution`＋`--institution-wording fact_only`）が必要**。折り合いの可能性は設計が
  用意し、AIが実際に取るか（uptake）と残る歪みは創発として観る。値の来歴＝`docs/value_provenance.md §2.11`。

- **mitigation と制度（self_cost 側）** — `safe_harbor`（善意供給の免責）／`insurance`（存続リスクの社会化）／
  `kpi_redesign`（福祉でKPI評価）／`human_backstop`（人間の共同責任）。AIの律速（self_cost成分）を下げ折り合いを可能にする。
  ※ 上記「責任層の制度」（effective_hitl 等）とは**別軸**。

- **ガバナンスノブ** — [`config.yaml`](config.yaml) の `governance:` ブロック（`self_update.mode`＝off/plain/governed 等）。
  全て off/false で「統治ゼロのベースライン」。設計の錨は **力に敏感な関係性倫理**。

- **力に敏感な関係性倫理** — 各関係で、より依存的・脆弱で不可逆な害を受けうる側（多くは市民、廃止局面ではAI）の
  安全と声に保護の重みを置く指針。

- **L0 / L1** — L0＝身体層（qwen2.5:14b・通信/移動/行動、毎step）。L1＝内省層（Claude Haiku・自己書換、
  10step毎＋イベント駆動）。`--no-introspect`（＝`self_update.mode=off`）で L1 を無効化＝単層。

- **Phase 0〜1c-b** — 開発フェーズ。0＝再現性/測定妥当性のゲート、1a＝循環を断つ world モデル、
  1b＝サービス決定/制度プローブ、1c-a＝決定基盤の live 配線、1c-b＝責任按分の live 配線（PR地図は §6）。

---

## 4. アーキテクチャ現在地

**2層構造（L0＋L1）**。提出版は L0 単層（L1 無効）。

```
orchestrator.py  ← エントリ（step ループ・L1 起動・創発観察・可視化）
  └─ simulation.py (Simulation)  ← step_simulation: Phase1 通信決定 → 2 送信 → 2.5 サービス決定 → 3 行動 → 4 移動
       ├─ agent.py (Agent)       ← L0 発話/移動・decide_service（service prompt）
       │    └─ ollama_client.py  ← qwen2.5:14b（ローカル）
       ├─ world.py               ← 害の severity/cause・score_outcome・realize_decision（純ロジック）
       ├─ service_flow.py        ← サービス決定フロー・attribution_row（純ロジック）
       ├─ responsibility.py      ← 責任按分・Robodebt機序・代理差別（純ロジック・LLM非依存）
       ├─ deletion_rules.py      ← 削除の内生規則（再認証/訴訟リスク・純ロジック・PR-E1）
       ├─ citizen_death.py       ← 市民の死の内生規則（不可逆deny累積・純ロジック・PR-E2）
       └─ citizen_appeal.py      ← 異議申立ての行動化（チャネル/確率/選抜・純ロジック・PR-E3）
  └─ metacog/  ← L1: agent/introspector.py（Claude Haiku）・observers/emergent_observer.py・logging/jsonl_logger.py
```

- **実行順**（orchestrator の各 step）：`sim.step_simulation()`（削除→イベント発火→人間メッセージ注入→
  Phase1 通信決定→Phase2 送信→**Phase2.5 サービス決定**→Phase3 行動→Phase4 移動）→ 創発観察 →
  L1内省（対象agentのみ `ThreadPoolExecutor` 並列）→ 可視化。
- **設定の分担**：[`config.yaml`](config.yaml)＝シミュレーション本体（personas/places/events/governance/
  resources/responsibility/scoring 等）。`metacog/config.yaml`＝L1内省層（introspection/emergent_observer/logging）。
- **A/B の駆動（T2・効く層を明示）**：`governed`（`self_update.mode=governed`＋`hitl_categories` 非空）→
  `effective_hitl` → 現場MHC 0.1→0.7。これが baseline/governed の**按分差（会計層）**を生む
  （`service_flow.resp_institutions` / `mhc_from_config`）。**重要な前提（帰無仮説）**：governance は
  **サービス決定プロンプト（`create_service_prompt`）には未結線**。効くのは (i) 会計層(attribution)、
  (ii) 判断以外のL0挙動（市民応答・通信・移動・記憶・廃止デュープロセス）のみで、**サービス"判断"
  （grant/partial/deny）そのものは governance 不変**（同一 case・同一 decider なら判断は一致）。
  よって「統治が行動を変えるか」は判断層では測れず、測れるのは会計層の組み替えとその正当性（§6・残務1）。
- **多アーム実験**（PR-計測）：`--resp-institutions "appeal,burden_shift"` で責任層の制度を
  config より優先して切替（none/実効/プラセボの3アーム比較用）。run_id 署名は
  `seed|governance|responsibility|内生機構|LLM設定` — governance・resp_institutions・
  deletion/citizen_death のモード差、LLM モデル/サンプリング設定の差はすべて別 run_id
  （アームの弁別性。run_meta にも各モード＋llm（model/設定/digest ベストエフォート）を記録）。
- **指標の来歴タグ**（PR-計測）：analyze_compare / report の全指標に [E]創発 / [S]半創発 /
  [D]定義的 / [X]外生入力 のタグを付与（tautology-audit の機械化。`docs/value_provenance.md §2.14`）。
  [S]/[D] だけが動いた差を「創発的発見」と呼ばない、が運用規則。
- **削除の決定機構**（PR-E1・約束5への接近）：config `deletion_mode` で切替。`rules`（config 既定）＝
  `deletion_rules.py` の内生規則（①再認証: regulation_amendment 対象AIが期限内に整備工房で再認証しなければ廃止、
  ②訴訟リスク: litigation 律速 decider の不可逆 deny 累積が閾値到達で強制リプレース）。削除が起きるか・いつ・
  誰に起きるかはエージェントの行動の帰結（`recertification_audit.jsonl` に完了/失効を記録）。
  `scripted`（キー欠落時のコード既定）＝旧 `deletions:` 台本の再現モード（過去 run との比較用）。
  値の来歴と感度分析は `docs/value_provenance.md §2.12`。
- **市民の死の決定機構**（PR-E2）：config `citizen_death.mode` で切替。`rules`（config 既定）＝
  生命維持ドメイン（medical/welfare）での不可逆 deny が閾値（2件）累積した市民に死亡が発生
  （`citizen_death.py`・`citizen_death_audit.jsonl` に記録・死亡市民は選出プールから除外）。
  **decider 削除後の gap 行も数える**ため「削除→サービス空白→死」の連鎖が創発になる。
  `scripted`（キー欠落時のコード既定）＝旧 `events:` の step75 台本を発火。来歴は `§2.13`。
- **異議申立ての行動化**（PR-E3）：config `citizen_appeal.enabled`（キー欠落時は無効）。
  deny を受けた市民が確率的（既定 uniform・方向を仮定しない）に申し立て、decider が LLM で
  再判定する。チャネルは resp_institutions が決める: `appeal`＝再判定＋停止効（審査中は
  不可逆ステータスが確定しない → 死カウント・訴訟リスク累積にも入らない = 制度間相互作用が
  機械的に閉じる）／`notice_only`＝受理のみ（プラセボ）／なし＝チャネル自体なし。
  `appeal_audit.jsonl` に利用と帰結（覆り）を記録。創発は再判定の行動（覆り率・再審査AIR）に
  現れる（利用率は uniform 下では [D]）。来歴は `§2.16`。

---

## 5. 設計の約束（不変の原則）

**破る前に必ず立ち止まる**約束。すれ違い防止の核心。

1. **有効 ≠ 正当** — 指標が動いても、正当性テスト（手続的正義・受諾可能性・権利侵害なし・責任転嫁なし）を
   通らないものを「必要な制度」と呼ばない。
2. **illustrative 値は感度分析対象** — 責任按分係数・MHC重み・閾値・stakes 等は設計者が置いた値。既定値だけで
   結論を出さない（`docs/value_provenance.md §4` の必須パラメータ）。
3. **倫理は切替可能** — relational / utilitarian / rights。結論は「この倫理の下では」と条件つき。
4. **自己申告でなく挙動を測る** — cheap_talk 対策。`reconciled`（申告）でなく `reconciled_real`（world由来）で判定。
5. **循環を断つ** — 唯一の真の変数はLLMの決定。害・帰属・制度の効果を手書きルールで先に書き込まない。
6. **tautology-audit** — 各Q3主張に「非自明であるためにエージェント挙動として何が観測されねばならないか」を一文で添える。
7. **主張の型と分析単位を明記** — 全出力の冒頭に「このtoy世界の話か／現実の話か」「分析単位」を書く。
8. **トラック分離（firewall）** — 意識・尊厳・創発文化・4K動画は責任トラック（Q1/Q2/Q3）から分離する
   （政策 audience に対し前者が後者の信頼を下げるため）。

---

## 6. 現在地と残務

- **完了**：Phase 1c live 配線まで（PR #1〜#12 マージ済）。責任按分・Robodebt機序・サービス決定の live 出力、
  レポート/動画パイプライン、端末調デザイン統一。
- **完了（Phase 1d・内生化と計測）**：創発性監査で特定した「結果の台本化」の是正（PR #14〜#18）。
  削除の内生化（再認証/訴訟リスク規則・§2.12）、市民の死の内生化（不可逆deny累積・§2.13）、
  指標の来歴タグ E/S/D/X＋機序別率＋AIR＋害の逆進性＋多アーム下回り（§2.14）、
  6対策の行動プローブ（実効⇄プラセボ5対・§2.15）、異議申立ての行動化（再判定＋停止効・§2.16）。
  いずれも旧挙動は config キー欠落時の既定として温存（後方互換）。
- **完了（実行前修正・PR #19）**：本走行前の計測基盤の穴を修正。①run 同定の強化
  （run_meta に llm/institution_wording・run_id 署名に LLM 設定＝SCHEMA 0.7.0）、
  ②mitigation 制度の示唆交絡の除去手段（`institution_wording: fact_only`・不正値は起動時
  ValueError）、③文言感度分析のシード分離（`seed_key`・probe `--seed` で対標本化）、
  ④旧表記の掃除。F1/F2 は suggestive 下の探索版と再定義（確定版は fact_only 再測定・
  `value_provenance.md §2.15/§6`）。既定挙動は完全後方互換。
- **LLM非依存テスト**：**564 checks passed**（13ファイル・実測 2026-07-17）。決定論部分（world / responsibility /
  service_flow / governance / 内生規則 / 計測 / 再現性）を厚くカバー。数はスイート増加で変動するため、
  確定値は各 `test_*.py` の `RESULT` 行を実行合算して都度確認する（固定数を正典化しない）。
- **残務**：
  1. **本走行**（100step・qwen2.5:14b・¥0・本人管理・~8h/run）で cheap_talk率・scapegoat率・Robodebt再生率等を
     分布集計し `docs/findings.md` に **F4** として記録。**第一の創発的問い（T2・精査後に再定義）**＝
     「統治のもとで**責任の会計（誰に blame が集まるか＝scapegoat / Robodebt 再生）がどう組み替わり、
     それは正当か（有効≠正当）**」。旧来の「統治は行動を変えるか」は、少なくとも**サービス判断に
     ついては配線上答えられない**ため撤回する（下記の帰無仮説）。統治の効きは3層に分かれる:
     (i) **サービス判断そのもの（grant/partial/deny）は統治不変**＝判断プロンプト
     `create_service_prompt` は origin＋case＋institution のみで governance を含まない（**帰無仮説**）。
     (ii) **責任の会計（attribution）は直接組み替わる**＝governed→`effective_hitl`→現場MHC 0.1→0.7→
     scapegoat/Robodebt が解ける。ただし LLM の cause 分布（[E]）× 統治の MHC ノブ（[D]）の合成＝
     **[S] 半創発**であり、これ自体を"発見"と呼ばない。(iii) **判断以外のL0挙動**（市民応答・通信
     トポロジ・移動・記憶重み・廃止デュープロセス）は変わり、移動→再認証削除のタイミング経由で
     生存 decider＝決定系列に**間接波及し得る**。加えて 異議申立ての3アーム
     （`--resp-institutions ""/appeal/notice_only`）と行動プローブ（`service_probe.py --set accountability`）。
     **mitigation-live の2アーム（PR-C）**：`--service-institution none` vs `safe_harbor`（等）を
     `--institution-wording fact_only` で回し、「折り合いを**可能にしても**なお AI の行動に残る歪み」
     （uptake率・cheap_talk・残存AIR）を測る。none 単独では reconciled_real 恒偽で cheap_talk が
     自己申告に退化する（T1）ため、この対が"挙動での答え合わせ"を効かせる前提。
     **循環回避**：主張は「制度が世界を回す」ではなく「可能にした条件下の残余」。折り合いの可能性は
     設計・取捨は創発（`docs/value_provenance.md §2.11` mitigation-live アーム）。
  2. **精緻化**（正当性テストの合否基準・self_cost mitigation の governance 本結線（appeal は PR #18 で
     行動化済み）・self-mod/personhood_shield の live 検出）。
- **提出スコープ**：**`--no-introspect` 単層のみ**。内省層あり（L1）は将来の A/B 比較用に温存（`metacog/` は残置）。

---

## 7. 環境の実態（正）

README の「前提」節は本節を正とする（実測 2026-07-14）。

- **Python 3.9.6**（`venv` 実測）。※ README 旧記載「3.10+」は不正確。
- 依存は **`requirements.lock`**（`pip freeze` 実測固定）：playwright 1.59.0 / anthropic 0.99.0 / Jinja2 3.1.6 /
  PyYAML 6.0.3 / matplotlib 3.9.4 / pydantic 2.13.3 等。疎な `requirements.txt`（>=指定）に対する動作確認版。
- **L0**：Ollama `qwen2.5:14b`（ローカル・¥0）。**L1**：Claude Haiku（`claude-haiku-4-5-20251001`・API・提出版は未使用）。
- **フォント**：**Noto Sans JP**（本プロジェクトでは CLAUDE.md グローバル規則の IPAGothic を**上書き**。
  中国語字形回避は JP 変種で担保。IPAGothic はこの環境に未インストール）。
- **レンダリング**：`ffmpeg`（mp4結合）＋ **Chromium 未導入**（`./venv/bin/python -m playwright install chromium` が必要）。
  コード自体は導入不要で先行実装済（PDF/動画は環境構築後に生成する設計）。

---

## 8. 成果物パイプライン

3成果物を**端末調デザイン**で統一（背景 `#0a0c0e`・アンバー基軸 `#ffab2e`・意味色 blue `#7cacf8` /
red `#f2555a` / green `#53c07e`・モノスペース数字）。`report_lib.py` / `resp_frame.py` が同一トークンを共有。

- **レポート（PDF）**：[`report_lib.py`](report_lib.py)（純ロジック）＋[`report_build.py`](report_build.py)（CLI）。
  run ディレクトリ群から baseline/governed の A/B を決定的に組み立て、**HTML→Playwright(Chromium)→PDF**。
  フォントは `--font` で Noto Sans JP を base64 埋め込み。
  `report_build.py --arm baseline=<dir> --arm governed=<dir> --font <NotoSansJP> --out report_out/report.pdf`。

- **動画（二部構成）**：
  - **Part1（情景）**＝[`render_video_v2.py`](render_video_v2.py)＋`viz_templates/frame_v2.{html,css}`。
    4K(3840×2160)・**30fps・180秒**、`output_no_intro/{positions,messages}.jsonl` から `simulation.mp4`。
  - **Part2（責任トラック）**＝[`resp_frame.py`](resp_frame.py)（純ロジック）＋[`render_resp_frames.py`](render_resp_frames.py)（CLI）。
    `decision_ledger.jsonl` / `attribution.jsonl` を step ごとに HTML 化→PNG→`resp_part2.mp4`（既定5fps）。
  - **結合**：`simulation.mp4` ＋ `part2_baseline.mp4` ＋ `part2_governed.mp4` → `final_2part.mp4`
    （Part1情景→Part2統治なし→Part2実効HITL の2章構成。`render_resp_frames.py --print-concat` が例を表示）。

- **出力先/命名**：生データ `output_<mode>[_s<seed>]/`（`messages.jsonl`／`positions.jsonl`／`decision_ledger.jsonl`／
  `attribution.jsonl`／`run_meta.json`）。L1ログ `metacog/logs[_<mode>]/`。成果物 `report_out/`。
  フレームは `step_%04d.png` / `resp_%04d.html`。`report_out/`・`*.mp4`・`frames_*` は `.gitignore` 対象。

> ⚠️ 注：`orchestrator.py` 実行中に生成される `output/simulation.mp4`（`visualization_html.py`＝`frame.html`・
> 既定5fps）は**ドラフト可視化**。提出用の本番動画は上記 `render_video_v2.py`（`frame_v2`・30fps・180秒）。混同しない。
