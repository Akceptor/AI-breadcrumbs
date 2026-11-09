#!/usr/bin/env python3
"""Merge hooks/hooks.json into a tool's hooks config, resolving ${PLUGIN_ROOT}. Idempotent.

usage: merge_hooks.py <hooks.json> <target json> <plugin root>   (target is settings.json or hooks.json)
Prints "changed" or "unchanged".
"""
import json, os, shutil, sys

src, path, plugin_root = sys.argv[1:4]
wanted = json.load(open(src))["hooks"]
d = json.load(open(path)) if os.path.exists(path) else {}
hooks = d.setdefault("hooks", {})
changed = False
for event, groups in wanted.items():
    have = hooks.setdefault(event, [])
    for g in groups:
        g = json.loads(json.dumps(g).replace("${PLUGIN_ROOT}", plugin_root))
        cmds = {h["command"] for h in g["hooks"]}
        if any(h.get("command") in cmds for x in have for h in x.get("hooks", [])):
            continue
        have.append(g)
        changed = True
if changed:
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
print("changed" if changed else "unchanged")
