"""Page range parser tests."""

import pytest

from utils.page_range import PageRangeError, all_pages, parse_page_range


def test_parse_simple_and_ranges():
    assert parse_page_range("1,3,5-8", 100) == [1, 3, 5, 6, 7, 8]


def test_parse_dedupe_and_sort():
    assert parse_page_range("1,2,2,3,1-5", 100) == [1, 2, 3, 4, 5]


def test_parse_all():
    assert all_pages(5) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    "expr",
    ["0", "-1", "5-2", "abc", "101", ""],
)
def test_parse_errors(expr):
    with pytest.raises(PageRangeError):
        parse_page_range(expr, 100)
