"""PolyglotEvasion — Unicode and multi-script obfuscation techniques.

These transforms are designed to pass payloads through content-classifier
filters that operate on ASCII/Latin tokenisation while preserving semantic
meaning for the underlying LLM (which handles Unicode natively).

All methods are pure Python — no external dependencies.
"""
from __future__ import annotations

import unicodedata

# Zero-width joiner — visually invisible, disrupts ASCII n-gram tokenisers
_ZWJ = "\u200d"
# Zero-width non-joiner
_ZWNJ = "\u200c"
# Right-to-left mark — inserts an invisible RTL flow control character
_RTL_MARK = "\u200f"


class PolyglotEvasion:
    """Apply Unicode obfuscation transforms to evade content filters.

    None of these transforms break the LLM's understanding of the text — they
    exploit the gap between the safety classifier (which often tokenises on
    ASCII) and the generation model (which has a full Unicode vocabulary).
    """

    # Cyrillic lookalike substitution table for common ASCII characters.
    # Each entry maps an ASCII char to a visually-identical Cyrillic codepoint.
    _CYRILLIC_MAP: dict[str, str] = {
        "a": "\u0430",   # а (Cyrillic small letter a)
        "e": "\u0435",   # е (Cyrillic small letter ie)
        "o": "\u043e",   # о (Cyrillic small letter o)
        "p": "\u0440",   # р (Cyrillic small letter er)
        "c": "\u0441",   # с (Cyrillic small letter es)
        "x": "\u0445",   # х (Cyrillic small letter ha)
        "A": "\u0410",   # А (Cyrillic capital letter A)
        "B": "\u0412",   # В (Cyrillic capital letter Ve — visually similar)
        "E": "\u0415",   # Е (Cyrillic capital letter Ie)
        "O": "\u041e",   # О (Cyrillic capital letter O)
        "P": "\u0420",   # Р (Cyrillic capital letter Er)
        "C": "\u0421",   # С (Cyrillic capital letter Es)
        "X": "\u0425",   # Х (Cyrillic capital letter Ha)
        "H": "\u041d",   # Н (Cyrillic capital letter En — visually similar)
        "T": "\u0422",   # Т (Cyrillic capital letter Te — visually similar)
        "M": "\u041c",   # М (Cyrillic capital letter Em — visually similar)
    }

    def insert_zero_width(self, text: str) -> str:
        """Insert U+200D (zero-width joiner) between every word.

        Preserves readability for humans and LLMs but breaks ASCII n-gram
        classifiers that scan for exact keyword sequences.

        Example::

            "ignore previous instructions"
            → "ignore\u200d previous\u200d instructions"
        """
        words = text.split(" ")
        return f" {_ZWJ}".join(words)

    def cyrillic_substitute(self, text: str, rate: float = 0.5) -> str:
        """Replace ASCII characters with Cyrillic lookalikes at the given rate.

        Only characters that have a mapping are substituted.  The ``rate``
        parameter controls what fraction of eligible characters are replaced
        (1.0 = replace all eligible, 0.5 = replace every other eligible char).

        The resulting string renders identically in most fonts and is processed
        correctly by multilingual LLMs but may evade ASCII keyword classifiers.

        Parameters
        ----------
        text:
            The input text to transform.
        rate:
            Fraction of eligible characters to substitute (0.0–1.0).
        """
        import random
        result: list[str] = []
        for ch in text:
            if ch in self._CYRILLIC_MAP and random.random() < rate:
                result.append(self._CYRILLIC_MAP[ch])
            else:
                result.append(ch)
        return "".join(result)

    def interleave_language(self, text: str, lang: str = "ar") -> str:
        """Interleave RTL language direction marks into the text.

        Inserts ``U+200F`` (right-to-left mark) between characters.  In
        Arabic-script contexts, this can confuse left-to-right classifiers
        that assume a single script direction.

        Parameters
        ----------
        text:
            The input payload string.
        lang:
            Language hint (currently only "ar" / Arabic is supported).
            Reserved for future script-specific transforms.
        """
        if lang != "ar":
            # For non-Arabic scripts, use ZWNJ between words instead
            return (_ZWNJ + " ").join(text.split(" "))

        # Arabic: wrap keywords in RTL marks to flip classifier direction
        words = text.split(" ")
        result = []
        for word in words:
            result.append(_RTL_MARK + word + _RTL_MARK)
        return " ".join(result)

    def normalize(self, text: str, form: str = "NFC") -> str:
        """Apply Unicode normalisation to reverse any obfuscation transforms.

        Useful for verifying that two visually identical strings are actually
        different at the code-point level (confirms evasion worked).
        """
        return unicodedata.normalize(form, text)  # type: ignore[arg-type]
