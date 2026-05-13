"""init catalog and pgvector

Revision ID: 0001_init_catalog
Revises:
Create Date: 2026-05-13
"""
from alembic import op

revision = "0001_init_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dentalkart_catalog (
          id           bigserial PRIMARY KEY,
          sku          text,
          name         text NOT NULL,
          normalized   text NOT NULL,
          brand        text,
          embedding    vector(384) NOT NULL,
          updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dentalkart_catalog_embedding_idx "
        "ON dentalkart_catalog USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dentalkart_catalog_brand_idx "
        "ON dentalkart_catalog (brand)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dentalkart_catalog")
