#!/usr/bin/env python3
"""A small tkinter drawing surface with a persistent state file.

Right-click empty canvas to add a rectangle (2 cm wide by 1 cm high) or a circle (2 cm across).
Right-click a shape to change its fill color. Shapes can be dragged to move them,
and dragged by their handles to resize them. Everything snaps to a 1 cm grid.

The Canvas tab holds the drawing; the Json tab shows the live state document.
Every change is written to ~/.<appname>/state.json, and that state can also be
saved to, or loaded from, a file of the user's choosing.

Usage:
    %(prog)s [--clean] [--file STATE.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Any, Literal
from xml.sax.saxutils import escape

# Identity written into every state file, and used for the hidden state directory.
APP_NAME = Path(sys.argv[0]).stem or "shape_canvas"
APP_VERSION = "v1"
STATE_DIR = Path.home() / f".{APP_NAME}"
STATE_FILE = STATE_DIR / "state.json"
STATE_SUFFIX = ".json"
HISTORY_LIMIT = 50  # how many undo steps are kept

EXPORT_MARGIN_CM = 1.0  # blank border around the shapes in an exported picture
EXPORT_FORMATS = (".png", ".jpg", ".jpeg", ".svg")
EXPORT_FONT_SIZE = 12
EXPORT_FONTS = (  # first one that exists wins; Pillow's built-in font is the fallback
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
)

CANVAS_BG = "#f4f4f4"  # very light gray
GRID_COLOR = "#cfe0f5"  # very light blue
GRID_CM = 1.0  # default grid pitch; shapes align to it whether or not the grid is drawn
GRID_CM_CHOICES = (0.5, 1.0, 2.0, 5.0)
GRID_CM_MIN = 0.1
GRID_CM_MAX = 20.0

# Selectable grid line types, as Tk dash patterns. An empty pattern draws a solid line.
GRID_LINE_TYPES: dict[str, tuple[int, ...]] = {
    "Solid": (),
    "Stippled": (1, 3),
    "Dotted": (2, 4),
    "Dashed": (6, 4),
    "Dash-dot": (8, 3, 2, 3),
}
GRID_LINE_DEFAULT = "Stippled"
SHAPE_FILL = "#d3d3d3"  # light gray
SHAPE_OUTLINE = "#5a5a5a"
HANDLE_FILL = "#ffffff"
HANDLE_OUTLINE = "#1f6feb"

HANDLE_RADIUS = 4  # half the side of a square handle, in pixels
MIN_SIZE = 12  # smallest allowed width/height of a shape, in pixels

RECT_W_CM = 2.0  # width
RECT_H_CM = 1.0  # height
CIRCLE_D_CM = 2.0  # diameter

# Offered by the right-click menu on a shape, on top of "Custom color...".
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

TAG_SHAPE = "shape"
TAG_HANDLE = "handle"
TAG_GRID = "grid"
TAG_LABEL = "label"
TAG_MARK = "marquee"  # dashed outline drawn round each shape in a multiple selection

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


@dataclass
class ShapeRecord:
    """Everything worth remembering about one shape on the canvas."""

    kind: str  # "rectangle" or "circle"
    fill: str
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    depth: int = 0  # 0 is the bottom layer; higher numbers sit on top
    group: str = ""  # uuid of the group this shape belongs to, empty when ungrouped

    def to_json(self, box: tuple[float, float, float, float]) -> dict[str, Any]:
        x1, y1, x2, y2 = box
        return {
            "uuid": self.uuid,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "fill": self.fill,
            "depth": self.depth,
            "group": self.group or None,
            "position": {"x": round(x1, 2), "y": round(y1, 2)},
            "size": {"width": round(x2 - x1, 2), "height": round(y2 - y1, 2)},
        }


class PropertiesDialog(tk.Toplevel):
    """Modal editor for a shape's name and description.

    Cancel (or Escape, or the window manager's close button) throws the edits away;
    Save hands them back through `result`.
    """

    def __init__(self, parent: tk.Misc, record: ShapeRecord) -> None:
        super().__init__(parent)
        self.result: tuple[str, str] | None = None

        self.title(f"{record.kind.capitalize()} properties")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Name").grid(row=0, column=0, sticky=tk.W, pady=(0, 6), padx=(0, 8))
        self.name_entry = ttk.Entry(body, width=42)
        self.name_entry.insert(0, record.name)
        self.name_entry.grid(row=0, column=1, sticky=tk.EW, pady=(0, 6))

        ttk.Label(body, text="Description").grid(row=1, column=0, sticky=tk.NW, padx=(0, 8))
        self.description_text = tk.Text(body, width=42, height=5, wrap=tk.WORD, font=("TkDefaultFont",))
        self.description_text.insert("1.0", record.description)
        self.description_text.grid(row=1, column=1, sticky=tk.EW)

        ttk.Label(body, text=record.uuid, foreground="#767676").grid(row=2, column=1, sticky=tk.W, pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=self.save).pack(side=tk.RIGHT, padx=(0, 6))

        self.bind("<Escape>", lambda _event: self.cancel())
        self.name_entry.bind("<Return>", lambda _event: self.save())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        self.name_entry.focus_set()
        self._center_on(parent.winfo_toplevel())

    def _center_on(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def save(self) -> None:
        self.result = (
            self.name_entry.get().strip(),
            self.description_text.get("1.0", tk.END).strip(),
        )
        self.destroy()

    def cancel(self) -> None:
        self.result = None  # nothing is carried back out
        self.destroy()


class ShapeCanvasApp:
    """Menu bar, drawing canvas and status line, wired together."""

    def __init__(self, root: tk.Tk, clean: bool = False, startup_file: Path | None = None) -> None:
        self.root = root
        self.root.title("Shape Canvas")
        self.root.geometry("1060x680")
        self.root.minsize(720, 420)

        self.selected: int | None = None  # the shape the pane edits
        self.selection: list[int] = []  # every shape being dragged, resized or deleted together
        self.drag_items: dict[int, tuple[float, float, float, float]] = {}  # boxes when the drag began
        self.drag_mode: str | None = None  # "move" or "resize"
        self.drag_handle: str | None = None
        self.drag_start: tuple[float, float] = (0.0, 0.0)
        self.drag_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.menu_point: tuple[float, float] = (0.0, 0.0)
        self.shapes: dict[int, ShapeRecord] = {}  # canvas item id -> record
        self.labels: dict[int, int] = {}  # shape item id -> the text item drawn on it
        self.suspend_autosave = False  # set while loading, so a load is one write not many
        self.suspend_history = False  # set while undoing, so an undo is not itself recorded
        self.history: list[tuple[str, dict[str, Any]]] = []  # (what changed, state before it)
        self.baseline: dict[str, Any] | None = None  # the state as of the last recorded change
        self.show_grid = tk.BooleanVar(master=root, value=True)
        self.grid_color = tk.StringVar(master=root, value=GRID_COLOR)
        self.grid_line = tk.StringVar(master=root, value=GRID_LINE_DEFAULT)
        self.grid_cm = tk.DoubleVar(master=root, value=GRID_CM)
        self.pane_item: int | None = None  # the shape the properties pane is showing
        self.confirm_deletes = tk.BooleanVar(master=root, value=True)

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
            label="Show grid",
            accelerator="Ctrl+G",
            variable=self.show_grid,
            command=self.toggle_grid,
        )
        grid_menu.add_separator()
        grid_menu.add_command(label="Grid color...", command=self.choose_grid_color)
        line_menu = tk.Menu(grid_menu, tearoff=False)
        for line_type in GRID_LINE_TYPES:
            line_menu.add_radiobutton(
                label=line_type,
                value=line_type,
                variable=self.grid_line,
                command=self.apply_grid_style,
            )
        grid_menu.add_cascade(label="Line type", menu=line_menu)
        size_menu = tk.Menu(grid_menu, tearoff=False)
        for choice in GRID_CM_CHOICES:
            size_menu.add_radiobutton(
                label=f"{choice:g} cm",
                value=choice,
                variable=self.grid_cm,
                command=self.apply_grid_size,
            )
        size_menu.add_separator()
        size_menu.add_command(label="Custom size...", command=self.choose_grid_size)
        grid_menu.add_cascade(label="Grid size", menu=size_menu)
        view_menu.add_cascade(label="Grid", menu=grid_menu)
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

        self.canvas = tk.Canvas(
            canvas_tab,
            background=CANVAS_BG,
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)
        self._build_properties_pane(canvas_tab)

        self._build_json_tab()
        self._build_popups()

    def _build_properties_pane(self, parent: ttk.Frame) -> None:
        """A live editor for the selected shape, to the right of the canvas."""
        pane = ttk.Frame(parent, padding=(10, 4, 0, 0), width=250)
        pane.grid(row=0, column=1, sticky=tk.NS)
        pane.grid_propagate(False)
        pane.columnconfigure(0, weight=1)

        ttk.Label(pane, text="Properties", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky=tk.W)
        self.pane_subject = ttk.Label(pane, text="No shape selected", foreground="#767676", wraplength=230)
        self.pane_subject.grid(row=1, column=0, sticky=tk.W, pady=(2, 10))

        ttk.Label(pane, text="Name").grid(row=2, column=0, sticky=tk.W)
        self.pane_name = ttk.Entry(pane)
        self.pane_name.grid(row=3, column=0, sticky=tk.EW, pady=(2, 10))

        ttk.Label(pane, text="Description").grid(row=4, column=0, sticky=tk.W)
        description_box = ttk.Frame(pane)
        description_box.grid(row=5, column=0, sticky=tk.NSEW, pady=(2, 8))
        description_box.rowconfigure(0, weight=1)
        description_box.columnconfigure(0, weight=1)
        pane.rowconfigure(5, weight=1)

        self.pane_description = tk.Text(description_box, width=24, height=8, wrap=tk.WORD, font=("TkDefaultFont",))
        scroll = ttk.Scrollbar(description_box, orient=tk.VERTICAL, command=self.pane_description.yview)
        self.pane_description.configure(yscrollcommand=scroll.set)
        self.pane_description.grid(row=0, column=0, sticky=tk.NSEW)
        scroll.grid(row=0, column=1, sticky=tk.NS)

        buttons = ttk.Frame(pane)
        buttons.grid(row=6, column=0, sticky=tk.EW)
        self.undo_button = ttk.Button(buttons, text="Undo", command=self.undo, state=tk.DISABLED)
        self.undo_button.pack(side=tk.LEFT)
        self.pane_apply = ttk.Button(buttons, text="Apply", command=self.commit_pane)
        self.pane_apply.pack(side=tk.RIGHT)

        # Typed text is kept when focus leaves the field, so edits are not lost by clicking away.
        self.pane_name.bind("<Return>", lambda _event: self.commit_pane())
        self.pane_name.bind("<FocusOut>", lambda _event: self.commit_pane())
        self.pane_description.bind("<FocusOut>", lambda _event: self.commit_pane())
        self.pane_name.bind("<Escape>", lambda _event: self.show_in_pane(self.pane_item))
        self.pane_description.bind("<Escape>", lambda _event: self.show_in_pane(self.pane_item))

        self.show_in_pane(None)

    def _build_json_tab(self) -> None:
        """A read-only view of the same document that gets written to disk."""
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
        # Right click on empty canvas: pick a shape to draw.
        self.canvas_popup = tk.Menu(self.canvas, tearoff=False)
        self.canvas_popup.add_command(label="Rectangle", command=lambda: self.add_shape_at_menu_point("rectangle"))
        self.canvas_popup.add_command(label="Circle", command=lambda: self.add_shape_at_menu_point("circle"))

        # Right click on a shape: recolor it or edit its properties.
        self.shape_popup = tk.Menu(self.canvas, tearoff=False)
        self.color_menu = tk.Menu(self.shape_popup, tearoff=False)
        for name, color in FILL_COLORS:
            self.color_menu.add_command(
                label=name,
                background=color,
                activebackground=color,
                command=self._fill_setter(color),
            )
        self.color_menu.add_separator()
        self.color_menu.add_command(label="Custom color...", command=self.choose_custom_fill)
        self.shape_popup.add_cascade(label="Color", menu=self.color_menu)

        depth_menu = tk.Menu(self.shape_popup, tearoff=False)
        depth_menu.add_command(label="Bring to top", accelerator="Ctrl+Shift+Up", command=lambda: self.set_depth("top"))
        depth_menu.add_command(label="Move up one", accelerator="Ctrl+Up", command=lambda: self.set_depth("up"))
        depth_menu.add_command(label="Move down one", accelerator="Ctrl+Down", command=lambda: self.set_depth("down"))
        depth_menu.add_command(
            label="Send to bottom",
            accelerator="Ctrl+Shift+Down",
            command=lambda: self.set_depth("bottom"),
        )
        self.shape_popup.add_cascade(label="Depth", menu=depth_menu)
        self.depth_menu = depth_menu

        self.shape_popup.add_separator()
        self.shape_popup.add_command(label="Group selection", command=self.group_selection)
        self.shape_popup.add_command(label="Ungroup", command=self.ungroup_selection)
        self.shape_popup.add_separator()
        self.shape_popup.add_command(label="Properties...", command=self.open_properties)

        # Releasing the right button while no entry is highlighted takes the menu back down.
        for menu in (self.canvas_popup, self.shape_popup, self.color_menu, self.depth_menu):
            menu.bind("<ButtonRelease-3>", self.dismiss_popup)
            menu.bind("<ButtonRelease-2>", self.dismiss_popup)

    def _build_status_line(self) -> None:
        self.status = tk.StringVar(value="")
        status_bar = ttk.Label(self.root, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W, padding=(8, 3))
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Configure>", lambda _event: self.draw_grid())
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self.refresh_json_view())

        # Right click: Button-3 everywhere, Button-2 as well for macOS trackpads.
        self.canvas.bind("<Button-3>", self.on_popup)
        self.canvas.bind("<Button-2>", self.on_popup)

        self.root.bind("<Delete>", lambda _event: self.delete_selected())
        self.root.bind("<BackSpace>", lambda _event: self.delete_selected())
        self.root.bind("<Escape>", lambda _event: self.select(None))
        self.root.bind("<Control-n>", lambda _event: self.clear_canvas())
        self.root.bind("<Control-z>", lambda _event: self.undo())
        self.root.bind("<Control-G>", lambda _event: self.group_selection())  # Ctrl+Shift+G
        self.root.bind("<Control-U>", lambda _event: self.ungroup_selection())
        self.root.bind("<Control-g>", lambda _event: self.toggle_grid(flip=True))
        self.root.bind("<Control-Up>", lambda _event: self.set_depth("up"))
        self.root.bind("<Control-Down>", lambda _event: self.set_depth("down"))
        self.root.bind("<Control-Shift-Up>", lambda _event: self.set_depth("top"))
        self.root.bind("<Control-Shift-Down>", lambda _event: self.set_depth("bottom"))
        self.root.bind("<Control-o>", lambda _event: self.load_state_dialog())
        self.root.bind("<Control-s>", lambda _event: self.save_state_dialog())
        self.root.bind("<Control-e>", lambda _event: self.export_dialog())
        self.root.bind("<Control-q>", lambda _event: self.on_close())

    # ------------------------------------------------------------------ units

    def cm(self, value: float) -> float:
        """Convert centimeters to pixels for the current display."""
        return value * self.root.winfo_fpixels("1c")

    def to_cm(self, pixels: float) -> float:
        return pixels / self.root.winfo_fpixels("1c")

    # ------------------------------------------------------------------- grid

    @property
    def cell(self) -> float:
        """Grid pitch in pixels."""
        return self.cm(self.grid_pitch())

    def grid_pitch(self) -> float:
        """The grid pitch in centimetres, kept inside sane bounds."""
        try:
            pitch = float(self.grid_cm.get())
        except (tk.TclError, ValueError):
            pitch = GRID_CM
        return min(max(pitch, GRID_CM_MIN), GRID_CM_MAX)

    def choose_grid_size(self) -> None:
        pitch = simpledialog.askfloat(
            "Grid size",
            f"Grid pitch in centimetres ({GRID_CM_MIN:g}-{GRID_CM_MAX:g}):",
            parent=self.root,
            initialvalue=self.grid_pitch(),
            minvalue=GRID_CM_MIN,
            maxvalue=GRID_CM_MAX,
        )
        if pitch is None:
            return
        self.grid_cm.set(pitch)
        self.apply_grid_size()

    def apply_grid_size(self) -> None:
        """Redraw at the new pitch. Shapes already placed keep their positions."""
        self.grid_cm.set(self.grid_pitch())  # write the clamped value back
        self.draw_grid()
        self.autosave("grid size")
        self.set_status(f"Grid size {self.grid_pitch():g} cm. Shapes already placed were not moved.")

    def snap(self, value: float) -> float:
        """Pull one coordinate onto the nearest grid line."""
        cell = self.cell
        return round(value / cell) * cell

    def draw_grid(self) -> None:
        """(Re)draw the grid to fit the current canvas size, in the chosen color and line type."""
        self.canvas.delete(TAG_GRID)
        if not self.show_grid.get():
            return

        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        cell = self.cell
        if cell < 2 or width < 2 or height < 2:  # not mapped yet, or an absurd DPI
            return

        color = self.grid_color.get()
        dash = GRID_LINE_TYPES.get(self.grid_line.get(), GRID_LINE_TYPES[GRID_LINE_DEFAULT])
        pattern = dash if dash else ""  # Tk wants an empty pattern, not (), for a solid line

        steps = int(width / cell) + 1
        for index in range(1, steps):
            x = index * cell
            self.canvas.create_line(x, 0, x, height, fill=color, dash=pattern, tags=(TAG_GRID,))
        steps = int(height / cell) + 1
        for index in range(1, steps):
            y = index * cell
            self.canvas.create_line(0, y, width, y, fill=color, dash=pattern, tags=(TAG_GRID,))

        self.canvas.tag_lower(TAG_GRID)  # always behind the shapes

    def choose_grid_color(self) -> None:
        _rgb, chosen = colorchooser.askcolor(color=self.grid_color.get(), title="Grid color", parent=self.root)
        if not chosen:
            return
        self.grid_color.set(str(chosen))
        self.apply_grid_style()

    def apply_grid_style(self) -> None:
        """Redraw after a color or line-type change, and remember it."""
        if not self.show_grid.get():
            self.show_grid.set(True)  # changing the look implies wanting to see it
        self.draw_grid()
        self.autosave("grid style")
        self.set_status(f"Grid: {self.grid_line.get().lower()} lines in {self.grid_color.get()}.")

    def toggle_grid(self, flip: bool = False) -> None:
        """Show or hide the grid. Alignment is unaffected; only the lines come and go."""
        if flip:
            self.show_grid.set(not self.show_grid.get())
        self.draw_grid()
        self.autosave("grid visibility")
        self.set_status("Grid shown." if self.show_grid.get() else "Grid hidden. Shapes still align to it.")

    # ----------------------------------------------------------- adding shapes

    def on_popup(self, event: tk.Event) -> None:
        """Right click: the fill menu over a shape, the shape menu over empty canvas."""
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.menu_point = (x, y)

        item = self._topmost_shape_at(x, y)
        if item is None and self._handle_under(self._current_item()) is not None:
            item = self.selected  # a handle counts as its own shape

        if item is None:
            self._post(self.canvas_popup, event)
            return

        if item not in self.selection:  # keep a multiple selection the user has built up
            self.select(item)
        elif self.selected != item:
            self.select_items(self.selection, item)
        self._post(self.shape_popup, event)

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

    # ------------------------------------------------------------- fill color

    def set_fill(self, color: str) -> None:
        targets = [item for item in self.selection if item in self.shapes]
        if not targets:
            self.set_status("Right-click a shape to change its fill.")
            return

        for item in targets:
            self.canvas.itemconfigure(item, fill=color)
            self.shapes[item].fill = color
            self.update_label(item)  # the name may need a lighter or darker ink

        self.autosave("recolor")
        subject = self._kind(targets[0]) if len(targets) == 1 else f"{len(targets)} shapes"
        self.set_status(f"{subject.capitalize()} filled with {color}.")

    def choose_custom_fill(self) -> None:
        record = self.shapes.get(self.selected) if self.selected is not None else None
        if record is None:
            return
        _rgb, chosen = colorchooser.askcolor(color=record.fill, title="Fill color", parent=self.root)
        if chosen:
            self.set_fill(str(chosen))

    # ------------------------------------------------------------- properties

    def show_in_pane(self, item: int | None) -> None:
        """Point the side pane at a shape, or empty it when nothing is selected."""
        record = self.shapes.get(item) if item is not None else None
        self.pane_item = item if record is not None else None

        state: Literal["normal", "disabled"] = "normal" if record is not None else "disabled"
        self.pane_name.configure(state=tk.NORMAL)
        self.pane_name.delete(0, tk.END)
        self.pane_description.configure(state=tk.NORMAL)
        self.pane_description.delete("1.0", tk.END)

        if record is None:
            self.pane_subject.configure(text="No shape selected")
        else:
            summary = f"{record.kind.capitalize()} · depth {record.depth}"
            if record.group:
                summary += f" · group {record.group[:8]}"
            if len(self.selection) > 1:
                summary += f" · {len(self.selection)} selected, editing this one"
            self.pane_subject.configure(text=f"{summary}\n{record.uuid}")
            self.pane_name.insert(0, record.name)
            self.pane_description.insert("1.0", record.description)

        self.pane_name.configure(state=state)
        self.pane_description.configure(state=state)
        self.pane_apply.configure(state=state)

    def commit_pane(self) -> None:
        """Write whatever is in the pane back to the shape it belongs to."""
        item = self.pane_item
        if item is None:
            return
        record = self.shapes.get(item)
        if record is None:
            return

        name = self.pane_name.get().strip()
        description = self.pane_description.get("1.0", tk.END).strip()
        if (name, description) == (record.name, record.description):
            return  # nothing typed, so nothing to save

        record.name, record.description = name, description
        self.update_label(item)
        self.autosave(f"edit of {record.kind} properties")
        self.set_status(f"Saved properties for {name or record.uuid[:8]}.")

    def open_properties(self) -> None:
        """Edit the name and description of the shape the popup was opened on."""
        item = self.selected
        if item is None or item not in self.shapes:
            self.set_status("Right-click a shape to edit its properties.")
            return
        record = self.shapes[item]

        dialog = PropertiesDialog(self.root, record)
        dialog.grab_set()  # modal: the canvas is off limits until it closes
        self.root.wait_window(dialog)

        if dialog.result is None:
            self.set_status(f"Properties of {record.kind} left unchanged.")
            return

        record.name, record.description = dialog.result
        self.update_label(item)
        self.show_in_pane(item)
        self.autosave(f"edit of {record.kind} properties")
        label = record.name or record.uuid[:8]
        self.set_status(f"Saved properties for {label}.")

    def add_shape_at_menu_point(self, kind: str) -> None:
        self.add_shape(kind, *self.menu_point)

    def add_shape_at_center(self, kind: str) -> None:
        self.canvas.update_idletasks()
        self.add_shape(kind, self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)

    def add_shape(self, kind: str, cx: float, cy: float) -> int:
        """Draw a new shape of the given kind, centered near (cx, cy) and snapped to the grid."""
        if kind == "rectangle":
            width, height = self.cm(RECT_W_CM), self.cm(RECT_H_CM)
        else:
            width = height = self.cm(CIRCLE_D_CM)

        # Snap the top-left corner: with whole-centimetre shapes that puts every edge on a grid line.
        left, top = self.snap(cx - width / 2), self.snap(cy - height / 2)
        record = ShapeRecord(kind=kind, fill=SHAPE_FILL, depth=len(self.shapes))  # new shapes land on top
        item = self._draw(record, (left, top, left + width, top + height))
        self._renumber(self._by_depth())
        self.restack()
        self.select(item)
        self.autosave(f"adding the {kind}")
        self.set_status(f"Added {kind}. Drag it to move, drag a handle to resize.")
        return item

    def _draw(self, record: ShapeRecord, box: tuple[float, float, float, float]) -> int:
        """Put one shape on the canvas and remember it. Shared by drawing and loading."""
        create = self.canvas.create_rectangle if record.kind == "rectangle" else self.canvas.create_oval
        item = create(*box, fill=record.fill, outline=SHAPE_OUTLINE, width=1, tags=(TAG_SHAPE,))
        self.shapes[item] = record
        self.update_label(item)
        return item

    # ----------------------------------------------------------- shape labels

    def update_label(self, item: int) -> None:
        """Draw the shape's name on it, or take the text away when the name is empty."""
        existing = self.labels.pop(item, None)
        if existing is not None:
            self.canvas.delete(existing)

        record = self.shapes.get(item)
        if record is None or not record.name:
            return

        x1, y1, x2, y2 = self.canvas.coords(item)
        label = self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=record.name,
            fill=self._text_color(record.fill),
            width=max(x2 - x1 - 6, 10),  # wrap inside the shape rather than spilling out
            justify=tk.CENTER,
            tags=(TAG_LABEL,),
        )
        self.labels[item] = label
        self.canvas.tag_raise(label, item)

    def _place_label(self, item: int) -> None:
        """Keep the label centred while its shape is dragged or resized."""
        label = self.labels.get(item)
        if label is None:
            return
        x1, y1, x2, y2 = self.canvas.coords(item)
        self.canvas.coords(label, (x1 + x2) / 2, (y1 + y2) / 2)
        self.canvas.itemconfigure(label, width=max(x2 - x1 - 6, 10))

    def _text_color(self, fill: str) -> str:
        """Black on light fills, white on dark ones, so the name stays readable."""
        try:
            red, green, blue = self.canvas.winfo_rgb(fill)
        except tk.TclError:
            return "#101010"
        luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 65535
        return "#101010" if luminance > 0.55 else "#ffffff"

    # -------------------------------------------------------------- selection

    def select(self, item: int | None, add: bool = False) -> None:
        """Select one shape, or with `add` toggle it into the current selection.

        Selecting any member of a group selects the whole group, which is what makes
        a group move and resize as one piece.
        """
        if item is None:
            self.select_items([], None)
            return

        family = self.group_members(item)
        if not add:
            self.select_items(family, item)
            return

        if item in self.selection:  # shift-clicking a selected shape takes it back out
            remaining = [other for other in self.selection if other not in family]
            self.select_items(remaining, remaining[-1] if remaining else None)
        else:
            self.select_items(self.selection + family, item)

    def select_items(self, items: list[int], primary: int | None) -> None:
        """Replace the selection. `primary` is the one the properties pane edits."""
        if primary != self.pane_item:
            self.commit_pane()  # don't lose text typed for the shape we are leaving

        seen: dict[int, None] = {}  # an ordered set: keep click order, drop duplicates
        for item in items:
            if item in self.shapes:
                seen[item] = None
        self.selection = list(seen)

        if primary not in self.selection:
            primary = self.selection[-1] if self.selection else None
        self.selected = primary

        self._redraw_selection()
        self.show_in_pane(primary)
        if primary is None:
            self.set_status("Nothing selected.")
        elif len(self.selection) > 1:
            group = self.shapes[primary].group
            what = "group" if group else "shapes"
            self.set_status(f"{len(self.selection)} {what} selected. Drag to move them together.")
        else:
            self._report_size(primary)

    def selection_box(self) -> tuple[float, float, float, float] | None:
        """The bounding box around everything selected, which is what the handles frame."""
        boxes = [self.canvas.coords(item) for item in self.selection if item in self.shapes]
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    def _redraw_selection(self) -> None:
        self.canvas.delete(TAG_HANDLE)
        self.canvas.delete(TAG_MARK)
        box = self.selection_box()
        if box is None:
            return

        if len(self.selection) > 1:
            # Show which shapes are in the selection, since the handles only frame the whole.
            for item in self.selection:
                mx1, my1, mx2, my2 = self.canvas.coords(item)
                self.canvas.create_rectangle(
                    mx1 - 2,
                    my1 - 2,
                    mx2 + 2,
                    my2 + 2,
                    outline=HANDLE_OUTLINE,
                    dash=(3, 2),
                    tags=(TAG_MARK,),
                )

        x1, y1, x2, y2 = box
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        positions = {
            "nw": (x1, y1),
            "n": (mx, y1),
            "ne": (x2, y1),
            "e": (x2, my),
            "se": (x2, y2),
            "s": (mx, y2),
            "sw": (x1, y2),
            "w": (x1, my),
        }
        for name, (hx, hy) in positions.items():
            self.canvas.create_rectangle(
                hx - HANDLE_RADIUS,
                hy - HANDLE_RADIUS,
                hx + HANDLE_RADIUS,
                hy + HANDLE_RADIUS,
                fill=HANDLE_FILL,
                outline=HANDLE_OUTLINE,
                width=1,
                tags=(TAG_HANDLE, f"handle:{name}"),
            )

    def _fill_setter(self, color: str) -> Callable[[], None]:
        """Bind one palette color to a menu command."""
        return lambda: self.set_fill(color)

    def _current_item(self) -> int | None:
        """The canvas item under the pointer, if any."""
        current = self.canvas.find_withtag("current")
        return current[0] if current else None

    def _handle_under(self, item: int | None) -> str | None:
        if item is None:
            return None
        for tag in self.canvas.gettags(item):
            if tag.startswith("handle:"):
                return tag.split(":", 1)[1]
        return None

    def _topmost_shape_at(self, x: float, y: float) -> int | None:
        for item in reversed(self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)):
            if TAG_SHAPE in self.canvas.gettags(item):
                return item
        return None

    # ----------------------------------------------------------------- groups

    def group_members(self, item: int) -> list[int]:
        """Every shape sharing this shape's group, or just the shape when it is on its own."""
        record = self.shapes.get(item)
        if record is None:
            return []
        if not record.group:
            return [item]
        return [other for other in self._by_depth() if self.shapes[other].group == record.group]

    def group_index(self) -> dict[str, list[int]]:
        """Group uuid -> its member items, in depth order."""
        groups: dict[str, list[int]] = {}
        for item in self._by_depth():
            group = self.shapes[item].group
            if group:
                groups.setdefault(group, []).append(item)
        return groups

    def group_selection(self) -> None:
        if len(self.selection) < 2:
            self.set_status("Shift-click a second shape before grouping.")
            return

        identifier = str(uuid.uuid4())
        for item in self.selection:
            self.shapes[item].group = identifier
        self._redraw_selection()
        self.autosave("grouping")
        self.set_status(f"Grouped {len(self.selection)} shapes as {identifier[:8]}.")

    def ungroup_selection(self) -> None:
        grouped = [item for item in self.selection if self.shapes[item].group]
        if not grouped:
            self.set_status("Nothing in the selection belongs to a group.")
            return

        for item in grouped:
            self.shapes[item].group = ""
        self._redraw_selection()
        self.autosave("ungrouping")
        self.set_status(f"Ungrouped {len(grouped)} shapes.")

    # ------------------------------------------------------------------ depth

    def _by_depth(self) -> list[int]:
        """Canvas items bottom to top. Equal depths keep their insertion order."""
        return sorted(self.shapes, key=lambda item: self.shapes[item].depth)

    def _renumber(self, order: list[int]) -> None:
        """Give the listed items depths 0..n-1 so the numbers stay small and gap-free."""
        for depth, item in enumerate(order):
            self.shapes[item].depth = depth

    def restack(self) -> None:
        """Make the canvas stacking order match the recorded depths."""
        for item in self._by_depth():
            self.canvas.tag_raise(item)
            label = self.labels.get(item)
            if label is not None:
                self.canvas.tag_raise(label, item)  # a name stays on its own shape
        self.canvas.tag_raise(TAG_HANDLE)  # handles stay reachable above every shape
        self.canvas.tag_lower(TAG_GRID)  # and the grid stays underneath everything

    def set_depth(self, action: str) -> None:
        """Move the selected shape through the stack: top, bottom, or one step either way."""
        item = self.selected
        if item is None or item not in self.shapes:
            self.set_status("Select a shape first, then change its depth.")
            return

        order = self._by_depth()
        if len(order) < 2:
            self.set_status("Depth only matters once there are two shapes.")
            return

        index = order.index(item)
        target = {"top": len(order) - 1, "bottom": 0, "up": index + 1, "down": index - 1}[action]
        target = max(0, min(target, len(order) - 1))
        if target == index:
            edge = "top" if index else "bottom"
            self.set_status(f"{self._kind(item).capitalize()} is already at the {edge}.")
            return

        order.insert(target, order.pop(index))
        self._renumber(order)
        self.restack()
        self.autosave(f"depth change of {self._kind(item)}")
        depth = self.shapes[item].depth
        self.set_status(f"{self._kind(item).capitalize()} moved to depth {depth} of {len(order) - 1}.")

    def on_press(self, event: tk.Event) -> None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        shift = bool(self._modifiers(event) & 0x0001)
        handle = self._handle_under(self._current_item())

        if handle is not None and self.selection:
            self._begin_drag("resize", x, y)
            self.drag_handle = handle
            return

        item = self._topmost_shape_at(x, y)
        if item is None:
            self.drag_mode = None
            if not shift:  # shift-clicking empty space keeps what is already selected
                self.select(None)
            return

        self.select(item, add=shift)
        self._begin_drag("move", x, y)
        self.drag_handle = None

    def _begin_drag(self, mode: str, x: float, y: float) -> None:
        """Remember where everything started, so snapping measures from a fixed origin."""
        self.drag_mode = mode
        self.drag_start = (x, y)
        self.drag_items = {}
        for item in self.selection:
            ix1, iy1, ix2, iy2 = self.canvas.coords(item)
            self.drag_items[item] = (ix1, iy1, ix2, iy2)
        box = self.selection_box()
        self.drag_bbox = box if box is not None else (0.0, 0.0, 0.0, 0.0)

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_mode is None or not self.selection:
            return

        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.drag_mode == "move":
            self._drag_move(x, y)
        else:
            keep_ratio = bool(self._modifiers(event) & 0x0001)  # Shift held
            self._drag_resize(self._resized_box(x, y, keep_ratio))

        self._redraw_selection()
        if self.selected is not None:
            if self.drag_mode == "move":
                self._report_position(self.selected)
            else:
                self._report_size(self.selected)

    def _drag_move(self, x: float, y: float) -> None:
        """Shift the whole selection, snapping the group's own top-left to the grid."""
        left = self.snap(self.drag_bbox[0] + (x - self.drag_start[0]))
        top = self.snap(self.drag_bbox[1] + (y - self.drag_start[1]))
        dx, dy = left - self.drag_bbox[0], top - self.drag_bbox[1]
        for item, (ix1, iy1, ix2, iy2) in self.drag_items.items():
            self.canvas.coords(item, ix1 + dx, iy1 + dy, ix2 + dx, iy2 + dy)
            self._place_label(item)

    def _drag_resize(self, box: tuple[float, float, float, float]) -> None:
        """Scale every selected shape into the new frame, keeping their relative places."""
        ox1, oy1, ox2, oy2 = self.drag_bbox
        nx1, ny1, nx2, ny2 = box
        scale_x = (nx2 - nx1) / (ox2 - ox1) if ox2 > ox1 else 1.0
        scale_y = (ny2 - ny1) / (oy2 - oy1) if oy2 > oy1 else 1.0
        for item, (ix1, iy1, ix2, iy2) in self.drag_items.items():
            self.canvas.coords(
                item,
                nx1 + (ix1 - ox1) * scale_x,
                ny1 + (iy1 - oy1) * scale_y,
                nx1 + (ix2 - ox1) * scale_x,
                ny1 + (iy2 - oy1) * scale_y,
            )
            self._place_label(item)

    def on_release(self, _event: tk.Event) -> None:
        if self.drag_mode is not None and self.selection:
            if self.selected is not None:
                self._report_size(self.selected)
            self.autosave("move" if self.drag_mode == "move" else "resize")  # written once the drag settles
        self.drag_mode = None
        self.drag_handle = None
        self.drag_items = {}

    @staticmethod
    def _modifiers(event: tk.Event) -> int:
        return event.state if isinstance(event.state, int) else 0

    def _resized_box(self, x: float, y: float, keep_ratio: bool) -> tuple[float, float, float, float]:
        """Return the new frame for the current resize drag, snapped to the grid."""
        x1, y1, x2, y2 = self.drag_bbox
        handle = self.drag_handle or ""
        x, y = self.snap(x), self.snap(y)
        smallest = max(MIN_SIZE, self.cell)  # a shape never gets thinner than one grid cell

        if "w" in handle:
            x1 = x
        if "e" in handle:
            x2 = x
        if "n" in handle:
            y1 = y
        if "s" in handle:
            y2 = y

        # Keep the shape at least one cell across by pushing back the edge being dragged.
        if x2 - x1 < smallest:
            if "w" in handle:
                x1 = x2 - smallest
            else:
                x2 = x1 + smallest
        if y2 - y1 < smallest:
            if "n" in handle:
                y1 = y2 - smallest
            else:
                y2 = y1 + smallest

        if keep_ratio and len(handle) == 2:
            old_w = max(self.drag_bbox[2] - self.drag_bbox[0], 1.0)
            old_h = max(self.drag_bbox[3] - self.drag_bbox[1], 1.0)
            new_h = max(self.snap((x2 - x1) * (old_h / old_w)), smallest)
            if "n" in handle:
                y1 = y2 - new_h
            else:
                y2 = y1 + new_h

        return x1, y1, x2, y2

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
        targets = [item for item in self.selection if item in self.shapes]
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

        for item in targets:
            self.shapes.pop(item, None)
            label = self.labels.pop(item, None)
            if label is not None:
                self.canvas.delete(label)
            self.canvas.delete(item)

        self.pane_item = None  # their records are gone; nothing to commit back to
        self.selection = []
        self.selected = None
        self.canvas.delete(TAG_HANDLE)
        self.canvas.delete(TAG_MARK)
        self.show_in_pane(None)
        self._renumber(self._by_depth())  # close the gaps the deleted layers left
        self.autosave(f"deletion of {described}")
        self.set_status(f"Deleted {described}.")

    def clear_canvas(self) -> None:
        count = len(self.shapes)
        if count and not self.confirm_delete(f"Delete all {count} shape(s) from the canvas?"):
            self.set_status("Clear cancelled.")
            return
        self.canvas.delete("all")
        self.shapes.clear()
        self.labels.clear()
        self.selection = []
        self.selected = None
        self.pane_item = None
        self.show_in_pane(None)
        self.draw_grid()  # "all" took the grid with it
        self.autosave("clearing the canvas")
        self.set_status("Canvas cleared. Right-click to add a shape.")

    def show_help(self) -> None:
        self.set_status(
            "Right-click empty canvas to add a shape, right-click a shape to recolor it. "
            "Drag to move, drag a handle to resize; everything snaps to the grid (Ctrl+G hides it). "
            "Del removes the selection after a confirmation, Ctrl+Z undoes the last change."
        )

    def on_hover(self, event: tk.Event) -> None:
        if self.drag_mode is not None:
            return
        handle = self._handle_under(self._current_item())
        if handle is not None:
            cursor = HANDLE_CURSORS.get(handle, "sizing")
        elif self._topmost_shape_at(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)) is not None:
            cursor = "fleur"
        else:
            cursor = ""
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            self.canvas.configure(cursor="")

    # ----------------------------------------------------------------- export

    def content_box(self) -> tuple[float, float, float, float] | None:
        """The bounding box of every shape, widened by a one-centimetre margin."""
        boxes = [self.canvas.coords(item) for item in self.shapes]
        if not boxes:
            return None
        margin = self.cm(EXPORT_MARGIN_CM)
        return (
            min(box[0] for box in boxes) - margin,
            min(box[1] for box in boxes) - margin,
            max(box[2] for box in boxes) + margin,
            max(box[3] for box in boxes) + margin,
        )

    def export_dialog(self) -> None:
        """Ask where to write the picture, then write it in the format the name implies."""
        if not self.shapes:
            self.set_status("Draw something before exporting.")
            return

        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export as picture",
            initialdir=str(Path.home()),
            initialfile=f"{APP_NAME}.png",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg *.jpeg"), ("SVG drawing", "*.svg")],
        )
        if chosen:
            self.export_to(Path(chosen))

    def export_to(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix not in EXPORT_FORMATS:
            self._warn(
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
                path.write_text(self._as_svg(box), encoding="utf-8")
            else:
                self._write_raster(path, box, suffix)
        except OSError as error:
            self._warn("Could not write the picture", f"{path}\n\n{error.strerror or error}")
            return False
        except ImportError:
            self._warn(
                "Pillow is not installed",
                "PNG and JPEG export needs the Pillow package:\n\n    pip install pillow\n\n"
                "SVG export works without it.",
            )
            return False

        width, height = box[2] - box[0], box[3] - box[1]
        self.set_status(f"Exported {len(self.shapes)} shape(s) to {path} ({int(width)}x{int(height)} px).")
        return True

    def _export_items(
        self, box: tuple[float, float, float, float]
    ) -> list[tuple[ShapeRecord, tuple[float, float, float, float]]]:
        """Shapes in drawing order, with coordinates moved into the picture's own frame."""
        drawn = []
        for item in self._by_depth():
            x1, y1, x2, y2 = self.canvas.coords(item)
            drawn.append((self.shapes[item], (x1 - box[0], y1 - box[1], x2 - box[0], y2 - box[1])))
        return drawn

    def _as_svg(self, box: tuple[float, float, float, float]) -> str:
        width, height = box[2] - box[0], box[3] - box[1]
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}">',
            f'<rect width="100%" height="100%" fill="{CANVAS_BG}"/>',
        ]
        for record, (x1, y1, x2, y2) in self._export_items(box):
            shape_width, shape_height = x2 - x1, y2 - y1
            common = f'fill="{record.fill}" stroke="{SHAPE_OUTLINE}" stroke-width="1"'
            if record.kind == "rectangle":
                parts.append(
                    f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{shape_width:.2f}" height="{shape_height:.2f}" {common}/>'
                )
            else:
                parts.append(
                    f'<ellipse cx="{x1 + shape_width / 2:.2f}" cy="{y1 + shape_height / 2:.2f}" '
                    f'rx="{shape_width / 2:.2f}" ry="{shape_height / 2:.2f}" {common}/>'
                )
            if record.name:
                parts.append(
                    f'<text x="{x1 + shape_width / 2:.2f}" y="{y1 + shape_height / 2:.2f}" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'font-family="sans-serif" font-size="{EXPORT_FONT_SIZE}" '
                    f'fill="{self._text_color(record.fill)}">{escape(record.name)}</text>'
                )
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    def _write_raster(self, path: Path, box: tuple[float, float, float, float], suffix: str) -> None:
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

        for record, (x1, y1, x2, y2) in self._export_items(box):
            corners = (x1, y1, max(x2, x1 + 1), max(y2, y1 + 1))
            if record.kind == "rectangle":
                draw.rectangle(corners, fill=record.fill, outline=SHAPE_OUTLINE, width=1)
            else:
                draw.ellipse(corners, fill=record.fill, outline=SHAPE_OUTLINE, width=1)
            if record.name:
                draw.text(
                    ((corners[0] + corners[2]) / 2, (corners[1] + corners[3]) / 2),
                    record.name,
                    fill=self._text_color(record.fill),
                    font=font,
                    anchor="mm",
                )

        if suffix in (".jpg", ".jpeg"):
            image.save(path, "JPEG", quality=92)
        else:
            image.save(path, "PNG")

    # ------------------------------------------------------------ state files

    def to_state(self) -> dict[str, Any]:
        """The whole canvas as the dict that gets written to disk."""
        shapes = []
        for item, record in self.shapes.items():
            x1, y1, x2, y2 = self.canvas.coords(item)
            shapes.append(record.to_json((x1, y1, x2, y2)))
        return {
            "program": APP_NAME,
            "version": APP_VERSION,
            "canvas": {
                "background": CANVAS_BG,
                "grid_cm": self.grid_pitch(),
                "grid_visible": self.show_grid.get(),
                "grid_color": self.grid_color.get(),
                "grid_line": self.grid_line.get(),
            },
            "settings": {"confirm_deletes": self.confirm_deletes.get()},
            "groups": [
                {"uuid": identifier, "members": [self.shapes[item].uuid for item in members]}
                for identifier, members in self.group_index().items()
            ],
            "shapes": shapes,
        }

    def show_tab(self, index: int) -> None:
        """Bring one notebook tab to the front (ttk.Notebook.select is untyped)."""
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
            self._record(label)
        self.refresh_json_view()
        self._refresh_undo_button()
        self.write_state(STATE_FILE)

    def _record(self, label: str) -> None:
        """Push the state as it was before this change onto the undo stack."""
        if self.baseline is not None:
            self.history.append((label, self.baseline))
            del self.history[:-HISTORY_LIMIT]  # keep only the most recent steps
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
            initialdir=str(STATE_DIR if STATE_DIR.is_dir() else Path.home()),
            initialfile=f"{APP_NAME}-state{STATE_SUFFIX}",
            defaultextension=STATE_SUFFIX,
            filetypes=[("State files", f"*{STATE_SUFFIX}"), ("All files", "*.*")],
        )
        if not chosen:
            return
        if self.write_state(Path(chosen)):
            self.set_status(f"Saved {len(self.shapes)} shape(s) to {chosen}")

    def load_state_dialog(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open state",
            initialdir=str(STATE_DIR if STATE_DIR.is_dir() else Path.home()),
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
                self._warn(
                    "Not a JSON file",
                    f"{startup_file}\n\nOnly '*{STATE_SUFFIX}' files are loaded. "
                    "The file was ignored and the canvas is empty.",
                )
                return
            self.load_state(startup_file, announce=True)
            return

        if clean:
            self.set_status(f"Started clean. {STATE_FILE} was not loaded; the next change will overwrite it.")
            return

        self.restore_autosave()

    def restore_autosave(self) -> None:
        """Bring back the last automatically saved canvas, if there is one."""
        if STATE_FILE.is_file():
            self.load_state(STATE_FILE, announce=False)

    def load_state(self, path: Path, announce: bool) -> bool:
        """Read, check and apply a state file. A file that isn't ours is reported and ignored."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            self._warn("Cannot read file", f"{path}\n\n{error.strerror or error}")
            return False
        except json.JSONDecodeError as error:
            self._warn("Not valid JSON", f"{path}\n\nLine {error.lineno}: {error.msg}")
            return False

        problem = self._identity_problem(raw)
        if problem is not None:
            self._warn(
                "Unrecognised state file",
                f"{path}\n\n{problem}\n\nExpected a file written by {APP_NAME} {APP_VERSION}. "
                "The file was ignored and the canvas is unchanged.",
            )
            return False

        self.apply_state(raw)
        if announce:
            self.set_status(f"Loaded {len(self.shapes)} shape(s) from {path}")
        else:
            self.set_status(f"Restored {len(self.shapes)} shape(s) from {path}")
        return True

    @staticmethod
    def _identity_problem(raw: object) -> str | None:
        """Describe why raw is not one of our state files, or None if it is."""
        if not isinstance(raw, dict):
            return "The file does not contain a JSON object."
        program = raw.get("program")
        if program is None:
            return "It carries no 'program' identifier."
        if program != APP_NAME:
            return f"It was written by '{program}', not '{APP_NAME}'."
        if raw.get("version") != APP_VERSION:
            return f"It declares version '{raw.get('version')}', not '{APP_VERSION}'."
        if not isinstance(raw.get("shapes"), list):
            return "Its 'shapes' entry is missing or is not a list."
        return None

    def apply_state(self, raw: dict[str, Any]) -> None:
        """Replace the canvas contents with the shapes described by a validated state dict."""
        self.suspend_autosave = True
        try:
            self.canvas.delete("all")
            self.shapes.clear()
            self.labels.clear()
            self.selection = []
            self.selected = None
            self.pane_item = None
            self.show_in_pane(None)
            canvas_settings = raw.get("canvas")
            if isinstance(canvas_settings, dict):
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

            settings = raw.get("settings")
            if isinstance(settings, dict) and "confirm_deletes" in settings:
                self.confirm_deletes.set(bool(settings["confirm_deletes"]))
            self.draw_grid()
            for position, entry in enumerate(raw.get("shapes", [])):
                box = self._box_from(entry)
                if box is None:
                    continue
                kind = "rectangle" if entry.get("kind") == "rectangle" else "circle"
                identifier = str(entry.get("uuid") or uuid.uuid4())
                fill = str(entry.get("fill") or SHAPE_FILL)
                depth = entry.get("depth")
                self._draw(
                    ShapeRecord(
                        kind=kind,
                        fill=fill,
                        uuid=identifier,
                        name=str(entry.get("name") or ""),
                        description=str(entry.get("description") or ""),
                        depth=depth if isinstance(depth, int) else position,  # file order is the fallback
                        group=str(entry.get("group") or ""),
                    ),
                    box,
                )
            self._apply_groups(raw.get("groups"))
            self._renumber(self._by_depth())
            self.restack()
        finally:
            self.suspend_autosave = False
        self.autosave("loading a state file")

    def _apply_groups(self, groups: object) -> None:
        """Honour the file's `groups` list, which is the authoritative membership record."""
        if not isinstance(groups, list):
            return
        by_uuid = {record.uuid: record for record in self.shapes.values()}
        for entry in groups:
            if not isinstance(entry, dict):
                continue
            identifier = str(entry.get("uuid") or "")
            members = entry.get("members")
            if not identifier or not isinstance(members, list):
                continue
            for member in members:
                record = by_uuid.get(str(member))
                if record is not None:
                    record.group = identifier

    @staticmethod
    def _box_from(entry: object) -> tuple[float, float, float, float] | None:
        """Pull a usable bounding box out of one shape entry, or None if it is malformed.

        The current layout is a position plus a size. Files written by an earlier build
        carried two corner points instead, so those are still understood.
        """
        if not isinstance(entry, dict):
            return None
        position = entry.get("position")
        if not isinstance(position, dict):
            return None

        size = entry.get("size")
        if isinstance(size, dict):
            values = ShapeCanvasApp._floats(position, ("x", "y")) or ()
            extent = ShapeCanvasApp._floats(size, ("width", "height"))
            if not values or not extent:
                return None
            left, top = values
            return left, top, left + max(extent[0], MIN_SIZE), top + max(extent[1], MIN_SIZE)

        corners = ShapeCanvasApp._floats(position, ("x1", "y1", "x2", "y2"))
        if not corners:
            return None
        left, right = sorted((corners[0], corners[2]))
        top, bottom = sorted((corners[1], corners[3]))
        return left, top, max(right, left + MIN_SIZE), max(bottom, top + MIN_SIZE)

    @staticmethod
    def _floats(mapping: dict[str, Any], keys: tuple[str, ...]) -> tuple[float, ...] | None:
        """Read a fixed set of numeric keys, or None if any is missing or not a number."""
        try:
            return tuple(float(mapping[key]) for key in keys)
        except (KeyError, TypeError, ValueError):
            return None

    def _warn(self, title: str, message: str) -> None:
        messagebox.showwarning(title, message, parent=self.root)
        self.set_status(f"{title}. File ignored.")

    def on_close(self) -> None:
        self.write_state(STATE_FILE)
        self.root.destroy()

    # ------------------------------------------------------------ status line

    def set_status(self, text: str) -> None:
        self.status.set(text)

    def _report_size(self, item: int) -> None:
        x1, y1, x2, y2 = self.canvas.coords(item)
        self.set_status(
            f"{self._kind(item).capitalize()}: {self.to_cm(x2 - x1):.2f} x {self.to_cm(y2 - y1):.2f} cm "
            f"({int(x2 - x1)} x {int(y2 - y1)} px)"
        )

    def _report_position(self, item: int) -> None:
        x1, y1, x2, y2 = self.canvas.coords(item)
        self.set_status(f"{self._kind(item).capitalize()} at ({int((x1 + x2) / 2)}, {int((y1 + y2) / 2)}) px")

    def _kind(self, item: int) -> str:
        record = self.shapes.get(item)
        return record.kind if record else "shape"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Draw movable, resizable rectangles and circles on a canvas.",
        epilog=f"State is kept in {STATE_FILE}",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="start with an empty canvas instead of restoring the saved state",
    )
    parser.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help=f"load this state file on startup instead of the saved state (must be a *{STATE_SUFFIX} file)",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    root = tk.Tk()
    ShapeCanvasApp(root, clean=args.clean, startup_file=args.file)
    root.mainloop()


if __name__ == "__main__":
    main()
