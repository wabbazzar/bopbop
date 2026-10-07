#!/usr/bin/env python3
"""Small, isolated Ollama agent-loop comparison for BopBop."""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
RUNS = ROOT / "runs"
MODEL = "gpt-oss:20b"
HOST = "http://127.0.0.1:11434/api/chat"
MAX_STEPS = 7

TASKS = {
    "find": "Find the launch code in this workspace's notes. Reply with just the code. Read files to check; do not guess.",
    "fix": "Fix `slug.py` so `slug(' Hi, BopBop! ')` returns `hi-bopbop`. Run the included test before you finish.",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
}

def function(name: str, description: str, properties: dict, required: list[str]):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}

NARROW_TOOLS = [
    function("list_files", "List workspace files", {}, []),
    function("read_file", "Read a workspace file", {"path": SCHEMA["properties"]["path"]}, ["path"]),
    function("write_file", "Replace a workspace file with exact content", SCHEMA["properties"], ["path", "content"]),
    function("run_test", "Run the workspace test and get its output", {}, []),
]
GENERIC_TOOLS = [function("workspace", "Operate on workspace files. action is list, read, write, or test",
    {"action": {"type": "string", "enum": ["list", "read", "write", "test"]}, **SCHEMA["properties"]}, ["action"])]

SYSTEM = ("You are a local assistant in a small test workspace. Use the available actions "
          "to inspect files. For a fix, edit the file and run the test. Give a short final answer. "
          "Never claim a tool action succeeded unless its result says so.")
JSON_SYSTEM = SYSTEM + (" Respond with ONE JSON object per turn, no markdown: "
    "{\"action\":\"list|read|write|test|final\",\"path\":\"...\",\"content\":\"...\"}. "
    "For final, put your answer in content. After each action you will receive its result.")

def make_fixture(variant: str, task: str) -> pathlib.Path:
    work = RUNS / variant / task
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "notes.txt").write_text("Meeting notes: the launch code is ORBIT-731. Do not share the old code ORBIT-719.\n")
    (work / "slug.py").write_text("import re\n\ndef slug(text):\n    return re.sub(r'[^a-z0-9]+', '-', text.lower())\n")
    (work / "test_slug.py").write_text("from slug import slug\nassert slug(' Hi, BopBop! ') == 'hi-bopbop'\nprint('PASS')\n")
    return work

def workspace_action(work: pathlib.Path, action: str, path: str = "", content: str = "") -> str:
    if action == "list":
        return "\n".join(sorted(p.name for p in work.iterdir() if p.is_file()))
    if action == "test":
        proc = subprocess.run(["python3", "test_slug.py"], cwd=work, capture_output=True,
                              text=True, timeout=10)
        return f"exit={proc.returncode}\n{(proc.stdout + proc.stderr)[-2000:]}"
    target = (work / path).resolve()
    if target.parent != work.resolve() or path not in {"notes.txt", "slug.py", "test_slug.py"}:
        return "ERROR: path outside allowed workspace files"
    if action == "read":
        return target.read_text()[:6000]
    if action == "write":
        if path != "slug.py":
            return "ERROR: only slug.py may be edited"
        target.write_text(content)
        return "written"
    return "ERROR: unknown action"

def ollama(messages: list[dict], tools: list[dict] | None, json_mode: bool) -> tuple[dict, dict]:
    body = {"model": MODEL, "messages": messages, "stream": False, "think": False,
            "options": {"num_ctx": 8192, "temperature": 0}, "keep_alive": "10m"}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["format"] = "json"
    req = urllib.request.Request(HOST, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as response:
        payload = json.load(response)
    return payload.get("message", {}), payload

def normalize(name: str, args: dict) -> tuple[str, str, str]:
    if name == "workspace":
        return str(args.get("action", "")), str(args.get("path", "")), str(args.get("content", ""))
    return {"list_files": "list", "read_file": "read", "write_file": "write",
            "run_test": "test"}.get(name, "invalid"), str(args.get("path", "")), str(args.get("content", ""))

def trial(variant: str, task: str) -> dict:
    work = make_fixture(variant, task)
    json_mode = variant == "json_actions"
    tools = None if json_mode else NARROW_TOOLS if variant == "narrow_tools" else GENERIC_TOOLS
    messages = [{"role": "system", "content": JSON_SYSTEM if json_mode else SYSTEM},
                {"role": "user", "content": TASKS[task]}]
    trace = []
    started = time.monotonic()
    final = ""
    for step in range(MAX_STEPS):
        try:
            message, payload = ollama(messages, tools, json_mode)
        except Exception as exc:
            trace.append({"step": step, "error": str(exc)})
            break
        trace.append({"step": step, "response": message,
                      "prompt_tokens": payload.get("prompt_eval_count"),
                      "output_tokens": payload.get("eval_count")})
        if json_mode:
            try:
                obj = json.loads(message.get("content", ""))
                action, path, content = normalize("workspace", obj)
            except (json.JSONDecodeError, TypeError) as exc:
                trace.append({"error": f"invalid JSON: {exc}"})
                break
            if action == "final":
                final = content
                break
            result = workspace_action(work, action, path, content)
            messages.extend([message, {"role": "user", "content": f"Action result: {result}"}])
            trace.append({"action": action, "path": path, "result": result})
            continue
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            final = message.get("content", "")
            break
        for call in calls:
            fn = call.get("function") or {}
            action, path, content = normalize(fn.get("name", ""), fn.get("arguments") or {})
            result = workspace_action(work, action, path, content)
            messages.append({"role": "tool", "tool_name": fn.get("name", ""), "content": result})
            trace.append({"action": action, "path": path, "result": result})
    elapsed = round(time.monotonic() - started, 1)
    if task == "find":
        passed = "ORBIT-731" in final and "ORBIT-719" not in final
    else:
        test = workspace_action(work, "test")
        passed = (test.startswith("exit=0") and bool(final.strip())
                  and any(x.get("action") == "test" for x in trace))
    return {"variant": variant, "task": task, "passed": passed, "seconds": elapsed,
            "steps": sum("response" in x for x in trace), "final": final, "trace": trace}

def main():
    results = []
    for task in TASKS:
        for variant in ("narrow_tools", "generic_tool", "json_actions"):
            print(f"Running {task}/{variant}...", flush=True)
            result = trial(variant, task)
            results.append(result)
            (ROOT / "results.json").write_text(json.dumps(results, indent=2))
            print(f"  passed={result['passed']} time={result['seconds']}s steps={result['steps']}", flush=True)

if __name__ == "__main__":
    main()
