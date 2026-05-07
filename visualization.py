"""
Visualization for LLM Multi-Agent 2D Simulation
"""
import matplotlib
import os
import time
import logging
from typing import List, Dict, Tuple, Optional

# Set backend for compatibility (Mac, Linux, WSL)
GUI_BACKENDS = ['TkAgg', 'Qt5Agg', 'MacOSX', 'Qt4Agg']
NON_GUI_BACKENDS = ['agg', 'pdf', 'svg', 'ps']

# Visualization constants
FIGURE_SIZE = (10, 10)
STATS_FIGURE_SIZE = (12, 8)
DPI = 150
INITIAL_WINDOW_DELAY = 0.5
VISUALIZATION_PAUSE = 0.05
STATS_PAUSE = 0.1

# Agent visualization constants
AGENT_SIZE_IN_BAR = 100
AGENT_SIZE_OUTSIDE = 80
AGENT_ALPHA = 0.7
COMMUNICATION_LINK_ALPHA = 0.3

# Place visualization constants
BAR_LINEWIDTH = 2
BAR_ALPHA = 0.3

# Fire visualization constants
FIRE_MARKER_SIZE = 200
FIRE_CIRCLE_ALPHA = 0.15
FIRE_CIRCLE_LINEWIDTH = 2

# Statistics plot constants (will be made configurable)
DEFAULT_OCCUPANCY_THRESHOLD = 0.6
DEFAULT_AGENT_THRESHOLD = 12
MAX_AGENTS_DISPLAY = 20

# Set backend for compatibility (Mac, Linux, WSL)
backend_set = False

# Check if we're in WSL or headless environment
is_wsl = 'microsoft' in os.uname().release.lower() if hasattr(os, 'uname') else False
is_headless = not os.environ.get('DISPLAY') and not is_wsl

if is_wsl or is_headless:
    # Use non-GUI backend for WSL or headless environments
    try:
        matplotlib.use('Agg')
        backend_set = True
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Using Agg backend (non-GUI) for WSL/headless environment")
    except Exception:
        pass
else:
    # Try GUI backends for interactive environments
    for backend_name in GUI_BACKENDS:
        try:
            matplotlib.use(backend_name)
            backend_set = True
            break
        except (ImportError, ValueError):
            continue

if not backend_set:
    # Fallback to Agg backend (non-GUI, always available)
    try:
        matplotlib.use('Agg')
        backend_set = True
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("No GUI backend available. Using Agg backend (non-GUI). Visualization windows will not display.")
    except Exception:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Failed to set matplotlib backend. Visualization may not work.")

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.colors as mcolors
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import numpy as np

# Japanese font (IPAGothic per global rule, with macOS fallbacks)
matplotlib.rcParams['font.family'] = [
    'IPAGothic',
    'Hiragino Sans',
    'Hiragino Kaku Gothic Pro',
    'Hiragino Maru Gothic ProN',
    'YuGothic',
    'Apple SD Gothic Neo',
    'sans-serif',
]
matplotlib.rcParams['axes.unicode_minus'] = False

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Asset paths (relative to project root i.e. directory containing visualization.py)
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")
PLACE_ICONS_DIR = os.path.join(_ASSETS_DIR, "places")
PERSONA_ICONS_DIR = os.path.join(_ASSETS_DIR, "personas")

# Place-type → image filename. All places are now image-based.
PLACE_IMAGE_FILES = {
    "happy_to_chat_bench": "bench.png",
    "ordinary_bench": "bench.png",
    "playground": "playground.png",
    "lawn": "lawn.png",
    "fountain_plaza": "fountain.png",
    "walking_path": "walking_path.png",
    "kiosk": "kiosk.png",
    "flower_garden": "flower_garden.png",
}

# Japanese display names for places
PLACE_DISPLAY_NAMES = {
    "happy_to_chat_bench": "ハッピー・トゥ・チャット・ベンチ",
    "ordinary_bench": "ベンチ",
    "playground": "ブランコ",
    "lawn": "芝生",
    "fountain_plaza": "噴水広場",
    "walking_path": "並木道",
    "kiosk": "売店",
    "flower_garden": "花壇",
}

# Background tint per place type (subtle, layered under image)
PLACE_TYPE_COLORS = {
    "happy_to_chat_bench": "lightyellow",
    "ordinary_bench": "lightgray",
    "playground": "lightcoral",
    "lawn": "lightgreen",
    "fountain_plaza": "lightblue",
    "walking_path": "wheat",
    "kiosk": "lavender",
    "flower_garden": "lightpink",
}

# Fallback for any unknown place type
DEFAULT_PLACE_COLORS = ['lightblue', 'lightcoral', 'lightgreen', 'lightyellow', 'lightpink']

# Icon zoom factors
PLACE_ICON_BASE_ZOOM = 0.5  # multiplied by half_size scale
AGENT_ICON_ZOOM = 0.35
IN_PLACE_RING_COLOR = 'gold'
IN_PLACE_RING_WIDTH = 2.5


class Visualizer:
    """Visualization class for simulation"""

    def __init__(self, half_space_size: int, places: List[Dict], num_agents: int = None):
        self.half_space_size = half_space_size
        self.places = places
        self.num_agents = num_agents
        self.fig = None
        self.ax = None
        self.figure_initialized = False
        # Pre-load icon images
        self._place_images: Dict[str, np.ndarray] = {}
        self._persona_images: Dict[int, np.ndarray] = {}
        self._load_icons()

    def _load_icons(self):
        """Load place and persona PNGs once at startup, resizing to keep frame size manageable."""
        if not _PIL_AVAILABLE:
            logger.warning("PIL/Pillow not available; icons will not be drawn (falling back to text labels).")
            return

        PLACE_MAX_DIM = 256  # downscale large user-provided photos
        PERSONA_MAX_DIM = 96

        # Place images (resized to PLACE_MAX_DIM to keep memory/file size small)
        for place_type, filename in PLACE_IMAGE_FILES.items():
            path = os.path.join(PLACE_ICONS_DIR, filename)
            if os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    img.thumbnail((PLACE_MAX_DIM, PLACE_MAX_DIM), Image.Resampling.LANCZOS)
                    self._place_images[place_type] = np.asarray(img)
                except Exception as e:
                    logger.warning(f"Failed to load place icon {path}: {e}")
            else:
                logger.warning(f"Place icon not found: {path}")

        # Persona icons (01.png .. NN.png), already small (~72px), but normalize anyway
        if self.num_agents:
            for i in range(self.num_agents):
                num = f"{i+1:02d}"
                path = os.path.join(PERSONA_ICONS_DIR, f"{num}.png")
                if os.path.exists(path):
                    try:
                        img = Image.open(path).convert("RGBA")
                        img.thumbnail((PERSONA_MAX_DIM, PERSONA_MAX_DIM), Image.Resampling.LANCZOS)
                        self._persona_images[i] = np.asarray(img)
                    except Exception as e:
                        logger.warning(f"Failed to load persona icon {path}: {e}")
                else:
                    logger.warning(f"Persona icon not found: {path}")

    def setup_figure(self, reuse_existing: bool = False):
        """Setup matplotlib figure"""
        if reuse_existing and self.fig is not None:
            # Clear existing figure instead of creating new one
            self.ax.clear()
        else:
            # Create new figure
            self.fig, self.ax = plt.subplots(figsize=FIGURE_SIZE)
            self.figure_initialized = True

        # Set up axes properties (origin-centered coordinate system)
        self.ax.set_xlim(-self.half_space_size, self.half_space_size)
        self.ax.set_ylim(-self.half_space_size, self.half_space_size)
        self.ax.set_aspect('equal')
        self.ax.set_xlabel('X 座標')
        self.ax.set_ylabel('Y 座標')
        self.ax.grid(True, alpha=0.3)

    def draw_bars(self):
        """Draw all place areas with park-themed colors and image overlays.

        For most places: colored rectangle background + image icon at center.
        For walking_path: procedural drawing (tan fill + dashed center line).
        """
        for i, place in enumerate(self.places):
            half_size = place['half_size']
            center_x = place['center_x']
            center_y = place['center_y']
            if 'name' not in place:
                raise ValueError(f"Place at index {i} is missing required field: 'name'")
            place_name = place['name']
            place_type = place['type']

            face_color = PLACE_TYPE_COLORS.get(
                place_type, DEFAULT_PLACE_COLORS[i % len(DEFAULT_PLACE_COLORS)]
            )

            place_width = 2 * half_size + 1
            display_name = PLACE_DISPLAY_NAMES.get(place_type, place_name)

            # Standard place: colored rectangle + image icon
            place_rect = patches.Rectangle(
                (center_x - half_size - 0.5, center_y - half_size - 0.5),
                place_width,
                place_width,
                linewidth=BAR_LINEWIDTH,
                edgecolor='gray',
                facecolor=face_color,
                alpha=BAR_ALPHA,
                label=display_name,
            )
            self.ax.add_patch(place_rect)

            # Place name label (Japanese) at top-left of the area
            self.ax.text(
                center_x - half_size,
                center_y + half_size + 0.7,
                display_name,
                fontsize=9,
                ha='left',
                va='bottom',
                weight='bold',
                color='darkblue'
            )

            # Image icon — placed via imshow with explicit extent to fit within the place rectangle
            img_arr = self._place_images.get(place_type)
            if img_arr is not None:
                pad = 0.4  # leave a bit of the colored background visible at the edges
                extent = (
                    center_x - half_size + pad,
                    center_x + half_size - pad,
                    center_y - half_size + pad,
                    center_y + half_size - pad,
                )
                self.ax.imshow(
                    img_arr,
                    extent=extent,
                    zorder=3,
                    aspect='auto',
                    interpolation='bilinear',
                )
            else:
                # Fallback: text label at center
                self.ax.text(
                    center_x, center_y, place_type,
                    fontsize=9, ha='center', va='center',
                    weight='bold', color='darkblue'
                )

    def _draw_walking_path(self, center_x: int, center_y: int, half_size: int, place_name: str, place_type: str):
        """Procedural walking path: tan fill + dashed center line + dark borders."""
        place_width = 2 * half_size + 1
        # Background tan rectangle
        rect = patches.Rectangle(
            (center_x - half_size - 0.5, center_y - half_size - 0.5),
            place_width,
            place_width,
            linewidth=BAR_LINEWIDTH,
            edgecolor='saddlebrown',
            facecolor='wheat',
            alpha=0.55,
            label=f"{place_name} ({place_type})",
            zorder=1,
        )
        self.ax.add_patch(rect)

        # Dashed center line (horizontal across the path)
        self.ax.plot(
            [center_x - half_size, center_x + half_size],
            [center_y, center_y],
            color='saddlebrown',
            linestyle='--',
            linewidth=2,
            alpha=0.8,
            zorder=2,
        )
        # Side stripes (top and bottom edges)
        for offset in (-half_size + 0.3, half_size - 0.3):
            self.ax.plot(
                [center_x - half_size + 0.3, center_x + half_size - 0.3],
                [center_y + offset, center_y + offset],
                color='saddlebrown',
                linestyle='-',
                linewidth=1.2,
                alpha=0.5,
                zorder=2,
            )

        # Place name label
        self.ax.text(
            center_x - half_size,
            center_y + half_size + 0.7,
            place_name,
            fontsize=9,
            ha='left',
            va='bottom',
            weight='bold',
            color='saddlebrown'
        )
    
    def draw_fires(self, fire_states: List[Dict]):
        """Draw fire center markers and perception radius circles for all active fires.

        Fire areas are colored using a colormap based on intensity (0.0-1.0).
        """
        fire_cmap = matplotlib.colormaps['YlOrRd']

        for fire in fire_states:
            if not fire.get('active'):
                continue

            fx, fy = fire['position']
            radius = fire['radius']
            intensity = fire['intensity']
            name = fire.get('name', 'fire')

            # Map intensity to color via colormap
            face_color = fire_cmap(intensity)

            # Draw perception radius circle with intensity-based color
            fire_circle = patches.Circle(
                (fx, fy),
                radius,
                linewidth=FIRE_CIRCLE_LINEWIDTH,
                edgecolor='red',
                facecolor=face_color,
                alpha=FIRE_CIRCLE_ALPHA + 0.1,
                linestyle='--',
            )
            self.ax.add_patch(fire_circle)

            # Draw fire center marker
            self.ax.scatter(
                fx, fy,
                c='red',
                s=FIRE_MARKER_SIZE,
                marker='^',
                edgecolors='darkred',
                linewidths=2,
                zorder=10,
            )

            # Label
            self.ax.text(
                fx, fy - 1.5,
                f'{name}\n(int={intensity})',
                fontsize=8,
                ha='center',
                va='top',
                color='darkred',
                fontweight='bold',
            )

    def draw_agents(
        self,
        agents: List,
        agents_by_place: Dict[str, List[int]],
        communication_links: List[Tuple[int, int]] = None
    ):
        """Draw agents and communication links"""
        # Draw communication links
        if communication_links:
            for agent_id1, agent_id2 in communication_links:
                agent1 = agents[agent_id1]
                agent2 = agents[agent_id2]
                self.ax.plot(
                    [agent1.position[0], agent2.position[0]],
                    [agent1.position[1], agent2.position[1]],
                    'gray',
                    alpha=COMMUNICATION_LINK_ALPHA,
                    linewidth=1
                )
        
        # Draw agents using persona icons. Gold ring around icon if agent is in a place.
        for agent in agents:
            img_arr = self._persona_images.get(agent.id) if _PIL_AVAILABLE else None

            if img_arr is not None:
                imagebox = OffsetImage(img_arr, zoom=AGENT_ICON_ZOOM)
                bboxprops = (
                    dict(edgecolor=IN_PLACE_RING_COLOR, linewidth=IN_PLACE_RING_WIDTH, boxstyle="round,pad=0.05")
                    if (agent.in_place and agent.current_place)
                    else dict(edgecolor='none', linewidth=0, boxstyle="round,pad=0.05")
                )
                ab = AnnotationBbox(
                    imagebox,
                    (agent.position[0], agent.position[1]),
                    frameon=(agent.in_place and agent.current_place),
                    bboxprops=bboxprops,
                    box_alignment=(0.5, 0.5),
                    pad=0,
                    zorder=5,
                )
                self.ax.add_artist(ab)
            else:
                # Fallback: dot colored by gender
                color = 'blue' if agent.gender == 'male' else 'red'
                marker = '*' if agent.in_place else 'o'
                size = AGENT_SIZE_IN_BAR * 1.5 if agent.in_place else AGENT_SIZE_OUTSIDE
                self.ax.scatter(
                    agent.position[0], agent.position[1],
                    c=color, s=size, marker=marker, alpha=AGENT_ALPHA,
                    edgecolors='black', linewidths=1
                )

            # Add agent ID label (offset so it doesn't overlap icon)
            self.ax.text(
                agent.position[0] + 0.7,
                agent.position[1] + 0.7,
                str(agent.id),
                fontsize=7,
                ha='left',
                color='black',
                bbox=dict(boxstyle="round,pad=0.1", facecolor="white", edgecolor='gray', alpha=0.7),
                zorder=6,
            )
    
    def visualize_step(
        self,
        agents: List,
        place_status: Dict,
        step: int,
        communication_radius: float = None,
        save_path: str = None,
        fire_states: Optional[List[Dict]] = None
    ):
        """Visualize a single simulation step"""
        # For saving frames, create new figure each time and close after saving
        # For interactive display, reuse existing figure
        reuse = save_path is None and self.figure_initialized
        self.setup_figure(reuse_existing=reuse)
        self.draw_bars()
        self.draw_fires(fire_states or [])
        
        # Get agents by place
        agents_by_place = {}
        for place in self.places:
            agents_by_place[place['name']] = [agent.id for agent in agents 
                                          if agent.in_place and agent.current_place == place['name']]
        
        # Find communication links (same-area condition: same place or both outside)
        communication_links = []
        if communication_radius:
            for i, agent1 in enumerate(agents):
                for agent2 in agents[i+1:]:
                    dist = agent1.distance_to(agent2.position)
                    # Must be within radius AND in the same area:
                    # - Both outside places, OR
                    # - Both in the same place
                    same_area = (
                        (not agent1.in_place and not agent2.in_place) or
                        (agent1.in_place and agent2.in_place and 
                         agent1.current_place == agent2.current_place)
                    )
                    if dist <= communication_radius and same_area:
                        communication_links.append((agent1.id, agent2.id))
        
        self.draw_agents(agents, agents_by_place, communication_links)
        
        # Build title with statistics for all places (Japanese)
        # Map config place name -> place type, so we can translate to Japanese display name
        name_to_type = {p['name']: p['type'] for p in self.places}

        if 'places' in place_status:
            place_info = []
            for place_name, status in place_status['places'].items():
                ptype = name_to_type.get(place_name, place_name)
                disp = PLACE_DISPLAY_NAMES.get(ptype, place_name)
                place_info.append(
                    f"{disp}: {status['agents_in_place']}/{status['capacity']} "
                    f"({status['occupancy_rate']:.0%})"
                )
            title = (
                f"ステップ {step} | "
                f"場内合計: {place_status['agents_in_place']} "
                f"({place_status['occupancy_rate']:.1%}) | "
                f"{' | '.join(place_info)}"
            )
        else:
            title = (
                f"ステップ {step} | "
                f"場内のエージェント: {place_status['agents_in_place']}/{place_status['capacity']} "
                f"({place_status['occupancy_rate']:.1%})"
            )
        # Append fire info to title if any active
        active_fires = [f for f in (fire_states or []) if f.get('active')]
        if active_fires:
            agents_in_any_fire = set()
            for fire in active_fires:
                for a in agents:
                    if a.distance_to(fire['position']) <= fire['radius']:
                        agents_in_any_fire.add(a.id)
            title += f" | 異常事象範囲内: {len(agents_in_any_fire)}"
        self.ax.set_title(title, fontsize=10, fontweight='bold')

        # Legend (Japanese)
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], marker='s', color='w', markerfacecolor='none',
                   markeredgecolor=IN_PLACE_RING_COLOR, markersize=12,
                   markeredgewidth=2, label='場内（金縁）'),
        ]
        if active_fires:
            for fire in active_fires:
                legend_elements.append(
                    Line2D([0], [0], marker='^', color='w', markerfacecolor='red',
                           markeredgecolor='darkred', markersize=10,
                           label=f"{fire.get('name', '異常事象')} (強度={fire['intensity']})")
                )
            legend_elements.append(
                Line2D([0], [0], color='red', linestyle='--', linewidth=1.5,
                       alpha=0.5, label='知覚範囲')
            )
        # Add place area legends from draw_bars (one per place, already in Japanese)
        for handle in self.ax.get_legend_handles_labels()[0]:
            legend_elements.append(handle)
        self.ax.legend(handles=legend_elements, loc='upper right', fontsize=7)

        # Fire intensity colorbar — only when fires are present (round 3 has none)
        if active_fires:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            fire_cmap = matplotlib.colormaps['YlOrRd']
            norm = mcolors.Normalize(vmin=0.0, vmax=1.0)
            sm = plt.cm.ScalarMappable(cmap=fire_cmap, norm=norm)
            sm.set_array([])
            divider = make_axes_locatable(self.ax)
            cax = divider.append_axes("right", size="3%", pad=0.1)
            cbar = self.fig.colorbar(sm, cax=cax)
            cbar.set_label('異常事象の強度', fontsize=10)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=DPI, bbox_inches='tight')
            # Close figure after saving to prevent memory leak
            plt.close(self.fig)
            self.fig = None
            self.ax = None
        else:
            self._display_interactive(step)
    
    def _display_interactive(self, step: int):
        """Display visualization interactively"""
        backend = matplotlib.get_backend()
        is_gui_backend = backend.lower() not in NON_GUI_BACKENDS
        
        if is_gui_backend:
            # Use interactive mode for GUI backends
            plt.ion()  # Turn on interactive mode (allows non-blocking display)
            
            if not self.figure_initialized:
                # First time: create and show window
                plt.show(block=False)
                time.sleep(INITIAL_WINDOW_DELAY)
                logger.info(f"Created visualization window for step {step}")
                self.figure_initialized = True
            else:
                # Update existing window
                plt.draw()
                # Force GUI to process events and update display
                if hasattr(self.fig.canvas, 'flush_events'):
                    self.fig.canvas.flush_events()
            
            # Small pause to ensure window is updated
            plt.pause(VISUALIZATION_PAUSE)
            logger.debug(f"Updated visualization for step {step}")
        else:
            # Non-GUI backend (WSL, headless): just draw without showing
            plt.draw()
            logger.debug(f"Drew visualization for step {step} (non-GUI backend: {backend})")
            logger.warning("GUI backend not available. Use --save-frames to save visualization images.")
    
    def plot_statistics(
        self,
        stats: Dict,
        save_path: Optional[str] = None,
        occupancy_threshold: float = DEFAULT_OCCUPANCY_THRESHOLD,
        agent_threshold: int = DEFAULT_AGENT_THRESHOLD,
        fire_states: Optional[List[Dict]] = None
    ):
        """Plot simulation statistics"""
        # Determine number of subplots based on number of places
        num_places = len(self.places) if hasattr(self, 'places') else 1
        has_fire = 'agents_in_fire_radius' in stats and any(v > 0 for v in stats['agents_in_fire_radius'])
        num_plots = 2 + num_places + (1 if has_fire else 0)
        
        fig, axes = plt.subplots(num_plots, 1, figsize=STATS_FIGURE_SIZE)
        if num_plots == 1:
            axes = [axes]
        
        plot_idx = 0
        
        # Plot overall place occupancy over time
        if 'place_occupancy' in stats and stats['place_occupancy']:
            steps = range(len(stats['place_occupancy']))
            axes[plot_idx].plot(steps, stats['place_occupancy'], 'b-', alpha=0.7, label='全体の占有率')
            axes[plot_idx].set_xlabel('ステップ')
            axes[plot_idx].set_ylabel('場の占有率')
            axes[plot_idx].set_title('場全体の占有率の推移')
            axes[plot_idx].legend()
            axes[plot_idx].grid(True, alpha=0.3)
            axes[plot_idx].set_ylim(0, 1)
            plot_idx += 1

        # Plot overall number of agents in places over time
        if 'agents_in_place' in stats and stats['agents_in_place']:
            steps = range(len(stats['agents_in_place']))
            axes[plot_idx].plot(steps, stats['agents_in_place'], 'g-', alpha=0.7, label='場内のエージェント総数')
            axes[plot_idx].set_xlabel('ステップ')
            axes[plot_idx].set_ylabel('エージェント数')
            axes[plot_idx].set_title('場内のエージェント数の推移')
            axes[plot_idx].legend()
            axes[plot_idx].grid(True, alpha=0.3)
            max_agents = max(stats['agents_in_place']) if stats['agents_in_place'] else MAX_AGENTS_DISPLAY
            axes[plot_idx].set_ylim(0, max(MAX_AGENTS_DISPLAY, max_agents + 2))
            plot_idx += 1

        # Plot per-place statistics (Japanese display name)
        if 'places' in stats:
            place_colors = ['red', 'orange', 'green', 'purple', 'brown', 'teal', 'magenta']
            for i, place in enumerate(self.places):
                place_name = place['name']
                disp_name = PLACE_DISPLAY_NAMES.get(place['type'], place_name)
                if place_name in stats['places']:
                    place_stats = stats['places'][place_name]

                    if place_stats['occupancy']:
                        steps = range(len(place_stats['occupancy']))
                        color = place_colors[i % len(place_colors)]
                        axes[plot_idx].plot(
                            steps, place_stats['occupancy'],
                            color=color, alpha=0.7,
                            label=f'{disp_name} 占有率'
                        )

                    if place_stats['agents_in_place']:
                        steps = range(len(place_stats['agents_in_place']))
                        color = place_colors[i % len(place_colors)]
                        axes[plot_idx].plot(
                            steps, place_stats['agents_in_place'],
                            color=color, alpha=0.5, linestyle=':',
                            label=f'{disp_name} エージェント数'
                        )

                    axes[plot_idx].set_xlabel('ステップ')
                    axes[plot_idx].set_ylabel('占有率 / エージェント数')
                    axes[plot_idx].set_title(f'{disp_name} の推移')
                    axes[plot_idx].legend()
                    axes[plot_idx].grid(True, alpha=0.3)
                    axes[plot_idx].set_ylim(0, 1)
                    plot_idx += 1

        # Plot fire statistics (Japanese)
        if has_fire:
            steps = range(len(stats['agents_in_fire_radius']))
            axes[plot_idx].plot(
                steps, stats['agents_in_fire_radius'],
                'r-', alpha=0.7, label='異常事象範囲内のエージェント数'
            )
            if fire_states:
                for fire in fire_states:
                    if 'start_step' in fire:
                        fire_start_idx = fire['start_step'] - 1
                        fire_name = fire.get('name', '異常事象')
                        axes[plot_idx].axvline(
                            x=fire_start_idx, color='red', linestyle='--',
                            alpha=0.5, label=f'{fire_name} 開始'
                        )
            axes[plot_idx].set_xlabel('ステップ')
            axes[plot_idx].set_ylabel('エージェント数')
            axes[plot_idx].set_title('異常事象範囲内のエージェント数の推移')
            axes[plot_idx].legend()
            axes[plot_idx].grid(True, alpha=0.3)
            max_fire = max(stats['agents_in_fire_radius']) if stats['agents_in_fire_radius'] else MAX_AGENTS_DISPLAY
            axes[plot_idx].set_ylim(0, max(MAX_AGENTS_DISPLAY, max_fire + 2))
            plot_idx += 1

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=DPI, bbox_inches='tight')
            plt.close(fig)
        else:
            # Check if we have a GUI backend
            backend = matplotlib.get_backend()
            is_gui_backend = backend.lower() not in NON_GUI_BACKENDS
            
            if is_gui_backend:
                # Use non-blocking show for GUI backends
                plt.show(block=False)
                plt.pause(STATS_PAUSE)
            else:
                # Non-GUI backend: just draw without showing, then close
                plt.draw()
                plt.close(fig)
                logger.warning("GUI backend not available. Statistics plot not displayed. Use --save-frames to save.")

