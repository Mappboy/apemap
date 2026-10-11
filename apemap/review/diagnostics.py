"""Scoped terminal diagnostics without logging request or provider payloads."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from itertools import count
import logging
from threading import Event, Thread
import time
import traceback
from typing import ParamSpec, TypeVar

logger = logging.getLogger(__name__)
PROGRESS_INTERVAL = 5.0
SLOW_SECONDS = 1.0
_identifiers = count(1)
P = ParamSpec("P")
T = TypeVar("T")


@contextmanager
def terminal_logging() -> Iterator[None]:
    """Enable review and Waitress logs only for the server's lifetime."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    loggers = [logging.getLogger(name) for name in ("apemap.review", "waitress")]
    previous = [(item.level, item.propagate) for item in loggers]
    for item in loggers:
        item.setLevel(logging.INFO)
        item.propagate = False
        item.addHandler(handler)
    try:
        yield
    finally:
        for item, (level, propagate) in zip(loggers, previous, strict=True):
            item.removeHandler(handler)
            item.setLevel(level)
            item.propagate = propagate
        handler.close()


class Operation:
    """Report an operation while it runs, then its elapsed time and outcome."""

    def __init__(self, label: str, *, level: int = logging.INFO) -> None:
        # Labels must be code-defined descriptions, never raw URLs or form values.
        self.label = label
        self.level = level
        self.identifier = next(_identifiers)
        self.started = time.perf_counter()
        self.finished = Event()
        self.monitor: Thread | None = None
        logger.log(level, "[%s] %s started", self.identifier, label)
        if logger.isEnabledFor(logging.INFO):
            self.monitor = Thread(
                target=self._progress, name="apemap-review-progress", daemon=True
            )
            self.monitor.start()

    def _progress(self) -> None:
        while not self.finished.wait(PROGRESS_INTERVAL):
            logger.warning(
                "[%s] %s still running (%.2fs)",
                self.identifier,
                self.label,
                time.perf_counter() - self.started,
            )

    def finish(
        self, *, status: int | None = None, error: BaseException | None = None
    ) -> None:
        if self.finished.is_set():
            return
        self.finished.set()
        if self.monitor is not None:
            self.monitor.join()
        elapsed = time.perf_counter() - self.started
        level = (
            logging.ERROR
            if error is not None or status is not None and status >= 500
            else logging.WARNING
            if elapsed >= SLOW_SECONDS or status is not None and status >= 400
            else self.level
        )
        logger.log(
            level,
            "[%s] %s finished in %.2fs%s",
            self.identifier,
            self.label,
            elapsed,
            f" (HTTP {status})" if status is not None else "",
        )
        if error is not None:
            report_error(f"[{self.identifier}] {self.label}", error)


def report_error(label: str, error: BaseException) -> None:
    # Arbitrary exception messages can contain credentials or upstream bodies.
    # Keep the exception type and stack locations, without values or source lines.
    frames = traceback.extract_tb(error.__traceback__)
    locations = " -> ".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames
    )
    logger.error(
        "%s failed: %s%s",
        label,
        type(error).__name__,
        f"; {locations}" if locations else "",
    )


@contextmanager
def operation(label: str, *, level: int = logging.INFO) -> Iterator[None]:
    activity = Operation(label, level=level)
    error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        error = exc
        raise
    finally:
        activity.finish(error=error)


def timed(label: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Surface slow service stages; keep quick repeated stages at DEBUG."""

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def measured(*args: P.args, **kwargs: P.kwargs) -> T:
            if not logger.isEnabledFor(logging.INFO):
                return function(*args, **kwargs)
            with operation(label, level=logging.DEBUG):
                return function(*args, **kwargs)

        return measured

    return decorate
