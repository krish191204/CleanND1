"""Base classes shared by all pipeline stages."""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from loguru import logger


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass
class StageResult(Generic[OutputT]):
    """What every stage returns."""
    passed: list[OutputT] = field(default_factory=list)
    rejected: list[tuple[OutputT, str]] = field(default_factory=list)  # item + reason
    stats: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.rejected)


class Stage(abc.ABC, Generic[InputT, OutputT]):
    """Abstract stage. Subclasses implement :meth:`process`."""

    name: str = "stage"

    def __init__(self, **kwargs: object) -> None:
        self.config = kwargs

    @abc.abstractmethod
    def process(self, items: list[InputT]) -> StageResult[OutputT]:
        ...

    def __call__(self, items: list[InputT]) -> StageResult[OutputT]:
        t0 = time.perf_counter()
        try:
            result = self.process(items)
        except Exception as exc:  # pragma: no cover - logged at run level
            logger.exception(f"[{self.name}] crashed: {exc}")
            raise
        result.elapsed_seconds = time.perf_counter() - t0
        logger.info(
            f"[{self.name}] passed={len(result.passed)} "
            f"rejected={len(result.rejected)} elapsed={result.elapsed_seconds:.2f}s"
        )
        return result