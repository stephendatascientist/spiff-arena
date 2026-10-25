# Generate and Edit Diagrams with the AI Process Assistant

The AI Process Assistant draws a BPMN diagram from a plain-language description and updates it when you ask for changes.
It runs in the process model diagram editor and uses a local [Ollama](https://ollama.com) model by default.
OpenAI, Anthropic Claude, Google Gemini and other LangChain chat models can be used instead.

## Use the assistant

1. Open a BPMN file of a process model in the diagram editor.
2. Click **AI Assistant** in the toolbar. The button only appears when the assistant is enabled and you are allowed to edit the file.
3. Describe the process, for example:
   *"Invoice processing: receive the invoice, check it against the purchase order, and route invoices over 10000 to the finance director for approval before payment."*
4. The assistant replies with a summary, the list of changes, and a preview of the suggested diagram.
   - Click **Apply to diagram** to load the suggestion into the editor.
   - Click **Discard** to ignore it.
   - Or type a follow-up such as *"add a step where procurement checks the vendor before payment"*. The assistant refines the suggestion you are looking at.
5. After applying, review the diagram and click **Save**. Use **Undo** in the assistant panel to return to the diagram as it was before the suggestion was applied.

The assistant also edits existing diagrams: it can add, insert, rename, retype, remove and reconnect elements.
Elements it does not change keep their ids, positions and configuration (forms, scripts, connectors and other properties).
New elements are laid out automatically next to the existing ones.

### What the assistant creates

The assistant works with start and end events, tasks (none, user, manual, script, service and call activities), and exclusive, parallel and inclusive gateways.
Other elements already in the diagram (for example timers, messages, or subprocesses) are shown to the model so it can connect to them, but it does not create them.

The assistant only draws the process. After applying a suggestion, finish the model in the properties panel:

- add forms to user tasks ([Use User Tasks and Forms](use_user_tasks_and_forms.md)),
- write the code for script tasks (the [AI Assist](ai_script_and_instructions_assistant.md) panel in the script editor can draft it),
- choose a connector operator for service tasks (SpiffWorkflow cannot run a service task without one, so the assistant shows a reminder),
- choose the called process for call activities,
- check the conditions on gateway flows. The assistant writes Python conditions such as `amount > 5000` based on the variable names it assumes; adjust them to match your forms and data.

## Enable the assistant

The assistant is turned off by default.
Configure it with environment variables on the backend and restart it:

| Variable | Default | Description |
| --- | --- | --- |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED` | `false` | Set to `true` to show the assistant in the editor. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_PROVIDER` | `ollama` | `ollama`, `openai`, `anthropic` (or `claude`), `google_genai` (or `gemini`), `mistralai`, `groq`, or `some.module:ChatModelClass` for any other LangChain chat model. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL` | `gemma4:12b-mlx` | Model name, for example `llama3.1:8b`, `gpt-4o-mini`, `claude-sonnet-4-5`, or `gemini-2.5-flash`. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_BASE_URL` | | Server URL, for example `http://localhost:11434` for Ollama or an OpenAI-compatible endpoint. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_API_KEY` | | API key for hosted providers. Without it, the provider's own environment variable is used (for example `OPENAI_API_KEY`). |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_TEMPERATURE` | `0.2` | Sampling temperature. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_TIMEOUT_SECONDS` | `180` | Request timeout for the model. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_MAX_REPAIR_ATTEMPTS` | `2` | How many times the model is asked to fix a diagram that fails validation. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_MAX_HISTORY_MESSAGES` | `6` | How many earlier chat messages are sent with a follow-up request. |
| `SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL_KWARGS` | `{}` | JSON object of extra arguments for the chat model class, for example `{"num_ctx": 8192}`. |

Ollama models run with thinking turned off (`{"reasoning": false}`), because thinking makes requests many times slower without improving the edits.
To turn it back on, set `SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL_KWARGS='{"reasoning": true}'`.

Users need permission to `create` on `/ai-bpmn/generate`; this is part of the basic permissions every user receives.

### Ollama (default)

```bash
ollama pull gemma4:12b-mlx
export SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED=true
export SPIFFWORKFLOW_BACKEND_AI_BPMN_BASE_URL=http://localhost:11434
```

When the backend runs in Docker and Ollama runs on the host, use `http://host.docker.internal:11434`.
Any Ollama chat model works; models with 8B or more parameters give noticeably better diagrams.

### Other providers

The backend image includes the `ai` extra with the Ollama integration.
For other providers, install the matching LangChain package in the backend environment, then set the provider, model and key:

```bash
uv pip install langchain-anthropic   # or langchain-openai, langchain-google-genai, langchain-mistralai, langchain-groq
export SPIFFWORKFLOW_BACKEND_AI_BPMN_ENABLED=true
export SPIFFWORKFLOW_BACKEND_AI_BPMN_PROVIDER=claude
export SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL=claude-sonnet-4-5
export SPIFFWORKFLOW_BACKEND_AI_BPMN_API_KEY=...
```

When running from source, install the base integrations with `uv sync --extra ai`.

## How it works

The assistant never asks the model to write BPMN XML, which keeps requests small and fast.
Instead:

1. The backend turns the current diagram into a short outline with an id per element, for example:

   ```text
   n1 start "Request received"
   n2 user "Review request"
   n3 xor "Approved?"
   n1 -> n2
   n2 -> n3
   ```

2. The model replies with a message and a list of short edit operations such as `insert t1 user "Check vendor" between n3 n4` or `n3 -> t2 "no" if approved == False`.
3. The backend applies the operations and validates the result (start and end events, connected elements, gateway conditions, reachability).
   If there are problems, a [LangGraph](https://langchain-ai.github.io/langgraph/) loop sends them back to the model for a fix.
4. The backend merges the result into the original XML, lays out new elements, checks that SpiffWorkflow can parse the diagram, and returns it to the editor as a suggestion.

A typical request uses about 1,000–2,500 input tokens and 100–300 output tokens, whatever the size of the XML.

## Limitations

- Suggestions are not saved until you apply them and click **Save**.
- The conversation is kept only in the browser and is cleared when you leave the page or start a new conversation.
- Lanes, pools and expanded subprocesses are kept, but new elements are not assigned to lanes.
- Smaller local models sometimes need a follow-up message to get the structure right. Read the change list before applying.
