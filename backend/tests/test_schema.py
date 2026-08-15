from sqlalchemy import inspect

from app.db.session import create_schema, engine


def test_create_schema_creates_core_tables() -> None:
    create_schema()

    table_names = set(inspect(engine).get_table_names())

    assert {"projects", "assets", "timeline_revisions", "shots", "shot_evidence", "shot_edits", "generations", "jobs", "prompt_revisions", "video_analyses"} <= table_names
