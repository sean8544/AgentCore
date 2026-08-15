"""Kill the vite dev server PID started in this session."""
import os
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kill_result.txt")
lines: list[str] = []
for pid in [8300]:
    try:
        os.kill(pid, 15)
        lines.append(f"pid {pid}: kill sent")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"pid {pid}: FAILED {exc!r}")
time.sleep(2)
try:
    out = os.popen('netstat -ano').read()
except Exception:  # noqa: BLE001
    out = ""
remaining = [
    line for line in out.splitlines()
    if "LISTENING" in line and ":3001" in line
]
lines.append("REMAINING: " + ("; ".join(remaining) if remaining else "NONE"))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
