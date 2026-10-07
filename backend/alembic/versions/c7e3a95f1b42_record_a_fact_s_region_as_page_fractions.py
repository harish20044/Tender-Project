"""record a fact's region as page fractions

``extracted_facts.bbox`` holds the rectangle in PDF points, which is what the
parser reports. A viewer needs the same rectangle as a fraction of the page,
because it knows how large it has drawn the page and never how large the page
is in points. Deriving one from the other needs the page's size, which lives
with the chunk rather than the fact, so it is stored.

Nullable. Facts extracted before this keep their page and their point
rectangle; re-running extraction fills it.

Revision ID: c7e3a95f1b42
Revises: b4f21c9d7e30
Create Date: 2026-10-08 00:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7e3a95f1b42"
down_revision: str | None = "b4f21c9d7e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extracted_facts",
        sa.Column("bbox_relative", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extracted_facts", "bbox_relative")
