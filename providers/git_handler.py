from pathlib import Path

import pygit2

from config import config


class _SshKeyCallbacks(pygit2.RemoteCallbacks):
    def __init__(self, private_key: Path, public_key: Path | None) -> None:
        super().__init__()
        self._private_key = private_key
        self._public_key = public_key

    def credentials(self, url, username_from_url, allowed_types):
        username = username_from_url or "git"
        if allowed_types & pygit2.enums.CredentialType.USERNAME:
            return pygit2.Username(username)
        if allowed_types & pygit2.enums.CredentialType.SSH_KEY:
            return pygit2.Keypair(username, self._public_key, self._private_key, "")
        return None

    def certificate_check(self, _certificate, valid, _host) -> bool:
        """Compare the server host key with known_hosts unless checking is off."""
        if config.git_strict_host_checking:
            return bool(valid)
        return True


def _ssh_callbacks() -> _SshKeyCallbacks:
    """Authenticate clone and push with the private key from GIT_SSH_KEY_PATH."""
    key_path = config.git_ssh_key_path.strip()
    if not key_path:
        raise RuntimeError("GIT_SSH_KEY_PATH is not set")

    private_key = Path(key_path).expanduser()
    if not private_key.is_file():
        raise RuntimeError(f"SSH private key not found: {private_key}")

    public_key = Path(f"{private_key}.pub")
    return _SshKeyCallbacks(
        private_key,
        public_key if public_key.is_file() else None,
    )


class GitHandler:
    def __init__(
        self,
        repo_name: str,
        base_branch: str,
        task_id: str,
        create_feature_branch: bool = True,
    ) -> None:
        self.repo_name = repo_name
        self.base_branch = base_branch
        self.create_feature_branch = create_feature_branch
        self.feature_branch = (
            self.feature_branch_name(task_id) if create_feature_branch else base_branch
        )
        self.repository: pygit2.Repository | None = None

    def clone_url(self) -> str:
        """Build the Git clone URL for a short repo name such as owner/name."""
        name = self.repo_name.strip().strip("/")
        if name.endswith(".git"):
            name = name[:-4]
        return f"{config.git_base_url.rstrip('/')}/{name}.git"

    def feature_branch_name(self, task_id: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in task_id)
        return f"agent/{safe}"

    def prepare_repository(self, destination: str) -> pygit2.Repository:
        repository = pygit2.clone_repository(
            self.clone_url(),
            destination,
            checkout_branch=self.base_branch,
            callbacks=_ssh_callbacks(),
        )
        if self.create_feature_branch:
            head_commit = repository.head.peel(pygit2.Commit)
            branch = repository.branches.local.create(self.feature_branch, head_commit)
            repository.checkout(branch)
        self.repository = repository
        return repository

    def stage_worktree(self) -> None:
        repository = self._require_repository()
        if not repository.workdir:
            raise RuntimeError("Repository has no work tree")
        repository.index.add_all()
        workdir = Path(repository.workdir)
        for entry in list(repository.index):
            if not (workdir / entry.path).exists():
                repository.index.remove(entry.path)
        repository.index.write()

    def commit_and_push(self, commit_title: str, description: str, summary: str) -> None:
        repository = self._require_repository()
        self.stage_worktree()
        tree_id = repository.index.write_tree()
        head_commit = repository.head.peel(pygit2.Commit)
        if tree_id == head_commit.tree_id:
            raise RuntimeError("Nothing to commit")

        parts = [part.strip() for part in (commit_title, description, summary) if part.strip()]
        message = "\n\n".join(parts)
        signature = pygit2.Signature(config.git_author_name, config.git_author_email)
        repository.create_commit(
            "HEAD",
            signature,
            signature,
            message,
            tree_id,
            [head_commit.id],
        )
        repository.remotes["origin"].push(
            [f"refs/heads/{self.feature_branch}:refs/heads/{self.feature_branch}"],
            callbacks=_ssh_callbacks(),
        )

    def _require_repository(self) -> pygit2.Repository:
        if self.repository is None:
            raise RuntimeError("Repository has not been prepared")
        return self.repository
