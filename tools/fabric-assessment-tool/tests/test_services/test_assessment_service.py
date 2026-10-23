"""Tests for AssessmentService."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from fabric_assessment_tool.services.assessment_service import AssessmentService
from fabric_assessment_tool.utils.output_paths import (
    ExportPathLengthError,
    PathViolation,
)


def test_aws_databricks_without_workspace_requires_account_api_auth(tmp_path):
    service = AssessmentService()

    with pytest.raises(ValueError, match="DATABRICKS_ACCOUNT_ID"):
        service.assess(
            source="databricks",
            mode="full",
            workspaces=[],
            output_path=str(tmp_path),
            cloud="aws",
        )


@patch.dict(
    os.environ,
    {
        "DATABRICKS_ACCOUNT_ID": "account-123",
        "DATABRICKS_CLIENT_ID": "client-id",
        "DATABRICKS_CLIENT_SECRET": "client-secret",
    },
    clear=True,
)
@patch("fabric_assessment_tool.services.assessment_service.utils_ui.prompt_confirm")
@patch(
    "fabric_assessment_tool.services.assessment_service.utils_ui.prompt_select_items"
)
def test_aws_databricks_without_workspace_lists_account_workspaces(
    mock_prompt_select_items, mock_prompt_confirm, tmp_path
):
    service = AssessmentService()
    fake_client = MagicMock()
    fake_client.get_workspaces.return_value = [
        SimpleNamespace(name="workspace-a"),
        SimpleNamespace(name="workspace-b"),
    ]
    mock_prompt_select_items.return_value = []
    mock_prompt_confirm.return_value = False

    with patch.object(service, "_get_client", return_value=fake_client):
        result = service.assess(
            source="databricks",
            mode="full",
            workspaces=[],
            output_path=str(tmp_path),
            cloud="aws",
        )

    assert result == {}
    fake_client.get_workspaces.assert_called_once_with()
    mock_prompt_select_items.assert_called_once_with(
        "Select workspaces:", ["workspace-a", "workspace-b"]
    )


@patch.dict(
    os.environ,
    {
        "DATABRICKS_HOST": "https://dbc-example.cloud.databricks.com",
        "DATABRICKS_TOKEN": "test-token",
    },
    clear=True,
)
def test_aws_databricks_multiple_workspaces_requires_oauth(tmp_path):
    service = AssessmentService()

    with pytest.raises(ValueError, match="DATABRICKS_CLIENT_ID"):
        service.assess(
            source="databricks",
            mode="full",
            workspaces=["workspace-a", "workspace-b"],
            output_path=str(tmp_path),
            cloud="aws",
        )


def test_windows_long_path_option_is_rejected_before_client_creation(tmp_path):
    service = AssessmentService()
    fake_adapter = SimpleNamespace(is_windows=False, root=tmp_path)

    with patch(
        "fabric_assessment_tool.services.assessment_service.OutputPathAdapter",
        return_value=fake_adapter,
    ):
        with patch.object(service, "_get_client") as get_client:
            with pytest.raises(ValueError, match="Windows-only"):
                service.assess(
                    source="synapse",
                    mode="full",
                    workspaces=["workspace"],
                    output_path=str(tmp_path),
                    windows_long_paths=True,
                )

    get_client.assert_not_called()


def test_invocation_summary_paths_are_validated_before_client_creation(tmp_path):
    service = AssessmentService()
    adapter = MagicMock()
    adapter.is_windows = True
    adapter.root = tmp_path
    adapter.probe_long_paths.return_value = "legacy"
    adapter.validate.return_value = [
        PathViolation(
            str(tmp_path / "assessment_summary.json"),
            260,
            259,
            "file path",
        )
    ]

    with patch(
        "fabric_assessment_tool.services.assessment_service.OutputPathAdapter",
        return_value=adapter,
    ):
        with patch.object(service, "_get_client") as get_client:
            with pytest.raises(ExportPathLengthError) as exc:
                service.assess(
                    source="synapse",
                    mode="full",
                    workspaces=["workspace"],
                    output_path=str(tmp_path),
                )

    assert exc.value.stage == "export_preflight"
    assert exc.value.error_code == "WINDOWS_PATH_LIMIT_EXCEEDED"
    adapter.probe_long_paths.assert_called_once_with()
    get_client.assert_not_called()


def test_legacy_path_failure_prompts_to_retry_collected_workspace(tmp_path):
    import sys

    import fabric_assessment_tool.services.assessment_service as service_module

    service = AssessmentService()
    adapter = MagicMock()
    adapter.probe_long_paths.return_value = "supported"
    workspace_data = object()
    path_error = ExportPathLengthError(
        "workspace",
        [PathViolation(r"C:\FAT\workspace\very-long-file.json", 260, 259, "file path")],
        legacy_limit=True,
    )
    with (
        patch.object(
            service_module, "OutputPathAdapter", return_value=adapter
        ) as factory,
        patch.object(service_module.utils_ui, "prompt_confirm", return_value=True),
        patch.object(
            service, "_export_workspace", return_value={"workspace_name": "workspace"}
        ) as export,
        patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: True)),
        patch.object(sys, "stdout", SimpleNamespace(isatty=lambda: True)),
    ):
        result, error = service._retry_with_extended_paths(
            path_error,
            workspace_data,
            "workspace",
            str(tmp_path),
            "json",
            None,
        )

    assert result == {"workspace_name": "workspace"}
    assert error is None
    factory.assert_called_once_with(str(tmp_path), windows_long_paths=True)
    adapter.probe_long_paths.assert_called_once_with()
    export.assert_called_once_with(
        workspace_data,
        "workspace",
        str(tmp_path),
        "json",
        None,
        adapter,
        False,
    )
