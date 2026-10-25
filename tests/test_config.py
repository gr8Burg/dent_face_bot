import pytest

from attendance.config import _int_set


@pytest.mark.parametrize(
    "value",
    ["465165912,-1001713309519", "465165912;-1001713309519", "465165912, -1001713309519", " 465165912 -1001713309519 "],
)
def test_id_lists_accept_common_separators(value):
    assert _int_set("X", value) == {465165912, -1001713309519}


def test_empty_id_list():
    assert _int_set("X", "") == frozenset()


def test_invalid_id_list_gives_a_clear_error():
    with pytest.raises(SystemExit, match="TELEGRAM_REPORT_CHAT_IDS must be a list of numeric ids"):
        _int_set("TELEGRAM_REPORT_CHAT_IDS", "123,@mychat")
