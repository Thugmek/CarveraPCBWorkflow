import subprocess
import shutil
import os

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
G28
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

    cmd = ['pcb2gcode', '--config', 'config.cfg']
    if gerber_front:
        cmd += ['--front', gerber_front]
    if gerber_back:
        cmd += ['--back', gerber_back]
    if gerber_outline:
        cmd += ['--outline', gerber_outline]
    if drill:
        cmd += ['--drill', drill]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (result, error) = proc.communicate()
    rc = proc.wait()
    print(f"Return code: {rc}")
    print(f"result: {result}")
    print(f"error: {error}")

    with open("merged.nc", "w") as f:
        if gerber_front and gerber_back:
            # 2-sided workflow
            pass
        else:
            # single sided workflow
            if os.path.exists("output/front.ngc"):
                f.write("( front.ngc )\n")
                f.write(patch_gcode("output/front.ngc"))
                f.write("\n\n( front.ngc end )\n\n")

            if os.path.exists("output/outline.ngc"):
                f.write("( outline.ngc )\n")
                f.write(patch_gcode("output/outline.ngc"))
                f.write("\n\n( outline.ngc end )\n\n")


# make pcb2gcode output compatible with carvera
def patch_gcode(path, type=None):
    with open(path, 'r') as f:
        content = f.read()

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

    with open(f"{path}_patched.nc", 'w') as f:
        f.write(content)

    return content