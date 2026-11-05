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


def test_bom_is_stripped(tmp_path: Path) -> None:
    secret = tmp_path / "bom.txt"
    secret.write_bytes(b"\xef\xbb\xbfsk-abc\r\n")
    assert read_secret_file(secret) == "sk-abc"


def test_non_utf8_raises_secret_file_error_without_content(tmp_path: Path) -> None:
    secret = tmp_path / "utf16.txt"
    secret.write_bytes("sk-secret-123".encode("utf-16"))
    with pytest.raises(SecretFileError) as excinfo:
        read_secret_file(secret)
    exc = excinfo.value
    assert str(secret) in str(exc)
    assert "sk-secret" not in str(exc)
    assert "sk-secret" not in repr(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_multiline_value_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "multi.txt"
    secret.write_text("sk-a\nsk-b", encoding="utf-8")
    with pytest.raises(SecretFileError) as excinfo:
        read_secret_file(secret)
    assert str(secret) in str(excinfo.value)
    assert "sk-a" not in str(excinfo.value)


def _assert_rejected_without_content(path: Path) -> None:
    with pytest.raises(SecretFileError) as excinfo:
        read_secret_file(path)
    assert str(path) in str(excinfo.value)
    assert "sk-abc" not in str(excinfo.value)


def test_nul_byte_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "nulbyte.txt"
    secret.write_bytes(b"sk-abc\x00def")
    _assert_rejected_without_content(secret)


def test_utf16le_without_bom_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "utf16le.txt"
    secret.write_bytes("sk-abc".encode("utf-16-le"))
    _assert_rejected_without_content(secret)


def test_doubled_bom_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "doublebom.txt"
    secret.write_bytes(b"\xef\xbb\xbf\xef\xbb\xbfsk-abc")
    _assert_rejected_without_content(secret)
