# SILENT_EMAIL_INBOXES — an inbox connected for OBSERVATION only: mail lands in
# Chatwoot but the bridge runs no email pipeline on it. No network.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import main  # noqa: E402

INFO = {"id": 99, "name": "Durian Info", "channel_type": "Channel::Email"}
HELLO = {"id": 1, "name": "Hello Durian", "channel_type": "Channel::Email"}


def test_silent_by_name_set_before_the_inbox_exists():
    # _csv lowercases env entries; set as the operator would ("Durian Info").
    config.SILENT_EMAIL_INBOXES = {"durian info"}
    assert main._is_silent_inbox(INFO) is True
    assert main._is_silent_inbox({"name": "DURIAN INFO"}) is True   # case-insensitive
    assert main._is_silent_inbox(HELLO) is False                    # other inboxes unaffected


def test_silent_by_numeric_id():
    config.SILENT_EMAIL_INBOXES = {"99"}
    assert main._is_silent_inbox(INFO) is True
    assert main._is_silent_inbox({"id": 1, "name": "Hello Durian"}) is False


def test_empty_default_silences_nothing():
    config.SILENT_EMAIL_INBOXES = set()
    assert main._is_silent_inbox(INFO) is False
    assert main._is_silent_inbox(HELLO) is False


def test_blank_name_never_matches_a_blank_entry():
    config.SILENT_EMAIL_INBOXES = {"durian info"}
    assert main._is_silent_inbox({"id": 5, "name": ""}) is False
    assert main._is_silent_inbox({"id": 5}) is False
