from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.core.config import Settings


class KeycloakAdminError(RuntimeError):
    """Base error for the small, server-only Keycloak Admin API adapter."""


class KeycloakAdminConfigurationError(KeycloakAdminError):
    pass


class KeycloakAdminConflictError(KeycloakAdminError):
    pass


class KeycloakAdminNotFoundError(KeycloakAdminError):
    pass


class KeycloakAdminPermissionError(KeycloakAdminError):
    pass


class KeycloakAdminUnavailableError(KeycloakAdminError):
    pass


class KeycloakAdminRequestError(KeycloakAdminError):
    pass


@dataclass(frozen=True, slots=True)
class KeycloakUser:
    subject: str
    username: str | None
    email: str | None
    email_verified: bool
    enabled: bool
    given_name: str | None = None
    family_name: str | None = None


class KeycloakAdminClient:
    """Server-side Keycloak user and realm-role operations.

    The client authenticates with a confidential service account. It is never
    used by Flutter and never returns a Keycloak password or access token.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not settings.keycloak_admin_configured:
            raise KeycloakAdminConfigurationError(
                "Keycloak user administration is not configured."
            )

        self._settings = settings
        self._client = client or httpx.Client(
            timeout=settings.keycloak_admin_timeout_seconds,
        )
        self._owns_client = client is None
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create_user(
        self,
        *,
        username: str,
        email: str,
    ) -> KeycloakUser:
        response = self._request(
            "POST",
            "/users",
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": False,
            },
            expected_statuses={201},
        )

        subject = self._subject_from_location(response.headers.get("Location"))
        if subject is None:
            return self.find_user_by_username(username)

        return self.get_user(subject)

    def delete_user(self, subject: str) -> None:
        self._request(
            "DELETE",
            f"/users/{quote(subject, safe='')}",
            expected_statuses={204},
        )

    def get_user(self, subject: str) -> KeycloakUser:
        response = self._request(
            "GET",
            f"/users/{quote(subject, safe='')}",
            expected_statuses={200},
        )
        return self._user_from_payload(self._json_object(response))

    def find_user_by_username(self, username: str) -> KeycloakUser:
        response = self._request(
            "GET",
            "/users",
            params={
                "username": username,
                "exact": "true",
                "max": "2",
            },
            expected_statuses={200},
        )
        payload = self._json_list(response)

        for value in payload:
            user = self._user_from_payload(value)
            if user.username == username:
                return user

        raise KeycloakAdminNotFoundError("The Keycloak user was not found.")

    def list_users(
        self,
        *,
        first: int,
        max_results: int,
        search: str | None = None,
    ) -> list[KeycloakUser]:
        params: dict[str, str] = {
            "first": str(first),
            "max": str(max_results),
        }
        if search:
            params["search"] = search

        response = self._request(
            "GET",
            "/users",
            params=params,
            expected_statuses={200},
        )
        return [
            self._user_from_payload(value)
            for value in self._json_list(response)
        ]

    def set_password(self, subject: str, password: str) -> None:
        self._request(
            "PUT",
            f"/users/{quote(subject, safe='')}/reset-password",
            json={
                "type": "password",
                "temporary": False,
                "value": password,
            },
            expected_statuses={204},
        )

    def set_user_enabled(
        self,
        subject: str,
        *,
        enabled: bool,
    ) -> KeycloakUser:
        self._request(
            "PUT",
            f"/users/{quote(subject, safe='')}",
            json={"enabled": enabled},
            expected_statuses={204},
        )
        return self.get_user(subject)

    def get_realm_role_names(
        self, subject: str, *, effective: bool = True,
    ) -> set[str]:
        suffix = "/composite" if effective else ""
        response = self._request(
            "GET",
            (
                f"/users/{quote(subject, safe='')}"
                f"/role-mappings/realm{suffix}"
            ),
            expected_statuses={200},
        )
        return {
            name
            for role in self._json_list(response)
            if isinstance((name := role.get("name")), str) and name.strip()
        }

    def get_user_group_paths(self, subject: str) -> list[str]:
        paths: set[str] = set()
        first = 0
        while True:
            response = self._request(
                "GET",
                f"/users/{quote(subject, safe='')}/groups",
                params={"first": str(first), "max": "100"},
                expected_statuses={200},
            )
            page = self._json_list(response)
            paths.update(
                path for group in page
                if isinstance((path := group.get("path")), str)
            )
            if len(page) < 100:
                return sorted(paths)
            first += len(page)

    def add_realm_roles(
        self,
        subject: str,
        role_names: set[str],
    ) -> set[str]:
        normalized = self._normalized_roles(role_names)
        if normalized:
            self._request(
                "POST",
                (
                    f"/users/{quote(subject, safe='')}"
                    "/role-mappings/realm"
                ),
                json=[self._realm_role(role) for role in normalized],
                expected_statuses={204},
            )
        return self.get_realm_role_names(subject)

    def replace_managed_realm_roles(
        self,
        subject: str,
        *,
        desired_roles: set[str],
        managed_roles: frozenset[str],
    ) -> set[str]:
        normalized_desired = self._normalized_roles(desired_roles)
        normalized_managed = frozenset(
            self._normalized_roles(managed_roles)
        )

        if not normalized_desired.issubset(normalized_managed):
            raise KeycloakAdminRequestError(
                "One or more requested roles are not managed by RacePulse."
            )

        # Mutate direct assignments, then read effective roles again. Removing
        # a composite must not accidentally remove a requested inherited role.
        existing = self.get_realm_role_names(subject, effective=False)
        to_remove = (existing & normalized_managed) - normalized_desired
        to_add = normalized_desired - existing
        user_path = f"/users/{quote(subject, safe='')}/role-mappings/realm"

        if to_remove:
            self._request(
                "DELETE",
                user_path,
                json=[self._realm_role(role) for role in sorted(to_remove)],
                expected_statuses={204},
            )
        if to_add:
            self._request(
                "POST",
                user_path,
                json=[self._realm_role(role) for role in sorted(to_add)],
                expected_statuses={204},
            )

        return self.get_realm_role_names(subject)

    def send_verification_email(self, subject: str) -> None:
        self._request(
            "PUT",
            f"/users/{quote(subject, safe='')}/send-verify-email",
            params=self._email_action_parameters(),
            expected_statuses={204},
        )

    def send_password_reset_email(self, subject: str) -> None:
        self._request(
            "PUT",
            f"/users/{quote(subject, safe='')}/execute-actions-email",
            params=self._email_action_parameters(),
            json=["UPDATE_PASSWORD"],
            expected_statuses={204},
        )

    def _realm_role(self, role_name: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/roles/{quote(role_name, safe='')}",
            expected_statuses={200},
        )
        return self._json_object(response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: set[int],
        params: dict[str, str] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        url = f"{self._settings.keycloak_admin_realm_url.rstrip('/')}{path}"

        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._admin_access_token()}",
                },
            )
        except httpx.RequestError as error:
            raise KeycloakAdminUnavailableError(
                "Keycloak user administration is unavailable."
            ) from error

        if response.status_code not in expected_statuses:
            self._raise_for_response(response)

        return response

    def _admin_access_token(self) -> str:
        now = datetime.now(UTC)
        if (
            self._access_token is not None
            and self._access_token_expires_at is not None
            and self._access_token_expires_at > now
        ):
            return self._access_token

        try:
            response = self._client.post(
                self._settings.keycloak_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._settings.keycloak_admin_client_id,
                    "client_secret": (
                        self._settings.keycloak_admin_client_secret
                    ),
                },
                headers={"Accept": "application/json"},
            )
        except httpx.RequestError as error:
            raise KeycloakAdminUnavailableError(
                "Keycloak user administration is unavailable."
            ) from error

        if response.status_code != 200:
            self._raise_for_response(response)

        payload = self._json_object(response)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise KeycloakAdminRequestError(
                "Keycloak did not return an admin access token."
            )

        expires_in = payload.get("expires_in")
        lifetime = expires_in if isinstance(expires_in, int) else 30
        self._access_token = token
        self._access_token_expires_at = now + timedelta(
            seconds=max(lifetime - 15, 1)
        )
        return token

    def _raise_for_response(self, response: httpx.Response) -> None:
        if response.status_code == 404:
            raise KeycloakAdminNotFoundError("The Keycloak resource was not found.")
        if response.status_code == 409:
            raise KeycloakAdminConflictError(
                "A Keycloak user with that username or email already exists."
            )
        if response.status_code in {401, 403}:
            raise KeycloakAdminPermissionError(
                "The RacePulse backend service account lacks Keycloak permission."
            )
        if response.status_code in {400, 422}:
            raise KeycloakAdminRequestError(
                "Keycloak rejected the requested account change."
            )
        if response.status_code >= 500:
            raise KeycloakAdminUnavailableError(
                "Keycloak user administration is temporarily unavailable."
            )
        raise KeycloakAdminRequestError(
            "Keycloak could not complete the requested account change."
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise KeycloakAdminRequestError(
                "Keycloak returned an invalid response."
            ) from error

        if not isinstance(payload, dict):
            raise KeycloakAdminRequestError(
                "Keycloak returned an invalid response."
            )
        return payload

    @classmethod
    def _json_list(cls, response: httpx.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as error:
            raise KeycloakAdminRequestError(
                "Keycloak returned an invalid response."
            ) from error

        if not isinstance(payload, list) or not all(
            isinstance(value, dict) for value in payload
        ):
            raise KeycloakAdminRequestError(
                "Keycloak returned an invalid response."
            )
        return payload

    @staticmethod
    def _user_from_payload(payload: dict[str, Any]) -> KeycloakUser:
        subject = payload.get("id")
        if not isinstance(subject, str) or not subject:
            raise KeycloakAdminRequestError(
                "Keycloak returned a user without an identifier."
            )

        username = payload.get("username")
        email = payload.get("email")
        given_name = payload.get("firstName")
        family_name = payload.get("lastName")
        return KeycloakUser(
            subject=subject,
            username=username if isinstance(username, str) else None,
            email=email if isinstance(email, str) else None,
            email_verified=payload.get("emailVerified") is True,
            enabled=payload.get("enabled") is not False,
            given_name=given_name if isinstance(given_name, str) else None,
            family_name=family_name if isinstance(family_name, str) else None,
        )

    @staticmethod
    def _normalized_roles(values: set[str] | frozenset[str]) -> set[str]:
        return {
            value.strip().casefold()
            for value in values
            if value.strip()
        }

    def _email_action_parameters(self) -> dict[str, str] | None:
        parameters: dict[str, str] = {}
        if self._settings.keycloak_public_client_id:
            parameters["client_id"] = self._settings.keycloak_public_client_id
        if self._settings.keycloak_public_redirect_uri:
            parameters["redirect_uri"] = (
                self._settings.keycloak_public_redirect_uri
            )
        return parameters or None

    @staticmethod
    def _subject_from_location(location: str | None) -> str | None:
        if not location:
            return None

        path = urlparse(location).path.rstrip("/")
        subject = path.rsplit("/", maxsplit=1)[-1]
        return subject or None
