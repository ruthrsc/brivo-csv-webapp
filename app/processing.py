import csv
import logging
import os.path
import time
from datetime import datetime as DateTime

from flask import session

from app.brivo import BrivoError
from app.util import BrivoApiContext, KeysBase, PathNames, is_input_true, random_string, writable_path

log = logging.getLogger(__name__)


class CSVCreateFormat(KeysBase):
    FIRST = "First"
    LAST = "Last"
    MEMBER_ID = "Member ID"
    GROUP = "Group"
    CARD_NUMBER = "Card Number"
    FACILITY_CODE = "Facility Code"


class CSVSuspendFormat(KeysBase):
    FIRST = "First"
    LAST = "Last"
    MEMBER_ID = "Member ID"
    SUSPEND = "Suspend"


EXPECTED_CSV_HEADERS = {
    "create": CSVCreateFormat.all(),
    "suspend": CSVSuspendFormat.all(),
}


async def _create_user(row, brivo):
    user_id = await brivo.create_user(
        first_name=row[CSVCreateFormat.FIRST],
        last_name=row[CSVCreateFormat.LAST],
        member_id=row[CSVCreateFormat.MEMBER_ID],
        group_names=row[CSVCreateFormat.GROUP].split(","),
        card_number=row[CSVCreateFormat.CARD_NUMBER],
        facility_id=row[CSVCreateFormat.FACILITY_CODE],
    )
    log.info(f"User processed {row[CSVCreateFormat.MEMBER_ID]}")
    return user_id


async def _suspend_user(row, brivo):
    await brivo.toggle_member_suspend(
        first_name=row[CSVSuspendFormat.FIRST],
        last_name=row[CSVSuspendFormat.LAST],
        member_id=row[CSVSuspendFormat.MEMBER_ID],
        suspend=is_input_true(row[CSVSuspendFormat.SUSPEND]),
    )
    log.info(f"User processed {row[CSVCreateFormat.MEMBER_ID]}")
    return None


def remove_old_processed_files(t=86400):
    # remove filed older than 1 day
    log.debug(f"Removing files older than {t} seconds")

    for f in os.listdir(writable_path(PathNames.PROCESSED_DIR)):
        path = os.path.join(writable_path(PathNames.PROCESSED_DIR), f)
        if os.path.isfile(path) and (time.time() - os.path.getmtime(path) > t):
            os.remove(path)


async def process_csv(file_path, row_fn):
    def log_processing_time_if(only_if):
        average_processing_time_sec = (DateTime.now() - processing_started_at).total_seconds() / record_count
        if only_if:
            log.info(f"Processed {record_count} records. Average processing time: {average_processing_time_sec}")

    remove_old_processed_files()

    errors = []
    record_count = 0

    async with BrivoApiContext() as brivo:
        processing_started_at = DateTime.now()
        session["_last_csv_results_file"] = os.path.join(writable_path(PathNames.PROCESSED_DIR), random_string(16) + ".csv")
        log.debug(f"Results file: {session['_last_csv_results_file']}")
        with open(file_path, "r") as f:
            reader = csv.DictReader(f)

            with open(session["_last_csv_results_file"], "w") as fw:
                writer = csv.DictWriter(fw, fieldnames=reader.fieldnames + ["Error"])  # type: ignore
                writer.writeheader()

                for row in reader:
                    error = "Success"
                    record_count += 1
                    try:
                        await row_fn(row, brivo)
                        log_processing_time_if(record_count % 10 == 0)
                    except BrivoError as e:
                        error = e
                        errors.append(f"Error during processing member {row['Member ID']}: {e}")

                    writer.writerow({**row, "Error": error})

                    if len(errors) > 100:
                        break

    log_processing_time_if(True)

    return errors, record_count
