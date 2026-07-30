import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from fabric_assessment_tool.assessment.synapse import SqlCodeObjectDefinition
from fabric_assessment_tool.clients.synapse_client import SynapseClient

workspace_name = "lakelense"


def test_get_workspace_info_success():

    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    assert workspace_info is not None


def test_get_notebooks_success():

    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    notebooks = cc._get_notebooks(workspace_name)

    assert notebooks is not None


def test_get_sql_pools_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    sql_pools = cc._get_sql_pools(workspace_name)

    assert sql_pools is not None


def test_get_spark_pools_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    spark_pools = cc._get_spark_pools(workspace_name)

    assert spark_pools is not None


def test_get_pipelines_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    pipelines = cc._get_pipelines(workspace_name)

    assert pipelines is not None


def test_get_serverless_databases_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    databases = cc._get_serverless_databases(workspace_name)

    assert databases is not None


def test_get_serverless_schemas_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    databases = cc._get_serverless_databases(workspace_name)

    schemas = cc._get_serverless_database_schemas(
        workspace_name, databases.databases[-1].name
    )

    assert schemas is not None


def test_get_serverless_database_tables_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    databases = cc._get_serverless_databases(workspace_name)

    tables = cc._get_serverless_database_tables(
        workspace_name, databases.databases[-1].name
    )

    assert tables is not None


def test_get_serverless_database_views_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    databases = cc._get_serverless_databases(workspace_name)

    views = cc._get_serverless_database_views(
        workspace_name, databases.databases[-1].name
    )

    assert views is not None


def test_get_dedicated_schemas_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    sql_pools = cc._get_sql_pools(workspace_name)

    schemas = cc._get_dedicated_schemas(
        workspace_name, sql_pools.dedicated_pools[0].name
    )

    assert schemas is not None


def test_get_dedicated_schema_tables_success():
    cc = SynapseClient()

    workspace_info = cc._get_workspace_info(workspace_name)

    cc._get_synapse_clients(workspace_info.endpoints)

    sql_pools = cc._get_sql_pools(workspace_name)

    schemas = cc._get_dedicated_schemas(
        workspace_name, sql_pools.dedicated_pools[0].name
    )

    tables = cc._get_dedicated_schema_tables(
        workspace_name, sql_pools.dedicated_pools[0].name, schemas.schemas[0].name
    )

    assert tables is not None


def test_dev_endpoint_permission_handling():
    """Test that 403 errors on dev endpoints are handled correctly"""
    import unittest.mock as mock
    from fabric_assessment_tool.errors.api import FATError

    cc = SynapseClient()

    # Mock workspace info to avoid real API calls
    mock_workspace = mock.MagicMock()
    mock_workspace.endpoints = {"dev": "test.dev.azuresynapse.net"}

    with mock.patch.object(cc, "_get_workspace_info", return_value=mock_workspace):
        with mock.patch.object(cc, "_get_synapse_clients"):
            # Create a mock dev client that throws 403
            mock_dev_client = mock.MagicMock()
            mock_dev_client.do_request.side_effect = FATError("Forbidden", "Forbidden")
            cc.synapse_clients = {"dev": mock_dev_client}

            # Test that permission issues are tracked
            pipelines = cc._get_pipelines("test_workspace")

            # Verify that permission issues were detected
            assert cc.dev_endpoint_permission_issues == True

            # Verify that empty result is returned instead of error
            assert len(pipelines.pipelines) == 0


def create_unit_client(**kwargs):
    token_provider = MagicMock()
    token_provider.get_subscription_id.return_value = "subscription"
    return SynapseClient(token_provider=token_provider, **kwargs)


def make_sql_pools():
    dedicated_database = SimpleNamespace(name="dedicated_db", complexity=None)
    serverless_database = SimpleNamespace(name="serverless_db", complexity=None)
    return SimpleNamespace(
        dedicated_pools=[SimpleNamespace(database=dedicated_database)],
        serverless_pool=SimpleNamespace(
            databases=SimpleNamespace(databases=[serverless_database])
        ),
    )


def test_complexity_without_credentials_is_explicitly_unavailable():
    client = create_unit_client(sql_complexity=True)
    sql_pools = make_sql_pools()

    client._collect_sql_complexity(
        workspace_name="workspace",
        sql_pools=sql_pools,
        sql_admin_login=None,
        sql_admin_password=None,
    )

    for database in [
        sql_pools.dedicated_pools[0].database,
        sql_pools.serverless_pool.databases.databases[0],
    ]:
        assert database.complexity.summary.status == "unavailable"
        assert database.complexity.summary.errors == [
            "SQL credentials were not provided"
        ]
    assert client.sql_complexity_issues == ["SQL credentials were not provided"]


def test_complexity_collects_dedicated_and_serverless_with_schema_filter():
    client = create_unit_client(
        sql_complexity=True,
        sql_auth_mode="entra-default",
        sql_complexity_schemas=["Sales"],
    )
    sql_pools = make_sql_pools()
    odbc_client = MagicMock()
    odbc_client.__enter__.return_value = odbc_client
    odbc_client.__exit__.return_value = False
    odbc_client.get_sql_code_objects.return_value = [
        SqlCodeObjectDefinition(
            database_name="db",
            schema_name="Sales",
            object_name="view_one",
            object_type="VIEW",
            definition="CREATE VIEW Sales.view_one AS SELECT 1;",
            is_encrypted=False,
            created_at=None,
            modified_at=None,
            json_response={"object_id": 1},
        )
    ]
    client._create_odbc_client = MagicMock(return_value=odbc_client)

    client._collect_sql_complexity(
        workspace_name="workspace",
        sql_pools=sql_pools,
        sql_admin_login=None,
        sql_admin_password=None,
    )

    assert client._create_odbc_client.call_count == 2
    assert odbc_client.get_sql_code_objects.call_args_list[0].args == (["Sales"],)
    assert sql_pools.dedicated_pools[0].database.complexity.summary.scored_objects == 1
    assert (
        sql_pools.serverless_pool.databases.databases[
            0
        ].complexity.summary.scored_objects
        == 1
    )


def test_complexity_query_failure_does_not_raise():
    client = create_unit_client(
        sql_complexity=True,
        sql_auth_mode="entra-default",
    )
    sql_pools = make_sql_pools()
    odbc_client = MagicMock()
    odbc_client.__enter__.return_value = odbc_client
    odbc_client.__exit__.return_value = False
    odbc_client.get_sql_code_objects.side_effect = RuntimeError("permission denied")
    client._create_odbc_client = MagicMock(return_value=odbc_client)

    client._collect_sql_complexity(
        workspace_name="workspace",
        sql_pools=sql_pools,
        sql_admin_login=None,
        sql_admin_password=None,
    )

    assert (
        sql_pools.dedicated_pools[0].database.complexity.summary.status == "unavailable"
    )
    assert "definition query failed" in client.sql_complexity_issues[0]


def test_complexity_does_not_require_table_statistics_dmv():
    client = create_unit_client(
        sql_complexity=True,
        sql_auth_mode="entra-default",
    )
    odbc_client = MagicMock()
    odbc_client.check_table_statistics_dmv_exists.return_value = False
    client._create_odbc_client = MagicMock(return_value=odbc_client)

    result = client._get_dedicated_database_statistics(
        workspace_name="workspace",
        database_name="warehouse",
        sql_user=None,
        sql_password=None,
    )

    assert result == ([], [], [])
    odbc_client.create_table_statistics_dmv.assert_not_called()
