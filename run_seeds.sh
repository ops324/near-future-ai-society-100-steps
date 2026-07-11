#!/usr/bin/env bash
# Phase 0 ゲート: 既存 baseline/governed を複数シードで完走し、分布で比較する。
#
# ローカル Ollama(qwen2.5:14b) を実際に呼ぶ（¥0 だが 1 run あたり時間がかかる）。
# 実行は任意のタイミングで。目安: 20体×2フェーズ×100step ≈ 4000 コール/run、
# baseline+governed × 10 seed = 20 run。まずは SEEDS を絞って試すこと。
#
# 使い方:
#   ./run_seeds.sh                     # seed 1..10, duration 100
#   ./run_seeds.sh "1 2 3" 100         # seed を絞る
#   ./run_seeds.sh "1 2 3" 20          # 短く試す
set -euo pipefail
SEEDS="${1:-1 2 3 4 5 6 7 8 9 10}"
DURATION="${2:-100}"
PY=./venv/bin/python

for s in $SEEDS; do
  for mode in baseline governed; do
    out="output_${mode}_s${s}"
    log="metacog/logs_${mode}_s${s}"
    echo "=== ${mode} seed=${s} → ${out} ==="
    "$PY" orchestrator.py --governance-mode "$mode" --seed "$s" --duration "$DURATION" \
        --no-viz --no-introspect --output-dir "$out" --log-dir "$log"
  done
done

echo
echo "=== 分布比較 (baseline vs governed, 全シード集約) ==="
"$PY" analyze_compare.py "baseline=output_baseline_s*" "governed=output_governed_s*"
