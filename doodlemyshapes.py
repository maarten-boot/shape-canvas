#!/usr/bin/env python3
"""DoodleMyShapes - a shape editor with a persistent JSON state document.

Right-click empty canvas to add a rectangle (2 cm wide by 1 cm high) or a circle (2 cm across).
Right-click a shape to recolour it, restack it, group it or edit its properties. Shapes are
movable and resizable, snap to a 1 cm grid, and can be exported as PNG, JPEG or SVG.

Geometry lives in the model, in model units fixed at 96 dpi. The canvas is a view rendered from
the model and is never read back as a source of truth, so every rule here can be exercised with
no display attached.

Usage:
    doodlemyshapes.py [--clean] [--file STATE.json] [--state-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import tkinter as tk
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Any, Literal
from xml.sax.saxutils import escape

# ------------------------------------------------------------------ identity

PROGRAM_NAME = "DoodleMyShapes"  # never derived from the filename
PROGRAM_VERSION = "v1"
DEFAULT_STATE_DIR = Path.home() / ".doodlemyshapes"
STATE_FILENAME = "state.json"
STATE_SUFFIX = ".json"

# ------------------------------------------------- coordinates and constants

# The model stores geometry in model units at a fixed scale. 1 cm is always this many model
# units, on every machine; the display's real DPI only scales what is drawn on screen.
MODEL_PX_PER_CM = 37.795275590551181  # 96 dpi, the same definition CSS uses

RECT_W_CM = 2.0
RECT_H_CM = 1.0
CIRCLE_D_CM = 2.0
MIN_SIZE = 12.0  # absolute floor for a shape's width or height, in model units
HANDLE_RADIUS = 4.0  # half a resize handle's side, in model units

CANVAS_BG = "#f4f4f4"
SHAPE_FILL = "#d3d3d3"
SHAPE_OUTLINE = "#5a5a5a"
GRID_COLOR = "#cfe0f5"
HANDLE_FILL = "#ffffff"
HANDLE_OUTLINE = "#1f6feb"
MARQUEE_DASH = (3, 2)
BAND_DASH = (4, 3)
BAND_STIPPLE = "gray12"
BAND_MIN_DRAG = 3.0  # a drag shorter than this in screen pixels is just a click
INK_DARK = "#101010"
INK_LIGHT = "#ffffff"

FILL_COLORS: tuple[tuple[str, str], ...] = (
    ("Light gray", "#d3d3d3"),
    ("White", "#ffffff"),
    ("Slate", "#8b98a5"),
    ("Red", "#e2726e"),
    ("Amber", "#e8b04b"),
    ("Green", "#7bb661"),
    ("Blue", "#6c9bd2"),
    ("Violet", "#a184c8"),
)

GRID_LINE_TYPES: dict[str, tuple[int, ...]] = {
    "Solid": (),
    "Stippled": (1, 3),
    "Dotted": (2, 4),
    "Dashed": (6, 4),
    "Dash-dot": (8, 3, 2, 3),
}
GRID_LINE_DEFAULT = "Stippled"

GRID_CM = 1.0
GRID_CM_CHOICES = (0.5, 1.0, 2.0, 5.0)
GRID_CM_MIN = 0.1
GRID_CM_MAX = 20.0

HISTORY_LIMIT = 50
GRID_LINE_LIMIT = 400  # stop drawing the grid rather than fill the canvas with lines
SPACE_RELEASE_GRACE_MS = 60  # X11 auto-repeat sends release/press pairs; wait before believing one
RESIZE_SETTLE_MS = 250  # quiet time after the last <Configure> before a resize counts as finished
SWATCH_SIZE = 18

EXPORT_MARGIN_CM = 1.0
EXPORT_FILETYPES: tuple[tuple[str, str, str], ...] = (
    ("PNG image", ".png", "*.png"),
    ("JPEG image", ".jpg", "*.jpg *.jpeg"),
    ("SVG drawing", ".svg", "*.svg"),
)
EXPORT_FORMATS = (".png", ".jpg", ".jpeg", ".svg")
EXPORT_UNSUPPORTED = (".bmp", ".gif", ".tif", ".tiff", ".webp", ".pdf", ".eps", ".ps", ".ico", ".heic")
EXPORT_FONT_SIZE = 12  # model units
EXPORT_FONTS = (
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
)

WINDOW_SIZE = "1060x680"
WINDOW_MIN = (720, 420)
PANE_WIDTH = 250

TAG_SHAPE = "shape"
TAG_HANDLE = "handle"
TAG_GRID = "grid"
TAG_LABEL = "label"
TAG_MARK = "marquee"
TAG_GROUP_LABEL = "group-label"
TAG_BAND = "rubber-band"

HANDLE_CURSORS = {
    "nw": "top_left_corner",
    "n": "top_side",
    "ne": "top_right_corner",
    "e": "right_side",
    "se": "bottom_right_corner",
    "s": "bottom_side",
    "sw": "bottom_left_corner",
    "w": "left_side",
}

Box = tuple[float, float, float, float]


def ink_for(fill: str) -> str:
    """Pick a legible text colour for a fill. Works without a display."""
    text = fill.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(channel * 2 for channel in text)
    if len(text) != 6:
        return INK_DARK
    try:
        red, green, blue = (int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return INK_DARK
    luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    return INK_DARK if luminance > 0.55 else INK_LIGHT


@dataclass
class ShapeRecord:
    """One shape. This owns its geometry; the canvas only draws what it says."""

    kind: str  # "rectangle" or "circle"
    x: float
    y: float
    width: float
    height: float
    fill: str = SHAPE_FILL
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    depth: int = 0
    group: str = ""
    show_name: bool = True  # a shape shows its name by default

    @property
    def box(self) -> Box:
        return self.x, self.y, self.x + self.width, self.y + self.height

    def set_box(self, box: Box) -> None:
        x1, y1, x2, y2 = box
        self.x, self.y = min(x1, x2), min(y1, y2)
        self.width, self.height = abs(x2 - x1), abs(y2 - y1)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def to_json(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "fill": self.fill,
            "depth": self.depth,
            "group": self.group or None,
            "show_name": self.show_name,
            "position": {"x": round(self.x, 2), "y": round(self.y, 2)},
            "size": {"width": round(self.width, 2), "height": round(self.height, 2)},
        }

    @classmethod
    def from_json(cls, entry: object, fallback_depth: int, floor: float) -> ShapeRecord | None:
        """Build a record from one state-file entry, or None when it is unusable."""
        if not isinstance(entry, dict):
            return None
        position, size = entry.get("position"), entry.get("size")
        if not isinstance(position, dict) or not isinstance(size, dict):
            return None
        try:
            x, y = float(position["x"]), float(position["y"])
            width, height = float(size["width"]), float(size["height"])
        except (KeyError, TypeError, ValueError):
            return None

        depth = entry.get("depth")
        return cls(
            kind="rectangle" if entry.get("kind") == "rectangle" else "circle",
            x=x,
            y=y,
            width=max(width, floor),
            height=max(height, floor),
            fill=str(entry.get("fill") or SHAPE_FILL),
            uuid=str(entry.get("uuid") or uuid.uuid4()),
            name=str(entry.get("name") or ""),
            description=str(entry.get("description") or ""),
            depth=depth if isinstance(depth, int) and not isinstance(depth, bool) else fallback_depth,
            group=str(entry.get("group") or ""),
            show_name=bool(entry.get("show_name", True)),
        )


@dataclass
class GroupRecord:
    """A group of shapes. Membership lives on the shapes; this holds the group's own properties."""

    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    show_name: bool = False  # a group keeps its name hidden by default
    parent: str = ""  # uuid of the group this one sits inside, empty when it is top level

    def to_json(self, members: list[str], subgroups: list[str]) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "description": self.description,
            "show_name": self.show_name,
            "parent": self.parent or None,
            "members": members,  # shapes directly in this group
            "subgroups": subgroups,  # groups directly in this group
        }


class DoodleMyShapes:
    """Menu bar, tabbed canvas with a properties pane, and a status line."""

    def __init__(
        self,
        root: tk.Tk,
        clean: bool = False,
        startup_file: Path | None = None,
        state_dir: Path = DEFAULT_STATE_DIR,
    ) -> None:
        self.root = root
        self.root.title(PROGRAM_NAME)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(*WINDOW_MIN)

        self.state_dir = state_dir
        self.state_file = state_dir / STATE_FILENAME

        # --- the model ---------------------------------------------------
        self.shapes: dict[str, ShapeRecord] = {}  # uuid -> record, the only source of geometry
        self.groups: dict[str, GroupRecord] = {}  # group uuid -> its own name and description

        # --- the view -----------------------------------------------------
        self.items: dict[str, int] = {}  # uuid -> canvas shape item
        self.labels: dict[str, int] = {}  # shape uuid -> canvas text item
        self.group_labels: dict[str, int] = {}  # group uuid -> canvas text item
        self.owners: dict[int, str] = {}  # canvas shape item -> uuid

        # --- interaction ---------------------------------------------------
        self.selection: list[str] = []
        self.selected: str | None = None
        self.pane_target: tuple[str, str] | None = None  # ("shape" | "group", uuid)
        self.drag_mode: str | None = None
        self.drag_handle: str | None = None
        self.drag_start: tuple[float, float] = (0.0, 0.0)
        self.drag_bbox: Box = (0.0, 0.0, 0.0, 0.0)
        self.drag_boxes: dict[str, Box] = {}
        self.drag_min: tuple[float, float] = (MIN_SIZE, MIN_SIZE)
        self.menu_point: tuple[float, float] = (0.0, 0.0)

        # --- the viewport ---------------------------------------------------
        # Panning moves the window onto the model; it never touches the shapes, so it is
        # not part of the document, not undoable, and negative model coordinates are fine.
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.space_held = False
        self.space_release_job: str | None = None
        self.pan_from: tuple[float, float] = (0.0, 0.0)  # pointer in screen units when the pan began
        self.pan_origin: tuple[float, float] = (0.0, 0.0)
        self.band_origin: tuple[float, float] = (0.0, 0.0)  # in model units
        self.band_base: list[str] = []  # selection to add to, when the band is additive
        self.canvas_size: tuple[int, int] | None = None  # None until the first layout
        self.resize_job: str | None = None
        self.pane_visible = True
        self.self_inflicted_layout = False  # set while the pane is showing or hiding itself

        # --- history and preferences -----------------------------------------
        self.suspend_autosave = False
        self.suspend_history = False
        self.history: list[tuple[str, dict[str, Any]]] = []
        self.baseline: dict[str, Any] | None = None

        self.show_grid = tk.BooleanVar(master=root, value=True)
        self.grid_color = tk.StringVar(master=root, value=GRID_COLOR)
        self.grid_line = tk.StringVar(master=root, value=GRID_LINE_DEFAULT)
        self.grid_cm = tk.DoubleVar(master=root, value=GRID_CM)
        self.confirm_deletes = tk.BooleanVar(master=root, value=True)
        self.color_groups = tk.BooleanVar(master=root, value=False)  # colouring a whole group is opt-in

        self._build_menu_bar()
        self._build_central_frame()
        self._build_status_line()
        self._bind_events()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.set_status("Right-click the canvas to add a shape.")
        self.startup_load(clean=clean, startup_file=startup_file)
        self.history.clear()  # whatever we started with is the starting point, not a change
        self.baseline = self.to_state()
        self._refresh_undo_button()
        self.refresh_json_view()

    # ------------------------------------------------------------------ setup

    def _build_menu_bar(self) -> None:
        menu_bar = tk.Menu(self.root)

        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Open state...", accelerator="Ctrl+O", command=self.load_state_dialog)
        file_menu.add_command(label="Save state as...", accelerator="Ctrl+S", command=self.save_state_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Export as picture...", accelerator="Ctrl+E", command=self.export_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Clear canvas", accelerator="Ctrl+N", command=self.clear_canvas)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_close)
        menu_bar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menu_bar, tearoff=False)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_separator()
        edit_menu.add_checkbutton(
            label="Confirm before deleting",
            variable=self.confirm_deletes,
            command=self.on_confirm_setting_changed,
        )
        edit_menu.add_checkbutton(
            label="Allow coloring a whole group",
            variable=self.color_groups,
            command=self.on_color_groups_changed,
        )
        menu_bar.add_cascade(label="Edit", menu=edit_menu)

        shape_menu = tk.Menu(menu_bar, tearoff=False)
        shape_menu.add_command(label="Add rectangle", command=lambda: self.add_shape_at_center("rectangle"))
        shape_menu.add_command(label="Add circle", command=lambda: self.add_shape_at_center("circle"))
        shape_menu.add_separator()
        shape_menu.add_command(label="Group selection", accelerator="Ctrl+Shift+G", command=self.group_selection)
        shape_menu.add_command(label="Ungroup", accelerator="Ctrl+Shift+U", command=self.ungroup_selection)
        shape_menu.add_separator()
        shape_menu.add_command(label="Delete selected", accelerator="Del", command=self.delete_selected)
        menu_bar.add_cascade(label="Shape", menu=shape_menu)

        view_menu = tk.Menu(menu_bar, tearoff=False)
        grid_menu = tk.Menu(view_menu, tearoff=False)
        grid_menu.add_checkbutton(
            label="Show grid", accelerator="Ctrl+G", variable=self.show_grid, command=self.toggle_grid
        )
        grid_menu.add_separator()
        grid_menu.add_command(label="Grid color...", command=self.choose_grid_color)

        line_menu = tk.Menu(grid_menu, tearoff=False)
        for line_type in GRID_LINE_TYPES:
            line_menu.add_radiobutton(
                label=line_type, value=line_type, variable=self.grid_line, command=self.apply_grid_style
            )
        grid_menu.add_cascade(label="Line type", menu=line_menu)

        size_menu = tk.Menu(grid_menu, tearoff=False)
        for choice in GRID_CM_CHOICES:
            size_menu.add_radiobutton(
                label=f"{choice:g} cm", value=choice, variable=self.grid_cm, command=self.apply_grid_size
            )
        size_menu.add_separator()
        size_menu.add_command(label="Custom size...", command=self.choose_grid_size)
        grid_menu.add_cascade(label="Grid size", menu=size_menu)

        view_menu.add_cascade(label="Grid", menu=grid_menu)
        view_menu.add_separator()
        view_menu.add_command(label="Center drawing", accelerator="Ctrl+0", command=self.center_drawing)
        view_menu.add_separator()
        view_menu.add_command(label="Canvas tab", command=lambda: self.show_tab(0))
        view_menu.add_command(label="Json tab", command=lambda: self.show_tab(1))
        menu_bar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="How it works", command=self.show_help)
        menu_bar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menu_bar)

    def _build_central_frame(self) -> None:
        self.central_frame = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        self.central_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(self.central_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        canvas_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(canvas_tab, text="Canvas")
        canvas_tab.rowconfigure(0, weight=1)
        canvas_tab.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_tab, background=CANVAS_BG, highlightthickness=1, highlightbackground="#c8c8c8")
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)

        self._build_properties_pane(canvas_tab)
        self._build_json_tab()
        self._build_popups()

    def _build_properties_pane(self, parent: ttk.Frame) -> None:
        self.pane = ttk.Frame(parent, padding=(10, 4, 0, 0), width=PANE_WIDTH)
        pane = self.pane
        pane.grid(row=0, column=1, sticky=tk.NS)
        pane.grid_propagate(False)
        pane.columnconfigure(0, weight=1)

        ttk.Label(pane, text="Properties", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky=tk.W)
        self.pane_subject = ttk.Label(pane, text="No shape selected", foreground="#767676", wraplength=230)
        self.pane_subject.grid(row=1, column=0, sticky=tk.W, pady=(2, 10))

        ttk.Label(pane, text="Fill").grid(row=2, column=0, sticky=tk.W)
        swatches = ttk.Frame(pane)
        swatches.grid(row=3, column=0, sticky=tk.W, pady=(2, 2))
        self.fill_swatches: dict[str, tk.Frame] = {}
        for name, color in FILL_COLORS:
            swatch = tk.Frame(
                swatches,
                background=color,
                width=SWATCH_SIZE,
                height=SWATCH_SIZE,
                highlightthickness=2,
                highlightbackground=CANVAS_BG,
                cursor="hand2",
            )
            swatch.pack(side=tk.LEFT, padx=1)
            swatch.bind("<Button-1>", self._swatch_setter(color))
            self.fill_swatches[color] = swatch
        self.custom_fill_button = ttk.Button(pane, text="Custom color...", command=self.choose_custom_fill)
        self.custom_fill_button.grid(row=4, column=0, sticky=tk.EW, pady=(2, 10))

        ttk.Label(pane, text="Name").grid(row=5, column=0, sticky=tk.W)
        self.pane_name = ttk.Entry(pane)
        self.pane_name.grid(row=6, column=0, sticky=tk.EW, pady=(2, 10))

        ttk.Label(pane, text="Description").grid(row=7, column=0, sticky=tk.W)
        description_box = ttk.Frame(pane)
        description_box.grid(row=8, column=0, sticky=tk.NSEW, pady=(2, 8))
        description_box.rowconfigure(0, weight=1)
        description_box.columnconfigure(0, weight=1)
        pane.rowconfigure(8, weight=1)

        self.pane_description = tk.Text(description_box, width=24, height=8, wrap=tk.WORD, font=("TkDefaultFont",))
        scroll = ttk.Scrollbar(description_box, orient=tk.VERTICAL, command=self.pane_description.yview)
        self.pane_description.configure(yscrollcommand=scroll.set)
        self.pane_description.grid(row=0, column=0, sticky=tk.NSEW)
        scroll.grid(row=0, column=1, sticky=tk.NS)

        visibility = ttk.Frame(pane)
        visibility.grid(row=9, column=0, sticky=tk.EW, pady=(0, 8))
        ttk.Label(visibility, text="Name label").pack(side=tk.LEFT)
        self.pane_show = tk.BooleanVar(master=pane, value=True)
        self.pane_show_text = tk.StringVar(master=pane, value="Show")
        self.show_button = ttk.Checkbutton(
            visibility,
            style="Toolbutton",
            textvariable=self.pane_show_text,
            variable=self.pane_show,
            command=self.on_show_toggled,
        )
        self.show_button.pack(side=tk.RIGHT)

        self.pane_apply = ttk.Button(pane, text="Apply", command=self.commit_pane)
        self.pane_apply.grid(row=10, column=0, sticky=tk.E)

        # Typed text is kept when focus leaves the field, so edits are not lost by clicking away.
        self.pane_name.bind("<Return>", lambda _event: self.commit_pane())
        self.pane_name.bind("<FocusOut>", lambda _event: self.commit_pane())
        self.pane_description.bind("<FocusOut>", lambda _event: self.commit_pane())
        self.pane_name.bind("<Escape>", lambda _event: self.show_in_pane(self.pane_target))
        self.pane_description.bind("<Escape>", lambda _event: self.show_in_pane(self.pane_target))

        self.show_in_pane(None)

    def _build_json_tab(self) -> None:
        json_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(json_tab, text="Json")
        json_tab.rowconfigure(0, weight=1)
        json_tab.columnconfigure(0, weight=1)

        self.json_text = tk.Text(
            json_tab,
            wrap=tk.NONE,
            font=("TkFixedFont",),
            background="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#c8c8c8",
            padx=8,
            pady=6,
        )
        vertical = ttk.Scrollbar(json_tab, orient=tk.VERTICAL, command=self.json_text.yview)
        horizontal = ttk.Scrollbar(json_tab, orient=tk.HORIZONTAL, command=self.json_text.xview)
        self.json_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        self.json_text.grid(row=0, column=0, sticky=tk.NSEW)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        self.json_text.configure(state=tk.DISABLED)  # look, don't type

    def _build_popups(self) -> None:
        self.canvas_popup = tk.Menu(self.canvas, tearoff=False)
        self.canvas_popup.add_command(label="Rectangle", command=lambda: self.add_shape_at_menu_point("rectangle"))
        self.canvas_popup.add_command(label="Circle", command=lambda: self.add_shape_at_menu_point("circle"))
        self.canvas_popup.add_separator()
        self.canvas_popup.add_command(label="Center drawing", command=self.center_drawing)

        self.shape_popup = tk.Menu(self.canvas, tearoff=False)
        self.color_menu = tk.Menu(self.shape_popup, tearoff=False)
        for name, color in FILL_COLORS:
            self.color_menu.add_command(
                label=name, background=color, activebackground=color, command=self._fill_setter(color)
            )
        self.color_menu.add_separator()
        self.color_menu.add_command(label="Custom color...", command=self.choose_custom_fill)
        self.shape_popup.add_cascade(label="Color", menu=self.color_menu)

        self.depth_menu = tk.Menu(self.shape_popup, tearoff=False)
        self.depth_menu.add_command(label="Bring to top", command=lambda: self.set_depth("top"))
        self.depth_menu.add_command(label="Move up one", command=lambda: self.set_depth("up"))
        self.depth_menu.add_command(label="Move down one", command=lambda: self.set_depth("down"))
        self.depth_menu.add_command(label="Send to bottom", command=lambda: self.set_depth("bottom"))
        self.shape_popup.add_cascade(label="Depth", menu=self.depth_menu)

        self.shape_extras = 2  # Color and Depth are always there; the rest is rebuilt per click

        # Releasing the right button while no entry is highlighted takes the menu back down.
        for menu in (self.canvas_popup, self.shape_popup, self.color_menu, self.depth_menu):
            menu.bind("<ButtonRelease-3>", self.dismiss_popup)

    def _build_status_line(self) -> None:
        bar = ttk.Frame(self.root, padding=(6, 2))
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Undo lives here rather than in the properties pane, because the pane hides itself
        # when nothing is selected and undo has to stay reachable.
        self.undo_button = ttk.Button(bar, text="Undo", command=self.undo, state=tk.DISABLED, width=8)
        self.undo_button.pack(side=tk.LEFT, padx=(0, 6))

        self.status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W, padding=(8, 3)).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.configure(takefocus=True)

        # Space arms panning wherever the keyboard happens to be, except inside a text field,
        # so there is no need to click the canvas first.
        self.root.bind_all("<KeyPress-space>", self.on_space_down)
        self.root.bind_all("<KeyRelease-space>", self.on_space_up)

        # The middle button pans too, with no key involved at all.
        self.canvas.bind("<Button-2>", self.on_middle_press)
        self.canvas.bind("<B2-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-2>", self.on_middle_release)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self.refresh_json_view())

        self.canvas.bind("<Button-3>", self.on_popup)  # right click only; the middle button is free

        self.root.bind("<Delete>", lambda _event: self.delete_selected())
        self.root.bind("<BackSpace>", lambda _event: self.delete_selected())
        self.root.bind("<Escape>", lambda _event: self.select(None))
        self.root.bind("<Control-n>", lambda _event: self.clear_canvas())
        self.root.bind("<Control-o>", lambda _event: self.load_state_dialog())
        self.root.bind("<Control-s>", lambda _event: self.save_state_dialog())
        self.root.bind("<Control-e>", lambda _event: self.export_dialog())
        self.root.bind("<Control-q>", lambda _event: self.on_close())
        self.root.bind("<Control-z>", lambda _event: self.undo())
        self.root.bind("<Control-g>", lambda _event: self.toggle_grid(flip=True))
        self.root.bind("<Control-Key-0>", lambda _event: self.center_drawing())
        self.root.bind("<Destroy>", self.on_destroy)
        self.root.bind("<Control-G>", lambda _event: self.group_selection())  # Ctrl+Shift+G
        self.root.bind("<Control-U>", lambda _event: self.ungroup_selection())
        self.root.bind("<Control-Up>", lambda _event: self.set_depth("up"))
        self.root.bind("<Control-Down>", lambda _event: self.set_depth("down"))
        self.root.bind("<Control-Shift-Up>", lambda _event: self.set_depth("top"))
        self.root.bind("<Control-Shift-Down>", lambda _event: self.set_depth("bottom"))

    # ------------------------------------------------------------------ units

    @property
    def display_scale(self) -> float:
        """Screen pixels per model unit. Never persisted, never exported."""
        try:
            return float(self.root.winfo_fpixels("1c")) / MODEL_PX_PER_CM
        except tk.TclError:
            return 1.0

    def length_to_screen(self, value: float) -> float:
        """Scale a distance. Distances are unaffected by where the view is panned to."""
        return value * self.display_scale

    def length_to_model(self, value: float) -> float:
        scale = self.display_scale
        return value / scale if scale else value

    def point_to_screen(self, x: float, y: float) -> tuple[float, float]:
        """Place a model point on the canvas, taking the pan offset into account."""
        scale = self.display_scale
        return (x - self.pan_x) * scale, (y - self.pan_y) * scale

    def point_to_model(self, sx: float, sy: float) -> tuple[float, float]:
        scale = self.display_scale or 1.0
        return sx / scale + self.pan_x, sy / scale + self.pan_y

    def screen_box(self, box: Box) -> Box:
        x1, y1 = self.point_to_screen(box[0], box[1])
        x2, y2 = self.point_to_screen(box[2], box[3])
        return x1, y1, x2, y2

    def viewport(self) -> Box:
        """The part of the model currently visible, in model units."""
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        left, top = self.point_to_model(0, 0)
        right, bottom = self.point_to_model(width, height)
        return left, top, right, bottom

    def pointer(self, event: tk.Event) -> tuple[float, float]:
        """Pointer position in model units."""
        return self.point_to_model(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    @staticmethod
    def cm_to_model(value: float) -> float:
        return value * MODEL_PX_PER_CM

    @staticmethod
    def model_to_cm(value: float) -> float:
        return value / MODEL_PX_PER_CM

    # ------------------------------------------------------------------- grid

    @property
    def cell(self) -> float:
        """Grid pitch in model units."""
        return self.cm_to_model(self.grid_pitch())

    @property
    def min_extent(self) -> float:
        """Smallest allowed width or height: a whole number of cells, so it stays on the grid."""
        cell = self.cell
        return max(1, math.ceil(MIN_SIZE / cell)) * cell

    def grid_pitch(self) -> float:
        """The grid pitch in centimetres, kept inside sane bounds."""
        try:
            pitch = float(self.grid_cm.get())
        except (tk.TclError, ValueError):
            pitch = GRID_CM
        return min(max(pitch, GRID_CM_MIN), GRID_CM_MAX)

    def snap(self, value: float) -> float:
        cell = self.cell
        return round(value / cell) * cell

    def draw_grid(self) -> None:
        """(Re)draw the grid across whatever part of the model is currently in view."""
        self.canvas.delete(TAG_GRID)
        if not self.show_grid.get():
            return

        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        step = self.length_to_screen(self.cell)
        if step < 2 or width < 2 or height < 2:  # not mapped yet, or an absurd pitch
            return

        color = self.grid_color.get()
        dash = GRID_LINE_TYPES.get(self.grid_line.get(), GRID_LINE_TYPES[GRID_LINE_DEFAULT])
        pattern = dash if dash else ""  # Tk wants an empty pattern, not (), for a solid line

        cell = self.cell
        left, top, right, bottom = self.viewport()
        columns = range(math.ceil(left / cell), math.floor(right / cell) + 1)
        rows = range(math.ceil(top / cell), math.floor(bottom / cell) + 1)
        if len(columns) + len(rows) > GRID_LINE_LIMIT:  # a wash of lines helps nobody
            return

        for index in columns:
            x, _ = self.point_to_screen(index * cell, 0)
            self.canvas.create_line(x, 0, x, height, fill=color, dash=pattern, tags=(TAG_GRID,))
        for index in rows:
            _, y = self.point_to_screen(0, index * cell)
            self.canvas.create_line(0, y, width, y, fill=color, dash=pattern, tags=(TAG_GRID,))

        self.canvas.tag_lower(TAG_GRID)  # always behind the shapes

    def choose_grid_color(self) -> None:
        _rgb, chosen = colorchooser.askcolor(color=self.grid_color.get(), title="Grid color", parent=self.root)
        if chosen:
            self.grid_color.set(str(chosen))
            self.apply_grid_style()

    def apply_grid_style(self) -> None:
        if not self.show_grid.get():
            self.show_grid.set(True)  # changing the look implies wanting to see it
        self.draw_grid()
        self.autosave("grid style")
        self.set_status(f"Grid: {self.grid_line.get().lower()} lines in {self.grid_color.get()}.")

    def choose_grid_size(self) -> None:
        pitch = simpledialog.askfloat(
            "Grid size",
            f"Grid pitch in centimetres ({GRID_CM_MIN:g}-{GRID_CM_MAX:g}):",
            parent=self.root,
            initialvalue=self.grid_pitch(),
            minvalue=GRID_CM_MIN,
            maxvalue=GRID_CM_MAX,
        )
        if pitch is not None:
            self.grid_cm.set(pitch)
            self.apply_grid_size()

    def apply_grid_size(self) -> None:
        """Redraw at the new pitch. Shapes already placed keep their positions."""
        self.grid_cm.set(self.grid_pitch())  # write the clamped value back
        self.draw_grid()
        self.autosave("grid size")
        self.set_status(f"Grid size {self.grid_pitch():g} cm. Shapes already placed were not moved.")

    def toggle_grid(self, flip: bool = False) -> None:
        """Show or hide the grid. Alignment is unaffected; only the lines come and go."""
        if flip:
            self.show_grid.set(not self.show_grid.get())
        self.draw_grid()
        self.autosave("grid visibility")
        self.set_status("Grid shown." if self.show_grid.get() else "Grid hidden. Shapes still align to it.")

    # ------------------------------------------------------------- the canvas

    def render(self, identifier: str) -> None:
        """Create or update the canvas items for one shape, from its record."""
        record = self.shapes.get(identifier)
        if record is None:
            return

        box = self.screen_box(record.box)
        item = self.items.get(identifier)
        if item is None:
            create = self.canvas.create_rectangle if record.kind == "rectangle" else self.canvas.create_oval
            item = create(*box, fill=record.fill, outline=SHAPE_OUTLINE, width=1, tags=(TAG_SHAPE,))
            self.items[identifier] = item
            self.owners[item] = identifier
        else:
            self.canvas.coords(item, *box)
            self.canvas.itemconfigure(item, fill=record.fill)
        self.render_label(identifier)

    def render_label(self, identifier: str) -> None:
        """Draw the shape's name on it, or take the text away when the name is empty."""
        record = self.shapes.get(identifier)
        existing = self.labels.pop(identifier, None)
        if existing is not None:
            self.canvas.delete(existing)
        if record is None or not record.name or not record.show_name:
            return

        cx, cy = record.center
        label = self.canvas.create_text(
            *self.point_to_screen(cx, cy),
            text=record.name,
            fill=ink_for(record.fill),
            width=max(self.length_to_screen(record.width - 6), 10),  # wrap inside the shape
            justify=tk.CENTER,
            tags=(TAG_LABEL,),
        )
        self.labels[identifier] = label
        item = self.items.get(identifier)
        if item is not None:
            self.canvas.tag_raise(label, item)

    def group_box(self, group: str) -> Box | None:
        """Bounding box of everything beneath a group, nested groups included."""
        boxes = [self.shapes[member].box for member in self.group_shapes(group)]
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    def render_group_labels(self) -> None:
        """Draw the name of every group that is showing one, centred on the group."""
        self.canvas.delete(TAG_GROUP_LABEL)
        self.group_labels.clear()
        for identifier, group in self.groups.items():
            members = self.group_shapes(identifier)
            if not group.name or not group.show_name or not members:
                continue
            box = self.group_box(identifier)
            if box is None:
                continue
            x1, y1, x2, y2 = box
            self.group_labels[identifier] = self.canvas.create_text(
                *self.point_to_screen((x1 + x2) / 2, (y1 + y2) / 2),
                text=group.name,
                fill=ink_for(self.shapes[members[-1]].fill),  # legible over the topmost member
                width=max(self.length_to_screen(x2 - x1 - 6), 10),
                justify=tk.CENTER,
                tags=(TAG_GROUP_LABEL,),
            )

    def forget(self, identifier: str) -> None:
        """Remove one shape's canvas items."""
        item = self.items.pop(identifier, None)
        if item is not None:
            self.owners.pop(item, None)
            self.canvas.delete(item)
        label = self.labels.pop(identifier, None)
        if label is not None:
            self.canvas.delete(label)

    def redraw_all(self) -> None:
        """Rebuild the whole view from the model."""
        self.canvas.delete(TAG_SHAPE)
        self.canvas.delete(TAG_LABEL)
        self.canvas.delete(TAG_GROUP_LABEL)
        self.canvas.delete(TAG_HANDLE)
        self.canvas.delete(TAG_MARK)
        self.canvas.delete(TAG_BAND)
        self.items.clear()
        self.labels.clear()
        self.group_labels.clear()
        self.owners.clear()
        for identifier in self.by_depth():
            self.render(identifier)
        self.draw_grid()
        self.restack()

    def nesting_depth(self, group: str) -> int:
        """How many groups enclose this one. Zero at the top level."""
        depth, current, seen = 0, group, {group}
        while current in self.groups and self.groups[current].parent:
            current = self.groups[current].parent
            if current in seen:
                break
            seen.add(current)
            depth += 1
        return depth

    def label_anchors(self) -> dict[str, list[str]]:
        """Shape -> the group labels that ride at that shape's depth.

        A group's name belongs at the group's own depth, which is the depth of its topmost
        member. Anything stacked above that member covers the name, as it should.
        """
        anchors: dict[str, list[str]] = {}
        for group in self.group_labels:
            members = self.group_shapes(group)
            if members:
                anchors.setdefault(members[-1], []).append(group)
        for riders in anchors.values():
            riders.sort(key=self.nesting_depth, reverse=True)  # innermost first, so it ends lowest
        return anchors

    def restack(self) -> None:
        """Make the canvas stacking order match the recorded depths."""
        self.render_group_labels()  # they follow their members, so rebuild before restacking
        anchors = self.label_anchors()
        for identifier in self.by_depth():
            item = self.items.get(identifier)
            if item is None:
                continue
            self.canvas.tag_raise(item)
            label = self.labels.get(identifier)
            if label is not None:
                self.canvas.tag_raise(label, item)  # a name stays on its own shape
            for group in anchors.get(identifier, []):
                self.canvas.tag_raise(self.group_labels[group])
        self.canvas.tag_raise(TAG_HANDLE)
        self.canvas.tag_lower(TAG_GRID)

    # ----------------------------------------------------------- adding shapes

    def on_popup(self, event: tk.Event) -> None:
        """Right click: the shape menu over a shape, the draw menu over empty canvas."""
        x, y = self.pointer(event)
        self.menu_point = (x, y)

        identifier = self.shape_at(x, y)
        if identifier is None and self.handle_at(x, y) is not None:
            identifier = self.selected  # a handle counts as its own shape

        if identifier is None:
            self._post(self.canvas_popup, event)
            return

        if identifier not in self.selection:  # keep a multiple selection the user has built up
            self.select(identifier)
        elif self.selected != identifier:
            self.select_items(self.selection, identifier)
        self.populate_shape_popup()
        self._post(self.shape_popup, event)

    def can_group(self) -> bool:
        """True when there are two or more top-level pieces to wrap up."""
        return len(self.selection_units()) >= 2

    def can_ungroup(self) -> bool:
        return any(self.root_group(identifier) for identifier in self.selection)

    def populate_shape_popup(self) -> None:
        """Rebuild the shape menu so it only offers what the current selection can do."""
        self.shape_popup.delete(self.shape_extras, tk.END)
        group, ungroup = self.can_group(), self.can_ungroup()
        if not (group or ungroup):
            return
        self.shape_popup.add_separator()
        if group:
            self.shape_popup.add_command(label="Group selection", command=self.group_selection)
        if ungroup:
            self.shape_popup.add_command(label="Ungroup", command=self.ungroup_selection)

    def _post(self, menu: tk.Menu, event: tk.Event) -> None:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def dismiss_popup(self, event: tk.Event) -> None:
        """Unpost the menu when the button comes up on no entry at all."""
        menu = event.widget
        if not isinstance(menu, tk.Menu):
            return
        try:
            active = menu.index(tk.ACTIVE)
        except tk.TclError:
            active = None
        if active is None:
            menu.unpost()
            menu.grab_release()

    def add_shape_at_menu_point(self, kind: str) -> None:
        self.add_shape(kind, *self.menu_point)

    def add_shape_at_center(self, kind: str) -> None:
        self.canvas.update_idletasks()
        left, top, right, bottom = self.viewport()
        self.add_shape(kind, (left + right) / 2, (top + bottom) / 2)

    def add_shape(self, kind: str, cx: float, cy: float) -> str:
        """Draw a new shape centred near (cx, cy) in model units, snapped to the grid."""
        if kind == "rectangle":
            width, height = self.cm_to_model(RECT_W_CM), self.cm_to_model(RECT_H_CM)
        else:
            width = height = self.cm_to_model(CIRCLE_D_CM)

        # Snap the top-left corner: with whole-centimetre shapes that puts every edge on a grid line.
        record = ShapeRecord(
            kind=kind,
            x=self.snap(cx - width / 2),
            y=self.snap(cy - height / 2),
            width=width,
            height=height,
            fill=SHAPE_FILL,
            depth=len(self.shapes),  # new shapes land on top
        )
        self.shapes[record.uuid] = record
        self.render(record.uuid)
        self.renumber()
        self.restack()
        self.select(record.uuid)
        self.autosave(f"adding the {kind}")
        self.set_status(f"Added {kind}. Drag it to move, drag a handle to resize.")
        return record.uuid

    # -------------------------------------------------------------- selection

    def select(self, identifier: str | None, add: bool = False) -> None:
        """Select one shape, or with `add` toggle it into the current selection.

        Selecting any member of a group selects the whole group, which is what makes
        a group move and resize as one piece.
        """
        if identifier is None:
            self.select_items([], None)
            return

        family = self.group_members(identifier)
        if not add:
            self.select_items(family, identifier)
        elif identifier in self.selection:  # shift-clicking a selected shape takes it back out
            remaining = [other for other in self.selection if other not in family]
            self.select_items(remaining, remaining[-1] if remaining else None)
        else:
            self.select_items(self.selection + family, identifier)

    def select_items(self, identifiers: list[str], primary: str | None) -> None:
        """Replace the selection. `primary` is the one the properties pane edits."""
        target = self.target_for(primary)
        if target != self.pane_target:
            self.commit_pane()  # don't lose text typed for whatever we are leaving

        seen: dict[str, None] = {}  # an ordered set: keep click order, drop duplicates
        for identifier in identifiers:
            if identifier in self.shapes:
                seen[identifier] = None
        self.selection = list(seen)

        if primary not in self.selection:
            primary = self.selection[-1] if self.selection else None
        self.selected = primary

        self.redraw_selection()
        self.show_in_pane(self.target_for(primary))
        if primary is None:
            self.set_status("Nothing selected.")
        elif len(self.selection) > 1:
            what = "group" if self.shapes[primary].group else "shapes"
            self.set_status(f"{len(self.selection)} {what} selected. Drag to move them together.")
        else:
            self.report_size(primary)

    def selection_box(self) -> Box | None:
        """The bounding box around everything selected, in model units."""
        boxes = [self.shapes[identifier].box for identifier in self.selection if identifier in self.shapes]
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    def handle_positions(self) -> dict[str, tuple[float, float]]:
        box = self.selection_box()
        if box is None:
            return {}
        x1, y1, x2, y2 = box
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        return {
            "nw": (x1, y1),
            "n": (mx, y1),
            "ne": (x2, y1),
            "e": (x2, my),
            "se": (x2, y2),
            "s": (mx, y2),
            "sw": (x1, y2),
            "w": (x1, my),
        }

    def redraw_selection(self) -> None:
        self.canvas.delete(TAG_HANDLE)
        self.canvas.delete(TAG_MARK)
        if not self.selection:
            return

        if len(self.selection) > 1:
            # Show which shapes are in the selection, since the handles only frame the whole.
            for identifier in self.selection:
                mx1, my1, mx2, my2 = self.screen_box(self.shapes[identifier].box)
                self.canvas.create_rectangle(
                    mx1 - 2, my1 - 2, mx2 + 2, my2 + 2, outline=HANDLE_OUTLINE, dash=MARQUEE_DASH, tags=(TAG_MARK,)
                )

        radius = self.length_to_screen(HANDLE_RADIUS)
        for name, (hx, hy) in self.handle_positions().items():
            sx, sy = self.point_to_screen(hx, hy)
            self.canvas.create_rectangle(
                sx - radius,
                sy - radius,
                sx + radius,
                sy + radius,
                fill=HANDLE_FILL,
                outline=HANDLE_OUTLINE,
                width=1,
                tags=(TAG_HANDLE, f"handle:{name}"),
            )

    def shape_at(self, x: float, y: float) -> str | None:
        """Topmost shape covering this model point, tested against the model, not the canvas."""
        for identifier in reversed(self.by_depth()):
            record = self.shapes[identifier]
            x1, y1, x2, y2 = record.box
            if not (x1 <= x <= x2 and y1 <= y <= y2):
                continue
            if record.kind == "rectangle":
                return identifier
            rx, ry = record.width / 2, record.height / 2
            cx, cy = record.center
            if rx > 0 and ry > 0 and ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                return identifier
        return None

    def handle_at(self, x: float, y: float) -> str | None:
        reach = HANDLE_RADIUS + 2
        for name, (hx, hy) in self.handle_positions().items():
            if abs(x - hx) <= reach and abs(y - hy) <= reach:
                return name
        return None

    def _fill_setter(self, color: str) -> Callable[[], None]:
        return lambda: self.set_fill(color)

    # ----------------------------------------------------------------- groups

    def group_index(self) -> dict[str, list[str]]:
        """Group uuid -> the shapes directly inside it, in depth order."""
        groups: dict[str, list[str]] = {}
        for identifier in self.by_depth():
            group = self.shapes[identifier].group
            if group:
                groups.setdefault(group, []).append(identifier)
        return groups

    def child_groups(self, group: str) -> list[str]:
        """The groups directly inside this one."""
        return [other for other, record in self.groups.items() if record.parent == group]

    def top_group(self, group: str) -> str:
        """Walk up the nesting to the outermost group. Tolerates a malformed cycle."""
        seen: set[str] = set()
        current = group
        while current in self.groups and self.groups[current].parent and current not in seen:
            seen.add(current)
            current = self.groups[current].parent
        return current if current in self.groups else ""

    def root_group(self, identifier: str) -> str:
        """The outermost group a shape belongs to, or empty when it is loose."""
        record = self.shapes.get(identifier)
        if record is None or not record.group:
            return ""
        return self.top_group(record.group)

    def group_shapes(self, group: str) -> list[str]:
        """Every shape anywhere beneath this group, in depth order."""
        found: set[str] = set()
        pending = [group]
        seen: set[str] = set()
        direct = self.group_index()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            found.update(direct.get(current, []))
            pending.extend(self.child_groups(current))
        return [identifier for identifier in self.by_depth() if identifier in found]

    def group_members(self, identifier: str) -> list[str]:
        """Everything that moves with this shape: its whole top-level group, or just itself."""
        root = self.root_group(identifier)
        if not root:
            return [identifier] if identifier in self.shapes else []
        return self.group_shapes(root)

    def selection_units(self) -> list[tuple[str, str]]:
        """The selection as top-level pieces: ("group", uuid) or ("shape", uuid)."""
        units: dict[tuple[str, str], None] = {}
        for identifier in self.selection:
            root = self.root_group(identifier)
            units[("group", root) if root else ("shape", identifier)] = None
        return list(units)

    def group_selection(self) -> None:
        """Wrap the selection in a new group.

        Groups already in the selection are kept whole and become subgroups of the new one,
        rather than being dissolved into a flat list of shapes.
        """
        units = self.selection_units()
        if len(units) < 2:
            self.set_status("Shift-click a second shape or group before grouping.")
            return

        group = GroupRecord()
        self.groups[group.uuid] = group
        nested = 0
        for kind, identifier in units:
            if kind == "group":
                self.groups[identifier].parent = group.uuid
                nested += 1
            else:
                self.shapes[identifier].group = group.uuid

        self.prune_groups()
        self.redraw_selection()
        self.show_in_pane(self.target_for(self.selected))  # the pane now edits the new group
        self.autosave("grouping")
        note = f", keeping {nested} existing group(s) whole" if nested else ""
        self.set_status(f"Grouped {len(units)} items as {group.uuid[:8]}{note}.")

    def ungroup_selection(self) -> None:
        """Take apart the outermost group only. Anything nested inside it stays a group."""
        roots: dict[str, None] = {}
        for identifier in self.selection:
            root = self.root_group(identifier)
            if root:
                roots[root] = None
        if not roots:
            self.set_status("Nothing in the selection belongs to a group.")
            return

        promoted = 0
        for root in roots:
            for shape in self.group_index().get(root, []):
                self.shapes[shape].group = ""
            for child in self.child_groups(root):
                self.groups[child].parent = ""  # a subgroup survives, now at the top level
                promoted += 1
            del self.groups[root]

        self.prune_groups()
        self.select_items(self.selection, self.selected)  # the pane may now edit a shape
        self.autosave("ungrouping")
        note = f" {promoted} subgroup(s) kept." if promoted else ""
        self.set_status(f"Ungrouped {len(roots)} group(s).{note}")

    def prune_groups(self) -> None:
        """Forget groups holding neither a shape nor a subgroup, repeatedly until none are left."""
        while True:
            direct = self.group_index()
            empty = [
                identifier
                for identifier in self.groups
                if not direct.get(identifier) and not self.child_groups(identifier)
            ]
            if not empty:
                break
            for identifier in empty:
                del self.groups[identifier]
                if self.pane_target == ("group", identifier):
                    self.pane_target = None

        for record in self.groups.values():  # a parent that no longer exists means top level
            if record.parent and record.parent not in self.groups:
                record.parent = ""

    # ------------------------------------------------------------------ depth

    def by_depth(self) -> list[str]:
        """Shapes bottom to top. Equal depths keep insertion order."""
        return sorted(self.shapes, key=lambda identifier: self.shapes[identifier].depth)

    def renumber(self, order: list[str] | None = None) -> None:
        """Give the shapes depths 0..n-1 so the numbers stay small and gap-free."""
        for depth, identifier in enumerate(order if order is not None else self.by_depth()):
            self.shapes[identifier].depth = depth

    def set_depth(self, action: str) -> None:
        """Move the selected shape through the stack: top, bottom, or one step either way."""
        identifier = self.selected
        if identifier is None or identifier not in self.shapes:
            self.set_status("Select a shape first, then change its depth.")
            return

        order = self.by_depth()
        if len(order) < 2:
            self.set_status("Depth only matters once there are two shapes.")
            return

        index = order.index(identifier)
        target = {"top": len(order) - 1, "bottom": 0, "up": index + 1, "down": index - 1}[action]
        target = max(0, min(target, len(order) - 1))
        if target == index:
            self.set_status(
                f"{self.kind_of(identifier).capitalize()} is already at the {'top' if index else 'bottom'}."
            )
            return

        order.insert(target, order.pop(index))
        self.renumber(order)
        self.restack()
        self.autosave(f"depth change of {self.kind_of(identifier)}")
        depth = self.shapes[identifier].depth
        self.set_status(f"{self.kind_of(identifier).capitalize()} moved to depth {depth} of {len(order) - 1}.")

    # ------------------------------------------------------------- fill color

    def fill_targets(self) -> list[str]:
        """Which selected shapes a colour change applies to.

        Colouring a whole group is opt-in. While it is off, a grouped shape is recoloured on its
        own - the one the popup was opened over - and its group mates are left alone. Shapes that
        merely happen to be shift-selected together are not a group and are all recoloured.
        """
        selected = [identifier for identifier in self.selection if identifier in self.shapes]
        if self.color_groups.get():
            return selected
        return [
            identifier for identifier in selected if not self.shapes[identifier].group or identifier == self.selected
        ]

    def set_fill(self, color: str) -> None:
        selected = [identifier for identifier in self.selection if identifier in self.shapes]
        if not selected:
            self.set_status("Right-click a shape to change its fill.")
            return

        targets = self.fill_targets()
        for identifier in targets:
            self.shapes[identifier].fill = color
            self.render(identifier)  # the name may need a lighter or darker ink

        self.restack()
        self.refresh_fill_row()
        self.autosave("recolor")
        subject = self.kind_of(targets[0]) if len(targets) == 1 else f"{len(targets)} shapes"
        held_back = len(selected) - len(targets)
        note = f" {held_back} group mate(s) left alone; enable group colouring to include them." if held_back else ""
        self.set_status(f"{subject.capitalize()} filled with {color}.{note}")

    def on_color_groups_changed(self) -> None:
        self.autosave("group colouring setting")
        if self.color_groups.get():
            self.set_status("Colouring now applies to every shape in a group.")
        else:
            self.set_status("Colouring now applies to one shape at a time within a group.")

    def choose_custom_fill(self) -> None:
        record = self.shapes.get(self.selected) if self.selected is not None else None
        if record is None:
            return
        _rgb, chosen = colorchooser.askcolor(color=record.fill, title="Fill color", parent=self.root)
        if chosen:
            self.set_fill(str(chosen))

    # ------------------------------------------------------------- properties

    def target_for(self, identifier: str | None) -> tuple[str, str] | None:
        """What the pane edits for a given primary shape: its group if it has one, else itself."""
        if identifier is None or identifier not in self.shapes:
            return None
        root = self.root_group(identifier)
        if root:
            return "group", root
        return "shape", identifier

    def target_record(self, target: tuple[str, str] | None) -> ShapeRecord | GroupRecord | None:
        if target is None:
            return None
        kind, identifier = target
        return self.groups.get(identifier) if kind == "group" else self.shapes.get(identifier)

    def show_in_pane(self, target: tuple[str, str] | None) -> None:
        """Point the side pane at a shape or a group, or empty it when nothing is selected."""
        record = self.target_record(target)
        self.pane_target = target if record is not None else None

        state: Literal["normal", "disabled"] = "normal" if record is not None else "disabled"
        self.pane_name.configure(state=tk.NORMAL)
        self.pane_name.delete(0, tk.END)
        self.pane_description.configure(state=tk.NORMAL)
        self.pane_description.delete("1.0", tk.END)

        if record is None:
            self.pane_subject.configure(text="No shape selected")
        elif isinstance(record, GroupRecord):
            members = len(self.group_shapes(record.uuid))
            nested = len(self.child_groups(record.uuid))
            summary = f"Group of {members} shapes"
            if nested:
                summary += f" in {nested} subgroup(s)"
            self.pane_subject.configure(text=f"{summary} · editing the group\n{record.uuid}")
            self.pane_name.insert(0, record.name)
            self.pane_description.insert("1.0", record.description)
        else:
            summary = f"{record.kind.capitalize()} · depth {record.depth}"
            if len(self.selection) > 1:
                summary += f" · {len(self.selection)} selected, editing this one"
            self.pane_subject.configure(text=f"{summary}\n{record.uuid}")
            self.pane_name.insert(0, record.name)
            self.pane_description.insert("1.0", record.description)

        self.pane_name.configure(state=state)
        self.pane_description.configure(state=state)
        self.pane_apply.configure(state=state)
        self.refresh_show_button()
        self.refresh_fill_row()
        self.update_pane_visibility()

    def _swatch_setter(self, color: str) -> Callable[[tk.Event], None]:
        def apply(_event: tk.Event) -> None:
            if self.pane_target is not None:
                self.set_fill(color)

        return apply

    def refresh_fill_row(self) -> None:
        """Mark the swatch matching the current fill, and grey the row out when it cannot be used."""
        usable = self.pane_target is not None
        fills = {self.shapes[identifier].fill for identifier in self.fill_targets()}
        current = fills.pop() if len(fills) == 1 else ""
        for color, swatch in self.fill_swatches.items():
            chosen = usable and color.lower() == current.lower()
            swatch.configure(highlightbackground=HANDLE_OUTLINE if chosen else CANVAS_BG)
        self.custom_fill_button.configure(state=tk.NORMAL if usable else tk.DISABLED)

    def update_pane_visibility(self) -> None:
        """Show the pane only when there is something with properties to edit."""
        wanted = self.pane_target is not None
        if wanted == self.pane_visible:
            return
        self.pane_visible = wanted
        # This resizes the canvas. The single <Configure> it produces must not be mistaken for
        # the user dragging the window edge, so flag it and let the handler consume the flag.
        self.self_inflicted_layout = True
        if wanted:
            self.pane.grid()
        else:
            self.pane.grid_remove()

    def refresh_show_button(self) -> None:
        """The toggle reads the current state, and is only usable once there is a name to show."""
        record = self.target_record(self.pane_target)
        shown = bool(record.show_name) if record is not None else True
        self.pane_show.set(shown)
        self.pane_show_text.set("Show" if shown else "Hide")
        self.show_button.configure(state=tk.NORMAL if record is not None and record.name else tk.DISABLED)

    def on_show_toggled(self) -> None:
        """Flip whether the name is drawn. The button then reads the new state."""
        target = self.pane_target
        record = self.target_record(target)
        if target is None or record is None or not record.name:
            self.refresh_show_button()
            return

        record.show_name = bool(self.pane_show.get())
        self.pane_show_text.set("Show" if record.show_name else "Hide")
        if isinstance(record, ShapeRecord):
            self.render_label(target[1])
            subject = record.kind
        else:
            subject = "group"
        self.restack()
        self.autosave(f"name visibility of the {subject}")
        self.set_status(f"Name of the {subject} is now {'shown' if record.show_name else 'hidden'}.")

    def commit_pane(self) -> None:
        """Write whatever is in the pane back to the shape or group it belongs to."""
        target = self.pane_target
        record = self.target_record(target)
        if target is None or record is None:
            return

        name = self.pane_name.get().strip()
        description = self.pane_description.get("1.0", tk.END).strip()
        if (name, description) == (record.name, record.description):
            return  # nothing typed, so nothing to save

        record.name, record.description = name, description
        if isinstance(record, ShapeRecord):
            self.render_label(target[1])
            self.restack()
            self.autosave(f"edit of {record.kind} properties")
        else:
            self.restack()  # the group label may have just appeared or gone
            self.autosave("edit of group properties")
        self.refresh_show_button()
        self.set_status(f"Saved properties for {name or record.uuid[:8]}.")

    # ------------------------------------------------------- move and resize

    def typing(self) -> bool:
        """True when the keyboard belongs to a text field, so space should type a space."""
        return self.root.focus_get() in (self.pane_name, self.pane_description, self.json_text)

    def on_space_down(self, _event: tk.Event) -> None:
        """Space arms panning while it is held."""
        if self.space_release_job is not None:
            # A release followed straight away by a press is auto-repeat, not a real release.
            self.root.after_cancel(self.space_release_job)
            self.space_release_job = None
        if self.space_held or self.typing():
            return
        self.space_held = True
        self.canvas.configure(cursor="fleur")
        self.set_status("Hold space and drag to move the view.")

    def on_space_up(self, _event: tk.Event) -> None:
        """Believe a release only if no repeat press follows within the grace period."""
        if not self.space_held:
            return
        if self.space_release_job is not None:
            self.root.after_cancel(self.space_release_job)
        self.space_release_job = self.root.after(SPACE_RELEASE_GRACE_MS, self.release_space)

    def release_space(self) -> None:
        self.space_release_job = None
        self.space_held = False
        if not self.canvas.winfo_exists():  # the window closed while the timer was pending
            return
        if self.drag_mode != "pan":  # a pan already under way runs until the button comes up
            self.canvas.configure(cursor="")

    def start_pan(self, event: tk.Event) -> None:
        self.drag_mode = "pan"
        self.pan_from = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self.pan_origin = (self.pan_x, self.pan_y)
        self.canvas.configure(cursor="fleur")

    def end_pan(self) -> None:
        self.drag_mode = None
        self.canvas.configure(cursor="fleur" if self.space_held else "")

    def on_middle_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        self.start_pan(event)

    def on_middle_release(self, _event: tk.Event) -> None:
        if self.drag_mode == "pan":
            self.end_pan()

    def pan_to(self, x: float, y: float) -> None:
        """Put the model point (x, y) at the top-left of the viewport and redraw."""
        self.pan_x, self.pan_y = x, y
        self.reposition()

    def reposition(self) -> None:
        """Move every canvas item to where the current viewport puts it.

        Coordinates are updated in place rather than recreated, because this runs on every
        motion event of a pan.
        """
        for identifier, item in self.items.items():
            self.canvas.coords(item, *self.screen_box(self.shapes[identifier].box))
            label = self.labels.get(identifier)
            if label is not None:
                record = self.shapes[identifier]
                self.canvas.coords(label, *self.point_to_screen(*record.center))
        self.render_group_labels()
        self.draw_grid()
        self.redraw_selection()

    def on_canvas_configure(self, event: tk.Event) -> None:
        """Redraw the grid for the new size, and recentre once the resize has settled."""
        size = (int(event.width), int(event.height))
        if size == self.canvas_size:
            return

        first_layout = self.canvas_size is None
        self.canvas_size = size
        self.draw_grid()
        if first_layout:  # the initial layout is not a resize
            return
        if self.self_inflicted_layout:  # the pane appearing or disappearing
            self.self_inflicted_layout = False
            return
        self.schedule_recentre()

    def schedule_recentre(self) -> None:
        """Restart the settle timer. <Configure> arrives continuously while a window is dragged."""
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(RESIZE_SETTLE_MS, self.on_resize_settled)

    def on_resize_settled(self) -> None:
        self.resize_job = None
        if not self.canvas.winfo_exists():
            return
        if self.drag_mode is not None:  # wait rather than move the ground under a drag
            self.schedule_recentre()
            return
        self.center_drawing()

    def center_drawing(self) -> None:
        """Put the middle of the drawing in the middle of the viewport."""
        boxes = [record.box for record in self.shapes.values()]
        if not boxes:
            self.pan_to(0.0, 0.0)
            self.set_status("Nothing drawn yet. View reset to the origin.")
            return

        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[2] for box in boxes)
        bottom = max(box[3] for box in boxes)
        width = self.length_to_model(max(self.canvas.winfo_width(), 1))
        height = self.length_to_model(max(self.canvas.winfo_height(), 1))
        self.pan_to((left + right) / 2 - width / 2, (top + bottom) / 2 - height / 2)
        self.set_status(f"Centred {len(self.shapes)} shape(s) in the view.")

    def on_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()  # so the space key reaches the canvas
        x, y = self.pointer(event)
        shift = bool(self.modifiers(event) & 0x0001)

        if self.space_held:  # space + drag moves the view, never the shapes
            self.start_pan(event)
            return

        handle = self.handle_at(x, y) if self.selection else None
        if handle is not None:
            self.begin_drag("resize", x, y)
            self.drag_handle = handle
            return

        identifier = self.shape_at(x, y)
        if identifier is None:
            self.begin_band(x, y, add=shift)  # a click that never moves just deselects
            return

        self.select(identifier, add=shift)
        self.begin_drag("move", x, y)
        self.drag_handle = None

    def begin_drag(self, mode: str, x: float, y: float) -> None:
        """Remember where everything started, so snapping measures from a fixed origin."""
        self.drag_mode = mode
        self.drag_start = (x, y)
        self.drag_boxes = {identifier: self.shapes[identifier].box for identifier in self.selection}
        box = self.selection_box()
        self.drag_bbox = box if box is not None else (0.0, 0.0, 0.0, 0.0)
        self.drag_min = self.minimum_frame()

    def minimum_frame(self) -> tuple[float, float]:
        """Smallest frame this drag may shrink to, as a whole number of cells.

        A frame of one cell would crush the members of a group well below the minimum size,
        because they scale proportionally. So the floor is whichever is larger: one minimum
        extent, or the frame size at which the *smallest* member reaches that extent.
        """
        cell = self.cell
        floor = self.min_extent
        frame_w = self.drag_bbox[2] - self.drag_bbox[0]
        frame_h = self.drag_bbox[3] - self.drag_bbox[1]

        widths = [box[2] - box[0] for box in self.drag_boxes.values()]
        heights = [box[3] - box[1] for box in self.drag_boxes.values()]
        min_w, min_h = floor, floor
        if widths and min(widths) > 0 and frame_w > 0:
            min_w = max(floor, frame_w * floor / min(widths))
        if heights and min(heights) > 0 and frame_h > 0:
            min_h = max(floor, frame_h * floor / min(heights))

        return math.ceil(min_w / cell) * cell, math.ceil(min_h / cell) * cell

    def begin_band(self, x: float, y: float, add: bool) -> None:
        """Start a rubber band on empty canvas. Shift keeps what is already selected."""
        self.drag_mode = "band"
        self.drag_start = (x, y)
        self.band_origin = (x, y)
        self.band_base = list(self.selection) if add else []
        self.canvas.delete(TAG_BAND)

    def draw_band(self, x: float, y: float) -> None:
        self.canvas.delete(TAG_BAND)
        x1, y1 = self.point_to_screen(min(self.band_origin[0], x), min(self.band_origin[1], y))
        x2, y2 = self.point_to_screen(max(self.band_origin[0], x), max(self.band_origin[1], y))
        self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            outline=HANDLE_OUTLINE,
            dash=BAND_DASH,
            fill=HANDLE_OUTLINE,
            stipple=BAND_STIPPLE,
            tags=(TAG_BAND,),
        )
        self.canvas.tag_raise(TAG_BAND)

    def band_box(self, x: float, y: float) -> Box:
        return (
            min(self.band_origin[0], x),
            min(self.band_origin[1], y),
            max(self.band_origin[0], x),
            max(self.band_origin[1], y),
        )

    def shapes_touching(self, box: Box) -> list[str]:
        """Every shape the band encloses or merely touches, bottom to top.

        Groups come along whole: catching one member catches the group, so a banded selection
        behaves exactly like one built with shift-click.
        """
        caught: dict[str, None] = {}
        for identifier in self.by_depth():
            x1, y1, x2, y2 = self.shapes[identifier].box
            if x2 < box[0] or x1 > box[2] or y2 < box[1] or y1 > box[3]:
                continue
            for member in self.group_members(identifier):
                caught[member] = None
        return list(caught)

    def finish_band(self, x: float, y: float) -> None:
        self.canvas.delete(TAG_BAND)
        travel = max(
            abs(self.length_to_screen(x - self.band_origin[0])),
            abs(self.length_to_screen(y - self.band_origin[1])),
        )
        if travel < BAND_MIN_DRAG:  # never really a drag; treat it as a click on empty canvas
            self.select_items(self.band_base, self.band_base[-1] if self.band_base else None)
            return

        caught = self.shapes_touching(self.band_box(x, y))
        chosen = self.band_base + [identifier for identifier in caught if identifier not in self.band_base]
        self.select_items(chosen, chosen[-1] if chosen else None)
        if len(chosen) > 1:
            self.set_status(f"{len(chosen)} shapes selected.")
        elif not chosen:
            self.set_status("Nothing in the band.")

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_mode == "band":
            self.draw_band(*self.pointer(event))
            return

        if self.drag_mode == "pan":
            scale = self.display_scale or 1.0
            dx = self.canvas.canvasx(event.x) - self.pan_from[0]
            dy = self.canvas.canvasy(event.y) - self.pan_from[1]
            self.pan_to(self.pan_origin[0] - dx / scale, self.pan_origin[1] - dy / scale)
            self.set_status(f"View at {self.model_to_cm(self.pan_x):.1f}, {self.model_to_cm(self.pan_y):.1f} cm")
            return

        if self.drag_mode is None or not self.selection:
            return

        x, y = self.pointer(event)
        if self.drag_mode == "move":
            self.drag_move(x, y)
        else:
            self.drag_resize(self.resized_box(x, y, bool(self.modifiers(event) & 0x0001)))

        for identifier in self.selection:
            self.render(identifier)
        self.restack()
        self.redraw_selection()
        if self.selected is not None:
            if self.drag_mode == "move":
                self.report_position(self.selected)
            else:
                self.report_size(self.selected)

    def drag_move(self, x: float, y: float) -> None:
        """Shift the whole selection, snapping the selection's own top-left to the grid.

        Measured from the press position rather than accumulated per event: with accumulation,
        sub-cell movements each round to zero and nothing ever moves.
        """
        left = self.snap(self.drag_bbox[0] + (x - self.drag_start[0]))
        top = self.snap(self.drag_bbox[1] + (y - self.drag_start[1]))
        dx, dy = left - self.drag_bbox[0], top - self.drag_bbox[1]
        for identifier, (bx1, by1, bx2, by2) in self.drag_boxes.items():
            self.shapes[identifier].set_box((bx1 + dx, by1 + dy, bx2 + dx, by2 + dy))

    def drag_resize(self, box: Box) -> None:
        """Scale every selected shape into the new frame, keeping their relative places."""
        ox1, oy1, ox2, oy2 = self.drag_bbox
        nx1, ny1, nx2, ny2 = box
        scale_x = (nx2 - nx1) / (ox2 - ox1) if ox2 > ox1 else 1.0
        scale_y = (ny2 - ny1) / (oy2 - oy1) if oy2 > oy1 else 1.0
        for identifier, (bx1, by1, bx2, by2) in self.drag_boxes.items():
            self.shapes[identifier].set_box(
                (
                    nx1 + (bx1 - ox1) * scale_x,
                    ny1 + (by1 - oy1) * scale_y,
                    nx1 + (bx2 - ox1) * scale_x,
                    ny1 + (by2 - oy1) * scale_y,
                )
            )

    def resized_box(self, x: float, y: float, keep_ratio: bool) -> Box:
        """The new frame for the current resize drag, snapped to the grid."""
        x1, y1, x2, y2 = self.drag_bbox
        handle = self.drag_handle or ""
        x, y = self.snap(x), self.snap(y)
        least_w, least_h = self.drag_min  # whole numbers of cells, so clamping stays on the grid

        if "w" in handle:
            x1 = x
        if "e" in handle:
            x2 = x
        if "n" in handle:
            y1 = y
        if "s" in handle:
            y2 = y

        if x2 - x1 < least_w:
            x1, x2 = (x2 - least_w, x2) if "w" in handle else (x1, x1 + least_w)
        if y2 - y1 < least_h:
            y1, y2 = (y2 - least_h, y2) if "n" in handle else (y1, y1 + least_h)

        if keep_ratio and len(handle) == 2:
            old_w = max(self.drag_bbox[2] - self.drag_bbox[0], 1.0)
            old_h = max(self.drag_bbox[3] - self.drag_bbox[1], 1.0)
            new_h = max(self.snap((x2 - x1) * (old_h / old_w)), least_h)
            y1, y2 = (y2 - new_h, y2) if "n" in handle else (y1, y1 + new_h)

        return x1, y1, x2, y2

    def on_release(self, event: tk.Event) -> None:
        if self.drag_mode == "band":
            self.drag_mode = None
            self.finish_band(*self.pointer(event))
            return
        if self.drag_mode == "pan":  # the viewport is not part of the document
            self.end_pan()
            return
        if self.drag_mode is not None and self.selection:
            if self.selected is not None:
                self.report_size(self.selected)
            self.autosave("move" if self.drag_mode == "move" else "resize")  # written once the drag settles
        self.drag_mode = None
        self.drag_handle = None
        self.drag_boxes = {}

    @staticmethod
    def modifiers(event: tk.Event) -> int:
        return event.state if isinstance(event.state, int) else 0

    def on_hover(self, event: tk.Event) -> None:
        if self.drag_mode is not None or self.space_held:
            return
        x, y = self.pointer(event)
        handle = self.handle_at(x, y) if self.selection else None
        if handle is not None:
            cursor = HANDLE_CURSORS.get(handle, "sizing")
        elif self.shape_at(x, y) is not None:
            cursor = "fleur"
        else:
            cursor = ""
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            self.canvas.configure(cursor="")

    # ------------------------------------------------------------- housekeeping

    def confirm_delete(self, question: str) -> bool:
        """Ask before destroying anything, unless the user has turned confirmation off."""
        if not self.confirm_deletes.get():
            return True
        return bool(messagebox.askyesno("Confirm delete", question, parent=self.root, default=messagebox.NO))

    def on_confirm_setting_changed(self) -> None:
        self.autosave("delete confirmation setting")
        if self.confirm_deletes.get():
            self.set_status("Deletes will be confirmed.")
        else:
            self.set_status("Deletes happen straight away now. Undo still works.")

    def delete_selected(self) -> None:
        targets = [identifier for identifier in self.selection if identifier in self.shapes]
        if not targets:
            self.set_status("Select a shape first, then delete it.")
            return

        if len(targets) == 1:
            record = self.shapes[targets[0]]
            described = f'the {record.kind} "{record.name}"' if record.name else f"this {record.kind}"
        else:
            described = f"these {len(targets)} shapes"
        if not self.confirm_delete(f"Delete {described}?"):
            self.set_status("Delete cancelled.")
            return

        self.pane_target = None  # their records are going; nothing to commit back to
        for identifier in targets:
            self.forget(identifier)
            self.shapes.pop(identifier, None)

        self.selection = []
        self.selected = None
        self.canvas.delete(TAG_HANDLE)
        self.canvas.delete(TAG_MARK)
        self.show_in_pane(None)
        self.prune_groups()  # a group with no members left is gone too
        self.renumber()  # close the gaps the deleted layers left
        self.restack()
        self.autosave(f"deletion of {described}")
        self.set_status(f"Deleted {described}.")

    def clear_canvas(self) -> None:
        count = len(self.shapes)
        if count and not self.confirm_delete(f"Delete all {count} shape(s) from the canvas?"):
            self.set_status("Clear cancelled.")
            return
        self.shapes.clear()
        self.groups.clear()
        self.selection = []
        self.selected = None
        self.pane_target = None
        self.show_in_pane(None)
        self.redraw_all()
        self.autosave("clearing the canvas")
        self.set_status("Canvas cleared. Right-click to add a shape.")

    def show_help(self) -> None:
        self.set_status(
            "Right-click empty canvas to add a shape, right-click a shape to recolor it. "
            "Shift-click to select several. Drag to move, drag a handle to resize; everything "
            "snaps to the grid. Hold space and drag to move the view, Ctrl+0 to centre the drawing. "
            f"Ctrl+Z undoes. State lives in {self.state_file}."
        )

    # ----------------------------------------------------------------- export

    def content_box(self) -> Box | None:
        """Bounding box of every shape widened by the export margin, in model units."""
        boxes = [record.box for record in self.shapes.values()]
        if not boxes:
            return None
        margin = self.cm_to_model(EXPORT_MARGIN_CM)
        return (
            min(box[0] for box in boxes) - margin,
            min(box[1] for box in boxes) - margin,
            max(box[2] for box in boxes) + margin,
            max(box[3] for box in boxes) + margin,
        )

    def export_dialog(self) -> None:
        """Ask where to write the picture, then write it in the format the user picked.

        The name is offered without an extension, and the one belonging to the chosen file
        type is appended afterwards, so the two can never disagree.
        """
        if not self.shapes:
            self.set_status("Draw something before exporting.")
            return

        picked = tk.StringVar(master=self.root, value=EXPORT_FILETYPES[0][0])
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export as picture",
            initialdir=str(Path.home()),
            initialfile=PROGRAM_NAME.lower(),  # no extension: the file type below supplies it
            filetypes=[(label, patterns) for label, _suffix, patterns in EXPORT_FILETYPES],
            typevariable=picked,
        )
        if not chosen:
            return

        target = self.export_path(Path(chosen), picked.get())
        if target is not None:
            self.export_to(target)

    def export_path(self, chosen: Path, type_label: str) -> Path | None:
        """Work out the file to write from the typed name and the selected file type."""
        suffix = chosen.suffix.lower()
        if suffix in EXPORT_FORMATS:
            return chosen  # an explicit extension always wins over the dropdown

        if suffix in EXPORT_UNSUPPORTED:
            self.warn(
                "Unsupported picture format",
                f"{chosen}\n\nExport can write {', '.join(sorted(EXPORT_FORMATS))}. "
                "Remove the extension and pick a file type instead.",
            )
            return None

        for label, extension, _patterns in EXPORT_FILETYPES:
            if label == type_label:
                return chosen.with_name(chosen.name + extension)
        return chosen.with_name(chosen.name + EXPORT_FILETYPES[0][1])  # dialog gave us no type

    def export_to(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix not in EXPORT_FORMATS:
            self.warn(
                "Unsupported picture format",
                f"{path}\n\nExport needs one of: {', '.join(sorted(EXPORT_FORMATS))}.",
            )
            return False

        box = self.content_box()
        if box is None:
            self.set_status("Draw something before exporting.")
            return False

        try:
            if suffix == ".svg":
                path.write_text(self.as_svg(box), encoding="utf-8")
            else:
                self.write_raster(path, box, suffix)
        except OSError as error:
            self.warn("Could not write the picture", f"{path}\n\n{error.strerror or error}")
            return False
        except ImportError:
            self.warn(
                "Pillow is not installed",
                "PNG and JPEG export needs the Pillow package:\n\n    pip install pillow\n\nSVG export works without it.",
            )
            return False

        width, height = box[2] - box[0], box[3] - box[1]
        self.set_status(f"Exported {len(self.shapes)} shape(s) to {path} ({int(width)}x{int(height)} px).")
        return True

    def export_items(self, box: Box) -> list[tuple[ShapeRecord, Box]]:
        """Shapes in drawing order, with coordinates moved into the picture's own frame."""
        placed = []
        for identifier in self.by_depth():
            record = self.shapes[identifier]
            x1, y1, x2, y2 = record.box
            placed.append((record, (x1 - box[0], y1 - box[1], x2 - box[0], y2 - box[1])))
        return placed

    def export_group_labels(self, box: Box) -> list[tuple[str, str, tuple[float, float], float]]:
        """Visible group names as (text, ink, centre, wrap width), in the picture's own frame."""
        labels = []
        members_by_group = self.group_index()
        for identifier, group in self.groups.items():
            members = members_by_group.get(identifier, [])
            if not group.name or not group.show_name or not members:
                continue
            frame = self.group_box(identifier)
            if frame is None:
                continue
            x1, y1, x2, y2 = frame
            labels.append(
                (
                    group.name,
                    ink_for(self.shapes[members[-1]].fill),
                    ((x1 + x2) / 2 - box[0], (y1 + y2) / 2 - box[1]),
                    x2 - x1,
                )
            )
        return labels

    def as_svg(self, box: Box) -> str:
        width, height = box[2] - box[0], box[3] - box[1]
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}">',
            f'<rect width="100%" height="100%" fill="{CANVAS_BG}"/>',
        ]
        for record, (x1, y1, x2, y2) in self.export_items(box):
            shape_w, shape_h = x2 - x1, y2 - y1
            common = f'fill="{record.fill}" stroke="{SHAPE_OUTLINE}" stroke-width="1"'
            if record.kind == "rectangle":
                parts.append(f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{shape_w:.2f}" height="{shape_h:.2f}" {common}/>')
            else:
                parts.append(
                    f'<ellipse cx="{x1 + shape_w / 2:.2f}" cy="{y1 + shape_h / 2:.2f}" '
                    f'rx="{shape_w / 2:.2f}" ry="{shape_h / 2:.2f}" {common}/>'
                )
            if record.name and record.show_name:
                parts.append(
                    f'<text x="{x1 + shape_w / 2:.2f}" y="{y1 + shape_h / 2:.2f}" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'font-family="sans-serif" font-size="{EXPORT_FONT_SIZE}" '
                    f'fill="{ink_for(record.fill)}">{escape(record.name)}</text>'
                )

        for text, ink, (cx, cy), _width in self.export_group_labels(box):
            parts.append(
                f'<text x="{cx:.2f}" y="{cy:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="sans-serif" font-size="{EXPORT_FONT_SIZE}" fill="{ink}">{escape(text)}</text>'
            )
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    def write_raster(self, path: Path, box: Box, suffix: str) -> None:
        """Draw the shapes into a bitmap. Raises ImportError when Pillow is absent."""
        from PIL import Image, ImageDraw, ImageFont  # imported here so SVG export works without it

        width, height = max(int(box[2] - box[0]), 1), max(int(box[3] - box[1]), 1)
        image = Image.new("RGB", (width, height), CANVAS_BG)
        draw = ImageDraw.Draw(image)

        font: Any = None
        for candidate in EXPORT_FONTS:
            try:
                font = ImageFont.truetype(candidate, EXPORT_FONT_SIZE)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()

        for record, (x1, y1, x2, y2) in self.export_items(box):
            corners = (x1, y1, max(x2, x1 + 1), max(y2, y1 + 1))
            if record.kind == "rectangle":
                draw.rectangle(corners, fill=record.fill, outline=SHAPE_OUTLINE, width=1)
            else:
                draw.ellipse(corners, fill=record.fill, outline=SHAPE_OUTLINE, width=1)
            if record.name and record.show_name:
                draw.text(
                    ((corners[0] + corners[2]) / 2, (corners[1] + corners[3]) / 2),
                    record.name,
                    fill=ink_for(record.fill),
                    font=font,
                    anchor="mm",
                )

        for text, ink, centre, _width in self.export_group_labels(box):
            draw.text(centre, text, fill=ink, font=font, anchor="mm")

        image.save(path, "JPEG", quality=92) if suffix in (".jpg", ".jpeg") else image.save(path, "PNG")

    # ------------------------------------------------------------ state files

    def to_state(self) -> dict[str, Any]:
        """The whole document, read from the model. The canvas is not consulted."""
        return {
            "program": PROGRAM_NAME,
            "version": PROGRAM_VERSION,
            "canvas": {
                "background": CANVAS_BG,
                "grid_cm": self.grid_pitch(),
                "grid_visible": self.show_grid.get(),
                "grid_color": self.grid_color.get(),
                "grid_line": self.grid_line.get(),
            },
            "settings": {
                "confirm_deletes": self.confirm_deletes.get(),
                "color_groups": self.color_groups.get(),
            },
            "groups": [
                record.to_json(self.group_index().get(identifier, []), self.child_groups(identifier))
                for identifier, record in self.groups.items()
            ],
            "shapes": [self.shapes[identifier].to_json() for identifier in self.by_depth()],
        }

    def show_tab(self, index: int) -> None:
        self.notebook.select(index)  # type: ignore[no-untyped-call]

    def refresh_json_view(self) -> None:
        """Mirror the current state into the Json tab."""
        document = json.dumps(self.to_state(), indent=2)
        self.json_text.configure(state=tk.NORMAL)
        self.json_text.delete("1.0", tk.END)
        self.json_text.insert("1.0", document)
        self.json_text.configure(state=tk.DISABLED)

    def write_state(self, path: Path) -> bool:
        """Write the current state to path. Returns False and reports if it could not be written."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".part")
            temporary.write_text(json.dumps(self.to_state(), indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)  # never leave a half-written state file behind
        except OSError as error:
            self.set_status(f"Could not save to {path}: {error.strerror or error}")
            return False
        return True

    def autosave(self, label: str = "change") -> None:
        """Called after every change that alters the state."""
        if self.suspend_autosave:
            return
        if not self.suspend_history:
            self.record_history(label)
        self.refresh_json_view()
        self._refresh_undo_button()
        self.write_state(self.state_file)

    def record_history(self, label: str) -> None:
        """Push the state as it was before this change onto the undo stack."""
        if self.baseline is not None:
            self.history.append((label, self.baseline))
            del self.history[:-HISTORY_LIMIT]
        self.baseline = self.to_state()

    def undo(self) -> None:
        """Step back to the state before the most recent change."""
        if not self.history:
            self.set_status("Nothing to undo.")
            return

        label, snapshot = self.history.pop()
        self.suspend_history = True
        try:
            self.apply_state(snapshot)  # writes to disk, but records no new history
        finally:
            self.suspend_history = False

        self.baseline = self.to_state()
        self._refresh_undo_button()
        self.set_status(f"Undid {label}. {len(self.history)} step(s) left.")

    def _refresh_undo_button(self) -> None:
        self.undo_button.configure(state=tk.NORMAL if self.history else tk.DISABLED)

    def save_state_dialog(self) -> None:
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save state as",
            initialdir=str(self.state_dir if self.state_dir.is_dir() else Path.home()),
            initialfile=f"{PROGRAM_NAME.lower()}-state{STATE_SUFFIX}",
            defaultextension=STATE_SUFFIX,
            filetypes=[("State files", f"*{STATE_SUFFIX}"), ("All files", "*.*")],
        )
        if chosen and self.write_state(Path(chosen)):
            self.set_status(f"Saved {len(self.shapes)} shape(s) to {chosen}")

    def load_state_dialog(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open state",
            initialdir=str(self.state_dir if self.state_dir.is_dir() else Path.home()),
            filetypes=[("State files", f"*{STATE_SUFFIX}"), ("All files", "*.*")],
        )
        if chosen:
            self.load_state(Path(chosen), announce=True)

    def startup_load(self, clean: bool, startup_file: Path | None) -> None:
        """Decide what the canvas starts with: a named file, the autosave, or nothing.

        A file named on the command line replaces the autosave entirely. If that file is
        rejected the canvas stays empty rather than quietly falling back to the autosave,
        so what you asked for and what you get can never differ silently.
        """
        if startup_file is not None:
            if startup_file.suffix.lower() != STATE_SUFFIX:
                self.warn(
                    "Not a JSON file",
                    f"{startup_file}\n\nOnly '*{STATE_SUFFIX}' files are loaded. "
                    "The file was ignored and the canvas is empty.",
                )
                return
            self.load_state(startup_file, announce=True)
            return

        if clean:
            self.set_status(f"Started clean. {self.state_file} was not loaded; the next change will overwrite it.")
            return

        if self.state_file.is_file():
            self.load_state(self.state_file, announce=False)

    def load_state(self, path: Path, announce: bool) -> bool:
        """Read, check and apply a state file. A file that isn't ours is reported and ignored."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            self.warn("Cannot read file", f"{path}\n\n{error.strerror or error}")
            return False
        except json.JSONDecodeError as error:
            self.warn("Not valid JSON", f"{path}\n\nLine {error.lineno}: {error.msg}")
            return False

        problem = self.identity_problem(raw)
        if problem is not None:
            self.warn(
                "Unrecognised state file",
                f"{path}\n\n{problem}\n\nExpected a file written by {PROGRAM_NAME} {PROGRAM_VERSION}. "
                "The file was ignored and the canvas is unchanged.",
            )
            return False

        self.apply_state(raw)
        verb = "Loaded" if announce else "Restored"
        self.set_status(f"{verb} {len(self.shapes)} shape(s) from {path}")
        return True

    @staticmethod
    def identity_problem(raw: object) -> str | None:
        """Describe why raw is not one of our state files, or None if it is."""
        if not isinstance(raw, dict):
            return "The file does not contain a JSON object."
        program = raw.get("program")
        if program is None:
            return "It carries no 'program' identifier."
        if program != PROGRAM_NAME:
            return f"It was written by '{program}', not '{PROGRAM_NAME}'."
        if raw.get("version") != PROGRAM_VERSION:
            return f"It declares version '{raw.get('version')}', not '{PROGRAM_VERSION}'."
        if not isinstance(raw.get("shapes"), list):
            return "Its 'shapes' entry is missing or is not a list."
        return None

    def apply_state(self, raw: dict[str, Any]) -> None:
        """Replace the model with the contents of a validated state document."""
        self.suspend_autosave = True
        try:
            self.shapes.clear()
            self.groups.clear()
            self.selection = []
            self.selected = None
            self.pane_target = None
            self.show_in_pane(None)
            self.read_canvas_settings(raw.get("canvas"))

            settings = raw.get("settings")
            if isinstance(settings, dict):
                if "confirm_deletes" in settings:
                    self.confirm_deletes.set(bool(settings["confirm_deletes"]))
                if "color_groups" in settings:
                    self.color_groups.set(bool(settings["color_groups"]))

            floor = self.min_extent
            for position, entry in enumerate(raw.get("shapes", [])):
                record = ShapeRecord.from_json(entry, position, floor)
                if record is not None:
                    self.shapes[record.uuid] = record

            self.apply_groups(raw.get("groups"))
            self.renumber()
            self.redraw_all()
        finally:
            self.suspend_autosave = False
        self.autosave("loading a state file")

    def read_canvas_settings(self, canvas_settings: object) -> None:
        if not isinstance(canvas_settings, dict):
            return
        if "grid_visible" in canvas_settings:
            self.show_grid.set(bool(canvas_settings["grid_visible"]))
        color = canvas_settings.get("grid_color")
        if isinstance(color, str) and color:
            self.grid_color.set(color)
        line = canvas_settings.get("grid_line")
        if line in GRID_LINE_TYPES:
            self.grid_line.set(str(line))
        pitch = canvas_settings.get("grid_cm")
        if isinstance(pitch, (int, float)) and not isinstance(pitch, bool):
            self.grid_cm.set(min(max(float(pitch), GRID_CM_MIN), GRID_CM_MAX))

    def apply_groups(self, groups: object) -> None:
        """Honour the file's `groups` list, which is the authoritative membership record."""
        self.groups.clear()
        if isinstance(groups, list):
            for entry in groups:
                if not isinstance(entry, dict):
                    continue
                identifier = str(entry.get("uuid") or "")
                members = entry.get("members")
                if not identifier or not isinstance(members, list):
                    continue
                self.groups[identifier] = GroupRecord(
                    uuid=identifier,
                    name=str(entry.get("name") or ""),
                    description=str(entry.get("description") or ""),
                    show_name=bool(entry.get("show_name", False)),
                    parent=str(entry.get("parent") or ""),
                )
                for member in members:
                    record = self.shapes.get(str(member))
                    if record is not None:
                        record.group = identifier

            # A file may state the nesting the other way round, as each group's subgroups.
            for entry in groups:
                if not isinstance(entry, dict):
                    continue
                parent = str(entry.get("uuid") or "")
                subgroups = entry.get("subgroups")
                if not parent or not isinstance(subgroups, list):
                    continue
                for child in subgroups:
                    nested = self.groups.get(str(child))
                    if nested is not None and not nested.parent:
                        nested.parent = parent

        # A shape may name a group the list forgot; keep the membership and give it a bare record.
        for record in self.shapes.values():
            if record.group and record.group not in self.groups:
                self.groups[record.group] = GroupRecord(uuid=record.group)

        self.break_group_cycles()
        self.prune_groups()

    def break_group_cycles(self) -> None:
        """A hand-edited file could nest a group inside itself. Cut any loop at the top."""
        for identifier in self.groups:
            seen: set[str] = {identifier}
            current = self.groups[identifier].parent
            while current and current in self.groups:
                if current in seen:
                    self.groups[identifier].parent = ""
                    break
                seen.add(current)
                current = self.groups[current].parent

    def warn(self, title: str, message: str) -> None:
        messagebox.showwarning(title, message, parent=self.root)
        self.set_status(f"{title}. File ignored.")

    def cancel_pending(self) -> None:
        """Drop any timer that would otherwise fire into a window that no longer exists."""
        for job in (self.space_release_job, self.resize_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.space_release_job = None
        self.resize_job = None

    def on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root:  # fires for every child too, so check it is the window
            self.cancel_pending()

    def on_close(self) -> None:
        self.cancel_pending()
        self.write_state(self.state_file)
        self.root.destroy()

    # ------------------------------------------------------------ status line

    def set_status(self, text: str) -> None:
        self.status.set(text)

    def kind_of(self, identifier: str) -> str:
        record = self.shapes.get(identifier)
        return record.kind if record else "shape"

    def report_size(self, identifier: str) -> None:
        record = self.shapes.get(identifier)
        if record is None:
            return
        self.set_status(
            f"{record.kind.capitalize()}: {self.model_to_cm(record.width):.2f} x "
            f"{self.model_to_cm(record.height):.2f} cm"
        )

    def report_position(self, identifier: str) -> None:
        record = self.shapes.get(identifier)
        if record is None:
            return
        cx, cy = record.center
        self.set_status(f"{record.kind.capitalize()} at {self.model_to_cm(cx):.2f}, {self.model_to_cm(cy):.2f} cm")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME.lower(),
        description="Draw movable, resizable rectangles and circles on a snapping grid.",
        epilog=f"State is kept in {DEFAULT_STATE_DIR / STATE_FILENAME} unless --state-dir says otherwise.",
    )
    parser.add_argument("--clean", action="store_true", help="start with an empty canvas instead of the saved state")
    parser.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help=f"load this state file on startup (must be a *{STATE_SUFFIX} file)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        metavar="PATH",
        help=f"keep {STATE_FILENAME} here instead of {DEFAULT_STATE_DIR}",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM_NAME} {PROGRAM_VERSION}")
    return parser.parse_args(argv)


def resolve_state_dir(requested: Path | None) -> Path:
    """Settle on the state directory, failing before the window opens if it is unusable.

    Stricter than --file on purpose: a bad --file costs one load and is visible, whereas an
    unusable state directory would let someone work for an hour with every autosave failing.
    """
    if requested is None:
        return DEFAULT_STATE_DIR

    path = requested.expanduser()
    path = path if path.is_absolute() else Path.cwd() / path
    if path.exists() and not path.is_dir():
        raise SystemExit(f"{PROGRAM_NAME}: --state-dir {path} exists and is not a directory")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SystemExit(f"{PROGRAM_NAME}: cannot use --state-dir {path}: {error.strerror or error}") from error
    return path


def main() -> None:
    args = parse_args()
    state_dir = resolve_state_dir(args.state_dir)
    root = tk.Tk()
    DoodleMyShapes(root, clean=args.clean, startup_file=args.file, state_dir=state_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
