"""git 명령을 감싸는 얇은 래퍼.

외부 의존성 없이 subprocess 로 git 을 직접 호출한다. 커널 트리를 다루므로
출력이 수 MB 단위로 커질 수 있어서, 텍스트 디코딩이 필요 없는 경우에는
bytes 를 그대로 돌려주는 :meth:`Git.run_bytes` 를 쓴다.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Mapping, Optional, Sequence

from .errors import QcMergeError

log = logging.getLogger(__name__)

#: 사용자 git 설정(예: core.autocrlf, gc 설정)의 영향을 받지 않도록 항상 붙이는 옵션.
COMMON_CONFIG: tuple[str, ...] = (
    "-c", "core.autocrlf=false",
    "-c", "core.safecrlf=false",
    "-c", "core.symlinks=true",
    "-c", "advice.detachedHead=false",
)


class GitError(QcMergeError):
    """git 명령이 0 이 아닌 종료 코드를 돌려준 경우."""

    def __init__(self, argv: Sequence[str], returncode: int, stderr: str) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            "git 명령 실패 (exit {code}): {cmd}\n{err}".format(
                code=returncode,
                cmd=" ".join(self.argv),
                err=self.stderr or "(stderr 없음)",
            )
        )


class Git:
    """특정 저장소를 대상으로 git 을 실행하는 헬퍼.

    Parameters
    ----------
    git_dir:
        ``--git-dir`` 로 넘길 경로. ``None`` 이면 ``cwd`` 기준으로 git 이 알아서 찾는다.
    work_tree:
        ``--work-tree`` 로 넘길 경로. 제조사 소스 디렉터리를 건드리지 않고
        인덱스만 만들 때 사용한다.
    cwd:
        git 을 실행할 디렉터리.
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

    # ------------------------------------------------------------------ 내부
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
        # 대화형 인증 프롬프트로 멈추지 않도록 한다.
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_ASKPASS", "echo")
        if extra:
            env.update(extra)
        return env

    # ------------------------------------------------------------------ 실행
    def run_bytes(
        self,
        *args: str,
        check: bool = True,
        input_bytes: Optional[bytes] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        """git 을 실행하고 :class:`subprocess.CompletedProcess` 를 돌려준다."""
        argv = self._argv(args)
        log.debug("git 실행: %s (cwd=%s)", " ".join(argv), self.cwd)
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
        """git 을 실행하고 stdout 을 문자열로 돌려준다(끝의 개행 제거)."""
        proc = self.run_bytes(*args, **kwargs)
        return proc.stdout.decode("utf-8", "surrogateescape").rstrip("\n")

    def lines(self, *args: str, **kwargs) -> list[str]:
        """git 출력을 줄 단위 리스트로 돌려준다(빈 줄 제거)."""
        out = self.run(*args, **kwargs)
        return [line for line in out.split("\n") if line]

    def ok(self, *args: str, **kwargs) -> bool:
        """git 명령이 성공했는지만 확인한다."""
        kwargs["check"] = False
        return self.run_bytes(*args, **kwargs).returncode == 0

    # ------------------------------------------------------------- 편의 함수
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
    """설치된 git 버전을 (major, minor, patch) 튜플로 돌려준다."""
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
