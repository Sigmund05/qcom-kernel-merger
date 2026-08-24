"""Flatten git trees into "path -> blob hash" maps and score their similarity.

Paths are kept as bytes to avoid decoding costs and non-UTF-8 path trouble.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence

#: A mapping of path to object hash (hex, as bytes).
TreeMap = "dict[bytes, bytes]"


def parse_ls_tree_z(data: bytes) -> dict:
    """Parse ``git ls-tree -r -z`` output.

    Each record is ``<mode> SP <type> SP <sha> TAB <path> NUL``.
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
    """Parse a raw git tree object into ``name -> hex hash``.

    Each entry is ``<mode> SP <name> NUL <20 byte sha>``.
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
    """Walk a ``git cat-file --batch`` output stream in order.

    Each response is either ``<oid> SP <type> SP <size> LF <contents> LF`` or,
    for an object that is not there, ``<input> SP missing LF``. Responses come
    back in input order, so ``(type, payload)`` is yielded in that same order,
    with ``(None, b"")`` standing in for a missing object.
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
        pos += length + 1  # skip the newline after the contents
        yield (obj_type, payload)


@dataclass(frozen=True)
class TagScore:
    """The result of comparing the OEM tree against one CLO tag."""

    tag: str
    matched: int      # same path, same contents
    modified: int     # same path, different contents
    only_vendor: int  # present only in the OEM source
    only_tag: int     # present only in the tag

    @property
    def vendor_total(self) -> int:
        return self.matched + self.modified + self.only_vendor

    @property
    def tag_total(self) -> int:
        return self.matched + self.modified + self.only_tag

    @property
    def union(self) -> int:
        """Number of distinct paths appearing in either tree."""
        return self.vendor_total + self.only_tag

    @property
    def score(self) -> float:
        """Jaccard index: identical files over total paths, 0.0 to 1.0."""
        return (self.matched / self.union) if self.union else 0.0

    @property
    def changed(self) -> int:
        """Files that make up the OEM's changes: modified, added and removed."""
        return self.modified + self.only_vendor + self.only_tag

    def summary(self) -> str:
        return (
            "{tag}  score {score:.4f}  same {matched}  modified {modified}  "
            "oem only {ov}  tag only {ot}".format(
                tag=self.tag,
                score=self.score,
                matched=self.matched,
                modified=self.modified,
                ov=self.only_vendor,
                ot=self.only_tag,
            )
        )


def score_maps(tag: str, vendor: Mapping, candidate: Mapping) -> TagScore:
    """Compare the OEM tree against a candidate tree into a :class:`TagScore`."""
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
    """Sort by descending score, then identical files, then tag name."""
    return sorted(scores, key=lambda s: (-s.score, -s.matched, s.tag))
