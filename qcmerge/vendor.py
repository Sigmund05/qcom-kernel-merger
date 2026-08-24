"""제조사 커널 소스를 git 인덱스에 담아 트리 오브젝트로 만든다.

제조사 소스 디렉터리 자체는 건드리지 않는다. ``--work-tree`` 로만 참조하고
인덱스 파일은 작업용 저장소 안에 따로 만든다.
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
    """제조사 소스를 담은 트리 오브젝트와 그 내용."""

    tree: str
    files: dict = field(repr=False)
    top_level: dict = field(repr=False)

    @property
    def file_count(self) -> int:
        return len(self.files)


def _check_source(source_dir: str) -> None:
    if not os.path.isdir(source_dir):
        raise QcMergeError("제조사 커널 소스 경로가 디렉터리가 아닙니다: %s" % source_dir)
    dot_git = os.path.join(source_dir, ".git")
    if os.path.exists(dot_git):
        raise QcMergeError(
            "제조사 소스 안에 .git 이 있습니다: %s\n"
            "그대로 두면 git 이 서브모듈(gitlink)로 취급해 비교가 어긋납니다. "
            "옮기거나 이름을 바꾼 뒤 다시 실행하세요." % dot_git
        )


def index_source(repo: Git, source_dir: str, index_file: str) -> VendorTree:
    """제조사 소스를 인덱스에 추가하고 트리 오브젝트를 만든다.

    Parameters
    ----------
    repo:
        오브젝트를 기록할 저장소를 가리키는 :class:`~qcmerge.gitcmd.Git`.
    source_dir:
        제조사 커널 소스 최상위 디렉터리.
    index_file:
        사용할 인덱스 파일 경로. 이미 있으면 지우고 새로 만든다.

    소스 안의 ``.gitignore`` 는 그대로 적용된다. 커널 트리의 ``.gitignore``
    는 빌드 산출물만 제외하므로, CLO 태그와 같은 기준으로 비교된다.
    """
    _check_source(source_dir)

    source_dir = os.path.abspath(source_dir)
    if os.path.exists(index_file):
        os.unlink(index_file)
    os.makedirs(os.path.dirname(index_file) or ".", exist_ok=True)

    git = Git(git_dir=repo.git_dir, work_tree=source_dir, cwd=source_dir)
    env = {"GIT_INDEX_FILE": os.path.abspath(index_file)}

    log.info("제조사 소스를 인덱스에 추가하는 중: %s", source_dir)
    git.run_bytes("add", "-A", ".", env=env)

    tree = git.run("write-tree", env=env)
    log.info("제조사 트리 생성: %s", tree)

    files = parse_ls_tree_z(git.run_bytes("ls-tree", "-r", "-z", tree, env=env).stdout)
    top_level = _top_level_of(git, tree, env)
    log.info("제조사 소스 파일 %d 개", len(files))
    if not files:
        raise QcMergeError(
            "제조사 소스에서 추가된 파일이 없습니다: %s\n"
            "경로가 맞는지, .gitignore 가 전부 제외하고 있지 않은지 확인하세요." % source_dir
        )
    return VendorTree(tree=tree, files=files, top_level=top_level)


def _top_level_of(git: Git, tree: str, env: dict) -> dict:
    """트리의 최상위 항목만 ``이름 -> 해시`` 로 돌려준다."""
    raw = git.run_bytes("cat-file", "tree", tree, env=env).stdout
    return parse_tree_object(raw)
