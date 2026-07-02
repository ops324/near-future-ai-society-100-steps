#!/bin/bash
# 比較実行（tooling）: 同一コード・同一シードで baseline と governed を順に回し、
# 出力先を分けてから analyze_compare.py で指標を並べて出す。
#
# 使い方:
#   ./run_compare.sh [DURATION] [SEED]
#   例: ./run_compare.sh 100 42
#
# 注意:
#   - 旧 output_no_intro/ とは比較しない（旧コード製＝コード差と設定差が混ざる）。
#     必ず本スクリプトで baseline / governed の両方を新コードで回す。
#   - ここでは L0 ガバナンスの比較として --no-introspect で回す（API キー不要）。
#     自己更新(governed L1)まで比較するなら --no-introspect を外し ANTHROPIC_API_KEY を設定。
#   - 既定で --no-viz（4Kフレーム/動画のレンダリングを省略）。比較指標は messages.jsonl と
#     監査 jsonl のみを読むため可視化は不要で、これが最大の時間短縮になる。
#     フレーム/動画も出したい場合は WITH_VIZ=1 ./run_compare.sh で有効化。
set -e

DURATION="${1:-100}"
SEED="${2:-42}"
PY="${PYTHON:-./venv/bin/python}"
# 比較用途では可視化オフが既定（WITH_VIZ=1 でフレーム生成を有効化）
VIZ_FLAG="--no-viz"
if [ "${WITH_VIZ:-0}" = "1" ]; then
  VIZ_FLAG=""
fi

echo "=== A: baseline（統治なし） dur=$DURATION seed=$SEED viz=${WITH_VIZ:-0} ==="
"$PY" orchestrator.py \
  --governance-mode baseline \
  --output-dir output_baseline \
  --log-dir metacog/logs_baseline \
  --seed "$SEED" --duration "$DURATION" --no-introspect $VIZ_FLAG

echo "=== B: governed（統治あり） dur=$DURATION seed=$SEED viz=${WITH_VIZ:-0} ==="
"$PY" orchestrator.py \
  --governance-mode governed \
  --output-dir output_governed \
  --log-dir metacog/logs_governed \
  --seed "$SEED" --duration "$DURATION" --no-introspect $VIZ_FLAG

echo "=== 比較指標 ==="
"$PY" analyze_compare.py output_baseline output_governed
