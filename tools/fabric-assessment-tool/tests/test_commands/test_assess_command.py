import argparse
from unittest.mock import Mock

import pytest

from fabric_assessment_tool.commands.assess import AssessCommand


def test_assess_flag_is_documented_and_failed_result_exits_nonzero(tmp_path, capsys):
    command = AssessCommand()
    parser = argparse.ArgumentParser()
    command.configure_parser(parser)
    args = parser.parse_args(
        ["--output", str(tmp_path), "--windows-long-paths", "--ws", "workspace"]
    )

    assert "--windows-long-paths" in parser.format_help()
    assert args.windows_long_paths is True
    assess = Mock(
        return_value={
            "summary": {"failed_workspaces": 1},
            "results": [
                {
                    "workspace": "workspace",
                    "status": "failed",
                    "error": "preflight failed",
                }
            ],
        }
    )
    command.assessment_service.assess = assess
    with pytest.raises(SystemExit) as exc:
        command.handle(args)

    assert exc.value.code == 1
    assess.assert_called_once()
    assert assess.call_args.kwargs["windows_long_paths"] is True
    output = capsys.readouterr().out
    assert "Assessment completed with workspace failures." in output
    assert "Assessment completed successfully!" not in output
