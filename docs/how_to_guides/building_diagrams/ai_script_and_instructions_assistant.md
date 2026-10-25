# Write Scripts and Instructions with AI Assist

The **AI Assist** panel drafts Python scripts and end-user instructions from a plain-language request.
It appears in the **Editing Script** dialog (script tasks, pre-scripts and post-scripts) and in the **Edit Markdown** dialog (instructions for end users).
It uses the same model configuration as the [AI Process Assistant](ai_process_assistant.md): a local [Ollama](https://ollama.com) model by default, or OpenAI, Anthropic Claude, Google Gemini and other LangChain chat models.

## Use the assistant

1. In the diagram editor, open a script or the instructions of a task from the properties panel.
2. Click **AI Assist** in the top right of the dialog. The assistant opens next to the editor.
   The button only appears when the assistant is enabled.
3. Describe what you want, for example:
   - Script: *"Calculate the order total from the line items, applying a 10% discount when it is over 1000."*
   - Instructions: *"Show a table of the line items with quantity and price."*
4. The assistant replies with a short explanation and shows its suggestion as a diff against the current text.
   - Click **Apply to script** or **Apply to instructions** to put the suggestion into the editor.
   - Click **Discard** to ignore it.
   - Or type a follow-up such as *"round the total to two decimals"*. The assistant refines the suggestion you are looking at.
5. Use **Undo** to restore the text as it was before the suggestion was applied.
   Close the dialog as usual to keep the changes, then save the diagram.

The assistant can write new text or change the existing text, for example to fix a bug, add error handling, or rename a variable.

### What the assistant knows

With each request the browser sends:

- the current script or instructions,
- the task's name and type, and which script you are editing (script, pre-script or post-script),
- the script's [unit tests](bpmn_unit_tests.md),
- the current diagram, from which the backend collects the names of task data variables.
  They come from other scripts, form schemas, instructions, gateway conditions, service task results and data objects.

The backend adds the list of Spiff functions available in scripts (for example `get_current_user`) and the modules scripts may use.
It does not send task data from running process instances.

### How suggestions are checked

Scripts are compiled with the same restrictions the script engine uses (RestrictedPython, unless `SPIFFWORKFLOW_BACKEND_USE_RESTRICTED_SCRIPT_ENGINE=false`).
If the script task has unit tests, the backend runs up to five of them against the suggestion.
When compilation or a test fails, the model is asked to fix the script; the panel shows how many tests passed.
Unit tests only run for users who are allowed to run script unit tests for the process model; otherwise the panel shows a warning.

Instructions are checked for valid Jinja syntax, and the assistant warns about variables that do not appear anywhere in the process.

## Enable the assistant

Set these environment variables on the backend and restart it:

```bash
export SPIFFWORKFLOW_BACKEND_SCRIPT_ASSIST_ENABLED=true
# model settings shared with the AI Process Assistant
export SPIFFWORKFLOW_BACKEND_AI_BPMN_BASE_URL=http://localhost:11434
export SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL=gemma4:12b-mlx
```

All `SPIFFWORKFLOW_BACKEND_AI_BPMN_*` settings (provider, model, base URL, API key, temperature, timeout, repair attempts) apply to this assistant too.
See [Enable the assistant](ai_process_assistant.md#enable-the-assistant) for the full list and for using other providers.
`SPIFFWORKFLOW_BACKEND_SCRIPT_ASSIST_ENABLED` and `SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED` are independent, so you can enable either assistant on its own.

Earlier versions used OpenAI only, configured with `SPIFFWORKFLOW_BACKEND_SECRET_KEY_OPENAI_API`.
That setting still works: when it is set and `SPIFFWORKFLOW_BACKEND_AI_BPMN_PROVIDER` is not, the assistant uses OpenAI with `gpt-4o-mini` (or `SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL`).
This requires the `langchain-openai` package, which is part of the backend's `ai` extra.

Users need permission to `create` on `/script-assist/process-message`; this is part of the basic permissions every user receives.

## Limitations

- Always review a suggestion before applying it. The model may assume variable names that do not match your data.
- The conversation is kept only in the browser and is cleared when you close the panel or the dialog, or start a new conversation.
- Requests are limited to 4,000 characters and the current text to 50,000 characters.
