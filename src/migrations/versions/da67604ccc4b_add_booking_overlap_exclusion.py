"""add booking overlap exclusion

Revision ID: da67604ccc4b
Revises: a7069b1709d8
Create Date: 2025-12-12 16:57:48.037851

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da67604ccc4b'
down_revision: Union[str, Sequence[str], None] = 'a7069b1709d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")

    # 1) колонка под диапазон
    op.execute("""
    ALTER TABLE bookings
    ADD COLUMN time_range tstzrange;
    """)

    # 2) триггер-функция, которая заполняет time_range
    op.execute("""
    CREATE OR REPLACE FUNCTION bookings_set_time_range()
    RETURNS trigger AS $$
    BEGIN
      NEW.time_range :=
        tstzrange(
          NEW.start_at,
          NEW.start_at + (NEW.duration_min * INTERVAL '1 minute'),
          '[)'
        );
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # 3) триггер на insert/update
    op.execute("""
    CREATE TRIGGER trg_bookings_set_time_range
    BEFORE INSERT OR UPDATE OF start_at, duration_min
    ON bookings
    FOR EACH ROW
    EXECUTE FUNCTION bookings_set_time_range();
    """)

    # 4) заполнить для существующих строк
    op.execute("""
    UPDATE bookings
    SET time_range = tstzrange(
      start_at,
      start_at + (duration_min * INTERVAL '1 minute'),
      '[)'
    );
    """)

    # 5) exclusion constraint без функций в индексе (только колонки)
    op.execute("""
    ALTER TABLE bookings
    ADD CONSTRAINT bookings_no_overlap_active
    EXCLUDE USING gist (
      master_id WITH =,
      time_range WITH &&
    )
    WHERE (status IN ('PENDING', 'CONFIRMED'));
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_no_overlap_active;")
    op.execute("DROP TRIGGER IF EXISTS trg_bookings_set_time_range ON bookings;")
    op.execute("DROP FUNCTION IF EXISTS bookings_set_time_range;")
    op.execute("ALTER TABLE bookings DROP COLUMN IF EXISTS time_range;")
