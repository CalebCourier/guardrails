import os
from typing import Optional

from guard_rails_api_client import AuthenticatedClient
from guard_rails_api_client.api.guards import update_guard, validate
from guard_rails_api_client.models import Guard, ValidatePayload


class GuardrailsApiClient:
    _client: AuthenticatedClient = None
    base_url: str = None
    api_key: str = None

    def __init__(self, base_url: str = None, api_key: str = None):
        self.base_url = (
            base_url
            if base_url is not None
            else os.environ.get("GUARDRAILS_BASE_URL", "http://localhost:8000")
        )
        self.api_key = (
            api_key if api_key is not None else os.environ.get("GUARDRAILS_API_KEY")
        )
        self._client = AuthenticatedClient(
            base_url=self.base_url,
            follow_redirects=True,
            token=self.api_key,
            timeout=300,
        )

    def upsert_guard(self, guard: Guard):
        update_guard.sync(guard_name=guard.name, client=self._client, json_body=guard)

    def validate(
        self,
        guard: Guard,
        payload: ValidatePayload,
        openai_api_key: Optional[str] = None,
    ):
        openai_api_key = (
            openai_api_key
            if openai_api_key is not None
            else os.environ.get("OPENAI_API_KEY")
        )
        return validate.sync(
            guard_name=guard.name,
            client=self._client,
            json_body=payload,
            x_openai_api_key=openai_api_key,
        )
