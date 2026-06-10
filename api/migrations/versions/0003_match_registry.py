"""match registry: products, competitor_products, product_links, golden_links

Revision ID: 0003_match_registry
Revises: 0002_match_feedback
"""
from alembic import op

revision = "0003_match_registry"
down_revision = "0002_match_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
          id           bigserial PRIMARY KEY,
          sku          text,
          url          text NOT NULL UNIQUE,
          name         text NOT NULL,
          description  text NOT NULL DEFAULT '',
          packaging    text NOT NULL DEFAULT '',
          brand        text,
          price        numeric,
          mrp          numeric,
          pack_size    integer NOT NULL DEFAULT 1,
          variants     jsonb,
          attrs        jsonb,
          embedding    vector(384),
          scraped_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS competitor_products (
          id           bigserial PRIMARY KEY,
          source       text NOT NULL,
          url          text NOT NULL UNIQUE,
          name         text NOT NULL,
          description  text NOT NULL DEFAULT '',
          packaging    text NOT NULL DEFAULT '',
          price        numeric,
          mrp          numeric,
          in_stock     boolean,
          pack_size    integer NOT NULL DEFAULT 1,
          variants     jsonb,
          attrs        jsonb,
          scraped_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_links (
          id              bigserial PRIMARY KEY,
          product_id      bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
          source          text NOT NULL,
          competitor_url  text NOT NULL,
          verdict         text NOT NULL,   -- confirmed | possible | variant | rejected
          confidence      double precision NOT NULL DEFAULT 0,
          matched_by      text NOT NULL,   -- rules | llm | human
          reason          text,
          llm_response    jsonb,
          status          text NOT NULL DEFAULT 'active',  -- active | human_verified | killed
          created_at      timestamptz NOT NULL DEFAULT now(),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_id, source, competitor_url)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS product_links_lookup_idx "
        "ON product_links (product_id, source, status)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS golden_links (
          id              bigserial PRIMARY KEY,
          dk_name         text NOT NULL,
          source          text NOT NULL,
          competitor_url  text,            -- NULL when label = 'no_match'
          label           text NOT NULL,   -- correct | no_match
          created_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (dk_name, source)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS golden_links")
    op.execute("DROP TABLE IF EXISTS product_links")
    op.execute("DROP TABLE IF EXISTS competitor_products")
    op.execute("DROP TABLE IF EXISTS products")
