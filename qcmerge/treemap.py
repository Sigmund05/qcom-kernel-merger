"""git 트리를 "경로 -> blob 해시" 로 펼치고, 두 트리의 유사도를 계산한다.

경로는 디코딩 비용과 비 UTF-8 경로 문제를 피하려고 bytes 그대로 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence

#: 경로 -> 오브젝트 해시(hex, bytes) 매핑.
TreeMap = "dict[bytes, bytes]"


def parse_ls_tree_z(data: bytes) -> dict:
    """``git ls-tree -r -z`` 출력을 파싱한다.

    레코드 형식은 ``<mode> SP <type> SP <sha> TAB <path> NUL`` 이다.
    """
    result: dict = {}
    for record in data.split(b"\x00"):
        if not record:
            continue
        head, tab, path = record.partition(b"\t")
        if not tab:
            continue
        result[path] = head[-40:]
    return result


def parse_tree_object(raw: bytes) -> dict:
    """git 트리 오브젝트 원본을 ``이름 -> hex 해시`` 로 파싱한다.

    항목 형식은 ``<mode> SP <name> NUL <20 byte sha>`` 이다.
    """
    result: dict = {}
    pos = 0
    size = len(raw)
    while pos < size:
        space = raw.find(b" ", pos)
        if space < 0:
            break
        nul = raw.find(b"\x00", space + 1)
        if nul < 0:
            break
        name = raw[space + 1 : nul]
        sha = raw[nul + 1 : nul + 21]
        if len(sha) < 20:
            break
        result[name] = sha.hex().encode("ascii")
        pos = nul + 21
    return result


def iter_batch_objects(data: bytes) -> Iterator:
    """``git cat-file --batch`` 출력 스트림을 순서대로 훑는다.

    각 응답은 ``<oid> SP <type> SP <size> LF <내용> LF`` 이거나,
    없는 오브젝트면 ``<입력> SP missing LF`` 이다.
    입력 순서와 출력 순서가 같으므로 ``(type, payload)`` 를 순서대로 내보낸다.
    ``missing`` 인 경우 ``(None, b"")`` 를 내보낸다.
    """
    pos = 0
    size = len(data)
    while pos < size:
        eol = data.find(b"\n", pos)
        if eol < 0:
            break
        header = data[pos:eol]
        pos = eol + 1
        fields = header.split(b" ")
        if len(fields) < 3 or fields[-2] in (b"missing", b"ambiguous"):
            yield (None, b"")
            continue
        obj_type = fields[1].decode("ascii", "replace")
        try:
            length = int(fields[2])
        except ValueError:
            yield (None, b"")
            continue
        payload = data[pos : pos + length]
        pos += length + 1  # 내용 뒤의 개행 한 칸
        yield (obj_type, payload)


@dataclass(frozen=True)
class TagScore:
    """제조사 트리와 CLO 태그 하나를 비교한 결과."""

    tag: str
    matched: int      # 경로와 내용이 모두 같은 파일 수
    modified: int     # 경로는 같지만 내용이 다른 파일 수
    only_vendor: int  # 제조사 소스에만 있는 파일 수
    only_tag: int     # 태그에만 있는 파일 수

    @property
    def vendor_total(self) -> int:
        return self.matched + self.modified + self.only_vendor

    @property
    def tag_total(self) -> int:
        return self.matched + self.modified + self.only_tag

    @property
    def union(self) -> int:
        """두 트리에 등장하는 서로 다른 경로의 총 개수."""
        return self.vendor_total + self.only_tag

    @property
    def score(self) -> float:
        """자카드 유사도: 같은 파일 수 / 전체 경로 수 (0.0 ~ 1.0)."""
        return (self.matched / self.union) if self.union else 0.0

    @property
    def changed(self) -> int:
        """제조사 변경점으로 볼 수 있는 파일 수(추가+수정+삭제)."""
        return self.modified + self.only_vendor + self.only_tag

    def summary(self) -> str:
        return (
            "{tag}  유사도 {score:.4f}  일치 {matched}  수정 {modified}  "
            "제조사만 {ov}  태그만 {ot}".format(
                tag=self.tag,
                score=self.score,
                matched=self.matched,
                modified=self.modified,
                ov=self.only_vendor,
                ot=self.only_tag,
            )
        )


def score_maps(tag: str, vendor: Mapping, candidate: Mapping) -> TagScore:
    """제조사 트리와 후보 트리를 비교해 :class:`TagScore` 를 만든다."""
    matched = 0
    modified = 0
    only_vendor = 0
    get = candidate.get
    for path, sha in vendor.items():
        other = get(path)
        if other is None:
            only_vendor += 1
        elif other == sha:
            matched += 1
        else:
            modified += 1
    only_tag = len(candidate) - (matched + modified)
    return TagScore(
        tag=tag,
        matched=matched,
        modified=modified,
        only_vendor=only_vendor,
        only_tag=only_tag,
    )


def rank(scores: Sequence[TagScore]) -> list:
    """유사도가 높은 순으로 정렬한다. 동점이면 일치 파일 수, 태그 이름 순."""
    return sorted(scores, key=lambda s: (-s.score, -s.matched, s.tag))
