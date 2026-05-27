#!/usr/bin/env python3
#
# tests/test_models_base.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.exc import StatementError

from app.models.base import Base, UTCDateTime


class _TimestampRecord(Base):
    __tablename__ = "test_timestamp_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    happened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class UTCDateTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[_TimestampRecord.__table__])

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine, tables=[_TimestampRecord.__table__])
        self.engine.dispose()

    def test_sqlite_roundtrip_returns_utc_aware_datetime(self) -> None:
        source = datetime(2026, 5, 27, 12, 34, 56, tzinfo=UTC) + timedelta(hours=2)

        with Session(self.engine) as session:
            session.add(_TimestampRecord(happened_at=source))
            session.commit()
            loaded = session.scalar(select(_TimestampRecord))

        assert loaded is not None
        self.assertEqual(loaded.happened_at.tzinfo, UTC)
        self.assertEqual(loaded.happened_at, source.astimezone(UTC))

    def test_sqlite_roundtrip_rejects_naive_datetime(self) -> None:
        naive = datetime(2026, 5, 27, 12, 34, 56)

        with Session(self.engine) as session:
            session.add(_TimestampRecord(happened_at=naive))
            with self.assertRaisesRegex(StatementError, "timezone-aware"):
                session.commit()
            session.rollback()


if __name__ == "__main__":
    unittest.main()