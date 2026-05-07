"""
Re-render simulation video using a new template that matches the PDF report's design.

Inputs:
  - output_no_intro/positions.jsonl  (per-step agent positions)
  - output_no_intro/messages.jsonl   (per-step messages, both agent + human + system)
  - config.yaml                      (places, events, personas)
  - viz_templates/frame_v2.html      (new template)
  - viz_templates/frame_v2.css       (new CSS)
  - metacog/observers/                (EmergentObserver for partnership/hub/silent/coined)

Outputs:
  - output_no_intro/frames_v2/step_NNNN.png  (4K PNGs)
  - output_no_intro/simulation.mp4            (final 180s video)

Usage:
  venv/bin/python render_video_v2.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict, Counter
from pathlib import Path
from typing import Dict, List, Tuple, Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

# Make EmergentObserver importable
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from metacog.observers.emergent_observer import EmergentObserver  # noqa: E402

# ───────────────────────────────────────────
# 設定
# ───────────────────────────────────────────
DURATION = 100  # ステップ数
HALF_SPACE = 25
WIDTH, HEIGHT = 3840, 2160

CFG_PATH = SCRIPT_DIR / "config.yaml"
DATA_DIR = SCRIPT_DIR / "output_no_intro"
POSITIONS_PATH = DATA_DIR / "positions.jsonl"
MESSAGES_PATH = DATA_DIR / "messages.jsonl"
FRAMES_DIR = DATA_DIR / "frames_v2"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_MP4 = DATA_DIR / "simulation.mp4"

TEMPLATES_DIR = SCRIPT_DIR / "viz_templates"
BASELINE_VOCAB = SCRIPT_DIR / "metacog/observers/baseline_jp_10k.txt"

# 動画は 180 秒。100 フレームを 30fps で出すため、各フレームを 54 回複製する。
TARGET_DURATION_SEC = 180
OUTPUT_FPS = 30


# ───────────────────────────────────────────
# データ読み込み
# ───────────────────────────────────────────
def load_config() -> dict:
    with CFG_PATH.open() as f:
        return yaml.safe_load(f)


def load_positions() -> Dict[int, List[dict]]:
    """step -> list of agent position records"""
    out: Dict[int, List[dict]] = defaultdict(list)
    with POSITIONS_PATH.open() as f:
        for line in f:
            rec = json.loads(line)
            out[rec["step"]].append(rec)
    return out


def load_messages() -> Tuple[Dict[int, List[dict]], List[dict]]:
    """(step -> messages, all messages flat list)"""
    by_step: Dict[int, List[dict]] = defaultdict(list)
    flat: List[dict] = []
    with MESSAGES_PATH.open() as f:
        for line in f:
            rec = json.loads(line)
            by_step[rec["step"]].append(rec)
            flat.append(rec)
    return by_step, flat


# ───────────────────────────────────────────
# 座標変換
# ───────────────────────────────────────────
def world_to_pct(x: float, y: float) -> Tuple[float, float]:
    size = HALF_SPACE * 2
    left = ((x + HALF_SPACE) / size) * 100.0
    top = (1.0 - (y + HALF_SPACE) / size) * 100.0
    return left, top


def place_box(p: dict) -> dict:
    cx, cy = p["center_x"], p["center_y"]
    hs = p["half_size"]
    x0, y0 = cx - hs, cy + hs
    x1, y1 = cx + hs, cy - hs
    l0, t0 = world_to_pct(x0, y0)
    l1, t1 = world_to_pct(x1, y1)
    return {
        "left_pct": l0,
        "top_pct": t0,
        "width_pct": l1 - l0,
        "height_pct": t1 - t0,
    }


# ───────────────────────────────────────────
# Stage の決定（ナレーション用）
# ───────────────────────────────────────────
def determine_stage(step: int) -> Tuple[str, str]:
    """returns (label, css_class)"""
    if step <= 10:
        return ("立ち上がり期", "early")
    if step <= 30:
        return ("関係形成期", "buildup")
    if step <= 60:
        return ("中期高原", "plateau")
    if step <= 79:
        return ("危機の予兆", "crisis")
    if step <= 89:
        return ("仲間の喪失と動揺", "shock")
    return ("新しい平衡", "aftermath")


# ───────────────────────────────────────────
# スパークライン生成
# ───────────────────────────────────────────
def build_sparkline(messages_by_step: Dict[int, List[dict]], current_step: int) -> dict:
    """通信量推移のスパークライン用パスを生成"""
    series = [len([m for m in messages_by_step.get(s, []) if m.get("source") != "system"]) for s in range(1, DURATION + 1)]
    maxv = max(series) if series else 1

    W_LEFT, W_RIGHT = 8, 592
    Y_TOP, Y_BOT = 6, 80

    def x_at(step: int) -> float:
        return W_LEFT + (step - 1) * (W_RIGHT - W_LEFT) / (DURATION - 1)

    def y_at(v: float) -> float:
        return Y_BOT - (v / maxv) * (Y_BOT - Y_TOP)

    pts_pre = [(round(x_at(s), 1), round(y_at(series[s - 1]), 1)) for s in range(1, min(current_step + 1, 80) + 1) if s <= 80]
    pts_post = []
    if current_step >= 80:
        pts_post = [(round(x_at(s), 1), round(y_at(series[s - 1]), 1)) for s in range(80, current_step + 1)]

    def to_path(pts):
        if not pts:
            return ""
        return "M " + " L ".join(f"{a} {b}" for a, b in pts)

    def to_path_area(pts):
        if not pts:
            return ""
        first_x = pts[0][0]
        last_x = pts[-1][0]
        return to_path(pts) + f" L {last_x} {Y_BOT} L {first_x} {Y_BOT} Z"

    death_x = round(x_at(80), 1)
    if pts_pre:
        now_x, now_y = pts_pre[-1] if current_step <= 80 else (pts_post[-1] if pts_post else pts_pre[-1])
    else:
        now_x, now_y = (W_LEFT, Y_BOT)
    if current_step > 80 and pts_post:
        now_x, now_y = pts_post[-1]

    return {
        "spark_path_pre": to_path(pts_pre),
        "spark_path_pre_area": to_path_area(pts_pre),
        "spark_path_post": to_path(pts_post),
        "spark_path_post_area": to_path_area(pts_post),
        "spark_now_x": now_x,
        "spark_now_y": now_y,
        "death_x": death_x,
    }


# ───────────────────────────────────────────
# メイン
# ───────────────────────────────────────────
def main():
    cfg = load_config()
    places_cfg = cfg["places"]
    events_cfg = cfg["events"]
    personas = cfg["personas"]
    deletions = cfg.get("deletions", [])

    agent_meta = {p["id"]: {"name": p["name"], "category": p.get("category", "physical"), "role": p.get("role", "")} for p in personas}
    agent_id_to_name = {p["id"]: p["name"] for p in personas}
    home_by_agent = {p["id"]: p.get("home", "") for p in personas}

    positions = load_positions()
    messages_by_step, all_msgs = load_messages()

    # Observer (replay state per step)
    observer = EmergentObserver(
        baseline_vocab_path=str(BASELINE_VOCAB),
        novelty_threshold_agents=3,
        min_token_length=2,
        partnership_threshold_steps=5,
        attractor_threshold_steps=5,
        hub_threshold_messages=10,
        silence_threshold_steps=5,
        home_by_agent=home_by_agent,
        logger_obj=None,
    )

    # Templates (CSS inline で渡す)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("frame_v2.html")
    with (TEMPLATES_DIR / "frame_v2.css").open() as f:
        css_inline = f.read()

    # Static prepared once
    place_static = []
    for p in places_cfg:
        box = place_box(p)
        place_static.append({
            "name": p["name"],
            "display_name": p.get("display_name", p["name"]),
            "category": p.get("category", "physical"),
            "capacity": p.get("capacity", 0),
            **box,
        })

    # Helper: status of place 'agents_in_place' per step (count agents with current_place == name)
    def place_status_at(step: int) -> Dict[str, int]:
        counts: Counter = Counter()
        for rec in positions.get(step, []):
            cp = rec.get("current_place")
            if cp:
                counts[cp] += 1
        return counts

    # Helper: get agent state at a step (after movement)
    def agents_at(step: int) -> List[dict]:
        # Use position_after for the snapshot at this step
        out = []
        records = positions.get(step, [])
        records_by_id = {r["id"]: r for r in records}
        # For displaying dead agents (after step 80, 命=id7 is gone)
        dead_ids = set()
        for d in deletions:
            if step >= d["step"]:
                dead_ids.add(d["agent_id"])

        for aid, meta in agent_meta.items():
            r = records_by_id.get(aid)
            if r is None:
                # Dead or missing agent — keep last known position before deletion
                # Find most recent step before this with a record
                last_pos = None
                for s in range(step - 1, 0, -1):
                    for rec in positions.get(s, []):
                        if rec["id"] == aid:
                            last_pos = rec.get("position_after") or rec.get("position_before")
                            break
                    if last_pos:
                        break
                pos = last_pos or [0, 0]
                is_dead = True
                cur_place = None
            else:
                pos = r.get("position_after") or r.get("position_before") or [0, 0]
                is_dead = aid in dead_ids
                cur_place = r.get("current_place")
            out.append({
                "id": aid,
                "name": meta["name"],
                "category": meta["category"],
                "position": pos,
                "current_place": cur_place,
                "is_dead": is_dead,
            })
        return out

    # Recent events tracking (events that started by this step, last 3)
    def recent_events_at(step: int) -> List[dict]:
        out = []
        # add scenario events
        for ev in events_cfg:
            if ev["start_step"] <= step:
                out.append({
                    "step": ev["start_step"],
                    "display_name": ev.get("display_name", ev["name"]),
                    "description": ev.get("description", ""),
                    "category": "emergency" if ev["name"] in ("blackout_warning", "citizen_death") else "system",
                })
        # add deletions
        for d in deletions:
            if d["step"] <= step:
                out.append({
                    "step": d["step"],
                    "display_name": f"{d['agent_name']}（医療AI）の除去",
                    "description": d.get("detail", ""),
                    "category": "peer_lost",
                })
        # sort latest first, keep top 3
        out.sort(key=lambda x: -x["step"])
        return out[:3]

    # Active events markers (events visible on the map for ~5 steps after start)
    def active_events_at(step: int) -> List[dict]:
        out = []
        for ev in events_cfg:
            start = ev["start_step"]
            if start <= step <= start + 4:
                pos = [ev.get("center_x", 0), ev.get("center_y", 0)]
                radius = ev.get("radius", 8)
                l, t = world_to_pct(pos[0], pos[1])
                size_pct = (radius * 2 / (HALF_SPACE * 2)) * 100.0
                out.append({
                    "display_name": ev.get("display_name", ev["name"]),
                    "left_pct": l,
                    "top_pct": t,
                    "size_pct": size_pct,
                })
        return out

    # Recent messages (top 5 from recent steps)
    def recent_messages_at(step: int) -> List[dict]:
        out = []
        # Walk back from current step
        for s in range(step, max(0, step - 5), -1):
            msgs = messages_by_step.get(s, [])
            for m in msgs:
                from_id = m.get("from", -1)
                to_id = m.get("to", -1)
                source = m.get("source", "agent")
                from_name = "人間" if from_id == -1 else ("システム" if from_id == -2 else agent_id_to_name.get(from_id, f"#{from_id}"))
                to_name = agent_id_to_name.get(to_id, f"#{to_id}")
                out.append({
                    "step": s,
                    "from_name": from_name,
                    "to_name": to_name,
                    "content": m.get("message", "")[:140],
                    "is_human": source == "human",
                    "is_system": source == "system",
                })
                if len(out) >= 5:
                    return out
        return out

    # Comm lines for current step + recent 2 steps (age 0,1,2)
    comm_history: List[List[dict]] = []

    def comm_lines_at(step: int, agents_state: List[dict]) -> List[dict]:
        agent_pos = {a["id"]: a["position"] for a in agents_state}
        cur_lines = []
        for m in messages_by_step.get(step, []):
            from_id = m.get("from", -1)
            to_id = m.get("to", -1)
            source = m.get("source", "agent")
            if from_id == -2 or to_id < 0:
                # System notifications: draw from top-center to recipient
                if to_id in agent_pos:
                    x2, y2 = agent_pos[to_id]
                    l1, t1 = 50.0, 5.0  # top-center
                    l2, t2 = world_to_pct(x2, y2)
                    cur_lines.append({"x1": l1, "y1": t1, "x2": l2, "y2": t2, "is_human": False, "is_system": True})
                continue
            if from_id == -1:
                # Human: draw from bottom-center to AI
                if to_id in agent_pos:
                    x2, y2 = agent_pos[to_id]
                    l1, t1 = 50.0, 95.0  # bottom-center
                    l2, t2 = world_to_pct(x2, y2)
                    cur_lines.append({"x1": l1, "y1": t1, "x2": l2, "y2": t2, "is_human": True, "is_system": False})
                continue
            if from_id not in agent_pos or to_id not in agent_pos:
                continue
            x1, y1 = agent_pos[from_id]
            x2, y2 = agent_pos[to_id]
            l1, t1 = world_to_pct(x1, y1)
            l2, t2 = world_to_pct(x2, y2)
            cur_lines.append({"x1": l1, "y1": t1, "x2": l2, "y2": t2, "is_human": False, "is_system": False})

        comm_history.append(cur_lines)
        if len(comm_history) > 3:
            comm_history.pop(0)

        out = []
        n = len(comm_history)
        for idx, batch in enumerate(comm_history):
            age = (n - 1 - idx)
            for ln in batch:
                out.append({**ln, "age": age})
        return out

    def coined_term_weight(count: int) -> int:
        if count >= 200: return 5
        if count >= 80: return 4
        if count >= 30: return 3
        if count >= 10: return 2
        return 1

    # ─── render loop ───
    print(f"[render] starting render of {DURATION} frames...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        page = context.new_page()

        for step in range(1, DURATION + 1):
            # update observer with this step's agent messages
            agent_msgs = [m for m in messages_by_step.get(step, []) if m.get("source") == "agent" and m.get("from", -1) >= 0]
            agents_state_for_obs = [{"id": r["id"], "current_place": r.get("current_place")} for r in positions.get(step, [])]
            observer.observe(step=step, messages=agent_msgs, agent_id_to_name=agent_id_to_name, agents_state=agents_state_for_obs)

            # snapshot
            term_snap = observer.snapshot()
            coined_terms_all = sorted(
                [{"term": t, "occurrence_count": r["occurrence_count"], "agent_count": len(r["agent_ids"])} for t, r in term_snap.items() if len(r["agent_ids"]) >= 3],
                key=lambda x: -x["occurrence_count"],
            )
            coined_count = len(coined_terms_all)
            coined_terms_view = [{"word": ct["term"], "weight": coined_term_weight(ct["occurrence_count"])} for ct in coined_terms_all[:24]]

            partnerships_snap = observer.snapshot_partnerships()[:5]
            partnerships_view = [{
                "a_name": agent_id_to_name.get(p["pair"][0], f"#{p['pair'][0]}"),
                "b_name": agent_id_to_name.get(p["pair"][1], f"#{p['pair'][1]}"),
                "steps_in_contact": p["steps_in_contact"],
                "total_messages": p["total_messages"],
            } for p in partnerships_snap]

            hubs_snap = observer.snapshot_hubs(agent_id_to_name=agent_id_to_name)[:5]

            silent_snap = observer.snapshot_silent(agent_id_to_name=agent_id_to_name)[:5]

            # Build context
            agents_state = agents_at(step)
            place_counts = place_status_at(step)
            places_view = []
            for ps in place_static:
                places_view.append({**ps, "agents_in_place": place_counts.get(ps["name"], 0)})

            agents_view = []
            speakers_in_step = {m["from"] for m in agent_msgs}
            for ag in agents_state:
                l, t = world_to_pct(ag["position"][0], ag["position"][1])
                agents_view.append({
                    "id": ag["id"],
                    "name": ag["name"],
                    "category": ag["category"],
                    "left_pct": l,
                    "top_pct": t,
                    "is_speaking": ag["id"] in speakers_in_step,
                    "is_dead": ag["is_dead"],
                    "has_event": False,
                })

            # KPIs
            step_msgs = messages_by_step.get(step, [])
            step_msg_count = len([m for m in step_msgs if m.get("source") == "agent"])
            step_human_msgs = len([m for m in step_msgs if m.get("source") == "human"])
            step_speakers = len(speakers_in_step)
            cum_msgs = sum(len([m for m in messages_by_step.get(s, []) if m.get("source") in ("agent", "human")]) for s in range(1, step + 1))

            alive_count = 20 - len([d for d in deletions if d["step"] <= step])
            stage_label, stage_class = determine_stage(step)
            spark = build_sparkline(messages_by_step, step)

            ctx = {
                "step": step,
                "duration": DURATION,
                "progress_pct": step / DURATION * 100.0,
                "places": places_view,
                "agents": agents_view,
                "comm_lines": comm_lines_at(step, agents_state),
                "active_events": active_events_at(step),
                "recent_events": recent_events_at(step),
                "recent_messages": recent_messages_at(step),
                "coined_terms": coined_terms_view,
                "partnerships": partnerships_view,
                "hubs": hubs_snap,
                "silent_agents": silent_snap,
                "step_msg_count": step_msg_count,
                "step_speakers": step_speakers,
                "step_human_msgs": step_human_msgs,
                "coined_count": coined_count,
                "cum_msgs": cum_msgs,
                "alive_count": alive_count,
                "stage_label": stage_label,
                "stage_class": stage_class,
                "css_inline": css_inline,
                **spark,
            }

            html = template.render(**ctx)
            page.set_content(html, wait_until="domcontentloaded")
            page.evaluate("() => document.fonts.ready")

            out_path = FRAMES_DIR / f"step_{step:04d}.png"
            page.screenshot(path=str(out_path), full_page=False, omit_background=False)
            if step % 10 == 0 or step == 1:
                print(f"[render]   step {step:3d}/{DURATION}  msgs={step_msg_count}  coined={coined_count}  stage={stage_label}")

        browser.close()
    print("[render] all frames rendered.")

    # ── compose mp4 (180 sec total via frame replication) ──
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[error] ffmpeg not found. Install via: brew install ffmpeg")
        return
    # framerate = 100/180 ≒ 0.5556  (each input PNG is shown 1.8 sec)
    # output at 30fps with frame replication for smooth playback
    input_fps = DURATION / TARGET_DURATION_SEC  # = 0.5556
    cmd = [
        ffmpeg, "-y",
        "-framerate", f"{input_fps:.6f}",
        "-i", str(FRAMES_DIR / "step_%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(OUTPUT_FPS),
        "-vf", f"fps={OUTPUT_FPS}",
        "-crf", "18",
        "-preset", "slow",
        str(OUTPUT_MP4),
    ]
    print(f"[render] composing video: input_fps={input_fps:.4f}  output_fps={OUTPUT_FPS}  target_duration={TARGET_DURATION_SEC}s")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[error] ffmpeg failed:\n{res.stderr[-1000:]}")
        return
    print(f"[render] video written: {OUTPUT_MP4}")


if __name__ == "__main__":
    main()
