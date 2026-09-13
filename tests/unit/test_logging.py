import logging

from app.main import RedactOAuthCallbackQuery


def _record(path: str) -> logging.LogRecord:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "", (), None)
    record.args = ("127.0.0.1:1234", "GET", path, "1.1", 303)
    return record


def test_callback_query_is_redacted():
    record = _record("/auth/callback?code=47534f728a7772c82d71&state=abc.def")
    assert RedactOAuthCallbackQuery().filter(record) is True
    assert record.args[2] == "/auth/callback?<redacted>"
    assert record.args[0] == "127.0.0.1:1234" and record.args[4] == 303


def test_other_requests_are_untouched():
    for path in ("/auth/github", "/api/overview?refresh=1", "/login?error=state"):
        record = _record(path)
        RedactOAuthCallbackQuery().filter(record)
        assert record.args[2] == path


def test_unexpected_record_shapes_pass_through():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "plain %s", ("x",), None
    )
    assert RedactOAuthCallbackQuery().filter(record) is True
    assert record.args == ("x",)
