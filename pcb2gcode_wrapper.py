import json
import subprocess
import shutil
import os
import re

OUTPUT_DIR = "output"
MERGED_FILE = os.path.join(OUTPUT_DIR, "merged.nc")
TOOLS_FILE = os.path.join(OUTPUT_DIR, "tools.json")

PREAMBLE = \
"""
G17
G90
G21
G94
G54
"""

# G28: park the machine so the finished board can be removed
POSTAMBLE = \
"""
M5
G28
"""

# Which pcb2gcode output files go into the merged program, in machining order.
# Each entry: (file name, tool category). Tools of the same category and
# diameter share one tool number (e.g. outline cutter and milldrill end mill).
SINGLE_SIDED_ORDER = [
    ("drill.ngc", "drill"),
    ("front.ngc", "vbit"),
    ("milldrill.ngc", "endmill"),
    ("outline.ngc", "endmill"),
]
DOUBLE_SIDED_ORDER = [
    ("milldrill.ngc", "endmill"),
    ("drill.ngc", "drill"),
    ("front.ngc", "vbit"),
    ("outline.ngc", "endmill"),
    ("back.ngc", "vbit"),
]
# Order in which tool numbers are assigned: V-bit 1, outline cutter 2, drills 3, 4, 5...
TOOL_ASSIGN_ORDER = ["front.ngc", "back.ngc", "outline.ngc", "drill.ngc", "milldrill.ngc"]

CATEGORY_NAMES = {"vbit": "V-bit", "endmill": "End mill", "drill": "Drill"}
OPERATION_NAMES = {
    "drill.ngc": "Drilling",
    "milldrill.ngc": "Milled holes",
    "front.ngc": "Isolation (front)",
    "back.ngc": "Isolation (back)",
    "outline.ngc": "Outline",
}

MSG_RE = re.compile(r"^\(MSG,\s*(.*?)\s*\)$")
DIAMETER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm")
SPEED_RE = re.compile(r"^(?:G0?0\s+)?S(\d+(?:\.\d+)?)$")
TOOL_SELECT_RE = re.compile(r"^T\d+$")


def process(gerber_front, gerber_back, gerber_outline, drill):
    print(f"Front: {gerber_front}")
    print(f"Bottom: {gerber_back}")
    print(f"Outline: {gerber_outline}")
    print(f"Drill: {drill}")

    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    os.mkdir(OUTPUT_DIR)

    if gerber_front and gerber_back:
        job_type = "both"
    elif gerber_front:
        job_type = "front"
    elif gerber_back:
        job_type = "back"
    else:
        job_type = None

    cmd = ['pcb2gcode', '--config', 'config.cfg']
    if gerber_front:
        cmd += ['--front', gerber_front]
    if gerber_back:
        cmd += ['--back', gerber_back]
    if gerber_outline:
        cmd += ['--outline', gerber_outline]
    if drill:
        cmd += ['--drill', drill]

    if job_type == "back":
        cmd += ['--cut-side', "back"]
        cmd += ['--drill-side', "back"]
    else:
        cmd += ['--cut-side', "front"]
        cmd += ['--drill-side', "front"]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Return code: {proc.returncode}")
    print(f"result: {proc.stdout}")
    print(f"error: {proc.stderr}")
    if proc.returncode != 0:
        raise RuntimeError(f"pcb2gcode failed (code {proc.returncode}):\n{proc.stderr.strip()}")

    order = DOUBLE_SIDED_ORDER if job_type == "both" else SINGLE_SIDED_ORDER
    merge_gcode(order, MERGED_FILE)


def merge_gcode(order, merged_path):
    """Patch all existing pcb2gcode outputs and write them as one Carvera program."""
    categories = dict(order)
    present = {name for name, _ in order if os.path.exists(os.path.join(OUTPUT_DIR, name))}

    tools = ToolTable()
    for name in TOOL_ASSIGN_ORDER:
        if name in present:
            tools.register_file(os.path.join(OUTPUT_DIR, name), categories[name])

    with open(merged_path, "w") as f:
        f.write(PREAMBLE.strip() + "\n\n")
        f.write(tools.describe() + "\n")
        for name, category in order:
            if name not in present:
                continue
            f.write(f"( {name} )\n")
            f.write(patch_gcode(os.path.join(OUTPUT_DIR, name), category, tools))
            f.write(f"\n( {name} end )\n\n")
        f.write(POSTAMBLE.strip() + "\n")

    with open(TOOLS_FILE, "w") as f:
        json.dump(tools.to_list(), f, indent=2)


class ToolTable:
    """Assigns project-wide tool numbers to (category, diameter) pairs."""

    def __init__(self):
        self.numbers = {}  # (category, diameter) -> tool number
        self.labels = {}  # tool number -> description
        self.operations = {}  # tool number -> [operation names, in program order]
        self.loaded = None  # tool currently in the spindle while patching

    def register_file(self, path, category):
        with open(path) as f:
            for line in f:
                if m := MSG_RE.match(line.strip()):
                    self.number(category, m.group(1))

    def number(self, category, message):
        m = DIAMETER_RE.search(message)
        if not m:
            raise ValueError(f"Cannot read tool diameter from '{message}'")
        key = (category, round(float(m.group(1)), 3))
        if key not in self.numbers:
            tool = len(self.numbers) + 1
            self.numbers[key] = tool
            self.labels[tool] = f"{category} {key[1]:g}mm"
        return self.numbers[key]

    def mark_used(self, tool, operation):
        ops = self.operations.setdefault(tool, [])
        if operation not in ops:
            ops.append(operation)

    def to_list(self):
        return [
            {
                "number": tool,
                "type": CATEGORY_NAMES.get(category, category),
                "diameter": diameter,
                "operations": self.operations.get(tool, []),
            }
            for (category, diameter), tool in sorted(self.numbers.items(), key=lambda kv: kv[1])
        ]

    def describe(self):
        lines = ["( Tools: )"]
        for tool, label in self.labels.items():
            lines.append(f"( T{tool}: {label} )")
        return "\n".join(lines) + "\n"


# make pcb2gcode output compatible with carvera
def patch_gcode(path, category, tools):
    """Rewrite one pcb2gcode file into a section of the merged Carvera program.

    - T<n> / M6 / M0 sequences become a single `M6 T<global number>`
    - M3 gets the spindle speed (pcb2gcode puts it on a separate S line)
    - program-end codes (M2, M9) and no-op dwells are dropped
    """
    with open(path) as f:
        lines = f.read().splitlines()

    out = []
    speed = None
    tool = None
    for raw in lines:
        line = raw.strip()
        code = line.split("(", 1)[0].strip().upper()

        if m := SPEED_RE.match(code):
            speed = m.group(1)
            continue
        if m := MSG_RE.match(line):
            tool = tools.number(category, m.group(1))
            out.append(f"( {m.group(1)} )")
            continue
        if TOOL_SELECT_RE.match(code) or code in ("M0", "M2", "M9", "G04 P0"):
            continue
        if code == "M6":
            if tool is None:
                raise ValueError(f"{path}: tool change without tool description")
            # skip a change to the tool that is already in the spindle (e.g. milldrill -> outline)
            if tool != tools.loaded:
                out.append(f"M6 T{tool}")
                tools.loaded = tool
            tools.mark_used(tool, OPERATION_NAMES.get(os.path.basename(path), os.path.basename(path)))
            continue
        if code == "M3":
            out.append(f"M3 S{speed}" if speed else "M3")
            continue
        out.append(raw.rstrip())

    content = "\n".join(out).strip() + "\n"
    with open(f"{path}_patched.nc", 'w') as f:
        f.write(content)
    return content
