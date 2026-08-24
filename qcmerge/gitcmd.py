"""A thin wrapper around the git command line.

git is called through subprocess so the tool needs no third-party
dependencies. Kernel trees are large enough that a single command can print
megabytes, so :meth:`Git.run_bytes` hands back raw bytes wherever the output
does not need to be decoded.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Mapping, Optional, Sequence

from .errors import QcMergeError

log = logging.getLogger(__name__)

#: Settings pinned on every call so the user's git configuration (core.autocrlf,
#: gc behaviour and the like) cannot change what we read back.
COMMON_CONFIG: tuple[str, ...] = (
    "-c", "core.autocrlf=false",
    "-c", "core.safecrlf=false",
    "-c", "core.symlinks=true",
    "-c", "advice.detachedHead=false",
)


class GitError(QcMergeError):
    """Raised when a git command exits with a non-zero status."""

    def __init__(self, argv: Sequence[str], returncode: int, stderr: str) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            "git command failed (exit {code}): {cmd}\n{err}".format(
                code=returncode,
                cmd=" ".join(self.argv),
                err=self.stderr or "(no stderr)",
            )
        )


class Git:
    """Runs git against one repository.

    Parameters
    ----------
    git_dir:
        Path passed as ``--git-dir``. When ``None``, git discovers the
        repository from ``cwd``.
    work_tree:
        Path passed as ``--work-tree``. Used to build an index from the OEM
        source directory without writing anything into it.
    cwd:
        Directory to run git in.
    """

    def __init__(
        self,
        git_dir: Optional[str] = None,
        work_tree: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> None:
        self.git_dir = os.path.abspath(git_dir) if git_dir else None
        self.work_tree = os.path.abspath(work_tree) if work_tree else None
        self.cwd = os.path.abspath(cwd) if cwd else (self.work_tree or self.git_dir)

    # -------------------------------------------------------------- internal
    def _argv(self, args: Sequence[str]) -> list[str]:
        argv = ["git"]
        if self.git_dir:
            argv += ["--git-dir", self.git_dir]
        if self.work_tree:
            argv += ["--work-tree", self.work_tree]
        argv += list(COMMON_CONFIG)
        argv += list(args)
        return argv

    def _env(self, extra: Optional[Mapping[str, str]]) -> dict:
        env = dict(os.environ)
        # Never block on an interactive credential prompt.
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_ASKPASS", "echo")
        if extra:
            env.update(extra)
        return env

    # --------------------------------------------------------------- running
    def run_bytes(
        self,
        *args: str,
        check: bool = True,
        input_bytes: Optional[bytes] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        """Run git and return the :class:`subprocess.CompletedProcess`."""
        argv = self._argv(args)
        log.debug("running git: %s (cwd=%s)", " ".join(argv), self.cwd)
        proc = subprocess.run(
            argv,
            cwd=self.cwd,
            env=self._env(env),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise GitError(argv, proc.returncode, proc.stderr.decode("utf-8", "replace"))
        return proc

    def run(self, *args: str, **kwargs) -> str:
        """Run git and return stdout as text, without the trailing newline."""
        proc = self.run_bytes(*args, **kwargs)
        return proc.stdout.decode("utf-8", "surrogateescape").rstrip("\n")

    def lines(self, *args: str, **kwargs) -> list[str]:
        """Run git and return stdout split into non-empty lines."""
        out = self.run(*args, **kwargs)
        return [line for line in out.split("\n") if line]

    def ok(self, *args: str, **kwargs) -> bool:
        """Return whether the git command succeeded."""
        kwargs["check"] = False
        return self.run_bytes(*args, **kwargs).returncode == 0

    # ------------------------------------------------------------ convenience
    def config_set(self, key: str, value: str) -> None:
        self.run("config", key, value)

    def config_get(self, key: str) -> Optional[str]:
        proc = self.run_bytes("config", "--get", key, check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8", "surrogateescape").strip()

    def rev_parse(self, rev: str) -> str:
        return self.run("rev-parse", "--verify", "--quiet", rev)

    def has_rev(self, rev: str) -> bool:
        return self.ok("rev-parse", "--verify", "--quiet", rev)


def git_version() -> tuple[int, ...]:
    """Return the installed git version as a ``(major, minor, patch)`` tuple."""
    out = subprocess.run(
        ["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ).stdout.decode("utf-8", "replace")
    parts: list[int] = []
    for token in out.split():
        if token[:1].isdigit():
            for chunk in token.split("."):
                if chunk.isdigit():
                    parts.append(int(chunk))
                else:
                    break
            break
    return tuple(parts)
