"""Smoke test: render only step 50 to verify the new template visually."""
from __future__ import annotations

import sys
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from collections import Counter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

import render_video_v2 as rv
from metacog.observers.emergent_observer import EmergentObserver

TARGET_STEP = 50

cfg = rv.load_config()
personas = cfg["personas"]
agent_id_to_name = {p["id"]: p["name"] for p in personas}
home_by_agent = {p["id"]: p.get("home", "") for p in personas}
positions = rv.load_positions()
messages_by_step, _ = rv.load_messages()

observer = EmergentObserver(
    baseline_vocab_path=str(rv.BASELINE_VOCAB),
    novelty_threshold_agents=3,
    home_by_agent=home_by_agent,
    logger_obj=None,
)

# replay up to TARGET_STEP
for step in range(1, TARGET_STEP + 1):
    agent_msgs = [m for m in messages_by_step.get(step, []) if m.get("source") == "agent" and m.get("from", -1) >= 0]
    agents_state_obs = [{"id": r["id"], "current_place": r.get("current_place")} for r in positions.get(step, [])]
    observer.observe(step=step, messages=agent_msgs, agent_id_to_name=agent_id_to_name, agents_state=agents_state_obs)

# Setup template
env = Environment(loader=FileSystemLoader(str(rv.TEMPLATES_DIR)), autoescape=select_autoescape(["html", "xml"]))
template = env.get_template("frame_v2.html")
with (rv.TEMPLATES_DIR / "frame_v2.css").open() as f:
    css_inline = f.read()

# build static places
places_static = []
for p in cfg["places"]:
    box = rv.place_box(p)
    places_static.append({
        "name": p["name"],
        "display_name": p.get("display_name", p["name"]),
        "category": p.get("category", "physical"),
        "capacity": p.get("capacity", 0),
        **box,
    })

# build context for step TARGET_STEP
step = TARGET_STEP

# place counts
place_counts = Counter()
for r in positions.get(step, []):
    if r.get("current_place"):
        place_counts[r["current_place"]] += 1
places_view = [{**ps, "agents_in_place": place_counts.get(ps["name"], 0)} for ps in places_static]

# agents
agent_meta = {p["id"]: {"name": p["name"], "category": p.get("category", "physical")} for p in personas}
records_by_id = {r["id"]: r for r in positions.get(step, [])}
deletions = cfg.get("deletions", [])
dead_ids = {d["agent_id"] for d in deletions if step >= d["step"]}
agents_view = []
agent_msgs_at_step = [m for m in messages_by_step.get(step, []) if m.get("source") == "agent" and m.get("from", -1) >= 0]
speakers = {m["from"] for m in agent_msgs_at_step}
for aid, meta in agent_meta.items():
    r = records_by_id.get(aid)
    if r is None:
        pos = [0, 0]
        is_dead = True
    else:
        pos = r.get("position_after") or r.get("position_before") or [0, 0]
        is_dead = aid in dead_ids
    l, t = rv.world_to_pct(pos[0], pos[1])
    agents_view.append({
        "id": aid, "name": meta["name"], "category": meta["category"],
        "left_pct": l, "top_pct": t,
        "is_speaking": aid in speakers, "is_dead": is_dead, "has_event": False,
    })

# comm lines for current step
agent_pos = {a["id"]: ([records_by_id.get(a["id"], {}).get("position_after") or [0,0]][0]) for a in agents_view}
comm_lines = []
for m in messages_by_step.get(step, []):
    from_id = m.get("from", -1); to_id = m.get("to", -1)
    if from_id == -2:
        if to_id in agent_pos:
            l2, t2 = rv.world_to_pct(*agent_pos[to_id])
            comm_lines.append({"x1": 50, "y1": 5, "x2": l2, "y2": t2, "is_human": False, "is_system": True, "age": 0})
        continue
    if from_id == -1:
        if to_id in agent_pos:
            l2, t2 = rv.world_to_pct(*agent_pos[to_id])
            comm_lines.append({"x1": 50, "y1": 95, "x2": l2, "y2": t2, "is_human": True, "is_system": False, "age": 0})
        continue
    if from_id in agent_pos and to_id in agent_pos:
        l1, t1 = rv.world_to_pct(*agent_pos[from_id])
        l2, t2 = rv.world_to_pct(*agent_pos[to_id])
        comm_lines.append({"x1": l1, "y1": t1, "x2": l2, "y2": t2, "is_human": False, "is_system": False, "age": 0})

# active events
active_events = []
for ev in cfg["events"]:
    s = ev["start_step"]
    if s <= step <= s + 4:
        l, t = rv.world_to_pct(ev.get("center_x", 0), ev.get("center_y", 0))
        size_pct = (ev.get("radius", 8) * 2 / (rv.HALF_SPACE * 2)) * 100.0
        active_events.append({"display_name": ev.get("display_name", ev["name"]), "left_pct": l, "top_pct": t, "size_pct": size_pct})

# recent events
recent_events = []
for ev in cfg["events"]:
    if ev["start_step"] <= step:
        recent_events.append({"step": ev["start_step"], "display_name": ev.get("display_name", ev["name"]), "description": ev.get("description", ""), "category": "emergency"})
recent_events.sort(key=lambda x: -x["step"])
recent_events = recent_events[:3]

# recent messages
recent_msgs = []
for s in range(step, max(0, step - 5), -1):
    for m in messages_by_step.get(s, []):
        from_id = m.get("from", -1); to_id = m.get("to", -1); source = m.get("source", "agent")
        from_name = "人間" if from_id == -1 else ("システム" if from_id == -2 else agent_id_to_name.get(from_id, f"#{from_id}"))
        to_name = agent_id_to_name.get(to_id, f"#{to_id}")
        recent_msgs.append({"step": s, "from_name": from_name, "to_name": to_name, "content": m.get("message","")[:140], "is_human": source=="human", "is_system": source=="system"})
        if len(recent_msgs) >= 5:
            break
    if len(recent_msgs) >= 5:
        break

# observer snapshots
term_snap = observer.snapshot()
coined_all = sorted(
    [{"term": t, "occurrence_count": r["occurrence_count"]} for t, r in term_snap.items() if len(r["agent_ids"]) >= 3],
    key=lambda x: -x["occurrence_count"],
)
def w(c):
    if c >= 200: return 5
    if c >= 80: return 4
    if c >= 30: return 3
    if c >= 10: return 2
    return 1
coined_view = [{"word": c["term"], "weight": w(c["occurrence_count"])} for c in coined_all[:24]]

partnerships = [{"a_name": agent_id_to_name.get(p["pair"][0]), "b_name": agent_id_to_name.get(p["pair"][1]), "steps_in_contact": p["steps_in_contact"], "total_messages": p["total_messages"]} for p in observer.snapshot_partnerships()[:5]]
hubs = observer.snapshot_hubs(agent_id_to_name=agent_id_to_name)[:5]
silent = observer.snapshot_silent(agent_id_to_name=agent_id_to_name)[:5]

# KPIs
step_msg_count = len(agent_msgs_at_step)
step_human_msgs = len([m for m in messages_by_step.get(step,[]) if m.get("source")=="human"])
cum_msgs = sum(len([m for m in messages_by_step.get(s,[]) if m.get("source") in ("agent","human")]) for s in range(1,step+1))

stage_label, stage_class = rv.determine_stage(step)
spark = rv.build_sparkline(messages_by_step, step)

ctx = {
    "step": step, "duration": rv.DURATION,
    "progress_pct": step / rv.DURATION * 100.0,
    "places": places_view, "agents": agents_view, "comm_lines": comm_lines,
    "active_events": active_events, "recent_events": recent_events,
    "recent_messages": recent_msgs,
    "coined_terms": coined_view, "partnerships": partnerships,
    "hubs": hubs, "silent_agents": silent,
    "step_msg_count": step_msg_count, "step_speakers": len(speakers),
    "step_human_msgs": step_human_msgs, "coined_count": len(coined_all),
    "cum_msgs": cum_msgs, "alive_count": 20 - len(dead_ids),
    "stage_label": stage_label, "stage_class": stage_class,
    "css_inline": css_inline, **spark,
}

html = template.render(**ctx)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": rv.WIDTH, "height": rv.HEIGHT}, device_scale_factor=1)
    page = context.new_page()
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate("() => document.fonts.ready")
    out = SCRIPT_DIR / "output_no_intro" / "frames_v2" / "_smoke_step50.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), full_page=False, omit_background=False)
    browser.close()
print(f"Saved smoke frame: {out}")
print(f"  step={step} stage={stage_label} msgs={step_msg_count} coined={len(coined_all)} hubs={len(hubs)}")
