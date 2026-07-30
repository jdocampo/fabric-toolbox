import argparse
from unittest.mock import Mock

import pytest

from fabric_assessment_tool.commands.assess import AssessCommand


def _parser():
    parser = argparse.ArgumentParser()
    AssessCommand().configure_parser(parser)
    return parser


def test_column_options_default_to_collection_without_cap():
    args = _parser().parse_args(["-o", "output"])

    assert args.skip_columns is False
    assert args.max_column_objects is None


def test_column_options_accept_skip_and_positive_cap():
    args = _parser().parse_args(
        ["-o", "output", "--skip-columns", "--max-column-objects", "25"]
    )

    assert args.skip_columns is True
    assert args.max_column_objects == 25


@pytest.mark.parametrize("value", ["0", "-1"])
def test_column_cap_rejects_non_positive_values(value):
    with pytest.raises(SystemExit):
        _parser().parse_args(["-o", "output", "--max-column-objects", value])


def test_handle_passes_column_options_to_assessment_service():
    command = AssessCommand()
    command.assessment_service = Mock()
    command.assessment_service.assess.return_value = {"results": [], "summary": {}}
    args = _parser().parse_args(
        [
            "-o",
            "output",
            "--workspace",
            "workspace",
            "--skip-columns",
            "--max-column-objects",
            "10",
        ]
    )

    command.handle(args)

    command.assessment_service.assess.assert_called_once()
    call = command.assessment_service.assess.call_args.kwargs
    assert call["skip_columns"] is True
    assert call["max_column_objects"] == 10
