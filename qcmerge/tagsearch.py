"""CLO 태그들을 받아와 제조사 소스와 가장 가까운 태그를 찾는다.

blob 은 받지 않는(``--filter=blob:none``) 부분 클론으로 태그의 트리 오브젝트만
받아온다. 트리에 적힌 blob 해시만 있으면 파일 내용이 같은지 판별할 수 있으므로
수 GB 짜리 blob 을 내려받지 않아도 유사도를 계산할 수 있다.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Sequence

from .errors import QcMergeError
from .gitcmd import Git, GitError
from .treemap import TagScore, iter_batch_objects, parse_ls_tree_z, parse_tree_object, rank, score_maps

log = logging.getLogger(__name__)

#: 한 번의 fetch 로 요청할 태그 수. 명령줄 길이와 서버 부담 사이의 절충값.
DEFAULT_BATCH_SIZE = 200

#: 1차 선별에서 남길 태그 수. 동점인 태그는 이 수를 넘겨도 모두 남긴다.
DEFAULT_PREFILTER_KEEP = 150


class TagSearcher:
    """태그 캐시 저장소를 관리하며 유사도 계산을 수행한다."""

    def __init__(
        self,
        cache_dir: str,
        url: str,
        jobs: int = 4,
        batch_size: int = DEFAULT_BATCH_SIZE,
        fetch_retries: int = 3,
    ) -> None:
        self.cache_dir = os.path.abspath(cache_dir)
        self.url = url
        self.jobs = max(1, jobs)
        self.batch_size = max(1, batch_size)
        self.fetch_retries = max(0, fetch_retries)
        self.git = Git(git_dir=self.cache_dir, cwd=self.cache_dir)

    # ------------------------------------------------------------ 저장소 준비
    def prepare(self) -> None:
        """부분 클론 설정이 된 bare 캐시 저장소를 준비한다."""
        if not os.path.exists(os.path.join(self.cache_dir, "HEAD")):
            os.makedirs(self.cache_dir, exist_ok=True)
            log.info("태그 캐시 저장소 생성: %s", self.cache_dir)
            Git(cwd=self.cache_dir).run("init", "--bare", "--quiet", self.cache_dir)

        git = self.git
        # extensions.* 를 쓰려면 저장소 포맷이 1 이어야 한다.
        git.config_set("core.repositoryformatversion", "1")
        git.config_set("extensions.partialClone", "origin")
        if git.config_get("remote.origin.url") is None:
            git.run("remote", "add", "origin", self.url)
        else:
            git.run("remote", "set-url", "origin", self.url)
        git.config_set("remote.origin.promisor", "true")
        git.config_set("remote.origin.partialclonefilter", "blob:none")
        # 태그 수천 개를 받는 동안 자동 gc 가 끼어들지 않게 한다.
        git.config_set("gc.auto", "0")

    def local_tags(self) -> set:
        """캐시에 이미 받아둔 태그 이름."""
        return set(self.git.lines("for-each-ref", "--format=%(refname:short)", "refs/tags"))

    # ------------------------------------------------------------------ 받기
    def fetch_tags(
        self,
        tags: Sequence[str],
        progress: Optional[Callable] = None,
    ) -> list:
        """아직 없는 태그의 커밋/트리 오브젝트를 받아온다.

        ``progress`` 는 ``(단계 이름, 진행 수, 전체 수)`` 로 호출된다.

        Returns
        -------
        받아오지 못한 태그 이름 목록.
        """
        have = self.local_tags()
        missing = [tag for tag in tags if tag not in have]
        if not missing:
            log.info("태그 %d 개 모두 캐시에 있음", len(tags))
            return []

        log.info("태그 %d 개 중 %d 개를 새로 받아옵니다", len(tags), len(missing))
        failed: list = []
        done = 0
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            if not self._fetch_batch(batch):
                failed.extend(batch)
            done += len(batch)
            if progress:
                progress("태그 받는 중", done, len(missing))
        if failed:
            log.warning("태그 %d 개는 받아오지 못했습니다", len(failed))
        return failed

    def _fetch_batch(self, batch: Sequence[str]) -> bool:
        refspecs = ["+refs/tags/{0}:refs/tags/{0}".format(tag) for tag in batch]
        args = [
            "fetch",
            "--filter=blob:none",
            "--depth=1",
            "--no-tags",
            "--no-write-fetch-head",
            "--quiet",
            "origin",
        ] + refspecs

        delay = 2.0
        for attempt in range(self.fetch_retries + 1):
            proc = self.git.run_bytes(*args, check=False)
            if proc.returncode == 0:
                return True
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            log.warning(
                "태그 fetch 실패(%d/%d): %s",
                attempt + 1,
                self.fetch_retries + 1,
                stderr.splitlines()[-1] if stderr else "(stderr 없음)",
            )
            if attempt < self.fetch_retries:
                time.sleep(delay)
                delay *= 2
        return False

    # --------------------------------------------------------------- 1차 선별
    def top_level_maps(self, tags: Sequence[str]) -> dict:
        """태그별 최상위 트리 항목을 한 번의 git 호출로 모아 온다."""
        if not tags:
            return {}
        request = "".join("{0}^{{tree}}\n".format(tag) for tag in tags).encode(
            "utf-8", "surrogateescape"
        )
        proc = self.git.run_bytes("cat-file", "--batch", input_bytes=request, check=False)
        result: dict = {}
        for tag, (obj_type, payload) in zip(tags, iter_batch_objects(proc.stdout)):
            if obj_type == "tree":
                result[tag] = parse_tree_object(payload)
        return result

    def prefilter(
        self,
        tags: Sequence[str],
        vendor_top_level: dict,
        keep: int = DEFAULT_PREFILTER_KEEP,
    ) -> list:
        """최상위 트리 항목만 비교해 후보를 빠르게 줄인다.

        최상위 디렉터리의 트리 해시는 그 아래가 통째로 같을 때만 일치하므로,
        제조사가 손대지 않은 디렉터리가 얼마나 남아 있는지를 싸게 잴 수 있다.
        동점인 태그는 잘라내지 않는다.
        """
        if not tags or not vendor_top_level or keep <= 0 or len(tags) <= keep:
            return list(tags)

        maps = self.top_level_maps(tags)
        scored = []
        for tag in tags:
            entries = maps.get(tag)
            if entries is None:
                continue
            hits = sum(1 for name, sha in entries.items() if vendor_top_level.get(name) == sha)
            scored.append((hits, tag))
        if not scored:
            return list(tags)

        scored.sort(key=lambda item: (-item[0], item[1]))
        threshold = scored[min(keep, len(scored)) - 1][0]
        survivors = [tag for hits, tag in scored if hits >= threshold]
        log.info(
            "1차 선별: 태그 %d -> %d 개 (최상위 항목 일치 %d 개 이상)",
            len(tags),
            len(survivors),
            threshold,
        )
        return survivors

    # --------------------------------------------------------------- 정밀 비교
    def _score_one(self, tag: str, vendor_files: dict) -> Optional[TagScore]:
        try:
            proc = self.git.run_bytes("ls-tree", "-r", "-z", tag)
        except GitError as exc:
            log.warning("태그 %s 의 트리를 읽지 못했습니다: %s", tag, exc.stderr.splitlines()[-1:])
            return None
        candidate = parse_ls_tree_z(proc.stdout)
        if not candidate:
            return None
        return score_maps(tag, vendor_files, candidate)

    def score_tags(
        self,
        tags: Sequence[str],
        vendor_files: dict,
        progress: Optional[Callable] = None,
    ) -> list:
        """후보 태그들을 제조사 트리와 전부 비교해 점수를 매긴다."""
        if not tags:
            raise QcMergeError("비교할 태그 후보가 없습니다.")

        log.info("태그 %d 개를 정밀 비교합니다 (동시 실행 %d)", len(tags), self.jobs)
        scores: list = []
        done = 0
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            for score in pool.map(lambda tag: self._score_one(tag, vendor_files), tags):
                done += 1
                if score is not None:
                    scores.append(score)
                if progress:
                    progress("태그 비교 중", done, len(tags))
        if not scores:
            raise QcMergeError("태그를 하나도 비교하지 못했습니다. 캐시 저장소 상태를 확인하세요.")
        return rank(scores)


def find_closest(
    searcher: TagSearcher,
    tags: Sequence[str],
    vendor_files: dict,
    vendor_top_level: dict,
    prefilter_keep: int = DEFAULT_PREFILTER_KEEP,
    progress: Optional[Callable] = None,
) -> list:
    """태그를 받아오고 1차 선별과 정밀 비교를 거쳐 순위를 돌려준다."""
    searcher.prepare()
    searcher.fetch_tags(tags, progress=progress)
    available = sorted(set(tags) & searcher.local_tags())
    if not available:
        raise QcMergeError("받아온 태그가 없습니다. 네트워크와 저장소 주소를 확인하세요.")
    candidates = searcher.prefilter(available, vendor_top_level, keep=prefilter_keep)
    return searcher.score_tags(candidates, vendor_files, progress=progress)
