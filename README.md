# 近未来AIインフラ社会シミュレーション

20体のAIエージェント（電力・水道・交通・医療・福祉・終末期ケアなど）が公共インフラを担う近未来社会のシミュレーション。
身体層（ローカルLLM = qwen2.5:14b）単体で動作し、AIたちの生・死・人間観・創発文化を観察する。

> **本リポジトリの提出版について**
> 本リポジトリで提出するのは **内省層なし（`--no-introspect`）のラン**のみです。
> 設計上は内省層（Claude Haiku による L1 = `metacog/`）を併用する 2 層構造ですが、
> 内省層ありのラン結果は **今後の比較対象** として別途扱います。
> 比較実験（A: 内省層あり / B: 内省層なし）の手順は下の「比較実験：内省層の有無（A/B）」を参照。

詳細な実験設計は [`DESIGN.md`](DESIGN.md) を参照。

---

## 構成

```
near-future-ai-society-100-steps/
├── DESIGN.md                  ← 設計文書（必読）
├── README.md                  ← このファイル
├── config.yaml                ← シミュレーション設定（場所・persona・イベント・人間メッセージ）
├── orchestrator.py            ← エントリポイント
├── simulation.py              ← シミュレーション本体
├── agent.py                   ← エージェント実装（L0身体層 + L1書き換え受け取り）
├── ollama_client.py           ← Ollama (qwen2.5:14b) 呼び出し
├── visualization.py           ← matplotlib版（簡易、デバッグ用）
├── visualization_html.py      ← HTML/CSS+Playwright版（4K本番用）
├── viz_templates/
│   ├── frame.html             ← Jinja2テンプレート
│   └── frame.css              ← ダーク寄り上品なスタイル
├── metacog/
│   ├── config.yaml            ← L1（内省層）設定
│   ├── agent/
│   │   ├── introspector.py    ← Claude Haiku呼び出し
│   │   └── prompt_template.py ← 内省プロンプト
│   ├── observers/
│   │   ├── emergent_observer.py
│   │   └── baseline_jp_10k.txt
│   ├── logging/
│   │   └── jsonl_logger.py
│   └── logs/                  ← 実行時生成
├── output/                    ← 実行時生成
│   ├── frames/
│   ├── key_frames/
│   ├── simulation.mp4
│   ├── messages.jsonl
│   └── memory_reasoning.jsonl
└── requirements.txt
```

---

## 前提

- **Python 3.10+**
- **Ollama** が起動していて `qwen2.5:14b` がpull済み
- **ANTHROPIC_API_KEY** 環境変数が設定されている
- **ffmpeg** がインストール済み（mp4結合用）
- **IPAGothic** フォントがシステムにインストール済み（macOS は brew で IPAfont 等）

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
| `--seed <int>` | 乱数シード（初期配置と人間メッセージ抽出を固定） |

---

## 出力物

実行後、以下が生成される：

### シミュレーション中に生成

- `metacog/logs/agent_log.jsonl` — セッションメタログ
- `metacog/logs/inner_thought.jsonl` — L1内省イベント全件
- `metacog/logs/coined_terms.jsonl` — 創発語ログ
- `output/messages.jsonl` — 全エージェント発話
- `output/memory_reasoning.jsonl` — 各stepのメモリと推論
- `output/positions.jsonl` — 各stepの全エージェントの位置情報（移動前後・action・direction・場所）
- `output/frames/step_NNNN.png` — 各stepフレーム（4K, ~10MB/枚）
- `output/key_frames/step_NNNN.png` — 重要step（30/50/75/90/100）
- `output/simulation.mp4` — 100step統合動画（5fps、20秒）
- `simulation.log` — 実行ログ

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
