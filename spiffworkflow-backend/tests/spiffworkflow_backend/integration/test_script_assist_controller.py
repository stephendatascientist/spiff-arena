from typing import Any

from flask.app import Flask
from pytest_mock.plugin import MockerFixture
from starlette.testclient import TestClient

from spiffworkflow_backend.models.user import UserModel
from spiffworkflow_backend.routes import script_assist_controller
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import TextAssistAgent
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import TextAssistReply
from tests.spiffworkflow_backend.helpers.base_test import BaseTest

ENABLED = "SPIFFWORKFLOW_BACKEND_SCRIPT_ASSIST_ENABLED"
URL = "/v1.0/script-assist/process-message"


class FakeStructuredModel:
    def __init__(self, replies: list[TextAssistReply], requests: list[list]) -> None:
        self.replies = replies
        self.requests = requests

    def invoke(self, messages: list) -> dict[str, Any]:
        self.requests.append(messages)
        return {"raw": None, "parsed": self.replies.pop(0)}


class FakeChatModel:
    def __init__(self, replies: list[TextAssistReply]) -> None:
        self.replies = replies
        self.requests: list[list] = []

    def with_structured_output(self, _schema: Any, include_raw: bool = False) -> FakeStructuredModel:
        return FakeStructuredModel(self.replies, self.requests)


class TestScriptAssistController(BaseTest):
    def _patch_agent(self, mocker: MockerFixture, replies: list[TextAssistReply]) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def build(settings: AiBpmnSettings, unit_test_runner: Any) -> TextAssistAgent:
            model = FakeChatModel(replies)
            captured.setdefault("runners", []).append(unit_test_runner)
            captured.update({"settings": settings, "runner": unit_test_runner, "model": model})
            return TextAssistAgent(model, settings, unit_test_runner)

        mocker.patch.object(script_assist_controller, "_build_agent", side_effect=build)
        return captured

    def test_enabled_reports_provider_and_model(
        self,
        app: Flask,
        client: TestClient,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        headers = self.logged_in_headers(with_super_admin_user)
        with self.app_config_mock(app, ENABLED, False):
            response = client.get("/v1.0/script-assist/enabled", headers=headers)
        assert response.json() == {"ok": False, "provider": None, "model": None}

        with self.app_config_mock(app, ENABLED, True):
            with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL", "llama3.2"):
                response = client.get("/v1.0/script-assist/enabled", headers=headers)
            assert response.json() == {"ok": True, "provider": "ollama", "model": "llama3.2"}

            with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_SECRET_KEY_OPENAI_API", "sk-test"):
                response = client.get("/v1.0/script-assist/enabled", headers=headers)
            assert response.json() == {"ok": True, "provider": "openai", "model": "gpt-4o-mini"}

    def test_process_message_validates_the_request(
        self,
        app: Flask,
        client: TestClient,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        headers = self.logged_in_headers(with_super_admin_user)
        with self.app_config_mock(app, ENABLED, False):
            response = client.post(URL, headers=headers, json={"prompt": "sum"})
        assert response.status_code == 400
        assert response.json()["error_code"] == "script_assist_disabled"

        with self.app_config_mock(app, ENABLED, True):
            response = client.post(URL, headers=headers, json={"prompt": "  "})
            assert response.json()["error_code"] == "no_openai_query_provided"
            response = client.post(URL, headers=headers, json={"prompt": "x" * 4001})
            assert response.json()["error_code"] == "script_assist_prompt_too_long"
            response = client.post(URL, headers=headers, json={"prompt": "x", "current_content": "y" * 50001})
            assert response.json()["error_code"] == "script_assist_content_too_long"

    def test_script_generation_and_legacy_query(
        self,
        app: Flask,
        client: TestClient,
        mocker: MockerFixture,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        captured = self._patch_agent(
            mocker,
            [
                TextAssistReply(message="Doubles the amount.", content="total = amount * 2"),
                TextAssistReply(message="Same.", content="result = 1"),
            ],
        )
        headers = self.logged_in_headers(with_super_admin_user)
        with self.app_config_mock(app, ENABLED, True):
            response = client.post(
                URL,
                headers=headers,
                json={
                    "kind": "script",
                    "prompt": "double the amount",
                    "current_content": "",
                    "element": {"id": "calc", "name": "Calculate", "type": "bpmn:ScriptTask", "script_type": "script"},
                    "unit_tests": [{"input": {"amount": 2}, "expected": {"amount": 2, "total": 4}}],
                    "process_model_identifier": "group:model",
                },
            )
            legacy_response = client.post(URL, headers=headers, json={"query": "set result to one"})

        assert response.status_code == 200
        body = response.json()
        assert body["content"] == "total = amount * 2"
        assert body["result"] == "total = amount * 2"
        assert body["message"] == "Doubles the amount."
        assert body["tests"] == {"passed": 1, "total": 1}
        assert captured["runners"][0] is not None
        assert captured["runners"][1] is None

        assert legacy_response.status_code == 200
        assert legacy_response.json()["result"] == "result = 1"

    def test_markdown_generation(
        self,
        app: Flask,
        client: TestClient,
        mocker: MockerFixture,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        self._patch_agent(mocker, [TextAssistReply(message="Greeting.", content="Hello **{{ name }}**")])
        headers = self.logged_in_headers(with_super_admin_user)
        with self.app_config_mock(app, ENABLED, True):
            response = client.post(URL, headers=headers, json={"kind": "markdown", "prompt": "greet the user"})
            invalid = client.post(URL, headers=headers, json={"kind": "sql", "prompt": "x"})
        assert response.status_code == 200
        assert response.json()["content"] == "Hello **{{ name }}**"
        assert invalid.status_code == 400

    def test_unit_tests_need_permission(
        self,
        app: Flask,
        client: TestClient,
        mocker: MockerFixture,
        with_db_and_bpmn_file_cleanup: None,
    ) -> None:
        user = self.find_or_create_user("script_assist_user")
        self.add_permissions_to_user(user, target_uri="/script-assist/*", permission_names=["create", "read"])
        captured = self._patch_agent(mocker, [TextAssistReply(message="ok", content="total = 1")])
        with self.app_config_mock(app, ENABLED, True):
            response = client.post(
                URL,
                headers=self.logged_in_headers(user),
                json={
                    "prompt": "set total",
                    "unit_tests": [{"input": {}, "expected": {"total": 1}}],
                    "process_model_identifier": "group:model",
                },
            )
        assert response.status_code == 200
        assert captured["runner"] is None
        assert any("permission" in warning for warning in response.json()["warnings"])

    def test_provider_failures_are_reported(
        self,
        app: Flask,
        client: TestClient,
        mocker: MockerFixture,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        def failing_agent(settings: AiBpmnSettings, unit_test_runner: Any) -> Any:
            agent = mocker.Mock()
            agent.run.side_effect = ConnectionError("connection refused")
            return agent

        mocker.patch.object(script_assist_controller, "_build_agent", side_effect=failing_agent)
        with self.app_config_mock(app, ENABLED, True):
            response = client.post(URL, headers=self.logged_in_headers(with_super_admin_user), json={"prompt": "x"})
        assert response.status_code == 502
        assert response.json()["error_code"] == "script_assist_provider_error"
