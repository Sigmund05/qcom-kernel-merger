"""Helpers that build a fake CLO repository and an OEM source for tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Mapping, Sequence

ENV = dict(os.environ)
ENV.update(
    {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+0000",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
)


def git(*args: str, cwd: str) -> str:
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        env=ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "git {0} failed: {1}".format(" ".join(args), proc.stderr.decode("utf-8", "replace"))
        )
    return proc.stdout.decode("utf-8", "replace").rstrip("\n")


def kernel_makefile(version: int, patchlevel: int, sublevel: int) -> str:
    return (
        "# SPDX-License-Identifier: GPL-2.0\n"
        "VERSION = {0}\n"
        "PATCHLEVEL = {1}\n"
        "SUBLEVEL = {2}\n"
        "NAME = Test Kernel\n"
    ).format(version, patchlevel, sublevel)


def write_tree(root: str, files: Mapping[str, str]) -> None:
    """Empty the directory and leave exactly the given files behind."""
    if os.path.isdir(root):
        for entry in os.listdir(root):
            if entry == ".git":
                continue
            path = os.path.join(root, entry)
            shutil.rmtree(path) if os.path.isdir(path) else os.unlink(path)
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)


def build_clo_fixture(base_dir: str, repo_name: str, snapshots: Sequence) -> str:
    """Build a bare fake CLO repository carrying several tags.

    ``snapshots`` is an ordered sequence of ``(tag name, {path: contents})``.
    Returns the group directory, ready to be passed as ``--clo-base``.
    """
    group_dir = os.path.join(base_dir, "clo")
    work_dir = os.path.join(base_dir, "clo-work", repo_name)
    bare_dir = os.path.join(group_dir, repo_name + ".git")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(group_dir, exist_ok=True)

    git("init", "--quiet", "-b", "main", ".", cwd=work_dir)
    for tag, files in snapshots:
        write_tree(work_dir, files)
        git("add", "-A", ".", cwd=work_dir)
        git("commit", "--quiet", "-m", "snapshot " + tag, cwd=work_dir)
        git("tag", "-a", tag, "-m", "release " + tag, cwd=work_dir)

    git("clone", "--quiet", "--bare", work_dir, bare_dir, cwd=base_dir)
    git("config", "uploadpack.allowFilter", "true", cwd=bare_dir)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare_dir)
    return group_dir


def clo_base_url(group_dir: str) -> str:
    return "file://" + os.path.abspath(group_dir)
