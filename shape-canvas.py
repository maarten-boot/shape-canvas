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

# Identity written into every state file, and used for the hidden state directory.
APP_NAME = Path(sys.argv[0]).stem or "shape_canvas"
APP_VERSION = "v1"
STATE_DIR = Path.home() / f".{APP_NAME}"
STATE_FILE = STATE_DIR / "state.json"
STATE_SUFFIX = ".json"

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

    def to_json(self, box: tuple[float, float, float, float]) -> dict[str, Any]:
        x1, y1, x2, y2 = box
        return {
            "uuid": self.uuid,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "fill": self.fill,
            "depth": self.depth,
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

        self.selected: int | None = None
        self.drag_mode: str | None = None  # "move" or "resize"
        self.drag_handle: str | None = None
        self.drag_start: tuple[float, float] = (0.0, 0.0)
        self.drag_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.menu_point: tuple[float, float] = (0.0, 0.0)
        self.shapes: dict[int, ShapeRecord] = {}  # canvas item id -> record
        self.suspend_autosave = False  # set while loading, so a load is one write not many
        self.show_grid = tk.BooleanVar(master=root, value=True)
        self.grid_color = tk.StringVar(master=root, value=GRID_COLOR)
        self.grid_line = tk.StringVar(master=root, value=GRID_LINE_DEFAULT)
        self.grid_cm = tk.DoubleVar(master=root, value=GRID_CM)
        self.pane_item: int | None = None  # the shape the properties pane is showing

        self._build_menu_bar()
        self._build_central_frame()
        self._build_status_line()
        self._bind_events()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.set_status("Right-click the canvas to add a shape.")
        self.startup_load(clean=clean, startup_file=startup_file)
        self.refresh_json_view()

    # ------------------------------------------------------------------ setup

    def _build_menu_bar(self) -> None:
        menu_bar = tk.Menu(self.root)

        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Open state...", accelerator="Ctrl+O", command=self.load_state_dialog)
        file_menu.add_command(label="Save state as...", accelerator="Ctrl+S", command=self.save_state_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Clear canvas", accelerator="Ctrl+N", command=self.clear_canvas)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_close)
        menu_bar.add_cascade(label="File", menu=file_menu)

        shape_menu = tk.Menu(menu_bar, tearoff=False)
        shape_menu.add_command(label="Add rectangle", command=lambda: self.add_shape_at_center("rectangle"))
        shape_menu.add_command(label="Add circle", command=lambda: self.add_shape_at_center("circle"))
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

        self.pane_apply = ttk.Button(pane, text="Apply", command=self.commit_pane)
        self.pane_apply.grid(row=6, column=0, sticky=tk.E)

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
        self.root.bind("<Control-g>", lambda _event: self.toggle_grid(flip=True))
        self.root.bind("<Control-Up>", lambda _event: self.set_depth("up"))
        self.root.bind("<Control-Down>", lambda _event: self.set_depth("down"))
        self.root.bind("<Control-Shift-Up>", lambda _event: self.set_depth("top"))
        self.root.bind("<Control-Shift-Down>", lambda _event: self.set_depth("bottom"))
        self.root.bind("<Control-o>", lambda _event: self.load_state_dialog())
        self.root.bind("<Control-s>", lambda _event: self.save_state_dialog())
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
        self.autosave()
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
        self.autosave()
        self.set_status(f"Grid: {self.grid_line.get().lower()} lines in {self.grid_color.get()}.")

    def toggle_grid(self, flip: bool = False) -> None:
        """Show or hide the grid. Alignment is unaffected; only the lines come and go."""
        if flip:
            self.show_grid.set(not self.show_grid.get())
        self.draw_grid()
        self.autosave()
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

        self.select(item)  # the popup always acts on the shape it was opened over
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
        item = self.selected
        record = self.shapes.get(item) if item is not None else None
        if item is None or record is None:
            self.set_status("Right-click a shape to change its fill.")
            return
        self.canvas.itemconfigure(item, fill=color)
        record.fill = color
        self.autosave()
        self.set_status(f"{record.kind.capitalize()} filled with {color}.")

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
            self.pane_subject.configure(text=f"{record.kind.capitalize()} · depth {record.depth} · {record.uuid}")
            self.pane_name.insert(0, record.name)
            self.pane_description.insert("1.0", record.description)

        self.pane_name.configure(state=state)
        self.pane_description.configure(state=state)
        self.pane_apply.configure(state=state)

    def commit_pane(self) -> None:
        """Write whatever is in the pane back to the shape it belongs to."""
        record = self.shapes.get(self.pane_item) if self.pane_item is not None else None
        if record is None:
            return

        name = self.pane_name.get().strip()
        description = self.pane_description.get("1.0", tk.END).strip()
        if (name, description) == (record.name, record.description):
            return  # nothing typed, so nothing to save

        record.name, record.description = name, description
        self.autosave()
        self.set_status(f"Saved properties for {name or record.uuid[:8]}.")

    def open_properties(self) -> None:
        """Edit the name and description of the shape the popup was opened on."""
        record = self.shapes.get(self.selected) if self.selected is not None else None
        if record is None:
            self.set_status("Right-click a shape to edit its properties.")
            return

        dialog = PropertiesDialog(self.root, record)
        dialog.grab_set()  # modal: the canvas is off limits until it closes
        self.root.wait_window(dialog)

        if dialog.result is None:
            self.set_status(f"Properties of {record.kind} left unchanged.")
            return

        record.name, record.description = dialog.result
        self.show_in_pane(self.selected)
        self.autosave()
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
        self.autosave()
        self.set_status(f"Added {kind}. Drag it to move, drag a handle to resize.")
        return item

    def _draw(self, record: ShapeRecord, box: tuple[float, float, float, float]) -> int:
        """Put one shape on the canvas and remember it. Shared by drawing and loading."""
        create = self.canvas.create_rectangle if record.kind == "rectangle" else self.canvas.create_oval
        item = create(*box, fill=record.fill, outline=SHAPE_OUTLINE, width=1, tags=(TAG_SHAPE,))
        self.shapes[item] = record
        return item

    # -------------------------------------------------------------- selection

    def select(self, item: int | None) -> None:
        if item != self.pane_item:
            self.commit_pane()  # don't lose text typed for the shape we are leaving
        self.selected = item
        self._redraw_handles()
        self.show_in_pane(item)
        if item is None:
            self.set_status("Nothing selected.")
        else:
            self._report_size(item)

    def _redraw_handles(self) -> None:
        self.canvas.delete(TAG_HANDLE)
        if self.selected is None:
            return

        x1, y1, x2, y2 = self.canvas.coords(self.selected)
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
        self.autosave()
        depth = self.shapes[item].depth
        self.set_status(f"{self._kind(item).capitalize()} moved to depth {depth} of {len(order) - 1}.")

    def on_press(self, event: tk.Event) -> None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        handle = self._handle_under(self._current_item())

        if handle is not None and self.selected is not None:
            self.drag_mode = "resize"
            self.drag_handle = handle
            self.drag_start = (x, y)
            bx1, by1, bx2, by2 = self.canvas.coords(self.selected)
            self.drag_bbox = (bx1, by1, bx2, by2)
            return

        item = self._topmost_shape_at(x, y)
        if item is None:
            self.drag_mode = None
            self.select(None)
            return

        self.select(item)
        self.drag_mode = "move"
        self.drag_handle = None
        self.drag_start = (x, y)
        mx1, my1, mx2, my2 = self.canvas.coords(item)
        self.drag_bbox = (mx1, my1, mx2, my2)  # move is measured from here, so snapping cannot drift

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_mode is None or self.selected is None:
            return

        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.drag_mode == "move":
            x1, y1, x2, y2 = self.drag_bbox
            left = self.snap(x1 + (x - self.drag_start[0]))
            top = self.snap(y1 + (y - self.drag_start[1]))
            self.canvas.coords(self.selected, left, top, left + (x2 - x1), top + (y2 - y1))
            self._redraw_handles()
            self._report_position(self.selected)
        else:
            state = event.state if isinstance(event.state, int) else 0
            keep_ratio = bool(state & 0x0001)  # Shift held
            self.canvas.coords(self.selected, *self._resized_box(x, y, keep_ratio))
            self._redraw_handles()
            self._report_size(self.selected)

    def on_release(self, _event: tk.Event) -> None:
        if self.drag_mode is not None and self.selected is not None:
            self._report_size(self.selected)
            self.autosave()  # geometry is only written once the drag settles
        self.drag_mode = None
        self.drag_handle = None

    def _resized_box(self, x: float, y: float, keep_ratio: bool) -> tuple[float, float, float, float]:
        """Return the new bounding box for the current resize drag, snapped to the grid."""
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

    def delete_selected(self) -> None:
        if self.selected is None:
            self.set_status("Select a shape first, then delete it.")
            return
        record = self.shapes.pop(self.selected, None)
        self.pane_item = None  # its record is gone; nothing to commit back to
        self.canvas.delete(self.selected)
        self.canvas.delete(TAG_HANDLE)
        self.selected = None
        self.show_in_pane(None)
        self._renumber(self._by_depth())  # close the gap the deleted layer left
        self.autosave()
        self.set_status(f"Deleted {record.kind if record else 'shape'}.")

    def clear_canvas(self) -> None:
        self.canvas.delete("all")
        self.shapes.clear()
        self.selected = None
        self.pane_item = None
        self.show_in_pane(None)
        self.draw_grid()  # "all" took the grid with it
        self.autosave()
        self.set_status("Canvas cleared. Right-click to add a shape.")

    def show_help(self) -> None:
        self.set_status(
            "Right-click empty canvas to add a shape, right-click a shape to recolor it. "
            "Drag to move, drag a handle to resize; everything snaps to the 1 cm grid (Ctrl+G hides it). "
            f"The Json tab mirrors {STATE_FILE}."
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

    def autosave(self) -> None:
        """Called after every change that alters the state."""
        if self.suspend_autosave:
            return
        self.refresh_json_view()
        self.write_state(STATE_FILE)

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
                    ),
                    box,
                )
            self._renumber(self._by_depth())
            self.restack()
        finally:
            self.suspend_autosave = False
        self.autosave()

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
