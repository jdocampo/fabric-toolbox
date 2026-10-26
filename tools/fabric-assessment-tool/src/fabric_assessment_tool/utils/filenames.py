"""Helpers for converting discovered names into safe filesystem components."""

import unicodedata

_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{digit}" for digit in "123456789¹²³"),
        *(f"lpt{digit}" for digit in "123456789¹²³"),
    }
)


def _percent_encode(character: str) -> str:
    return "".join(
        f"%{byte:02X}" for byte in character.encode("utf-8", errors="surrogatepass")
    )


def safe_filename_component(value: object) -> str:
    """Return a deterministic, reversible-safe single path component.

    Filesystem-valid characters, including spaces and Unicode, are preserved.
    Unsupported characters are UTF-8 percent-encoded, and literal percent
    signs are encoded as well so the representation remains unambiguous.
    """
    name = str(value)
    if not name:
        return "unknown"

    device_basename = name.split(".", maxsplit=1)[0].rstrip(" .").casefold()
    reserved_device_name = device_basename in _WINDOWS_RESERVED_NAMES
    dot_component = name in {".", ".."}
    trailing_suffix_start = len(name.rstrip(" ."))
    encoded = []

    for index, character in enumerate(name):
        unsupported = (
            character == "%"
            or character in _WINDOWS_INVALID_CHARACTERS
            or unicodedata.category(character) in {"Cc", "Cs"}
            or (index >= trailing_suffix_start and character in " .")
            or (dot_component and character == ".")
            or (reserved_device_name and index == 0)
        )
        encoded.append(_percent_encode(character) if unsupported else character)

    return "".join(encoded) or "unknown"
