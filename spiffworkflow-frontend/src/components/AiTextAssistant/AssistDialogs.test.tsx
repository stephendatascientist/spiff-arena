import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import MarkdownEditorDialog from '../../../packages/bpmn-js-spiffworkflow-react/src/components/MarkdownEditorDialog';
import ScriptEditorDialog from '../../../packages/bpmn-js-spiffworkflow-react/src/components/ScriptEditorDialog';

const scriptDialog = (props: Record<string, any> = {}) =>
  render(
    <ScriptEditorDialog
      open
      title="Edit script"
      tabValue={0}
      onTabChange={vi.fn()}
      scriptTabLabel="Script"
      unitTestsTabLabel="Unit Tests"
      renderScript={() => <div data-testid="script-editor" />}
      renderUnitTests={() => <div data-testid="unit-tests" />}
      closeLabel="Close"
      onClose={vi.fn()}
      {...props}
    />,
  );

describe('assistant side panel in editor dialogs', () => {
  it('toggles the assistant next to the script editor', () => {
    const onToggleAssist = vi.fn();
    const { rerender } = scriptDialog({
      renderAssistPanel: () => <div data-testid="assist" />,
      onToggleAssist,
      assistToggleLabel: 'AI Assist',
    });
    expect(screen.getByTestId('script-editor')).toBeInTheDocument();
    expect(screen.queryByTestId('assist')).not.toBeInTheDocument();
    expect(screen.getAllByRole('tab')).toHaveLength(2);

    fireEvent.click(screen.getByTestId('assist-toggle'));
    expect(onToggleAssist).toHaveBeenCalled();

    rerender(
      <ScriptEditorDialog
        open
        title="Edit script"
        tabValue={0}
        onTabChange={vi.fn()}
        scriptTabLabel="Script"
        unitTestsTabLabel="Unit Tests"
        renderScript={() => <div data-testid="script-editor" />}
        closeLabel="Close"
        onClose={vi.fn()}
        renderAssistPanel={() => <div data-testid="assist" />}
        assistPanelOpen
        onToggleAssist={onToggleAssist}
        assistToggleLabel="AI Assist"
      />,
    );
    expect(screen.getByTestId('assist')).toBeInTheDocument();
    expect(screen.getByTestId('script-editor')).toBeInTheDocument();
    expect(screen.getByTestId('assist-toggle')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('keeps the legacy assist tab when no side panel is given', () => {
    scriptDialog({
      assistEnabled: true,
      assistTabLabel: 'Script Assist',
      renderAssist: () => <div data-testid="legacy-assist" />,
    });
    expect(screen.getAllByRole('tab')).toHaveLength(3);
    expect(screen.queryByTestId('assist-toggle')).not.toBeInTheDocument();
  });

  it('shows the assistant next to the markdown editor', () => {
    render(
      <MarkdownEditorDialog
        open
        onClose={vi.fn()}
        title="Edit Markdown"
        closeLabel="Close"
        renderEditor={() => <div data-testid="markdown-editor" />}
        renderAssistPanel={() => <div data-testid="assist" />}
        assistPanelOpen
        onToggleAssist={vi.fn()}
        assistToggleLabel="AI Assist"
      />,
    );
    expect(screen.getByTestId('markdown-editor')).toBeInTheDocument();
    expect(screen.getByTestId('assist')).toBeInTheDocument();
    expect(screen.getByTestId('assist-toggle')).toBeInTheDocument();
  });
});
