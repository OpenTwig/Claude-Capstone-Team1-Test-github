"""Paged lists for the Administration page (user feedback: lists mustn't grow without end).

A list asks `clamp` which page to show, fetches that page with LIMIT/OFFSET, and returns a
ListPage; the template's page selector uses `page_numbers`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PER_PAGE = 10


@dataclass(frozen=True)
class ListPage:
    """One page of a list."""
    rows: tuple[Any, ...]
    page: int                    # 1-based, clamped to the pages that exist
    pages: int                   # at least 1
    total: int                   # rows on every page
    per_page: int

    @property
    def first(self) -> int:
        """The 1-based position of this page's first row ("Showing 11-20 of 57")."""
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last(self) -> int:
        return (self.page - 1) * self.per_page + len(self.rows)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


def page_number(raw) -> int:
    """A page number from a URL or form field; 1 if it isn't a positive whole number."""
    return int(raw) if str(raw or "").isdigit() and int(raw) > 0 else 1


def clamp(page: int, total: int, per_page: int = PER_PAGE) -> tuple[int, int]:
    """(page, pages): the asked-for page kept between 1 and the last page, which is at least 1."""
    pages = max(1, -(-total // per_page))
    return min(max(1, page), pages), pages


def page_numbers(page: int, pages: int, around: int = 1) -> list[int | None]:
    """Page numbers for a page selector: the first, the last, and `around` pages either side of the
    current one; None marks a gap ("…"). E.g. page 6 of 12 -> [1, None, 5, 6, 7, None, 12]."""
    shown = sorted({1, pages, *range(max(1, page - around), min(pages, page + around) + 1)})
    numbers: list[int | None] = []
    for n in shown:
        if numbers and n - numbers[-1] > 1:
            numbers.append(None)
        numbers.append(n)
    return numbers
