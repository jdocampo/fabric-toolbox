import json
import os
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from fabric_assessment_tool.assessment.common import AssessmentStatus
from fabric_assessment_tool.assessment.databricks import (
    DatabricksAssessment,
    DatabricksAssessmentMetadata,
    DatabricksAlerts,
    DatabricksCatalogs,
    DatabricksClusters,
    DatabricksCluster,
    DatabricksClusterPolicies,
    DatabricksConnections,
    DatabricksExperiments,
    DatabricksExternalLocations,
    DatabricksGenieSpaces,
    DatabricksInstancePools,
    DatabricksJobs,
    DatabricksNotebooks,
    DatabricksPipelines,
    DatabricksRepos,
    DatabricksSecretScopes,
    DatabricksServingEndpoints,
    DatabricksSqlWarehouses,
    DatabricksWorkspaceInfo,
)
from fabric_assessment_tool.services.structured_export_service import JSONExporter
from fabric_assessment_tool.utils.output_paths import (
    ExportPathLengthError,
    OutputPathAdapter,
)


def make_databricks_assessment(cluster_name="sample"):
    return DatabricksAssessment(
        status=AssessmentStatus(status="completed"),
        workspace_info=DatabricksWorkspaceInfo(
            id="workspace-id",
            name="workspace",
            resource_group="resource-group",
            url="https://example.invalid",
            status="Running",
            tier="premium",
            json_response={},
        ),
        clusters=DatabricksClusters(
            clusters=[
                DatabricksCluster(
                    cluster_id="cluster-id",
                    cluster_name=cluster_name,
                    state="RUNNING",
                    node_type_id="node",
                    cluster_cores=4,
                    cluster_memory_mb=1024,
                    spark_version="test",
                    json_response={},
                )
            ]
        ),
        sql_warehouses=DatabricksSqlWarehouses(sql_warehouses=[]),
        notebooks=DatabricksNotebooks(notebooks=[]),
        jobs=DatabricksJobs(jobs=[]),
        catalogs=DatabricksCatalogs(catalogs=[]),
        external_locations=DatabricksExternalLocations(external_locations=[]),
        connections=DatabricksConnections(connections=[]),
        secret_scopes=DatabricksSecretScopes(secret_scopes=[]),
        assessment_metadata=DatabricksAssessmentMetadata(
            mode="full", timestamp="2026-01-01T00:00:00"
        ),
        pipelines=DatabricksPipelines(pipelines=[]),
        repos=DatabricksRepos(repos=[]),
        experiments=DatabricksExperiments(experiments=[]),
        serving_endpoints=DatabricksServingEndpoints(serving_endpoints=[]),
        alerts=DatabricksAlerts(alerts=[]),
        genie_spaces=DatabricksGenieSpaces(genie_spaces=[]),
        cluster_policies=DatabricksClusterPolicies(cluster_policies=[]),
        instance_pools=DatabricksInstancePools(instance_pools=[]),
    )


@dataclass
class FakeSynapseAssessment:
    status: dict
    workspace_info: dict
    sql_pools: dict
    spark_pools: dict
    pipelines: dict
    dataflows: dict
    notebooks: dict
    spark_job_definitions: dict
    sql_scripts: dict
    integration_runtimes: dict
    linked_services: dict
    datasets: dict
    managed_private_endpoints: dict
    libraries: dict
    spark_configurations: dict
    assessment_metadata: dict

    def get_summary(self):
        return {"counts": {}}


def make_synapse_assessment():
    database_name = "d" * 45
    schema_name = "s" * 45
    table_name = "t" * 90
    schema = {
        "name": schema_name,
        "tables": {"tables": [{"name": table_name, "columns": []}]},
        "views": {"views": []},
    }
    return FakeSynapseAssessment(
        status={},
        workspace_info={},
        sql_pools={
            "dedicated_pools": [],
            "serverless_pools": [],
            "serverless_pool": {
                "databases": {
                    "databases": [
                        {"name": database_name, "schemas": {"schemas": [schema]}}
                    ]
                }
            },
        },
        spark_pools={"spark_pools": []},
        pipelines={"pipelines": []},
        dataflows={"dataflows": []},
        notebooks={"notebooks": []},
        spark_job_definitions={"spark_job_definitions": []},
        sql_scripts={"sql_scripts": []},
        integration_runtimes={"integration_runtimes": []},
        linked_services={"linked_services": []},
        datasets={"datasets": []},
        managed_private_endpoints={"managed_private_endpoints": []},
        libraries={"libraries": []},
        spark_configurations={"spark_configurations": []},
        assessment_metadata={},
    )


def test_json_export_executes_only_after_plan_and_preserves_layout(tmp_path):
    exporter = JSONExporter()
    windows = os.name == "nt"
    adapter = OutputPathAdapter(str(tmp_path), is_windows=windows)

    result = exporter.export(
        make_databricks_assessment("short-cluster"),
        str(tmp_path),
        "workspace",
        output_adapter=adapter,
        legacy_path_limit=windows,
    )

    cluster_file = (
        tmp_path / "workspace" / "resources" / "clusters" / "cluster_short-cluster.json"
    )
    assert (tmp_path / "workspace" / "summary.json").is_file()
    assert (
        json.loads(cluster_file.read_text(encoding="utf-8"))["cluster_data"][
            "cluster_name"
        ]
        == "short-cluster"
    )
    assert str(cluster_file) in result["files_created"]


@pytest.mark.skipif(
    os.name != "nt", reason="Uses Windows legacy path-length validation"
)
def test_databricks_partial_preflight_preserves_existing_output(tmp_path):
    workspace = tmp_path / "workspace"
    clusters = workspace / "resources" / "clusters"
    clusters.mkdir(parents=True)
    summary = workspace / "summary.json"
    summary_bytes = b'{"counts":{"clusters":9}}\n'
    old_cluster = clusters / "old.json"
    old_cluster_bytes = b'{"old":true}\n'
    summary.write_bytes(summary_bytes)
    old_cluster.write_bytes(old_cluster_bytes)
    exporter = JSONExporter()

    with pytest.raises(ExportPathLengthError) as exc:
        exporter.export(
            make_databricks_assessment("cluster-" + ("x" * 180)),
            str(tmp_path),
            "workspace",
            resources=["clusters"],
            output_adapter=OutputPathAdapter(str(tmp_path), is_windows=True),
            legacy_path_limit=True,
        )

    assert exc.value.error_code == "WINDOWS_PATH_LIMIT_EXCEEDED"
    assert summary.read_bytes() == summary_bytes
    assert old_cluster.read_bytes() == old_cluster_bytes


@pytest.mark.skipif(
    os.name != "nt", reason="Uses Windows legacy path-length validation"
)
def test_synapse_complete_plan_rejects_long_final_table_before_any_write(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "fabric_assessment_tool.services.structured_export_service.SynapseAssessment",
        FakeSynapseAssessment,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    summary = workspace / "summary.json"
    previous = b'{"old":"workspace output"}\n'
    summary.write_bytes(previous)

    with pytest.raises(ExportPathLengthError) as exc:
        JSONExporter().export(
            make_synapse_assessment(),
            str(tmp_path),
            "workspace",
            output_adapter=OutputPathAdapter(str(tmp_path), is_windows=True),
            legacy_path_limit=True,
        )

    assert exc.value.error_code == "WINDOWS_PATH_LIMIT_EXCEEDED"
    assert "t" * 90 in str(exc.value)
    assert summary.read_bytes() == previous
    assert not (workspace / "workspace.json").exists()
