import json

from fabric_assessment_tool.services.structured_export_service import JSONExporter


def test_exports_definitions_by_type_with_safe_names(tmp_path):
    exporter = JSONExporter()
    files_created = []
    database = {
        "definition_summary": {
            "extraction_status": "completed",
            "total_objects": 3,
        },
        "definitions": {
            "definitions": [
                {
                    "name": "load/orders",
                    "schema": "etl:data",
                    "object_type": "stored_procedure",
                    "definition": "CREATE PROCEDURE",
                },
                {
                    "name": "calculate",
                    "schema": "dbo",
                    "object_type": "function",
                    "definition": None,
                    "definition_hash": "abc",
                },
                {
                    "name": "orders",
                    "schema": "reporting",
                    "object_type": "view",
                    "definition": None,
                },
            ]
        },
    }

    exporter._export_synapse_definitions(database, tmp_path, files_created)

    assert (tmp_path / "definitions" / "summary.json").exists()
    assert (
        tmp_path / "definitions" / "stored_procedures" / "etldata.loadorders.json"
    ).exists()
    assert (tmp_path / "definitions" / "functions" / "dbo.calculate.json").exists()
    assert (tmp_path / "definitions" / "views" / "reporting.orders.json").exists()
    assert len(files_created) == 4


def test_redacted_export_does_not_add_definition_text(tmp_path):
    exporter = JSONExporter()
    files_created = []
    database = {
        "definition_summary": {
            "extraction_status": "completed",
            "total_objects": 1,
        },
        "definitions": {
            "definitions": [
                {
                    "name": "secret",
                    "schema": "dbo",
                    "object_type": "stored_procedure",
                    "definition": None,
                    "definition_hash": "digest",
                    "json_response": {"definition_length": 100},
                }
            ]
        },
    }

    exporter._export_synapse_definitions(database, tmp_path, files_created)

    exported = json.loads(
        (tmp_path / "definitions" / "stored_procedures" / "dbo.secret.json").read_text()
    )
    assert exported["data"]["definition"] is None
    assert exported["data"]["definition_hash"] == "digest"
    assert "CREATE" not in json.dumps(exported)


def test_does_not_export_definitions_when_not_requested(tmp_path):
    exporter = JSONExporter()
    files_created = []

    exporter._export_synapse_definitions(
        {
            "definition_summary": {"extraction_status": "not_requested"},
            "definitions": {"definitions": []},
        },
        tmp_path,
        files_created,
    )

    assert files_created == []
    assert not (tmp_path / "definitions").exists()
