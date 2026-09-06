from types import SimpleNamespace

import httpx

from app.services.keycloak_admin_client import KeycloakAdminClient


def test_group_sync_reads_every_page_and_keeps_full_paths():
    calls = []
    client = KeycloakAdminClient.__new__(KeycloakAdminClient)

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs["params"]))
        first = int(kwargs["params"]["first"])
        groups = [
            {"path": f"/Fans/{index}"}
            for index in range(first, min(first + 100, 103))
        ]
        return httpx.Response(200, json=groups)

    client._request = request
    paths = client.get_user_group_paths("user-id")
    assert len(paths) == 103 and "/Fans/102" in paths
    assert [call[2]["first"] for call in calls] == ["0", "100"]


def test_role_demotion_preserves_requested_role_in_keycloak():
    client = KeycloakAdminClient.__new__(KeycloakAdminClient)
    client._settings = SimpleNamespace()
    direct = {"admin"}

    def request(method, path, **kwargs):
        if method == "GET" and path.endswith("/composite"):
            effective = direct | ({"editor", "fan"} if "admin" in direct else set())
            return httpx.Response(
                200, json=[{"name": role} for role in effective]
            )
        if method == "GET":
            return httpx.Response(
                200, json=[{"name": role} for role in direct]
            )
        roles = {role["name"] for role in kwargs["json"]}
        if method == "DELETE":
            direct.difference_update(roles)
        elif method == "POST":
            direct.update(roles)
        return httpx.Response(204)

    client._request = request
    client._realm_role = lambda role: {"name": role}
    assert client.replace_managed_realm_roles(
        "user-id",
        desired_roles={"fan"},
        managed_roles=frozenset({"fan", "editor", "admin"}),
    ) == {"fan"}
    assert direct == {"fan"}
