#!/usr/bin/env python3
"""In-memory priority order for layer_builder's multi-hour round-robin dispatch
(architecture review candidate "test changed processes much more quickly").

A section with a large backlog otherwise waits behind every other MULTI_HOUR_SECTIONS
entry declared ahead of it in TASK_CLASSES -- fine in steady state, painful when a
developer is iterating on one layer's render code and has to wait through the rest of
the round-robin order before it renders again. reorder() lets a section jump the
queue without touching a round already in flight: _run_dispatch_cycle asks ordered()
fresh at the start of each round, so a reorder only ever affects the NEXT round, never
one already submitted to the process pool.

Validated with ast.parse.
"""
import threading


class RoundRobinOrder:
    def __init__(self, sections):
        self._default = list(sections)
        self._order = list(sections)
        self._lock = threading.Lock()

    def ordered(self, pending) -> list:
        """`pending` (any iterable of section names), in current priority order."""
        pending = set(pending)
        with self._lock:
            order = self._order
        return [s for s in order if s in pending]

    def reorder(self, priority_sections: list) -> None:
        """Move `priority_sections` to the front, in the given order. Sections not
        named keep their existing relative order, appended after. Raises ValueError
        (and leaves the order untouched) if any name isn't a known section."""
        unknown = [s for s in priority_sections if s not in self._default]
        if unknown:
            raise ValueError(f"unknown section(s): {', '.join(unknown)}")
        with self._lock:
            priority = list(dict.fromkeys(priority_sections))
            rest = [s for s in self._order if s not in priority]
            self._order = priority + rest

    def reset(self) -> None:
        with self._lock:
            self._order = list(self._default)

    def current(self) -> list:
        with self._lock:
            return list(self._order)
