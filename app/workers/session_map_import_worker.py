import argparse
from uuid import UUID

from app.db.database import SessionLocal
from app.services.session_map_import_service import (
    SessionMapImportService,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one RacePulse session map import job."
    )

    parser.add_argument(
        "--job-id",
        required=True,
        type=UUID,
    )

    args = parser.parse_args()

    db = SessionLocal()

    try:
        job = SessionMapImportService(db).run(args.job_id)

        print(
            f"Map import {job.id} finished with status "
            f"{job.status.value}. "
            f"Samples written: {job.samples_written}"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()