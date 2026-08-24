"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional, Sequence

from . import __version__, clo, kernel, merger, tagsearch, vendor
from .errors import QcMergeError
from .treemap import TagScore

log = logging.getLogger("qcmerge")

DEFAULT_OUTPUT = "qcmerge-out"
DEFAULT_BRANCH = "vendor"


def default_cache_root() -> str:
    """Where the tag cache lives by default."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "qcom-kernel-merger")


class Progress:
    """Reports per-stage progress on stderr."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled and sys.stderr.isatty()
        self._last = ""

    def __call__(self, label: str, done: int, total: int) -> None:
        if not self.enabled or total <= 0:
            return
        text = "  {label}: {done}/{total} ({pct:.0f}%)".format(
            label=label, done=done, total=total, pct=done * 100.0 / total
        )
        sys.stderr.write("\r" + text.ljust(len(self._last)))
        sys.stderr.flush()
        self._last = text
        if done >= total:
            self.finish()

    def finish(self) -> None:
        if self.enabled and self._last:
            sys.stderr.write("\n")
            sys.stderr.flush()
            self._last = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qcmerge",
        description=(
            "Build a repository that holds an OEM kernel source on top of the "
            "closest CodeLinaro (CLO) tag."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  qcmerge ~/src/oem-kernel -o ~/work/merged\n"
            "  qcmerge ~/src/oem-kernel --tag-pattern 'LA.UM.9.14*LAHAINA*'\n"
        ),
    )
    parser.add_argument("source", help="top-level directory of the OEM kernel source")
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT, help="result repository path (default: %(default)s)"
    )
    parser.add_argument(
        "-b", "--branch", default=DEFAULT_BRANCH,
        help="branch to put the OEM source on (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-dir",
        help="tag cache repository (default: ~/.cache/qcom-kernel-merger/<repo>)",
    )

    group = parser.add_argument_group("CLO repository")
    group.add_argument(
        "--clo-base", default=clo.DEFAULT_CLO_BASE,
        help="CLO kernel group URL (default: %(default)s)",
    )
    group.add_argument("--repo", help="repository name to use instead of the detected one")
    group.add_argument(
        "--tag-pattern",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob pattern narrowing the candidate tags; may be given more than once",
    )

    group = parser.add_argument_group("search")
    group.add_argument(
        "-j", "--jobs", type=int, default=os.cpu_count() or 4,
        help="number of comparisons to run at once (default: %(default)s)",
    )
    group.add_argument(
        "--prefilter-keep",
        type=int,
        default=tagsearch.DEFAULT_PREFILTER_KEEP,
        help="tags kept by the first pass, 0 to skip it (default: %(default)s)",
    )
    group.add_argument(
        "--batch-size",
        type=int,
        default=tagsearch.DEFAULT_BATCH_SIZE,
        help="tags requested per fetch (default: %(default)s)",
    )
    group.add_argument(
        "--top", type=int, default=10, help="how many tag candidates to print (default: %(default)s)"
    )

    group = parser.add_argument_group("result")
    group.add_argument(
        "--depth", type=int, default=1,
        help="depth to fetch the base tag at, 0 for full history (default: %(default)s)",
    )
    group.add_argument("-m", "--message", help="message for the OEM source commit")
    group.add_argument(
        "--no-checkout", action="store_true",
        help="do not check the source out into the result work tree",
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    parser.add_argument("--version", action="version", version="qcom-kernel-merger " + __version__)
    return parser


def setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.INFO
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)


def print_ranking(scores: Sequence[TagScore], top: int) -> None:
    print("")
    print("Closest tag candidates:")
    for index, score in enumerate(scores[:top], start=1):
        print("  {rank:2d}. {summary}".format(rank=index, summary=score.summary()))
    print("")


def run(args: argparse.Namespace) -> int:
    source = os.path.abspath(args.source)

    kernel_version = kernel.detect(source)
    log.info("kernel version: %s", kernel_version.release)
    if not kernel_version.is_lts():
        log.warning(
            "%s is not a known long-term support series; Android device "
            "kernels track an LTS release, so check that the source is the "
            "kernel you expect",
            kernel_version.series,
        )

    repo_name = args.repo or clo.repo_name(kernel_version)
    url = "{base}/{name}.git".format(base=args.clo_base.rstrip("/"), name=repo_name)
    log.info("CLO repository: %s", url)

    out_git = merger.prepare_output_repo(args.output, url, args.branch)
    vendor_tree = vendor.index_source(
        out_git, source, os.path.join(out_git.git_dir, "index")
    )

    tags = clo.filter_tags(clo.list_remote_tags(url), args.tag_pattern)
    if not tags:
        raise QcMergeError("no tags matched; check --tag-pattern.")

    cache_dir = args.cache_dir or os.path.join(default_cache_root(), repo_name)
    searcher = tagsearch.TagSearcher(
        cache_dir=cache_dir,
        url=url,
        jobs=args.jobs,
        batch_size=args.batch_size,
    )
    progress = Progress(enabled=not args.quiet)
    scores = tagsearch.find_closest(
        searcher,
        tags,
        vendor_tree.files,
        vendor_tree.top_level,
        prefilter_keep=args.prefilter_keep,
        progress=progress,
    )
    progress.finish()
    print_ranking(scores, args.top)

    best = scores[0]
    message = args.message or merger.build_commit_message(source, kernel_version, url, best)
    result = merger.merge(
        out_dir=args.output,
        url=url,
        branch=args.branch,
        tag=best.tag,
        tree=vendor_tree.tree,
        message=message,
        score=best,
        git=out_git,
        depth=args.depth,
        checkout=not args.no_checkout,
    )

    print("Done.")
    print("  result repository : %s" % result.out_dir)
    print("  base tag          : %s (%s)" % (result.base_tag, result.base_commit[:12]))
    print("  branch            : %s (%s)" % (result.branch, result.commit[:12]))
    print(
        "  OEM changes       : %d modified, %d added, %d removed"
        % (best.modified, best.only_vendor, best.only_tag)
    )
    print("")
    print("Review the changes with:")
    print("  git -C %s diff --stat %s..%s" % (result.out_dir, result.base_tag, result.branch))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    try:
        return run(args)
    except QcMergeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - user interrupt
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
