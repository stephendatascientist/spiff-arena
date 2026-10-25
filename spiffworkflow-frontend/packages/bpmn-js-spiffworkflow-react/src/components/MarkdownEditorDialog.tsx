import React, { ReactNode } from 'react';
import { Box, Button, Dialog, Stack } from '@mui/material';
import { AssistLayout, AssistToggleButton } from './AssistLayout';

type MarkdownEditorDialogProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  closeLabel: string;
  renderEditor: () => ReactNode;
  /** Side panel shown next to the editor, e.g. an AI assistant. */
  renderAssistPanel?: () => ReactNode;
  assistPanelOpen?: boolean;
  onToggleAssist?: () => void;
  assistToggleLabel?: string;
  assistToggleTooltip?: string;
  assistToggleIcon?: ReactNode;
};

export default function MarkdownEditorDialog({
  open,
  onClose,
  title,
  closeLabel,
  renderEditor,
  renderAssistPanel,
  assistPanelOpen = false,
  onToggleAssist,
  assistToggleLabel,
  assistToggleTooltip,
  assistToggleIcon,
}: MarkdownEditorDialogProps) {
  if (!open) {
    return null;
  }

  return (
    <Dialog
      className="bpmn-editor-wide-dialog"
      open={open}
      onClose={onClose}
      aria-labelledby="modal-modal-title"
      aria-describedby="modal-modal-description"
    >
      <Box sx={{ p: 4 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <h2 id="modal-modal-title" style={{ flex: 1 }}>
            {title}
          </h2>
          {renderAssistPanel ? (
            <AssistToggleButton
              label={assistToggleLabel}
              tooltip={assistToggleTooltip}
              open={assistPanelOpen}
              onToggle={onToggleAssist}
              icon={assistToggleIcon}
            />
          ) : null}
        </Stack>
        <AssistLayout
          assistPanelOpen={assistPanelOpen}
          renderAssistPanel={renderAssistPanel}
        >
          <div data-color-mode="light">{renderEditor()}</div>
        </AssistLayout>
        <Button onClick={onClose}>{closeLabel}</Button>
      </Box>
    </Dialog>
  );
}
