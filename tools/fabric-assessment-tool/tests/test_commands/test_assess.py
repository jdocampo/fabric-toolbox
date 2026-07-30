import argparse
from unittest.mock import MagicMock

import pytest

from fabric_assessment_tool.commands.assess import AssessCommand


def make_parser():
    parser = argparse.ArgumentParser()
    AssessCommand().configure_parser(parser)
    return parser


def test_definition_options_parse():
    args = make_parser().parse_args(
        [
            "--output",
            "out",
            "--extract-definitions",
            "--definition-redaction",
            "hash",
            "--definition-schema-filter",
            "dbo, reporting",
            "--max-definition-size",
            "4096",
        ]
    )

    assert args.extract_definitions is True
    assert args.definition_redaction == "hash"
    assert args.definition_schema_filter == "dbo, reporting"
    assert args.max_definition_size == 4096


def test_negative_definition_size_is_rejected():
    with pytest.raises(SystemExit):
        make_parser().parse_args(["--output", "out", "--max-definition-size", "-1"])


def test_definition_options_are_forwarded_to_assessment_service():
    command = AssessCommand()
    command.assessment_service = MagicMock()
    command.assessment_service.assess.return_value = {}
    args = make_parser().parse_args(
        [
            "--output",
            "out",
            "--workspace",
            "workspace",
            "--extract-definitions",
            "--definition-redaction",
            "partial",
            "--definition-schema-filter",
            "dbo, reporting,",
            "--max-definition-size",
            "2048",
        ]
    )

    command.handle(args)

    command.assessment_service.assess.assert_called_once()
    kwargs = command.assessment_service.assess.call_args.kwargs
    assert kwargs["extract_definitions"] is True
    assert kwargs["definition_redaction"] == "partial"
    assert kwargs["definition_schema_filter"] == ["dbo", "reporting"]
    assert kwargs["max_definition_size"] == 2048
