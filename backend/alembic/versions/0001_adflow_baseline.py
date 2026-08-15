"""AdFlow production schema baseline.

This baseline intentionally stamps already-created local installations; new
deployments receive constraints directly from the SQLAlchemy schema.
"""
from alembic import op

revision = "0001_adflow_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE shots ADD CONSTRAINT ck_shots_non_negative_start CHECK (start_sec >= 0) NOT VALID")
    op.execute("ALTER TABLE shots ADD CONSTRAINT ck_shots_positive_range CHECK (end_sec > start_sec) NOT VALID")
    op.execute("ALTER TABLE generations ADD CONSTRAINT ck_generations_attempts_non_negative CHECK (attempts >= 0) NOT VALID")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT ck_jobs_attempts_non_negative CHECK (attempts >= 0) NOT VALID")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_attempts_non_negative")
    op.execute("ALTER TABLE generations DROP CONSTRAINT IF EXISTS ck_generations_attempts_non_negative")
    op.execute("ALTER TABLE shots DROP CONSTRAINT IF EXISTS ck_shots_positive_range")
    op.execute("ALTER TABLE shots DROP CONSTRAINT IF EXISTS ck_shots_non_negative_start")
