import pytest
from urllib.parse import unquote

from fabric_assessment_tool.utils.filenames import safe_filename_component


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("plain name", "plain name"),
        ("café", "café"),
        ("line\nbreak", "line%0Abreak"),
        ("nul\x00byte", "nul%00byte"),
        ("bad:name?*", "bad%3Aname%3F%2A"),
        ("folder/name\\child", "folder%2Fname%5Cchild"),
        ("literal%0A", "literal%250A"),
        ("trailing. ", "trailing%2E%20"),
        (".", "%2E"),
        ("..", "%2E%2E"),
        ("CON.txt", "%43ON.txt"),
        ("CON .txt", "%43ON .txt"),
        ("lpt¹", "%6Cpt¹"),
        ("", "unknown"),
    ],
)
def test_safe_filename_component_encodes_unsupported_names(name, expected):
    encoded = safe_filename_component(name)
    assert encoded == expected
    if name:
        assert unquote(encoded) == name


def test_safe_filename_component_is_deterministic_and_avoids_lossy_collisions():
    unsafe = safe_filename_component("a\nb")
    literal_encoding = safe_filename_component("a%0Ab")

    assert unsafe == "a%0Ab"
    assert literal_encoding == "a%250Ab"
    assert unsafe != literal_encoding
    assert safe_filename_component("a\nb") == unsafe
