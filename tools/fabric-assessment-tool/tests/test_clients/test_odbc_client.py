"""Tests for OdbcClient connection string generation and authentication modes."""

from types import SimpleNamespace

import pytest

from fabric_assessment_tool.clients.odbc_client import (
    OdbcClient,
    get_fabric_type_compatibility,
)


class TestOdbcClientConnectionString:
    """Test connection string generation for different auth modes."""

    def test_sql_auth_connection_string(self):
        """Test SQL authentication connection string generation."""
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            username="sqladmin",
            password="secret123",
            auth_mode="sql",
        )

        conn_str = client._connection_string

        # Should include server with full domain
        assert "Server=tcp:myworkspace.sql.azuresynapse.net,1433" in conn_str
        assert "Database=mydb" in conn_str
        assert "Uid=sqladmin" in conn_str
        assert "Pwd=secret123" in conn_str
        assert "Encrypt=yes" in conn_str
        assert "TrustServerCertificate=no" in conn_str
        # Should NOT include Authentication parameter for SQL auth
        assert "Authentication=" not in conn_str

    def test_sql_auth_with_full_domain(self):
        """Test SQL auth when workspace already has full domain."""
        client = OdbcClient(
            workspace_name="myworkspace.sql.azuresynapse.net",
            database="mydb",
            username="sqladmin",
            password="secret123",
            auth_mode="sql",
        )

        conn_str = client._connection_string

        # Should not duplicate the domain
        assert "Server=tcp:myworkspace.sql.azuresynapse.net,1433" in conn_str
        assert "myworkspace.sql.azuresynapse.net.sql.azuresynapse.net" not in conn_str

    def test_entra_interactive_connection_string(self):
        """Test Entra ID interactive authentication connection string."""
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            auth_mode="entra-interactive",
        )

        conn_str = client._connection_string

        assert "Server=tcp:myworkspace.sql.azuresynapse.net,1433" in conn_str
        assert "Database=mydb" in conn_str
        assert "Authentication=ActiveDirectoryInteractive" in conn_str
        assert "Encrypt=yes" in conn_str
        # Should NOT include Uid/Pwd for interactive auth
        assert "Uid=" not in conn_str
        assert "Pwd=" not in conn_str

    def test_entra_spn_connection_string(self):
        """Test Entra ID Service Principal authentication connection string."""
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            auth_mode="entra-spn",
            client_id="my-client-id",
            client_secret="my-client-secret",
            tenant_id="my-tenant-id",
        )

        conn_str = client._connection_string

        assert "Server=tcp:myworkspace.sql.azuresynapse.net,1433" in conn_str
        assert "Database=mydb" in conn_str
        assert "Authentication=ActiveDirectoryServicePrincipal" in conn_str
        # SPN auth uses UID for client_id and PWD for client_secret
        assert "UID=my-client-id" in conn_str
        assert "PWD=my-client-secret" in conn_str
        assert "Encrypt=yes" in conn_str

    def test_entra_spn_default_tenant(self):
        """Test Entra ID SPN auth defaults to 'common' tenant."""
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            auth_mode="entra-spn",
            client_id="my-client-id",
            client_secret="my-client-secret",
            # tenant_id not provided
        )

        # tenant_id should default to "common"
        assert client.tenant_id == "common"

    def test_entra_default_connection_string(self):
        """Test Entra ID default authentication connection string."""
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            auth_mode="entra-default",
        )

        conn_str = client._connection_string

        assert "Server=tcp:myworkspace.sql.azuresynapse.net,1433" in conn_str
        assert "Database=mydb" in conn_str
        assert "Authentication=ActiveDirectoryDefault" in conn_str
        assert "Encrypt=yes" in conn_str
        # Should NOT include Uid/Pwd for default auth
        assert "Uid=" not in conn_str
        assert "Pwd=" not in conn_str


class TestOdbcClientValidation:
    """Test parameter validation for different auth modes."""

    def test_sql_auth_requires_username(self):
        """Test that SQL auth mode requires username."""
        with pytest.raises(ValueError, match="SQL authentication requires"):
            OdbcClient(
                workspace_name="myworkspace",
                database="mydb",
                password="secret123",
                auth_mode="sql",
            )

    def test_sql_auth_requires_password(self):
        """Test that SQL auth mode requires password."""
        with pytest.raises(ValueError, match="SQL authentication requires"):
            OdbcClient(
                workspace_name="myworkspace",
                database="mydb",
                username="sqladmin",
                auth_mode="sql",
            )

    def test_entra_spn_requires_client_id(self):
        """Test that SPN auth mode requires client_id."""
        with pytest.raises(
            ValueError, match="Service Principal authentication requires"
        ):
            OdbcClient(
                workspace_name="myworkspace",
                database="mydb",
                auth_mode="entra-spn",
                client_secret="my-secret",
            )

    def test_entra_spn_requires_client_secret(self):
        """Test that SPN auth mode requires client_secret."""
        with pytest.raises(
            ValueError, match="Service Principal authentication requires"
        ):
            OdbcClient(
                workspace_name="myworkspace",
                database="mydb",
                auth_mode="entra-spn",
                client_id="my-client-id",
            )

    def test_entra_interactive_no_credentials_required(self):
        """Test that interactive mode doesn't require credentials."""
        # Should not raise
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            auth_mode="entra-interactive",
        )
        assert client.auth_mode == "entra-interactive"

    def test_entra_default_no_credentials_required(self):
        """Test that default mode doesn't require credentials."""
        # Should not raise
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            auth_mode="entra-default",
        )
        assert client.auth_mode == "entra-default"

    def test_unsupported_auth_mode(self):
        """Test that unsupported auth mode raises error."""
        with pytest.raises(ValueError, match="Unsupported authentication mode"):
            OdbcClient(
                workspace_name="myworkspace",
                database="mydb",
                auth_mode="invalid-mode",
            )


class TestOdbcClientDefaults:
    """Test default values and backward compatibility."""

    def test_default_auth_mode_is_sql(self):
        """Test that default auth mode is 'sql' for backward compatibility."""
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            username="sqladmin",
            password="secret123",
        )
        assert client.auth_mode == "sql"

    def test_legacy_constructor_still_works(self):
        """Test that old-style constructor still works (backward compatibility)."""
        # This mimics the old constructor signature
        client = OdbcClient(
            workspace_name="myworkspace",
            database="mydb",
            username="sqladmin",
            password="secret123",
        )
        assert client.workspace_name == "myworkspace"
        assert client.database == "mydb"
        assert client.username == "sqladmin"
        assert client.password == "secret123"
        assert "Uid=sqladmin" in client._connection_string


def _column_row(
    schema,
    table_type,
    table_name,
    column_name,
    ordinal,
    data_type="int",
    nullable="NO",
    **overrides,
):
    values = {
        "TABLE_SCHEMA": schema,
        "TABLE_TYPE": table_type,
        "TABLE_NAME": table_name,
        "COLUMN_NAME": column_name,
        "ORDINAL_POSITION": ordinal,
        "COLUMN_DEFAULT": None,
        "IS_NULLABLE": nullable,
        "DATA_TYPE": data_type,
        "CHARACTER_MAXIMUM_LENGTH": None,
        "NUMERIC_PRECISION": None,
        "NUMERIC_SCALE": None,
        "DATETIME_PRECISION": None,
        "CHARACTER_SET_NAME": None,
        "COLLATION_NAME": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestColumnMetadata:
    def _client(self):
        return OdbcClient(
            workspace_name="workspace",
            database="database",
            username="user",
            password="password",
        )

    @pytest.mark.parametrize(
        ("data_type", "classification"),
        [
            ("int", "compatible"),
            ("varchar", "review"),
            ("xml", "unsupported"),
            ("future_type", "review"),
        ],
    )
    def test_fabric_compatibility_mapping(self, data_type, classification):
        assert get_fabric_type_compatibility(data_type).classification == classification

    def test_query_and_row_mapping_include_tables_views_and_optional_fields(
        self, monkeypatch
    ):
        client = self._client()
        queries = []
        rows = [
            _column_row(
                "dbo",
                "BASE TABLE",
                "orders",
                "description",
                1,
                data_type="nvarchar",
                nullable="YES",
                COLUMN_DEFAULT="('unknown')",
                CHARACTER_MAXIMUM_LENGTH=200,
                CHARACTER_SET_NAME="UNICODE",
                COLLATION_NAME="Latin1_General_100_CI_AS_SC_UTF8",
            ),
            _column_row(
                "reporting",
                "VIEW",
                "orders_view",
                "amount",
                1,
                data_type="decimal",
                NUMERIC_PRECISION=18,
                NUMERIC_SCALE=2,
                DATETIME_PRECISION=None,
            ),
        ]

        def execute(query):
            queries.append(query)
            return iter(rows)

        monkeypatch.setattr(client, "execute_query", execute)

        result = client.get_column_metadata()

        assert len(queries) == 1
        assert "INFORMATION_SCHEMA.COLUMNS" in queries[0]
        assert "INFORMATION_SCHEMA.TABLES" in queries[0]
        assert "'BASE TABLE', 'VIEW'" in queries[0]
        assert result.total_objects == 2
        assert [obj.object_type for obj in result.objects] == ["table", "view"]
        column = result.objects[0].columns[0]
        assert column.is_nullable is True
        assert column.character_maximum_length == 200
        assert column.column_default == "('unknown')"
        assert column.character_set_name == "UNICODE"
        assert column.collation_name == "Latin1_General_100_CI_AS_SC_UTF8"
        amount = result.objects[1].columns[0]
        assert amount.numeric_precision == 18
        assert amount.numeric_scale == 2

    def test_combined_cap_is_deterministic_and_never_partial(self, monkeypatch):
        client = self._client()
        rows = [
            _column_row("a", "BASE TABLE", "first", "c1", 1),
            _column_row("a", "BASE TABLE", "first", "c2", 2),
            _column_row("a", "VIEW", "second", "c1", 1),
            _column_row("b", "BASE TABLE", "third", "c1", 1),
        ]
        monkeypatch.setattr(client, "execute_query", lambda query: iter(rows))

        result = client.get_column_metadata(max_objects=2)

        assert result.total_objects == 3
        assert result.selected_objects == 2
        assert result.capped is True
        assert [obj.name for obj in result.objects if obj.columns_collected] == [
            "first",
            "second",
        ]
        assert [column.name for column in result.objects[0].columns] == [
            "c1",
            "c2",
        ]
        assert result.objects[2].columns == []

    def test_1000_objects_still_execute_one_metadata_query(self, monkeypatch):
        client = self._client()
        rows = [
            _column_row("dbo", "BASE TABLE", f"table_{index:04d}", "id", 1)
            for index in range(1001)
        ]
        query_count = 0

        def execute(query):
            nonlocal query_count
            query_count += 1
            return iter(rows)

        monkeypatch.setattr(client, "execute_query", execute)

        result = client.get_column_metadata(max_objects=25)

        assert query_count == 1
        assert result.total_objects == 1001
        assert result.selected_objects == 25
        assert [obj.name for obj in result.objects if obj.columns_collected] == [
            f"table_{index:04d}" for index in range(25)
        ]

    def test_inventory_query_does_not_read_columns(self, monkeypatch):
        client = self._client()
        queries = []

        def execute(query):
            queries.append(query)
            return iter(
                [
                    SimpleNamespace(
                        TABLE_SCHEMA="dbo",
                        TABLE_TYPE="VIEW",
                        TABLE_NAME="inventory_view",
                    )
                ]
            )

        monkeypatch.setattr(client, "execute_query", execute)

        objects = client.get_table_view_objects()

        assert len(queries) == 1
        assert "INFORMATION_SCHEMA.TABLES" in queries[0]
        assert "INFORMATION_SCHEMA.COLUMNS" not in queries[0]
        assert objects[0].object_type == "view"
