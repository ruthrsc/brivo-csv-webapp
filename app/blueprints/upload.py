import logging
import os
from datetime import datetime as DateTime

from flask import Blueprint, flash, redirect, render_template, request, send_file, session

from app.processing import EXPECTED_CSV_HEADERS, _create_user, _suspend_user, process_csv
from app.util import PathNames, check_csv_header, login_required, login_required_async, random_string, writable_path

upload = Blueprint("upload", __name__)
log = logging.getLogger(__name__)


@upload.route("/download_result")
@login_required
def download_processing_result():
    if "_last_csv_results_file" not in session:
        flash("No results to download", "danger")
        return redirect("/")
    # use send_from_directory
    download_name = DateTime.now().strftime("%Y%m%d_%H%M%S") + f"_brivo_results.csv"
    log.debug(f"Downloading results file: {session['_last_csv_results_file']}")

    return send_file(
        session["_last_csv_results_file"],
        as_attachment=True,
        download_name=download_name,
        conditional=False,
    )


def validate_file(file):
    if file.filename == "":
        flash("No selected file", "danger")
        return redirect("/")
    if not file.filename.lower().endswith(".csv"):  # type: ignore
        flash("Invalid file format. Only CSV files are accepted", "danger")
        return redirect("/")
    return None


def validate_csv_format(file_path, csv_type):
    is_header_valid, header_diff = check_csv_header(file_path, csv_type)
    if not is_header_valid:
        flash(
            "Invalid file format. Column headers have to be (without order) " + ",".join(EXPECTED_CSV_HEADERS[csv_type]),
            "danger",
        )
        flash(f"Suspicious entry: {header_diff}", "danger")
        flash(f"Column names are case sensitive - double check", "danger")
        flash(
            f"Refusing to import current file. Investigate the format and try again",
            "danger",
        )
        return redirect("/")
    return None


@upload.route("/upload", methods=["POST"])
@login_required_async
async def upload_file():
    def get_row_fn_for_type(typ):
        mapping = {"create": _create_user, "suspend": _suspend_user}
        fn = mapping.get(typ, None)
        if fn is None:
            raise ValueError(f"Invalid type {typ}")
        return fn

    csv_type = request.form["upload_type"]

    file = request.files["file"]
    if ret := validate_file(file):
        return ret

    errors = []
    record_count = 0

    if file:
        file_path = os.path.join(writable_path(PathNames.UPLOADS_DIR), random_string(16))
        try:
            file.save(file_path)
            if ret := validate_csv_format(file_path, csv_type):
                return ret
            errors, record_count = await process_csv(file_path, get_row_fn_for_type(csv_type))

            error_count = len(errors)
            if error_count > 0:
                flash(
                    f"{error_count} errors occurred during processing. Review the report",
                    "danger",
                )
            else:
                flash(f"File {file.filename} uploaded successfully", "success")
        except Exception as e:
            flash(f"Error during processing: {e}", "danger")
            log.exception(e)
        finally:
            os.remove(file_path)
    return render_template("upload_report.html", errors=errors, record_count=record_count)
