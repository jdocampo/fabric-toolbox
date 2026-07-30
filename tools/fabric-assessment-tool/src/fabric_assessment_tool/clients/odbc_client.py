import hashlib
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Optional, Sequence

from mssql_python import connect  # type: ignore[import-untyped]

from ..assessment.synapse import (
    CodeObjectCount,
    CodeObjectLines,
    SynapseDefinitionSummary,
    SynapseSqlDefinition,
    SynapseSqlDefinitions,
    TableStatistics,
)

# Supported SQL authentication modes
SqlAuthMode = Literal["sql", "entra-interactive", "entra-spn", "entra-default"]
DefinitionRedactionMode = Literal["none", "full", "partial", "hash"]

PARTIAL_REDACTION_LENGTH = 256
PARTIAL_REDACTION_MARKER = "\n/* ... definition redacted ... */\n"


class OdbcClient:
    """ODBC client for connecting to Azure Synapse Analytics dedicated SQL pools.

    Supports multiple authentication modes:
    - sql: Traditional SQL authentication with username/password
    - entra-interactive: Entra ID interactive authentication (browser popup)
    - entra-spn: Entra ID Service Principal authentication
    - entra-default: Entra ID default authentication (Azure CLI, managed identity, etc.)
    """

    def __init__(
        self,
        workspace_name: str,
        database: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_mode: SqlAuthMode = "sql",
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        """
        Initialize ODBC client with connection parameters.

        Args:
            workspace_name: The Synapse workspace name (e.g., 'myworkspace.sql.azuresynapse.net')
            database: The database name to connect to
            username: SQL authentication username (required for 'sql' mode)
            password: SQL authentication password (required for 'sql' mode)
            auth_mode: Authentication mode - 'sql', 'entra-interactive', 'entra-spn', or 'entra-default'
            client_id: Service principal client ID (required for 'entra-spn' mode)
            client_secret: Service principal client secret (required for 'entra-spn' mode)
            tenant_id: Azure tenant ID (optional for 'entra-spn' mode, defaults to 'common')
        """
        self.workspace_name = workspace_name
        self.database = database
        self.username = username
        self.password = password
        self.auth_mode = auth_mode
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id or "common"
        self._validate_auth_params()
        self._connection_string = self._build_connection_string()
        self._connection: Optional[Any] = None

    def _validate_auth_params(self) -> None:
        """Validate that required parameters are provided for the selected auth mode."""
        if self.auth_mode == "sql":
            if not self.username or not self.password:
                raise ValueError(
                    "SQL authentication requires both 'username' and 'password'"
                )
        elif self.auth_mode == "entra-spn":
            if not self.client_id or not self.client_secret:
                raise ValueError(
                    "Entra ID Service Principal authentication requires "
                    "'client_id' and 'client_secret'"
                )
        # entra-interactive and entra-default don't require additional params

    def _build_connection_string(self) -> str:
        """Build the connection string based on the authentication mode."""
        # Ensure workspace_name has the full domain if not provided
        if not self.workspace_name.endswith(".sql.azuresynapse.net"):
            server = f"{self.workspace_name}.sql.azuresynapse.net"
        else:
            server = self.workspace_name

        # Base connection string components
        base = (
            f"Server=tcp:{server},1433;"
            f"Database={self.database};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )

        if self.auth_mode == "sql":
            return base + f"Uid={self.username};Pwd={self.password};"

        elif self.auth_mode == "entra-interactive":
            # Interactive browser-based authentication with MFA support
            return base + "Authentication=ActiveDirectoryInteractive;"

        elif self.auth_mode == "entra-spn":
            # Service Principal authentication
            # UID = client_id, PWD = client_secret
            return (
                base + f"Authentication=ActiveDirectoryServicePrincipal;"
                f"UID={self.client_id};"
                f"PWD={self.client_secret};"
            )

        elif self.auth_mode == "entra-default":
            # Default authentication - uses Azure CLI, managed identity, etc.
            return base + "Authentication=ActiveDirectoryDefault;"

        else:
            raise ValueError(f"Unsupported authentication mode: {self.auth_mode}")

    def __enter__(self):
        """Enter context manager - open connection."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager - close connection."""
        self.close()
        return False

    def open(self) -> None:
        """Open the database connection."""
        if self._connection is None:
            self._connection = connect(self._connection_string)

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass  # Ignore errors on close
            finally:
                self._connection = None

    def _ensure_connection(self) -> Any:
        """Ensure connection is open and return it."""
        if self._connection is None:
            self.open()
        return self._connection

    def execute_query(
        self, query: str, parameters: Optional[Sequence[Any]] = None
    ) -> Iterator[Any]:
        """
        Execute a SQL query and yield results row by row.

        Args:
            query: SQL query to execute

        Yields:
            Row objects from the query results
        """
        conn = self._ensure_connection()
        with conn.cursor() as cursor:
            if parameters:
                cursor.execute(query, tuple(parameters))
            else:
                cursor.execute(query)
            for row in cursor:
                yield row

    def get_schemas(self) -> list[str]:
        """Get schema names from the dedicated SQL pool.

        Returns:
            List of schema names
        """
        query = """
SELECT SCHEMA_NAME
FROM INFORMATION_SCHEMA.SCHEMATA
ORDER BY SCHEMA_NAME
"""
        return [row.SCHEMA_NAME for row in self.execute_query(query)]

    def get_tables(self, schema_name: str) -> list[str]:
        """Get table names for a given schema in the dedicated SQL pool.

        Args:
            schema_name: The schema to list tables for

        Returns:
            List of table names
        """
        query = f"""
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = '{schema_name}' AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME
"""
        return [row.TABLE_NAME for row in self.execute_query(query)]

    def check_table_statistics_dmv_exists(self) -> bool:
        """
        Check if the vTableSizes view exists in the master database.

        Returns:
            True if the view exists, False otherwise.
        """
        check_query = """
SELECT OBJECT_ID('dbo.vTableSizes', 'V')
        """

        conn = self._ensure_connection()
        with conn.cursor() as cursor:
            cursor.execute(check_query)
            result = cursor.fetchone()
            return result[0] is not None

    def create_table_statistics_dmv(self) -> None:
        """
        Create the vTableSizes view in the master database if it does not exist.

        Raises:
            Exception: If there is an error creating the view.
        """

        create_view_query = """
CREATE VIEW dbo.vTableSizes
AS
WITH base
AS
(
SELECT
 GETDATE()                                                             AS  [execution_time]
, DB_NAME()                                                            AS  [database_name]
, s.name                                                               AS  [schema_name]
, t.name                                                               AS  [table_name]
, QUOTENAME(s.name)+'.'+QUOTENAME(t.name)                              AS  [two_part_name]
, nt.[name]                                                            AS  [node_table_name]
, ROW_NUMBER() OVER(PARTITION BY nt.[name] ORDER BY (SELECT NULL))     AS  [node_table_name_seq]
, tp.[distribution_policy_desc]                                        AS  [distribution_policy_name]
, c.[name]                                                             AS  [distribution_column]
, nt.[distribution_id]                                                 AS  [distribution_id]
, i.[type]                                                             AS  [index_type]
, i.[type_desc]                                                        AS  [index_type_desc]
, nt.[pdw_node_id]                                                     AS  [pdw_node_id]
, pn.[type]                                                            AS  [pdw_node_type]
, pn.[name]                                                            AS  [pdw_node_name]
, di.name                                                              AS  [dist_name]
, di.position                                                          AS  [dist_position]
, nps.[partition_number]                                               AS  [partition_nmbr]
, nps.[reserved_page_count]                                            AS  [reserved_space_page_count]
, nps.[reserved_page_count] - nps.[used_page_count]                    AS  [unused_space_page_count]
, nps.[in_row_data_page_count]
    + nps.[row_overflow_used_page_count]
    + nps.[lob_used_page_count]                                        AS  [data_space_page_count]
, nps.[reserved_page_count]
 - (nps.[reserved_page_count] - nps.[used_page_count])
 - ([in_row_data_page_count]
         + [row_overflow_used_page_count]+[lob_used_page_count])       AS  [index_space_page_count]
, nps.[row_count]                                                      AS  [row_count]
from
    sys.schemas s
INNER JOIN sys.tables t
    ON s.[schema_id] = t.[schema_id]
INNER JOIN sys.indexes i
    ON  t.[object_id] = i.[object_id]
    AND i.[index_id] <= 1
INNER JOIN sys.pdw_table_distribution_properties tp
    ON t.[object_id] = tp.[object_id]
INNER JOIN sys.pdw_table_mappings tm
    ON t.[object_id] = tm.[object_id]
INNER JOIN sys.pdw_nodes_tables nt
    ON tm.[physical_name] = nt.[name]
INNER JOIN sys.dm_pdw_nodes pn
    ON  nt.[pdw_node_id] = pn.[pdw_node_id]
INNER JOIN sys.pdw_distributions di
    ON  nt.[distribution_id] = di.[distribution_id]
INNER JOIN sys.dm_pdw_nodes_db_partition_stats nps
    ON nt.[object_id] = nps.[object_id]
    AND nt.[pdw_node_id] = nps.[pdw_node_id]
    AND nt.[distribution_id] = nps.[distribution_id]
    AND i.[index_id] = nps.[index_id]
LEFT OUTER JOIN (select * from sys.pdw_column_distribution_properties where distribution_ordinal = 1) cdp
    ON t.[object_id] = cdp.[object_id]
LEFT OUTER JOIN sys.columns c
    ON cdp.[object_id] = c.[object_id]
    AND cdp.[column_id] = c.[column_id]
WHERE pn.[type] = 'COMPUTE'
)
, size
AS
(
SELECT
   [execution_time]
,  [database_name]
,  [schema_name]
,  [table_name]
,  [two_part_name]
,  [node_table_name]
,  [node_table_name_seq]
,  [distribution_policy_name]
,  [distribution_column]
,  [distribution_id]
,  [index_type]
,  [index_type_desc]
,  [pdw_node_id]
,  [pdw_node_type]
,  [pdw_node_name]
,  [dist_name]
,  [dist_position]
,  [partition_nmbr]
,  [reserved_space_page_count]
,  [unused_space_page_count]
,  [data_space_page_count]
,  [index_space_page_count]
,  [row_count]
,  ([reserved_space_page_count] * 8.0)                                 AS [reserved_space_KB]
,  ([reserved_space_page_count] * 8.0)/1000                            AS [reserved_space_MB]
,  ([reserved_space_page_count] * 8.0)/1000000                         AS [reserved_space_GB]
,  ([reserved_space_page_count] * 8.0)/1000000000                      AS [reserved_space_TB]
,  ([unused_space_page_count]   * 8.0)                                 AS [unused_space_KB]
,  ([unused_space_page_count]   * 8.0)/1000                            AS [unused_space_MB]
,  ([unused_space_page_count]   * 8.0)/1000000                         AS [unused_space_GB]
,  ([unused_space_page_count]   * 8.0)/1000000000                      AS [unused_space_TB]
,  ([data_space_page_count]     * 8.0)                                 AS [data_space_KB]
,  ([data_space_page_count]     * 8.0)/1000                            AS [data_space_MB]
,  ([data_space_page_count]     * 8.0)/1000000                         AS [data_space_GB]
,  ([data_space_page_count]     * 8.0)/1000000000                      AS [data_space_TB]
,  ([index_space_page_count]  * 8.0)                                   AS [index_space_KB]
,  ([index_space_page_count]  * 8.0)/1000                              AS [index_space_MB]
,  ([index_space_page_count]  * 8.0)/1000000                           AS [index_space_GB]
,  ([index_space_page_count]  * 8.0)/1000000000                        AS [index_space_TB]
FROM base
)
SELECT *
FROM size
        """

        conn = self._ensure_connection()
        # Save current autocommit state and set to True for DDL
        original_autocommit = conn.autocommit
        try:
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute(create_view_query)
        finally:
            # Restore original autocommit state
            conn.autocommit = original_autocommit

    def get_table_statistics(self, database: str) -> Iterator[TableStatistics]:
        """
        Get table statistics from the vTableSizes view.

        Args:
            database: The database name to query

        Yields:
            TableStatistics objects with table size and distribution information
        """
        query = """
SELECT
    database_name
,   schema_name
,   table_name
,   distribution_policy_name
,   distribution_column
,   index_type_desc
,   COUNT(distinct partition_nmbr) as nbr_partitions
,   SUM(row_count)                 as table_row_count
,   SUM(reserved_space_GB)         as table_reserved_space_GB
,   SUM(data_space_GB)             as table_data_space_GB
,   SUM(index_space_GB)            as table_index_space_GB
,   SUM(unused_space_GB)           as table_unused_space_GB
FROM
    dbo.vTableSizes
GROUP BY
    database_name
,   schema_name
,   table_name
,   distribution_policy_name
,   distribution_column
,   index_type_desc
ORDER BY
    table_reserved_space_GB desc
"""

        for row in self.execute_query(query):
            yield TableStatistics(
                database_name=row.database_name,
                schema_name=row.schema_name,
                table_name=row.table_name,
                distribution_policy_name=row.distribution_policy_name,
                distribution_column=row.distribution_column,
                index_type_desc=row.index_type_desc,
                nbr_partitions=row.nbr_partitions,
                table_row_count=row.table_row_count,
                table_reserved_space_gb=row.table_reserved_space_GB,
                table_data_space_gb=row.table_data_space_GB,
                table_index_space_gb=row.table_index_space_GB,
                table_unused_space_gb=row.table_unused_space_GB,
            )

    def get_object_count(self, database: str) -> Iterator[CodeObjectCount]:

        query = """
SELECT 
    type_desc, count(*) AS count_objects
FROM SYS.OBJECTS
WHERE TYPE IN ( 'P','V','TR','FN')
GROUP BY TYPE_desc,type        
"""
        for row in self.execute_query(query):
            yield CodeObjectCount(
                type_description=row.type_desc, count=row.count_objects
            )

    def get_code_lines_statistics(self, database: str) -> Iterator[CodeObjectLines]:

        query = """
declare @lencount nchar(2)
set @lencount = char(0x0d) + char(0x0a);
select [Schema]=schema_name(p.schema_id), [ObjectName]=p.name
, Num_of_LineCode=(len(m.definition) -len(replace(m.definition, @lencount, ''))) /2,'Procedure' as Type
from sys.sql_modules m
inner join sys.procedures p on m.object_id = p.object_id
union all
select [Schema]=schema_name(p.schema_id), [ObjectName]=p.name
, Num_of_LineCode=(len(m.definition) -len(replace(m.definition, @lencount, ''))) /2,'Views' as Type
from sys.sql_modules m
inner join sys.views p on m.object_id = p.object_id
union all
select [Schema]=schema_name(p.schema_id), [ObjectName]=p.name
, Num_of_LineCode=(len(m.definition) -len(replace(m.definition, @lencount, ''))) /2,'Functions' as Type
from sys.sql_modules m inner join
sys.objects AS p   
    ON m.object_id = p.object_id   
    AND type = ('FN'); 
"""
        for row in self.execute_query(query):
            yield CodeObjectLines(
                schema_name=row.Schema,
                object_name=row.ObjectName,
                code_line_number=row.Num_of_LineCode,
                type_description=row.Type,
            )

    def get_sql_definitions(
        self,
        redaction_mode: DefinitionRedactionMode = "partial",
        schema_filter: Optional[Sequence[str]] = None,
        max_definition_size: int = 1_000_000,
    ) -> tuple[SynapseSqlDefinitions, SynapseDefinitionSummary]:
        """Extract and protect SQL module definitions with one set-based query."""

        if redaction_mode not in ("none", "full", "partial", "hash"):
            raise ValueError(f"Unsupported definition redaction mode: {redaction_mode}")
        if max_definition_size < 0:
            raise ValueError("max_definition_size must be non-negative")

        normalized_schemas = [
            schema.strip()
            for schema in (schema_filter or [])
            if schema and schema.strip()
        ]
        schema_clause = ""
        parameters: list[Any] = []
        if normalized_schemas:
            placeholders = ", ".join("?" for _ in normalized_schemas)
            schema_clause = f"AND s.name COLLATE DATABASE_DEFAULT IN ({placeholders})"
            parameters.extend(normalized_schemas)

        query = f"""
WITH definition_objects AS
(
SELECT
    DB_NAME() AS database_name,
    s.name AS schema_name,
    o.name AS object_name,
    o.type AS sql_type,
    o.type_desc AS sql_type_description,
    o.create_date,
    o.modify_date,
    OBJECTPROPERTY(o.object_id, 'IsEncrypted') AS is_encrypted,
    HAS_PERMS_BY_NAME(
        QUOTENAME(s.name) + '.' + QUOTENAME(o.name),
        'OBJECT',
        'VIEW DEFINITION'
    ) AS has_view_definition,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION')
        AS has_database_view_definition,
    DATALENGTH(m.definition) / 2 AS definition_length,
    m.definition
FROM sys.objects AS o
INNER JOIN sys.schemas AS s
    ON o.schema_id = s.schema_id
LEFT JOIN sys.sql_modules AS m
    ON o.object_id = m.object_id
WHERE o.is_ms_shipped = 0
  AND o.type IN ('P', 'PC', 'V', 'FN', 'IF', 'TF', 'FS', 'FT')
  {schema_clause}
)
SELECT *
FROM definition_objects
UNION ALL
SELECT
    DB_NAME(),
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    0,
    NULL,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION'),
    NULL,
    NULL
WHERE NOT EXISTS (SELECT 1 FROM definition_objects)
ORDER BY schema_name, sql_type, object_name
"""

        definitions: list[SynapseSqlDefinition] = []
        database_has_view_definition: Optional[bool] = None
        for row in self.execute_query(query, parameters):
            database_has_view_definition = bool(row.has_database_view_definition)
            if row.object_name is None:
                continue
            definitions.append(
                self._transform_definition_row(
                    row=row,
                    redaction_mode=redaction_mode,
                    max_definition_size=max_definition_size,
                )
            )

        collection = SynapseSqlDefinitions(definitions=definitions)
        summary = self.build_definition_summary(collection)
        if summary.unavailable_objects:
            summary.extraction_status = "partial"
            summary.status_description = (
                f"{summary.unavailable_objects} object definitions were unavailable."
            )
        elif database_has_view_definition is False and not definitions:
            summary.extraction_status = "unavailable"
            summary.status_description = (
                "The identity does not have VIEW DEFINITION permission."
            )
        return collection, summary

    @staticmethod
    def _transform_definition_row(
        row: Any,
        redaction_mode: DefinitionRedactionMode,
        max_definition_size: int,
    ) -> SynapseSqlDefinition:
        """Convert a SQL row without retaining unredacted text in raw metadata."""

        source_definition = row.definition
        is_encrypted = bool(row.is_encrypted)
        is_unavailable = source_definition is None and not is_encrypted
        definition_hash = (
            hashlib.sha256(source_definition.encode("utf-8")).hexdigest()
            if source_definition is not None
            else None
        )
        original_length = (
            int(row.definition_length)
            if row.definition_length is not None
            else len(source_definition or "")
        )

        protected_definition: Optional[str]
        is_truncated = False
        if source_definition is None or redaction_mode in ("full", "hash"):
            protected_definition = None
        elif redaction_mode == "partial":
            protected_definition = OdbcClient._partially_redact(
                source_definition, max_definition_size
            )
            is_truncated = (
                max_definition_size > 0 and original_length > max_definition_size
            )
        else:
            protected_definition = source_definition

        if (
            protected_definition is not None
            and redaction_mode == "none"
            and max_definition_size > 0
            and len(protected_definition) > max_definition_size
        ):
            protected_definition = protected_definition[:max_definition_size]
            is_truncated = True

        sql_type = row.sql_type.strip()
        object_type = OdbcClient._normalize_definition_type(sql_type)
        created_at = OdbcClient._format_datetime(row.create_date)
        modified_at = OdbcClient._format_datetime(row.modify_date)
        stored_length = len(protected_definition or "")

        safe_metadata = {
            "database_name": row.database_name,
            "schema_name": row.schema_name,
            "object_name": row.object_name,
            "sql_type": row.sql_type,
            "sql_type_description": row.sql_type_description,
            "create_date": created_at,
            "modify_date": modified_at,
            "is_encrypted": is_encrypted,
            "has_view_definition": bool(row.has_view_definition),
            "has_database_view_definition": bool(row.has_database_view_definition),
            "definition_length": original_length,
        }

        return SynapseSqlDefinition(
            name=row.object_name,
            database=row.database_name,
            schema=row.schema_name,
            object_type=object_type,
            sql_type=sql_type,
            sql_type_description=row.sql_type_description,
            definition=protected_definition,
            original_length=original_length,
            stored_length=stored_length,
            created_at=created_at,
            modified_at=modified_at,
            is_encrypted=is_encrypted,
            is_unavailable=is_unavailable,
            is_truncated=is_truncated,
            redaction_mode=redaction_mode,
            definition_hash=definition_hash if redaction_mode == "hash" else None,
            json_response=safe_metadata,
        )

    @staticmethod
    def _partially_redact(definition: str, max_definition_size: int = 0) -> str:
        """Keep a bounded prefix and suffix while removing the middle."""

        if len(definition) <= 2:
            return definition
        output_budget = (
            max_definition_size
            if max_definition_size > 0
            else PARTIAL_REDACTION_LENGTH * 2 + len(PARTIAL_REDACTION_MARKER)
        )
        if output_budget <= len(PARTIAL_REDACTION_MARKER):
            return PARTIAL_REDACTION_MARKER[:output_budget]

        edge_budget = (output_budget - len(PARTIAL_REDACTION_MARKER)) // 2
        edge_length = min(
            PARTIAL_REDACTION_LENGTH,
            max(1, len(definition) // 4),
            edge_budget,
        )
        return (
            definition[:edge_length]
            + PARTIAL_REDACTION_MARKER
            + definition[-edge_length:]
        )

    @staticmethod
    def _normalize_definition_type(sql_type: str) -> str:
        sql_type = sql_type.strip()
        if sql_type in ("P", "PC"):
            return "stored_procedure"
        if sql_type == "V":
            return "view"
        return "function"

    @staticmethod
    def _format_datetime(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def build_definition_summary(
        definitions: SynapseSqlDefinitions,
        now: Optional[datetime] = None,
    ) -> SynapseDefinitionSummary:
        """Build deterministic metadata used by JSON summaries and HTML reports."""

        current_time = now or datetime.now(timezone.utc)
        counts_by_type: dict[str, int] = {}
        age_buckets = {
            "less_than_1_year": 0,
            "1_to_3_years": 0,
            "3_to_5_years": 0,
            "more_than_5_years": 0,
            "unknown": 0,
        }

        for definition in definitions.definitions:
            counts_by_type[definition.object_type] = (
                counts_by_type.get(definition.object_type, 0) + 1
            )
            age_buckets[
                OdbcClient._get_age_bucket(definition.modified_at, current_time)
            ] += 1

        largest_objects = [
            {
                "database": definition.database,
                "schema": definition.schema,
                "name": definition.name,
                "object_type": definition.object_type,
                "original_length": definition.original_length,
                "is_encrypted": definition.is_encrypted,
                "is_unavailable": definition.is_unavailable,
                "is_truncated": definition.is_truncated,
                "modified_at": definition.modified_at,
            }
            for definition in sorted(
                definitions.definitions,
                key=lambda item: item.original_length,
                reverse=True,
            )[:10]
        ]

        return SynapseDefinitionSummary(
            extraction_status="completed",
            total_objects=len(definitions.definitions),
            counts_by_type=counts_by_type,
            encrypted_objects=sum(
                definition.is_encrypted for definition in definitions.definitions
            ),
            unavailable_objects=sum(
                definition.is_unavailable for definition in definitions.definitions
            ),
            truncated_objects=sum(
                definition.is_truncated for definition in definitions.definitions
            ),
            total_definition_characters=sum(
                definition.original_length for definition in definitions.definitions
            ),
            age_buckets=age_buckets,
            largest_objects=largest_objects,
        )

    @staticmethod
    def _get_age_bucket(modified_at: Optional[str], now: datetime) -> str:
        if not modified_at:
            return "unknown"
        try:
            modified = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            reference = now
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            age_days = max((reference - modified).days, 0)
        except (TypeError, ValueError):
            return "unknown"

        if age_days < 365:
            return "less_than_1_year"
        if age_days < 365 * 3:
            return "1_to_3_years"
        if age_days < 365 * 5:
            return "3_to_5_years"
        return "more_than_5_years"
