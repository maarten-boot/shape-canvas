# DoodleMyShapes — specification

A shape editor with a canvas, a persistent JSON state document, and export to picture formats.

This document is written to be implemented deterministically: constants, formulas and structures are
given explicitly. Where a value is stated, use that value. Where behaviour is stated as a rule,
implement the rule rather than an approximation of it.

---

## 1. Identity

| What | Value | Notes |
|---|---|---|
| Program identifier | `DoodleMyShapes` | Fixed string. Written to every state file; the sole test of whether a file is ours. |
| Program version | `v1` | Written to every state file. |
| State directory | `~/.doodlemyshapes` | Fixed default. Overridable for one session with `--state-dir` (§9); nothing else changes it. |
| State file | `<state directory>/state.json` | |

Nothing here depends on the program's filename or on how it was started. Renaming the program file,
packaging it as an executable, or launching it through a wrapper changes neither where state is
stored nor whether a state file is recognised.

The identifier is never overridable: a state file written with `--state-dir` is an ordinary
`DoodleMyShapes` file and loads anywhere.

The directory is created on first write if it does not exist.

---

## 2. Coordinate system and units

**The model stores geometry in model units, at a fixed scale.**

```
MODEL_PX_PER_CM = 37.795275590551181      # 96 dpi reference, same as CSS
```

- All positions, sizes and grid pitches in the model and in the state file are model units.
- 1 cm is always `MODEL_PX_PER_CM` model units, on every machine and every display.
- Model units are the export unit too: the same drawing exports to the same pixel dimensions
  everywhere.

**The display's real DPI, and the viewport, are used only to render.**

```
display_scale = actual_pixels_per_cm / MODEL_PX_PER_CM     # 1.0 when DPI is unknown
screen_x      = (model_x - pan_x) * display_scale
model_x       = screen_x / display_scale + pan_x
```

Distances and points transform differently: a length is scaled but not offset. Keep the two
conversions separate (`length_to_screen` and `point_to_screen`) so a wrap width or a handle radius
cannot accidentally pick up the pan.

Model coordinates may be negative. Nothing depends on an origin at zero: bounding boxes use
min/max, snapping rounds, and export normalises to the content box.

- Convert pointer coordinates to model units on the way in; convert to screen units on the way out.
- All arithmetic — snapping, hit testing, bounding boxes, minimum sizes — happens in model units.
- `display_scale` is never persisted and never affects a saved or exported file.

The consequence is deliberate: a shape declared 2 cm wide is 2 cm wide in the file and in the export
on any machine, and appears approximately 2 cm on a display whose reported DPI is accurate.

---

## 3. Constants

### 3.1 Geometry

| Name | Value |
|---|---|
| `RECT_W_CM`, `RECT_H_CM` | 2.0, 1.0 |
| `CIRCLE_D_CM` | 2.0 |
| `MIN_SIZE` | 12 model units (absolute floor) |
| minimum shape width/height | `max(MIN_SIZE, grid cell)` |
| `HANDLE_RADIUS` | 4 model units (half a handle's side) |

### 3.2 Colours

| Name | Value |
|---|---|
| Canvas background | `#f4f4f4` |
| Default shape fill | `#d3d3d3` |
| Shape outline | `#5a5a5a`, width 1 |
| Grid | `#cfe0f5` |
| Handle fill / outline | `#ffffff` / `#1f6feb` |
| Selection marquee | `#1f6feb`, dash `(3, 2)` |

Fill palette, in menu order: Light gray `#d3d3d3`, White `#ffffff`, Slate `#8b98a5`, Red `#e2726e`,
Amber `#e8b04b`, Green `#7bb661`, Blue `#6c9bd2`, Violet `#a184c8`, then a separator and
**Custom color…** opening a colour picker.

### 3.3 Grid line types

| Label | Dash pattern |
|---|---|
| Solid | none |
| Stippled | `(1, 3)` |
| Dotted | `(2, 4)` |
| Dashed | `(6, 4)` |
| Dash-dot | `(8, 3, 2, 3)` |

Default: **Stippled**.

### 3.4 Other

| Name | Value |
|---|---|
| Grid pitch presets | 0.5, 1.0, 2.0, 5.0 cm; default 1.0 |
| Grid pitch bounds | 0.1 – 20.0 cm, clamped |
| Undo history depth | 50 |
| Export margin | 1.0 cm |
| Export label font size | 12 model units |
| Default window size | 1060 × 680, minimum 720 × 420 |

---

## 4. Application shell

- A **menu bar** at the top.
- A **central frame** holding a tabbed view with exactly two tabs, `Canvas` and `Json`.
- A **status line** at the bottom, showing the result of the last action.

The `Canvas` tab holds the drawing surface on the left and the properties pane on the right; the
canvas expands with the window, the pane keeps a fixed width of about 250 units.

The `Json` tab holds a non-editable, scrollable text view of the current state document, formatted
with `indent = 2`. It updates whenever the state changes, not only when the tab is shown, and always
matches what is written to disk.

---

## 5. Menus

```
File
  Open state...              Ctrl+O
  Save state as...           Ctrl+S
  ---
  Export as picture...       Ctrl+E
  ---
  Clear canvas               Ctrl+N
  ---
  Quit                       Ctrl+Q
Edit
  Undo                       Ctrl+Z
  ---
  [x] Confirm before deleting
  [ ] Allow coloring a whole group
Shape
  Add rectangle
  Add circle
  ---
  Group selection            Ctrl+Shift+G
  Ungroup                    Ctrl+Shift+U
  ---
  Delete selected            Del
View
  Grid
    [x] Show grid            Ctrl+G
    ---
    Grid color...
    Line type >              Solid | Stippled | Dotted | Dashed | Dash-dot   (radio)
    Grid size >              0.5 cm | 1 cm | 2 cm | 5 cm | --- | Custom size...  (radio + command)
  ---
  Center drawing            Ctrl+0
  ---
  Canvas tab
  Json tab
Help
  How it works
```

Additional keys: `Escape` deselects, `Backspace` deletes, `Ctrl+Up` / `Ctrl+Down` move the selected
shape one layer, `Ctrl+Shift+Up` / `Ctrl+Shift+Down` send it to top / bottom.

**Canvas popup** (right click on empty canvas): `Rectangle`, `Circle`, separator,
`Center drawing`.

Popups are on the **right button only**. The middle button pans (§10a).

**Shape popup** (right click on a shape): `Color >` (the palette) and `Depth >` (Bring to top, Move
up one, Move down one, Send to bottom) always; then, only when they would do something:

| Entry | Shown when |
|---|---|
| `Group selection` | two or more shapes are selected and they are not already one single group |
| `Ungroup` | at least one selected shape belongs to a group |

A lone ungrouped shape therefore offers `Color` and `Depth` and nothing else — no separator, no
inapplicable entries. The menu is rebuilt from the current selection each time it is posted.

There is no Properties entry: names and descriptions are edited in the pane (§18).

Releasing the right button without highlighting an entry dismisses the popup.

---

## 6. Data model

The model owns the geometry. The canvas is a view rendered from the model and is never read back as
a source of truth.

**Shape record**

| Field | Type | Notes |
|---|---|---|
| `uuid` | string | UUID4, assigned at creation, stable for the shape's life |
| `kind` | `"rectangle"` \| `"circle"` | |
| `x`, `y` | number | top-left corner, model units |
| `width`, `height` | number | model units |
| `fill` | string | hex colour |
| `name` | string | may be empty |
| `description` | string | may be empty |
| `depth` | integer | 0 is bottom; higher is nearer the front |
| `group` | string | group uuid, empty when ungrouped |
| `show_name` | bool | whether the name is drawn on the canvas; default true |

**Group record**

| Field | Type | Notes |
|---|---|---|
| `uuid` | string | UUID4, assigned when the group is created |
| `name` | string | may be empty |
| `description` | string | may be empty |
| `show_name` | bool | whether the group name is drawn; default **false** |

Members are not stored on the group at runtime; they are the shapes whose `group` matches. The
document writes them out explicitly so the membership is readable and authoritative on load.

**Invariants**

- Depths are contiguous `0..n-1` with no gaps or ties; renumber after every change that adds,
  removes or reorders shapes.
- Drawing order is derived from `depth`. The view is re-stacked whenever depths change, so display
  order and model order can never disagree.
- `width` and `height` are at least `min_extent` (§12) — including every member of a group after
  the group has been resized.
- Every uuid in a group's `members` list refers to an existing shape.
- Every group record has at least one member; empty groups are pruned.
- Every non-empty `group` field on a shape names an existing group record.

Interactions compute new field values on the model and then redraw. Serialisation reads the model.
The model must be fully usable with no display attached: creating shapes, snapping, grouping, depth
ordering, undo and serialisation all work headlessly.

---

## 7. State document

```json
{
  "program": "DoodleMyShapes",
  "version": "v1",
  "canvas": {
    "background": "#f4f4f4",
    "grid_cm": 1.0,
    "grid_visible": true,
    "grid_color": "#cfe0f5",
    "grid_line": "Stippled"
  },
  "settings": { "confirm_deletes": true, "color_groups": false },
  "groups": [
    {
      "uuid": "…",
      "name": "Bracket assembly",
      "description": "Two parts, welded.",
      "show_name": false,
      "members": ["shape-uuid", "shape-uuid"]
    }
  ],
  "shapes": [
    {
      "uuid": "…",
      "kind": "rectangle",
      "name": "Plate",
      "description": "Load bearing.",
      "fill": "#d3d3d3",
      "depth": 0,
      "group": null,
      "show_name": true,
      "position": { "x": 264.57, "y": 188.98 },
      "size": { "width": 75.59, "height": 37.80 }
    }
  ]
}
```

- Position and size are separate objects, not two corner points.
- Numbers are model units, rounded to two decimals on write. Position and size are rounded
  independently, so a reloaded shape's far edge can differ from the original by up to 0.01 model
  units (about 0.003 mm). Repeated save/load cycles do not accumulate.
- `groups` is the authoritative membership record on load; each shape's `group` field mirrors it.
- Shapes are written in depth order.

---

## 8. Persistence

**Autosave.** Write the state file after every change: adding, deleting, moving, resizing,
recolouring, depth, grouping, properties, grid settings, the delete-confirmation preference, and
loading a file. During a drag, write once on release rather than on every motion event. Write on
exit, including via the window manager's close button.

Writes are atomic: write a temporary file, then rename over the target.

**Save as / Open** write and read a file of the user's choosing.

**Validation.** A file is loaded only if all of the following hold. Otherwise show a warning naming
the specific reason and leave the canvas exactly as it was.

1. It parses as JSON and the root is an object.
2. `program` is present and equals `DoodleMyShapes`.
3. `version` equals `v1`.
4. `shapes` is a list.

Individual malformed shape entries inside an otherwise valid file are skipped, not fatal. Values out
of range (grid pitch, sizes) are clamped. Unknown line-type or colour values fall back to the
current setting.

**Startup.** Restore the autosaved state unless `--clean` or `--file` says otherwise.

---

## 9. Command-line arguments

```
doodlemyshapes [--clean] [--file PATH] [--state-dir PATH] [--version] [--help]
```

- `--clean` — start empty; the existing state file is left on disk until the first change.
- `--file PATH` — load this file instead of the autosave.
  - The name must end in `.json`, checked before opening. Otherwise warn and ignore.
  - A file passing the name check still goes through the validation in §8.
  - `--file` supersedes the autosave. If the file is rejected the canvas starts **empty**; it does
    not fall back to the autosave.
- `--file` wins over `--clean` when both are given.
- `--state-dir PATH` — use this directory instead of `~/.doodlemyshapes` for the whole session.
  - Autosave reads and writes `PATH/state.json`. Save-as and Open dialogs open there by default.
  - `~` is expanded and a relative path is resolved against the working directory at startup.
  - The directory is created, including parents, if it does not exist.
  - It applies to the autosave only. `--file` still names a file anywhere on disk, and exporting
    still defaults to the user's home.
  - It changes only *where* state lives. The program identifier, the file name `state.json`, and
    every validation rule are unchanged, so a state file written this way is an ordinary
    `DoodleMyShapes` file.

**Failing early.** A `--state-dir` that exists but is not a directory, or that cannot be created,
is a startup error: report it on stderr and exit non-zero **before** opening the window. This is
deliberately stricter than `--file`, which warns and carries on — a bad `--file` costs you one load,
whereas a bad state directory would silently discard every subsequent change.

---

## 10. Drawing

Right-clicking empty canvas and choosing a shape draws it centred on the click point at the fixed
size in §3.1, filled with the default fill, with a fresh uuid, at the top of the depth stack.

The shape's top-left corner is snapped to the grid, which puts every edge of a whole-centimetre
shape on a grid line.

---

## 10a. The viewport

The canvas shows a window onto the model, positioned by `pan_x`, `pan_y` in model units.

- **Hold space and drag with the left button** to move the view. The pointer keeps hold of the
  model point it grabbed, so the drag is 1:1 with the mouse.
- **Drag with the middle button** to do the same with no key involved.
- **Center drawing** — on the View menu at `Ctrl+0` and on the canvas popup — puts the middle of
  the drawing's bounding box in the middle of the viewport. With nothing drawn it returns to the
  origin.

**Holding space must be read correctly.** X11 auto-repeat delivers a *release* followed by a press
for as long as a key is held, so a handler that trusts the first `KeyRelease` disarms the pan a
fraction of a second after it starts. Treat a release as real only if no repeat press arrives
within a short grace period (about 60 ms), and cancel the pending release when one does.

Two further rules make the gesture forgiving:

- A pan already under way continues until the mouse button comes up, whatever the key does
  meanwhile.
- Space arms panning wherever the keyboard focus happens to be, *except* inside a text field, where
  it types a space as normal.

Panning and centring move the window, never the shapes. They therefore:

- do not change the model, the state file or the Json tab;
- record no undo step, so a pan can never displace a real edit from the 50-step history;
- have no effect on an export.

The viewport is not persisted at all. Two people opening the same file need not agree on where they
were looking, and reopening a drawing does not restore a stale scroll position.

The grid is drawn across the visible model rectangle rather than from the canvas origin, and is
skipped entirely rather than drawn when it would exceed `GRID_LINE_LIMIT` lines.

Zoom is not implemented yet; the transform above is written so it can be added as one more term.

---

## 11. Selection

- Clicking a shape selects it; clicking empty canvas clears the selection.
- **Shift + click** adds a shape to the selection; shift-clicking a selected shape removes it.

### Rubber band

**Dragging from empty canvas** draws a selection band and selects everything it encloses **or
merely touches**, tested against bounding boxes. A band always starts on empty canvas; pressing on
a shape moves that shape instead.

- The band is drawn as a stippled, dashed rectangle while the button is held, and disappears on
  release.
- Catching any member of a group catches the whole group, so a banded selection is
  indistinguishable from one built with shift-click.
- Plain drag replaces the selection; **shift + drag** adds to it.
- A band that catches exactly one shape behaves as an ordinary single selection: no member
  marquees, and the pane edits that shape.
- A band that catches nothing clears the selection.
- A press and release that travels less than a few pixels is a click, not a band, and simply
  deselects.
- Banding selects; it changes no shape, writes nothing and records no undo step.
- Selecting any member of a group selects the entire group.
- The selection has a **primary** member — the most recently clicked — which is what the properties
  pane and the Properties window edit.
- Right-clicking a shape already in the selection keeps the selection and makes that shape primary.
  Right-clicking a shape outside it replaces the selection.

**Visuals.** Eight resize handles are drawn on the bounding box of the whole selection. When more
than one shape is selected, each member also gets a dashed marquee.

Handles are drawn above all shapes; the grid is drawn below all shapes; a shape's name label sits
directly above its own shape.

---

## 12. Move and resize

Both operate on the entire selection, so a group moves and resizes as one piece.

**On press**, record each selected shape's box and the selection's bounding box.

**Move.** Snap the *selection's* top-left, then apply one shared delta to every member:

```
left = snap(start_box.x + (pointer_x - press_x))
top  = snap(start_box.y + (pointer_y - press_y))
dx, dy = left - start_box.x, top - start_box.y
```

Measuring from the press position rather than accumulating per-event deltas is required: with
accumulation, sub-cell movements each round to zero and the selection never moves.

**Resize.** Compute the new bounding box from the dragged handle, snapping the dragged edges, then
scale each member into it:

```
snap(v)   = round(v / cell) * cell,  cell = grid_cm * MODEL_PX_PER_CM
scale_x   = (new.width) / (old.width)      # 1.0 if old.width == 0
new_shape.x = new.x + (old_shape.x - old.x) * scale_x
```

**Clamping.** The floor for a frame dimension is a whole number of cells, so clamping never lands
off the grid:

```
min_extent   = ceil(MIN_SIZE / cell) * cell
least_width  = ceil(max(min_extent, frame.width * min_extent / smallest_member.width) / cell) * cell
```

The second term matters for groups: members scale proportionally, so a frame clamped to one
`min_extent` would crush a small member far below it. Taking the frame size at which the *smallest*
member reaches `min_extent` keeps the §6 invariant true for every shape. For a lone shape the two
terms coincide.

Clamping pushes back the edge being dragged; the anchored edge never moves. A selection that starts
grid-aligned therefore stays grid-aligned, and one whose members are already off-grid — as they will
be after any non-integral scale — keeps its origin where it was.

Holding **Shift** on a corner handle preserves the frame's aspect ratio.

**On release**, record one undo step and write the state file.

---

## 13. Grouping

- Grouping requires at least two selected shapes. It assigns a fresh group uuid to each.
- Ungrouping clears the group from every selected shape that has one.
- Group membership drives selection (§11), which in turn drives move and resize (§12) — there is no
  separate group-transform code path.
- Groups are saved and restored.

**A group is a record, not just a label.** It has a uuid, a `name` and a `description` of its own,
independent of its members' names. Membership lives on the shapes; the group record holds the
properties.

A group with no members left — after ungrouping, or after its last member is deleted — is removed
from the model and from the document. Orphan group records never persist.

A group's name is *not* drawn on the canvas; only shape names are (§16).

---

## 14. Depth

Four operations on the primary selected shape: **top**, **up one**, **down one**, **bottom**.
Reorder the shape within the depth-sorted list, then renumber `0..n-1` and re-stack the view. When
the shape is already at the requested end, report that and change nothing.

---

## 15. Fill colour

The `Color` submenu applies to every selected shape, with one exception: **colouring a whole group
is opt-in and off by default.**

While it is off, a shape that belongs to a group is recoloured on its own — the one the popup was
opened over, which is the primary member — and its group mates keep their fills. The status line
says how many were left alone.

While it is on, every member of the group is recoloured together.

The setting applies to groups only. Shapes that merely happen to be shift-selected together are not
a group, and are always recoloured as a set.

`Edit > Allow coloring a whole group` toggles it; the preference is part of the saved state.

Changing a fill also recomputes the label ink colour (§16).

---

## 16. Labels

A shape with a non-empty `name` and `show_name` true displays that name as text centred on it.

A **group** with a non-empty `name` and `show_name` true displays its name centred on the bounding
box of its members, in the same style. It follows the group as members move or resize, sits above
every member, and disappears when the group is ungrouped or its name is hidden. Its ink colour is
chosen against the topmost member's fill.

- The text follows the shape while it is moved or resized.
- It wraps to the shape's width minus 6 model units.
- Ink colour is chosen for contrast:
  `luminance = (0.299·R + 0.587·G + 0.114·B) / 255`; use `#101010` when `luminance > 0.55`,
  otherwise `#ffffff`.
- Clearing the name removes the text.
- Clicking on the text selects the shape beneath it; labels are never click targets.
- Only names that are set to show are drawn — on the canvas and in an export alike.

---

## 17. Properties pane

To the right of the canvas. Shows and edits `Name` and `Description`, plus a read-only line
identifying what is being edited.

**What the pane edits depends on the selection:**

| Selection | Pane edits | Read-only line |
|---|---|---|
| One ungrouped shape | that shape | kind, depth, uuid |
| Several ungrouped shapes | the primary shape | kind, depth, uuid, "n selected, editing this one" |
| Any member of a group | **the group** | "Group of n shapes · editing the group", group uuid |
| Nothing | nothing; fields disabled | "No shape selected" |

### Showing the name on the canvas

Beside the fields is a two-state **Show / Hide** button controlling whether the name is drawn on the
canvas. The button reads the current state: it says `Show` while the name is visible and `Hide`
while it is not, and pressing it flips both the state and the label.

- A **shape** defaults to `Show` — a name given to a shape appears on it straight away.
- A **group** defaults to `Hide` — a group name is for organising, and only appears once asked for.
- The button is disabled until there is a name to show.
- The setting is per shape and per group, and is part of the saved state (`show_name`).

Since selecting any member selects the whole group (§11), clicking a grouped shape shows the
group's properties. To edit an individual member's own name and description, ungroup it, edit the member, and group it again.

- With nothing selected, the fields and the Apply button are **not editable**.
- Edits commit on Apply, on Return in the Name field, and when focus leaves a field.
- Changing the selection first commits pending edits **to the shape being left**, not the one
  arriving. Track which shape the pane is showing separately from the current selection.
- Escape reverts a field to the stored value.
- An edit that changes nothing writes nothing and records no undo step.
- The pane and the Properties window edit the same data and always agree.
- The Undo button lives here and is enabled whenever the history is non-empty, including when
  nothing is selected.

---

## 18. Grid

A grid of `grid_cm` × `grid_cm`, default 1 cm, drawn from the canvas origin. Shapes always align to
it; hiding the grid changes only whether the lines are drawn.

Menu controls: show/hide, colour, line type, pitch. All four are part of the saved state. Changing
the pitch does not move shapes already placed. Changing colour or line type while the grid is hidden
shows it again.

Redraw the grid when the canvas is resized and after any operation that clears the canvas.

---

## 19. Undo

A history of at most 50 steps, undone with the Undo button or `Ctrl+Z`.

The reliable implementation is snapshots, not inverse operations: serialisation and loading already
round-trip the whole document, so record the state *before* each change and restore it on undo. This
makes undo cover every kind of change for free.

- Undo is not itself recorded.
- A change that changes nothing records nothing — including a rejected file load and a no-op
  property edit.
- The status line names what was undone ("Undid depth change of circle. 4 steps left.").
- The history is per-session and is not saved.

---

## 20. Deleting

Deleting acts on the entire selection.

**Confirmation is on by default.** The prompt names what is going: `the rectangle "Plate"`,
`these 3 shapes`, `all 7 shapes`. The default button is *No*. Declining changes nothing, writes
nothing and records no undo step. Clearing an already-empty canvas does not prompt.

`Edit > Confirm before deleting` turns confirmation off; the preference is part of the saved state.

---

## 21. Export

`File > Export as picture...` writes `png`, `jpg`/`jpeg` or `svg`.

**Content.** The bounding box of all shapes plus a 1 cm margin on each side. Rendered from the
model, in depth order, with labels. Because it is rendered from the model and in model units:

- the grid, handles and marquees never appear;
- the output dimensions depend only on the drawing, not on the display it was drawn on.

SVG is generated directly, with no third-party dependency, and its text content is XML-escaped.
Raster output needs an imaging library; if it is missing, say so and name the install, and keep SVG
export working.

**Extension handling.** The dialog offers the filename *without* an extension and appends the one
belonging to the chosen file type, so the two cannot disagree.

| Typed | File type | Result |
|---|---|---|
| `diagram` | PNG | `diagram.png` |
| `diagram` | SVG | `diagram.svg` |
| `diagram.svg` | PNG | `diagram.svg` — a typed extension wins |
| `report.v2` | SVG | `report.v2.svg` — an unrecognised dot is part of the name |
| `diagram.bmp` | PNG | refused with a warning, not renamed |

---

## 22. Acceptance checks

An implementation is correct if all of these hold, verifiable without a human at the screen.

1. A new rectangle measures exactly `2 × MODEL_PX_PER_CM` by `1 × MODEL_PX_PER_CM` model units, and
   a new circle `2 × MODEL_PX_PER_CM` square, regardless of display DPI.
2. Every corner of a newly placed shape lies on a grid multiple.
3. The state file, the Json tab and the model agree at all times.
4. Save → load → save produces byte-identical files.
5. A file with a different `program`, a bad `version`, unparseable JSON, or a missing path is
   rejected with a warning, and the canvas and the state file are unchanged.
6. `--file notes.txt` warns and leaves the canvas empty.
7. Dragging any member of a group moves every member by an identical delta.
8. Resizing a group scales all members and preserves their relative layout.
9. Undoing each of add, delete, move, resize, recolour, depth, group, ungroup and property edit
   returns the document to its exact previous serialisation.
10. Screen order of shapes equals depth order after every operation, including after a load.
11. Exporting the same document twice produces identical bytes, and exporting with a selection
    active produces the same bytes as with none.
12. The same document exported on two machines with different display DPI produces identical
    dimensions.
13. Running the program under a different filename reads and writes the same
    `~/.doodlemyshapes/state.json` and accepts the same files.
14. With `--state-dir`, every write lands in `PATH/state.json` and the default location is left
    untouched; a file written there loads without it.
15. A `--state-dir` pointing at an existing plain file exits non-zero with a message and opens no
    window.
16. Declining a delete confirmation leaves the document, the state file and the undo history
    untouched, and `Edit > Confirm before deleting` survives a restart.
17. Typing a name for one shape and then selecting another commits the text to the shape being
    left, not the one arriving, and the pane is not editable when nothing is selected.
18. Releasing the right mouse button while no menu entry is highlighted unposts the popup and
    invokes nothing.
19. Resizing down to nothing clamps the frame to a whole number of cells and leaves the anchored
    corner where it was; a grid-aligned selection stays grid-aligned, and no member of a group is
    scaled below `min_extent`.
20. Changing the grid pitch redraws the grid and leaves the position and size of every existing
    shape unchanged.
21. A name on a dark fill renders in `#ffffff` and on a light fill in `#101010`; clearing the name
    removes the text entirely, leaving no orphaned label.
22. The five rows of the extension table in §21 resolve exactly as written, and the refused row
    writes no file.
23. Selecting any member of a group shows the group in the pane; edits save to the group and leave
    member names untouched, survive save/load, and are undoable. An ungrouped shape still shows
    itself.
24. With group colouring off, recolouring a grouped shape changes only that shape; with it on, every
    member changes. An ad-hoc multiple selection is always recoloured together. The preference
    survives a restart.
25. Ungrouping, or deleting every member, removes the group record from the model and from the
    document.
26. The shape popup offers no Properties entry.
27. A named shape shows its name by default; the toggle hides and restores it, the button text
    tracks the state, and the setting survives save/load and undo.
28. A named group hides its name by default; when shown it is centred on the group's bounding box,
    follows the group as it moves, sits above every member, and appears in exports only while
    shown.
29. Space + drag moves every shape on screen by the same pixel delta while leaving the model, the
    state file and the undo history untouched; labels and handles follow, and hit testing still
    resolves to the right shape. A simulated auto-repeat burst (release/press pairs) mid-drag does
    not interrupt it, releasing space mid-drag lets the pan finish, and space inside a text field
    does not arm it.
30. A shape dragged to negative coordinates round-trips through save and load, the grid still
    covers the view when the origin is off screen, and two exports taken at different pan offsets
    are byte-identical.
31. Center drawing puts the middle of the drawing at the middle of the viewport, returns to the
    origin when nothing is drawn, and appears on both the View menu and the canvas popup. Panning
    by either gesture, and centring, leave the document, the state file, the Json tab and the undo
    history byte-for-byte unchanged. The right button posts the menu; the middle button pans and
    posts nothing.
32. A band selects what it encloses and what it merely clips, pulls in whole groups, extends the
    selection with shift, clears it when it catches nothing, moves no shape, and leaves the
    document and history untouched. A single caught shape shows no marquees. A press that does not
    travel is a click, not a band.
33. The shape popup offers `Color` and `Depth` alone for a lone ungrouped shape, adds
    `Group selection` for two or more loose shapes, adds `Ungroup` for a group, and offers both for
    a group plus a loose shape.
