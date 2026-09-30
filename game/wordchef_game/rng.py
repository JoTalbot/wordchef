"""Deterministic RNG for Word Chef.

We deliberately do NOT use ``random.Random``: its stream is an implementation
detail of CPython and may differ across versions/platforms. SplitMix64 is a
tiny, fully specified generator; seeds are derived from (master seed, context)
via SHA-256 so every game facet (tray generation, order picks, chaos events)
has its own independent, reproducible stream.
"""
from __future__ import annotations

import hashlib

_MASK = (1 << 64) - 1


def derive_seed(master_seed: str, context: str) -> int:
    """Stable 64-bit seed for one named stream."""
    digest = hashlib.sha256(f"{master_seed}\x00{context}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class Rng:
    """SplitMix64 generator with convenience draws."""

    def __init__(self, seed: int):
        self.state = seed & _MASK

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        """Uniform integer in [0, n)."""
        if n <= 0:
            raise ValueError("n must be positive")
        return self.next_u64() % n

    def between(self, lo: int, hi: int) -> int:
        """Uniform integer in [lo, hi] inclusive."""
        return lo + self.below(hi - lo + 1)

    def chance(self, numerator: int, denominator: int) -> bool:
        return self.below(denominator) < numerator

    def choice(self, items):
        if not items:
            raise ValueError("empty sequence")
        return items[self.below(len(items))]

    def weighted_choice(self, items, weights):
        total = sum(weights)
        if total <= 0 or len(items) != len(weights) or not items:
            raise ValueError("bad weighted_choice arguments")
        roll = self.below(total)
        acc = 0
        for item, weight in zip(items, weights):
            acc += weight
            if roll < acc:
                return item
        return items[-1]

    def shuffle(self, items):
        """Fisher-Yates, deterministic."""
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = self.below(i + 1)
            out[i], out[j] = out[j], out[i]
        return out
