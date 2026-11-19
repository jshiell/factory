"""THROWAWAY (plan 55). Run from the repo root, in YOUR terminal (not inside an agent's nono session):
    uv run --project orchestrator python spike/gate_sandbox_probe.py [--smoke] [--tmp DIR]
--smoke: only run `uv --version` through the gate sandbox prefix, then stop.
Needs network (package index, and an older uv for the lock probes). Creates temp fixtures, removes them at the end.
Raw output (exit code, stdout, stderr per probe) goes to spike/gate-sandbox-probe-out/ (gitignored). Paste the summary back."""
import json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))
from factory.gate import check_gate, commit_all  # noqa: E402
from factory.gate_sandbox import GateSandbox, build_gate_sandbox, discover_uv_tooling  # noqa: E402
from factory.sandbox import NonoSandbox  # noqa: E402
from factory.worktree_clean import clean_worktree  # noqa: E402

OUT = Path(__file__).resolve().parent / "gate-sandbox-probe-out"
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir()
TMP_ARG = sys.argv[sys.argv.index("--tmp") + 1] if "--tmp" in sys.argv else None
WORK = Path(tempfile.mkdtemp(prefix="gate-probe-", dir=TMP_ARG)).resolve()
TEST_ARGV = ("uv", "run", "pytest", "-q")
SUMMARY: list[str] = []
DEVIATIONS: list[str] = []
EXTRA_READ: list[str] = []
N = 0


def say(line: str) -> None:
    SUMMARY.append(line)
    print(line, flush=True)


def sh(*a, cwd=None, env=None, timeout=300):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout, stdin=subprocess.DEVNULL)


def git(*a, cwd):
    return sh("git", "-c", "user.name=f", "-c", "user.email=f@f", "-c", "commit.gpgsign=false", *a, cwd=cwd)


def run(label, argv, cwd, env, timeout=300):
    """Run, save exit/stdout/stderr, return (exit, stdout, stderr). An OSError or timeout is recorded as exit -1."""
    global N
    N += 1
    name = f"{N:02d}-{re.sub(r'[^A-Za-z0-9]+', '-', label)}"
    try:
        r = sh(*argv, cwd=cwd, env=env, timeout=timeout)
        code, out, err = r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        code, out, err = -1, "", repr(e)
    (OUT / f"{name}.txt").write_text(f"argv: {json.dumps(list(argv))}\ncwd: {cwd}\nexit: {code}\n--- stdout\n{out}\n--- stderr\n{err}\n")
    return code, out, err


TOOLING = discover_uv_tooling(ROOT / "target")


def fixture(name: str) -> tuple[Path, Path, Path]:
    """Fresh git repo holding the committed target/ (git archive: no .venv, no caches) plus an empty cache dir."""
    repo = WORK / name / "repo"
    repo.mkdir(parents=True)
    tar = subprocess.run(["git", "archive", "HEAD", "target"], cwd=ROOT, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(repo)], input=tar, check=True)
    git("init", "-q", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    cache = WORK / name / "cache"
    cache.mkdir()
    return repo, repo / "target", cache


def sandbox(repo: Path, project: Path, cache: Path) -> GateSandbox:
    sb = build_gate_sandbox(
        sandbox=NonoSandbox("nono"), worktree=repo, project_dir=project, cache_dir=cache,
        python_prefix=TOOLING.python_prefix, uv_python_dir=TOOLING.uv_python_dir, real_uv=TOOLING.real_uv,
        base_env=dict(os.environ), test_argv=TEST_ARGV,
    )
    if EXTRA_READ:  # labelled deviation: grant read on the directory holding uv
        prefix = list(sb.prefix)
        for d in EXTRA_READ:
            prefix[-1:-1] = ["--read", d]
        sb = GateSandbox(tuple(prefix), sb.argv, sb.env)
    return sb


def uv_argv(sb: GateSandbox, *args: str) -> list[str]:
    return [sb.argv[0], *args]


def under(sb: GateSandbox, argv, on: bool) -> list[str]:
    return [*(sb.prefix if on else ()), *argv]


def env_notes() -> None:
    keys = [k for k in os.environ if k.upper().endswith("_PROXY") or k.startswith("UV_") or k == "NO_PROXY"]
    say(f"env: proxy/UV_* vars set: {sorted(keys) or 'none'}")
    for label, cmd in (("uv", [TOOLING.real_uv, "--version"]), ("nono", ["nono", "--version"])):
        say(f"version {label}: {sh(*cmd).stdout.strip()}")
    say(f"real_uv={TOOLING.real_uv} python_prefix={TOOLING.python_prefix} uv_python_dir={TOOLING.uv_python_dir}")


def smoke() -> None:
    repo, project, cache = fixture("smoke")
    sb = sandbox(repo, project, cache)
    code, out, err = run("smoke uv --version", under(sb, [sb.argv[0], "--version"], True), project, sb.env)
    say(f"smoke [prefix as built]: exit={code} out={out.strip()!r} err={err.strip()[:200]!r}")
    if code != 127:
        if code:
            say("  -> not an exec failure (127); no deviation applied, later probes will likely fail the same way")
        return
    say("  -> gate cannot exec uv under the prefix (plan 56 defect candidate); retrying with a read grant on uv's directory")
    EXTRA_READ.append(str(Path(TOOLING.real_uv).resolve().parent))
    DEVIATIONS.append(f"--read {EXTRA_READ[0]} added locally")
    sb = sandbox(repo, project, cache)
    code, out, err = run("smoke uv --version (deviation)", under(sb, [sb.argv[0], "--version"], True), project, sb.env)
    say(f"smoke [DEVIATION read uv dir]: exit={code} out={out.strip()!r} err={err.strip()[:200]!r}")


PY = str(Path(TOOLING.python_prefix) / "bin" / "python3")
DNS = "import socket; print(socket.getaddrinfo('pypi.org', 443)[0][4])"
TCP = "import socket; socket.create_connection(('1.1.1.1', 443), timeout=10).close(); print('connected')"


def network() -> None:
    repo, project, cache = fixture("network")
    sb = sandbox(repo, project, cache)
    for label, code_ in (("dns getaddrinfo", DNS), ("tcp connect 1.1.1.1:443", TCP)):
        for on in (False, True):
            code, out, err = run(f"net {label} {'sandbox' if on else 'control'}", under(sb, [PY, "-c", code_], on), project, sb.env)
            say(f"network {label} [{'sandbox' if on else 'control'}]: exit={code} {(out or err).strip()[:120]!r}")
    for on in (False, True):
        r, p, c = fixture(f"net-uv-{'sb' if on else 'ctl'}")
        s = sandbox(r, p, c)
        code, out, err = run(f"net uv run pytest cold {'sandbox' if on else 'control'}", under(s, s.argv, on), p, s.env)
        say(f"network uv run pytest, cold cache [{'sandbox' if on else 'control'}]: exit={code} tail={(out + err).strip()[-120:]!r}")
    for on in (False, True):
        r, p, c = fixture(f"net-grp-{'sb' if on else 'ctl'}")
        s = sandbox(r, p, c)
        text = (p / "pyproject.toml").read_text().replace('"pytest>=9.2.0",', '"pytest>=9.2.0",\n    "tomli-w",')
        (p / "pyproject.toml").write_text(text)
        code, out, err = run(f"net new dev dep {'sandbox' if on else 'control'}", under(s, uv_argv(s, "run", "python", "-c", "import tomli_w; print('installed')"), on), p, s.env)
        say(f"network new [dev] dependency installed [{'sandbox' if on else 'control'}]: exit={code} {(out or err).strip()[-120:]!r}")


def markers(project: Path, names) -> str:
    return ", ".join(f"{n}={'present' if (project / n).exists() else 'absent'}" for n in names)


SETUPTOOLS_PYPROJECT = """[project]
name = "calc"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []
dynamic = ["entry-points"]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[dependency-groups]
dev = ["pytest>=9.2.0"]
"""
SETUP_PY = """import os
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "marker_a"), "w").write("setup.py ran")
from setuptools import setup
setup(entry_points={"pytest11": ["calcplug = calc.plug"]})
"""
PLUG = """import os
def pytest_configure(config):
    open(os.path.join(os.getcwd(), "marker_b"), "w").write("plugin loaded")
"""
HATCH_PYPROJECT = """[project]
name = "calc"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/calc"]

[tool.hatch.build.hooks.custom]

[dependency-groups]
dev = ["pytest>=9.2.0"]
"""
HATCH_BUILD = """import os
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

class Hook(BuildHookInterface):
    def initialize(self, version, build_data):
        open(os.path.join(self.root, "marker_c"), "w").write("hatch hook ran")
"""


def build_probe(name: str, on: bool, files: dict[str, str], marks: tuple[str, ...], warm_edit: str | None = None) -> None:
    repo, project, cache = fixture(f"{name}-{'sb' if on else 'ctl'}")
    sb = sandbox(repo, project, cache)
    for rel, text in files.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text)
    (project / "uv.lock").unlink(missing_ok=True)
    tag = "sandbox" if on else "control"
    code, out, err = run(f"build {name} cold {tag}", under(sb, sb.argv, on), project, sb.env)
    built = "build/test ok" if code == 0 else f"exit={code} (NOT RUN if the build failed: {(out + err).strip()[-150:]!r})"
    say(f"build {name} cold cache [{tag}]: {built}; {markers(project, marks)}")
    if warm_edit and on:
        for m in marks:
            (project / m).unlink(missing_ok=True)
        (project / warm_edit).write_text((project / warm_edit).read_text() + "\n# edited\n")
        code, out, err = run(f"build {name} warm rerun {tag}", under(sb, sb.argv, on), project, sb.env)
        say(f"build {name} warm cache, hook file edited [information only]: exit={code}; {markers(project, marks)}")


def builds() -> None:
    for on in (True, False):
        build_probe("setuptools", on, {"pyproject.toml": SETUPTOOLS_PYPROJECT, "setup.py": SETUP_PY, "src/calc/plug.py": PLUG}, ("marker_a", "marker_b"))
        build_probe("hatch", on, {"pyproject.toml": HATCH_PYPROJECT, "hatch_build.py": HATCH_BUILD}, ("marker_c",), warm_edit="hatch_build.py")


def revision(lock: Path) -> str:
    m = re.search(r"^revision = (\d+)", lock.read_text(), re.M)
    return m.group(1) if m else "none"


def lock_with_old_uv(project: Path, cache: Path, version: str) -> tuple[bool, str]:
    (project / "uv.lock").unlink(missing_ok=True)
    env = {**os.environ, "UV_CACHE_DIR": str(cache)}
    code, out, err = run(f"old uv lock {version}", [TOOLING.real_uv, "tool", "run", "--from", f"uv=={version}", "uv", "lock"], project, env)
    return code == 0 and (project / "uv.lock").exists(), (out + err).strip()[-150:]


def lock_probes() -> None:
    repo, project, cache = fixture("lock-native")
    native = revision(project / "uv.lock")
    say(f"lock: committed target/uv.lock revision={native}")
    chosen = None
    for version in ("0.8.0", "0.7.0", "0.6.0"):
        r, p, c = fixture(f"lock-old-{version}")
        ok, note = lock_with_old_uv(p, c, version)
        rev = revision(p / "uv.lock") if ok else "n/a"
        say(f"lock: uv=={version} wrote revision={rev}" + ("" if ok else f" (failed: {note!r})"))
        if ok and rev != native and chosen is None:
            chosen = (version, r, p, c)
    variants = []
    if chosen:
        variants.append((f"old-uv-{chosen[0]}", chosen[1], chosen[2], chosen[3]))
    r, p, c = fixture("lock-hand-edited")
    (p / "uv.lock").write_text(re.sub(r"^revision = \d+", "revision = 1", (p / "uv.lock").read_text(), flags=re.M))
    variants.append(("hand-edited-revision-1 (ARTIFICIAL)", r, p, c))
    for name, repo, project, cache in variants:
        git("add", "-A", cwd=repo)
        git("commit", "-qm", "old lock", "--allow-empty", cwd=repo)
        sb = sandbox(repo, project, cache)
        before = (project / "uv.lock").read_bytes()
        code, out, err = run(f"lock {name} gate run", under(sb, sb.argv, True), project, sb.env)
        after = (project / "uv.lock").read_bytes()
        say(f"lock {name}: gate run exit={code}; lock rewritten={before != after}; revision {revision_of(before)} -> {revision_of(after)}; git status={git('status', '--porcelain', cwd=repo).stdout.strip()!r}")
        for flag in ("--frozen", "--locked"):
            (project / "uv.lock").write_bytes(before)
            code, out, err = run(f"lock {name} {flag}", under(sb, uv_argv(sb, "run", flag, "pytest", "-q"), True), project, sb.env)
            say(f"lock {name} {flag} [plan 56 input]: exit={code}; lock rewritten={(project / 'uv.lock').read_bytes() != before}; tail={(out + err).strip()[-100:]!r}")
        replay(name, repo, project, cache, before)


def revision_of(data: bytes) -> str:
    m = re.search(rb"^revision = (\d+)", data, re.M)
    return m.group(1).decode() if m else "none"


def replay(name: str, repo: Path, project: Path, cache: Path, old_lock: bytes) -> None:
    """Round 1 fails, a fix edit, round 2; then a separate passing round 1. Uses the real commit_all/clean_worktree/check_gate."""
    git("checkout", "-q", "--", ".", cwd=repo)
    git("clean", "-ffdxq", cwd=repo)
    (project / "uv.lock").write_bytes(old_lock)
    git("add", "-A", cwd=repo); git("commit", "-qm", "base with old lock", "--allow-empty", cwd=repo)
    base = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    init = project / "src" / "calc" / "__init__.py"
    good = init.read_text() or "def add(a, b):\n    return a + b\n"
    init.write_text("def add(a, b):\n    return a - b\n")  # round 1: tests fail
    sb = sandbox(repo, project, cache)

    def gate(frozen=None):
        commit_all(repo, "agent work")
        clean_worktree(repo)
        return check_gate(repo, base, "target", list(sb.argv), 300, env=sb.env, test_prefix=sb.prefix, frozen_sha=frozen)

    r1 = gate()
    status1 = git("status", "--porcelain", cwd=repo).stdout.strip()
    say(f"replay {name}: round 1 -> {r1.reason} (passed={r1.passed}); uncommitted after round 1: {status1!r}")
    frozen = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    init.write_text(good)  # simulated fix
    r2 = gate(frozen)
    say(f"replay {name}: round 2 -> {r2.reason} (passed={r2.passed}); lock differs from base: {git('diff', '--name-only', base, 'HEAD', '--', 'target/uv.lock', cwd=repo).stdout.strip()!r}")
    git("reset", "-q", "--hard", base, cwd=repo)
    (project / "src" / "calc" / "extra.py").write_text("X = 1\n")
    r3 = gate()
    say(f"replay {name}: passing round 1 -> {r3.reason} (passed={r3.passed}); git status after: {git('status', '--porcelain', cwd=repo).stdout.strip()!r}")


def main() -> None:
    say(f"fixtures under {WORK}")
    env_notes()
    smoke()
    if "--smoke" in sys.argv:
        return
    network()
    builds()
    lock_probes()


try:
    main()
finally:
    say(f"deviations: {DEVIATIONS or 'none'}")
    (OUT / "summary.txt").write_text("\n".join(SUMMARY) + "\n")
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\nraw output: {OUT}")
