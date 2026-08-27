#!/usr/bin/env python3
"""Runs the 22 acceptance checks from the specification against doodlemyshapes.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
a.on_drag(press(a.to_screen(a.shapes[one].center[0] + 150), a.to_screen(a.shapes[one].center[1] + 90)))
a.on_release(tk.Event())
deltas = [tuple(round(n - o, 6) for n, o in zip(a.shapes[k].box, before[k])) for k in (one, two)]
check(7, "dragging a group moves every member by one delta", len(a.selection) == 2 and deltas[0] == deltas[1], str(deltas))

before = {k: a.shapes[k].box for k in (one, two)}
frame = a.selection_box()
a.begin_drag("resize", frame[2], frame[3])
a.drag_handle = "se"
a.on_drag(press(a.to_screen(frame[2] + 200), a.to_screen(frame[3] + 120)))
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
a.on_drag(press(a.to_screen(a.shapes[extra].center[0] + 100), a.to_screen(a.shapes[extra].center[1])))
a.on_release(tk.Event())
steps.append(("move", before_move))

before_resize = snapshot()
box = a.shapes[extra].box
a.begin_drag("resize", box[2], box[3])
a.drag_handle = "se"
a.on_drag(press(a.to_screen(box[2] + 80), a.to_screen(box[3] + 80)))
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
    a.on_drag(press(a.to_screen(box[0] - 900), a.to_screen(box[1] - 900)))
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
    a.on_drag(press(a.to_screen(frame[0] - 900), a.to_screen(frame[1] - 900)))
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

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("failed:", FAIL)
    sys.exit(1)
