"""Trigram (Markov) reference model with backoff on the *collapsed* token sequences.

This is the AI-aided pair's reference baseline on exactly the same target as the transformer
(next different action token, same filtered sessions, same split). The human-only pair keeps its
own trigram; this module exists so the two models can be compared position by position.

Backoff: use the trigram distribution P(a_t | a_{t-2}, a_{t-1}) if that context was seen in
training at least `min_count` times, otherwise the bigram P(a_t | a_{t-1}), otherwise the unigram.
The top-k list is filled from the lower levels if the chosen level has fewer than k candidates.
The start of a session is padded with BOS, so the first actions use (BOS, BOS) and (BOS, a_0).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

BOS_CONTEXT = -1


@dataclass(frozen=True)
class TrigramPrediction:
    top_k: list[int]
    probs: list[float]     # probabilities of top_k under the level that was used
    p_true: float          # probability of the true token under that level (0 if unseen)
    level: str             # 'trigram', 'bigram' or 'unigram'


class TrigramModel:
    def __init__(self, n_actions: int, min_count: int = 1):
        self.n_actions = n_actions
        self.min_count = min_count
        self.trigram: dict[tuple[int, int], Counter] = defaultdict(Counter)
        self.bigram: dict[int, Counter] = defaultdict(Counter)
        self.unigram: Counter = Counter()

    def fit(self, sequences: list[np.ndarray]) -> "TrigramModel":
        for sequence in sequences:
            history = [BOS_CONTEXT, BOS_CONTEXT]
            for token in map(int, sequence):
                self.trigram[(history[-2], history[-1])][token] += 1
                self.bigram[history[-1]][token] += 1
                self.unigram[token] += 1
                history.append(token)
        return self

    def _levels(self, previous2: int, previous1: int):
        trigram = self.trigram.get((previous2, previous1))
        if trigram and sum(trigram.values()) >= self.min_count:
            yield "trigram", trigram
        bigram = self.bigram.get(previous1)
        if bigram and sum(bigram.values()) >= self.min_count:
            yield "bigram", bigram
        yield "unigram", self.unigram

    def predict(self, previous2: int, previous1: int, true_token: int, k: int = 5) -> TrigramPrediction:
        levels = list(self._levels(previous2, previous1))
        level, counts = levels[0]
        total = sum(counts.values())
        top_k = [t for t, _ in counts.most_common(k)]
        for _, lower in levels[1:]:  # fill up from lower levels
            if len(top_k) >= k:
                break
            top_k += [t for t, _ in lower.most_common() if t not in top_k][:k - len(top_k)]
        probs = [counts.get(t, 0) / total for t in top_k]
        return TrigramPrediction(top_k, probs, counts.get(true_token, 0) / total, level)

    def predict_sequence(self, sequence: np.ndarray, k: int = 5) -> list[TrigramPrediction]:
        history = [BOS_CONTEXT, BOS_CONTEXT] + [int(t) for t in sequence]
        return [self.predict(history[i], history[i + 1], int(token), k) for i, token in enumerate(sequence)]
