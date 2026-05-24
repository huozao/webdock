import pytest

from src.browser.manager import classify_login_status


@pytest.mark.parametrize(
    ("chat_input_found", "login_indicator_found", "expected"),
    [
        (True, True, "not_logged_in"),
        (True, False, "probably_logged_in"),
        (False, True, "not_logged_in"),
        (False, False, "unknown"),
    ],
)
def test_login_indicator_takes_priority(chat_input_found, login_indicator_found, expected):
    assert classify_login_status(chat_input_found, login_indicator_found) == expected
