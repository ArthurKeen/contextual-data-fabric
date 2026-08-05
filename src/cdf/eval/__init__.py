"""Module 10 — Evaluation & Golden Set (federated-query regression gate).

F1: a declarative golden set of seed questions run end-to-end through the M5
pipeline (partition → execute → ground) against fixture source data. Each case
pins the expected answer, the sources touched, the citations, and the
grounded/partial/refused status — so the later real-adapter work (Ontop / AQL)
cannot silently regress the engine's contract.
"""

from .golden import GoldenOutcome, load_goldens, run_golden
from .nl_corpus import (
    CorpusExample,
    CorpusValidationError,
    DeterministicCorpusRouter,
    LexicalFewShotRetriever,
    NlCorpus,
    load_nl_corpus,
)

__all__ = [
    "GoldenOutcome",
    "load_goldens",
    "run_golden",
    "CorpusExample",
    "CorpusValidationError",
    "DeterministicCorpusRouter",
    "LexicalFewShotRetriever",
    "NlCorpus",
    "load_nl_corpus",
]
