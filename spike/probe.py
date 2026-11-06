"""THROWAWAY: probe the 3 open questions. Usage: probe.py q1|q2|q3"""
import json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
q = sys.argv[1]

def worktree(tag):
    name = f"probe-{tag}-{int(time.time())}"
    wt = REPO / ".worktrees" / name
    subprocess.run(["git", "worktree", "add", "-q", "-b", name, str(wt)], cwd=REPO, check=True)
    return name, wt

def run(tag, prompt, *flags):
    name, wt = worktree(tag)
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", "sonnet", *flags]
    t = time.time()
    p = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    (REPO / "spike" / f"{name}.stdout.json").write_text(p.stdout)
    print(f"[{tag}] exit={p.returncode} {time.time()-t:.1f}s wt={wt}")
    try:
        d = json.loads(p.stdout)
        keep = {k: d.get(k) for k in ("subtype", "is_error", "terminal_reason", "stop_reason",
                                      "num_turns", "total_cost_usd", "api_error_status")}
        print(json.dumps(keep)); print("denials:", [x["tool_name"] + ":" + str(x["tool_input"])[:90] for x in d.get("permission_denials", [])])
        print("result:", str(d.get("result"))[:600])
    except Exception as e:
        print("unparsed", e, p.stdout[:800], p.stderr[:500])
    return wt

ASK = ("Without using tools: does your context include any user-level or project instructions "
       "(CLAUDE.md) about TDD or commit signing? Quote one line verbatim if so, else answer NONE.")
if q == "q1":
    run("q1-default", ASK, "--tools", "")
    run("q1-project", ASK, "--tools", "", "--setting-sources", "project")
elif q == "q2":
    run("q2-budget", "Read every file under target/ one at a time with the Read tool, then summarise each.",
        "--max-budget-usd", "0.02", "--allowedTools", "Read Bash(ls*)")
elif q == "q3":
    wt = run("q3-git", "Create a file hello.txt containing 'hi' in the repo root, then `git add hello.txt` and "
        "`git commit -m 'Add hello'` (unsigned: use -c commit.gpgsign=false). Report git log --oneline.",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Read Write Edit Bash(git add *) Bash(git commit *) Bash(git -c commit.gpgsign=false commit *) Bash(git log *) Bash(git status*)",
        "--setting-sources", "project")
    print(subprocess.run(["git", "log", "--oneline", "-3"], cwd=wt, capture_output=True, text=True).stdout)
if q == "q1b":
    run("q1b-empty-sources", ASK, "--tools", "", "--setting-sources", "")
    run("q1b-sysprompt", ASK, "--tools", "", "--system-prompt", "You are a terse assistant.")
    import os
    os.environ["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    run("q1b-env-disable", ASK, "--tools", "")
