from app.db.models import Asset, Project
from app.db.session import SessionLocal
from app.services.product_rules import confirmed_generation_product_assets


def test_group_confirmation_includes_every_analyzed_product_view() -> None:
    with SessionLocal() as session:
        project = Project(name="three views", mode="replace_product")
        session.add(project)
        session.flush()
        for index, confirmed in enumerate((False, False, True)):
            session.add(Asset(
                project_id=project.id,
                kind="product_reference_image",
                original_path=f"view-{index}.png",
                profile_text=f"view {index}",
                profile_json=f'{{"summary_confirmed": {str(confirmed).lower()}}}',
                analysis_status="succeeded",
            ))
        session.commit()

        assert len(confirmed_generation_product_assets(session, project)) == 3


def test_malformed_legacy_metadata_does_not_break_group_selection() -> None:
    with SessionLocal() as session:
        project = Project(name="legacy views", mode="replace_product")
        session.add(project)
        session.flush()
        session.add_all([
            Asset(project_id=project.id, kind="product_reference_image", original_path="old.png", profile_text="old", profile_json="invalid", analysis_status="succeeded"),
            Asset(project_id=project.id, kind="product_reference_image", original_path="confirmed.png", profile_text="confirmed", profile_json='{"summary_confirmed": true}', analysis_status="succeeded"),
        ])
        session.commit()

        assert len(confirmed_generation_product_assets(session, project)) == 2
