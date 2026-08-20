"""Path utility tests."""

from utils.paths import sanitize_book_name, page_image_name


def test_sanitize_book_name():
    assert sanitize_book_name("数学分析.pdf") == "数学分析"
    assert sanitize_book_name("bad:name?.pdf") == "bad_name_"


def test_page_image_name():
    assert page_image_name(1) == "page_0001.png"
    assert page_image_name(123) == "page_0123.png"
