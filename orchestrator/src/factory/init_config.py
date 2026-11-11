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

# Per-stage additions: plan, review, implement, audit.
# [stage_prompts]
# implement = "Text appended to the implementer's system prompt."
'''


def init_global_config(io) -> int:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE)
    print(f"wrote {path}", file=io.stdout)
    return 0
