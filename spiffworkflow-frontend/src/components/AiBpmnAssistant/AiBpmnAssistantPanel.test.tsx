import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AiBpmnAssistantPanel from './AiBpmnAssistantPanel';

const { makeCallToBackend } = vi.hoisted(() => ({
  makeCallToBackend: vi.fn(),
}));

vi.mock('../../services/HttpService', () => ({
  default: { makeCallToBackend },
}));

vi.mock('./BpmnPreview', () => ({
  default: ({ bpmnXml }: { bpmnXml: string }) => (
    <div data-testid="bpmn-preview">{bpmnXml}</div>
  ),
}));

const CURRENT_XML = '<definitions>current</definitions>';

const respondWith = (response: Record<string, any>) => {
  const call = makeCallToBackend.mock.calls.at(-1)?.[0];
  act(() => {
    call.successCallback({
      message: 'Done.',
      changes: [],
      warnings: [],
      usage: { input_tokens: 10, output_tokens: 5 },
      ...response,
    });
  });
};

const renderPanel = () => {
  const onApply = vi.fn();
  const getCurrentXml = vi.fn().mockResolvedValue(CURRENT_XML);
  render(
    <AiBpmnAssistantPanel
      open
      onClose={vi.fn()}
      getCurrentXml={getCurrentXml}
      onApply={onApply}
      modelLabel="ollama · test"
    />,
  );
  return { onApply, getCurrentXml };
};

const sendPrompt = async (prompt: string) => {
  fireEvent.change(screen.getByTestId('ai-bpmn-input'), {
    target: { value: prompt },
  });
  await act(async () => {
    fireEvent.click(screen.getByTestId('ai-bpmn-send'));
  });
};

describe('AiBpmnAssistantPanel', () => {
  beforeEach(() => {
    makeCallToBackend.mockReset();
  });

  it('sends the prompt with the current diagram and applies the suggestion', async () => {
    const { onApply } = renderPanel();
    await sendPrompt('leave request process');

    expect(makeCallToBackend).toHaveBeenCalledTimes(1);
    const call = makeCallToBackend.mock.calls[0][0];
    expect(call.path).toBe('/ai-bpmn/generate');
    expect(call.httpMethod).toBe('POST');
    expect(call.postBody).toEqual({
      prompt: 'leave request process',
      bpmn_xml: CURRENT_XML,
      history: [],
    });
    expect(screen.getByTestId('ai-bpmn-loading')).toBeInTheDocument();

    respondWith({ bpmn_xml: '<new/>', changes: ['Added user task "A"'] });
    expect(screen.getByTestId('bpmn-preview')).toHaveTextContent('<new/>');

    await act(async () => {
      fireEvent.click(screen.getByTestId('ai-bpmn-accept'));
    });
    expect(onApply).toHaveBeenCalledWith('<new/>');
    expect(screen.getByTestId('ai-bpmn-status-accepted')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('ai-bpmn-revert'));
    expect(onApply).toHaveBeenLastCalledWith(CURRENT_XML);
    expect(screen.getByTestId('ai-bpmn-status-reverted')).toBeInTheDocument();
  });

  it('refines a pending suggestion and sends the conversation history', async () => {
    const { onApply, getCurrentXml } = renderPanel();
    await sendPrompt('first');
    respondWith({ bpmn_xml: '<first/>', changes: ['Added task "A"'] });

    await sendPrompt('second');
    const call = makeCallToBackend.mock.calls[1][0];
    expect(call.postBody.bpmn_xml).toBe('<first/>');
    expect(call.postBody.history).toEqual([
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'Done.' },
    ]);
    expect(getCurrentXml).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('ai-bpmn-status-superseded')).toBeInTheDocument();

    respondWith({ bpmn_xml: '<second/>', changes: ['Added task "B"'] });
    fireEvent.click(screen.getByTestId('ai-bpmn-discard'));
    expect(screen.getByTestId('ai-bpmn-status-discarded')).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it('shows errors from the backend', async () => {
    renderPanel();
    await sendPrompt('something');
    const call = makeCallToBackend.mock.calls[0][0];
    act(() => {
      call.failureCallback({ message: 'model not found' });
    });
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByTestId('ai-bpmn-loading')).not.toBeInTheDocument();
  });
});
