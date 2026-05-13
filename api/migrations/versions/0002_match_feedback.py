"""match feedback table

Revision ID: 0002_match_feedback
Revises: 0001_init_catalog
Create Date: 2026-05-13
"""
from alembic import op

revision = "0002_match_feedback"
down_revision = "0001_init_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS match_feedback (
          id              bigserial PRIMARY KEY,
          search_term     text NOT NULL,
          competitor_id   text NOT NULL,
          matched_name    text NOT NULL,
          matched_url     text,
          matched_price   numeric,
          dk_price        numeric,
          score           double precision NOT NULL,
          cosine          double precision,
          verdict         text NOT NULL,
          reasons         text,
          was_correct     boolean NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS match_feedback_created_idx "
        "ON match_feedback (created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS match_feedback_search_idx "
        "ON match_feedback (search_term)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS match_feedback")
