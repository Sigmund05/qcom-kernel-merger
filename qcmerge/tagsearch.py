"""Fetch CLO tags and find the one closest to the OEM source.

Tags are fetched as a blobless partial clone (``--filter=blob:none``), so only
their commits and trees arrive. The blob hashes recorded in those trees are
enough to tell whether two files hold the same contents, so gigabytes of blobs
never have to be downloaded to score a tag.
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

#: Tags requested per fetch, balancing command line length against server load.
DEFAULT_BATCH_SIZE = 200

#: Tags kept by the first pass. Tags tied at the cut-off are all kept, even
#: when that leaves more than this many.
DEFAULT_PREFILTER_KEEP = 150


class TagSearcher:
    """Owns the tag cache repository and scores tags against the OEM tree."""

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

    # ----------------------------------------------------------- preparation
    def prepare(self) -> None:
        """Set up a bare cache repository configured for partial clone."""
        if not os.path.exists(os.path.join(self.cache_dir, "HEAD")):
            os.makedirs(self.cache_dir, exist_ok=True)
            log.info("creating the tag cache repository: %s", self.cache_dir)
            Git(cwd=self.cache_dir).run("init", "--bare", "--quiet", self.cache_dir)

        git = self.git
        # extensions.* requires repository format version 1.
        git.config_set("core.repositoryformatversion", "1")
        git.config_set("extensions.partialClone", "origin")
        if git.config_get("remote.origin.url") is None:
            git.run("remote", "add", "origin", self.url)
        else:
            git.run("remote", "set-url", "origin", self.url)
        git.config_set("remote.origin.promisor", "true")
        git.config_set("remote.origin.partialclonefilter", "blob:none")
        # Keep automatic gc out of the way while thousands of tags come in.
        git.config_set("gc.auto", "0")

    def local_tags(self) -> set:
        """Tag names already present in the cache."""
        return set(self.git.lines("for-each-ref", "--format=%(refname:short)", "refs/tags"))

    # --------------------------------------------------------------- fetching
    def fetch_tags(
        self,
        tags: Sequence[str],
        progress: Optional[Callable] = None,
    ) -> list:
        """Fetch commits and trees for the tags that are not cached yet.

        ``progress`` is called as ``(stage name, done, total)``.

        Returns
        -------
        The tag names that could not be fetched.
        """
        have = self.local_tags()
        missing = [tag for tag in tags if tag not in have]
        if not missing:
            log.info("all %d tags are already cached", len(tags))
            return []

        log.info("fetching %d of %d tags", len(missing), len(tags))
        failed: list = []
        done = 0
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            if not self._fetch_batch(batch):
                failed.extend(batch)
            done += len(batch)
            if progress:
                progress("fetching tags", done, len(missing))
        if failed:
            log.warning("%d tags could not be fetched", len(failed))
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
                "tag fetch failed (%d/%d): %s",
                attempt + 1,
                self.fetch_retries + 1,
                stderr.splitlines()[-1] if stderr else "(no stderr)",
            )
            if attempt < self.fetch_retries:
                time.sleep(delay)
                delay *= 2
        return False

    # ------------------------------------------------------------- first pass
    def top_level_maps(self, tags: Sequence[str]) -> dict:
        """Collect every tag's top-level tree entries in a single git call."""
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
        """Cut the candidate list down by comparing top-level tree entries only.

        A directory's tree hash matches only when everything below it is
        identical, which makes this a very cheap measure of how much the OEM
        left untouched. Tags tied at the cut-off are all kept.
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
            "first pass: %d -> %d tags (at least %d matching top-level entries)",
            len(tags),
            len(survivors),
            threshold,
        )
        return survivors

    # ------------------------------------------------------------ second pass
    def _score_one(self, tag: str, vendor_files: dict) -> Optional[TagScore]:
        try:
            proc = self.git.run_bytes("ls-tree", "-r", "-z", tag)
        except GitError as exc:
            log.warning("could not read the tree of tag %s: %s", tag, exc.stderr.splitlines()[-1:])
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
        """Compare every candidate tag against the OEM tree and rank them."""
        if not tags:
            raise QcMergeError("no candidate tags left to compare.")

        log.info("comparing %d tags in full (%d workers)", len(tags), self.jobs)
        scores: list = []
        done = 0
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            for score in pool.map(lambda tag: self._score_one(tag, vendor_files), tags):
                done += 1
                if score is not None:
                    scores.append(score)
                if progress:
                    progress("comparing tags", done, len(tags))
        if not scores:
            raise QcMergeError("not a single tag could be compared; check the cache repository.")
        return rank(scores)


def find_closest(
    searcher: TagSearcher,
    tags: Sequence[str],
    vendor_files: dict,
    vendor_top_level: dict,
    prefilter_keep: int = DEFAULT_PREFILTER_KEEP,
    progress: Optional[Callable] = None,
) -> list:
    """Fetch the tags, run both passes and return the ranked scores."""
    searcher.prepare()
    searcher.fetch_tags(tags, progress=progress)
    available = sorted(set(tags) & searcher.local_tags())
    if not available:
        raise QcMergeError("no tags were fetched; check the network and the repository URL.")
    candidates = searcher.prefilter(available, vendor_top_level, keep=prefilter_keep)
    return searcher.score_tags(candidates, vendor_files, progress=progress)
