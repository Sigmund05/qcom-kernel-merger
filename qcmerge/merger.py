"""Build the result repository.

The closest CLO tag goes in as the first commit, and the whole OEM source tree
is laid on top of it as a single commit. In the resulting repository,
``git diff <tag>..<branch>`` is exactly the OEM's changes.
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

#: Author identity used when the repository has none configured.
FALLBACK_IDENTITY = {
    "GIT_AUTHOR_NAME": "qcom-kernel-merger",
    "GIT_AUTHOR_EMAIL": "qcom-kernel-merger@localhost",
    "GIT_COMMITTER_NAME": "qcom-kernel-merger",
    "GIT_COMMITTER_EMAIL": "qcom-kernel-merger@localhost",
}


@dataclass
class MergeResult:
    """What came out of building the result repository."""

    out_dir: str
    branch: str
    base_tag: str
    base_commit: str
    commit: str
    score: TagScore


def prepare_output_repo(out_dir: str, url: str, branch: str) -> Git:
    """Initialise the result repository and register the CLO remote."""
    out_dir = os.path.abspath(out_dir)
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise QcMergeError(
            "the output path is not empty: %s\n"
            "Pick another path, or clear the existing directory and run again."
            % out_dir
        )
    os.makedirs(out_dir, exist_ok=True)

    git = Git(git_dir=os.path.join(out_dir, ".git"), work_tree=out_dir, cwd=out_dir)
    Git(cwd=out_dir).run("init", "--quiet", "-b", branch, out_dir)
    git.run("remote", "add", "origin", url)
    git.config_set("gc.auto", "0")
    log.info("initialised the result repository: %s", out_dir)
    return git


def fetch_base_tag(git: Git, tag: str, depth: int = 1) -> str:
    """Fetch the base tag with its blobs and return its commit hash."""
    args = ["fetch", "--no-tags", "--no-write-fetch-head", "--quiet"]
    if depth > 0:
        args.append("--depth=%d" % depth)
    args += ["origin", "+refs/tags/{0}:refs/tags/{0}".format(tag)]
    log.info("fetching the base tag: %s", tag)
    git.run(*args)
    commit = git.rev_parse("refs/tags/{0}^{{commit}}".format(tag))
    if not commit:
        raise QcMergeError("could not resolve the commit of the base tag: %s" % tag)
    return commit


def _commit_env(git: Git) -> dict:
    """Supply a fallback identity when user.name/user.email are unset."""
    if git.config_get("user.email") and git.config_get("user.name"):
        return {}
    return dict(FALLBACK_IDENTITY)


def build_commit_message(
    source_dir: str,
    kernel_version: KernelVersion,
    repo_url_value: str,
    score: TagScore,
) -> str:
    """Compose the default message for the OEM source commit.

    It stays in a kernel repository, so it is written in English.
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
    """Commit the OEM tree on top of the base commit and point the branch at it."""
    env = _commit_env(git)
    commit = git.run(
        "commit-tree", tree, "-p", parent, "-m", message, env=env
    )
    git.run("update-ref", "refs/heads/" + branch, commit)
    git.run("symbolic-ref", "HEAD", "refs/heads/" + branch)
    log.info("created the OEM source commit: %s", commit)
    return commit


def checkout_result(git: Git, branch: str) -> None:
    """Materialise the source in the result repository's work tree."""
    log.info("checking the source out into the work tree")
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
    """Finish building the result repository."""
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
