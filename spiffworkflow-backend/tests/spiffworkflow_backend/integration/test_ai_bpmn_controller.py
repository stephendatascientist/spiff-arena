from typing import Any

from flask.app import Flask
from pytest_mock.plugin import MockerFixture
from starlette.testclient import TestClient

from spiffworkflow_backend.models.user import UserModel
from spiffworkflow_backend.routes import ai_bpmn_controller
from spiffworkflow_backend.services.ai_bpmn.bpmn_agent import AiBpmnAgent
from spiffworkflow_backend.services.ai_bpmn.bpmn_agent import AssistantReply
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from tests.spiffworkflow_backend.helpers.base_test import BaseTest


class FakeStructuredModel:
    def __init__(self, reply: AssistantReply) -> None:
        self.reply = reply

    def invoke(self, _messages: list) -> dict[str, Any]:
        return {"raw": None, "parsed": self.reply}


class FakeChatModel:
    def __init__(self, reply: AssistantReply) -> None:
        self.reply = reply

    def with_structured_output(self, _schema: Any, include_raw: bool = False) -> FakeStructuredModel:
        return FakeStructuredModel(self.reply)


class TestAiBpmnController(BaseTest):
    def test_enabled_reports_the_configured_model(
        self,
        app: Flask,
        client: TestClient,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        headers = self.logged_in_headers(with_super_admin_user)
        response = client.get("/v1.0/ai-bpmn/enabled", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"ok": False, "provider": None, "model": None}

        with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED", True):
            with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL", "llama3.2"):
                response = client.get("/v1.0/ai-bpmn/enabled", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"ok": True, "provider": "ollama", "model": "llama3.2"}

    def test_generate_requires_the_feature_and_a_prompt(
        self,
        app: Flask,
        client: TestClient,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        headers = self.logged_in_headers(with_super_admin_user)
        response = client.post("/v1.0/ai-bpmn/generate", headers=headers, json={"prompt": "leave request"})
        assert response.status_code == 400
        assert response.json()["error_code"] == "ai_bpmn_disabled"

        with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED", True):
            response = client.post("/v1.0/ai-bpmn/generate", headers=headers, json={"prompt": "  "})
            assert response.status_code == 400
            assert response.json()["error_code"] == "ai_bpmn_prompt_missing"

            response = client.post("/v1.0/ai-bpmn/generate", headers=headers, json={"prompt": "x" * 4001})
            assert response.status_code == 400
            assert response.json()["error_code"] == "ai_bpmn_prompt_too_long"

    def test_generate_returns_the_updated_diagram(
        self,
        app: Flask,
        client: TestClient,
        mocker: MockerFixture,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        reply = AssistantReply(
            message="Added a review.",
            ops=["add n1 start", 'add t1 user "Review"', "add e1 end", "n1 -> t1", "t1 -> e1"],
        )
        mocker.patch.object(
            ai_bpmn_controller,
            "_build_agent",
            side_effect=lambda settings: AiBpmnAgent(FakeChatModel(reply), settings),
        )
        headers = self.logged_in_headers(with_super_admin_user)
        with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED", True):
            response = client.post(
                "/v1.0/ai-bpmn/generate",
                headers=headers,
                json={"prompt": "review", "history": [{"role": "user", "content": "hi"}]},
            )
            invalid_response = client.post(
                "/v1.0/ai-bpmn/generate", headers=headers, json={"prompt": "review", "bpmn_xml": "<broken"}
            )
        assert invalid_response.status_code == 400
        assert invalid_response.json()["error_code"] == "ai_bpmn_invalid_diagram"

        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Added a review."
        assert 'Added user task "Review"' in body["changes"]
        assert "<bpmn:userTask" in body["bpmn_xml"]
        assert body["warnings"] == []

    def test_generate_maps_provider_failures(
        self,
        app: Flask,
        client: TestClient,
        mocker: MockerFixture,
        with_db_and_bpmn_file_cleanup: None,
        with_super_admin_user: UserModel,
    ) -> None:
        def failing_agent(settings: AiBpmnSettings) -> Any:
            agent = mocker.Mock()
            agent.run.side_effect = ConnectionError("connection refused")
            return agent

        mocker.patch.object(ai_bpmn_controller, "_build_agent", side_effect=failing_agent)
        headers = self.logged_in_headers(with_super_admin_user)
        with self.app_config_mock(app, "SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED", True):
            response = client.post("/v1.0/ai-bpmn/generate", headers=headers, json={"prompt": "review"})
            assert response.status_code == 502
            assert response.json()["error_code"] == "ai_bpmn_provider_error"
