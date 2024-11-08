# test_brivo.py
import asyncio
import json
import logging
import re
import unittest
import urllib.parse
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from unittest.mock import AsyncMock, patch

from app.brivo import BrivoApi, BrivoApiError
from app.brivo_errors import BrivoError

log = logging.getLogger(__name__)


def asynctest(coro):
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()

    wrapper.__test__ = True  # type: ignore[reportFunctionMemberAccess]
    return wrapper


class CallResponseMocker:
    def __init__(self, maps=None, autoadd_default_success_responses=True):
        """
        maps overrides over autoadd_default_success_responses
        """
        if maps is None:
            maps = {}
        self.urlmap = {}
        self.calls = []
        if autoadd_default_success_responses:
            self.add_default_success_responses()
        for path, value in maps.items():
            self.add_url(path, value[0], value[1])

    def add_default_success_responses(self):
        self.add_url(
            "/oauth/token",
            200,
            {
                "access_token": "test_access_token",
                "refresh_token": "test_refresh_token",
                "expires_in": 3600,
            },
        )

    def add_url(self, path, status, json):
        self.urlmap[path] = (status, json)

    def count_calls(self, method=None, path=None):
        count = 0
        for call in self.calls:
            if method is not None and call[0] != method:
                continue
            if path is not None and not call[1].startswith(path):
                continue
            count += 1
        return count

    def __call__(self, method, url, headers, data=None):
        if data is None:
            data = {}
        parsed_url = urllib.parse.urlparse(url)
        haystack = parsed_url.path
        if parsed_url.query:
            haystack += "?" + parsed_url.query
        self.calls.append((method, haystack, headers, data))
        for regex, entry in self.urlmap.items():
            if isinstance(entry, Exception):
                # Mocks a exception thrown by BrivoApi._http_request call
                raise entry
            if re.match(regex, haystack):
                return AsyncMock(status=entry[0], json=AsyncMock(return_value=entry[1]))
        raise Exception(f"Mocked call to unknown path: {haystack}. Full url was: {url}")

    def clear_calls(self):
        self.calls = []

    def get_calls(self):
        return self.calls


class BrivoApiTestCase(unittest.TestCase):

    def setUp(self):
        self.api_key = "test_api_key"
        self.client_id = "test_client_id"
        self.client_secret = "test_client_secret"

        self.brivo = BrivoApi(
            api_key=self.api_key,
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri="http://fake.redirect.uri:8950/callback",
            token_data={
                "access_token": "test_access_token",
                "refresh_token": "test_refresh_token",
                "expires_after": DateTime.now() + TimeDelta(seconds=3600),
            },
        )

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_expired_token(self, mock_http_request):
        self.brivo._refresh_token = "abc"
        self.brivo._expires_after = DateTime.now() - TimeDelta(seconds=1)

        mock_http_request.side_effect = CallResponseMocker(
            maps={
                "/oauth/token": (
                    200,
                    {"access_token": "new_access_token", "refresh_token": "new_refresh_token", "expires_in": 30},
                )
            },
            autoadd_default_success_responses=False,
        )

        await self.brivo.refresh_token()

        self.assertEqual(self.brivo._access_token, "new_access_token")
        self.assertEqual(self.brivo._refresh_token, "new_refresh_token")
        self.assertTrue(self.brivo._expires_after > DateTime.now() + TimeDelta(seconds=24))
        self.assertTrue(self.brivo._expires_after < DateTime.now() + TimeDelta(seconds=26))

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_call_method_logic(self, mock_http_request):
        mock_http_request.side_effect = CallResponseMocker(
            maps={
                "/test": (403, {"message": "Fake permission denied"}),
                "/empty": (204, None),
                "/success": (200, {"data": [1, 2, 3]}),
            }
        )

        with self.assertRaises(BrivoApiError) as cm:
            _ = await self.brivo.call(method="GET", url="/test", data={"test": "data"})
        self.assertIn("Fake permission denied", str(cm.exception))
        self.assertIn("[403]", str(cm.exception))

        res = await self.brivo.call(method="POST", url="/empty", data=None)
        self.assertEqual(res, {})

        res = await self.brivo.call(method="POST", url="/success", data=None)
        self.assertEqual(res, [1, 2, 3])

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_remove_all_credentials_from_user_missing_member_id(self, mock_http_request):
        call_mocker = CallResponseMocker(
            maps={
                "/v1/api/users/": (200, {"data": [{"id": "42"}, {"id": "43"}]}),
            }
        )
        mock_http_request.side_effect = call_mocker

        await self.brivo.remove_all_credentials_from_user(user_id="000", member_id=None)
        self.assertEqual(1, call_mocker.count_calls("GET", "/v1/api/users/000/credentials"))
        self.assertEqual(1, call_mocker.count_calls("DELETE", "/v1/api/users/000/credentials/42"))
        self.assertEqual(1, call_mocker.count_calls("DELETE", "/v1/api/users/000/credentials/43"))

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_member_id_discovery(self, mock_http_request):
        call_mocker = CallResponseMocker(
            maps={"/v1/api/custom-fields": (200, {"data": [{"fieldName": "custom1", "id": -1}]})}
        )
        mock_http_request.side_effect = call_mocker

        with self.assertRaises(BrivoError):
            await self.brivo._find_memberid_custom_field()

        call_mocker.urlmap["/v1/api/custom-fields"] = (200, {"data": [{"fieldName": "member id", "id": 42}]})
        await self.brivo._find_memberid_custom_field()

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_create_user(self, mock_http_request):
        call_mocker = CallResponseMocker(
            maps={
                "/v1/api/custom-fields": (200, {"data": [{"fieldName": "member id", "id": 11}]}),
                r"/v1/api/users\?filter=cf_11__eq:999999": (200, {"data": []}),
                r"/v1/api/credentials\?filter=reference_id__eq:999;facility_code__eq:123": (
                    200,
                    {"data": [{"id": "cred_id"}]},
                ),
                r"/v1/api/users": (200, {"data": {"id": "42"}}),
                r"/v1/api/groups\?pageSize=100": (
                    200,
                    {"data": [{"id": "111", "name": "Group1"}, {"id": "222", "name": "Group2"}]},
                ),
                # r"/v1/api/users\?filter=cf_42__eq:999999": (200, {"data": [{"firstName": "Bad", "lastName": "Kwik"}]}),
                # r"/v1/api/users\?filter=cf_42__eq:999999": (200, {"data": [{"id": "42"}]}),
            }
        )
        mock_http_request.side_effect = call_mocker

        await self.brivo.create_user(
            first_name="Test",
            last_name="User",
            member_id="999999",
            group_names=["Group1", "Group2"],
            card_number=999,
            facility_id=123,
        )

        self.assertEqual(1, call_mocker.count_calls("GET", "/v1/api/custom-fields"))
        self.assertEqual(1, call_mocker.count_calls("GET", "/v1/api/users?filter=cf_11__eq:999999"))
        self.assertEqual(2, call_mocker.count_calls("POST", "/v1/api/users"))
        last_call = call_mocker.get_calls()[-1]
        self.assertEqual("POST", last_call[0])
        self.assertEqual("/v1/api/users/42/groups", last_call[1])
        self.assertEqual({"addGroups": ["111", "222"]}, json.loads(last_call[3]))

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_create_user_existing_user(self, mock_http_request):
        call_mocker = CallResponseMocker(
            maps={
                "/v1/api/custom-fields": (200, {"data": [{"fieldName": "member id", "id": 11}]}),
                r"/v1/api/users\?filter=cf_11__eq:999999": (
                    200,
                    {"data": [{"firstName": "Bob", "lastName": "Smith", "id": "987"}]},
                ),
                r"/v1/api/credentials\?filter=reference_id__eq:999;facility_code__eq:123": (
                    200,
                    {"data": [{"id": "cred_id"}]},
                ),
                r"/v1/api/users": (200, {"data": {"id": "42"}}),
                r"/v1/api/groups\?pageSize=100": (
                    200,
                    {"data": [{"id": "111", "name": "Group1"}, {"id": "222", "name": "Group2"}]},
                ),
                # r"/v1/api/users\?filter=cf_42__eq:999999": (200, {"data": [{"firstName": "Bad", "lastName": "Kwik"}]}),
                # r"/v1/api/users\?filter=cf_42__eq:999999": (200, {"data": [{"id": "42"}]}),
            }
        )
        mock_http_request.side_effect = call_mocker

        with self.assertRaises(BrivoError) as cm:
            await self.brivo.create_user(
                first_name="Test",
                last_name="User",
                member_id="999999",
                group_names=["Group1", "Group2"],
                card_number=999,
                facility_id=123,
            )
        self.assertIn("already exists", str(cm.exception))

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_toggle_user(self, mock_http_request):
        call_mocker = CallResponseMocker(
            maps={
                r"/v1/api/custom-fields\?pageSize=100": (200, {"data": [{"fieldName": "member id", "id": 11}]}),
                r"/v1/api/users\?filter=cf_11__eq:42": (
                    200,
                    {"data": [{"id": "16", "firstName": "Test", "lastName": "User"}]},
                ),
                r"/v1/api/users/16/suspended": (200, {"data": {"id": "16", "suspended": False}}),
            }
        )
        mock_http_request.side_effect = call_mocker
        with self.assertRaises(BrivoError) as cm:
            await self.brivo.toggle_member_suspend(member_id="42", suspend=True, first_name="Tes", last_name="Use")
        self.assertIn("Refusing", str(cm.exception))
        _ = await self.brivo.toggle_member_suspend(member_id="42", suspend=True, first_name="Test", last_name="User")
        self.assertEqual(call_mocker.count_calls("PUT", "/v1/api/users/16/suspended"), 1)

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_remove_groups(self, mock_http_request):
        call_mocker = CallResponseMocker(
            maps={
                r"/v1/api/users/42/groups": (
                    200,
                    {"data": [{"id": "111", "name": "Group1"}, {"id": "222", "name": "Group2"}]},
                ),
            }
        )
        mock_http_request.side_effect = call_mocker
        await self.brivo.remove_all_groups_from_user(user_id="42", member_id="123")
        self.assertEqual(1, call_mocker.count_calls("GET", "/v1/api/users/42/groups"))
        self.assertEqual(1, call_mocker.count_calls("POST", "/v1/api/users/42/groups"))
        last_call = call_mocker.get_calls()[-1]
        self.assertEqual("POST", last_call[0])
        self.assertEqual({"removeGroups": ["111", "222"]}, json.loads(last_call[3]))

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @asynctest
    async def test_exchange_oauth_code_for_token(self, mock_http_request):
        mock_http_request.side_effect = CallResponseMocker(
            maps={
                "/oauth/token": (
                    200,
                    {
                        "access_token": "whoop_access_token",
                        "refresh_token": "whoop_refresh_token",
                        "expires_in": 300,
                    },
                )
            },
            autoadd_default_success_responses=False,
        )

        token_data = await self.brivo.exchange_oauth_code_for_token("test_code")

        self.assertEqual(token_data["access_token"], "whoop_access_token")
        self.assertEqual(token_data["refresh_token"], "whoop_refresh_token")
        print(token_data["expires_after"] - DateTime.now())
        self.assertTrue(token_data["expires_after"] > DateTime.now() + TimeDelta(seconds=294))
        self.assertTrue(token_data["expires_after"] < DateTime.now() + TimeDelta(seconds=296))

        self.assertEqual(1, mock_http_request.call_count)

    @patch('app.brivo.BrivoApi._http_request', new_callable=AsyncMock)
    @patch('app.brivo.BrivoApi.refresh_token', new_callable=AsyncMock)
    @asynctest
    async def test_healthcheck(self, mock_refresh_token, mock_http_request):
        # extra unit test since healthcheck is a bit special
        self.brivo._expires_after = DateTime.now() - TimeDelta(seconds=1)
        mock_http_request.side_effect = CallResponseMocker(
            maps={
                "/v1/api/administrators": (200, {"data": "some_data"}),
            }
        )

        res = await self.brivo.healthcheck()
        self.assertEqual(res.status, 200)
        self.assertEqual(await res.json(), {"data": "some_data"})
        mock_refresh_token.assert_called_once()
        self.assertEqual(mock_http_request.call_count, 1)

        self.brivo._expires_after = DateTime.now() + TimeDelta(seconds=3600)
        mock_refresh_token.reset_mock()

        res = await self.brivo.healthcheck()
        self.assertEqual(res.status, 200)
        self.assertEqual(await res.json(), {"data": "some_data"})
        mock_refresh_token.assert_not_called()
        self.assertEqual(mock_http_request.call_count, 2)


if __name__ == '__main__':
    # logging.basicConfig(level=logging.ERROR)
    unittest.main()
