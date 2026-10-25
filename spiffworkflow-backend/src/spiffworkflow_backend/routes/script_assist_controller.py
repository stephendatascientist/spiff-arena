import dataclasses
import importlib
import json
import os
from typing import Any

from flask import current_app
from flask import g
from flask import jsonify
from flask import make_response
from flask.wrappers import Response

from spiffworkflow_backend.exceptions.api_error import ApiError
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnConfigurationError
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from spiffworkflow_backend.services.ai_bpmn.llm_provider import create_chat_model
from spiffworkflow_backend.services.authorization_service import AuthorizationService
from spiffworkflow_backend.services.script_unit_test_runner import ScriptUnitTestRunner

MAX_PROMPT_LENGTH = 4000
MAX_CURRENT_CONTENT_LENGTH = 50000
LEGACY_OPENAI_MODEL = "gpt-4o-mini"
KINDS = ("script", "markdown")


def _is_enabled() -> bool:
    return bool(current_app.config.get("SPIFFWORKFLOW_BACKEND_SCRIPT_ASSIST_ENABLED"))


def _settings() -> AiBpmnSettings:
    """Shared AI_BPMN_* settings, falling back to the legacy OpenAI key when no provider is configured."""
    settings = AiBpmnSettings.from_app_config()
    config = current_app.config
    legacy_key = config.get("SPIFFWORKFLOW_BACKEND_SECRET_KEY_OPENAI_API")
    if not legacy_key:
        return settings
    if not config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_PROVIDER"):
        model = config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL") or LEGACY_OPENAI_MODEL
        return dataclasses.replace(settings, provider="openai", model=str(model), api_key=legacy_key, model_kwargs={})
    if settings.provider.lower() == "openai" and not settings.api_key:
        return dataclasses.replace(settings, api_key=legacy_key)
    return settings


def enabled() -> Response:
    if not _is_enabled():
        return make_response(jsonify({"ok": False, "provider": None, "model": None}), 200)
    try:
        settings = _settings()
    except AiBpmnConfigurationError:
        return make_response(jsonify({"ok": True, "provider": None, "model": None}), 200)
    return make_response(jsonify({"ok": True, "provider": settings.provider, "model": settings.model}), 200)


def _agent_module() -> Any:
    # imported lazily so the backend runs without the optional "ai" extra installed
    try:
        return importlib.import_module("spiffworkflow_backend.services.ai_bpmn.text_assist_agent")
    except ImportError as exception:
        raise AiBpmnConfigurationError(
            "The AI script assistant needs the backend 'ai' extra. Install it with `uv sync --extra ai`."
        ) from exception


def _build_agent(settings: AiBpmnSettings, unit_test_runner: Any) -> Any:
    return _agent_module().TextAssistAgent(create_chat_model(settings), settings, unit_test_runner)


def _run_unit_test(script: str, input_context: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str | None]:
    result = ScriptUnitTestRunner.run_with_script_and_pre_post_contexts(script, input_context, expected)
    if result.result:
        return True, None
    if result.error:
        line = f" (line {result.line_number})" if result.line_number else ""
        return False, f"{result.error}{line}"
    actual = json.dumps(result.context, default=str, separators=(",", ":"))
    wanted = json.dumps(expected, default=str, separators=(",", ":"))
    return False, f"expected task data {wanted} but the script produced {actual}"


def _user_can(permission: str, target_uri: str) -> bool:
    user = getattr(g, "user", None)
    if user is None:
        return False
    try:
        return AuthorizationService.user_has_permission(user=user, permission=permission, target_uri=target_uri)
    except Exception:
        return False


def _validate_body(body: dict) -> tuple[str, str, str]:
    if not _is_enabled():
        raise ApiError(
            error_code="script_assist_disabled",
            message="The AI script assistant is not enabled. Set SPIFFWORKFLOW_BACKEND_SCRIPT_ASSIST_ENABLED=true.",
            status_code=400,
        )
    kind = str(body.get("kind") or "script")
    if kind not in KINDS:
        raise ApiError(
            error_code="script_assist_invalid_kind", message=f"kind must be one of {', '.join(KINDS)}", status_code=400
        )
    prompt = str(body.get("prompt") or body.get("query") or "").strip()
    if not prompt:
        raise ApiError(error_code="no_openai_query_provided", message="Please describe what you want.", status_code=400)
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ApiError(
            error_code="script_assist_prompt_too_long",
            message=f"Please keep the request under {MAX_PROMPT_LENGTH} characters.",
            status_code=400,
        )
    current_content = str(body.get("current_content") or "")
    if len(current_content) > MAX_CURRENT_CONTENT_LENGTH:
        raise ApiError(
            error_code="script_assist_content_too_long",
            message="The current content is too long for the assistant.",
            status_code=400,
        )
    return kind, prompt, current_content


def process_message(body: dict) -> Response:
    kind, prompt, current_content = _validate_body(body)
    raw_element = body.get("element")
    element: dict[str, Any] = raw_element if isinstance(raw_element, dict) else {}
    unit_tests = [item for item in (body.get("unit_tests") or []) if isinstance(item, dict)] if kind == "script" else []
    history = [item for item in (body.get("history") or []) if isinstance(item, dict)]
    process_model_identifier = str(body.get("process_model_identifier") or "").replace("/", ":") or None

    # unit tests execute code and form schemas are process model files, so honor the same permissions as those APIs
    unit_test_runner = None
    warnings: list[str] = []
    if unit_tests:
        if process_model_identifier and _user_can("create", f"/process-models/{process_model_identifier}/script-unit-tests/run"):
            unit_test_runner = _run_unit_test
        else:
            warnings.append("Unit tests were not run because you do not have permission to run them for this process model.")
    can_read_files = bool(process_model_identifier) and _user_can("read", f"/process-models/{process_model_identifier}/files")

    try:
        settings = _settings()
        agent = _build_agent(settings, unit_test_runner)
        context = importlib.import_module("spiffworkflow_backend.services.ai_bpmn.text_assist_context")
        request = _agent_module().TextAssistRequest(
            kind=kind,
            prompt=prompt,
            current_content=current_content,
            element_name=element.get("name"),
            element_type=element.get("type"),
            script_type=element.get("script_type"),
            unit_tests=unit_tests,
            variables=context.process_variables(
                process_model_identifier if can_read_files else None,
                body.get("bpmn_xml"),
                element.get("id") if kind == "script" else None,
            ),
            spiff_functions=context.spiff_functions() if kind == "script" else [],
            modules=context.SCRIPT_MODULES,
            builtins=context.SCRIPT_BUILTINS,
            jinja_helpers=context.jinja_helpers() if kind == "markdown" else [],
            history=history,
            restricted=os.environ.get("SPIFFWORKFLOW_BACKEND_USE_RESTRICTED_SCRIPT_ENGINE") != "false",
        )
        result = agent.run(request)
    except AiBpmnConfigurationError as exception:
        raise ApiError(error_code="script_assist_configuration_error", message=str(exception), status_code=500) from exception
    except ApiError:
        raise
    except Exception as exception:
        current_app.logger.exception("AI script assist failed")
        raise ApiError(
            error_code="script_assist_provider_error",
            message=f"The AI model could not complete the request: {exception}",
            status_code=502,
        ) from exception

    result.warnings = warnings + result.warnings
    return make_response(jsonify(result.to_dict()), 200)
