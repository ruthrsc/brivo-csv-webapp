import logging

from flask import Blueprint, flash, redirect, render_template, request, session

from app.util import BrivoApiContext, EnvKeys, valid_config_found

auth = Blueprint("auth", __name__)

log = logging.getLogger(__name__)


@auth.route("/login")
async def login():
    log.info("Test")
    if not valid_config_found():
        flash(
            (f"Missing config variables. Make sure env {','.join(EnvKeys.all()) } are set"),
            "danger",
        )
    async with BrivoApiContext() as brivo:
        auth_url = await brivo.get_link_to_start_oauth()
    return render_template("login.html", auth_url=auth_url)


@auth.route("/oauth_callback")
async def oauth_callback():
    code = request.args.get("code")
    try:
        async with BrivoApiContext() as brivo:
            token_object = await brivo.exchange_oauth_code_for_token(code=code)
    except Exception as e:
        flash(f"Error during oauth callback: {e}", "danger")
        return redirect("/login")
    flash(f"Login successful!", "success")
    session["username"] = "authorized_by_oauth"
    # NOTE: this is not great. It will serialize and deserialize the token object
    # on every request. But it's fairly convienent.
    session["token_object"] = token_object
    return redirect("/")


@auth.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "success")
    return redirect("/login")
