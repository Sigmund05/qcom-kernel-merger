"""Stage the OEM kernel source into a git index and write it out as a tree.

The OEM source directory itself is never written to. It is referenced through
``--work-tree`` only, and the index file lives inside the working repository.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .errors import QcMergeError
from .gitcmd import Git
from .treemap import parse_ls_tree_z, parse_tree_object

log = logging.getLogger(__name__)


@dataclass
class VendorTree:
    """A tree object holding the OEM source, and its contents."""

    tree: str
    files: dict = field(repr=False)
    top_level: dict = field(repr=False)

    @property
    def file_count(self) -> int:
        return len(self.files)


def _check_source(source_dir: str) -> None:
    if not os.path.isdir(source_dir):
        raise QcMergeError("OEM kernel source path is not a directory: %s" % source_dir)
    dot_git = os.path.join(source_dir, ".git")
    if os.path.exists(dot_git):
        raise QcMergeError(
            "the OEM source contains a .git: %s\n"
            "Left in place, git records it as a submodule (gitlink) and the "
            "comparison goes wrong. Move or rename it and run again." % dot_git
        )


def index_source(repo: Git, source_dir: str, index_file: str) -> VendorTree:
    """Add the OEM source to an index and write it out as a tree object.

    Parameters
    ----------
    repo:
        :class:`~qcmerge.gitcmd.Git` for the repository that stores the objects.
    source_dir:
        Top-level directory of the OEM kernel source.
    index_file:
        Index file to use. An existing one is removed first.

    ``.gitignore`` files in the source apply as usual. A kernel tree's
    ``.gitignore`` only excludes build output, so the comparison against CLO
    tags stays on the same footing.
    """
    _check_source(source_dir)

    source_dir = os.path.abspath(source_dir)
    if os.path.exists(index_file):
        os.unlink(index_file)
    os.makedirs(os.path.dirname(index_file) or ".", exist_ok=True)

    git = Git(git_dir=repo.git_dir, work_tree=source_dir, cwd=source_dir)
    env = {"GIT_INDEX_FILE": os.path.abspath(index_file)}

    log.info("adding the OEM source to an index: %s", source_dir)
    git.run_bytes("add", "-A", ".", env=env)

    tree = git.run("write-tree", env=env)
    log.info("wrote OEM tree %s", tree)

    files = parse_ls_tree_z(git.run_bytes("ls-tree", "-r", "-z", tree, env=env).stdout)
    top_level = _top_level_of(git, tree, env)
    log.info("OEM source holds %d files", len(files))
    if not files:
        raise QcMergeError(
            "no files were added from the OEM source: %s\n"
            "Check the path, and that .gitignore is not excluding everything."
            % source_dir
        )
    return VendorTree(tree=tree, files=files, top_level=top_level)


def _top_level_of(git: Git, tree: str, env: dict) -> dict:
    """Return only the top-level entries of a tree as ``name -> hash``."""
    raw = git.run_bytes("cat-file", "tree", tree, env=env).stdout
    return parse_tree_object(raw)
