import subprocess
import shutil
import os
import re

PREAMBLE = \
"""
G17
G90
G21
G54
M6 T1
M3 S20000
"""

POSTAMBLE = \
"""
M5
"""

def process(gerber_front, gerber_back, gerber_outline, drill):
    print(f"Front: {gerber_front}")
    print(f"Bottom: {gerber_back}")
    print(f"Outline: {gerber_outline}")
    print(f"Drill: {drill}")

    try:
        shutil.rmtree("output")
    except Exception as e:
        print(f"Exception: {e}")

    os.mkdir("output")

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

    if job_type == "both":
        cmd += ['--cut-side', "front"]
        cmd += ['--drill-side', "front"]
    elif job_type == "front":
        cmd += ['--cut-side', "front"]
        cmd += ['--drill-side', "front"]
    elif job_type == "back":
        cmd += ['--cut-side', "back"]
        cmd += ['--drill-side', "back"]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (result, error) = proc.communicate()
    rc = proc.wait()
    print(f"Return code: {rc}")
    print(f"result: {result}")
    print(f"error: {error}")

    with open("merged.nc", "w") as f:
        if job_type == "both":
            # 2-sided workflow
            if os.path.exists("output/milldrill.ngc"):
                f.write("( milldrill.ngc )\n")
                f.write(patch_gcode("output/milldrill.ngc", None, 1))
                f.write("\n\n( milldrill.ngc end )\n\n")

            if os.path.exists("output/drill.ngc"):
                f.write("( drill.ngc )\n")
                f.write(patch_gcode("output/drill.ngc","drill", 2))
                f.write("\n\n( drill.ngc end )\n\n")

            if os.path.exists("output/front.ngc"):
                f.write("( front.ngc )\n")
                f.write(patch_gcode("output/front.ngc"))
                f.write("\n\n( front.ngc end )\n\n")

            if os.path.exists("output/outline.ngc"):
                f.write("( outline.ngc )\n")
                f.write(patch_gcode("output/outline.ngc", None, 1))
                f.write("\n\n( outline.ngc end )\n\n")

            if os.path.exists("output/back.ngc"):
                f.write("( back.ngc )\n")
                f.write(patch_gcode("output/back.ngc"))
                f.write("\n\n( back.ngc end )\n\n")
        else:
            # single sided workflow
            if os.path.exists("output/front.ngc"):
                f.write("( front.ngc )\n")
                f.write(patch_gcode("output/front.ngc"))
                f.write("\n\n( front.ngc end )\n\n")

            if os.path.exists("output/outline.ngc"):
                f.write("( outline.ngc )\n")
                f.write(patch_gcode("output/outline.ngc", None, 1))
                f.write("\n\n( outline.ngc end )\n\n")

            if os.path.exists("output/drill.ngc"):
                f.write("( drill.ngc )\n")
                f.write(patch_gcode("output/drill.ngc","drill", 2))
                f.write("\n\n( drill.ngc end )\n\n")
            if os.path.exists("output/milldrill.ngc"):
                f.write("( milldrill.ngc )\n")
                f.write(patch_gcode("output/milldrill.ngc", None, 1))
                f.write("\n\n( milldrill.ngc end )\n\n")

# Find all toolchange gcodes (T0-T8) in the given gcode string and add
# `offset` to each tool number (e.g. T1 -> T3 for offset=2).
def apply_toolchange_offset(content, offset):
    def replace(match):
        tool = int(match.group(1))
        return f"M6 T{tool + offset}"

    # \bT matches the tool word; [0-8] limits it to T0-T8; the trailing
    # boundary keeps us from touching things like T12 or coordinates.
    return re.sub(r'\bT([0-8])\b', replace, content)

# make pcb2gcode output compatible with carvera
def patch_gcode(path, type=None, tools_offset=0):
    with open(path, 'r') as f:
        content = f.read()

    if(type == "drill"):
        spindle_marker = 'M5      (Spindle off.)'
        spindle_idx = content.rfind(spindle_marker)
        if spindle_idx != -1:
            before_postamble = content[:spindle_idx]
            content = before_postamble + POSTAMBLE.strip() + '\n'
    else:
        # Replace preamble: everything from start up to and including first '( retract )'
        retract_marker = '( retract )'
        retract_idx = content.find(retract_marker)
        if retract_idx != -1:
            after_preamble = content[retract_idx + len(retract_marker):]
            content = PREAMBLE.strip() + '\n' + after_preamble

        # Replace postamble: everything from last 'M5 ( Spindle off. )' to end
        spindle_marker = 'M5 ( Spindle off. )'
        spindle_idx = content.rfind(spindle_marker)
        if spindle_idx != -1:
            before_postamble = content[:spindle_idx]
            content = before_postamble + POSTAMBLE.strip() + '\n'

        content = "T1\n" + content


    content = apply_toolchange_offset(content,tools_offset)

    with open(f"{path}_patched.nc", 'w') as f:
        f.write(content)

    return content