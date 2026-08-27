#!/usr/bin/env python3
"""Runs the 22 acceptance checks from the specification against doodlemyshapes.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME

import tkinter as tk

import doodlemyshapes as dms
from doodlemyshapes import MODEL_PX_PER_CM, DoodleMyShapes, ShapeRecord

WARNINGS: list[str] = []
ANSWER = [True]
dms.messagebox.showwarning = lambda title, message, **kw: WARNINGS.append(title)
dms.messagebox.askyesno = lambda *a, **k: ANSWER[0]

PASS, FAIL = [], []


def check(number: int, description: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(number)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {number:2d}. {description}" + (f"  <- {detail}" if detail and not ok else ""))


def app(**kwargs) -> tuple[tk.Tk, DoodleMyShapes]:
    root = tk.Tk()
    instance = DoodleMyShapes(root, state_dir=Path(kwargs.pop("state_dir", HOME + "/.doodlemyshapes")), **kwargs)
    root.update()
    return root, instance


def press(x, y, shift=False):
    event = tk.Event()
    event.x, event.y, event.state = int(x), int(y), (0x0001 if shift else 0)
    return event


# --------------------------------------------------------------------- 1, 2
root, a = app()
rect = a.add_shape("rectangle", 300, 200)
circ = a.add_shape("circle", 700, 400)
r, c = a.shapes[rect], a.shapes[circ]
check(
    1,
    "fixed size in model units regardless of DPI",
    (r.width, r.height) == (2 * MODEL_PX_PER_CM, 1 * MODEL_PX_PER_CM)
    and (c.width, c.height) == (2 * MODEL_PX_PER_CM, 2 * MODEL_PX_PER_CM),
    f"{r.width}x{r.height}, scale={a.display_scale}",
)
on_grid = lambda v: abs(v / a.cell - round(v / a.cell)) < 1e-9
check(2, "new shapes land on grid multiples", all(on_grid(v) for v in r.box + c.box))

# ------------------------------------------------------------------------ 3
disk = json.loads((Path(HOME) / ".doodlemyshapes/state.json").read_text())
tab = json.loads(a.json_text.get("1.0", "end-1c"))
check(3, "state file, Json tab and model agree", disk == tab == a.to_state())

# ------------------------------------------------------------------------ 4
first = Path(HOME) / "rt1.json"
second = Path(HOME) / "rt2.json"
a.write_state(first)
a.load_state(first, announce=False)
a.write_state(second)
check(4, "save -> load -> save is byte-identical", first.read_bytes() == second.read_bytes())

# ------------------------------------------------------------------------ 5
before_doc, before_file = a.to_state(), (Path(HOME) / ".doodlemyshapes/state.json").read_bytes()
bad_cases = {
    "foreign": '{"program":"OtherApp","version":"v1","shapes":[]}',
    "version": '{"program":"DoodleMyShapes","version":"v9","shapes":[]}',
    "broken": "{not json",
}
results = []
for name, text in bad_cases.items():
    path = Path(HOME) / f"{name}.json"
    path.write_text(text)
    results.append(a.load_state(path, announce=True))
results.append(a.load_state(Path(HOME) / "missing.json", announce=True))
check(
    5,
    "bad files rejected, canvas and state file unchanged",
    not any(results)
    and a.to_state() == before_doc
    and (Path(HOME) / ".doodlemyshapes/state.json").read_bytes() == before_file
    and len(WARNINGS) == 4,
)

# ------------------------------------------------------------------------ 6
root.destroy()
notes = Path(HOME) / "notes.txt"
notes.write_text(json.dumps(before_doc))
root, b = app(startup_file=notes)
check(6, "--file notes.txt warns and leaves the canvas empty", len(b.shapes) == 0 and WARNINGS[-1] == "Not a JSON file")
root.destroy()

# --------------------------------------------------------------------- 7, 8
root, a = app(clean=True)
one = a.add_shape("rectangle", 200, 200)
two = a.add_shape("circle", 500, 300)
a.select(one)
a.select(two, add=True)
a.group_selection()

a.select(one)  # selecting a member selects the group
before = {k: a.shapes[k].box for k in (one, two)}
a.begin_drag("move", *a.shapes[one].center)
a.on_drag(press(*a.point_to_screen(a.shapes[one].center[0] + 150, a.shapes[one].center[1] + 90)))
a.on_release(tk.Event())
deltas = [tuple(round(n - o, 6) for n, o in zip(a.shapes[k].box, before[k])) for k in (one, two)]
check(7, "dragging a group moves every member by one delta", len(a.selection) == 2 and deltas[0] == deltas[1], str(deltas))

before = {k: a.shapes[k].box for k in (one, two)}
frame = a.selection_box()
a.begin_drag("resize", frame[2], frame[3])
a.drag_handle = "se"
a.on_drag(press(*a.point_to_screen(frame[2] + 200, frame[3] + 120)))
a.on_release(tk.Event())
grew = all(a.shapes[k].width > before[k][2] - before[k][0] for k in (one, two))
gap_before = before[two][0] - before[one][0]
gap_after = a.shapes[two].x - a.shapes[one].x
check(8, "resizing a group scales members and keeps relative layout", grew and gap_after > gap_before)

# ------------------------------------------------------------------------ 9
def snapshot():
    return json.dumps(a.to_state(), sort_keys=True)


steps = []
a.select(one)
before_add = snapshot()
extra = a.add_shape("rectangle", 800, 500)
steps.append(("add", before_add))

a.select(extra)
before_move = snapshot()
a.begin_drag("move", *a.shapes[extra].center)
a.on_drag(press(*a.point_to_screen(a.shapes[extra].center[0] + 100, a.shapes[extra].center[1])))
a.on_release(tk.Event())
steps.append(("move", before_move))

before_resize = snapshot()
box = a.shapes[extra].box
a.begin_drag("resize", box[2], box[3])
a.drag_handle = "se"
a.on_drag(press(*a.point_to_screen(box[2] + 80, box[3] + 80)))
a.on_release(tk.Event())
steps.append(("resize", before_resize))

before_fill = snapshot()
a.set_fill("#7bb661")
steps.append(("recolour", before_fill))

before_depth = snapshot()
a.set_depth("bottom")
steps.append(("depth", before_depth))

a.select(one)
a.select(extra, add=True)
before_group = snapshot()
a.group_selection()
steps.append(("group", before_group))

before_ungroup = snapshot()
a.ungroup_selection()
steps.append(("ungroup", before_ungroup))

a.select(extra)
before_props = snapshot()
a.pane_name.insert(0, "Named")
a.commit_pane()
steps.append(("properties", before_props))

before_delete = snapshot()
a.select(extra)
a.delete_selected()
steps.append(("delete", before_delete))

undone = []
for label, expected in reversed(steps):
    a.undo()
    undone.append((label, snapshot() == expected))
check(9, "undo restores the exact previous serialisation for every change", all(ok for _l, ok in undone), str(undone))

# ----------------------------------------------------------------------- 10
def screen_order_matches() -> bool:
    order = [a.owners[i] for i in a.canvas.find_withtag("shape") if i in a.owners]
    return order == a.by_depth()


ok10 = screen_order_matches()
a.select(one)
a.set_depth("top")
ok10 = ok10 and screen_order_matches()
a.write_state(Path(HOME) / "z.json")
a.load_state(Path(HOME) / "z.json", announce=False)
ok10 = ok10 and screen_order_matches()
check(10, "screen order equals depth order, including after a load", ok10)

# ----------------------------------------------------------------------- 11
a.select(None)
a.export_to(Path(HOME) / "e1.png")
a.select(a.by_depth()[-1])
a.export_to(Path(HOME) / "e2.png")
a.export_to(Path(HOME) / "e3.png")
check(
    11,
    "export is deterministic and ignores selection chrome",
    (Path(HOME) / "e1.png").read_bytes() == (Path(HOME) / "e2.png").read_bytes() == (Path(HOME) / "e3.png").read_bytes(),
)

# ----------------------------------------------------------------------- 12
box = a.content_box()
expected_px = (int(box[2] - box[0]), int(box[3] - box[1]))
from PIL import Image

actual = Image.open(Path(HOME) / "e1.png").size
scaled = a.display_scale
check(
    12,
    "export dimensions are display-independent",
    actual == expected_px and abs(scaled - 1.0) > 1e-9,
    f"{actual} vs {expected_px}, display_scale={scaled}",
)

# ----------------------------------------------------------------------- 16
ANSWER[0] = False
doc_before, hist_before = snapshot(), len(a.history)
file_before = (Path(HOME) / ".doodlemyshapes/state.json").read_bytes()
a.select(a.by_depth()[0])
a.delete_selected()
a.clear_canvas()
ok16 = (
    snapshot() == doc_before
    and len(a.history) == hist_before
    and (Path(HOME) / ".doodlemyshapes/state.json").read_bytes() == file_before
)
ANSWER[0] = True
a.confirm_deletes.set(False)
a.on_confirm_setting_changed()
root.destroy()
root, a = app()
ok16 = ok16 and a.confirm_deletes.get() is False
a.confirm_deletes.set(True)
a.on_confirm_setting_changed()
check(16, "declining a delete changes nothing; the preference survives a restart", ok16)

# ----------------------------------------------------------------------- 17
for identifier in list(a.shapes):  # this check is about shapes, so start ungrouped
    a.shapes[identifier].group = ""
a.prune_groups()
first_id = a.by_depth()[0]
second_id = a.by_depth()[-1]
a.select(first_id)
a.pane_name.delete(0, tk.END)
a.pane_name.insert(0, "Typed here")
a.select(second_id)  # switch without pressing Apply
ok17 = a.shapes[first_id].name == "Typed here" and a.shapes[second_id].name != "Typed here"
a.select(None)
ok17 = ok17 and str(a.pane_name.cget("state")) == "disabled" and str(a.pane_apply.cget("state")) == "disabled"
check(17, "pending edits commit to the shape being left; pane locked with no selection", ok17)

# ----------------------------------------------------------------------- 18
invoked = []
a.canvas_popup.entryconfigure(0, command=lambda: invoked.append("rectangle"))
event = tk.Event()
event.widget = a.canvas_popup
a.dismiss_popup(event)  # no entry active
check(18, "right-button release with nothing highlighted unposts and invokes nothing", not invoked)

# ----------------------------------------------------------------------- 19
ok19 = True
for identifier in list(a.shapes):
    a.shapes[identifier].group = ""
whole_cells = lambda v: abs(v / a.cell - round(v / a.cell)) < 1e-6

for pitch in (0.1, 0.25, 0.5, 1.0, 2.0, 5.0):
    a.grid_cm.set(pitch)

    # A lone shape, placed on the grid, is clamped and every edge stays on the grid.
    lone = a.add_shape("rectangle", 900, 600)
    a.select(lone)
    box = a.shapes[lone].box
    a.begin_drag("resize", box[2], box[3])
    a.drag_handle = "se"
    a.on_drag(press(*a.point_to_screen(box[0] - 900, box[1] - 900)))
    a.on_release(tk.Event())
    record = a.shapes[lone]
    if not (
        record.width >= a.min_extent - 1e-6
        and record.height >= a.min_extent - 1e-6
        and all(whole_cells(v) for v in record.box)
    ):
        ok19 = False
        print(f"        lone, pitch {pitch}: {record.width:.3f}x{record.height:.3f} min {a.min_extent:.3f}")

    # A group: the frame clamps to whole cells, the anchored corner does not move,
    # and no member is scaled below the minimum.
    partner = a.add_shape("circle", 1200, 700)
    a.select(lone)
    a.select(partner, add=True)
    a.group_selection()
    frame = a.selection_box()
    a.begin_drag("resize", frame[2], frame[3])
    a.drag_handle = "se"
    a.on_drag(press(*a.point_to_screen(frame[0] - 900, frame[1] - 900)))
    a.on_release(tk.Event())
    new_frame = a.selection_box()

    anchored = abs(new_frame[0] - frame[0]) < 1e-6 and abs(new_frame[1] - frame[1]) < 1e-6
    sized = whole_cells(new_frame[2] - new_frame[0]) and whole_cells(new_frame[3] - new_frame[1])
    aligned = all(whole_cells(v) for v in new_frame)  # true because the frame started aligned
    members_ok = all(
        a.shapes[m].width >= a.min_extent - 1e-6 and a.shapes[m].height >= a.min_extent - 1e-6
        for m in (lone, partner)
    )
    if not (anchored and sized and aligned and members_ok):
        ok19 = False
        sizes = ", ".join(f"{a.shapes[m].width:.2f}x{a.shapes[m].height:.2f}" for m in (lone, partner))
        print(
            f"        group, pitch {pitch}: anchored={anchored} sized={sized} aligned={aligned} "
            f"members={sizes} min {a.min_extent:.3f}"
        )
    a.ungroup_selection()
    for spare in (lone, partner):
        a.shapes.pop(spare, None)
        a.forget(spare)
    a.select(None)
    a.renumber()

a.grid_cm.set(1.0)
check(19, "clamps to whole cells, holds the anchored corner, crushes no group member", ok19)

# ----------------------------------------------------------------------- 20
geometry_before = {k: a.shapes[k].box for k in a.shapes}
a.grid_cm.set(2.0)
a.apply_grid_size()
lines_two = len(a.canvas.find_withtag("grid"))
a.grid_cm.set(0.5)
a.apply_grid_size()
lines_half = len(a.canvas.find_withtag("grid"))
check(
    20,
    "changing the pitch redraws the grid and moves nothing",
    {k: a.shapes[k].box for k in a.shapes} == geometry_before and lines_half > lines_two,
)

# ----------------------------------------------------------------------- 21
target = a.by_depth()[0]
a.select(target)
a.set_fill("#1b3a5c")
a.pane_name.delete(0, tk.END)
a.pane_name.insert(0, "Contrast")
a.commit_pane()
dark_ink = a.canvas.itemcget(a.labels[target], "fill")
a.set_fill("#ffffff")
light_ink = a.canvas.itemcget(a.labels[target], "fill")
a.pane_name.delete(0, tk.END)
a.commit_pane()
check(
    21,
    "label ink follows the fill; clearing the name leaves no orphan",
    dark_ink == "#ffffff"
    and light_ink == "#101010"
    and target not in a.labels
    and len(a.canvas.find_withtag("label")) == len(a.labels),
)

# ----------------------------------------------------------------------- 22
rows = [
    ("diagram", "PNG image", "diagram.png"),
    ("diagram", "SVG drawing", "diagram.svg"),
    ("diagram.svg", "PNG image", "diagram.svg"),
    ("report.v2", "SVG drawing", "report.v2.svg"),
    ("diagram.bmp", "PNG image", None),
]
ok22 = True
for name, label, expected in rows:
    got = a.export_path(Path(HOME) / name, label)
    if (got.name if got else None) != expected:
        ok22 = False
        print(f"        {name} + {label} -> {got}")
ok22 = ok22 and not (Path(HOME) / "diagram.bmp").exists()
check(22, "the extension table resolves as specified", ok22)

root.destroy()

# ------------------------------------------------------------------ 13, 14, 15
here = Path(__file__).resolve().parent
renamed = Path(HOME) / "totally_different_name.py"
renamed.write_text((here / "doodlemyshapes.py").read_text())
probe = """
import json, sys
from pathlib import Path
sys.argv[0] = str(Path(__file__))
import tkinter as tk
mod = __import__(Path(__file__).stem)
root = tk.Tk(); a = mod.DoodleMyShapes(root); root.update()
print(json.dumps({"state_file": str(a.state_file), "shapes": len(a.shapes),
                  "program": a.to_state()["program"]}))
root.destroy()
"""
(Path(HOME) / "probe_body.py").write_text(probe)
env = dict(os.environ, HOME=HOME, PYTHONPATH=f"{HOME}:{here}", DISPLAY=os.environ.get("DISPLAY", ""))
out = subprocess.run(
    [sys.executable, "-c", probe.replace("__file__", repr(str(renamed)))],
    capture_output=True, text=True, env=env,
)
try:
    info = json.loads(out.stdout.strip().splitlines()[-1])
    ok13 = info["state_file"] == str(Path(HOME) / ".doodlemyshapes/state.json") and info["program"] == "DoodleMyShapes"
except Exception as error:  # noqa: BLE001
    ok13, info = False, f"{error}: {out.stdout!r} {out.stderr[-300:]!r}"
check(13, "a renamed program uses the same fixed state file and identifier", ok13, str(info))

custom = Path(HOME) / "scratch/deeper"
root = tk.Tk()
d = DoodleMyShapes(root, state_dir=custom)
root.update()
default_before = (Path(HOME) / ".doodlemyshapes/state.json").read_bytes()
d.add_shape("circle", 200, 200)
written = (custom / "state.json").exists()
untouched = (Path(HOME) / ".doodlemyshapes/state.json").read_bytes() == default_before
portable = d.load_state(custom / "state.json", announce=False)
root.destroy()
check(14, "--state-dir writes there, leaves the default alone, stays portable", written and untouched and portable)

plain = Path(HOME) / "iam_a_file"
plain.write_text("x")
result = subprocess.run(
    [sys.executable, str(here / "doodlemyshapes.py"), "--state-dir", str(plain)],
    capture_output=True, text=True, env=env,
)
check(
    15,
    "--state-dir pointing at a file exits non-zero before opening a window",
    result.returncode != 0 and "not a directory" in result.stderr.lower(),
    f"rc={result.returncode} err={result.stderr.strip()[:120]}",
)

# ------------------------------------------------------------------ 23, 24, 25
root = tk.Tk()
g = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".groups")
root.update()
left = g.add_shape("rectangle", 200, 200)
right = g.add_shape("circle", 500, 300)
loose = g.add_shape("rectangle", 900, 500)
g.select(left)
g.select(right, add=True)
g.group_selection()
gid = next(iter(g.groups))

# 23: the pane edits the group, not its members
ok23 = g.pane_target == ("group", gid)
g.pane_name.insert(0, "Bracket assembly")
g.pane_description.insert("1.0", "Two parts, welded.")
g.commit_pane()
ok23 = ok23 and g.groups[gid].name == "Bracket assembly"
ok23 = ok23 and g.shapes[left].name == "" and g.shapes[right].name == ""

g.select(None)
g.select(right)  # any member shows the same group
ok23 = ok23 and g.pane_target == ("group", gid) and g.pane_name.get() == "Bracket assembly"
g.select(loose)  # an ungrouped shape shows itself
ok23 = ok23 and g.pane_target == ("shape", loose)

document = json.loads((Path(HOME) / ".groups/state.json").read_text())
entry = document["groups"][0]
ok23 = ok23 and entry["name"] == "Bracket assembly" and entry["description"] == "Two parts, welded."
ok23 = ok23 and sorted(entry["members"]) == sorted([left, right])

saved = Path(HOME) / "groups.json"
g.write_state(saved)
before_undo = json.dumps(g.to_state(), sort_keys=True)
g.select(right)
g.pane_name.delete(0, tk.END)
g.pane_name.insert(0, "Renamed")
g.commit_pane()
g.undo()
ok23 = ok23 and json.dumps(g.to_state(), sort_keys=True) == before_undo

g.clear_canvas()
g.load_state(saved, announce=False)
reloaded = next(iter(g.groups.values()))
ok23 = ok23 and reloaded.name == "Bracket assembly" and reloaded.description == "Two parts, welded."
check(23, "groups carry their own name and description, shown and edited in the pane", ok23)

# 24: colouring a whole group is opt-in
members = [k for k in g.shapes if g.shapes[k].group]
solo = [k for k in g.shapes if not g.shapes[k].group]
g.select(members[0])
ok24 = g.color_groups.get() is False  # default
g.set_fill("#e2726e")
coloured = [k for k in members if g.shapes[k].fill == "#e2726e"]
ok24 = ok24 and coloured == [members[0]] and len(g.selection) == len(members)

g.color_groups.set(True)
g.on_color_groups_changed()
g.select(members[0])
g.set_fill("#7bb661")
ok24 = ok24 and all(g.shapes[k].fill == "#7bb661" for k in members)

# an ad-hoc multiple selection is not a group and is always coloured together
g.color_groups.set(False)
g.on_color_groups_changed()
if len(solo) >= 1:
    extra_shape = g.add_shape("circle", 1200, 600)
    g.select(solo[0])
    g.select(extra_shape, add=True)
    g.set_fill("#e8b04b")
    ok24 = ok24 and g.shapes[solo[0]].fill == "#e8b04b" and g.shapes[extra_shape].fill == "#e8b04b"

persisted = json.loads((Path(HOME) / ".groups/state.json").read_text())["settings"]["color_groups"]
root.destroy()
root = tk.Tk()
g2 = DoodleMyShapes(root, state_dir=Path(HOME) / ".groups")
root.update()
ok24 = ok24 and persisted is False and g2.color_groups.get() is False
check(24, "group colouring is off by default, can be enabled, and persists", ok24)

# 25: no orphan group records
grouped = [k for k in g2.shapes if g2.shapes[k].group]
g2.select(grouped[0])
g2.ungroup_selection()
ok25 = g2.groups == {} and json.loads((Path(HOME) / ".groups/state.json").read_text())["groups"] == []

again = [g2.add_shape("rectangle", 300, 700), g2.add_shape("circle", 400, 700)]
g2.select(again[0])
g2.select(again[1], add=True)
g2.group_selection()
ok25 = ok25 and len(g2.groups) == 1
g2.delete_selected()
ok25 = ok25 and g2.groups == {} and g2.pane_target is None
check(25, "groups with no members are pruned from the model and the document", ok25)
root.destroy()

# ---------------------------------------------------------------- 26, 27, 28
root = tk.Tk()
v = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".visible")
root.update()
labels = lambda: sorted(v.canvas.itemcget(i, "text") for i in v.canvas.find_withtag("label"))
group_labels = lambda: sorted(v.canvas.itemcget(i, "text") for i in v.canvas.find_withtag("group-label"))

# 26: the popup no longer offers Properties
entries = []
for index in range(v.shape_popup.index("end") + 1):
    try:
        entries.append(v.shape_popup.entrycget(index, "label"))
    except tk.TclError:
        pass
ok26 = not any("propert" in entry.lower() for entry in entries) and not hasattr(v, "open_properties")
check(26, "the shape popup no longer carries a Properties entry", ok26, str(entries))

# 27: a shape shows its name by default and the toggle hides it
one = v.add_shape("rectangle", 200, 200)
v.select(one)
ok27 = str(v.show_button.cget("state")) == "disabled"  # nothing to show yet
v.pane_name.insert(0, "Plate")
v.commit_pane()
ok27 = ok27 and v.pane_show_text.get() == "Show" and str(v.show_button.cget("state")) == "normal"
ok27 = ok27 and labels() == ["Plate"] and v.shapes[one].show_name is True

v.pane_show.set(False)
v.on_show_toggled()
ok27 = ok27 and v.pane_show_text.get() == "Hide" and labels() == [] and v.shapes[one].show_name is False

v.pane_show.set(True)
v.on_show_toggled()
ok27 = ok27 and v.pane_show_text.get() == "Show" and labels() == ["Plate"]

# it survives save/load and undo
saved = Path(HOME) / "visible.json"
v.pane_show.set(False)
v.on_show_toggled()
v.write_state(saved)
v.clear_canvas()
v.load_state(saved, announce=False)
reloaded = next(iter(v.shapes.values()))
ok27 = ok27 and reloaded.show_name is False and labels() == []
v.undo()
check(27, "shape names show by default; the toggle hides them and persists", ok27)

# 28: a group's name is hidden by default and appears centred on the group
v.clear_canvas()
left = v.add_shape("rectangle", 200, 300)
right = v.add_shape("circle", 700, 300)
v.select(left)
v.select(right, add=True)
v.group_selection()
gid = next(iter(v.groups))
v.pane_name.insert(0, "Assembly")
v.commit_pane()
ok28 = v.groups[gid].show_name is False and v.pane_show_text.get() == "Hide" and group_labels() == []

v.pane_show.set(True)
v.on_show_toggled()
ok28 = ok28 and v.pane_show_text.get() == "Show" and group_labels() == ["Assembly"]

frame = v.group_box(gid)
expected = v.point_to_screen((frame[0] + frame[2]) / 2, (frame[1] + frame[3]) / 2)
actual = v.canvas.coords(v.group_labels[gid])
ok28 = ok28 and all(abs(a - b) < 0.01 for a, b in zip(actual, expected))

# it follows the group when the group moves
v.select(left)
v.begin_drag("move", *v.shapes[left].center)
v.on_drag(press(*v.point_to_screen(v.shapes[left].center[0] + 120, v.shapes[left].center[1] + 80)))
v.on_release(tk.Event())
frame = v.group_box(gid)
expected = v.point_to_screen((frame[0] + frame[2]) / 2, (frame[1] + frame[3]) / 2)
ok28 = ok28 and all(abs(a - b) < 0.01 for a, b in zip(v.canvas.coords(v.group_labels[gid]), expected))

# the group label sits above every member
order = v.canvas.find_all()
ok28 = ok28 and all(order.index(v.group_labels[gid]) > order.index(v.items[m]) for m in (left, right))

# hidden names do not reach the export; shown ones do
v.export_to(Path(HOME) / "shown.svg")
shown_svg = (Path(HOME) / "shown.svg").read_text()
v.pane_show.set(False)
v.on_show_toggled()
v.export_to(Path(HOME) / "hidden.svg")
hidden_svg = (Path(HOME) / "hidden.svg").read_text()
ok28 = ok28 and ">Assembly<" in shown_svg and ">Assembly<" not in hidden_svg

# ungrouping takes the label away with the group
v.select(left)
v.ungroup_selection()
ok28 = ok28 and group_labels() == [] and v.group_labels == {}
check(28, "group names are hidden by default, centre on the group, and follow it", ok28)
root.destroy()

# ---------------------------------------------------------------- 29, 30, 31
root = tk.Tk()
v = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".view")
root.update()
root.update_idletasks()

one = v.add_shape("rectangle", 300, 200)
v.select(one)
v.pane_name.insert(0, "Anchor")
v.commit_pane()
model_before = v.shapes[one].box
screen_before = v.canvas.coords(v.items[one])
doc_before = json.dumps(v.to_state(), sort_keys=True)
history_before = len(v.history)

# 29: space + drag moves the view, not the shapes
v.on_space_down(tk.Event())
ok29 = v.space_held is True
start = v.point_to_screen(*v.shapes[one].center)
v.on_press(press(*start))
ok29 = ok29 and v.drag_mode == "pan"
v.on_drag(press(start[0] - 200, start[1] - 120))
v.on_release(tk.Event())

model_after = v.shapes[one].box
screen_after = v.canvas.coords(v.items[one])
ok29 = ok29 and model_after == model_before  # the model never moved
ok29 = ok29 and [round(n - o) for n, o in zip(screen_after, screen_before)] == [-200, -120, -200, -120]
ok29 = ok29 and json.dumps(v.to_state(), sort_keys=True) == doc_before
ok29 = ok29 and len(v.history) == history_before  # panning is not undoable

# the label and the handles came along
label_at = v.canvas.coords(v.labels[one])
expect_label = v.point_to_screen(*v.shapes[one].center)
ok29 = ok29 and all(abs(a - b) < 0.01 for a, b in zip(label_at, expect_label))

# clicks still land on the right shape after panning
hit = v.shape_at(*v.point_to_model(*v.point_to_screen(*v.shapes[one].center)))
ok29 = ok29 and hit == one

# an X11 auto-repeat burst mid-drag must not stop the pan
v.on_space_down(tk.Event())
resume = v.point_to_screen(*v.shapes[one].center)
v.on_press(press(*resume))
marks = []
for step in (40, 80, 120, 160):
    if step in (80, 160):  # auto-repeat sends release/press pairs while the key is held
        v.on_space_up(tk.Event())
        root.update()
        v.on_space_down(tk.Event())
    v.on_drag(press(resume[0] - step, resume[1]))
    marks.append(round(v.pan_x, 1))
ok29 = ok29 and len(set(marks)) == 4 and v.drag_mode == "pan"

# releasing space mid-drag lets the pan finish
v.on_space_up(tk.Event())
root.update()
time.sleep(0.15)
root.update()
ok29 = ok29 and v.space_held is False and v.drag_mode == "pan"
v.on_drag(press(resume[0] - 400, resume[1]))
ok29 = ok29 and round(v.pan_x, 1) != marks[-1]
v.on_release(tk.Event())
ok29 = ok29 and v.drag_mode is None

# space inside a text field types a space instead of arming the pan
v.select(one)
v.pane_name.focus_set()
root.update()
v.on_space_down(tk.Event())
ok29 = ok29 and v.typing() is True and v.space_held is False
v.canvas.focus_set()
root.update()
check(29, "space + drag pans, survives auto-repeat, and stays out of text fields", ok29)

# 30: negative model coordinates are legal and survive a round trip
v.pan_to(0.0, 0.0)
v.select(one)
v.begin_drag("move", *v.shapes[one].center)
v.on_drag(press(*v.point_to_screen(v.shapes[one].center[0] - 600, v.shapes[one].center[1] - 400)))
v.on_release(tk.Event())
negative = v.shapes[one]
stored = [round(value, 2) for value in (negative.x, negative.y, negative.width, negative.height)]
ok30 = negative.x < 0 and negative.y < 0

saved = Path(HOME) / "negative.json"
v.write_state(saved)
v.clear_canvas()
v.load_state(saved, announce=False)
back = next(iter(v.shapes.values()))
# position and size are each rounded to 2dp on write, so compare those, not the derived far edge
ok30 = ok30 and [round(value, 2) for value in (back.x, back.y, back.width, back.height)] == stored

# the grid still covers the visible area when the origin is off screen
v.pan_to(-900.0, -700.0)
ok30 = ok30 and len(v.canvas.find_withtag("grid")) > 0

# and export is unaffected by where the view happens to be
v.pan_to(0.0, 0.0)
v.export_to(Path(HOME) / "v1.png")
v.pan_to(-2500.0, 1800.0)
v.export_to(Path(HOME) / "v2.png")
ok30 = ok30 and (Path(HOME) / "v1.png").read_bytes() == (Path(HOME) / "v2.png").read_bytes()
check(30, "negative coordinates round-trip; grid and export ignore the viewport", ok30)

# 31: centring, and right click only
v.clear_canvas()
first = v.add_shape("rectangle", 200, 200)
second = v.add_shape("circle", 1400, 900)
v.pan_to(9000.0, -4000.0)
v.center_drawing()
boxes = [r.box for r in v.shapes.values()]
mid = ((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2,
       (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2)
centre_screen = v.point_to_screen(*mid)
viewport_middle = (v.canvas.winfo_width() / 2, v.canvas.winfo_height() / 2)
ok31 = all(abs(a - b) < 1.0 for a, b in zip(centre_screen, viewport_middle))

v.clear_canvas()
v.pan_to(500.0, 500.0)
v.center_drawing()
ok31 = ok31 and (v.pan_x, v.pan_y) == (0.0, 0.0)  # nothing drawn -> back to the origin

# no view gesture may touch the document, the state file or the undo history
state_path = Path(HOME) / ".view/state.json"
v.add_shape("rectangle", 400, 400)
quiet = (len(v.history), state_path.read_bytes(), json.dumps(v.to_state(), sort_keys=True))
v.on_space_down(tk.Event())
v.on_press(press(600, 400))
v.on_drag(press(420, 300))
v.on_release(tk.Event())
v.on_space_up(tk.Event())
root.update()
v.on_middle_press(press(500, 500))
v.on_drag(press(250, 350))
v.on_middle_release(tk.Event())
v.center_drawing()
ok31 = ok31 and (v.pan_x, v.pan_y) != (0.0, 0.0)  # the view really did move
ok31 = ok31 and (len(v.history), state_path.read_bytes(), json.dumps(v.to_state(), sort_keys=True)) == quiet
ok31 = ok31 and json.loads(v.json_text.get("1.0", "end-1c")) == v.to_state()

entries = []
for index in range(v.canvas_popup.index("end") + 1):
    try:
        entries.append(v.canvas_popup.entrycget(index, "label"))
    except tk.TclError:
        pass
ok31 = ok31 and "Center drawing" in entries

# the popup is on the right button; the middle button pans instead of posting a menu
posted = []
v._post = lambda menu, event: posted.append(menu)
v.on_popup(press(30, 30))
ok31 = ok31 and posted == [v.canvas_popup] and bool(v.canvas.bind("<Button-3>"))

pan_before = (v.pan_x, v.pan_y)
model_before = {k: v.shapes[k].box for k in v.shapes}
v.on_middle_press(press(500, 400))
v.on_drag(press(300, 250))
moved = (v.pan_x, v.pan_y) != pan_before
v.on_middle_release(tk.Event())
ok31 = ok31 and moved and v.drag_mode is None and {k: v.shapes[k].box for k in v.shapes} == model_before
ok31 = ok31 and not posted[1:]  # no menu appeared from the middle button
check(31, "view gestures record nothing; right button posts the menu, middle button pans", ok31, str(entries))
root.destroy()

# ---------------------------------------------------------------- 32, 33
root = tk.Tk()
b = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".band")
root.update()
root.update_idletasks()

# a row of shapes, plus a group off to the right
left = b.add_shape("rectangle", 200, 200)
middle = b.add_shape("circle", 400, 200)
far = b.add_shape("rectangle", 1400, 800)
pair_a = b.add_shape("rectangle", 800, 500)
pair_b = b.add_shape("circle", 1000, 500)
b.select(pair_a)
b.select(pair_b, add=True)
b.group_selection()
b.select(None)


def band(x1, y1, x2, y2, shift=False):
    b.on_press(press(*b.point_to_screen(x1, y1), shift=shift))
    b.on_drag(press(*b.point_to_screen((x1 + x2) / 2, (y1 + y2) / 2)))
    b.on_drag(press(*b.point_to_screen(x2, y2)))
    b.on_release(press(*b.point_to_screen(x2, y2)))


# 32: the band selects what it encloses or touches.
# A band always starts on empty canvas - pressing on a shape moves it instead.
box_left = b.shapes[left].box
box_middle = b.shapes[middle].box

band(box_left[0] - 20, box_left[1] - 20, box_middle[2] + 20, box_middle[3] + 20)
ok32 = sorted(b.selection) == sorted([left, middle]) and far not in b.selection

# merely clipping an edge is enough: start clear of the shape and drag back onto it
band(box_left[2] + 120, box_left[1] + 5, box_left[2] - 1, box_left[1] + 10)
ok32 = ok32 and b.selection == [left]

# nothing moved while banding
ok32 = ok32 and b.shapes[left].box == box_left and b.shapes[middle].box == box_middle

# a band that misses everything clears the selection
band(box_left[0] - 500, box_left[1] - 500, box_left[0] - 400, box_left[1] - 400)
ok32 = ok32 and b.selection == [] and b.selected is None

# one shape banded behaves as a plain single selection: no marquees, pane on the shape
band(box_left[0] - 10, box_left[1] - 10, box_left[2] + 10, box_left[3] + 10)
ok32 = ok32 and b.selection == [left] and b.canvas.find_withtag("marquee") == ()
ok32 = ok32 and b.pane_target == ("shape", left) and len(b.canvas.find_withtag("handle")) == 8

# catching one member of a group catches the whole group.
# Aim at the member itself: the corner of a group's bounding box is often empty space.
member_box = b.shapes[pair_a].box
band(member_box[0] - 5, member_box[1] - 5, member_box[0] + 5, member_box[1] + 5)
ok32 = ok32 and sorted(b.selection) == sorted([pair_a, pair_b])
ok32 = ok32 and b.pane_target == ("group", next(iter(b.groups)))

# shift extends an existing selection
band(box_left[0] - 10, box_left[1] - 10, box_left[2] + 10, box_left[3] + 10)
band(box_middle[0] - 10, box_middle[1] - 10, box_middle[2] + 10, box_middle[3] + 10, shift=True)
ok32 = ok32 and sorted(b.selection) == sorted([left, middle])

# a click that does not travel is still a plain deselect, and leaves no band behind
b.on_press(press(*b.point_to_screen(box_left[0] - 400, box_left[1] - 400)))
b.on_release(press(*b.point_to_screen(box_left[0] - 400, box_left[1] - 400)))
ok32 = ok32 and b.selection == [] and b.canvas.find_withtag("rubber-band") == ()

# the band is visible while dragging, stippled, and gone afterwards
b.on_press(press(*b.point_to_screen(box_left[0] - 30, box_left[1] - 30)))
b.on_drag(press(*b.point_to_screen(box_middle[2] + 30, box_middle[3] + 30)))
drawn = b.canvas.find_withtag("rubber-band")
ok32 = ok32 and len(drawn) == 1 and b.canvas.itemcget(drawn[0], "stipple") == "gray12"
ok32 = ok32 and b.canvas.itemcget(drawn[0], "dash") != ""
b.on_release(press(*b.point_to_screen(box_middle[2] + 30, box_middle[3] + 30)))
ok32 = ok32 and b.canvas.find_withtag("rubber-band") == ()

# selecting changes nothing on disk
doc = json.dumps(b.to_state(), sort_keys=True)
history = len(b.history)
band(box_left[0] - 40, box_left[1] - 40, box_middle[2] + 40, box_middle[3] + 40)
ok32 = ok32 and json.dumps(b.to_state(), sort_keys=True) == doc and len(b.history) == history
check(32, "rubber band selects what it touches, groups included, and records nothing", ok32)


# 33: the popup only offers grouping when it applies
def popup_entries():
    b.populate_shape_popup()
    found = []
    for index in range(b.shape_popup.index("end") + 1):
        try:
            found.append(b.shape_popup.entrycget(index, "label"))
        except tk.TclError:
            pass
    return found


b.select(far)  # a lone, ungrouped shape
lone = popup_entries()
ok33 = lone == ["Color", "Depth"]

b.select(left)
b.select(middle, add=True)  # two loose shapes
two = popup_entries()
ok33 = ok33 and two == ["Color", "Depth", "Group selection"]

b.select(pair_a)  # a group
grouped = popup_entries()
ok33 = ok33 and grouped == ["Color", "Depth", "Ungroup"]

b.select(pair_a)
b.select(far, add=True)  # a group plus a loose shape: both make sense
mixed = popup_entries()
ok33 = ok33 and mixed == ["Color", "Depth", "Group selection", "Ungroup"]

# and the entries still work after being rebuilt
b.select(left)
b.select(middle, add=True)
b.populate_shape_popup()
b.shape_popup.invoke(b.shape_popup.index("Group selection"))
ok33 = ok33 and b.shapes[left].group and b.shapes[left].group == b.shapes[middle].group
check(33, "grouping entries appear only when they apply", ok33, f"{lone} / {two} / {grouped} / {mixed}")
root.destroy()

# -------------------------------------------------------------------- 34, 35
root = tk.Tk()
n = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".nested")
root.update()
root.update_idletasks()

a1 = n.add_shape("rectangle", 200, 200)
a2 = n.add_shape("circle", 400, 200)
b1 = n.add_shape("rectangle", 800, 200)
b2 = n.add_shape("circle", 1000, 200)
solo = n.add_shape("rectangle", 1400, 700)

n.select(a1)
n.select(a2, add=True)
n.group_selection()
inner_a = n.root_group(a1)
n.select(b1)
n.select(b2, add=True)
n.group_selection()
inner_b = n.root_group(b1)
n.pane_name.insert(0, "Inner B")
n.commit_pane()

# 34: grouping two groups keeps them whole
n.select(a1)              # selects all of inner A
n.select(b1, add=True)    # adds all of inner B
ok34 = sorted(n.selection) == sorted([a1, a2, b1, b2])
ok34 = ok34 and len(n.selection_units()) == 2  # two pieces, not four
n.group_selection()
outer = n.root_group(a1)
ok34 = ok34 and outer not in (inner_a, inner_b) and len(n.groups) == 3
ok34 = ok34 and n.groups[inner_a].parent == outer and n.groups[inner_b].parent == outer
ok34 = ok34 and n.groups[outer].parent == ""
ok34 = ok34 and n.groups[inner_b].name == "Inner B"  # the subgroup kept its properties
ok34 = ok34 and sorted(n.child_groups(outer)) == sorted([inner_a, inner_b])
ok34 = ok34 and sorted(n.group_shapes(outer)) == sorted([a1, a2, b1, b2])
ok34 = ok34 and n.group_index().get(outer, []) == []  # it holds groups, not shapes directly

# touching any shape selects the whole outer group, and the pane edits the outer group
n.select(None)
n.select(b2)
ok34 = ok34 and sorted(n.selection) == sorted([a1, a2, b1, b2]) and n.pane_target == ("group", outer)

# moving one member moves everything beneath the outer group
before = {k: n.shapes[k].box for k in (a1, a2, b1, b2, solo)}
n.begin_drag("move", *n.shapes[b2].center)
n.on_drag(press(*n.point_to_screen(n.shapes[b2].center[0] + 150, n.shapes[b2].center[1] + 75)))
n.on_release(tk.Event())
deltas = {k: tuple(round(x - y, 6) for x, y in zip(n.shapes[k].box, before[k])) for k in (a1, a2, b1, b2)}
ok34 = ok34 and len(set(deltas.values())) == 1 and n.shapes[solo].box == before[solo]

# the nesting round-trips
saved = Path(HOME) / "nested.json"
n.write_state(saved)
entry = {g["uuid"]: g for g in json.loads(saved.read_text())["groups"]}
ok34 = ok34 and entry[inner_a]["parent"] == outer and sorted(entry[outer]["subgroups"]) == sorted([inner_a, inner_b])
ok34 = ok34 and entry[outer]["members"] == []
before_state = json.dumps(n.to_state(), sort_keys=True)
n.clear_canvas()
n.load_state(saved, announce=False)
ok34 = ok34 and json.dumps(n.to_state(), sort_keys=True) == before_state
check(34, "grouping wraps existing groups instead of dissolving them", ok34)

# 35: ungrouping peels one layer only
a1 = next(k for k in n.shapes if n.shapes[k].uuid == a1)
n.select(a1)
outer = n.root_group(a1)
n.ungroup_selection()
ok35 = outer not in n.groups and len(n.groups) == 2
ok35 = ok35 and n.groups[inner_a].parent == "" and n.groups[inner_b].parent == ""
ok35 = ok35 and n.groups[inner_b].name == "Inner B"

# the subgroups are now the top level: selecting a member picks up its subgroup only
n.select(None)
n.select(a1)
ok35 = ok35 and sorted(n.selection) == sorted([a1, a2]) and n.pane_target == ("group", inner_a)

# ungroup again dissolves the last layer
n.ungroup_selection()
ok35 = ok35 and inner_a not in n.groups and n.shapes[a1].group == "" and inner_b in n.groups
n.select(a1)
ok35 = ok35 and n.selection == [a1] and n.pane_target == ("shape", a1)

# the popup reflects the nesting
def entries_for(*picked):
    n.select(picked[0])
    for extra_pick in picked[1:]:
        n.select(extra_pick, add=True)
    n.populate_shape_popup()
    found = []
    for index in range(n.shape_popup.index("end") + 1):
        try:
            found.append(n.shape_popup.entrycget(index, "label"))
        except tk.TclError:
            pass
    return found

ok35 = ok35 and entries_for(a1) == ["Color", "Depth"]
ok35 = ok35 and entries_for(b1) == ["Color", "Depth", "Ungroup"]  # a whole group, nothing to add
ok35 = ok35 and entries_for(b1, a1) == ["Color", "Depth", "Group selection", "Ungroup"]

# a group nested inside itself in a hand-edited file is repaired, not hung
broken = Path(HOME) / "cycle.json"
document = json.loads(saved.read_text())
for group in document["groups"]:
    group["parent"] = document["groups"][0]["uuid"] if group is not document["groups"][0] else group["uuid"]
broken.write_text(json.dumps(document))
ok35 = ok35 and n.load_state(broken, announce=False)
ok35 = ok35 and all(n.top_group(g) in n.groups or not n.groups[g].parent for g in n.groups)
check(35, "ungrouping peels the outermost layer and keeps subgroups", ok35)
root.destroy()

# ------------------------------------------------------------------------ 36
root = tk.Tk()
root.geometry("1000x640")
r = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".resize")
root.update()
root.update_idletasks()
r.add_shape("rectangle", 300, 200)
r.add_shape("circle", 900, 600)


def settle(ms=600):
    end = time.time() + ms / 1000
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def centred(app):
    boxes = [record.box for record in app.shapes.values()]
    if not boxes:
        return (app.pan_x, app.pan_y) == (0.0, 0.0)
    mid = (
        (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2,
        (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2,
    )
    sx, sy = app.point_to_screen(*mid)
    return abs(sx - app.canvas.winfo_width() / 2) < 1.5 and abs(sy - app.canvas.winfo_height() / 2) < 1.5


# a pan is left alone while no resize happens
r.pan_to(4000.0, -3000.0)
settle(500)
ok36 = not centred(r) and round(r.pan_x) == 4000

# a burst of resize events recentres once, after the burst
for width in (960, 900, 840, 800):
    root.geometry(f"{width}x600")
    root.update()
    time.sleep(0.05)
ok36 = ok36 and not centred(r)  # still settling: the burst has not finished
settle(700)
ok36 = ok36 and centred(r)

# recentring writes nothing and records nothing
doc = json.dumps(r.to_state(), sort_keys=True)
history = len(r.history)
state_file = Path(HOME) / ".resize/state.json"
written = state_file.read_bytes()
root.geometry("1080x700")
settle(700)
ok36 = ok36 and centred(r)
ok36 = ok36 and json.dumps(r.to_state(), sort_keys=True) == doc and len(r.history) == history
ok36 = ok36 and state_file.read_bytes() == written

# with nothing drawn a resize returns the view to the origin
r.clear_canvas()
r.pan_to(700.0, 700.0)
root.geometry("940x620")
settle(700)
ok36 = ok36 and (r.pan_x, r.pan_y) == (0.0, 0.0)

# no timer is left behind to fire into a closed window
r.add_shape("circle", 300, 300)
root.geometry("900x600")
root.update()
r.cancel_pending()
ok36 = ok36 and r.resize_job is None and r.space_release_job is None
check(36, "a finished window resize recentres the drawing, once, recording nothing", ok36)
root.destroy()

# ------------------------------------------------------------------------ 37
root = tk.Tk()
root.geometry("1000x640")
c = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".fill")
root.update()
root.update_idletasks()

marked = lambda: [col for col, sw in c.fill_swatches.items() if str(sw.cget("highlightbackground")) == "#1f6feb"]

# the pane hides when there is nothing with properties, and Undo stays reachable
c.select(None)
root.update()
ok37 = c.pane_visible is False and not c.pane.winfo_ismapped()
ok37 = ok37 and c.undo_button.winfo_ismapped()  # Undo lives in the status bar now

wide = c.canvas.winfo_width()
one = c.add_shape("rectangle", 300, 200)
root.update()
narrow = c.canvas.winfo_width()
ok37 = ok37 and c.pane_visible is True and c.pane.winfo_ismapped() and narrow < wide

# the swatch row shows the current fill and sets a new one
ok37 = ok37 and marked() == ["#d3d3d3"] and len(c.fill_swatches) == 8
c.fill_swatches["#7bb661"].event_generate("<Button-1>")
root.update()
ok37 = ok37 and c.shapes[one].fill == "#7bb661" and marked() == ["#7bb661"]

# a mixed selection marks nothing
two = c.add_shape("circle", 700, 400)
c.select(one)
c.select(two, add=True)
ok37 = ok37 and marked() == []
c.fill_swatches["#e2726e"].event_generate("<Button-1>")
ok37 = ok37 and c.shapes[one].fill == "#e2726e" and c.shapes[two].fill == "#e2726e" and marked() == ["#e2726e"]

# with group colouring off, the swatch touches only the clicked shape
c.select(one)
c.select(two, add=True)
c.group_selection()
c.select(one)
c.fill_swatches["#6c9bd2"].event_generate("<Button-1>")
ok37 = ok37 and c.shapes[one].fill == "#6c9bd2" and c.shapes[two].fill == "#e2726e"

# a colour change is undoable and recorded once
history = len(c.history)
c.fill_swatches["#a184c8"].event_generate("<Button-1>")
ok37 = ok37 and len(c.history) == history + 1
c.undo()
ok37 = ok37 and c.shapes[one].fill == "#6c9bd2"

# hiding and showing the pane must not recentre the drawing
c.pan_to(1500.0, 1500.0)
mark = (round(c.pan_x), round(c.pan_y))
c.select(None)
root.update()
end = time.time() + 0.7
while time.time() < end:
    root.update()
    time.sleep(0.01)
ok37 = ok37 and not c.pane_visible and (round(c.pan_x), round(c.pan_y)) == mark
c.select(one)
root.update()
end = time.time() + 0.7
while time.time() < end:
    root.update()
    time.sleep(0.01)
ok37 = ok37 and c.pane_visible and (round(c.pan_x), round(c.pan_y)) == mark
check(37, "the pane carries a fill selector and hides itself when nothing is selected", ok37)
root.destroy()

# ------------------------------------------------------------------------ 38
root = tk.Tk()
root.geometry("1100x700")
d = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".depthlabel")
root.update()
root.update_idletasks()

# label item ids are recreated on every restack, so always read them fresh
place = lambda item: d.canvas.find_all().index(item)

m1 = d.add_shape("rectangle", 300, 300)
m2 = d.add_shape("circle", 500, 300)
outsider = d.add_shape("rectangle", 400, 300)
d.select(m1)
d.select(m2, add=True)
d.group_selection()
inner = d.root_group(m1)
d.pane_name.insert(0, "Assembly")
d.commit_pane()
d.pane_show.set(True)
d.on_show_toggled()

# the name sits above its own members
ok38 = all(place(d.group_labels[inner]) > place(d.items[member]) for member in (m1, m2))

# a shape stacked above the group covers the name
d.select(outsider)
d.set_depth("top")
ok38 = ok38 and place(d.items[outsider]) > place(d.group_labels[inner])

# and stops covering it once it goes below
d.select(outsider)
d.set_depth("bottom")
ok38 = ok38 and place(d.items[outsider]) < place(d.group_labels[inner])

# nesting: each name rides at its own group's depth
loose = d.add_shape("circle", 900, 300)
d.select(m1)
d.select(loose, add=True)
d.group_selection()
outer = d.root_group(m1)
d.pane_name.insert(0, "Outer")
d.commit_pane()
d.pane_show.set(True)
d.on_show_toggled()

ok38 = ok38 and place(d.group_labels[inner]) > place(d.items[m2])
ok38 = ok38 and place(d.group_labels[outer]) > place(d.group_labels[inner])
ok38 = ok38 and all(place(d.group_labels[outer]) > place(d.items[m]) for m in d.group_shapes(outer))

# a shape genuinely on top covers both names
d.select(outsider)
d.set_depth("top")
ok38 = ok38 and place(d.items[outsider]) > place(d.group_labels[outer])
ok38 = ok38 and place(d.items[outsider]) > place(d.group_labels[inner])

# handles stay above everything, the grid below everything
ok38 = ok38 and min(place(h) for h in d.canvas.find_withtag("handle")) > place(d.group_labels[outer])
ok38 = ok38 and max(place(g) for g in d.canvas.find_withtag("grid")) < place(d.items[m1])

# the ordering survives a reload
saved = Path(HOME) / "depthlabel.json"
d.write_state(saved)
d.clear_canvas()
d.load_state(saved, announce=False)
top_group = next(g for g in d.groups if not d.groups[g].parent)
top_member = d.group_shapes(top_group)[-1]
ok38 = ok38 and place(d.group_labels[top_group]) > place(d.items[top_member])
check(38, "a group's name rides at its group's depth and is covered by shapes above it", ok38)
root.destroy()

# ------------------------------------------------------------------------ 39
root = tk.Tk()
m = DoodleMyShapes(root, clean=True, state_dir=Path(HOME) / ".multimove")
root.update()
root.update_idletasks()

first = m.add_shape("rectangle", 300, 200)
second = m.add_shape("circle", 700, 400)
third = m.add_shape("rectangle", 1100, 250)


def centre(identifier, dx=0.0, dy=0.0):
    cx, cy = m.shapes[identifier].center
    return m.point_to_screen(cx + dx, cy + dy)


def boxes():
    return {k: m.shapes[k].box for k in (first, second, third)}


# pressing inside a shift-built selection keeps it, and the whole set drags as one
m.select(first)
m.select(second, add=True)
before = boxes()
m.on_press(press(*centre(first)))
ok39 = len(m.selection) == 2 and m.selected == first and m.drag_mode == "move"
m.on_drag(press(*centre(first, 150, 80)))
m.on_release(tk.Event())
deltas = {k: tuple(round(n - o, 6) for n, o in zip(m.shapes[k].box, before[k])) for k in (first, second)}
ok39 = ok39 and deltas[first] == deltas[second] and deltas[first] != (0.0, 0.0, 0.0, 0.0)
ok39 = ok39 and m.shapes[third].box == before[third]  # the unselected one stays put
ok39 = ok39 and len(m.selection) == 2  # and the selection survives the drag

# the whole move is a single undo step
history = len(m.history)
before = boxes()
restored = {k: (m.shapes[k].x, m.shapes[k].y, m.shapes[k].width, m.shapes[k].height) for k in (first, second)}
m.on_press(press(*centre(second)))
m.on_drag(press(*centre(second, -120, 40)))
m.on_release(tk.Event())
ok39 = ok39 and len(m.history) == history + 1
m.undo()
# undo restores through the state document: position and size each round to 2dp independently,
# so compare the stored fields rather than the derived far corner
fields = lambda: {
    k: tuple(round(v, 2) for v in (m.shapes[k].x, m.shapes[k].y, m.shapes[k].width, m.shapes[k].height))
    for k in (first, second)
}
ok39 = ok39 and fields() == {k: tuple(round(v, 2) for v in restored[k]) for k in (first, second)}

# three at once, dragged from the middle one
m.select(first)
m.select(second, add=True)
m.select(third, add=True)
before = boxes()
m.on_press(press(*centre(second)))
m.on_drag(press(*centre(second, 80, -160)))
m.on_release(tk.Event())
deltas = {k: tuple(round(n - o, 6) for n, o in zip(m.shapes[k].box, before[k])) for k in before}
ok39 = ok39 and len(set(deltas.values())) == 1 and deltas[first] != (0.0, 0.0, 0.0, 0.0)

# a press that never travels is a click: the selection collapses to that shape
m.select(first)
m.select(second, add=True)
m.on_press(press(*centre(second)))
m.on_release(tk.Event())
ok39 = ok39 and m.selection == [second] and m.pane_target == ("shape", second)

# shift-clicking a selected shape removes it and starts no drag
m.select(first)
m.select(second, add=True)
m.on_press(press(*centre(second), shift=True))
ok39 = ok39 and m.selection == [first] and m.drag_mode is None

# clicking an unselected shape still replaces the selection
m.select(first)
m.select(second, add=True)
m.on_press(press(*centre(third)))
ok39 = ok39 and m.selection == [third]
m.on_release(tk.Event())

# a collapsing click writes nothing
doc = json.dumps(m.to_state(), sort_keys=True)
history = len(m.history)
m.select(first)
m.select(second, add=True)
m.on_press(press(*centre(first)))
m.on_release(tk.Event())
ok39 = ok39 and json.dumps(m.to_state(), sort_keys=True) == doc and len(m.history) == history
check(39, "a multiple selection drags as one piece and collapses on a click", ok39)
root.destroy()

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("failed:", FAIL)
    sys.exit(1)
