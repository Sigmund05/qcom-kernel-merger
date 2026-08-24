"""CodeLinaro (CLO) repository addressing and tag listing."""

from __future__ import annotations

import fnmatch
import logging
from typing import Iterable, Optional, Sequence

from .errors import QcMergeError
from .gitcmd import Git
from .kernel import KernelVersion

log = logging.getLogger(__name__)

#: Group that holds CLO's kernel repositories.
DEFAULT_CLO_BASE = "https://git.codelinaro.org/clo/la/kernel"


def repo_name(kernel_version: KernelVersion) -> str:
    """Name of the CLO repository holding this kernel version.

    * below 6.1: one repository per series, ``msm-3.18``, ``msm-4.9``,
      ``msm-5.4`` and so on
    * 6.1 and up: the merged ``qcom`` repository
    """
    if kernel_version.uses_qcom_repo():
        return "qcom"
    return "msm-" + kernel_version.series


def repo_url(kernel_version: KernelVersion, base: str = DEFAULT_CLO_BASE) -> str:
    """URL of the CLO repository holding this kernel version."""
    return "{base}/{name}.git".format(base=base.rstrip("/"), name=repo_name(kernel_version))


def parse_ls_remote(output: str) -> list[str]:
    """Pull tag names out of ``git ls-remote --tags`` output.

    An annotated tag appears twice, as ``refs/tags/<name>`` and as the peeled
    ``refs/tags/<name>^{}``, so duplicates are dropped.
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
    """Fetch every tag name from the remote repository."""
    git = Git()
    log.info("listing CLO tags: %s", url)
    proc = git.run_bytes("ls-remote", "--tags", url, check=False, timeout=timeout)
    if proc.returncode != 0:
        raise QcMergeError(
            "could not list tags of the CLO repository: {url}\n{err}".format(
                url=url, err=proc.stderr.decode("utf-8", "replace").strip()
            )
        )
    tags = parse_ls_remote(proc.stdout.decode("utf-8", "surrogateescape"))
    log.info("found %d tags", len(tags))
    return tags


def filter_tags(tags: Sequence[str], patterns: Optional[Iterable[str]] = None) -> list[str]:
    """Narrow the candidate tags with glob patterns, or keep them all."""
    patterns = [p for p in (patterns or []) if p]
    if not patterns:
        return list(tags)
    selected = [tag for tag in tags if any(fnmatch.fnmatch(tag, p) for p in patterns)]
    log.info("patterns %s applied: %d -> %d tags", patterns, len(tags), len(selected))
    return selected
