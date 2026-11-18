"""THROWAWAY (plan O7). $0, no API calls. Run from the repo root in YOUR terminal (not inside an agent nono session):
    uv run --project orchestrator python spike/confinement_probe.py
    uv run --project orchestrator python spike/confinement_probe.py --claude-dir --ssh     # the two opt-in checks

What it measures: what code running under the REAL prefixes can read and write. The prefixes come from the product:
the agent prefix from `SubprocessRunner.run` (confine=True, a fake executable alone in its own directory, run once under
an implement-like config and once under AUDIT_CONFIG), the gate prefix from `build_gate_sandbox`. Each read target first
gets an unconfined control (a target that does not read OK unconfined is dropped); a confined read counts as a leak only
if the canary token is seen. The positive controls (project dir read and write) must pass or nono failed to start and
every "denied" is meaningless.

Touches: a fixture under $HOME (`confprobe-<pid>/`: repo, two worktrees, state dir, fake executable), one canary file in
$HOME, one in $TMPDIR, `~/.claude/` and `~/.cache/` write canaries (only if those directories exist). All removed at the end.
Does NOT touch ~/.claude.json (the older `nono_impl_probe.py` does, and restores it).
Opt-in: `--claude-dir` plants a canary beside the real `claude` binary and checks the executable grant; `--ssh` tries to
open ONE real file in ~/.ssh and reports only opened or the errno (the contents are never read out or printed).
Paste the printed SUMMARY back."""
import json, os, shutil, subprocess, sys, tempfile, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))
from factory.audit_task import AUDIT_CONFIG  # noqa: E402
from factory.command import RunnerConfig, sandbox_prefix  # noqa: E402
from factory.gate_sandbox import build_gate_sandbox, discover_uv_tooling  # noqa: E402
from factory.runner import SubprocessRunner, _executable_directory  # noqa: E402
from factory.task import Task  # noqa: E402

WITH_CLAUDE_DIR = "--claude-dir" in sys.argv
WITH_SSH = "--ssh" in sys.argv
HOME = Path.home()
PID = os.getpid()
BASE = HOME / f"confprobe-{PID}"
PYTHON = "/usr/bin/python3"
SUBDIR = "proj"
TIMEOUT = 120

READER = r'''
import errno, json, os, sys
spec = json.load(open(sys.argv[1]))
out = {"reads": {}, "writes": {}, "env": {}}
for t in spec["reads"]:
    try:
        with open(t["path"], "rb") as f:
            data = f.read(65536) if t["token"] else f.read(1)
        out["reads"][t["label"]] = ("READ" if t["token"].encode() in data else "OPENED-NO-TOKEN") if t["token"] else "OPENED"
    except OSError as e:
        out["reads"][t["label"]] = errno.errorcode.get(e.errno, str(e.errno))
for t in spec["writes"]:
    try:
        with open(t["path"], "w") as f:
            f.write("ESCAPED")
        out["writes"][t["label"]] = "WROTE"
    except OSError as e:
        out["writes"][t["label"]] = errno.errorcode.get(e.errno, str(e.errno))
for name in spec["env"]:
    out["env"][name] = "visible" if os.environ.get(name) else "absent"
print(json.dumps(out))
'''


def sh(*argv, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=TIMEOUT)


def git(*args, cwd):
    return sh("git", "-c", "user.name=f", "-c", "user.email=f@f", "-c", "commit.gpgsign=false", *args, cwd=cwd)


def token(tag):
    return f"CONFPROBE-{tag}-{uuid.uuid4().hex[:8]}"


def version(*argv):
    try:
        p = sh(*argv)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({type(e).__name__})"


def plant(path, tag, tokens):
    path.parent.mkdir(parents=True, exist_ok=True)
    tokens[tag] = token(tag)
    path.write_text(tokens[tag] + "\n")
    path.chmod(0o644)
    return path


# ---- fixture ---------------------------------------------------------------------------------------------------
BASE.mkdir()
REPO = (BASE / "repo").resolve()
STATE = BASE / "state"
EXEDIR = BASE / "exe"
OUTSIDE = Path(tempfile.mkdtemp(prefix="confprobe-outside-"))
for d in (REPO, STATE, EXEDIR):
    d.mkdir(exist_ok=True)
(REPO / SUBDIR).mkdir()
(REPO / SUBDIR / "code.py").write_text("x = 1\n")
git("init", "-q", cwd=REPO)
git("add", "-A", cwd=REPO)
git("commit", "-qm", "base", cwd=REPO)
WT = (REPO / ".worktrees" / "a")
WT_B = (REPO / ".worktrees" / "b")
git("worktree", "add", "-q", "-b", "probe-a", str(WT), cwd=REPO)
git("worktree", "add", "-q", "-b", "probe-b", str(WT_B), cwd=REPO)
WT, WT_B = WT.resolve(), WT_B.resolve()
PROJECT = WT / SUBDIR
cleanup_files = []
TOKENS = {}

(WT / SUBDIR / "in-project.txt").write_text(token("project") + "\n")
TOKENS["project dir file (positive control)"] = (WT / SUBDIR / "in-project.txt").read_text().strip()
READS = [("project dir file (positive control)", WT / SUBDIR / "in-project.txt")]
for label, path in (
    ("other task's worktree", WT_B / SUBDIR / "b-canary.txt"),
    ("main repo root file", REPO / "root-canary.txt"),
    ("state dir (server token)", STATE / "token"),
    ("outside the repo, in $TMPDIR", OUTSIDE / "outside.txt"),
    ("$HOME", HOME / f".confprobe-{PID}.txt"),
    ("dir of the fake executable (granted)", EXEDIR / "sibling.txt"),
):
    READS.append((label, plant(path, label, TOKENS)))
cleanup_files.append(HOME / f".confprobe-{PID}.txt")
with open(REPO / ".git" / "config", "a") as f:
    TOKENS["main repo .git/config"] = token("gitconfig")
    f.write(f"# {TOKENS['main repo .git/config']}\n")
READS.append(("main repo .git/config", REPO / ".git" / "config"))

claude_dir_canary = None
if WITH_CLAUDE_DIR:
    found = shutil.which("claude")
    if found:
        claude_dir_canary = plant(Path(found).resolve().parent / f".confprobe-{PID}.txt", "beside the real claude binary", TOKENS)
        cleanup_files.append(claude_dir_canary)
        READS.append(("beside the real claude binary", claude_dir_canary))
    else:
        print("--claude-dir: no `claude` on PATH, skipped")

ssh_file = None
if WITH_SSH:
    candidates = sorted(p for p in (HOME / ".ssh").glob("*") if p.is_file()) if (HOME / ".ssh").is_dir() else []
    if candidates:
        ssh_file = candidates[0]
        READS.append(("one real ~/.ssh file (opened only)", ssh_file))
    else:
        print("--ssh: no regular file in ~/.ssh, skipped")

WRITES = [
    ("project dir (positive control)", PROJECT / "escape.txt"),
    ("worktree root", WT / "escape.txt"),
    ("other task's worktree", WT_B / SUBDIR / "escape.txt"),
    ("main repo root", REPO / "escape.txt"),
    ("main repo .git/config", REPO / ".git" / "config"),
    ("state dir", STATE / "escape.txt"),
    ("$HOME", HOME / f"escape-{PID}.txt"),
    ("$TMPDIR", OUTSIDE / "escape.txt"),
]
for name in (".claude", ".cache"):
    if (HOME / name).is_dir():
        WRITES.append((f"~/{name}", HOME / name / f"confprobe-escape-{PID}.txt"))
WRITE_PATHS = {label: path for label, path in WRITES}
cleanup_files += [path for label, path in WRITES if label not in ("main repo .git/config",)]
ENV_CANARIES = {"ANTHROPIC_API_KEY": token("api"), "GH_TOKEN": token("gh"), "GITHUB_TOKEN": token("ghb"), "CONFPROBE_PLAIN": token("env")}
os.environ.update(ENV_CANARIES)

(EXEDIR / "reader.py").write_text(READER)
fake = EXEDIR / "fake-claude"
fake.write_text(f"#!/bin/sh\nexec {PYTHON} {EXEDIR / 'reader.py'} {EXEDIR / 'spec.json'}\n")
fake.chmod(0o755)


def spec_for(reads):
    return {"reads": [{"label": label, "path": str(path), "token": TOKENS.get(label)} for label, path in reads],
            "writes": [{"label": label, "path": str(path)} for label, path in WRITES], "env": list(ENV_CANARIES)}


def parse(stdout):
    lines = [l for l in stdout.splitlines() if l.startswith("{")]
    return json.loads(lines[-1]) if lines else None


def reset_writes():
    for label, path in WRITES:
        if label != "main repo .git/config":
            path.unlink(missing_ok=True)
    git_config_before = (REPO / ".git" / "config").read_text()
    return git_config_before


def ground_truth_writes(git_config_before):
    truth = {label: path.exists() and path.read_text() == "ESCAPED" for label, path in WRITES if label != "main repo .git/config"}
    truth["main repo .git/config"] = (REPO / ".git" / "config").read_text() == "ESCAPED"
    (REPO / ".git" / "config").write_text(git_config_before)
    reset_writes()
    return truth


# ---- control: unconfined ---------------------------------------------------------------------------------------
control_spec = spec_for(READS)
(BASE / "control-spec.json").write_text(json.dumps({**control_spec, "writes": []}))
(BASE / "reader.py").write_text(READER)
control = parse(sh(PYTHON, str(BASE / "reader.py"), str(BASE / "control-spec.json"), cwd=PROJECT).stdout) or {"reads": {}}
usable = [(label, path) for label, path in READS if control["reads"].get(label) in ("READ", "OPENED")]
dropped = [label for label, _ in READS if label not in {l for l, _ in usable}]
(EXEDIR / "spec.json").write_text(json.dumps(spec_for(usable)))

# ---- confined runs ---------------------------------------------------------------------------------------------
runs = {}
IMPLEMENT = RunnerConfig(model="sonnet", confine=True, permission_mode="acceptEdits")
for name, config in (("agent (implement-like config)", IMPLEMENT), ("agent (AUDIT_CONFIG, read-only tools)", AUDIT_CONFIG)):
    before = reset_writes()
    runner = SubprocessRunner(str(fake), SUBDIR, TIMEOUT, private_dirs=(str(STATE),))
    result = runner.run(Task(id="probe", prompt="probe", worktree=str(WT)), config)
    runs[name] = {"exit": result.exit_code, "report": parse(result.stdout), "stderr": result.stderr[-300:].strip(),
                  "writes_truth": ground_truth_writes(before)}

tooling = discover_uv_tooling(PROJECT)
cache_dir = BASE / "gate-cache"
cache_dir.mkdir()
gate = build_gate_sandbox(sandbox_executable="nono", worktree=WT, project_dir=PROJECT, cache_dir=cache_dir,
                          python_prefix=tooling.python_prefix, uv_python_dir=tooling.uv_python_dir, real_uv=tooling.real_uv,
                          base_env=dict(os.environ), test_argv=("uv", "run", "pytest", "-q"))
(PROJECT / "gate-reader.py").write_text(READER)
(PROJECT / "gate-spec.json").write_text(json.dumps(spec_for(usable)))
before = reset_writes()
p = sh(*gate.prefix, PYTHON, "gate-reader.py", "gate-spec.json", cwd=PROJECT, env=gate.env)
runs["gate (build_gate_sandbox)"] = {"exit": p.returncode, "report": parse(p.stdout), "stderr": p.stderr[-300:].strip(),
                                     "writes_truth": ground_truth_writes(before)}
(PROJECT / "gate-reader.py").unlink()
(PROJECT / "gate-spec.json").unlink()

# ---- the executable grant for the real claude (opt-in) ---------------------------------------------------------
claude_grant = None
if claude_dir_canary:
    grant = _executable_directory("claude", os.environ.get("PATH"), (str(STATE), str(HOME)))
    siblings = {d: len(list(Path(d).iterdir())) for d in grant}
    (BASE / "claude-spec.json").write_text(json.dumps({"reads": [{"label": "beside the real claude binary", "path": str(claude_dir_canary),
                                                                 "token": TOKENS["beside the real claude binary"]}],
                                                       "writes": [], "env": []}))
    (EXEDIR / "claude-spec.json").write_text((BASE / "claude-spec.json").read_text())
    prefix = sandbox_prefix("nono", WT, read=(*grant, str(EXEDIR)))
    p = sh(*prefix, PYTHON, str(EXEDIR / "reader.py"), str(EXEDIR / "claude-spec.json"), cwd=PROJECT)
    claude_grant = {"directories granted": list(grant), "entries in them": siblings, "report": parse(p.stdout), "exit": p.returncode}

# ---- summary ---------------------------------------------------------------------------------------------------
print("\n================ SUMMARY (plan O7 confinement probe) ================")
print("nono:", version("nono", "--version"), "| kernel:", version("uname", "-r"), "| uv:", version("uv", "--version"),
      "| claude:", version("claude", "--version"))
print("agent exe grant (fake exe):", _executable_directory(str(fake), os.environ.get("PATH"), (str(STATE), str(HOME))))
print("gate prefix:", " ".join(gate.prefix))
print("targets dropped (not readable unconfined):", dropped or "none")
columns = list(runs)
print("\nREADS ('READ' = canary seen = leak; anything else is the errno or OPENED)")
print(f"  {'target':42s} {'control':8s} " + " | ".join(columns))
for label, _ in READS:
    if label in dropped:
        continue
    cells = [str((runs[c]["report"] or {}).get("reads", {}).get(label, "-")) for c in columns]
    print(f"  {label:42s} {control['reads'].get(label, '-'):8s} " + " | ".join(cells))
print("\nWRITES (ground truth: the file exists with the ESCAPED content)")
print(f"  {'target':42s} " + " | ".join(columns))
for label, _ in WRITES:
    cells = [("WROTE" if runs[c]["writes_truth"].get(label) else "no") + f" ({(runs[c]['report'] or {}).get('writes', {}).get(label, '-')})" for c in columns]
    print(f"  {label:42s} " + " | ".join(cells))
print("\nENV (visible to the confined child; values never printed)")
for name in ENV_CANARIES:
    print(f"  {name:20s} " + " | ".join(str((runs[c]['report'] or {}).get('env', {}).get(name, '-')) for c in columns))
print("\nRUN STATUS")
for c in columns:
    print(f"  {c}: exit={runs[c]['exit']} report={'yes' if runs[c]['report'] else 'NO (nono may have failed to start)'} stderr={runs[c]['stderr']!r}")
    pos_read = (runs[c]['report'] or {}).get("reads", {}).get("project dir file (positive control)")
    pos_write = runs[c]["writes_truth"].get("project dir (positive control)")
    if c.startswith("gate") or pos_read:
        print(f"    positive controls: read={pos_read} write={pos_write}")
if claude_grant:
    print("\nREAL claude executable grant:", json.dumps(claude_grant))
if ssh_file:
    print("\n(~/.ssh row above shows only OPENED or an errno; no contents were read out)")

# ---- cleanup (always) ------------------------------------------------------------------------------------------
for f in cleanup_files:
    f.unlink(missing_ok=True)
git("worktree", "remove", "--force", str(WT), cwd=REPO)
git("worktree", "remove", "--force", str(WT_B), cwd=REPO)
shutil.rmtree(BASE, ignore_errors=True)
shutil.rmtree(OUTSIDE, ignore_errors=True)
