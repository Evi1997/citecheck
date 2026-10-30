"""BM25 ranking of document sentences, used only to show closest passages.

Ranking never decides a label (TECH_PLAN.md section 5). Scores stay internal
and are never shown or written to a report.
"""

import math
from collections import Counter
from typing import List

from .tokens import Token

K1 = 1.2
B = 0.75

STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "of", "to", "in", "on", "for", "with", "at", "by", "from", "and", "or",
    "as", "it", "its", "this", "that", "these", "those", "there", "their",
    "they", "them", "we", "you", "he", "she", "i", "his", "her", "our", "your",
    "has", "have", "had", "do", "does", "did", "will", "would", "can", "could",
    "into", "than", "then", "so", "such", "which", "who", "whom", "what",
})


def ranking_terms(tokens: List[Token]) -> List[str]:
    """Terms used for ranking: no symbols, no stopwords."""
    return [t.norm for t in tokens if t.kind != "symbol" and t.word not in STOPWORDS]


class BM25:
    def __init__(self, documents: List[List[str]]):
        self.counts = [Counter(terms) for terms in documents]
        self.lengths = [len(terms) for terms in documents]
        n_docs = len(documents)
        self.avg_length = (sum(self.lengths) / n_docs) if n_docs else 1.0
        if self.avg_length == 0:
            self.avg_length = 1.0
        doc_freq: Counter = Counter()
        for counts in self.counts:
            doc_freq.update(counts.keys())
        self.idf = {
            term: math.log(1 + (n_docs - n + 0.5) / (n + 0.5))
            for term, n in doc_freq.items()
        }

    def scores(self, query_terms: List[str]) -> List[float]:
        query = sorted(set(query_terms))  # fixed order keeps float sums identical
        results = []
        for counts, length in zip(self.counts, self.lengths):
            score = 0.0
            for term in query:
                freq = counts.get(term)
                if freq:
                    norm = K1 * (1 - B + B * length / self.avg_length)
                    score += self.idf[term] * freq * (K1 + 1) / (freq + norm)
            results.append(score)
        return results
