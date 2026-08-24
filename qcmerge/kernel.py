"""제조사 커널 소스의 커널 버전을 최상위 Makefile 에서 읽어온다."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .errors import QcMergeError

#: Makefile 최상단의 ``VERSION = 5`` 형태를 잡아내는 정규식.
_ASSIGN_RE = re.compile(
    r"^\s*(VERSION|PATCHLEVEL|SUBLEVEL|EXTRAVERSION|NAME)\s*[:?]?=\s*(.*?)\s*$"
)

#: 커널 Makefile 인지 판별할 때 요구하는 항목.
_REQUIRED = ("VERSION", "PATCHLEVEL")

#: 이 값 이상이면 CLO 의 통합 저장소(qcom)를, 미만이면 msm-<버전> 저장소를 쓴다.
QCOM_REPO_MIN = (6, 1)


@dataclass(frozen=True)
class KernelVersion:
    """커널 Makefile 에서 읽은 버전 정보."""

    version: int
    patchlevel: int
    sublevel: int = 0
    extraversion: str = ""

    @property
    def series(self) -> str:
        """``5.4`` 처럼 LTS 계열을 나타내는 문자열."""
        return "{0}.{1}".format(self.version, self.patchlevel)

    @property
    def release(self) -> str:
        """``5.4.210`` 처럼 EXTRAVERSION 을 제외한 전체 버전 문자열."""
        return "{0}.{1}.{2}".format(self.version, self.patchlevel, self.sublevel)

    @property
    def full(self) -> str:
        """EXTRAVERSION 까지 붙인 문자열."""
        return self.release + self.extraversion

    @property
    def key(self) -> tuple:
        return (self.version, self.patchlevel, self.sublevel)

    def uses_qcom_repo(self) -> bool:
        """6.1 이상이라 CLO 의 ``qcom`` 저장소를 써야 하는지 여부."""
        return (self.version, self.patchlevel) >= QCOM_REPO_MIN

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return self.full


def parse_makefile(text: str) -> Optional[KernelVersion]:
    """Makefile 내용에서 커널 버전을 뽑아낸다.

    커널 Makefile 은 최상단 몇 줄에 버전을 적어두므로 앞쪽만 훑어도 충분하다.
    커널 Makefile 로 보이지 않으면 ``None`` 을 돌려준다.
    """
    found: dict = {}
    for line in text.splitlines()[:60]:
        match = _ASSIGN_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if key == "NAME":
            continue
        found.setdefault(key, value)

    if any(key not in found for key in _REQUIRED):
        return None

    def as_int(key: str) -> Optional[int]:
        raw = found.get(key, "")
        # ``SUBLEVEL = 0`` 처럼 주석이 붙는 경우가 있어 앞쪽 숫자만 취한다.
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
        extraversion=found.get("EXTRAVERSION", ""),
    )


def read_makefile(path: str) -> Optional[KernelVersion]:
    """Makefile 경로를 받아 커널 버전을 읽는다. 없으면 ``None``."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_makefile(handle.read())


def detect(source_dir: str) -> KernelVersion:
    """제조사 커널 소스 디렉터리에서 커널 버전을 알아낸다.

    최상위 ``Makefile`` 만 본다. 제조사 배포본이 한 단계 더 들어가 있는 경우가
    흔해서, 실패하면 바로 아래 디렉터리에서 후보를 찾아 안내 메시지에 담는다.
    """
    if not os.path.isdir(source_dir):
        raise QcMergeError("제조사 커널 소스 경로가 디렉터리가 아닙니다: %s" % source_dir)

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
        "커널 Makefile 에서 버전을 읽지 못했습니다: %s\n"
        "커널 소스 최상위 디렉터리(VERSION/PATCHLEVEL 이 적힌 Makefile 이 있는 곳)를 지정하세요."
        % os.path.join(source_dir, "Makefile")
    )
    if hints:
        message += "\n아래 경로가 커널 소스로 보입니다:\n" + "\n".join("  " + h for h in hints)
    raise QcMergeError(message)
