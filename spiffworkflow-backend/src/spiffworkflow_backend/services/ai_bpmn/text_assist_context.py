"""Compact context for the script and markdown assistant.

Everything here is kept short on purpose: function signatures are one line each and variables
are names only, so the prompt stays small even for large process models.
"""

from __future__ import annotations

import ast
import builtins
import json
import logging
import re
from typing import Any

from lxml import etree  # type: ignore

from spiffworkflow_backend.scripts.script import Script
from spiffworkflow_backend.services.file_system_service import FileSystemService
from spiffworkflow_backend.services.jinja_service import JinjaHelpers
from spiffworkflow_backend.services.process_model_service import ProcessModelService

LOGGER = logging.getLogger(__name__)

MAX_VARIABLES = 60
MAX_DESCRIPTION_LENGTH = 90

# names injected by CustomBpmnScriptEngine; kept in sync manually because the engine builds them in __init__
SCRIPT_MODULES = [
    "calendar",
    "datetime",
    "dateparser",
    "decimal",
    "json",
    "pytz",
    "random",
    "re",
    "time",
    "timedelta",
    "uuid",
]
SCRIPT_BUILTINS = ["all", "any", "dict", "enumerate", "filter", "format", "list", "map", "max", "min", "set", "sum"]

SPIFF_NS = "http://spiffworkflow.org/bpmn/schema/1.0/core"
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
JINJA_VARIABLE_RE = re.compile(r"{{-?\s*([A-Za-z_][A-Za-z0-9_]*)")
JINJA_STATEMENT_VARIABLE_RE = re.compile(r"{%-?\s*(?:if|for\s+\w+\s+in|elif)\s+([A-Za-z_][A-Za-z0-9_]*)")
IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
PYTHON_KEYWORDS = {"and", "or", "not", "in", "is", "None", "True", "False", "if", "else", "for", "lambda"}

_spiff_function_cache: list[str] | None = None


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(sentence) > MAX_DESCRIPTION_LENGTH:
        sentence = sentence[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"
    return sentence


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def spiff_functions() -> list[str]:
    """One line per Spiff script function, e.g. `get_current_user(): Return the current user.`"""
    global _spiff_function_cache  # noqa: PLW0603
    if _spiff_function_cache is not None:
        return _spiff_function_cache
    lines: list[str] = []
    for subclass in Script.get_all_subclasses():
        try:
            description = subclass().get_description()
        except Exception:  # noqa: S112
            continue
        lines.append(f"{_snake_case(subclass.__name__)}(): {_first_sentence(str(description))}")
    _spiff_function_cache = sorted(set(lines))
    return _spiff_function_cache


def jinja_helpers() -> list[str]:
    return sorted(JinjaHelpers.get_helper_mapping().keys())


def _add(names: list[str], name: str) -> None:
    if name and name not in names and name not in PYTHON_KEYWORDS and len(names) < MAX_VARIABLES:
        names.append(name)


def _script_names(script: str) -> list[str]:
    """Names a script assigns, plus names it reads that are not modules, builtins or called functions."""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return []
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    local_only: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            local_only.update(sub.id for sub in ast.walk(node.target) if isinstance(sub, ast.Name))
        elif isinstance(node, ast.Lambda | ast.FunctionDef):
            local_only.update(arg.arg for arg in node.args.args)
    ignored = called | local_only | set(SCRIPT_MODULES) | set(SCRIPT_BUILTINS) | set(dir(builtins))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in ignored:
            names.append(node.id)
    return names


def _schema_properties(schema: Any, prefix: str = "") -> list[str]:
    names: list[str] = []
    if not isinstance(schema, dict):
        return names
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            names.append(f"{prefix}{key}")
            if isinstance(value, dict) and value.get("type") == "object":
                names.extend(_schema_properties(value, f"{prefix}{key}."))
    for dependency in (schema.get("dependencies") or {}).values():
        for option in dependency.get("oneOf", []) if isinstance(dependency, dict) else []:
            names.extend(_schema_properties(option, prefix))
    return names


def _variables_from_xml(bpmn_xml: str, exclude_element_id: str | None) -> tuple[list[str], list[str]]:
    """Return (variable names, referenced form schema file names)."""
    try:
        root = etree.fromstring(bpmn_xml.encode("utf-8"))  # noqa: S320
    except etree.XMLSyntaxError:
        return [], []
    names: list[str] = []
    schema_files: list[str] = []
    for element in root.iter():
        tag = element.tag if isinstance(element.tag, str) else ""
        owner_id = None
        parent = element.getparent()
        while parent is not None and owner_id is None:
            owner_id = parent.get("id")
            parent = parent.getparent()
        own_element = exclude_element_id is not None and owner_id == exclude_element_id
        local = etree.QName(tag).localname if tag else ""
        text = element.text or ""
        if local in ("script", "preScript", "postScript") and not own_element:
            names.extend(_script_names(text))
        elif local == "instructionsForEndUser":
            names.extend(JINJA_VARIABLE_RE.findall(text))
            names.extend(JINJA_STATEMENT_VARIABLE_RE.findall(text))
        elif local == "conditionExpression":
            names.extend(IDENTIFIER_RE.findall(text))
        elif local == "serviceTaskOperator" and element.get("resultVariable"):
            names.append(str(element.get("resultVariable")))
        elif local == "property" and element.get("name") == "formJsonSchemaFilename" and element.get("value"):
            schema_files.append(str(element.get("value")))
        if local in ("dataObject", "dataInput", "dataOutput") and element.get("id"):
            names.append(str(element.get("id")))
    return names, schema_files


def process_variables(
    process_model_identifier: str | None,
    bpmn_xml: str | None,
    exclude_element_id: str | None = None,
) -> list[str]:
    """Best-effort list of task data variable names known in the diagram and its forms."""
    names: list[str] = []
    schema_files: list[str] = []
    if bpmn_xml:
        xml_names, schema_files = _variables_from_xml(bpmn_xml, exclude_element_id)
        for name in xml_names:
            _add(names, name)
    if process_model_identifier:
        try:
            process_model = ProcessModelService.get_process_model(process_model_identifier.replace(":", "/"))
            files = FileSystemService.get_files(process_model, extension_filter="-schema.json")
            wanted = set(schema_files)
            for file in sorted(files, key=lambda f: (f.name not in wanted, f.name)):
                data = json.loads(FileSystemService.get_data(process_model, file.name))
                for name in _schema_properties(data):
                    _add(names, name)
        except Exception as exception:  # the context is a hint; never fail the request because of it
            LOGGER.debug("Could not read form schemas for %s: %s", process_model_identifier, exception)
    return names
