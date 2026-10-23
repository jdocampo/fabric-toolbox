"""Windows-aware output path normalization, validation, and capability probing."""

import errno
import ntpath
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

LEGACY_MAX_FILE_PATH = 259
LEGACY_MAX_DIRECTORY_PATH = 248
EXTENDED_MAX_PATH = 32767
WINDOWS_MAX_COMPONENT = 255
MAX_ERROR_EXAMPLES = 5
WINDOWS_LONG_PATH_DOCUMENTATION = (
    "https://learn.microsoft.com/en-us/windows/win32/fileio/"
    "maximum-file-path-limitation"
)


class OutputPathCapabilityError(RuntimeError):
    """Raised when effective destination path support cannot be determined."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.stage = "path_capability_probe"
        self.error_code = "WINDOWS_PATH_CAPABILITY_FAILED"


@dataclass(frozen=True)
class PathViolation:
    path: str
    length: int
    limit: int
    kind: str


class ExportPathLengthError(ValueError):
    """Raised when a complete export plan contains unsupported output paths."""

    def __init__(
        self,
        workspace_name: str,
        violations: list[PathViolation],
        *,
        legacy_limit: bool,
        stage: str = "export_preflight",
    ) -> None:
        self.workspace_name = workspace_name
        self.violations = violations
        self.legacy_limit = legacy_limit
        self.stage = stage
        self.error_code = (
            "WINDOWS_PATH_LIMIT_EXCEEDED" if legacy_limit else "OUTPUT_PATH_INVALID"
        )
        longest = max(violations, key=lambda violation: utf16_length(violation.path))
        failure_counts: dict[str, int] = {}
        for violation in violations:
            failure_counts[violation.kind] = failure_counts.get(violation.kind, 0) + 1
        lines = [
            (
                "Export blocked: Windows path-length limit exceeded."
                if legacy_limit
                else "Export blocked: output path constraints were exceeded."
            ),
            "",
            f"Workspace: {workspace_name}",
            f"Stage: {stage}",
            f"Invalid planned paths: {len(violations)}",
            "Failures by constraint: "
            + ", ".join(
                f"{count} {kind}" for kind, count in sorted(failure_counts.items())
            ),
            f"Longest offending path: {utf16_length(longest.path)} UTF-16 code units "
            "(conventional absolute path)",
            f"Applicable limit: {longest.limit} UTF-16 code units ({longest.kind}; "
            f"measured {longest.length})",
            "",
            "Offending path:",
            longest.path,
            "",
            "No assessment export files were created, replaced, or deleted for this workspace.",
            "",
            f"Representative offending paths (up to {MAX_ERROR_EXAMPLES}):",
        ]
        for violation in sorted(violations, key=lambda item: item.length, reverse=True)[
            :MAX_ERROR_EXAMPLES
        ]:
            lines.append(
                f"- {violation.path} ({violation.length} units; limit "
                f"{violation.limit}, {violation.kind})"
            )
        lines.extend(
            [
                "",
                "Choose one of these alternatives:",
                "1. Use a shorter absolute output directory, for example -o C:\\FAT. "
                "Recheck the complete plan; a shorter root is not guaranteed to resolve every path.",
                "2. Rerun with --windows-long-paths to use Windows extended-length absolute paths "
                "(\\\\?\\), including the UNC equivalent. This does not change Windows settings; "
                "Explorer and external tools may still have difficulty consuming the output.",
                "3. Enable Windows long-path support with a compatible Python runtime, then restart FAT.",
                "4. Run FAT on Linux, macOS, or a suitable Unix filesystem. For WSL, use its Linux "
                "filesystem rather than assuming a Windows-mounted destination removes restrictions.",
                f"Windows path documentation: {WINDOWS_LONG_PATH_DOCUMENTATION}",
                "Collected assessment data is held only in memory; FAT does not support checkpoint/resume.",
            ]
        )
        if not legacy_limit:
            lines.append(
                "The explicit extended-path option may not resolve component-length or "
                "extended-path constraints."
            )
        elif any(violation.kind == "path component" for violation in violations):
            lines.append(
                "The explicit extended-path option does not remove component-length limits."
            )
        super().__init__("\n".join(lines))


class OutputPathAdapter:
    """Keep readable absolute paths internally and prefix only filesystem operations."""

    def __init__(
        self,
        output_path: str,
        *,
        windows_long_paths: bool = False,
        is_windows: Optional[bool] = None,
    ) -> None:
        self.is_windows = os.name == "nt" if is_windows is None else is_windows
        self.windows_long_paths = windows_long_paths
        if self.is_windows:
            self.root = Path(self._normalize_windows(output_path))
        else:
            self.root = Path(os.path.abspath(os.path.expanduser(output_path)))

    @staticmethod
    def _strip_windows_prefix(path: str) -> str:
        path = path.replace("/", "\\")
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
        return path

    @classmethod
    def _normalize_windows(cls, path: str) -> str:
        expanded = os.path.expanduser(path)
        without_prefix = cls._strip_windows_prefix(expanded)
        return ntpath.normpath(ntpath.abspath(without_prefix))

    def normalize(self, path: os.PathLike | str) -> str:
        value = os.fspath(path)
        if self.is_windows:
            if ntpath.isabs(value) or value.startswith("\\\\?\\"):
                return self._normalize_windows(value)
            return ntpath.normpath(ntpath.join(str(self.root), value))
        return os.path.abspath(os.path.normpath(value))

    def filesystem_path(self, path: os.PathLike | str) -> str:
        normalized = self.normalize(path)
        if not (self.is_windows and self.windows_long_paths):
            return normalized
        if normalized.startswith("\\\\"):
            return "\\\\?\\UNC\\" + normalized[2:]
        if len(normalized) >= 3 and normalized[1:3] == ":\\":
            return "\\\\?\\" + normalized
        raise ValueError(
            f"Windows extended-length paths require an absolute drive or UNC path: {normalized}"
        )

    def open(self, path: os.PathLike | str, mode: str = "r", **kwargs):
        return open(self.filesystem_path(path), mode, **kwargs)

    def exists(self, path: os.PathLike | str) -> bool:
        return os.path.exists(self.filesystem_path(path))

    def mkdir(
        self,
        path: os.PathLike | str,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        target = self.filesystem_path(path)
        if parents:
            os.makedirs(target, exist_ok=exist_ok)
        else:
            try:
                os.mkdir(target)
            except FileExistsError:
                if not exist_ok or not os.path.isdir(target):
                    raise

    def remove_tree(self, path: os.PathLike | str) -> None:
        shutil.rmtree(self.filesystem_path(path))

    def glob(self, directory: os.PathLike | str, pattern: str) -> list[Path]:
        base = self.filesystem_path(directory)
        return [Path(item) for item in Path(base).glob(pattern)]

    def validate(
        self,
        files: Iterable[os.PathLike | str],
        directories: Iterable[os.PathLike | str] = (),
        *,
        legacy_limit: bool,
    ) -> list[PathViolation]:
        if not self.is_windows:
            return []
        file_paths = {self.normalize(path) for path in files}
        directory_paths = {self.normalize(path) for path in directories}
        for path in file_paths | directory_paths:
            parent = os.path.dirname(path)
            while parent and parent != path:
                directory_paths.add(parent)
                path, parent = parent, os.path.dirname(parent)

        violations: dict[tuple[str, str], PathViolation] = {}
        for path in file_paths:
            self._validate_components(path, violations)
            length = utf16_length(path)
            if legacy_limit and length > LEGACY_MAX_FILE_PATH:
                violation = PathViolation(
                    path, length, LEGACY_MAX_FILE_PATH, "file path"
                )
                violations[(path, violation.kind)] = violation
            elif (
                not legacy_limit
                and utf16_length(self.filesystem_path(path)) > EXTENDED_MAX_PATH
            ):
                violation = PathViolation(
                    path,
                    utf16_length(self.filesystem_path(path)),
                    EXTENDED_MAX_PATH,
                    "extended file path",
                )
                violations[(path, violation.kind)] = violation

        for path in directory_paths:
            self._validate_components(path, violations)
            length = utf16_length(path)
            if legacy_limit and length > LEGACY_MAX_DIRECTORY_PATH:
                violation = PathViolation(
                    path, length, LEGACY_MAX_DIRECTORY_PATH, "directory creation path"
                )
                violations[(path, violation.kind)] = violation
            elif (
                not legacy_limit
                and utf16_length(self.filesystem_path(path)) > EXTENDED_MAX_PATH
            ):
                violation = PathViolation(
                    path,
                    utf16_length(self.filesystem_path(path)),
                    EXTENDED_MAX_PATH,
                    "extended directory path",
                )
                violations[(path, violation.kind)] = violation
        return list(violations.values())

    def _validate_components(
        self, path: str, violations: dict[tuple[str, str], PathViolation]
    ) -> None:
        if not self.is_windows:
            return
        normalized = path.replace("/", "\\")
        _, remainder = ntpath.splitdrive(normalized)
        for component in remainder.split("\\"):
            if not component:
                continue
            length = utf16_length(component)
            if length > WINDOWS_MAX_COMPONENT:
                violation = PathViolation(
                    path, length, WINDOWS_MAX_COMPONENT, "path component"
                )
                violations[(path, violation.kind)] = violation

    def probe_long_paths(self) -> str:
        """Probe the destination filesystem; return `supported` or `legacy`."""
        ancestor = self.root
        while not self.exists(ancestor):
            parent = ancestor.parent
            if parent == ancestor:
                raise OutputPathCapabilityError(
                    f"Cannot find an existing ancestor for output directory {self.root}."
                )
            ancestor = parent
        if not os.path.isdir(self.filesystem_path(ancestor)):
            raise OutputPathCapabilityError(
                f"Output path ancestor is not a directory: {ancestor}"
            )

        if not self.windows_long_paths:
            while utf16_length(str(ancestor)) > 254 and ancestor.parent != ancestor:
                ancestor = ancestor.parent
        available_component_length = (
            LEGACY_MAX_FILE_PATH - utf16_length(str(ancestor)) - 1
        )
        if not self.windows_long_paths and available_component_length < 4:
            raise OutputPathCapabilityError(
                f"Cannot create an isolated ordinary-path probe beneath {ancestor}; "
                "the destination ancestor is too long. Choose a shorter output root."
            )
        probe_component_length = min(
            8,
            available_component_length if not self.windows_long_paths else 8,
        )
        for _ in range(10):
            probe_root = ancestor / secrets.token_hex(4)[:probe_component_length]
            if not self.exists(probe_root):
                break
        else:
            raise OutputPathCapabilityError(
                f"Could not reserve a unique isolated probe directory beneath {ancestor}."
            )
        created: list[str] = []
        primary_error: Optional[BaseException] = None
        long_path_failure: Optional[OSError] = None
        try:
            os.mkdir(self.filesystem_path(probe_root))
            created.append(str(probe_root))
            parent = probe_root
            part_index = 0
            while utf16_length(str(parent)) < 220:
                component = f"p{part_index}-" + ("x" * 35)
                next_path = parent / component
                if utf16_length(str(next_path)) > 230:
                    break
                os.mkdir(self.filesystem_path(next_path))
                created.append(str(next_path))
                parent = next_path
                part_index += 1

            parent_length = utf16_length(str(parent))
            component_length = max(1, LEGACY_MAX_FILE_PATH + 2 - parent_length)
            if not 1 <= component_length <= WINDOWS_MAX_COMPONENT:
                raise OutputPathCapabilityError(
                    f"Cannot construct a reliable long-path probe beneath {ancestor}; "
                    "the destination's existing path is too long."
                )
            if (
                self.windows_long_paths
                and utf16_length(self.filesystem_path(parent)) + 1 + component_length
                > EXTENDED_MAX_PATH
            ):
                raise OutputPathCapabilityError(
                    f"Cannot construct a long-path probe within the extended-path limit "
                    f"beneath {ancestor}."
                )
            directory_probe = parent / ("d" * component_length)
            file_component = ("f" * (component_length - 4)) + ".tmp"
            file_probe = parent / file_component

            try:
                os.mkdir(self.filesystem_path(directory_probe))
                created.append(str(directory_probe))
            except OSError as error:
                if not self._is_path_length_error(error):
                    raise OutputPathCapabilityError(
                        f"Windows path capability probe could not create {directory_probe}: {error}"
                    ) from error
                long_path_failure = error

            try:
                created.append(str(file_probe))
                with open(
                    self.filesystem_path(file_probe), "w", encoding="utf-8"
                ) as stream:
                    stream.write("probe")
                with open(
                    self.filesystem_path(file_probe), "r", encoding="utf-8"
                ) as stream:
                    if stream.read() != "probe":
                        raise OutputPathCapabilityError(
                            "Windows path capability probe could not verify its test file."
                        )
            except OSError as error:
                if not self._is_path_length_error(error):
                    raise OutputPathCapabilityError(
                        f"Windows path capability probe could not write/read {file_probe}: {error}"
                    ) from error
                long_path_failure = long_path_failure or error

            if long_path_failure:
                return "legacy"
            return "supported"
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                for path in reversed(created):
                    if os.path.isdir(self.filesystem_path(path)):
                        os.rmdir(self.filesystem_path(path))
                    elif os.path.exists(self.filesystem_path(path)):
                        os.remove(self.filesystem_path(path))
                if os.path.isdir(self.filesystem_path(probe_root)):
                    os.rmdir(self.filesystem_path(probe_root))
            except OSError as cleanup_error:
                message = (
                    f"Windows path capability probe cleanup failed: {cleanup_error}"
                )
                if primary_error:
                    raise OutputPathCapabilityError(
                        f"{primary_error}; additionally, {message}"
                    ) from cleanup_error
                raise OutputPathCapabilityError(message) from cleanup_error

    @staticmethod
    def _is_path_length_error(error: OSError) -> bool:
        return (
            getattr(error, "winerror", None) == 206
            or error.errno == errno.ENAMETOOLONG
            or error.errno == errno.ELOOP
        )


def utf16_length(value: str) -> int:
    """Count Windows path units excluding any terminating null."""
    return len(value.encode("utf-16-le")) // 2


def format_path_error(
    workspace_name: str,
    adapter: OutputPathAdapter,
    files: Iterable[os.PathLike | str],
    directories: Iterable[os.PathLike | str],
    *,
    legacy_limit: bool,
) -> Optional[ExportPathLengthError]:
    violations = adapter.validate(files, directories, legacy_limit=legacy_limit)
    if not violations:
        return None
    legacy_failure = legacy_limit and any(
        violation.kind in ("file path", "directory creation path")
        for violation in violations
    )
    return ExportPathLengthError(
        workspace_name, violations, legacy_limit=legacy_failure
    )
