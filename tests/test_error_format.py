from src.utils.errors import ErrorCode, error_response


def test_error_response_includes_required_fields():
    payload = error_response(
        ErrorCode.CHAT_INPUT_NOT_FOUND,
        "Cannot find ChatGPT input box. Open noVNC to inspect the page.",
        debug_dir="logs/debug/example",
    )

    assert payload == {
        "ok": False,
        "error_code": "CHAT_INPUT_NOT_FOUND",
        "message": "Cannot find ChatGPT input box. Open noVNC to inspect the page.",
        "debug_dir": "logs/debug/example",
    }
