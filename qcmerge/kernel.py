"""Read the kernel version out of an OEM source's top-level Makefile."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .errors import QcMergeError

#: Matches the ``VERSION = 5`` style assignments at the top of the Makefile.
_ASSIGN_RE = re.compile(
    r"^\s*(VERSION|PATCHLEVEL|SUBLEVEL)\s*[:?]?=\s*(.*?)\s*$"
)

#: Fields that must be present for the file to count as a kernel Makefile.
_REQUIRED = ("VERSION", "PATCHLEVEL")

#: Last kernel series CLO keeps in its own msm-<series> repository. Everything
#: past it lives in the merged qcom repository. The boundary is written as the
#: last msm series rather than the first qcom one because no long-term support
#: release sits between 5.15 and 6.1.
LAST_MSM_SERIES = (5, 15)


@dataclass(frozen=True)
class KernelVersion:
    """Version fields read from a kernel Makefile."""

    version: int
    patchlevel: int
    sublevel: int = 0

    @property
    def series(self) -> str:
        """The LTS series, such as ``5.4``."""
        return "{0}.{1}".format(self.version, self.patchlevel)

    @property
    def release(self) -> str:
        """The full version, such as ``5.4.210``."""
        return "{0}.{1}.{2}".format(self.version, self.patchlevel, self.sublevel)

    @property
    def key(self) -> tuple:
        return (self.version, self.patchlevel, self.sublevel)

    def uses_qcom_repo(self) -> bool:
        """Whether this version lives in CLO's merged ``qcom`` repository."""
        return (self.version, self.patchlevel) > LAST_MSM_SERIES

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.release


def parse_makefile(text: str) -> Optional[KernelVersion]:
    """Extract the kernel version from Makefile text.

    A kernel Makefile states its version in the first few lines, so only the
    head of the file is scanned. Returns ``None`` when the text does not look
    like a kernel Makefile.
    """
    found: dict = {}
    for line in text.splitlines()[:60]:
        match = _ASSIGN_RE.match(line)
        if not match:
            continue
        found.setdefault(match.group(1), match.group(2))

    if any(key not in found for key in _REQUIRED):
        return None

    def as_int(key: str) -> Optional[int]:
        raw = found.get(key, "")
        # Values are sometimes followed by a comment, so take the leading digits.
        match = re.match(r"^\d+", raw)
        return int(match.group(0)) if match else None

    version = as_int("VERSION")
    patchlevel = as_int("PATCHLEVEL")
    if version is None or patchlevel is None:
        return None

    return KernelVersion(
        version=version,
        patchlevel=patchlevel,
        sublevel=as_int("SUBLEVEL") or 0,
    )


def read_makefile(path: str) -> Optional[KernelVersion]:
    """Read the kernel version from a Makefile path, or ``None`` if absent."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_makefile(handle.read())


def detect(source_dir: str) -> KernelVersion:
    """Determine the kernel version of an OEM source directory.

    Only the top-level ``Makefile`` is considered. OEM releases often nest the
    source one level deeper, so on failure the immediate subdirectories are
    scanned and any candidates are named in the error message.
    """
    if not os.path.isdir(source_dir):
        raise QcMergeError("OEM kernel source path is not a directory: %s" % source_dir)

    kernel_version = read_makefile(os.path.join(source_dir, "Makefile"))
    if kernel_version is not None:
        return kernel_version

    hints = []
    try:
        for entry in sorted(os.listdir(source_dir)):
            candidate = os.path.join(source_dir, entry)
            if os.path.isdir(candidate) and read_makefile(os.path.join(candidate, "Makefile")):
                hints.append(candidate)
    except OSError:
        pass

    message = (
        "could not read a kernel version from %s\n"
        "Point at the top of the kernel source, where the Makefile declares "
        "VERSION and PATCHLEVEL." % os.path.join(source_dir, "Makefile")
    )
    if hints:
        message += "\nThese paths look like kernel sources:\n" + "\n".join(
            "  " + hint for hint in hints
        )
    raise QcMergeError(message)
