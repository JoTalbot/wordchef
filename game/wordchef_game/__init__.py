"""Word Chef — deterministic game core.

Everything in this package is pure and deterministic: given the same master
seed, context strings and clock values, results are byte-identical across
processes and platforms. The server is authoritative; the client only sends
intents.
"""

__version__ = "1.4.0"

from .rng import Rng, derive_seed  # noqa: F401
from .engine import MatchState, PlayerState, Outcome, apply_intent, start_match, start_round, end_round  # noqa: F401
