"""seed map_region rows

Revision ID: 41ed038c7bfd
Revises: 98327e175b52
Create Date: 2026-07-05 09:10:34.751688

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41ed038c7bfd'
down_revision: Union[str, Sequence[str], None] = '98327e175b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (label, xmin, ymin, xmax, ymax) — ported verbatim from the ST_MakeEnvelope INSERTs
# that used to live in config/database/001_create_dbs.sql.
REGIONS = [
    ("NZ_Aus", 63.131759, -57.173648, 190.337125, 0.239941),
    ("NZ", 153.019076, -48.473543, 188.534969, -31.786772),
    ("Suez_Canal", 27.665706, 21.859824, 40.572526, 33.179878),
    ("English_Channel", -13.134662, 48.654641, 9.564140, 59.612725),
    ("Singapore", 93.568655, -7.149559, 118.816790, 10.193142),
    ("Strait_of_Hormuz", 46.049941, 19.082662, 65.784619, 30.797730),
    ("Saudi_Arabia", 18.167952, 2.294974, 71.636505, 34.836029),
    ("Mediterranean", -24.658106, 19.590094, 40.609030, 47.955593),
    ("Panama_Canal", -115.741454, -8.257072, -56.267345, 30.935109),
    ("Ukraine", 17.955828, 38.687680, 47.497370, 52.332015),
    ("Europe", -29.270429, 29.877027, 48.612485, 61.223772),
    ("France", -7.346384, 42.490591, 10.854976, 51.487329),
]


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    stmt = sa.text(
        "INSERT INTO map_region (label, boundary) "
        "VALUES (:label, ST_MakeEnvelope(:xmin, :ymin, :xmax, :ymax, 4326))"
    )
    for label, xmin, ymin, xmax, ymax in REGIONS:
        bind.execute(stmt, {"label": label, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM map_region WHERE label = ANY(:labels)"),
        {"labels": [label for label, *_ in REGIONS]},
    )
