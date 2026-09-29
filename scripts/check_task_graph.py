"""Check that the JSON dependency graph in tasks.md matches the Depends on lines."""
import json
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")

# Tasks and their Depends on lines, in document order.
tasks = {}
current = None
for line in text.splitlines():
    m = re.match(r"- \[[ x]\] \*\*(\d+\.\d+) ", line)
    if m:
        current = m.group(1)
        continue
    m = re.match(r"\s+- \*\*Depends on:\*\* (.+?) ·", line)
    if m and current:
        tasks[current] = m.group(1)
        current = None

wave_of = lambda t: int(t.split(".")[0])
by_wave = {}
for t in tasks:
    by_wave.setdefault(wave_of(t), []).append(t)

def expand(dep: str):
    if dep == "nothing":
        return []
    out = []
    for part in dep.split(", "):
        m = re.fullmatch(r"Wave (\d+)", part)
        out += by_wave[int(m.group(1))] if m else [part]
    return out

md_deps = {t: expand(d) for t, d in tasks.items()}

graph = json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))
problems = []
if graph["depends_on"] != md_deps:
    for t in sorted(set(md_deps) | set(graph["depends_on"]), key=lambda s: tuple(map(int, s.split(".")))):
        if md_deps.get(t) != graph["depends_on"].get(t):
            problems.append(f"{t}: markdown={md_deps.get(t)} json={graph['depends_on'].get(t)}")
for w in graph["waves"]:
    if sorted(w["tasks"]) != sorted(by_wave.get(w["wave"], [])):
        problems.append(f"wave {w['wave']} task list differs: {w['tasks']} vs {by_wave.get(w['wave'])}")
for t, deps in md_deps.items():
    for d in deps:
        if wave_of(d) >= wave_of(t):
            problems.append(f"{t} depends on {d}, which is not in an earlier wave")

print(f"{len(tasks)} tasks, {len(graph['waves'])} waves")
print("\n".join(problems) if problems else "OK: JSON graph matches the Depends on lines; no same-wave dependencies")
sys.exit(1 if problems else 0)
