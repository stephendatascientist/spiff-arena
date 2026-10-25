import { useEffect, useRef, useState } from 'react';
import { Alert, Box } from '@mui/material';
import NavigatedViewer from 'bpmn-js/lib/NavigatedViewer';
import 'bpmn-js/dist/assets/diagram-js.css';
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn-embedded.css';

type BpmnPreviewProps = {
  bpmnXml: string;
  height?: number | string;
  errorMessage: string;
};

/** Read-only, pannable/zoomable rendering of a BPMN diagram. */
export default function BpmnPreview({
  bpmnXml,
  height = 260,
  errorMessage,
}: BpmnPreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }
    const viewer = new NavigatedViewer({ container: containerRef.current });
    let cancelled = false;
    viewer
      .importXML(bpmnXml)
      .then(() => {
        if (!cancelled) {
          (viewer.get('canvas') as any).zoom('fit-viewport', 'auto');
          setFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
      viewer.destroy();
    };
  }, [bpmnXml]);

  return (
    <Box
      sx={{
        position: 'relative',
        height,
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        backgroundColor: '#fff',
        overflow: 'hidden',
      }}
    >
      {failed ? <Alert severity="error">{errorMessage}</Alert> : null}
      <Box
        ref={containerRef}
        data-testid="ai-bpmn-preview"
        sx={{ width: '100%', height: '100%' }}
      />
    </Box>
  );
}
