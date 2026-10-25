import { KeyboardEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  Drawer,
  IconButton,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import CloseIcon from '@mui/icons-material/Close';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import SendIcon from '@mui/icons-material/Send';
import HttpService from '../../services/HttpService';
import BpmnPreview from './BpmnPreview';

const MAX_HISTORY_ENTRIES = 6;
const DRAWER_WIDTH = 460;

type ProposalStatus =
  | 'pending'
  | 'accepted'
  | 'discarded'
  | 'superseded'
  | 'reverted';

type Proposal = {
  bpmnXml: string;
  changes: string[];
  warnings: string[];
  status: ProposalStatus;
  previousXml?: string | null;
};

export type AiBpmnChatEntry = {
  id: number;
  role: 'user' | 'assistant' | 'error';
  content: string;
  proposal?: Proposal;
  usage?: { input_tokens?: number; output_tokens?: number };
};

type AiBpmnGenerateResponse = {
  bpmn_xml: string;
  message: string;
  changes: string[];
  warnings: string[];
  usage?: { input_tokens?: number; output_tokens?: number };
};

type AiBpmnAssistantPanelProps = {
  open: boolean;
  onClose: () => void;
  getCurrentXml: () => Promise<string | null>;
  onApply: (bpmnXml: string) => void;
  modelLabel?: string | null;
};

export default function AiBpmnAssistantPanel({
  open,
  onClose,
  getCurrentXml,
  onApply,
  modelLabel,
}: AiBpmnAssistantPanelProps) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<AiBpmnChatEntry[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [enlargedXml, setEnlargedXml] = useState<string | null>(null);
  const nextId = useRef(1);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [entries, loading]);

  const addEntry = (entry: Omit<AiBpmnChatEntry, 'id'>) => {
    const id = nextId.current;
    nextId.current += 1;
    setEntries((previous) => [...previous, { ...entry, id }]);
  };

  const updateProposal = (id: number, update: Partial<Proposal>) => {
    setEntries((previous) =>
      previous.map((entry) =>
        entry.id === id && entry.proposal
          ? { ...entry, proposal: { ...entry.proposal, ...update } }
          : entry,
      ),
    );
  };

  const send = async (text: string) => {
    const prompt = text.trim();
    if (!prompt || loading) {
      return;
    }
    const pending = [...entries]
      .reverse()
      .find((entry) => entry.proposal?.status === 'pending');
    // refinements build on the suggestion the user is currently looking at
    const baseXml = pending?.proposal
      ? pending.proposal.bpmnXml
      : await getCurrentXml();
    if (pending) {
      updateProposal(pending.id, { status: 'superseded' });
    }
    const history = entries
      .filter((entry) => entry.role !== 'error')
      .slice(-MAX_HISTORY_ENTRIES)
      .map((entry) => ({ role: entry.role, content: entry.content }));

    addEntry({ role: 'user', content: prompt });
    setInput('');
    setLoading(true);
    HttpService.makeCallToBackend({
      path: '/ai-bpmn/generate',
      httpMethod: 'POST',
      postBody: { prompt, bpmn_xml: baseXml, history },
      successCallback: (result: AiBpmnGenerateResponse) => {
        setLoading(false);
        const hasChanges = result.changes && result.changes.length > 0;
        addEntry({
          role: 'assistant',
          content: result.message,
          usage: result.usage,
          proposal: hasChanges
            ? {
                bpmnXml: result.bpmn_xml,
                changes: result.changes,
                warnings: result.warnings || [],
                status: 'pending',
              }
            : undefined,
        });
      },
      failureCallback: (error: any) => {
        setLoading(false);
        addEntry({
          role: 'error',
          content: t('ai_bpmn_error', {
            error: error?.message || String(error),
          }),
        });
      },
    });
  };

  const accept = async (entry: AiBpmnChatEntry) => {
    if (!entry.proposal) {
      return;
    }
    const previousXml = await getCurrentXml();
    onApply(entry.proposal.bpmnXml);
    updateProposal(entry.id, { status: 'accepted', previousXml });
  };

  const revert = (entry: AiBpmnChatEntry) => {
    if (!entry.proposal?.previousXml) {
      return;
    }
    onApply(entry.proposal.previousXml);
    updateProposal(entry.id, { status: 'reverted' });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send(input);
    }
  };

  const examples = [
    t('ai_bpmn_example_1'),
    t('ai_bpmn_example_2'),
    t('ai_bpmn_example_3'),
  ];

  const statusChip = (status: ProposalStatus) => {
    if (status === 'pending') {
      return null;
    }
    const color = status === 'accepted' ? 'success' : 'default';
    return (
      <Chip
        size="small"
        color={color}
        label={t(`ai_bpmn_status_${status}`)}
        data-testid={`ai-bpmn-status-${status}`}
      />
    );
  };

  const proposalElement = (entry: AiBpmnChatEntry) => {
    const { proposal } = entry;
    if (!proposal) {
      return null;
    }
    const isLatestAccepted =
      proposal.status === 'accepted' &&
      !entries.some(
        (other) => other.id > entry.id && other.proposal?.status === 'accepted',
      );
    return (
      <Stack spacing={1} sx={{ mt: 1 }}>
        <Box
          component="details"
          open={proposal.status === 'pending'}
          sx={{ '& summary': { cursor: 'pointer' } }}
        >
          <Typography component="summary" variant="body2">
            {t('ai_bpmn_changes', { count: proposal.changes.length })}
          </Typography>
          <Box component="ul" sx={{ m: 0, pl: 3 }}>
            {proposal.changes.map((change) => (
              <Typography component="li" variant="body2" key={change}>
                {change}
              </Typography>
            ))}
          </Box>
        </Box>
        {proposal.warnings.length > 0 ? (
          <Alert severity="warning" data-testid="ai-bpmn-warnings">
            {proposal.warnings.map((warning) => (
              <div key={warning}>{warning}</div>
            ))}
          </Alert>
        ) : null}
        {proposal.status === 'pending' ? (
          <>
            <Box sx={{ position: 'relative' }}>
              <BpmnPreview
                bpmnXml={proposal.bpmnXml}
                errorMessage={t('ai_bpmn_preview_error')}
              />
              <Tooltip title={t('ai_bpmn_expand_preview')}>
                <IconButton
                  size="small"
                  aria-label={t('ai_bpmn_expand_preview')}
                  onClick={() => setEnlargedXml(proposal.bpmnXml)}
                  sx={{
                    position: 'absolute',
                    top: 4,
                    right: 4,
                    backgroundColor: 'background.paper',
                  }}
                >
                  <OpenInFullIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                size="small"
                onClick={() => accept(entry)}
                data-testid="ai-bpmn-accept"
              >
                {t('ai_bpmn_accept')}
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={() =>
                  updateProposal(entry.id, { status: 'discarded' })
                }
                data-testid="ai-bpmn-discard"
              >
                {t('ai_bpmn_discard')}
              </Button>
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {t('ai_bpmn_refine_hint')}
            </Typography>
          </>
        ) : (
          <Stack direction="row" spacing={1} alignItems="center">
            {statusChip(proposal.status)}
            {isLatestAccepted && proposal.previousXml ? (
              <Button
                size="small"
                onClick={() => revert(entry)}
                data-testid="ai-bpmn-revert"
              >
                {t('ai_bpmn_revert')}
              </Button>
            ) : null}
          </Stack>
        )}
      </Stack>
    );
  };

  const entryElement = (entry: AiBpmnChatEntry) => {
    if (entry.role === 'error') {
      return (
        <Alert severity="error" key={entry.id}>
          {entry.content}
        </Alert>
      );
    }
    const isUser = entry.role === 'user';
    return (
      <Box
        key={entry.id}
        data-testid={`ai-bpmn-entry-${entry.role}`}
        sx={{
          alignSelf: isUser ? 'flex-end' : 'stretch',
          maxWidth: isUser ? '85%' : '100%',
          px: 1.5,
          py: 1,
          borderRadius: 2,
          backgroundColor: isUser ? 'primary.main' : 'action.hover',
          color: isUser ? 'primary.contrastText' : 'text.primary',
          whiteSpace: 'pre-wrap',
        }}
      >
        <Typography variant="body2">{entry.content}</Typography>
        {!isUser && !entry.proposal ? (
          <Typography variant="caption" color="text.secondary">
            {t('ai_bpmn_no_changes')}
          </Typography>
        ) : null}
        {proposalElement(entry)}
        {!isUser && entry.usage?.input_tokens ? (
          <Typography
            variant="caption"
            color="text.secondary"
            component="div"
            sx={{ mt: 0.5 }}
          >
            {t('ai_bpmn_tokens', {
              input: entry.usage.input_tokens,
              output: entry.usage.output_tokens || 0,
            })}
          </Typography>
        ) : null}
      </Box>
    );
  };

  const emptyState = (
    <Stack spacing={1.5}>
      <Typography variant="body2" color="text.secondary">
        {t('ai_bpmn_intro')}
      </Typography>
      <Typography variant="subtitle2">{t('ai_bpmn_examples_title')}</Typography>
      {examples.map((example) => (
        <Chip
          key={example}
          label={example}
          variant="outlined"
          onClick={() => setInput(example)}
          sx={{
            height: 'auto',
            justifyContent: 'flex-start',
            '& .MuiChip-label': { whiteSpace: 'normal', py: 0.75 },
          }}
        />
      ))}
    </Stack>
  );

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        ModalProps={{ keepMounted: true }}
        slotProps={{
          paper: {
            sx: { width: { xs: '100%', sm: DRAWER_WIDTH } },
            'aria-label': t('ai_bpmn_title'),
          } as any,
        }}
        data-testid="ai-bpmn-panel"
      >
        <Stack sx={{ height: '100%' }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ p: 1.5, borderBottom: 1, borderColor: 'divider' }}
          >
            <AutoAwesomeIcon color="primary" />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="h6" component="h2">
                {t('ai_bpmn_title')}
              </Typography>
              {modelLabel ? (
                <Typography variant="caption" color="text.secondary" noWrap>
                  {modelLabel}
                </Typography>
              ) : null}
            </Box>
            <Tooltip title={t('ai_bpmn_new_chat')}>
              <span>
                <IconButton
                  aria-label={t('ai_bpmn_new_chat')}
                  onClick={() => setEntries([])}
                  disabled={loading || entries.length === 0}
                >
                  <RestartAltIcon />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title={t('ai_bpmn_close')}>
              <IconButton aria-label={t('ai_bpmn_close')} onClick={onClose}>
                <CloseIcon />
              </IconButton>
            </Tooltip>
          </Stack>

          <Stack
            spacing={1.5}
            sx={{ flex: 1, overflowY: 'auto', p: 1.5 }}
            aria-live="polite"
          >
            {entries.length === 0 ? emptyState : entries.map(entryElement)}
            {loading ? (
              <Box data-testid="ai-bpmn-loading">
                <Typography variant="body2" color="text.secondary">
                  {t('ai_bpmn_thinking')}
                </Typography>
                <LinearProgress sx={{ mt: 1 }} />
              </Box>
            ) : null}
            <div ref={transcriptEndRef} />
          </Stack>

          <Box sx={{ p: 1.5, borderTop: 1, borderColor: 'divider' }}>
            <TextField
              fullWidth
              multiline
              minRows={2}
              maxRows={8}
              label={t('ai_bpmn_input_label')}
              helperText={t('ai_bpmn_input_helper')}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              slotProps={{ htmlInput: { 'data-testid': 'ai-bpmn-input' } }}
            />
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
              <Button
                variant="contained"
                endIcon={<SendIcon />}
                onClick={() => send(input)}
                disabled={loading || !input.trim()}
                data-testid="ai-bpmn-send"
              >
                {t('ai_bpmn_send')}
              </Button>
            </Box>
          </Box>
        </Stack>
      </Drawer>
      <Dialog
        open={enlargedXml !== null}
        onClose={() => setEnlargedXml(null)}
        maxWidth="xl"
        fullWidth
      >
        <DialogTitle>{t('ai_bpmn_preview_title')}</DialogTitle>
        <DialogContent>
          {enlargedXml ? (
            <BpmnPreview
              bpmnXml={enlargedXml}
              height="70vh"
              errorMessage={t('ai_bpmn_preview_error')}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
