from __future__ import annotations

import re


def text_matches_concept_anchor(text: str, concept: str) -> bool:
    def terms(value: str) -> list[str]:
        tokens = re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE)
        normalized: list[str] = []
        for token in tokens:
            if token in {"and", "or", "the", "a", "an"}:
                continue
            if token == "routing":
                token = "route"
            normalized.append(token)
        return normalized

    haystack = set(terms(text))
    needles = terms(concept)
    return bool(needles) and all(item in haystack for item in needles)


__all__ = ["text_matches_concept_anchor"]
