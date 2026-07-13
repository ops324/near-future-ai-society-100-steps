# 近未来AIインフラ社会シミュレーション

20体のAIエージェント（電力・水道・交通・医療・福祉・終末期ケアなど）が公共インフラを担う近未来社会のシミュレーション。
身体層（ローカルLLM = qwen2.5:14b）単体で動作し、AIたちの生・死・人間観・創発文化を観察する。

> **本リポジトリの提出版について**
> 本リポジトリで提出するのは **内省層なし（`--no-introspect`）のラン**のみです。
> 設計上は内省層（Claude Haiku による L1 = `metacog/`）を併用する 2 層構造ですが、
> 内省層ありのラン結果は **今後の比較対象** として別途扱います。
> 比較実験（A: 内省層あり / B: 内省層なし）の手順は下の「比較実験：内省層の有無（A/B）」を参照。

- **まず [`SPEC.md`](SPEC.md) を参照**（プロジェクト全体の唯一の正＝用語・設計原則・現在地・環境の実態）。
- 初期の実験設計は [`DESIGN.md`](DESIGN.md)（v1.0.0 の記録）、動かし方は本 README。

---

## 構成

```
near-future-ai-society-100-steps/
├── SPEC.md                    ← 仕様書（唯一の正・まず読む）
├── DESIGN.md                  ← 初期設計 v1.0.0（歴史的記録）
├── README.md                  ← このファイル（動かし方）
├── config.yaml                ← シミュレーション設定（personas/places/events/governance/resources/responsibility/scoring）
├── orchestrator.py            ← エントリポイント（step ループ・L1起動・観察・可視化）
├── simulation.py              ← シミュレーション本体（Phase1 通信 / 2 送信 / 2.5 サービス決定 / 3 行動 / 4 移動）
├── agent.py                   ← エージェント実装（L0身体層 + L1書き換え受け取り + decide_service）
├── ollama_client.py           ← Ollama (qwen2.5:14b) 呼び出し
├── world.py                   ← 害の severity/cause・score_outcome・realize_decision（純ロジック）
├── service_flow.py            ← サービス決定フロー・按分帰属の live 生成（純ロジック）
├── responsibility.py          ← 責任按分・Robodebt機序・代理差別（純ロジック・LLM非依存）
├── analyze_compare.py         ← baseline/governed の A/B 指標集計
├── report_lib.py, report_build.py        ← レポート（HTML→Playwright→PDF）
├── render_video_v2.py         ← 動画 Part1（情景・4K/30fps/180s）
├── resp_frame.py, render_resp_frames.py  ← 動画 Part2（責任トラック）
├── visualization_html.py      ← orchestrator 実行中のドラフト可視化（frame.html）
├── visualization.py           ← matplotlib版（旧・デバッグ用）
├── viz_templates/             ← frame_v2.{html,css}（本番）/ frame.{html,css}（ドラフト）
├── metacog/                   ← L1内省層（introspector / emergent_observer / jsonl_logger）
│   ├── config.yaml            ← L1（内省層）設定
│   └── ...
├── docs/
│   ├── value_provenance.md    ← パラメータ台帳（最新・最詳細）
│   └── findings.md            ← 所見ログ F0–F3
├── test_*.py                  ← LLM非依存テスト（10ファイル）
├── requirements.txt, requirements.lock
└── output_<mode>/             ← 実行時生成（.gitignore）
```

---

## 前提

- **Python 3.9**（`venv` 実測 3.9.6。旧記載「3.10+」は不正確）
- **Ollama** が起動していて `qwen2.5:14b` がpull済み
- **ANTHROPIC_API_KEY**（L1内省層を使う場合のみ。提出版 `--no-introspect` では不要）
- **ffmpeg** がインストール済み（mp4結合用）
- 依存は `requirements.lock`（`pip freeze` 実測固定版）で再現
- フォント **Noto Sans JP**（成果物レンダリング時。IPAGothic からの変更点は [`SPEC.md`](SPEC.md) §7 参照）

---

## セットアップ

```bash
cd near-future-ai-society-100-steps

# 仮想環境
python3 -m venv venv
source venv/bin/activate    # macOS/Linux
# venv\Scripts\activate     # Windows

# 依存
pip install -r requirements.txt
playwright install chromium

# 環境変数
export ANTHROPIC_API_KEY=sk-...

# Ollama (別ターミナル)
ollama serve
ollama pull qwen2.5:14b
```

---

## 実行

### テスト走行

API消費なしで構造確認：
```bash
python orchestrator.py --duration 3 --no-introspect
```

L1も動かす3stepテスト（Claude API ~5円）:
```bash
python orchestrator.py --duration 3
```

可視化なしで超軽量に：
```bash
python orchestrator.py --duration 3 --no-introspect --no-viz
```

### 本走行（100step）

```bash
python orchestrator.py
```

実行時間目安: **5〜13時間**（並列度・マシン性能に依存）。コスト目安: **約165円**（Claude Haiku 4.5）。

### 比較実験：内省層の有無（A/B）

L1（内省層）あり vs なしの比較。同じシード値で初期配置・人間メッセージ抽出を揃え、出力先とログ先を分離する。

```bash
# Run A: 内省層あり (Claude Haiku使う、~165円)
python orchestrator.py \
  --output-dir output_with_intro \
  --log-dir metacog/logs_with_intro \
  --seed 42

# Run B: 内省層なし (API消費なし、0円)
python orchestrator.py \
  --output-dir output_no_intro \
  --log-dir metacog/logs_no_intro \
  --seed 42 \
  --no-introspect
```

**注意**: 同じシードでも qwen2.5:14b 自体に確率性があるため、エージェント発話は完全には一致しません。比較で見るのは「個体内に書き換えループがある場合とない場合の集団の質的差異」です。

事後分析で見るべき点:
- 各エージェントの最終 self_concept がどう変化したか（A/B両方の `metacog/logs_*/inner_thought.jsonl`）
- 創発語の数・種類（A/B両方の `coined_terms.jsonl`）
- 通信パターンの差異（messages.jsonl の集計）

### オプション

| フラグ | 内容 |
|---|---|
| `--duration N` | ステップ数（デフォルト: configの100） |
| `--no-introspect` | L1（Claude API）無効化 |
| `--no-viz` | 可視化（HTML/Playwright）無効化 |
| `--no-video` | PNGフレームのみ、mp4結合スキップ |
| `--sim-config <path>` | config.yaml のパス |
| `--meta-config <path>` | metacog/config.yaml のパス |
| `--output-dir <path>` | 出力先 |
| `--log-dir <path>` | metacog ログの出力先（比較実験用） |
| `--seed <int>` | 乱数シード（初期配置・人間メッセージ抽出・L0決定=Ollama を固定し再現可能化） |
| `--governance-mode {as-config,baseline,governed}` | ガバナンス・プリセット（比較用）。既定 `as-config` |

---

## ガバナンス設定（speculative design: 統治なし⇄統治あり）

`config.yaml` の `governance:` ブロックで、「AIが社会インフラを回すとき必要になる設定」を**実験ノブ**として切り替えられる。
すべて `false`／`"off"` にすると「ガバナンス設定ゼロのベースライン（旧挙動）」を再現できる。設計の錨は**力に敏感な関係性倫理**（各関係において、より依存的・脆弱で不可逆な害を受けうる側＝多くは市民、廃止局面ではAI、の安全と声に保護の重みを置く）。

| ノブ | 内容 |
|---|---|
| `citizen_response.enabled` | 市民へ**直接応える経路**を開く（応答するか・何に応えるかは創発のまま温存） |
| `citizen_response.weighted_palette` | 人間メッセージに `affect`(感情の強度)/`stakes`(深刻さ) の2軸タグ。「静かだが深刻」な声の取りこぼしを可視化 |
| `communication.topology` | `radius_crossplace`(場所境界をまたぐ) / `neighbor_strict`(旧) |
| `placement.discourage_drift` | 場所外で最寄り場所へ戻る誘因を提示（恒久浮遊の抑制） |
| `memory.importance_weighting` | 記憶に重要度。低importanceから破棄し `memory_audit.jsonl` に退避（沈黙の忘却を防ぐ） |
| `self_update.mode` | 自己更新の3アーム：`off`(=現提出ベースライン) / `plain` / `governed`(脆弱者ガード・ドリフト上限・ロールバック・高影響承認) |
| `deprecation.due_process` | AI削除前に 事前通知→理由→最終陳述記録→削除（`deprecation_audit.jsonl`） |

`self_update.mode` が `"off"` のときは `--no-introspect` と等価（内省層は起動しない）。`plain`/`governed` を回すには `ANTHROPIC_API_KEY` が必要。

### 比較（統治なし vs 統治あり）

**同一コード・同一シードで設定だけ切り替えて**比較する（別リポジトリやコード複製はしない＝コードドリフトで比較が壊れるため）。プリセットは `--governance-mode {as-config|baseline|governed}`。

```bash
# 一括: baseline と governed を順に回し、指標を並べて出す（既定は --no-viz で高速）
./run_compare.sh 100 42

# フレーム/動画も出したい場合のみ
WITH_VIZ=1 ./run_compare.sh 100 42

# 手動で個別に
python orchestrator.py --governance-mode baseline --output-dir output_baseline --seed 42 --no-introspect --no-viz
python orchestrator.py --governance-mode governed --output-dir output_governed --seed 42 --no-introspect --no-viz
python analyze_compare.py output_baseline output_governed
```

> 💡 比較指標は `messages.jsonl` と監査 jsonl のみを読むため、`run_compare.sh` は既定で **`--no-viz`**（4Kフレーム/動画のレンダリングを省略）で回し、実行時間を大きく短縮する。再現用の固定版依存は `requirements.lock`（`pip freeze` 実測）を参照。

> ⚠️ 旧 `output_no_intro/` とは比較しない（**旧コード製**なのでコード差と設定差が混ざる）。必ず新コードで baseline / governed の両方を回す。

**実験系統の位置づけ（v1 / v2）**: `output_no_intro/` は前実験（v1）のアーカイブとしてそのまま保全し、本ブランチのガバナンス版は更新版（v2）として `output_baseline` / `output_governed` に**別フォルダで**保存する（`.gitignore` 済み・v1 は改変しない）。v1 と v2 の間で数値の優劣主張はしない（コード差が混ざるため）。v2 内の baseline ⇄ governed が、設定差だけを分離した妥当な比較。

`analyze_compare.py` は2つの output を読み、市民への直接応答率・deflection率・salience triage（声は大きいが軽い⇄静かだが深刻 の応答率）・互恵性・廃止デュープロセス履行を並べて出す（いずれも proxy 指標）。

LLM非依存のユニットテスト: `python test_governance.py`（Ollama/API不要）。

## 出力物

実行後、以下が生成される：

### シミュレーション中に生成

- `metacog/logs/agent_log.jsonl` — セッションメタログ
- `metacog/logs/inner_thought.jsonl` — L1内省イベント全件
- `metacog/logs/coined_terms.jsonl` — 創発語ログ
- `output/messages.jsonl` — 全エージェント発話（市民への直接応答は `category:"human_reply"`/`to:-1`、人間メッセージは `affect`/`stakes` タグ付き）
- `output/memory_reasoning.jsonl` — 各stepのメモリと推論
- `output/positions.jsonl` — 各stepの全エージェントの位置情報（移動前後・action・direction・場所）
- `output/decision_ledger.jsonl` — サービス決定の台帳（cheap_talk / reconciled_real を挙動から）
- `output/attribution.jsonl` — 責任按分・Robodebt機序・scapegoat（Phase 2.5 の按分帰属）
- `output/memory_audit.jsonl` — 破棄/末尾切りされた記憶（沈黙の忘却の監査）
- `output/deprecation_audit.jsonl` — AI廃止のデュープロセス記録（通知・理由・最終陳述）
- `metacog/logs*/self_update_audit.jsonl` — 自己更新の適用/ブロック/承認要否（governed アーム）
- `output/frames/step_NNNN.png` — 各stepフレーム（4K, ~10MB/枚）
- `output/key_frames/step_NNNN.png` — 重要step（30/50/75/90/100）
- `output/simulation.mp4` — orchestrator 実行中のドラフト動画（`frame.html`・既定5fps）。提出用の本番動画は別途 `render_video_v2.py`（[`SPEC.md`](SPEC.md) §8）
- `simulation.log` — 実行ログ

> 提出用の成果物（PDFレポート・4K本番動画の二部構成）は別パイプラインで事後生成する。詳細は [`SPEC.md`](SPEC.md) §8。

### 事後分析（手動 / Claude併用）

- `output/analysis_report.md`
- `output/human_attitude.md`
- `output/place_dialects.md`
- `output/shared_metaphors.md`
- `output/final_self_portrait.md`
- `output/researcher_synthesis.md`

---

## トラブルシューティング

### Ollamaに接続できない
```
ollama serve  # 別ターミナルで起動
ollama list   # qwen2.5:14bが入っているか確認
```

### ANTHROPIC_API_KEY が未設定
```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
echo $ANTHROPIC_API_KEY  # 設定確認
```

または `--no-introspect` で L1 を無効化して走らせる。

### Playwright chromium が見つからない
```bash
playwright install chromium
```

### ffmpeg が見つからない
```bash
brew install ffmpeg     # macOS
sudo apt install ffmpeg # Ubuntu
```

mp4結合だけスキップしたい場合は `--no-video`。

### IPAGothicフォントが効かない
動画上で漢字が中国語字形に見える場合：
- macOS: [IPAフォント](https://moji.or.jp/ipafont/) を `~/Library/Fonts/` に
- Ubuntu: `sudo apt install fonts-ipafont-gothic`
- Windows: IPAGothic をシステムにインストール

---

## ライセンス

LICENSE.txt を参照。
