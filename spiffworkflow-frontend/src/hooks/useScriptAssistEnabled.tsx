import { useEffect, useState } from 'react';
import HttpService from '../services/HttpService';

type ScriptAssistEnabledResponse = {
  ok: boolean;
  provider?: string | null;
  model?: string | null;
};

/** Fetches whether the AI script/instructions assistant is enabled and which model it uses */
const useScriptAssistEnabled = () => {
  const [response, setResponse] = useState<ScriptAssistEnabledResponse | null>(
    null,
  );

  useEffect(() => {
    if (response === null) {
      HttpService.makeCallToBackend({
        path: `/script-assist/enabled`,
        successCallback: (result: ScriptAssistEnabledResponse) =>
          setResponse(result),
        failureCallback: () => setResponse({ ok: false }),
      });
    }
  }, [response]);

  const modelLabel =
    response?.provider && response?.model
      ? `${response.provider} · ${response.model}`
      : null;

  return {
    scriptAssistEnabled: response === null ? null : response.ok,
    scriptAssistModelLabel: modelLabel,
  };
};

export default useScriptAssistEnabled;
