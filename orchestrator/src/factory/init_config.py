from factory.settings import global_config_path

TEMPLATE = '''\
# Global factory settings, applied to every project.
# A project's own factory.toml (or --config) overrides these key by key.
# Per-repo paths (repo, state_dir, worktree_root) are not allowed here.
# Uncomment and edit what you want to change.

# model = "sonnet"
# budget_usd = 0.5
# cap_usd = 5.0
# max_turns = 30
# fix_rounds = 2
# allowed_tools = ["Read", "Grep"]
# append_system_prompt = "Text appended to every stage's system prompt."

# Plugins (skills and agents) to give every stage, by directory name under <home>/_shared/plugins/.
# plugins = ["my-plugin"]

# Per-stage additions: plan, review, implement, audit.
# [stage_prompts]
# implement = "Text appended to the implementer's system prompt."

# Per-stage plugins, added to the global plugins for that stage only.
# [stage_plugins]
# review = ["my-plugin"]
'''


def init_global_config(io) -> int:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as file:
            file.write(TEMPLATE)
    except FileExistsError:
        print(f"{path} already exists; not overwriting", file=io.stderr)
        return 1
    (path.parent / "_shared" / "plugins").mkdir(parents=True, exist_ok=True)
    print(f"wrote {path}", file=io.stdout)
    return 0
