import json

from fabric_assessment_tool.services.structured_export_service import JSONExporter


def complexity_database(definition):
    return {
        "name": "warehouse",
        "complexity": {
            "summary": {
                "status": "completed",
                "rubric_version": "1.0",
                "total_objects": 1,
                "scored_objects": 1,
                "unavailable_definitions": 0,
                "distribution": {
                    "LOW": 1,
                    "MEDIUM": 0,
                    "HIGH": 0,
                    "VERY_HIGH": 0,
                },
                "by_type": {"VIEW": {"total": 1, "LOW": 1}},
                "objects_needing_review": 0,
                "readiness_percentage": 100.0,
                "readiness_indicator": "READY",
                "elapsed_seconds": 0.1,
                "errors": [],
            },
            "objects": [
                {
                    "database_name": "warehouse",
                    "schema_name": "sales",
                    "object_name": "monthly/revenue",
                    "object_type": "VIEW",
                    "definition_status": "available",
                    "definition_length": 24,
                    "definition_hash": "hash",
                    "definition": definition,
                    "line_count": 1,
                    "score": 0,
                    "complexity_level": "LOW",
                    "matched_rules": [],
                    "escalation_reasons": [],
                    "created_at": None,
                    "modified_at": None,
                }
            ],
        },
    }


def test_exports_summary_and_safe_object_path(tmp_path):
    exporter = JSONExporter()
    files_created = []

    exporter._export_sql_complexity(complexity_database(None), tmp_path, files_created)

    summary_path = tmp_path / "complexity" / "summary.json"
    object_path = (
        tmp_path / "complexity" / "objects" / "views" / "sales.monthlyrevenue.json"
    )
    assert summary_path.exists()
    assert object_path.exists()
    assert len(files_created) == 2


def test_redacted_export_contains_no_definition_text(tmp_path):
    exporter = JSONExporter()

    exporter._export_sql_complexity(complexity_database(None), tmp_path, [])

    exported_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "complexity").rglob("*.json")
    )
    assert "CREATE VIEW" not in exported_text
    object_data = json.loads(
        next((tmp_path / "complexity" / "objects").rglob("*.json")).read_text()
    )
    assert object_data["data"]["definition"] is None


def test_unredacted_export_preserves_definition(tmp_path):
    exporter = JSONExporter()
    definition = "CREATE VIEW sales.v AS SELECT 1;"

    exporter._export_sql_complexity(complexity_database(definition), tmp_path, [])

    object_data = json.loads(
        next((tmp_path / "complexity" / "objects").rglob("*.json")).read_text()
    )
    assert object_data["data"]["definition"] == definition
