"""
成果物レポートのビルド CLI（HTML 組立 → Playwright(Chromium) で A4 PDF 化）。

  ./venv/bin/python report_build.py \
      --arm "baseline=output_baseline_s1" --arm "governed=output_governed_s1" \
      --font report_assets/NotoSansJP-Regular.ttf --out report.pdf

※ 純ロジックは report_lib.py（test_report.py で検証）。実 PDF 化には環境構築が必要:
  - Chromium: ./venv/bin/python -m playwright install chromium
  - フォント: Noto Sans JP (.ttf/.otf) を --font で指定（未指定なら font-family チェーンへ fallback）
HTML の書き出しはフォント/Chromium 不要（--no-render で HTML だけ出せる）。
"""
import argparse
import os
import sys

import report_lib as R


def parse_arms(arm_args):
    specs = {}
    for a in arm_args or []:
        if "=" not in a:
            raise SystemExit(f"--arm は name=dir 形式: {a!r}")
        name, d = a.split("=", 1)
        specs[name.strip()] = d.strip()
    return specs


def render_pdf(html_path: str, pdf_path: str) -> bool:
    """Playwright(Chromium) で HTML → A4 PDF。Chromium 未整備なら False＋案内。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("playwright 未インストール: ./venv/bin/pip install playwright")
        return False
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:
                print("Chromium 未インストール（環境構築）: "
                      "./venv/bin/python -m playwright install chromium")
                print("  詳細:", str(e).splitlines()[0][:160])
                return False
            page = browser.new_page()
            page.goto("file://" + os.path.abspath(html_path))
            page.pdf(path=pdf_path, format="A4", print_background=True,
                     prefer_css_page_size=True)
            browser.close()
        return True
    except Exception as e:
        print("PDF 生成に失敗:", str(e)[:200])
        return False


def main():
    ap = argparse.ArgumentParser(description="成果物レポート(A/B)の HTML→PDF ビルド")
    ap.add_argument("--arm", action="append", metavar="name=dir",
                    help="比較アーム（例: baseline=output_baseline_s1）。複数指定可")
    ap.add_argument("--font", default=None, help="Noto Sans JP の .ttf/.otf パス（任意・埋め込み）")
    ap.add_argument("--out", default="report.pdf", help="出力 PDF パス")
    ap.add_argument("--html-out", default=None, help="中間 HTML の出力先（既定は <out>.html）")
    ap.add_argument("--no-render", action="store_true", help="HTML のみ出力（Chromium 不要）")
    args = ap.parse_args()

    arm_specs = parse_arms(args.arm)
    if not arm_specs:
        raise SystemExit("--arm を1つ以上指定してください（例: --arm baseline=output_smoke_b）")

    font_css = R.font_face_from_path(args.font)
    if args.font and not font_css:
        print(f"注意: フォント {args.font} が見つからず font-family チェーンにフォールバックします。")

    html = R.build_html(arm_specs=arm_specs, font_face_css=font_css)
    html_path = args.html_out or (os.path.splitext(args.out)[0] + ".html")
    os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML を書き出しました → {html_path}")

    if args.no_render:
        return
    if render_pdf(html_path, args.out):
        print(f"PDF を生成しました → {args.out}")
    else:
        print("PDF は未生成（HTML は出力済み）。環境構築後に再実行してください。")
        sys.exit(2)


if __name__ == "__main__":
    main()
