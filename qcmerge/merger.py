"""결과 저장소를 만든다.

가장 가까운 CLO 태그를 첫 커밋으로 두고, 그 위에 제조사 소스 트리를 통째로
얹은 커밋을 하나 만든다. 결과 저장소에서 ``git diff <태그>..<브랜치>`` 가
곧 제조사 변경점이 된다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from .errors import QcMergeError
from .gitcmd import Git
from .kernel import KernelVersion
from .treemap import TagScore

log = logging.getLogger(__name__)

#: 커밋 작성자 정보가 없을 때 쓰는 기본값.
FALLBACK_IDENTITY = {
    "GIT_AUTHOR_NAME": "qcom-kernel-merger",
    "GIT_AUTHOR_EMAIL": "qcom-kernel-merger@localhost",
    "GIT_COMMITTER_NAME": "qcom-kernel-merger",
    "GIT_COMMITTER_EMAIL": "qcom-kernel-merger@localhost",
}


@dataclass
class MergeResult:
    """결과 저장소를 만든 뒤의 정보."""

    out_dir: str
    branch: str
    base_tag: str
    base_commit: str
    commit: str
    score: TagScore


def prepare_output_repo(out_dir: str, url: str, branch: str) -> Git:
    """결과 저장소를 초기화하고 CLO 원격을 등록한다."""
    out_dir = os.path.abspath(out_dir)
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise QcMergeError(
            "결과 저장소 경로가 비어 있지 않습니다: %s\n"
            "다른 경로를 지정하거나 기존 디렉터리를 정리한 뒤 다시 실행하세요." % out_dir
        )
    os.makedirs(out_dir, exist_ok=True)

    git = Git(git_dir=os.path.join(out_dir, ".git"), work_tree=out_dir, cwd=out_dir)
    Git(cwd=out_dir).run("init", "--quiet", "-b", branch, out_dir)
    git.run("remote", "add", "origin", url)
    git.config_set("gc.auto", "0")
    log.info("결과 저장소 초기화: %s", out_dir)
    return git


def fetch_base_tag(git: Git, tag: str, depth: int = 1) -> str:
    """기준이 될 태그를 blob 까지 포함해 받아온다. 커밋 해시를 돌려준다."""
    args = ["fetch", "--no-tags", "--no-write-fetch-head", "--quiet"]
    if depth > 0:
        args.append("--depth=%d" % depth)
    args += ["origin", "+refs/tags/{0}:refs/tags/{0}".format(tag)]
    log.info("기준 태그 받아오는 중: %s", tag)
    git.run(*args)
    commit = git.rev_parse("refs/tags/{0}^{{commit}}".format(tag))
    if not commit:
        raise QcMergeError("기준 태그의 커밋을 찾지 못했습니다: %s" % tag)
    return commit


def _commit_env(git: Git) -> dict:
    """user.name/user.email 이 없으면 기본 작성자 정보를 채워 준다."""
    if git.config_get("user.email") and git.config_get("user.name"):
        return {}
    return dict(FALLBACK_IDENTITY)


def build_commit_message(
    source_dir: str,
    kernel_version: KernelVersion,
    repo_url_value: str,
    score: TagScore,
) -> str:
    """제조사 소스 커밋에 붙일 기본 메시지를 만든다.

    커널 저장소에 그대로 남는 메시지라 영어로 쓴다.
    """
    name = os.path.basename(os.path.normpath(source_dir)) or "vendor"
    return (
        "vendor: import {name} kernel source\n"
        "\n"
        "Imported an OEM kernel release on top of the closest CodeLinaro tag.\n"
        "\n"
        "Kernel version: {version}\n"
        "CLO repository: {repo}\n"
        "Base tag: {tag}\n"
        "Source: {source}\n"
        "Similarity: {score:.4f} ({matched} identical of {union} paths)\n"
        "Changes vs base: {modified} modified, {added} added, {removed} removed\n".format(
            name=name,
            version=kernel_version.full,
            repo=repo_url_value,
            tag=score.tag,
            source=os.path.abspath(source_dir),
            score=score.score,
            matched=score.matched,
            union=score.union,
            modified=score.modified,
            added=score.only_vendor,
            removed=score.only_tag,
        )
    )


def commit_vendor_tree(git: Git, tree: str, parent: str, message: str, branch: str) -> str:
    """제조사 트리를 기준 커밋 위에 얹은 커밋을 만들고 브랜치를 옮긴다."""
    env = _commit_env(git)
    commit = git.run(
        "commit-tree", tree, "-p", parent, "-m", message, env=env
    )
    git.run("update-ref", "refs/heads/" + branch, commit)
    git.run("symbolic-ref", "HEAD", "refs/heads/" + branch)
    log.info("제조사 소스 커밋 생성: %s", commit)
    return commit


def checkout_result(git: Git, branch: str) -> None:
    """결과 저장소의 작업 트리에 소스를 펼친다."""
    log.info("작업 트리에 소스를 펼치는 중")
    git.run("reset", "--hard", "--quiet", "refs/heads/" + branch)


def merge(
    out_dir: str,
    url: str,
    branch: str,
    tag: str,
    tree: str,
    message: str,
    score: TagScore,
    git: Optional[Git] = None,
    depth: int = 1,
    checkout: bool = True,
) -> MergeResult:
    """결과 저장소를 완성한다."""
    if git is None:
        git = prepare_output_repo(out_dir, url, branch)
    base_commit = fetch_base_tag(git, tag, depth=depth)
    commit = commit_vendor_tree(git, tree, base_commit, message, branch)
    if checkout:
        checkout_result(git, branch)
    return MergeResult(
        out_dir=os.path.abspath(out_dir),
        branch=branch,
        base_tag=tag,
        base_commit=base_commit,
        commit=commit,
        score=score,
    )
