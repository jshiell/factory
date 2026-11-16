# Plan 49: let the sandboxed agent read the `claude` binary's directory

Status: reviewed (opus plan-reviewer) and folded; not approved. Three decisions and the spike result are open (below).

## Problem

Found in the first real-repo trial (jshiell/factory-scrap, Linux, nono 0.81, `claude` installed under mise).
Every confined agent run exits 127 with empty stdout, which the factory reports as `plan: no progress (unparseable)`.
Run by hand, nono says: the executable `claude` resolved at
`/home/jsh/.local/share/mise/installs/claude-code/latest/claude`, but its directory is not readable inside the
sandbox (profile `nolabs-ai/claude`), and suggests `--read /home/jsh/.local/share/mise/installs/claude-code/latest`.
The README documents macOS and Seatbelt only; this is the first Linux run.

## Decided (not up for review)

- The factory grants read on the `claude` executable's directory. Not a local nono profile (the factory hardcodes
  `SANDBOX_PROFILE = "nolabs-ai/claude"`, pack-provided) and not a root-owned install.

## Facts checked in the code

- `sandbox_prefix` (`orchestrator/src/factory/command.py:50`) has two callers: `runner.py:29` (agents) and
  `gate_sandbox.py:35` (the test gate; `profile=None`, never runs `claude`). The change goes in `runner.py` only.
- `SubprocessRunner` is built at two sites, `cli.py:56` (eval-audit) and `cli.py:799` (job runner and stages), so one
  change in `runner.py` covers every agent launch. Nothing else in `src/` constructs a runner.
- `sandbox_prefix` emits `--allow-cwd --read <worktree>` first, then one `--read` per `read` entry, then `--`.
- The child env is `os.environ` minus `STRIPPED_ENV` (`process.py:9`, which does not include `PATH`) plus the overlay,
  so a lookup on `os.environ["PATH"]` sees what nono sees.
- `claude_executable` is a plain string setting (`settings.py:63`): no `~` expansion, no resolution against the config
  file.

## Tests affected

- `tests/test_runner.py:317` `test_each_plugin_dir_is_granted_read_only_to_the_sandbox` asserts
  `reads[1:] == ["/h/p a", "/h/p b"]` exactly. `fake_claude` lives in `tmp_path`, so the new grant adds a `--read`
  there and the assertion fails wherever the grant sits. Cycle 1 edits it in the same commit: the plugin dirs are
  the reads after the worktree, excluding the executable's directory. This is a real behaviour change, so the edit is
  legitimate, but it needs the user's approval.
- `tests/test_runner.py:163` takes the first `--read` as the worktree; it stays green as long as the grant comes after
  the worktree. `:181` (unconfined) stays green.
- `tests/test_cli_wiring.py:184,233` and `tests/test_command.py:92,114,118` look only at the first arguments or at
  `sandbox_prefix` itself; unchanged. `tests/test_gate_sandbox.py:23` is the gate; unchanged.

## Step 0: spike (user runs it; throwaway)

Record the binary, then try each grant alone (add each to the worktree command from the trial):

```
ls -l /home/jsh/.local/share/mise/installs/claude-code/latest/claude
readlink -f /home/jsh/.local/share/mise/installs/claude-code/latest/claude
file "$(readlink -f /home/jsh/.local/share/mise/installs/claude-code/latest/claude)"
echo $PATH
nono run --profile nolabs-ai/claude --allow-cwd --read "$PWD" <GRANT> -- claude -p "reply with the word ok" --output-format json; echo "exit=$?"
```

with `<GRANT>` = (a) `--read .../latest` only, (b) `--read <dirname of readlink -f>` only, (c) both.
Pass: a JSON `result`. Also note whether `node` or other paths are needed.

Grant rule, decided by the result: **A** = `dirname(abspath(which(exe)))` (default, matches nono's own suggestion, and
immune to `latest` moving); **B** = the resolved directory; **C** = both. Choose A unless (a) fails.

## Cycles (TDD, one commit each)

1. Red then Green: a confined run adds `--read <rule directory>` after the worktree read. Looked up per `run()`, not at
   startup, with `shutil.which(executable, path=<child PATH>)`; the helper lives in `runner.py`. Fixture mirrors the
   real layout: `inst/latest -> inst/2.1.325/` (directory symlink) with `claude` inside, runner given
   `.../latest/claude`; assert exactly the directories the spike says are needed, and the worktree still the first
   `--read`. Also edits `:317` as described. Commit.
2. Red: an executable `which` cannot find adds no extra `--read` and the run proceeds as today (the fake wrapper's
   exec then gives 127, stdout empty). Commit.
3. Red, only if the user chooses the guard: a directory that is `/`, is `$HOME`, or contains `state_dir` adds no grant.
   Commit.
4. Guard, no new test: an unconfined run is unchanged (`:181`; the grant sits inside `if config.confine:`).
5. Docs: `docs/spike-findings.md` gets the spike result and the chosen rule. README sandbox section: which directory is
   granted, that it is looked up per run, that a relative `claude_executable` and a mise shim as the executable are
   unsupported, and that Linux has been tried once. Commit.
6. Full suite in the background, then `verify`.

## Open (user decides)

1. The spike result (grant rule A, B or C).
2. Secrets guard (cycle 3): skip the grant when the directory is `/`, `$HOME` or contains `state_dir` (the server
   `token` lives there, so a readable `state_dir` lets an agent call the server and approve its own plan). Recommended:
   add it. Alternative: accept the risk and document it. A native-installer layout puts `claude` in `~/.local/bin`,
   which is where it would matter.
3. Shims: recommended is to document the limit. The alternative is to look up on a PATH with shims stripped (reusing
   `gate_sandbox._without_shims`, moved to a shared place) and pass the absolute path after `--`; one more red cycle
   and it touches the executable the agent is launched with.
4. Approval of the `:317` edit.

## Risks / unverified

- Whether nono canonicalises `--read` arguments before its pre-flight check. The spike's (a) vs (b) decides it.
- What `latest/claude` is (ELF or script, symlink or not). The spike records it.
- A mise shim as the executable resolves to mise's own binary. Unverified; documented as unsupported (or fixed under
  open item 3).
- A relative `claude_executable` resolves against the factory's cwd for the lookup but the child's cwd for nono, so the
  grant would name the wrong directory. Already unsupported in practice; documented, not handled.
- Every agent can read the granted directory (the `claude` binary's own files, unless it is a shared bin directory;
  see open item 2).

## Not covered

- Keeping or logging the agent's stdout and stderr on an unparseable result. This cost the trial a lot of diagnosis.
  Worth its own plan.
- Other Linux gaps in the README's macOS-only sandbox assumptions.
- A stray `orchestrator/tests/test_runner.py.bak` exists; not part of this work.
