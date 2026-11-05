from pathlib import Path

import pytest

from marketing_agent.secrets import SecretFileError, read_secret_file


def test_reads_and_strips(tmp_path: Path) -> None:
    secret = tmp_path / "key.txt"
    secret.write_text("  sk-fake-123\n", encoding="utf-8")
    assert read_secret_file(secret) == "sk-fake-123"


def test_missing_file_error_names_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"
    with pytest.raises(SecretFileError) as excinfo:
        read_secret_file(missing)
    assert str(missing) in str(excinfo.value)


def test_empty_file_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text(" \n", encoding="utf-8")
    with pytest.raises(SecretFileError) as excinfo:
        read_secret_file(empty)
    assert str(empty) in str(excinfo.value)
