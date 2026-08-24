"""CodeLinaro(CLO) 커널 저장소 주소 계산과 태그 목록 조회."""

from __future__ import annotations

import fnmatch
import logging
from typing import Iterable, Optional, Sequence

from .errors import QcMergeError
from .gitcmd import Git
from .kernel import KernelVersion

log = logging.getLogger(__name__)

#: CLO 커널 저장소들이 모여 있는 그룹 주소.
DEFAULT_CLO_BASE = "https://git.codelinaro.org/clo/la/kernel"


def repo_name(kernel_version: KernelVersion) -> str:
    """커널 버전에 맞는 CLO 저장소 이름.

    * 6.1 미만: ``msm-3.18``, ``msm-4.9``, ``msm-5.4`` 처럼 계열별 저장소
    * 6.1 이상: 계열이 통합된 ``qcom`` 저장소
    """
    if kernel_version.uses_qcom_repo():
        return "qcom"
    return "msm-" + kernel_version.series


def repo_url(kernel_version: KernelVersion, base: str = DEFAULT_CLO_BASE) -> str:
    """커널 버전에 맞는 CLO 저장소 URL."""
    return "{base}/{name}.git".format(base=base.rstrip("/"), name=repo_name(kernel_version))


def parse_ls_remote(output: str) -> list[str]:
    """``git ls-remote --tags`` 출력에서 태그 이름 목록을 뽑는다.

    annotated 태그는 ``refs/tags/<name>`` 과 역참조된 ``refs/tags/<name>^{}`` 가
    같이 나오므로 중복을 제거한다.
    """
    names: list[str] = []
    seen: set = set()
    for line in output.splitlines():
        if "\t" not in line:
            continue
        ref = line.split("\t", 1)[1].strip()
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            name = name[: -len("^{}")]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def list_remote_tags(url: str, timeout: Optional[float] = None) -> list[str]:
    """원격 저장소의 태그 이름을 전부 가져온다."""
    git = Git()
    log.info("CLO 태그 목록 조회: %s", url)
    proc = git.run_bytes("ls-remote", "--tags", url, check=False, timeout=timeout)
    if proc.returncode != 0:
        raise QcMergeError(
            "CLO 저장소의 태그 목록을 가져오지 못했습니다: {url}\n{err}".format(
                url=url, err=proc.stderr.decode("utf-8", "replace").strip()
            )
        )
    tags = parse_ls_remote(proc.stdout.decode("utf-8", "surrogateescape"))
    log.info("태그 %d 개 확인", len(tags))
    return tags


def filter_tags(tags: Sequence[str], patterns: Optional[Iterable[str]] = None) -> list[str]:
    """glob 패턴으로 태그 후보를 좁힌다. 패턴이 없으면 그대로 돌려준다."""
    patterns = [p for p in (patterns or []) if p]
    if not patterns:
        return list(tags)
    selected = [tag for tag in tags if any(fnmatch.fnmatch(tag, p) for p in patterns)]
    log.info("패턴 %s 적용: 태그 %d -> %d 개", patterns, len(tags), len(selected))
    return selected
