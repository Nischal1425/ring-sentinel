"""The signal contract.

Lifted from D:/Neurons Prototype/src/engine/types.py, which predates this
hackathon. The `Action` ladder in particular is carried over unchanged in
spirit: there is no BLOCK and no DELETE, and that was already written down as
a principle before this track asked for "defense-only". Provenance matters
here - it is a design commitment, not a compliance gesture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class Detection:
    """What every signal returns: how bad, and why in plain language.

    `score` is 0.0-1.0 and signal-local. The 0-100 ring risk number is the
    fusion layer's job, not any single signal's.

    A signal with nothing to judge returns None, NOT Detection(0.0). Zero means
    "looked, found nothing"; None means "had nothing to look at". Fusion treats
    them differently and the distinction is load-bearing - see fusion.fuse.
    """

    score: float
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Detection.score must be in [0.0, 1.0], got {self.score!r}")


class Action(StrEnum):
    """What the risk system is allowed to do about a ring.

    Note what is absent: no FREEZE, no BLOCK, no ACCOUNT_CLOSURE, no reserve
    seizure. The ladder tops out at a reversible 24-hour settlement delay that
    auto-expires unless a human confirms it.

    This is the enum, not the docstring, that makes the system defense-only:
    there is no member here that moves money.
    """

    OBSERVE = "observe"          # logged, nothing else
    WATCH = "watch"              # flagged internally, nobody notified
    REVIEW = "review"            # human queue, with the evidence bundle
    HOLD_SETTLEMENT = "hold"     # T+2 -> T+3, auto-expires in 24h, one-click reversal


@dataclass(frozen=True)
class Ring:
    """A candidate ring: who is in it, and what linked them."""

    ring_id: str
    members: tuple[str, ...]
    # (a, b, weight, attribute_type) - kept so the report can say WHY two
    # merchants were linked, rather than asserting that they were.
    edges: tuple[tuple[str, str, float, str], ...] = ()

    def __len__(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class Verdict:
    """The system's answer for one ring."""

    ring: Ring
    score: int                                   # 0-100, after verification
    raw_score: int                               # before verification
    # Filled in by respond.decide, which is the only place allowed to choose
    # an action - keeping the gates in one file rather than scattered.
    action: Action | None = None
    reasons: list[str] = field(default_factory=list)
    exonerations: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError(f"Verdict.score must be in [0, 100], got {self.score!r}")
        if self.score > self.raw_score:
            raise ValueError(
                f"verification may only lower a score: {self.raw_score} -> {self.score}"
            )
