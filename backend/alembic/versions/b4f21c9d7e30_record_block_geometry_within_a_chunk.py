"""record block geometry within a chunk

Narrows a citation from a page to a region. Each entry is
[start, end, page, x0, y0, x1, y1]: where one laid-out block of the source
page landed inside the chunk's text, and the rectangle it was printed in.

Nullable, and chunks written before this stay null — a fact extracted from
one keeps its page citation and simply has no box to highlight. Re-ingesting
a document fills it.

Revision ID: b4f21c9d7e30
Revises: ee8c4a71aaf2
Create Date: 2026-10-07 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4f21c9d7e30"
down_revision: str | None = "ee8c4a71aaf2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column("block_spans", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chunks", "block_spans")
