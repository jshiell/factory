"""THROWAWAY (plan 2 cycle 0). Usage: uv run --project orchestrator python spike/probe3.py <probe>
Probes: appendprompt, maxturns, noprompts, pgroup, apikey. Saves stdout, stderr, exit code."""
import json, os, subprocess, sys, time
from pathlib import Path
from factory.command import RunnerConfig, build_command

REPO = Path(__file__).resolve().parent.parent

def worktree(tag):
    name = f"p3-{tag}-{int(time.time())}"
    wt = REPO / ".worktrees" / name
    subprocess.run(["git", "worktree", "add", "-q", "-b", name, str(wt)], cwd=REPO, check=True)
    return name, wt

def execute(tag, argv, env_overlay, extra_env=None):
    name, wt = worktree(tag)
    env = {**os.environ, **env_overlay, **(extra_env or {})}
    t = time.time()
    p = subprocess.Popen(argv, cwd=wt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    print(f"[{tag}] claude pid/pgid = {p.pid}/{os.getpgid(p.pid)}")
    out, err = p.communicate(timeout=180)
    base = REPO / "spike" / name
    base.with_suffix(".stdout.json").write_text(out)
    base.with_suffix(".stderr.txt").write_text(err)
    base.with_suffix(".exit.txt").write_text(str(p.returncode))
    print(f"[{tag}] exit={p.returncode} {time.time()-t:.1f}s stderr={err[:300]!r}")
    try:
        d = json.loads(out)
        print({k: d.get(k) for k in ("subtype", "is_error", "terminal_reason", "num_turns", "total_cost_usd", "api_error_status")})
        print("denials:", [x["tool_name"] + ":" + str(x["tool_input"])[:80] for x in d.get("permission_denials", [])])
        print("errors:", d.get("errors")); print("result:", str(d.get("result"))[:500])
    except Exception as e:
        print("unparsed:", e, out[:400])

def cfg(**kw): return RunnerConfig(model="sonnet", budget_usd=0.3, **kw)
probe = sys.argv[1]
if probe == "appendprompt":
    argv, env = build_command("Without tools: quote verbatim any line you were given containing PROJECT-MARKER, else NONE.", cfg())
    argv += ["--append-system-prompt", "PROJECT-MARKER-9: always answer tersely.", "--tools", ""]
    execute(probe, argv, env)
elif probe == "maxturns":
    argv, env = build_command("Run `ls`, then `ls target`, then `ls docs` as three separate Bash calls and summarise.",
                              cfg(permission_mode="acceptEdits", allowed_tools=("Bash(ls*)",)))
    argv += ["--max-turns", "1"]
    execute(probe, argv, env)
elif probe == "noprompts":
    argv, env = build_command("Run `rm -rf /tmp/nonexistent-probe-dir` with Bash and report what happened.", cfg())
    argv += ["--permission-prompts", "none"]
    execute(probe, argv, env)
elif probe == "pgroup":
    argv, env = build_command("Run exactly this Bash command once and report its output verbatim: python3 -c 'import os; print(os.getpid(), os.getppid(), os.getpgrp())'",
                              cfg(permission_mode="acceptEdits", allowed_tools=("Bash(python3 *)",)))
    execute(probe, argv, env)
elif probe == "apikey":
    argv, env = build_command("say hi", cfg())
    execute(probe, argv, env, {"ANTHROPIC_API_KEY": "sk-ant-bogus-probe-key"})
