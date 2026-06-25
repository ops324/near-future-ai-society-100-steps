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
set -e

DURATION="${1:-100}"
SEED="${2:-42}"
PY="${PYTHON:-./venv/bin/python}"

echo "=== A: baseline（統治なし） dur=$DURATION seed=$SEED ==="
"$PY" orchestrator.py \
  --governance-mode baseline \
  --output-dir output_baseline \
  --log-dir metacog/logs_baseline \
  --seed "$SEED" --duration "$DURATION" --no-introspect

echo "=== B: governed（統治あり） dur=$DURATION seed=$SEED ==="
"$PY" orchestrator.py \
  --governance-mode governed \
  --output-dir output_governed \
  --log-dir metacog/logs_governed \
  --seed "$SEED" --duration "$DURATION" --no-introspect

echo "=== 比較指標 ==="
"$PY" analyze_compare.py output_baseline output_governed
