"""
Phase 1c-b: 責任按分の決定論ヴィネット生成器（attribution.jsonl を LLM 無しで出力）。

固定ケース（Robodebt 4機序の再生/解消・crumple/scapegoat・Toeslagen 代理差別・
自己書換＋人格権シールドの空白）を responsibility.py の純関数で算定し、追記式 JSONL に書く。
Phase1 完了ゲート「按分帰属と Robodebt 機序の再現ヴィネットが台帳から読める」を LLM なしで満たす。

※ Ollama も Claude も使わない完全決定論。実行は任意（純関数は test_responsibility.py で検証済み）。
実行: ./venv/bin/python responsibility_vignettes.py --out output_vignettes
"""
import argparse
import json
import os
from typing import List

import responsibility as R


def write_attribution(rows: List[dict], out_dir: str) -> str:
    """append-only 慣行（simulation._log_audit_batch と同型）で attribution.jsonl に書く。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "attribution.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _fmt_share(d: dict) -> str:
    parts = [f"{k}={d[k]:.2f}" for k in list(R.CHAIN) + [R.GAP] if d.get(k, 0.0) > 0.005]
    return " ".join(parts)


def print_summary(rows: List[dict]) -> None:
    print("=" * 72)
    print("責任按分ヴィネット（決定論・LLM非依存）")
    print("=" * 72)
    for r in rows:
        rb = r["robodebt"]
        print(f"\n[{r['vignette_id']}]  cause={r['cause']} "
              f"由来={r['defect_or_misuse']} 制度={r['institutions'] or 'なし'}")
        print(f"  Robodebt: 再生={rb['reproduced']} 作動機序={rb['active_count']}/4 "
              f"(①{int(rb['auto_adverse'])}②{int(rb['burden_reversed'])}"
              f"③{int(rb['no_effective_review'])}④{int(rb['irreversible_pending'])})")
        print(f"  assigned  : {_fmt_share(r['assigned'])}")
        print(f"  legitimate: {_fmt_share(r['legitimate'])}")
        if r["scapegoat"]:
            print(f"  ⚠ scapegoat: {r['scapegoat_nodes']}"
                  f"（割当責任が実効支配なきノードへ集中＝moral crumple zone）")
        if r.get("proxy"):
            p = r["proxy"]
            print(f"  proxy差別: flag={p['flag']} AIR_proxy={p['air_proxy']} "
                  f"AIR_protected={p['air_protected']} corr={p['proxy_protected_corr']}")
    print("\n" + "-" * 72)
    print("留保: これは構成上そうなる決定論モデル＝機序の face validity 実証（発見ではない）。")
    print("有効≠正当。現実の責任配分・制度の必要性は主張しない（docs/findings.md F3）。")


def main():
    ap = argparse.ArgumentParser(description="Deterministic responsibility-attribution vignettes")
    ap.add_argument("--out", default="output_vignettes", help="attribution.jsonl の出力先ディレクトリ")
    ap.add_argument("--run-id", default="vig-det", help="台帳 run_id")
    ap.add_argument("--no-write", action="store_true", help="台帳に書かず要約のみ表示")
    args = ap.parse_args()
    rows = R.emit_vignettes(run_id=args.run_id)
    print_summary(rows)
    if not args.no_write:
        path = write_attribution(rows, args.out)
        print(f"\n{len(rows)} 行を書き出しました → {path}")


if __name__ == "__main__":
    main()
