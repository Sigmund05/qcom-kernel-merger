"""도구 전역에서 쓰는 예외 정의."""

from __future__ import annotations


class QcMergeError(Exception):
    """사용자에게 그대로 보여줄 수 있는 오류.

    CLI 는 이 예외만 붙잡아서 트레이스백 없이 메시지만 출력한다.
    """
