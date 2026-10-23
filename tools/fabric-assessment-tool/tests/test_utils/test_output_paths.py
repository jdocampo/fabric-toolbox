import os
import ntpath
import tempfile

import pytest

from fabric_assessment_tool.utils.output_paths import (
    LEGACY_MAX_DIRECTORY_PATH,
    LEGACY_MAX_FILE_PATH,
    OutputPathAdapter,
    OutputPathCapabilityError,
    utf16_length,
)


def path_with_length(length: int) -> str:
    prefix = "C:\\"
    first = "a" * 100
    second = "b" * (length - len(prefix) - len(first) - 1)
    return prefix + first + "\\" + second


def test_utf16_length_counts_non_bmp_characters_as_two_units():
    assert utf16_length("abc") == 3
    assert utf16_length("a😀b") == 4


def test_normalizes_paths_before_applying_extended_prefix():
    adapter = OutputPathAdapter(
        r"C:\FAT\.\nested\..\assessment", is_windows=True, windows_long_paths=True
    )

    assert str(adapter.root) == r"C:\FAT\assessment"
    assert adapter.filesystem_path(adapter.root) == r"\\?\C:\FAT\assessment"


def test_relative_output_is_resolved_before_extended_prefix():
    adapter = OutputPathAdapter(
        r".\assessment", is_windows=True, windows_long_paths=True
    )

    assert ntpath.isabs(str(adapter.root))
    assert adapter.filesystem_path(adapter.root).startswith("\\\\?\\")
    assert not adapter.filesystem_path(adapter.root).startswith(r"\\?\.")


def test_converts_unc_paths_and_strips_existing_prefixes():
    unc = OutputPathAdapter(
        r"\\server\share\FAT", is_windows=True, windows_long_paths=True
    )
    assert unc.filesystem_path(unc.root) == r"\\?\UNC\server\share\FAT"

    prefixed = OutputPathAdapter(
        r"\\?\C:\FAT\folder\..", is_windows=True, windows_long_paths=True
    )
    assert str(prefixed.root) == r"C:\FAT"
    assert prefixed.filesystem_path(prefixed.root) == r"\\?\C:\FAT"

    ordinary = OutputPathAdapter(r"\\?\C:\FAT", is_windows=True)
    assert ordinary.filesystem_path(ordinary.root) == r"C:\FAT"


def test_legacy_file_path_boundaries_are_measured_in_utf16_units():
    adapter = OutputPathAdapter("C:\\", is_windows=True)

    assert utf16_length(path_with_length(LEGACY_MAX_FILE_PATH)) == 259
    assert (
        adapter.validate([path_with_length(LEGACY_MAX_FILE_PATH)], legacy_limit=True)
        == []
    )

    violations = adapter.validate([path_with_length(260)], legacy_limit=True)
    assert any(item.kind == "file path" and item.length == 260 for item in violations)

    unicode_path = "C:\\a\\" + ("😀" * 127)
    assert utf16_length(unicode_path) == 259
    assert adapter.validate([unicode_path], legacy_limit=True) == []
    unicode_violation = adapter.validate(["C:\\a\\" + ("😀" * 128)], legacy_limit=True)
    assert any(
        item.kind == "file path" and item.length == 261 for item in unicode_violation
    )


def test_legacy_directory_limit_is_checked_separately():
    adapter = OutputPathAdapter("C:\\", is_windows=True)
    assert (
        adapter.validate(
            [], [path_with_length(LEGACY_MAX_DIRECTORY_PATH)], legacy_limit=True
        )
        == []
    )
    violations = adapter.validate([], [path_with_length(249)], legacy_limit=True)
    assert any(
        item.kind == "directory creation path" and item.limit == 248
        for item in violations
    )


@pytest.mark.parametrize("legacy_limit", [True, False])
def test_component_length_is_enforced_in_every_windows_mode(legacy_limit):
    adapter = OutputPathAdapter("C:\\FAT", is_windows=True)
    violations = adapter.validate(
        ["C:\\FAT\\" + ("x" * 256)], legacy_limit=legacy_limit
    )

    assert any(
        item.kind == "path component" and item.limit == 255 for item in violations
    )


def test_extended_path_limit_includes_the_filesystem_prefix():
    adapter = OutputPathAdapter("C:\\", is_windows=True, windows_long_paths=True)
    path = "C:\\" + "\\".join(["x" * 250] * 131)

    violations = adapter.validate([path], legacy_limit=False)

    assert any(item.kind == "extended file path" for item in violations)


def test_non_windows_skips_windows_path_limits_and_rejects_flag_in_service(tmp_path):
    adapter = OutputPathAdapter(str(tmp_path), is_windows=False)
    assert adapter.validate(["x" * 40000], legacy_limit=False) == []


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows filesystem path APIs")
def test_windows_probe_exercises_long_directory_and_file_operations():
    adapter = OutputPathAdapter(tempfile.gettempdir())

    assert adapter.probe_long_paths() in {"legacy", "supported"}


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows extended-length paths")
def test_extended_path_filesystem_operations_use_selected_representation(tmp_path):
    adapter = OutputPathAdapter(str(tmp_path), windows_long_paths=True)
    if adapter.probe_long_paths() != "supported":
        pytest.skip("The Python runtime or destination does not support extended paths")
    directory = adapter.root / "extended" / "nested"
    file_path = directory / "content.json"

    adapter.mkdir(directory, parents=True, exist_ok=True)
    with adapter.open(file_path, "w", encoding="utf-8") as stream:
        stream.write('{"ok":true}')

    assert adapter.exists(file_path)
    assert [item.name for item in adapter.glob(directory, "*.json")] == ["content.json"]
    with adapter.open(file_path, "r", encoding="utf-8") as stream:
        assert stream.read() == '{"ok":true}'
    adapter.remove_tree(adapter.root / "extended")
    assert not adapter.exists(directory)


def test_unrelated_probe_errors_keep_their_actual_diagnosis(tmp_path, monkeypatch):
    adapter = OutputPathAdapter(str(tmp_path), is_windows=True)

    def denied(_):
        raise PermissionError("access denied")

    monkeypatch.setattr(os, "mkdir", denied)
    with pytest.raises(PermissionError, match="access denied"):
        adapter.probe_long_paths()
