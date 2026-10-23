import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fabric_assessment_tool.clients.databricks_client import DatabricksClient
from fabric_assessment_tool.clients.synapse_client import SynapseClient

from ..utils import ui as utils_ui
from .structured_export_service import DecimalEncoder, StructuredExportService
from ..utils.output_paths import (
    ExportPathLengthError,
    OutputPathAdapter,
    OutputPathCapabilityError,
)


class AssessmentStageError(RuntimeError):
    def __init__(self, message: str, stage: str, error_code: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_code = error_code


class AssessmentService:
    """Service for managing assessments of different data platforms."""

    def __init__(self):
        self.clients = {}
        self.export_service = StructuredExportService()

    def assess(
        self,
        source: str,
        mode: str,
        workspaces: List[str],
        output_path: str,
        cloud: str = "azure",
        output_format: str = "json",
        subscription_id: Optional[str] = None,
        auth_method: Optional[str] = None,
        sql_admin_password: Optional[str] = None,
        create_dmv: bool = False,
        sql_auth_mode: str = "sql",
        sql_client_id: Optional[str] = None,
        sql_client_secret: Optional[str] = None,
        sql_tenant_id: Optional[str] = None,
        resources: Optional[List[str]] = None,
        download_notebooks: bool = False,
        windows_long_paths: bool = False,
    ) -> Dict[str, Any]:
        """
        Perform assessment on specified workspaces.

        Args:
            source: Source platform (databricks, synapse)
            mode: Assessment mode (full, etc.)
            workspaces: List of workspace names to assess
            output_path: Base path for output folder structure
            cloud: Cloud provider for the source platform ("azure" or "aws")
            output_format: Export format (json, csv, parquet)
            subscription_id: Azure subscription ID (optional, will use Azure CLI default if not provided)
            auth_method: Authentication method ("azure-cli", "fabric", or None for auto-detect)
            sql_admin_password: SQL admin password for dedicated SQL pools (bypasses interactive prompt)
            create_dmv: Auto-create vTableSizes DMV without confirmation prompt
            sql_auth_mode: SQL pool authentication mode:
                - "sql": Traditional SQL authentication (default)
                - "entra-interactive": Entra ID interactive authentication (browser popup)
                - "entra-spn": Entra ID Service Principal authentication
                - "entra-default": Entra ID default (Azure CLI, managed identity, etc.)
            sql_client_id: Service principal client ID (required for 'entra-spn' mode)
            sql_client_secret: Service principal client secret (required for 'entra-spn' mode)
            sql_tenant_id: Azure tenant ID (optional for 'entra-spn' mode)

        Returns:
            Assessment results dictionary
        """
        output_adapter, legacy_path_limit = self._prepare_output_path(
            output_path, windows_long_paths
        )
        output_path = str(output_adapter.root)

        # print(f"Initializing {source} client...")

        if source == "synapse" and cloud != "azure":
            raise ValueError("Synapse assessments only support --cloud azure")

        if source == "databricks" and cloud == "aws":
            self._validate_aws_databricks_workspace_selection(workspaces)

        if (
            source == "databricks"
            and cloud == "aws"
            and len(workspaces) > 1
            and (
                not os.getenv("DATABRICKS_CLIENT_ID")
                or not os.getenv("DATABRICKS_CLIENT_SECRET")
            )
        ):
            raise ValueError(
                "AWS Databricks assessments with multiple workspaces require "
                "DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET environment "
                "variables. DATABRICKS_TOKEN only supports a single workspace."
            )

        # Get or create client for the source
        client_kwargs: Dict[str, Any] = {}
        if source == "databricks":
            client_kwargs["cloud"] = cloud
        if subscription_id:
            client_kwargs["subscription_id"] = subscription_id
        if auth_method:
            client_kwargs["auth_method"] = auth_method
        if sql_admin_password is not None:
            client_kwargs["sql_admin_password"] = sql_admin_password
        if create_dmv:
            client_kwargs["create_dmv"] = create_dmv
        # SQL auth mode parameters (Synapse only)
        if sql_auth_mode:
            client_kwargs["sql_auth_mode"] = sql_auth_mode
        if sql_client_id:
            client_kwargs["sql_client_id"] = sql_client_id
        if sql_client_secret:
            client_kwargs["sql_client_secret"] = sql_client_secret
        if sql_tenant_id:
            client_kwargs["sql_tenant_id"] = sql_tenant_id
        client = self._get_client(source=source, **client_kwargs)

        # Perform assessment
        assessment_results: Dict[str, Any] = {
            "metadata": {
                "source": source,
                "mode": mode,
                "cloud": cloud,
                "workspaces": workspaces,
                "timestamp": datetime.now().isoformat(),
                "version": "0.3.1",
                "output_format": output_format,
            },
            "results": [],
            "summary": {
                "total_workspaces": len(workspaces),
                "assessed_workspaces": 0,
                "incomplete_workspaces": 0,
                "failed_workspaces": 0,
            },
        }

        export_results: Dict[str, Any] = {"results": []}

        if not workspaces or len(workspaces) == 0:
            # Get all workspaces from the client and let the client choose which ones to assess
            client_workspaces = client.get_workspaces()
            workspace_names = [workspace.name for workspace in client_workspaces]
            workspaces = utils_ui.prompt_select_items(
                "Select workspaces:", workspace_names
            )
            for workspace_str in workspaces:
                utils_ui.print_grey(workspace_str)
            utils_ui.print_grey("------------------------------")
            if not utils_ui.prompt_confirm():
                utils_ui.print_fabric_assessment_tool("Aborted.")
                return {}

        # Assess each workspace
        for workspace in workspaces:
            # print(f"Assessing workspace: {workspace}")
            workspace_assessment = None
            export_resources = resources
            try:
                # Get assessment data as dataclass object
                if source == "databricks":
                    workspace_assessment = client.assess_workspace(
                        workspace,
                        mode,
                        resources=resources,
                        output_path=output_path,
                        download_notebooks=download_notebooks,
                        output_adapter=output_adapter,
                    )
                else:
                    workspace_assessment = client.assess_workspace(workspace, mode)

                # Export the assessment data using the structured export service
                # When jobs are re-extracted, notebooks are re-annotated with
                # job execution data — ensure they are also rewritten on disk.
                export_resources = resources
                if resources and "jobs" in resources and "notebooks" not in resources:
                    export_resources = list(resources) + ["notebooks"]

                export_result = self._export_workspace(
                    workspace_assessment,
                    workspace,
                    output_path,
                    output_format,
                    export_resources,
                    output_adapter,
                    legacy_path_limit,
                )

                export_results["results"].append(export_result)

                # Determine the result status based on the assessment status
                assessment_status = workspace_assessment.status.status
                result_status = (
                    "success" if assessment_status == "completed" else "incomplete"
                )

                result_entry = {
                    "workspace": workspace,
                    "status": result_status,
                    "summary": workspace_assessment.get_summary(),
                    # "export_info": export_result,
                }

                # Include assessment status details if incomplete
                if assessment_status == "incomplete":
                    result_entry["assessment_status"] = {
                        "status": assessment_status,
                        "description": workspace_assessment.status.description,
                    }
                    assessment_results["summary"]["incomplete_workspaces"] += 1
                else:
                    assessment_results["summary"]["assessed_workspaces"] += 1

                assessment_results["results"].append(result_entry)
                # This is redundant now, as export info is included in each result
                # assessment_results["export_results"].append(export_result)

            except ExportPathLengthError as e:
                utils_ui.print_error(str(e))
                retried_export_result = None
                retry_error = None
                if workspace_assessment is not None:
                    retried_export_result, retry_error = (
                        self._retry_with_extended_paths(
                            e,
                            workspace_assessment,
                            workspace,
                            output_path,
                            output_format,
                            export_resources,
                        )
                    )
                if retried_export_result is not None:
                    assert workspace_assessment is not None
                    export_results["results"].append(retried_export_result)
                    assessment_status = workspace_assessment.status.status
                    result_status = (
                        "success" if assessment_status == "completed" else "incomplete"
                    )
                    result_entry = {
                        "workspace": workspace,
                        "status": result_status,
                        "summary": workspace_assessment.get_summary(),
                    }
                    if assessment_status == "incomplete":
                        result_entry["assessment_status"] = {
                            "status": assessment_status,
                            "description": workspace_assessment.status.description,
                        }
                        assessment_results["summary"]["incomplete_workspaces"] += 1
                    else:
                        assessment_results["summary"]["assessed_workspaces"] += 1
                    assessment_results["results"].append(result_entry)
                    continue
                if retry_error is not None:
                    utils_ui.print_error(
                        f"Extended-path retry failed for workspace {workspace}: {retry_error}"
                    )
                    failure = retry_error
                else:
                    failure = e
                assessment_results["results"].append(
                    {
                        "workspace": workspace,
                        "status": "failed",
                        "stage": getattr(failure, "stage", e.stage),
                        "error_code": getattr(failure, "error_code", e.error_code),
                        "error": str(failure),
                    }
                )
                assessment_results["summary"]["failed_workspaces"] += 1
            except Exception as e:
                utils_ui.print_error(f"Failed to assess workspace {workspace}: {e}")
                assessment_results["results"].append(
                    {
                        "workspace": workspace,
                        "status": "failed",
                        "stage": getattr(e, "stage", "assessment"),
                        "error_code": getattr(e, "error_code", None),
                        "error": str(e),
                    }
                )
                assessment_results["summary"]["failed_workspaces"] += 1

        # Save overall assessment summary
        summary_file = self._save_assessment_summary(
            assessment_results, output_path, output_adapter
        )
        export_summary_file = self._save_export_results(
            export_results, output_path, output_adapter
        )

        utils_ui.print("")
        utils_ui.print(f"Assessment summary saved to: {summary_file}")
        utils_ui.print(f"Export results summary saved to: {export_summary_file}")
        utils_ui.print(f"Individual workspace details saved in: {output_path}")

        return assessment_results

    def _prepare_output_path(
        self, output_path: str, windows_long_paths: bool
    ) -> tuple[OutputPathAdapter, bool]:
        adapter = OutputPathAdapter(output_path, windows_long_paths=windows_long_paths)
        if windows_long_paths and not adapter.is_windows:
            raise ValueError(
                "--windows-long-paths is Windows-only; omit this option on non-Windows systems."
            )

        legacy_path_limit = False
        if adapter.is_windows:
            try:
                capability = adapter.probe_long_paths()
            except OutputPathCapabilityError as probe_error:
                probe_error.stage = "path_capability_probe"
                probe_error.error_code = "WINDOWS_PATH_CAPABILITY_FAILED"
                raise
            except OSError as probe_error:
                raise OutputPathCapabilityError(
                    f"Could not probe Windows output-path capability beneath "
                    f"{adapter.root}: {probe_error}"
                ) from probe_error
            if windows_long_paths and capability != "supported":
                mode_error = OutputPathCapabilityError(
                    "The destination could not create, read, and remove an extended-length "
                    "probe path. FAT will not collect data or silently fall back to ordinary paths."
                )
                mode_error.stage = "path_capability_probe"
                mode_error.error_code = "WINDOWS_LONG_PATHS_UNAVAILABLE"
                raise mode_error
            legacy_path_limit = not windows_long_paths and capability == "legacy"
            if legacy_path_limit:
                utils_ui.print_warning(
                    "Windows legacy path limits apply to this FAT process.\n"
                    "FAT will validate all export paths after collection and before writing "
                    "assessment output. A short absolute output directory is recommended, "
                    "for example: -o C:\\FAT\n"
                    "Alternatively, rerun with --windows-long-paths to use extended-length "
                    "absolute Windows paths."
                )

        summary_files = [
            adapter.root / "assessment_summary.json",
            adapter.root / "export_results_summary.json",
        ]
        violations = adapter.validate(
            summary_files, [adapter.root], legacy_limit=legacy_path_limit
        )
        if violations:
            legacy_failure = legacy_path_limit and any(
                violation.kind in ("file path", "directory creation path")
                for violation in violations
            )
            raise ExportPathLengthError(
                "invocation", violations, legacy_limit=legacy_failure
            )
        return adapter, legacy_path_limit

    def _export_workspace(
        self,
        assessment_data: Any,
        workspace_name: str,
        output_path: str,
        output_format: str,
        resources: Optional[List[str]],
        output_adapter: OutputPathAdapter,
        legacy_path_limit: bool,
    ) -> Dict[str, Any]:
        return self.export_service.export_assessment(
            assessment_data=assessment_data,
            workspace_name=workspace_name,
            output_path=output_path,
            format=output_format,
            resources=resources,
            output_adapter=output_adapter,
            legacy_path_limit=legacy_path_limit,
        )

    def _retry_with_extended_paths(
        self,
        path_error: ExportPathLengthError,
        assessment_data: Any,
        workspace_name: str,
        output_path: str,
        output_format: str,
        resources: Optional[List[str]],
    ) -> tuple[Optional[Dict[str, Any]], Optional[Exception]]:
        if not path_error.legacy_limit or not (
            sys.stdin.isatty() and sys.stdout.isatty()
        ):
            return None, None

        use_extended_paths = utils_ui.prompt_confirm(
            f"Workspace {workspace_name} has already been collected. Retry its export "
            "using Windows extended-length paths? This changes no Windows settings."
        )
        if use_extended_paths is not True:
            return None, None

        adapter = OutputPathAdapter(output_path, windows_long_paths=True)
        try:
            if adapter.probe_long_paths() != "supported":
                raise OutputPathCapabilityError(
                    "The destination's extended-length probe failed; no retry was attempted."
                )
            result = self._export_workspace(
                assessment_data,
                workspace_name,
                output_path,
                output_format,
                resources,
                adapter,
                False,
            )
            return result, None
        except (ExportPathLengthError, OutputPathCapabilityError) as error:
            if isinstance(error, OutputPathCapabilityError):
                error.stage = "path_capability_probe"
                error.error_code = "WINDOWS_LONG_PATHS_UNAVAILABLE"
            return None, error
        except OSError as error:
            capability_error = OutputPathCapabilityError(
                f"Could not probe extended-length paths beneath {adapter.root}: {error}"
            )
            capability_error.error_code = "WINDOWS_LONG_PATHS_UNAVAILABLE"
            return None, capability_error
        except Exception as error:
            return None, AssessmentStageError(
                str(error), "export", "EXPORT_RETRY_FAILED"
            )

    def _get_client(self, source: str, **kwargs) -> Any:
        """Get or create API client for the specified source."""
        client_key = f"{source}_{hash(str(kwargs))}"

        if client_key not in self.clients:
            if source == "synapse":
                self.clients[client_key] = SynapseClient(**kwargs)
            elif source == "databricks":
                self.clients[client_key] = DatabricksClient(**kwargs)
            else:
                raise ValueError(f"Unsupported source: {source}")

        return self.clients[client_key]

    def _validate_aws_databricks_workspace_selection(
        self, workspaces: List[str]
    ) -> None:
        """Validate AWS Databricks workspace selection authentication."""
        if workspaces:
            return

        required_vars = [
            "DATABRICKS_ACCOUNT_ID",
            "DATABRICKS_CLIENT_ID",
            "DATABRICKS_CLIENT_SECRET",
        ]
        missing_vars = [
            var_name for var_name in required_vars if not os.getenv(var_name)
        ]
        if missing_vars:
            raise ValueError(
                "AWS Databricks assessments without --workspace/-ws require "
                "account API authentication to list workspaces. Missing "
                f"environment variable(s): {', '.join(missing_vars)}."
            )

    def _save_assessment_summary(
        self,
        results: Dict[str, Any],
        output_path: str,
        output_adapter: Optional[OutputPathAdapter] = None,
    ) -> str:
        """Save overall assessment summary to the output directory."""
        adapter = output_adapter or OutputPathAdapter(output_path)
        adapter.mkdir(adapter.root, parents=True, exist_ok=True)

        summary_file = str(adapter.root / "assessment_summary.json")
        with adapter.open(summary_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, cls=DecimalEncoder)

        return summary_file

    def _save_export_results(
        self,
        results: Dict[str, Any],
        output_path: str,
        output_adapter: Optional[OutputPathAdapter] = None,
    ) -> str:
        """Save export results summary to the output directory."""
        adapter = output_adapter or OutputPathAdapter(output_path)
        adapter.mkdir(adapter.root, parents=True, exist_ok=True)

        export_summary_file = str(adapter.root / "export_results_summary.json")
        with adapter.open(export_summary_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, cls=DecimalEncoder)

        return export_summary_file

    def _save_results(
        self,
        results: Dict[str, Any],
        output_path: str,
        output_adapter: Optional[OutputPathAdapter] = None,
    ) -> None:
        """Legacy method for backward compatibility."""
        adapter = output_adapter or OutputPathAdapter(output_path)
        # For backward compatibility, save as single file if output_path ends with .json
        if output_path.endswith(".json"):
            parent = Path(output_path).parent
            adapter.mkdir(parent, parents=True, exist_ok=True)
            with adapter.open(output_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, cls=DecimalEncoder)
            print(f"Assessment results saved to: {output_path}")
        else:
            # Use new structured approach
            self._save_assessment_summary(results, output_path, adapter)
