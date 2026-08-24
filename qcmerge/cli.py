"""명령줄 진입점."""

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
    """태그 캐시를 둘 기본 위치."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "qcom-kernel-merger")


class Progress:
    """단계별 진행 상황을 stderr 에 표시한다."""

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
            "제조사 커널 소스를 CodeLinaro(CLO) 의 가장 가까운 태그 위에 올려 "
            "결과 저장소를 만듭니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  qcmerge ~/src/oem-kernel -o ~/work/merged\n"
            "  qcmerge ~/src/oem-kernel --tag-pattern 'LA.UM.9.14*LAHAINA*'\n"
        ),
    )
    parser.add_argument("source", help="제조사 커널 소스 최상위 디렉터리")
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT, help="결과 저장소 경로 (기본: %(default)s)"
    )
    parser.add_argument(
        "-b", "--branch", default=DEFAULT_BRANCH, help="제조사 소스를 올릴 브랜치 이름 (기본: %(default)s)"
    )
    parser.add_argument("--cache-dir", help="태그 캐시 저장소 경로 (기본: ~/.cache/qcom-kernel-merger/<저장소>)")

    group = parser.add_argument_group("CLO 저장소")
    group.add_argument(
        "--clo-base", default=clo.DEFAULT_CLO_BASE, help="CLO 커널 그룹 주소 (기본: %(default)s)"
    )
    group.add_argument("--repo", help="자동 판별 대신 사용할 저장소 이름 (예: msm-5.4, qcom)")
    group.add_argument(
        "--tag-pattern",
        action="append",
        default=[],
        metavar="GLOB",
        help="후보 태그를 좁히는 glob 패턴. 여러 번 지정 가능",
    )

    group = parser.add_argument_group("탐색")
    group.add_argument(
        "-j", "--jobs", type=int, default=os.cpu_count() or 4, help="비교에 쓸 동시 실행 수 (기본: %(default)s)"
    )
    group.add_argument(
        "--prefilter-keep",
        type=int,
        default=tagsearch.DEFAULT_PREFILTER_KEEP,
        help="1차 선별에서 남길 태그 수 (0 이면 선별하지 않음, 기본: %(default)s)",
    )
    group.add_argument(
        "--batch-size",
        type=int,
        default=tagsearch.DEFAULT_BATCH_SIZE,
        help="한 번에 받아올 태그 수 (기본: %(default)s)",
    )
    group.add_argument("--top", type=int, default=10, help="상위 몇 개 태그를 표시할지 (기본: %(default)s)")

    group = parser.add_argument_group("결과")
    group.add_argument(
        "--depth", type=int, default=1, help="기준 태그를 받아올 깊이. 0 이면 전체 이력 (기본: %(default)s)"
    )
    group.add_argument("-m", "--message", help="제조사 소스 커밋에 쓸 메시지")
    group.add_argument(
        "--no-checkout", action="store_true", help="결과 저장소의 작업 트리를 펼치지 않음"
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="자세한 로그 출력")
    parser.add_argument("-q", "--quiet", action="store_true", help="경고 이상만 출력")
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
    print("가장 가까운 태그 후보:")
    for index, score in enumerate(scores[:top], start=1):
        print("  {rank:2d}. {summary}".format(rank=index, summary=score.summary()))
    print("")


def run(args: argparse.Namespace) -> int:
    source = os.path.abspath(args.source)

    kernel_version = kernel.detect(source)
    log.info("커널 버전: %s", kernel_version.full)

    repo_name = args.repo or clo.repo_name(kernel_version)
    url = "{base}/{name}.git".format(base=args.clo_base.rstrip("/"), name=repo_name)
    log.info("CLO 저장소: %s", url)

    out_git = merger.prepare_output_repo(args.output, url, args.branch)
    vendor_tree = vendor.index_source(
        out_git, source, os.path.join(out_git.git_dir, "index")
    )

    tags = clo.filter_tags(clo.list_remote_tags(url), args.tag_pattern)
    if not tags:
        raise QcMergeError("조건에 맞는 태그가 없습니다. --tag-pattern 을 확인하세요.")

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

    print("완료했습니다.")
    print("  결과 저장소 : %s" % result.out_dir)
    print("  기준 태그   : %s (%s)" % (result.base_tag, result.base_commit[:12]))
    print("  브랜치      : %s (%s)" % (result.branch, result.commit[:12]))
    print("  제조사 변경 : 수정 %d, 추가 %d, 삭제 %d" % (best.modified, best.only_vendor, best.only_tag))
    print("")
    print("변경점 확인:")
    print("  git -C %s diff --stat %s..%s" % (result.out_dir, result.base_tag, result.branch))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    try:
        return run(args)
    except QcMergeError as exc:
        print("오류: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - 사용자 중단
        print("\n중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
