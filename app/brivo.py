# type: ignore  # pylance is not happy with aiohttp_requests and complaints a lot
import asyncio
import base64
import json
import logging
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from typing import Any, Dict

import aiohttp
from async_lru import alru_cache as memoize  # helper to cache results of the function to avoid unnecessary calls

from app.brivo_errors import BrivoApiError, BrivoError, BrivoUserNotFoundError
from app.util import gen_batches

log = logging.getLogger(__name__)

# requests are notoriously chatty; silence
requests_log = logging.getLogger("urllib3.connectionpool")
requests_log.setLevel(logging.WARNING)


class BrivoApi:
    """
    Async Brivo API client

    methods starting with _ are internal and should not be used directly
    methods starting with __ are private

    Brivo API requires multiple calls to manage user data:
    1. Create user
    2. Set member ID (custom field)
    3. Assign user to groups
    4. Assign credentials

    By parallelizing these calls, we reduced the user creation time around 3x.
    """

    def __init__(
        self,
        api_key,
        client_id,
        client_secret,
        redirect_uri,
        token_data: Dict[str, Any] = None,
    ):
        self.api_key = api_key
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.client_credentials_b64 = BrivoApi._encode_client_credentials(client_id, client_secret)
        self._access_token = None
        self._refresh_token = None
        self._expires_after = DateTime.now()
        if token_data:
            self._access_token = token_data["access_token"]
            self._refresh_token = token_data["refresh_token"]
            self._expires_after = token_data["expires_after"]

        self._request_timeout = 10
        self._aiohttp_session = None

    async def _http_request(self, method, url, headers=None, data=None, allow_redirects=False):  # pragma: no cover

        if self._aiohttp_session is None or self._aiohttp_session.closed or self._aiohttp_session._loop.is_closed():
            log.debug("Creating new aiohttp session")
            self._aiohttp_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._request_timeout))

        log.debug(f"Making {method} request to {url}")
        log.debug(f"Headers: {headers}")
        log.debug(f"Data: {data}")
        async with self._aiohttp_session.request(
            method, url, headers=headers, data=data, allow_redirects=allow_redirects
        ) as response:
            await response.read()
            return response

    async def close(self):
        # there is something about flask event loops. If the session is not manually closed
        # it will throw exceptions on request teardown
        if self._aiohttp_session is not None:
            await self._aiohttp_session.close()

    @staticmethod
    def _encode_client_credentials(client_id, client_secret):
        client_credentials = f"{client_id}:{client_secret}"
        return base64.b64encode(client_credentials.encode()).decode()

    @property
    def _auth_request_headers(self):
        return {
            "Authorization": f"Basic {self.client_credentials_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
            "api-key": self.api_key,
        }

    @property
    def _api_request_headers(self):
        return {
            "Authorization": f"Bearer {self._access_token}",
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }

    async def get_link_to_start_oauth(self):
        return f"https://auth.brivo.com/oauth/authorize?response_type=code&client_id={self.client_id}"

    def __set_token_data(self, data):
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._expires_after = DateTime.now() + TimeDelta(seconds=data["expires_in"] - 5)

    def get_token_data(self):
        return {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_after": self._expires_after,
        }

    async def exchange_oauth_code_for_token(self, code):
        log.debug("Exchange code for token")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        res = await self._http_request(
            "POST",
            "https://auth.brivo.com/oauth/token",
            headers=self._auth_request_headers,
            data=data,
        )
        await self._process_error(res)
        data = await res.json()
        self.__set_token_data(data)
        return self.get_token_data()

    async def healthcheck(self):
        if self._expires_after < DateTime.now():
            await self.refresh_token()
        # plain request to get more info from the api with minimal processing
        res = await self._http_request(
            "GET", "https://api.brivo.com/v1/api/administrators", headers=self._api_request_headers
        )
        await self._process_error(res)
        return res

    async def refresh_token(self):
        log.debug("Refreshing token")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        res = await self._http_request(
            "POST",
            "https://auth.brivo.com/oauth/token",
            headers=self._auth_request_headers,
            data=data,
        )
        await self._process_error(res)
        data = await res.json()
        self.__set_token_data(data)
        return self.get_token_data()

    async def _process_error(self, response):
        if response.status >= 400:
            data = await response.json()
            # brivo api reports errors inconsistently
            # sometimes is message, sometimes it's error + error_description
            # here is "python-ish" way to handle it
            for key in ["message", "error"]:
                if key in data:
                    msg = f"Error from BrivoAPI [{response.status}]: " + data[key]
                    if "error_description" in data:
                        msg += f" - {data['error_description']}"
                    log.error(msg)
                    raise BrivoApiError(msg)
            # if we didn't find any message, just raise the whole response
            raise BrivoApiError(f"Error from BrivoAPI [{response.status}]: {await response.text()}")

    async def call(self, url, method="GET", data=None, body=None):
        if body and data:
            raise ValueError("body and data cannot be used together")

        if self._expires_after < DateTime.now():
            await self.refresh_token()

        request_data = body if body else data

        log.debug(f"Calling {url}, method: {method}, data: {request_data}")

        # TODO: support pagination - it might be important for some discovery
        # queries
        request_started_at = DateTime.now()
        response = await self._http_request(method, url, headers=self._api_request_headers, data=request_data)
        request_duration = (DateTime.now() - request_started_at).total_seconds()
        log.debug(f"Timing - {method} {url} took {request_duration:0.3f}sec")
        await self._process_error(response)
        if response.status == 204:
            # HACK: should be None, but pylance is complaining about typing
            # so returning empty dict
            return dict()
        try:
            request_data = await response.json()
        except json.JSONDecodeError:
            log.error(f"Response [{response.status}]: {await response.text()}")
            raise BrivoError("Invalid JSON response")

        log.debug(f"Response [{response.status}]: {request_data}")

        return request_data.get("data", request_data)

    async def list_all_user_credentials(self, user_id):
        data = await self.call(f"https://api.brivo.com/v1/api/users/{user_id}/credentials")
        return [credential["id"] for credential in data]

    async def remove_all_credentials_from_user(self, user_id, member_id):
        log.info(f"Removing all credentials from member_id {member_id}")
        calls = [
            self.call(f"https://api.brivo.com/v1/api/users/{user_id}/credentials/{credential_id}", method="DELETE")
            for credential_id in await self.list_all_user_credentials(user_id)
        ]
        for batch in gen_batches(calls, 5):
            await asyncio.gather(*batch)
            await asyncio.sleep(0.1)
        return True

    async def delete_user(self, user_id, are_you_sure=False):
        if not are_you_sure:
            raise ValueError("You must pass are_you_sure=True to delete the user")
        return await self.call(f"https://api.brivo.com/v1/api/users/{user_id}", method="DELETE")

    async def create_user(self, first_name, last_name, member_id, group_names=None, card_number=None, facility_id=None):
        if (facility_id is None) ^ (card_number is None):
            raise ValueError("Both facility_id and card_number must be provided or none of them")

        if group_names is None:
            group_names = []

        try:
            user = await self.find_user_by_member_id(member_id)
            if user["firstName"] != first_name or user["lastName"] != last_name:
                msg = (
                    f"User with member ID {member_id} already exists in Brivo, but with different name. "
                    "Refusing to update automatically"
                )
                log.error(msg)
                raise BrivoError(msg)
            log.info(f"First and last name match. Updating member {member_id}")
            # NOTE: I tested the time it takes to delete a user and create a new one.
            # The difference in time between deleting the entire user and just removing their
            # credentials was approximately ≈0.3 seconds. However, deleting the entire user
            # left a confusing trace in the audit log, which could be difficult to interpret.
            # Therefore, we are currently implementing the following approach:
            # 1. Remove all credentials and groups associated with the user.
            # 2. Update the user's information accordingly.
            await asyncio.gather(
                *[
                    self.remove_all_groups_from_user(user['id'], member_id),
                    self.remove_all_credentials_from_user(user['id'], member_id),
                ]
            )
        except BrivoUserNotFoundError:
            log.debug(f"Creating user with member ID {member_id}")
            url = "https://api.brivo.com/v1/api/users"
            user = await self.call(url, method="POST", body=json.dumps({"firstName": first_name, "lastName": last_name}))
        futures = [self._set_member_id_value(user['id'], member_id), self._assign_user_to_groups(user['id'], group_names)]

        if card_number:
            futures.append(self.assign_card(user['id'], facility_id=facility_id, card_number=card_number))
        await asyncio.gather(*futures)
        return user['id']

    async def _assign_user_to_groups(self, user_id, group_names):
        res = []
        for group_batch in gen_batches(group_names, 5):
            x = await asyncio.gather(*[self.find_group_id_by_name(name) for name in group_batch])
            res.extend(x)
            await asyncio.sleep(0.1)
        return await self.call(
            f"https://api.brivo.com/v1/api/users/{user_id}/groups",
            method="POST",
            body=json.dumps({"addGroups": x}),
        )

    async def _set_member_id_value(self, user_id, value):
        member_id_field_id = await self._find_memberid_custom_field()
        return await self.call(
            f"https://api.brivo.com/v1/api/users/{user_id}/custom-fields/{member_id_field_id}",
            method="PUT",
            body=json.dumps({"value": value}),
        )

    @memoize(maxsize=None)
    async def _find_memberid_custom_field(self):
        # Member id is implemented as a custom field in Brivo
        # we need an ID of this field to assign a value to it
        expected_field_names = [
            "member id",
            "memberid",
            "member_id",
            "member_number",
            "member number",
            "membernumber",
        ]
        fields = await self.call("https://api.brivo.com/v1/api/custom-fields?pageSize=100")
        for x in fields:
            if x["fieldName"].lower() in expected_field_names:
                return x["id"]
        raise BrivoError(
            f"Member ID custom field not found. Check if custom field named {','.join(expected_field_names)} exists"
        )

    @memoize(maxsize=None)
    async def find_group_id_by_name(self, name):
        # TODO: optimize with ?filter in query name
        log.info(f"Looking up group {name}")
        groups = await self.call("https://api.brivo.com/v1/api/groups?pageSize=100")
        for group in groups:
            if group["name"].lower() == name.lower():
                return group["id"]
        raise BrivoError(f"Group {name} not found")

    async def assign_card(self, user_id, facility_id, card_number):
        # Card number from the CSV file is the reference ID in Brivo
        # To assign a card to a user, we need to find the internal credential ID
        # which is different from the reference ID
        log.info(f"Assinging card to user")
        credential = await self._find_credential(facility_id, card_number)
        return await self._assign_credential(user_id, credential['id'])

    async def _assign_credential(self, user_id, credential_id):
        # TODO: check with Brian if we need to handle EffectiveFrom/To dates.
        # From initial testing, it seems Brivo automatically sets EffectiveFrom from first assignment date

        return await self.call(
            f"https://api.brivo.com/v1/api/users/{user_id}/credentials/{credential_id}",
            method="PUT",
        )

    @memoize(maxsize=None)
    async def _find_credential(self, facility_id, ref_id):
        log.info(f"Checking credential ref_id {ref_id}")
        url = f"https://api.brivo.com/v1/api/credentials?filter=reference_id__eq:{ref_id};facility_code__eq:{facility_id}"
        data = await self.call(url)
        if len(data) == 0:
            raise BrivoError(f"No credentials with reference ID {ref_id} found in facility {facility_id}")
        elif len(data) > 1:
            raise BrivoError(f"Multiple credentials with reference ID {ref_id} found in facility {facility_id}")
        return data[0]

    async def find_user_by_member_id(self, member_id):
        member_id_field_id = await self._find_memberid_custom_field()
        url = f"https://api.brivo.com/v1/api/users?filter=cf_{member_id_field_id}__eq:{member_id}"
        data = await self.call(url)
        if len(data) == 0:
            raise BrivoUserNotFoundError(f"No user with member ID {member_id} found")
        elif len(data) > 1:
            raise BrivoError(f"Multiple users with member ID {member_id} found in Brivo. Proceed manually... with caution")
        return data[0]

    async def toggle_member_suspend(self, member_id, suspend: bool, first_name=None, last_name=None):
        user = await self.find_user_by_member_id(member_id)
        # quick safety check
        if any([first_name, last_name]) and (user["firstName"] != first_name or user["lastName"] != last_name):
            raise BrivoError("Member ID does not match the first and last name. Refusing to suspend")
        return await self.call(
            f"https://api.brivo.com/v1/api/users/{user['id']}/suspended",
            method="PUT",
            body=json.dumps({"suspended": suspend}),
        )

    async def remove_all_groups_from_user(self, user_id, member_id):
        log.info(f"Removing all groups from member {member_id}")
        groups = await self.call(
            f"https://api.brivo.com/v1/api/users/{user_id}/groups",
        )
        group_ids = [group["id"] for group in groups]

        if len(group_ids) == 0:
            # user is not assigned to any group
            return
        await self.call(
            f"https://api.brivo.com/v1/api/users/{user_id}/groups",
            method="POST",
            body=json.dumps({"removeGroups": group_ids}),
        )

    async def update_user(self, old_member_id, **kwargs):
        # NOTE: The goal of update flow is currently unclear to me.
        # If the intention is to reassign a card to a new user, consider the following approach:
        # 1. Delete the old user associated with the card.
        # 2. Add the new user and assign the same card to them.
        # This method ensures that audit logs accurately reflect the history of card ownership by
        # two different people (old and new user). Without this, the audit logs may show the card
        # being assigned to the new user without any indication that it was previously assigned to.
        #
        # Additional questions to clarify:
        # - What should be done with the 'suspended' flag? Should it be reset when reassigning the card?
        # - How should the EffectiveFrom/To dates be handled during this process?
        #
        # Need further clarification on these points before proceeding with implementation.
        # Consult with Brian for more details.

        """
        warnings.warn("Rather than updating user, consider deleting and creating new user")
        user = self.find_user_by_member_id(old_member_id)
        member_id = kwargs.pop("newMemberId")
        group_names = kwargs.pop("groups", [])
        user = self.call(f"https://api.brivo.com/v1/api/users/{user['id']}", method="PUT", body=json.dumps(kwargs))
        self._set_member_id_value(user['id'], member_id)
        self.remove_all_groups_from_user(user['id'])
        self._assign_user_to_groups(user['id'], group_names)
        """
        raise NotImplementedError("Not implemented - awaiting clarification on the requirements")
