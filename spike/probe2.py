"""THROWAWAY (plan 2 spike). Usage: uv run --project orchestrator python spike/probe2.py <probe>
Probes: iso (isolation+tools+project CLAUDE.md), repeat (repeated --allowedTools),
dashdash (-- before prompt), badmodel (error subtype). Saves stdout, stderr, exit code."""
import json, os, subprocess, sys, time
from pathlib import Path
from factory.command import RunnerConfig, build_command

REPO = Path(__file__).resolve().parent.parent
TOOLS = ("Read", "Write", "Edit", "Bash(git add *)", "Bash(git commit *)", "Bash(git status*)", "Bash(git log *)")
ASK = ("1) Without tools: quote verbatim any line from a project CLAUDE.md containing PROJECT-MARKER, else NONE. "
       "2) Quote any line from user-level instructions about TDD or commit signing, else NONE. "
       "3) Create hello.txt containing 'hi', `git add hello.txt`, `git commit -m 'Add hello'`. Report both answers and git log --oneline.")

def worktree(tag, with_claude_md=True):
    name = f"p2-{tag}-{int(time.time())}"
    wt = REPO / ".worktrees" / name
    subprocess.run(["git", "worktree", "add", "-q", "-b", name, str(wt)], cwd=REPO, check=True)
    if with_claude_md:
        (wt / "CLAUDE.md").write_text("PROJECT-MARKER-7421: always be terse.\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=wt, check=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Add CLAUDE.md"], cwd=wt, check=True)
    return name, wt

def execute(tag, argv, env_overlay):
    name, wt = worktree(tag)
    t = time.time()
    p = subprocess.run(argv, cwd=wt, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, **env_overlay})
    out = REPO / "spike" / name
    out.with_suffix(".stdout.json").write_text(p.stdout)
    out.with_suffix(".stderr.txt").write_text(p.stderr)
    out.with_suffix(".exit.txt").write_text(str(p.returncode))
    print(f"[{tag}] exit={p.returncode} {time.time()-t:.1f}s")
    print("stderr:", p.stderr[:300])
    try:
        d = json.loads(p.stdout)
        print({k: d.get(k) for k in ("subtype", "is_error", "terminal_reason", "num_turns", "total_cost_usd", "api_error_status")})
        print("denials:", [x["tool_name"] + ":" + str(x["tool_input"])[:80] for x in d.get("permission_denials", [])])
        print("result:", str(d.get("result"))[:700])
    except Exception as e:
        print("unparsed:", e, p.stdout[:400])
    print("git log:", subprocess.run(["git", "log", "--oneline", "-3"], cwd=wt, capture_output=True, text=True).stdout)

cfg = RunnerConfig(model="sonnet", budget_usd=0.5, permission_mode="acceptEdits", allowed_tools=TOOLS)
probe = sys.argv[1]
if probe == "iso":
    argv, env = build_command(ASK, cfg); execute("iso", argv, env)
elif probe == "repeat":
    argv, env = build_command(ASK, cfg)
    i = argv.index("--allowedTools"); joined = argv[i + 1]
    argv[i:i + 2] = [x for t in TOOLS for x in ("--allowedTools", t)]
    execute("repeat", argv, env)
elif probe == "dashdash":
    argv, env = build_command(ASK, cfg)
    i = argv.index("-p"); prompt = argv.pop(i + 1); argv.pop(i)
    argv += ["-p", "--", prompt]
    execute("dashdash", argv, env)
elif probe == "badmodel":
    argv, env = build_command("say hi", RunnerConfig(model="no-such-model", budget_usd=0.1)); execute("badmodel", argv, env)
