import os
from dotenv import load_dotenv

# Take environment variables from .env and load them into os.environ
load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment variable. Empty or unset keeps the default."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false, got {raw!r}")


# DB connection
mongo_conn_str = os.environ.get("MONGO_CONN_STR", "")
mongo_app_db_name = "mongodb_claude_orchestrator"    # Task and session metadata.
claude_session_db_name = "claude"       # Claude SDK transcripts for resumable sessions.
poll_interval_seconds = 5

# Git configs
git_base_url = os.environ.get("GIT_HOST", "")
git_ssh_key_path = os.environ.get("GIT_SSH_KEY_PATH", "")
git_strict_host_checking = _env_flag("STRICT_HOST_CHECKING", True)
git_author_name = os.environ.get("GIT_AUTHOR_NAME", "")
git_author_email = os.environ.get("GIT_AUTHOR_EMAIL", "")

# LLM configs
claude_model = os.environ.get("CLAUDE_MODEL", "")
claude_api_key = os.environ.get("CLAUDE_API_KEY", "")
claude_api_url = os.environ.get("CLAUDE_API_URL", "")

# Harness configs
system_prompt = """\
The current working directory contains a code repo. Perform the actions described in the prompt you are given by the user.
When the work is finished, reply with a summary of the tasks performed and the assumptions made.
"""
