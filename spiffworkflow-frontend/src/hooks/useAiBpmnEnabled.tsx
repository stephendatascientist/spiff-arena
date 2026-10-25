import { useEffect, useState } from 'react';
import HttpService from '../services/HttpService';

export type AiBpmnStatus = {
  ok: boolean;
  provider?: string | null;
  model?: string | null;
};

/** Fetches whether the backend AI BPMN assistant is configured. */
const useAiBpmnEnabled = (skip = false) => {
  const [aiBpmnStatus, setAiBpmnStatus] = useState<AiBpmnStatus | null>(null);

  useEffect(() => {
    if (skip || aiBpmnStatus !== null) {
      return;
    }
    HttpService.makeCallToBackend({
      path: `/ai-bpmn/enabled`,
      successCallback: (response: AiBpmnStatus) => setAiBpmnStatus(response),
      failureCallback: () => setAiBpmnStatus({ ok: false }),
      onUnauthorized: () => setAiBpmnStatus({ ok: false }),
    });
  }, [skip, aiBpmnStatus]);

  return {
    aiBpmnEnabled: !!aiBpmnStatus?.ok,
    aiBpmnStatus,
  };
};

export default useAiBpmnEnabled;
