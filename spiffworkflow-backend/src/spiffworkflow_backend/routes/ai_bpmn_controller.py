import importlib
from typing import Any

from flask import current_app
from flask import jsonify
from flask import make_response
from flask.wrappers import Response

from spiffworkflow_backend.exceptions.api_error import ApiError
from spiffworkflow_backend.services.ai_bpmn.bpmn_document import BpmnDocumentError
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnConfigurationError
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from spiffworkflow_backend.services.ai_bpmn.llm_provider import create_chat_model

MAX_PROMPT_LENGTH = 4000


def _is_enabled() -> bool:
    return bool(current_app.config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED"))


def enabled() -> Response:
    if not _is_enabled():
        return make_response(jsonify({"ok": False, "provider": None, "model": None}), 200)
    settings = AiBpmnSettings.from_app_config()
    return make_response(jsonify({"ok": True, "provider": settings.provider, "model": settings.model}), 200)


def _build_agent(settings: AiBpmnSettings) -> Any:
    # imported lazily so the backend runs without the optional "ai" extra installed
    try:
        agent_module = importlib.import_module("spiffworkflow_backend.services.ai_bpmn.bpmn_agent")
    except ImportError as exception:
        raise AiBpmnConfigurationError(
            "The AI BPMN assistant needs the backend 'ai' extra. Install it with `uv sync --extra ai`."
        ) from exception
    return agent_module.AiBpmnAgent(create_chat_model(settings), settings)


def generate(body: dict) -> Response:
    if not _is_enabled():
        raise ApiError(
            error_code="ai_bpmn_disabled",
            message="The AI BPMN assistant is not enabled. Set SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED=true.",
            status_code=400,
        )
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ApiError(
            error_code="ai_bpmn_prompt_missing", message="Please describe what the diagram should do.", status_code=400
        )
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ApiError(
            error_code="ai_bpmn_prompt_too_long",
            message=f"Please keep the request under {MAX_PROMPT_LENGTH} characters.",
            status_code=400,
        )
    history = [item for item in (body.get("history") or []) if isinstance(item, dict)]

    try:
        settings = AiBpmnSettings.from_app_config()
        agent = _build_agent(settings)
        result = agent.run(prompt, body.get("bpmn_xml"), history, body.get("process_id"))
    except AiBpmnConfigurationError as exception:
        raise ApiError(error_code="ai_bpmn_configuration_error", message=str(exception), status_code=500) from exception
    except BpmnDocumentError as exception:
        raise ApiError(error_code="ai_bpmn_invalid_diagram", message=str(exception), status_code=400) from exception
    except ApiError:
        raise
    except Exception as exception:
        current_app.logger.exception("AI BPMN generation failed")
        raise ApiError(
            error_code="ai_bpmn_provider_error",
            message=f"The AI model could not complete the request: {exception}",
            status_code=502,
        ) from exception

    return make_response(jsonify(result.to_dict()), 200)
