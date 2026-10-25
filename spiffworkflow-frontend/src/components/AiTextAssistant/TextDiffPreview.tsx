import { useMemo } from 'react';
import { Box } from '@mui/material';
import { unifiedMergeView } from '@codemirror/merge';
import { python } from '@codemirror/lang-python';
import { EditorState } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import ThemedCodeMirror from '../ThemedCodeMirror';

type TextDiffPreviewProps = {
  original: string;
  modified: string;
  language: 'python' | 'markdown';
  ariaLabel: string;
  maxHeight?: string;
};

/** Read-only inline diff of a suggestion against the text it would replace. */
export default function TextDiffPreview({
  original,
  modified,
  language,
  ariaLabel,
  maxHeight = '260px',
}: TextDiffPreviewProps) {
  const extensions = useMemo(
    () => [
      ...(language === 'python' ? [python()] : [EditorView.lineWrapping]),
      unifiedMergeView({ original, mergeControls: false }),
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
    ],
    [original, language],
  );
  return (
    <Box
      data-testid="ai-text-diff"
      sx={{ border: 1, borderColor: 'divider', borderRadius: 1 }}
    >
      <ThemedCodeMirror
        value={modified}
        extensions={extensions}
        maxHeight={maxHeight}
        height="auto"
        editable={false}
        basicSetup={{ foldGutter: false, highlightActiveLine: false }}
        ariaLabel={ariaLabel}
      />
    </Box>
  );
}
