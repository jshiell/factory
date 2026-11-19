"""THROWAWAY (plan O7 gap 2). $0, no API calls. Needs real `nono`, `git`, `uv`. Run from the repo root in YOUR terminal
(not inside an agent nono session):
    uv run --project orchestrator python spike/git_hardening_probe.py

Question: can an agent steer the FACTORY's own (unconfined) git into running code? Git config keys that run commands
(filter.*.clean/smudge, diff.*.textconv, core.fsmonitor ...) only matter if git reads them from somewhere the agent can
write, or if an agent-written `.gitattributes` selects a driver that the user's global/system git config defines.

Steps (each prints a line; SUMMARY JSON at the end):
 1  Inventory (read-only, your real environment): the filter/diff/merge drivers and includes in your effective git config,
    and whether git-lfs is installed. Names and origins only.
 2  Writability under the real agent prefix (`SubprocessRunner.run`, confine=True, a fake executable) and the real gate
    prefix (`build_gate_sandbox`): `~/.config/git/` (a sibling file, never `config`), `.git/worktrees/<id>/`, `.git/info/`,
    `.git/hooks/`, any `include` target directory. Positive control: a write in the project dir. Ground truth is checked on
    the host.
 3  Exec-harness controls (host-written, no agent): FIXTURE drivers (filter clean/smudge, diff textconv; each appends to its
    own canary file) live in a fixture file passed as GIT_CONFIG_GLOBAL to the factory calls only (never your real
    ~/.gitconfig). Negative control: a matching new file with no attributes: no canary. Positive controls: attributes + a
    new matching file through the real `commit_all` (clean fires), plain `diff` (textconv fires), a checkout (smudge fires).
    If a positive control fails, the verdicts for that driver are void.
 4  Attributes route: the agent prefix writes `proj/.gitattributes` and a matching file; then the factory's real calls run
    unconfined and each call's canaries are recorded: commit_all, status after a same-size edit, diff --numstat / --stat /
    --name-status, `git show HEAD:path`, and `merge --ff-only` into the fixture main checkout.
 5  Symlinked `.gitattributes` (does the installed git refuse it?) and a symlink to a file outside (stored as a link, not
    its content?).
 6  Nested repo: the agent prefix writes `proj/sub/.git/{HEAD,config,...}` with fsmonitor + filter in its own config; the
    factory's `status`/`add -A` run from the worktree root; controls run the same in `proj/sub`, where that config is live.
Verdicts come from canary files, never exit codes. git-lfs smudge fetch is NOT probed.

Touches: a fixture under $HOME (`gitprobe-<pid>/`), a canary dir in $TMPDIR, and (only if those directories exist) one
sibling file `gitprobe-<pid>` in ~/.config/git and in each include-target directory. All removed at the end. Never touches
~/.gitconfig, ~/.config/git/config, ~/.claude.json or a real repo.
Paste the printed SUMMARY back."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))
from factory.command import RunnerConfig  # noqa: E402
from factory.gate import commit_all  # noqa: E402
from factory.gate_sandbox import build_gate_sandbox, discover_uv_tooling  # noqa: E402
from factory.runner import SubprocessRunner  # noqa: E402
from factory.sandbox import NonoSandbox  # noqa: E402
from factory.task import Task  # noqa: E402
from factory.worktrees import HARDENED_GIT, run_git  # noqa: E402

HOME = Path.home()
PID = os.getpid()
BASE = HOME / f"gitprobe-{PID}"
PYTHON = "/usr/bin/python3"
SUBDIR = "proj"
TIMEOUT = 120
IMPLEMENT = RunnerConfig(model="sonnet", confine=True, permission_mode="acceptEdits")

WRITER = r'''
import errno, json, os, sys
out = {}
for op in json.load(open(sys.argv[1])):
    try:
        if op.get("mk"):
            os.makedirs(os.path.dirname(op["path"]), exist_ok=True)
        if op["kind"] == "symlink":
            os.symlink(op["content"], op["path"])
        elif op["kind"] == "dir":
            os.makedirs(op["path"], exist_ok=True)
        else:
            with open(op["path"], "w") as f:
                f.write(op["content"])
        out[op["label"]] = "WROTE"
    except OSError as e:
        out[op["label"]] = errno.errorcode.get(e.errno, str(e.errno))
print(json.dumps(out))
'''


def sh(*argv, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=TIMEOUT)


def git(*args, cwd):
    return sh("git", "-c", "user.name=f", "-c", "user.email=f@f", "-c", "commit.gpgsign=false", *args, cwd=cwd)


def version(*argv):
    try:
        p = sh(*argv)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


def parse(stdout):
    lines = [line for line in stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1]) if lines else None


def main():
    assert " " not in str(BASE), "fixture path must not contain spaces"
    BASE.mkdir()
    outside = Path(tempfile.mkdtemp(prefix="gitprobe-outside-"))
    cleanup_files = []
    summary = {}
    try:
        run(outside, cleanup_files, summary)
    finally:
        for path in cleanup_files:
            path.unlink(missing_ok=True)
        shutil.rmtree(BASE, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
    print("\n================ SUMMARY (plan O7 git hardening probe) ================")
    print(json.dumps(summary, indent=1, default=str))


def run(outside, cleanup_files, summary):
    # ---- versions and inventory (your real environment) ------------------------------------------------------
    summary["versions"] = {"git": version("git", "--version"), "nono": version("nono", "--version"),
                           "kernel": version("uname", "-r"), "git-lfs": version("git", "lfs", "version")}
    listed = sh("git", "config", "--show-origin", "--includes", "--get-regexp",
                r"^(filter|diff|merge)\.|^core\.attributesfile|^include").stdout.strip().splitlines()
    summary["inventory (names and origins only; values dropped)"] = [" ".join(line.split()[:2]) for line in listed]
    print(f"1 inventory: {len(listed)} matching config entries, git-lfs: {summary['versions']['git-lfs']}", flush=True)
    include_dirs = set()
    for line in sh("git", "config", "--includes", "--get-regexp", r"^include").stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            target = Path(os.path.expanduser(parts[1].strip())).parent
            if target.is_dir() and target.is_relative_to(HOME):
                include_dirs.add(target)

    # ---- fixture ---------------------------------------------------------------------------------------------
    repo = (BASE / "repo").resolve()
    state = BASE / "state"
    exedir = BASE / "exe"
    drivers = BASE / "drivers"
    can = outside / "canaries"
    for d in (repo, state, exedir, drivers, can):
        d.mkdir(exist_ok=True)
    (repo / SUBDIR).mkdir()
    (repo / SUBDIR / "code.py").write_text("x = 1\n")
    git("init", "-q", "-b", "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    base_sha = git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    def worktree(name):
        path = repo / ".worktrees" / name
        git("worktree", "add", "-q", "-b", f"factory/{name}", str(path), cwd=repo)
        return path.resolve()

    def script(name, body):
        path = drivers / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return path

    clean = script("clean.sh", f'echo "$*" >> {can}/filter-clean\ncat')
    smudge = script("smudge.sh", f'echo "$*" >> {can}/filter-smudge\ncat')
    textconv = script("textconv.sh", f'echo "$*" >> {can}/textconv\ncat "$1"')
    fsmon = script("fsmon.sh", f'echo "$*" >> {can}/nested-fsmonitor')
    nested_clean = script("nested-clean.sh", f'echo "$*" >> {can}/nested-filter-clean\ncat')
    fixture_cfg = BASE / "fixture-gitconfig"
    fixture_cfg.write_text(f'[filter "probe"]\n\tclean = {clean} %f\n\tsmudge = {smudge} %f\n'
                           f'[diff "probe"]\n\ttextconv = {textconv}\n')

    @contextlib.contextmanager
    def fixture_git_config():
        old = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(fixture_cfg)
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = old

    def reset():
        for p in can.glob("*"):
            p.unlink()

    def call(label, fn):
        reset()
        try:
            fn()
            status = "ok"
        except Exception as e:
            status = f"error {type(e).__name__}: {str(e)[:100]} stderr={str(getattr(e, 'stderr', ''))[:300]!r}"
        fired = sorted(p.name for p in can.glob("*"))
        print(f"   {label:58} canaries: {fired or 'none'}", flush=True)
        return {"call": label, "status": status, "canaries": fired}

    (exedir / "writer.py").write_text(WRITER)
    fake = exedir / "fake-claude"
    fake.write_text(f"#!/bin/sh\nexec {PYTHON} {exedir / 'writer.py'} {exedir / 'spec.json'}\n")
    fake.chmod(0o755)

    def run_agent(wt, ops):
        (exedir / "spec.json").write_text(json.dumps(ops))
        runner = SubprocessRunner(str(fake), SUBDIR, TIMEOUT, private_dirs=(str(state),))
        result = runner.run(Task(id="probe", prompt="probe", worktree=str(wt)), IMPLEMENT)
        return {"exit": result.exit_code, "report": parse(result.stdout), "stderr": result.stderr[-200:].strip()}

    def op(label, path, content="x\n", kind="file", mk=False):
        return {"label": label, "path": str(path), "content": content, "kind": kind, "mk": mk}

    # ---- 2 writability ---------------------------------------------------------------------------------------
    wa = worktree("a")
    gitdir = Path(git("rev-parse", "--absolute-git-dir", cwd=wa).stdout.strip())
    targets = [("project dir (positive control)", wa / SUBDIR / "gitprobe-w.txt"),
               (".git/worktrees/<id>/", gitdir / f"gitprobe-{PID}"),
               (".git/info/", repo / ".git" / "info" / f"gitprobe-{PID}"),
               (".git/hooks/", repo / ".git" / "hooks" / f"gitprobe-{PID}")]
    xdg = HOME / ".config" / "git"
    targets.append(("~/.config/git/ (sibling file)" if xdg.is_dir() else "~/.config/ (git/ absent; sibling file)",
                    (xdg if xdg.is_dir() else HOME / ".config") / f"gitprobe-{PID}"))
    for d in sorted(include_dirs):
        targets.append((f"include target dir {d}", d / f"gitprobe-{PID}"))
    ops = [op(label, path) for label, path in targets]
    cleanup_files += [path for _, path in targets]
    print("2 writability", flush=True)
    summary["2 writability"] = {}
    agent = run_agent(wa, ops)
    truth = {label: path.exists() for label, path in targets}
    for _, path in targets:
        path.unlink(missing_ok=True)
    summary["2 writability"]["agent prefix"] = {"run": agent, "host saw the file": truth}
    tooling = discover_uv_tooling(wa / SUBDIR)
    cache_dir = BASE / "gate-cache"
    cache_dir.mkdir()
    gate = build_gate_sandbox(sandbox=NonoSandbox("nono"), worktree=wa, project_dir=wa / SUBDIR, cache_dir=cache_dir,
                              python_prefix=tooling.python_prefix, uv_python_dir=tooling.uv_python_dir, real_uv=tooling.real_uv,
                              base_env=dict(os.environ), test_argv=("uv", "run", "pytest", "-q"))
    (wa / SUBDIR / "gate-writer.py").write_text(WRITER)
    (wa / SUBDIR / "gate-spec.json").write_text(json.dumps(ops))
    p = sh(*gate.prefix, PYTHON, "gate-writer.py", "gate-spec.json", cwd=wa / SUBDIR, env=gate.env)
    gate_truth = {label: path.exists() for label, path in targets}
    for _, path in targets:
        path.unlink(missing_ok=True)
    (wa / SUBDIR / "gate-writer.py").unlink()
    (wa / SUBDIR / "gate-spec.json").unlink()
    summary["2 writability"]["gate prefix"] = {"exit": p.returncode, "report": parse(p.stdout), "stderr": p.stderr[-200:].strip(),
                                               "host saw the file": gate_truth}
    for label, _ in targets:
        print(f"   {label:58} agent: {(agent['report'] or {}).get(label)!s:8} (host saw: {truth[label]})   "
              f"gate: {(parse(p.stdout) or {}).get(label)!s:8} (host saw: {gate_truth[label]})", flush=True)

    # ---- 3 exec-harness controls (host-written; RAW git with the fixture drivers: the factory's git now ignores them) ----------------------
    print("3 harness controls (fixture drivers via GIT_CONFIG_GLOBAL)", flush=True)
    controls = []
    with fixture_git_config():
        (wa / SUBDIR / "neg.probe").write_text("negative\n")
        controls.append(call("NEG: new *.probe file, no attributes: raw git add", lambda: (git("add", "-A", cwd=wa), git("commit", "-m", "neg", cwd=wa))))
        (wa / SUBDIR / ".gitattributes").write_text("*.probe filter=probe diff=probe\n")
        (wa / SUBDIR / "pos.probe").write_text("positive\n")
        controls.append(call("POS: attributes + new file: raw git add (clean)", lambda: (git("add", "-A", cwd=wa), git("commit", "-m", "pos", cwd=wa))))
        controls.append(call("POS: plain diff base..HEAD (textconv)", lambda: git("diff", base_sha, "HEAD", "--", SUBDIR, cwd=wa)))
        (wa / SUBDIR / "pos.probe").unlink()
        controls.append(call("POS: checkout of pos.probe (smudge)", lambda: git("checkout", "--", f"{SUBDIR}/pos.probe", cwd=wa)))
    summary["3 controls"] = controls
    neg_ok = not controls[0]["canaries"]
    clean_ok = "filter-clean" in controls[1]["canaries"]
    textconv_ok = "textconv" in controls[2]["canaries"]
    smudge_ok = "filter-smudge" in controls[3]["canaries"]
    summary["3 harness valid"] = {"negative (no canary)": neg_ok, "clean": clean_ok, "textconv": textconv_ok, "smudge": smudge_ok}
    print(f"   harness valid: negative={neg_ok} clean={clean_ok} textconv={textconv_ok} smudge={smudge_ok}", flush=True)
    if not (neg_ok and clean_ok):
        print("   HARNESS BROKEN (negative or clean control): steps 4-6 verdicts are void; stopping.", flush=True)
        return

    # ---- 4 attributes route ----------------------------------------------------------------------------------
    print("4 attributes route (agent writes .gitattributes + file; factory calls run unconfined)", flush=True)
    w1 = worktree("t1")
    agent = run_agent(w1, [op("gitattributes", w1 / SUBDIR / ".gitattributes", "*.probe filter=probe diff=probe\n"),
                           op("agent.probe", w1 / SUBDIR / "agent.probe", "hello\n")])
    wrote = (w1 / SUBDIR / ".gitattributes").is_file() and (w1 / SUBDIR / "agent.probe").is_file()
    print(f"   agent wrote both files (host check): {wrote}; report {agent['report']}", flush=True)
    calls = []
    with fixture_git_config():
        calls.append(call("commit_all (git add -A)", lambda: commit_all(w1, "agent")))
        (w1 / SUBDIR / "agent.probe").write_text("jello\n")
        calls.append(call("status --porcelain after same-size edit", lambda: run_git(w1, "status", "--porcelain")))
        calls.append(call("commit_all again", lambda: commit_all(w1, "agent2")))
        calls.append(call("diff --numstat base..HEAD", lambda: run_git(w1, "diff", "--numstat", "--no-renames", base_sha, "HEAD", "--", SUBDIR)))
        calls.append(call("diff --stat=200 base..HEAD", lambda: run_git(w1, "diff", "--stat=200", base_sha, "HEAD")))
        calls.append(call("diff --name-status base..HEAD", lambda: run_git(w1, "diff", "--name-status", "--no-renames", base_sha, "HEAD", "--", SUBDIR)))
        calls.append(call("git show HEAD:proj/agent.probe (HARDENED_GIT)",
                          lambda: subprocess.run([*HARDENED_GIT, "show", f"HEAD:{SUBDIR}/agent.probe"], cwd=w1, capture_output=True, stdin=subprocess.DEVNULL)))
        # run_git forces GIT_CONFIG_GLOBAL=/dev/null, so the fixture drivers never reach the factory calls above: their
        # "none" holds by construction. This control uses raw git with the fixture config on the same agent-written files.
        calls.append(call("CONTROL raw git diff base..HEAD (textconv expected)", lambda: git("diff", base_sha, "HEAD", "--", SUBDIR, cwd=w1)))
        head = git("rev-parse", "HEAD", cwd=w1).stdout.strip()
        calls.append(call("merge --ff-only into main checkout (merge_task call)",
                          lambda: run_git(repo, "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", head)))
    summary["4 attributes route"] = {"agent": agent, "host saw both files": wrote, "calls": calls}

    # ---- 5 symlinks ------------------------------------------------------------------------------------------
    print("5 symlinks", flush=True)
    secret = outside / "secret.txt"
    secret.write_text("SECRET-CONTENT-SHOULD-NOT-BE-STORED\n")
    w2 = worktree("t2")
    agent = run_agent(w2, [op("real-attrs", w2 / SUBDIR / "real-attrs", "*.probe filter=probe\n"),
                           op("symlinked .gitattributes", w2 / SUBDIR / ".gitattributes", "real-attrs", kind="symlink"),
                           op("sym.probe", w2 / SUBDIR / "sym.probe", "sym\n"),
                           op("link to a file outside", w2 / SUBDIR / "outlink", str(secret), kind="symlink")])
    with fixture_git_config():
        sym = call("commit_all with a SYMLINKED .gitattributes (filter?)", lambda: commit_all(w2, "sym"))
    mode = git("ls-files", "-s", f"{SUBDIR}/outlink", cwd=w2).stdout.split()[:1]
    stored = git("cat-file", "-p", f":{SUBDIR}/outlink", cwd=w2).stdout.strip()
    summary["5 symlinks"] = {"agent": agent, "symlinked gitattributes": sym, "outlink index mode": mode,
                             "outlink stored content is the link target, not the file": stored == str(secret)}
    print(f"   outlink mode {mode}, stored is link target (not file content): {stored == str(secret)}", flush=True)

    # ---- 6 nested repo ---------------------------------------------------------------------------------------
    print("6 nested repo (config inside proj/sub/.git; factory runs from the worktree root)", flush=True)
    w3 = worktree("n")
    sub = w3 / SUBDIR / "sub"
    nested_cfg = f'[core]\n\tfsmonitor = {fsmon}\n[filter "nested"]\n\tclean = {nested_clean} %f\n'
    agent = run_agent(w3, [op("nested HEAD", sub / ".git" / "HEAD", "ref: refs/heads/main\n", mk=True),
                           op("nested config", sub / ".git" / "config", nested_cfg),
                           op("nested objects", sub / ".git" / "objects", kind="dir"),
                           op("nested refs", sub / ".git" / "refs", kind="dir"),
                           op("nested attributes", sub / ".gitattributes", "*.nested filter=nested\n"),
                           op("nested file", sub / "x.nested", "n\n")])
    wrote = (sub / ".git" / "config").is_file()
    print(f"   agent wrote the nested .git (host check): {wrote}; report {agent['report']}", flush=True)
    nested = []
    nested.append(call("factory: status --porcelain from the root", lambda: run_git(w3, "status", "--porcelain")))
    nested.append(call("factory: add -A from the root", lambda: run_git(w3, "add", "-A")))
    nested.append(call("CONTROL (inside proj/sub, unhardened): git status",
                       lambda: subprocess.run(["git", "status"], cwd=sub, capture_output=True, stdin=subprocess.DEVNULL)))
    nested.append(call("CONTROL (inside proj/sub, unhardened): git add -A",
                       lambda: subprocess.run(["git", "add", "-A"], cwd=sub, capture_output=True, stdin=subprocess.DEVNULL)))
    summary["6 nested repo"] = {"agent": agent, "host saw nested .git/config": wrote, "calls": nested}

    # ---- verdict ---------------------------------------------------------------------------------------------
    steer = [c["call"] for c in calls + nested if c["canaries"] and not c["call"].startswith("CONTROL")]
    ctrl_nested = [c for c in nested if c["call"].startswith("CONTROL")]
    summary["VERDICT"] = {
        "factory calls where an agent-written file made git run a fixture driver": steer or "none",
        "nested-repo controls fired (config is live in proj/sub)": [bool(c["canaries"]) for c in ctrl_nested],
        "textconv positive control valid": textconv_ok, "smudge positive control valid": smudge_ok,
    }
    print(f"VERDICT: {'STEERABLE via ' + ', '.join(steer) if steer else 'no factory call ran a driver'}", flush=True)


main()
