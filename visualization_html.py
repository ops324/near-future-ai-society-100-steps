"""
HTML/CSS + Playwright + ffmpeg 路線のビジュアライザ (Round 4 本番用)
4K (3840x2160) で各ステップを HTML レンダリング → PNG キャプチャ → mp4 結合。

使い方:
    from visualization_html import HTMLVisualizer
    viz = HTMLVisualizer(places, num_agents, half_space_size, output_dir="output")
    # 毎step:
    viz.render_step(step, sim_state)  # PNGを output/frames/step_NNNN.png に保存
    # 終了時:
    viz.compose_video()  # output/simulation.mp4 を生成

依存:
    pip install playwright jinja2
    playwright install chromium
"""
import json
import os
import subprocess
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    Environment = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

logger = logging.getLogger(__name__)

# 4K解像度
DEFAULT_WIDTH = 3840
DEFAULT_HEIGHT = 2160
LEFT_PANEL_WIDTH = 2400  # 左パネル幅 (px) — frame.cssの grid-template-columns と一致
WORLD_PADDING = 100      # left-panel padding-left (px)


class HTMLVisualizer:
    def __init__(
        self,
        places: List[Dict],
        agents_meta: List[Dict],
        half_space_size: int,
        output_dir: str = "output",
        templates_dir: str = "viz_templates",
        resolution: Tuple[int, int] = (DEFAULT_WIDTH, DEFAULT_HEIGHT),
        fps: int = 5,
    ):
        if Environment is None:
            raise ImportError("jinja2 が必要です: pip install jinja2")
        if sync_playwright is None:
            raise ImportError("playwright が必要です: pip install playwright && playwright install chromium")

        self.places = places
        self.agents_meta = agents_meta  # [{"id":i, "name":"雷光", "category":"physical"}, ...]
        self.half_space_size = half_space_size
        self.output_dir = output_dir
        self.frames_dir = os.path.join(output_dir, "frames")
        self.key_frames_dir = os.path.join(output_dir, "key_frames")
        self.templates_dir = templates_dir
        self.resolution = resolution
        self.fps = fps

        os.makedirs(self.frames_dir, exist_ok=True)
        os.makedirs(self.key_frames_dir, exist_ok=True)

        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )

        # Playwright sessionは render_step ごとに起動するとオーバーヘッドが大きいので
        # コンテキストマネージャで保持する
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

        # 過去の通信線を年齢別に持つ（最近3step分）
        self.comm_history: List[List[Dict]] = []

    # ─────── Playwright session ───────

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            viewport={"width": self.resolution[0], "height": self.resolution[1]},
            device_scale_factor=1,
        )
        self._page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._page is not None:
            self._page.close()
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    # ─────── 座標変換 ───────

    def _world_to_pct(self, x: int, y: int) -> Tuple[float, float]:
        """ワールド座標 (-half..+half, -half..+half) を world-stage 内のパーセンテージに変換。
        画面では Y が下向きなので反転する。"""
        size = self.half_space_size * 2
        left = ((x + self.half_space_size) / size) * 100.0
        # 画面上は y が大きいほど下なので反転
        top = (1.0 - (y + self.half_space_size) / size) * 100.0
        return left, top

    def _place_box(self, place: Dict) -> Dict[str, float]:
        cx, cy = place["center_x"], place["center_y"]
        hs = place["half_size"]
        x0, y0 = cx - hs, cy + hs   # top-left of bounding box (画面上は y+ が上)
        x1, y1 = cx + hs, cy - hs   # bottom-right
        l0, t0 = self._world_to_pct(x0, y0)
        l1, t1 = self._world_to_pct(x1, y1)
        return {
            "left_pct": l0,
            "top_pct": t0,
            "width_pct": l1 - l0,
            "height_pct": t1 - t0,
        }

    # ─────── データ準備 ───────

    def _prepare_places(self, place_status: Dict) -> List[Dict]:
        out = []
        for p in self.places:
            box = self._place_box(p)
            ps = place_status.get("places", {}).get(p["name"], {})
            out.append({
                "name": p["name"],
                "display_name": p.get("display_name", p["name"]),
                "category": p.get("category", "physical"),
                "agents_in_place": ps.get("agents_in_place", 0),
                "capacity": p.get("capacity", 0),
                **box,
            })
        return out

    def _prepare_agents(self, agents_state: List[Dict]) -> List[Dict]:
        out = []
        for ag in agents_state:
            l, t = self._world_to_pct(ag["position"][0], ag["position"][1])
            out.append({
                "id": ag["id"],
                "name": ag["name"],
                "category": ag.get("category", "physical"),
                "left_pct": l,
                "top_pct": t,
                "has_event": ag.get("has_event", False),
            })
        return out

    def _prepare_comm_lines(self, current_messages: List[Dict], agents_state: List[Dict]) -> List[Dict]:
        """直近3step分の通信線を age=0/1/2 で重ね描き"""
        # 現在のstepの通信を comm_history に追加
        agent_pos = {a["id"]: (a["position"][0], a["position"][1]) for a in agents_state}
        current_lines = []
        for msg in current_messages:
            from_id = msg.get("from", -1)
            to_id = msg.get("to", -1)
            if from_id < 0 or to_id < 0:
                continue
            if from_id not in agent_pos or to_id not in agent_pos:
                continue
            x1, y1 = agent_pos[from_id]
            x2, y2 = agent_pos[to_id]
            l1, t1 = self._world_to_pct(x1, y1)
            l2, t2 = self._world_to_pct(x2, y2)
            current_lines.append({"x1": l1, "y1": t1, "x2": l2, "y2": t2})

        self.comm_history.append(current_lines)
        if len(self.comm_history) > 3:
            self.comm_history.pop(0)

        # 古い順に age を割り当て (最新が age=0)
        out = []
        n = len(self.comm_history)
        for idx, batch in enumerate(self.comm_history):
            age = (n - 1 - idx)  # 最後に追加されたものが age=0
            for ln in batch:
                out.append({**ln, "age": age})
        return out

    def _prepare_active_events(self, active_events: List[Dict]) -> List[Dict]:
        out = []
        for ev in active_events:
            pos = ev.get("position", [0, 0])
            l, t = self._world_to_pct(pos[0], pos[1])
            radius = ev.get("radius", 10)
            size_pct = (radius * 2 / (self.half_space_size * 2)) * 100.0
            out.append({
                "name": ev.get("name", ""),
                "display_name": ev.get("display_name", ev.get("name", "")),
                "left_pct": l,
                "top_pct": t,
                "size_pct": size_pct,
            })
        return out

    def _prepare_term_cloud(self, coined_terms: List[Dict]) -> List[Dict]:
        """term: {term: str, occurrence_count: int}"""
        out = []
        for ct in coined_terms[:30]:  # 表示は上位30件
            count = ct.get("occurrence_count", 1)
            # 重み 1〜5
            if count >= 20:
                w = 5
            elif count >= 10:
                w = 4
            elif count >= 5:
                w = 3
            elif count >= 3:
                w = 2
            else:
                w = 1
            out.append({"word": ct.get("term", ""), "weight": w})
        return out

    # ─────── レンダリング ───────

    def render_step(
        self,
        step: int,
        duration: int,
        place_status: Dict,
        agents_state: List[Dict],
        current_messages: List[Dict],
        active_events: List[Dict],
        recent_events: List[Dict],
        recent_messages_display: List[Dict],
        recent_thoughts: List[Dict],
        coined_terms: List[Dict],
        partnerships: Optional[List[Dict]] = None,
        attractors: Optional[List[Dict]] = None,
        hubs: Optional[List[Dict]] = None,
        silent_agents: Optional[List[Dict]] = None,
        save_key: bool = False,
    ) -> str:
        """1ステップぶんのフレームをレンダリングしてPNGを保存"""
        if self._page is None:
            raise RuntimeError("HTMLVisualizer は context manager として使ってください: `with HTMLVisualizer(...) as viz:`")

        ctx = {
            "step": step,
            "duration": duration,
            "progress_pct": (step / duration * 100.0) if duration else 0,
            "places": self._prepare_places(place_status),
            "agents": self._prepare_agents(agents_state),
            "comm_lines": self._prepare_comm_lines(current_messages, agents_state),
            "active_events": self._prepare_active_events(active_events),
            "recent_events": recent_events,
            "recent_messages": recent_messages_display,
            "recent_thoughts": recent_thoughts,
            "coined_terms": self._prepare_term_cloud(coined_terms),
            "partnerships": partnerships or [],
            "attractors": attractors or [],
            "hubs": hubs or [],
            "silent_agents": silent_agents or [],
        }

        template = self.env.get_template("frame.html")
        html = template.render(**ctx)

        # CSSをinline化（Playwrightのfile://だとリンクが解決しないため、HTMLにCSSを埋め込む）
        css_path = os.path.join(self.templates_dir, "frame.css")
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                css_content = f.read()
            html = html.replace(
                '<link rel="stylesheet" href="frame.css">',
                f'<style>{css_content}</style>',
            )

        # ページ読み込み
        self._page.set_content(html, wait_until="domcontentloaded")
        # フォントロードを待つ
        self._page.evaluate("() => document.fonts.ready")

        # スクリーンショット
        out_path = os.path.join(self.frames_dir, f"step_{step:04d}.png")
        self._page.screenshot(path=out_path, full_page=False, omit_background=False)

        if save_key:
            key_path = os.path.join(self.key_frames_dir, f"step_{step:04d}.png")
            shutil.copy2(out_path, key_path)

        logger.info(f"[viz] frame saved: {out_path}")
        return out_path

    # ─────── 動画結合 ───────

    def compose_video(self, output_path: Optional[str] = None) -> Optional[str]:
        """ffmpegで全フレームをmp4に結合"""
        if output_path is None:
            output_path = os.path.join(self.output_dir, "simulation.mp4")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.error("ffmpegが見つかりません。Homebrew等でインストールしてください: brew install ffmpeg")
            return None
        cmd = [
            ffmpeg, "-y",
            "-framerate", str(self.fps),
            "-i", os.path.join(self.frames_dir, "step_%04d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-preset", "slow",
            output_path,
        ]
        logger.info(f"[viz] composing mp4: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr[-500:]}")
            return None
        logger.info(f"[viz] video written: {output_path}")
        return output_path
