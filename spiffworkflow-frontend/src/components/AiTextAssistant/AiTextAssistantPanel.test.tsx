import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AiTextAssistantPanel from './AiTextAssistantPanel';

const { makeCallToBackend } = vi.hoisted(() => ({
  makeCallToBackend: vi.fn(),
}));

vi.mock('../../services/HttpService', () => ({
  default: { makeCallToBackend },
}));

vi.mock('./TextDiffPreview', () => ({
  default: ({ original, modified }: { original: string; modified: string }) => (
    <div data-testid="ai-text-diff">
      <span data-testid="diff-original">{original}</span>
      <span data-testid="diff-modified">{modified}</span>
    </div>
  ),
}));

const CONTEXT = {
  element: { id: 'calc', name: 'Calculate', type: 'bpmn:ScriptTask' },
  process_model_identifier: 'group:model',
  bpmn_xml: '<definitions/>',
};

const respondWith = (response: Record<string, any>) => {
  const call = makeCallToBackend.mock.calls.at(-1)?.[0];
  act(() => {
    call.successCallback({
      message: 'Done.',
      warnings: [],
      usage: { input_tokens: 10, output_tokens: 5 },
      ...response,
    });
  });
};

const renderPanel = (kind: 'script' | 'markdown' = 'script') => {
  let content = 'x = 1';
  const onApply = vi.fn((value: string) => {
    content = value;
  });
  const getContext = vi.fn().mockResolvedValue(CONTEXT);
  render(
    <AiTextAssistantPanel
      kind={kind}
      getContent={() => content}
      onApply={onApply}
      getContext={getContext}
      modelLabel="ollama · test"
    />,
  );
  return { onApply, getContext };
};

const sendPrompt = async (prompt: string) => {
  fireEvent.change(screen.getByTestId('ai-text-input'), {
    target: { value: prompt },
  });
  await act(async () => {
    fireEvent.click(screen.getByTestId('ai-text-send'));
  });
};

describe('AiTextAssistantPanel', () => {
  beforeEach(() => {
    makeCallToBackend.mockReset();
  });

  it('sends the request with context, previews a diff and applies it', async () => {
    const { onApply } = renderPanel();
    expect(screen.getByText('ollama · test')).toBeInTheDocument();
    await sendPrompt('double x');

    const call = makeCallToBackend.mock.calls[0][0];
    expect(call.path).toBe('/script-assist/process-message');
    expect(call.httpMethod).toBe('POST');
    expect(call.postBody).toEqual({
      kind: 'script',
      prompt: 'double x',
      current_content: 'x = 1',
      history: [],
      ...CONTEXT,
    });
    expect(screen.getByTestId('ai-text-loading')).toBeInTheDocument();

    respondWith({ content: 'x = 2', tests: { passed: 1, total: 1 } });
    expect(screen.getByTestId('diff-original')).toHaveTextContent('x = 1');
    expect(screen.getByTestId('diff-modified')).toHaveTextContent('x = 2');
    expect(screen.getByTestId('ai-text-tests')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('ai-text-accept'));
    expect(onApply).toHaveBeenCalledWith('x = 2');
    expect(screen.getByTestId('ai-text-status-accepted')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('ai-text-revert'));
    expect(onApply).toHaveBeenLastCalledWith('x = 1');
    expect(screen.getByTestId('ai-text-status-reverted')).toBeInTheDocument();
  });

  it('refines the pending suggestion and can discard it', async () => {
    const { onApply } = renderPanel('markdown');
    await sendPrompt('greet');
    respondWith({ content: 'Hello', warnings: ['Unknown variable: name'] });
    expect(screen.getByTestId('ai-text-warnings')).toHaveTextContent(
      'Unknown variable: name',
    );

    await sendPrompt('make it bold');
    const call = makeCallToBackend.mock.calls[1][0];
    expect(call.postBody.kind).toBe('markdown');
    expect(call.postBody.current_content).toBe('Hello');
    expect(call.postBody.history).toEqual([
      { role: 'user', content: 'greet' },
      { role: 'assistant', content: 'Done.' },
    ]);
    expect(screen.getByTestId('ai-text-status-superseded')).toBeInTheDocument();

    respondWith({ content: '**Hello**' });
    fireEvent.click(screen.getByTestId('ai-text-discard'));
    expect(screen.getByTestId('ai-text-status-discarded')).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it('reports unchanged content and backend errors', async () => {
    renderPanel();
    await sendPrompt('nothing');
    respondWith({ content: 'x = 1', message: 'Already correct.' });
    expect(screen.queryByTestId('ai-text-diff')).not.toBeInTheDocument();
    expect(screen.getByText('ai_text_no_changes')).toBeInTheDocument();

    await sendPrompt('fail');
    const call = makeCallToBackend.mock.calls.at(-1)?.[0];
    act(() => {
      call.failureCallback({ message: 'model not found' });
    });
    expect(screen.getByTestId('ai-text-error')).toBeInTheDocument();
    expect(screen.queryByTestId('ai-text-loading')).not.toBeInTheDocument();
  });
});
