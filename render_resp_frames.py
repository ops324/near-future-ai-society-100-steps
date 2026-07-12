"""
動画 Part 2（責任トラック）の描画 CLI（各 step の HTML → Playwright(Chromium) で PNG → ffmpeg で mp4）。

  ./venv/bin/python render_resp_frames.py --run output_governed_s1 \
      --out-dir frames_resp --mp4 resp_part2.mp4 --fps 5

純ロジックは resp_frame.py（test_resp_frame.py で検証）。実 PNG 化には Chromium が必要:
  ./venv/bin/python -m playwright install chromium   （環境構築）
ffmpeg は導入済みを想定。--no-render で HTML フレームのみ書き出す（Chromium 不要）。

二部構成: Part1（情景=既存 render_video_v2）と Part2（本スクリプト）の mp4 を同解像度で作り、
env 構築後に ffmpeg concat で1本化する（例は本スクリプトの --print-concat で表示）。
"""
import argparse
import os
import shutil
import subprocess
import sys

import resp_frame as RF


def write_html_frames(states, out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for st in states:
        p = os.path.join(out_dir, f"resp_{st['step']:04d}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(RF.render_frame_html(st))
        paths.append(p)
    return paths


def render_pngs(states, out_dir: str):
    """各 step を Chromium でスクリーンショット。Chromium 未整備なら None＋案内。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("playwright 未インストール: ./venv/bin/pip install playwright")
        return None
    os.makedirs(out_dir, exist_ok=True)
    pngs = []
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:
                print("Chromium 未インストール（環境構築）: "
                      "./venv/bin/python -m playwright install chromium")
                print("  詳細:", str(e).splitlines()[0][:160])
                return None
            ctx = browser.new_context(viewport={"width": RF.WIDTH, "height": RF.HEIGHT},
                                      device_scale_factor=1)
            page = ctx.new_page()
            for st in states:
                page.set_content(RF.render_frame_html(st), wait_until="domcontentloaded")
                out = os.path.join(out_dir, f"resp_{st['step']:04d}.png")
                page.screenshot(path=out, full_page=False)
                pngs.append(out)
            browser.close()
        return pngs
    except Exception as e:
        print("PNG 生成に失敗:", str(e)[:200])
        return None


def encode_mp4(png_dir: str, mp4_path: str, fps: int) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg 未検出: brew install ffmpeg")
        return False
    cmd = [ffmpeg, "-y", "-framerate", str(fps),
           "-pattern_type", "glob", "-i", os.path.join(png_dir, "resp_*.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "slow", mp4_path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("ffmpeg 失敗:\n", res.stderr[-800:])
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="責任トラック動画(Part2)の描画")
    ap.add_argument("--run", required=True, help="run ディレクトリ（decision_ledger/attribution を含む）")
    ap.add_argument("--arm", default=None, help="アーム名（既定は run_meta から判定）")
    ap.add_argument("--out-dir", default="frames_resp", help="フレーム出力ディレクトリ")
    ap.add_argument("--mp4", default="resp_part2.mp4", help="出力 mp4")
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--no-render", action="store_true", help="HTML フレームのみ（Chromium 不要）")
    ap.add_argument("--print-concat", action="store_true",
                    help="Part1 と結合する ffmpeg コマンド例を表示")
    args = ap.parse_args()

    states = RF.frame_series(args.run, arm=args.arm)
    if not states:
        raise SystemExit(f"{args.run} に台帳(attribution/decision_ledger)がありません。"
                         "本走行または短走行で生成してください。")
    print(f"フレーム状態: {len(states)} step（arm={states[0]['arm']}）")

    if args.no_render:
        paths = write_html_frames(states, args.out_dir)
        print(f"HTML フレーム {len(paths)} 枚 → {args.out_dir}")
        return

    pngs = render_pngs(states, args.out_dir)
    if pngs is None:
        write_html_frames(states, args.out_dir)
        print("PNG は未生成（HTML は出力済み）。環境構築後に再実行してください。")
        sys.exit(2)
    print(f"PNG {len(pngs)} 枚 → {args.out_dir}")
    if encode_mp4(args.out_dir, args.mp4, args.fps):
        print(f"mp4 を生成しました → {args.mp4}")
    if args.print_concat:
        print("\n# Part1(情景) と Part2(責任) を結合する例（同解像度前提）:")
        print("printf \"file '%s'\\nfile '%s'\\n\" simulation.mp4 " + args.mp4
              + " > concat.txt && ffmpeg -f concat -safe 0 -i concat.txt -c copy final_2part.mp4")


if __name__ == "__main__":
    main()
