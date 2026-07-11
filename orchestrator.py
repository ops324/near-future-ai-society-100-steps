"""
Round 4 Orchestrator
- L0 (身体): 各stepでsimulationを進める
- L1 (内省): 起動条件を満たすagentに対しIntrospectorを呼ぶ
- 創発観察: 毎step、その回の通信内容を観察
- 可視化: HTMLVisualizer (4K, ダーク寄り上品) で各stepのフレームを生成

起動: python orchestrator.py [--sim-config config.yaml] [--meta-config metacog/config.yaml] [--duration N]
"""
import argparse
import json
import logging
import os
import random
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

import yaml

sys.path.insert(0, os.path.dirname(__file__))

from simulation import Simulation
from metacog.agent.introspector import Introspector
from metacog.observers.emergent_observer import EmergentObserver
from metacog.logging.jsonl_logger import MetaCogLogger

# キーステップ（重要イベントの瞬間、key_frames/に別途保存）
KEY_STEPS = {30, 50, 75, 90, 100}


def setup_logging(log_file: str = "simulation.log"):
    handlers = [logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def load_messages_for_step(output_dir: str, step: int) -> List[dict]:
    """messages.jsonl から指定step のメッセージを読み出す"""
    if not output_dir:
        return []
    msg_file = os.path.join(output_dir, "messages.jsonl")
    if not os.path.exists(msg_file):
        return []
    out = []
    with open(msg_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("step") == step:
                out.append(rec)
    return out


def load_recent_inner_thoughts(log_dir: str, max_n: int = 3) -> List[Dict]:
    """inner_thought.jsonl から直近N件を読み出す"""
    path = os.path.join(log_dir, "inner_thought.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-max_n:]


def build_agents_state(sim) -> List[Dict]:
    out = []
    for a in sim.agents:
        out.append({
            "id": a.id,
            "name": a.persona_name,
            "category": a.category,
            "position": list(a.position),
            "has_event": bool(a.event_queue),
        })
    return out


def build_recent_events(sim, current_step: int) -> List[Dict]:
    """発火済みイベントを「最近順」で返す"""
    out = []
    for fs in sim.event_states:
        out.append({
            "step": fs.get("start_step", 0),
            "display_name": fs.get("display_name", fs.get("name", "")),
            "description": fs.get("description", ""),
            "category": "physical",  # 全イベントを同じcategoryで（必要なら拡張）
        })
    out.sort(key=lambda e: e["step"], reverse=True)
    return out


def build_recent_messages_display(sim, current_messages: List[Dict], max_n: int = 5) -> List[Dict]:
    """current_messagesをUI表示用に整形"""
    name_by_id = {a.id: a.persona_name for a in sim.agents}
    out = []
    for msg in current_messages[-max_n:]:
        from_id = msg.get("from", -1)
        to_id = msg.get("to", -1)
        is_human = (from_id == -1 or msg.get("source") == "human")
        from_name = "人間" if is_human else name_by_id.get(from_id, f"#{from_id}")
        to_name = name_by_id.get(to_id, f"#{to_id}")
        out.append({
            "from_name": from_name,
            "to_name": to_name,
            "content": msg.get("message", "") or msg.get("content", ""),
            "is_human": is_human,
        })
    return out


def build_recent_thoughts(log_dir: str, max_n: int = 3, agent_categories: Dict[int, str] = None) -> List[Dict]:
    raw = load_recent_inner_thoughts(log_dir, max_n=max_n)
    out = []
    cats = agent_categories or {}
    for r in raw:
        diffs = []
        before = r.get("before", {})
        after = r.get("after", {})
        for k in ["self_concept", "current_goal", "coping_notes"]:
            if before.get(k) != after.get(k):
                diffs.append(k)
        diff_summary = "更新: " + ", ".join(diffs) if diffs else "（自己理解は維持）"
        out.append({
            "agent_name": r.get("agent_name", ""),
            "thought": r.get("inner_thought", ""),
            "diff_summary": diff_summary,
            "category": cats.get(r.get("agent_id", -1), "physical"),
        })
    return out


def build_coined_terms(observer, top_n: int = 30) -> List[Dict]:
    if observer is None:
        return []
    snap = observer.snapshot()
    threshold = observer.novelty_threshold
    items = []
    for term, rec in snap.items():
        if len(rec["agent_ids"]) >= threshold:
            items.append({
                "term": term,
                "occurrence_count": rec["occurrence_count"],
                "agent_count": len(rec["agent_ids"]),
            })
    items.sort(key=lambda x: x["occurrence_count"], reverse=True)
    return items[:top_n]


def build_emergent_partnerships(observer, name_by_id: Dict[int, str], top_n: int = 5) -> List[Dict]:
    if observer is None:
        return []
    items = observer.snapshot_partnerships()[:top_n]
    out = []
    for it in items:
        a, b = it["pair"]
        out.append({
            "a_name": name_by_id.get(a, f"#{a}"),
            "b_name": name_by_id.get(b, f"#{b}"),
            "steps_in_contact": it["steps_in_contact"],
            "total_messages": it["total_messages"],
        })
    return out


def build_emergent_attractors(
    observer,
    name_by_id: Dict[int, str],
    place_disp_by_name: Dict[str, str],
    top_n: int = 5
) -> List[Dict]:
    if observer is None:
        return []
    items = observer.snapshot_attractors()[:top_n]
    out = []
    for it in items:
        out.append({
            "agent_name": name_by_id.get(it["agent_id"], f"#{it['agent_id']}"),
            "home_display": place_disp_by_name.get(it["home"], it["home"]),
            "attracted_to_display": place_disp_by_name.get(it["attracted_to"], it["attracted_to"]),
            "visit_count": it["visit_count"],
        })
    return out


def build_emergent_hubs(observer, name_by_id: Dict[int, str], top_n: int = 3) -> List[Dict]:
    if observer is None:
        return []
    items = observer.snapshot_hubs(name_by_id)[:top_n]
    return items


def build_emergent_silent(observer, name_by_id: Dict[int, str], top_n: int = 3) -> List[Dict]:
    if observer is None:
        return []
    items = observer.snapshot_silent(name_by_id)[:top_n]
    return items


def main():
    parser = argparse.ArgumentParser(description="Round 4 Orchestrator")
    parser.add_argument("--sim-config", default="config.yaml")
    parser.add_argument("--meta-config", default="metacog/config.yaml")
    parser.add_argument("--duration", type=int, default=None, help="シミュレーションステップ数（config上書き）")
    parser.add_argument("--output-dir", default="output", help="出力ディレクトリ")
    parser.add_argument("--no-introspect", action="store_true", help="L1（内省層）を無効化（API消費なしのドライラン）")
    parser.add_argument("--no-viz", action="store_true", help="可視化を無効化")
    parser.add_argument("--no-video", action="store_true", help="mp4結合をスキップ（フレームPNGのみ生成）")
    parser.add_argument("--log-dir", default=None, help="metacog ログの出力先（指定するとmetacog/configを上書き）")
    parser.add_argument("--seed", type=int, default=None, help="乱数シード（初期配置・人間メッセージ抽出・L0決定=Ollamaまで揃え、実験を再現可能にする）")
    parser.add_argument("--governance-mode", choices=["as-config", "baseline", "governed"],
                        default="as-config",
                        help="ガバナンス・プリセット（比較用）: as-config=config.yamlのまま / baseline=統治なし / governed=統治あり")
    args = parser.parse_args()

    # 乱数シード（指定されたら numpy/Python random どちらも固定）
    if args.seed is not None:
        random.seed(args.seed)
        try:
            import numpy as np
            np.random.seed(args.seed)
        except Exception:
            pass

    # configロード
    with open(args.meta_config, "r", encoding="utf-8") as f:
        meta_config = yaml.safe_load(f)

    with open(args.sim_config, "r", encoding="utf-8") as f:
        sim_config_dict = yaml.safe_load(f)
    log_file = sim_config_dict.get("logging", {}).get("log_file", "simulation.log")
    setup_logging(log_file)
    logger = logging.getLogger("orchestrator")

    # 出力ディレクトリ
    os.makedirs(args.output_dir, exist_ok=True)

    # シミュレーション初期化（--governance-mode のプリセットがあれば config を上書き）
    from simulation import governance_preset
    gov_override = governance_preset(args.governance_mode)
    sim = Simulation(config_path=args.sim_config, output_dir=args.output_dir,
                     governance_override=gov_override, seed=args.seed)
    if args.governance_mode != "as-config":
        logger.info(f"Governance preset applied: {args.governance_mode}")
    if args.duration:
        sim.duration = args.duration

    # 再実行時の二重計上を防ぐため output_dir の追記ログを初期化し、実行同定情報を書く
    sim.reset_output_logs()
    sim.write_run_meta(extra={"governance_mode": args.governance_mode,
                              "introspect": not args.no_introspect})
    logger.info(f"run_id={sim.run_id} schema_version={sim.schema_version}")

    session_id = uuid.uuid4().hex[:8]
    # ログ出力先: --log-dir 優先、なければ meta_config の値
    log_dir = args.log_dir if args.log_dir else meta_config["logging"]["log_dir"]
    os.makedirs(log_dir, exist_ok=True)
    meta_logger = MetaCogLogger(log_dir=log_dir, session_id=session_id, console=meta_config["logging"]["console"])
    meta_logger.log_session_start(
        config={
            "sim_config": args.sim_config,
            "meta_config": args.meta_config,
            "duration": sim.duration,
            "introspect_enabled": not args.no_introspect,
            "viz_enabled": not args.no_viz,
            "seed": args.seed,
            "output_dir": args.output_dir,
            "log_dir": log_dir,
        }
    )

    # L1 (Introspector)
    # B-5: 自己更新は 3アーム。governance.self_update.mode == "off" は --no-introspect と等価（提出ベースライン）。
    # プリセット上書きを反映した実効値（sim.governance）を使う。
    self_update_mode = sim.governance.get("self_update", {}).get("mode", "off")
    introspector = None
    if (not args.no_introspect
            and self_update_mode != "off"
            and meta_config.get("introspection", {}).get("enabled", True)):
        try:
            introspector = Introspector(meta_config, logger_obj=meta_logger)
            logger.info(f"Introspector ready (model={introspector.model}, self_update.mode={self_update_mode})")
        except (ImportError, EnvironmentError) as e:
            logger.error(f"Introspector を起動できません: {e}")
            logger.error("--no-introspect 付きで実行するか、anthropic SDK と ANTHROPIC_API_KEY を設定してください。")
            return
    elif self_update_mode == "off":
        logger.info("self_update.mode=off → 内省層は起動しない（ベースライン）。")

    trigger_interval = meta_config.get("introspection", {}).get("trigger_interval_steps", 10)

    # 創発観察 (multi-axis: 語彙 / 通信ペア / 場所アトラクター / ハブ / 沈黙)
    observer = None
    obs_cfg = meta_config.get("emergent_observer", {})
    if obs_cfg.get("enabled", True):
        baseline_path = obs_cfg.get("baseline_vocab_path", "metacog/observers/baseline_jp_10k.txt")
        # ホーム情報をconfig.yamlのpersonasから取得（agentの初期化前なので生configから）
        home_by_agent_pre = {p["id"]: p.get("home", "") for p in sim_config_dict.get("personas", []) if "id" in p}
        observer = EmergentObserver(
            baseline_vocab_path=baseline_path,
            novelty_threshold_agents=obs_cfg.get("novelty_threshold_agents", 3),
            min_token_length=obs_cfg.get("min_token_length", 2),
            partnership_threshold_steps=obs_cfg.get("partnership_threshold_steps", 5),
            attractor_threshold_steps=obs_cfg.get("attractor_threshold_steps", 5),
            hub_threshold_messages=obs_cfg.get("hub_threshold_messages", 20),
            silence_threshold_steps=obs_cfg.get("silence_threshold_steps", 5),
            home_by_agent=home_by_agent_pre,
            logger_obj=meta_logger,
        )
        logger.info("EmergentObserver ready (multi-axis)")

    # Ollama接続確認
    if not sim.llm_client.check_connection():
        logger.error("Ollamaに接続できません。Ollamaが起動しているか確認してください。")
        return
    if not sim.llm_client.check_model_exists():
        logger.error(f"モデル '{sim.llm_client.model}' が Ollama に存在しません。`ollama pull {sim.llm_client.model}` してください。")
        return

    sim.initialize_agents()
    logger.info(f"シミュレーション開始 (duration={sim.duration}, trigger_interval={trigger_interval})")
    logger.info(f"セッションID: {session_id}")

    agent_id_to_name = {a.id: a.persona_name for a in sim.agents}
    agent_id_to_category = {a.id: a.category for a in sim.agents}

    # 可視化設定
    viz = None
    viz_cm = None
    if not args.no_viz:
        try:
            from visualization_html import HTMLVisualizer
            viz_cfg = sim_config_dict.get("visualization", {})
            res = tuple(viz_cfg.get("resolution", [3840, 2160]))
            fps = viz_cfg.get("fps", 5)
            agents_meta = [
                {"id": a.id, "name": a.persona_name, "category": a.category}
                for a in sim.agents
            ]
            viz = HTMLVisualizer(
                places=sim.places,
                agents_meta=agents_meta,
                half_space_size=sim.half_space_size,
                output_dir=args.output_dir,
                templates_dir="viz_templates",
                resolution=res,
                fps=fps,
            )
            viz_cm = viz.__enter__()
            logger.info(f"HTMLVisualizer ready (resolution={res}, fps={fps})")
        except ImportError as e:
            logger.error(f"HTMLVisualizerを起動できません: {e}")
            logger.error("--no-viz 付きで実行するか、playwright/jinja2をインストールしてください。")
            viz = None

    try:
        while sim.step < sim.duration:
            sim.step_simulation()
            logger.info(f"[sim] step {sim.step}/{sim.duration} 完了")

            # このstepのメッセージ
            step_messages = load_messages_for_step(args.output_dir, sim.step)
            agent_messages = [m for m in step_messages if m.get("source", "agent") != "human"]

            # 創発観察（語彙＋通信ペア＋アトラクター＋ハブ＋沈黙）
            if observer is not None:
                # agents_state を渡してアトラクター観察に使う
                obs_agents_state = [
                    {"id": a.id, "current_place": a.current_place}
                    for a in sim.agents
                ]
                observer.observe(sim.step, agent_messages, agent_id_to_name, agents_state=obs_agents_state)

            # L1 (内省) — 並列化
            if introspector is not None:
                check_every = meta_config.get("orchestrator", {}).get("introspection_check_every_n_steps", 1)
                if sim.step % check_every == 0:
                    targets = [a for a in sim.agents if a.should_introspect(sim.step, trigger_interval)]
                    if targets:
                        max_concurrent = meta_config.get("orchestrator", {}).get("max_concurrent_introspections", 5)

                        def _run_intro(agent):
                            try:
                                introspector.run_for_agent(
                                    agent,
                                    current_step=sim.step,
                                    all_agents=sim.agents,
                                    output_dir=args.output_dir,
                                    log_dir=log_dir,
                                )
                            except Exception as e:
                                logger.error(f"Introspector failed for agent {agent.id}: {e}")

                        with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
                            list(ex.map(_run_intro, targets))

            # 可視化
            if viz is not None:
                try:
                    place_status = sim.get_place_status()
                    agents_state = build_agents_state(sim)
                    active_events = []
                    for fs in sim.event_states:
                        if fs.get("active"):
                            active_events.append({
                                "name": fs["name"],
                                "display_name": fs.get("display_name", fs["name"]),
                                "position": list(fs["position"]),
                                "radius": fs["radius"],
                            })
                    recent_events_disp = build_recent_events(sim, sim.step)
                    recent_msgs_disp = build_recent_messages_display(sim, step_messages, max_n=5)
                    recent_thoughts = build_recent_thoughts(log_dir, max_n=3, agent_categories=agent_id_to_category)
                    coined_terms = build_coined_terms(observer, top_n=20)
                    place_disp_by_name = {p["name"]: p.get("display_name", p["name"]) for p in sim.places}
                    partnerships = build_emergent_partnerships(observer, agent_id_to_name, top_n=4)
                    attractors = build_emergent_attractors(observer, agent_id_to_name, place_disp_by_name, top_n=4)
                    hubs = build_emergent_hubs(observer, agent_id_to_name, top_n=3)
                    silent_agents = build_emergent_silent(observer, agent_id_to_name, top_n=3)

                    viz.render_step(
                        step=sim.step,
                        duration=sim.duration,
                        place_status=place_status,
                        agents_state=agents_state,
                        current_messages=step_messages,
                        active_events=active_events,
                        recent_events=recent_events_disp,
                        recent_messages_display=recent_msgs_disp,
                        recent_thoughts=recent_thoughts,
                        coined_terms=coined_terms,
                        partnerships=partnerships,
                        attractors=attractors,
                        hubs=hubs,
                        silent_agents=silent_agents,
                        save_key=(sim.step in KEY_STEPS),
                    )
                except Exception as e:
                    logger.error(f"Visualization failed at step {sim.step}: {e}", exc_info=True)

        logger.info("シミュレーション完了")

    except KeyboardInterrupt:
        logger.info("中断されました")
    finally:
        # 動画結合
        if viz is not None:
            try:
                if not args.no_video:
                    viz.compose_video()
                viz.__exit__(None, None, None)
            except Exception as e:
                logger.error(f"Video composition failed: {e}", exc_info=True)

        # 最終スナップショット
        summary = {
            "total_steps": sim.step,
            "duration": sim.duration,
            "num_agents": sim.num_agents,
            "introspections_per_agent": {a.id: a.introspection_count for a in sim.agents},
            "final_self_concepts": {a.id: a.self_concept for a in sim.agents},
            "final_current_goals": {a.id: a.current_goal for a in sim.agents},
        }
        if observer is not None:
            snap = observer.snapshot()
            summary["coined_terms_count"] = sum(
                1 for v in snap.values()
                if len(v["agent_ids"]) >= observer.novelty_threshold
            )
        meta_logger.log_session_end(summary=summary)
        logger.info(f"ログ保存先: {log_dir}/")
        logger.info(f"出力先: {args.output_dir}/")


if __name__ == "__main__":
    main()
