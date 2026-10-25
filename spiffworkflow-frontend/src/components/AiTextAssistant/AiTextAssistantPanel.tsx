import { KeyboardEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import CloseIcon from '@mui/icons-material/Close';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import SendIcon from '@mui/icons-material/Send';
import HttpService from '../../services/HttpService';
import TextDiffPreview from './TextDiffPreview';

const MAX_HISTORY_ENTRIES = 6;

export type AiTextAssistKind = 'script' | 'markdown';

export type AiTextAssistContext = {
  element?: {
    id?: string;
    name?: string;
    type?: string;
    script_type?: string;
  };
  unit_tests?: { input: unknown; expected: unknown }[];
  process_model_identifier?: string;
  bpmn_xml?: string | null;
};

type ProposalStatus =
  | 'pending'
  | 'accepted'
  | 'discarded'
  | 'superseded'
  | 'reverted';

type Proposal = {
  content: string;
  baseContent: string;
  warnings: string[];
  tests?: { passed: number; total: number } | null;
  status: ProposalStatus;
  previousContent?: string;
};

type ChatEntry = {
  id: number;
  role: 'user' | 'assistant' | 'error';
  content: string;
  proposal?: Proposal;
  usage?: { input_tokens?: number; output_tokens?: number };
};

type TextAssistResponse = {
  content: string;
  message: string;
  warnings?: string[];
  tests?: { passed: number; total: number } | null;
  usage?: { input_tokens?: number; output_tokens?: number };
};

type AiTextAssistantPanelProps = {
  kind: AiTextAssistKind;
  getContent: () => string;
  onApply: (content: string) => void;
  getContext?: () => Promise<AiTextAssistContext> | AiTextAssistContext;
  onClose?: () => void;
  modelLabel?: string | null;
  height?: string | number;
};

/**
 * Chat panel that drafts a script or end-user instructions, shows the
 * suggestion as a diff, and lets the user apply, discard, refine or undo it.
 */
export default function AiTextAssistantPanel({
  kind,
  getContent,
  onApply,
  getContext,
  onClose,
  modelLabel,
  height = '560px',
}: AiTextAssistantPanelProps) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const nextId = useRef(1);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [entries, loading]);

  const addEntry = (entry: Omit<ChatEntry, 'id'>) => {
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
    const baseContent = pending?.proposal
      ? pending.proposal.content
      : getContent();
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
    let context: AiTextAssistContext = {};
    try {
      context = (await getContext?.()) || {};
    } catch (_error) {
      context = {};
    }
    HttpService.makeCallToBackend({
      path: '/script-assist/process-message',
      httpMethod: 'POST',
      postBody: {
        kind,
        prompt,
        current_content: baseContent,
        history,
        ...context,
      },
      successCallback: (result: TextAssistResponse) => {
        setLoading(false);
        const changed =
          typeof result.content === 'string' && result.content !== baseContent;
        addEntry({
          role: 'assistant',
          content: result.message,
          usage: result.usage,
          proposal: changed
            ? {
                content: result.content,
                baseContent,
                warnings: result.warnings || [],
                tests: result.tests,
                status: 'pending',
              }
            : undefined,
        });
      },
      failureCallback: (error: any) => {
        setLoading(false);
        addEntry({
          role: 'error',
          content: t('ai_text_error', {
            error: error?.message || String(error),
          }),
        });
      },
    });
  };

  const accept = (entry: ChatEntry) => {
    if (!entry.proposal) {
      return;
    }
    const previousContent = getContent();
    onApply(entry.proposal.content);
    updateProposal(entry.id, { status: 'accepted', previousContent });
  };

  const revert = (entry: ChatEntry) => {
    if (entry.proposal?.previousContent === undefined) {
      return;
    }
    onApply(entry.proposal.previousContent);
    updateProposal(entry.id, { status: 'reverted' });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send(input);
    }
  };

  const examples = [1, 2, 3].map((index) =>
    t(`ai_text_${kind}_example_${index}`),
  );

  const testsChip = (tests: Proposal['tests']) => {
    if (!tests || !tests.total) {
      return null;
    }
    const allPassed = tests.passed === tests.total;
    return (
      <Chip
        size="small"
        color={allPassed ? 'success' : 'warning'}
        label={t('ai_text_tests', { passed: tests.passed, total: tests.total })}
        data-testid="ai-text-tests"
      />
    );
  };

  const proposalElement = (entry: ChatEntry) => {
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
        {proposal.warnings.length > 0 ? (
          <Alert severity="warning" data-testid="ai-text-warnings">
            {proposal.warnings.map((warning) => (
              <div key={warning}>{warning}</div>
            ))}
          </Alert>
        ) : null}
        {proposal.status === 'pending' ? (
          <>
            <TextDiffPreview
              original={proposal.baseContent}
              modified={proposal.content}
              language={kind === 'script' ? 'python' : 'markdown'}
              ariaLabel={t('ai_text_suggestion')}
            />
            <Stack direction="row" spacing={1} alignItems="center">
              <Button
                variant="contained"
                size="small"
                onClick={() => accept(entry)}
                data-testid="ai-text-accept"
              >
                {t(`ai_text_${kind}_accept`)}
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={() =>
                  updateProposal(entry.id, { status: 'discarded' })
                }
                data-testid="ai-text-discard"
              >
                {t('ai_bpmn_discard')}
              </Button>
              {testsChip(proposal.tests)}
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {t('ai_bpmn_refine_hint')}
            </Typography>
          </>
        ) : (
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              color={proposal.status === 'accepted' ? 'success' : 'default'}
              label={t(`ai_bpmn_status_${proposal.status}`)}
              data-testid={`ai-text-status-${proposal.status}`}
            />
            {isLatestAccepted && proposal.previousContent !== undefined ? (
              <Button
                size="small"
                onClick={() => revert(entry)}
                data-testid="ai-text-revert"
              >
                {t('ai_bpmn_revert')}
              </Button>
            ) : null}
          </Stack>
        )}
      </Stack>
    );
  };

  const entryElement = (entry: ChatEntry) => {
    if (entry.role === 'error') {
      return (
        <Alert severity="error" key={entry.id} data-testid="ai-text-error">
          {entry.content}
        </Alert>
      );
    }
    const isUser = entry.role === 'user';
    return (
      <Box
        key={entry.id}
        data-testid={`ai-text-entry-${entry.role}`}
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
            {t('ai_text_no_changes')}
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
        {t(`ai_text_${kind}_intro`)}
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
    <Stack
      component="section"
      aria-label={t(`ai_text_${kind}_title`)}
      data-testid="ai-text-panel"
      sx={{
        height,
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        overflow: 'hidden',
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: 'divider' }}
      >
        <AutoAwesomeIcon color="primary" fontSize="small" />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" component="h3">
            {t(`ai_text_${kind}_title`)}
          </Typography>
          {modelLabel ? (
            <Typography
              variant="caption"
              color="text.secondary"
              noWrap
              component="div"
            >
              {modelLabel}
            </Typography>
          ) : null}
        </Box>
        <Tooltip title={t('ai_bpmn_new_chat')}>
          <span>
            <IconButton
              size="small"
              aria-label={t('ai_bpmn_new_chat')}
              onClick={() => setEntries([])}
              disabled={loading || entries.length === 0}
            >
              <RestartAltIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        {onClose ? (
          <Tooltip title={t('ai_bpmn_close')}>
            <IconButton
              size="small"
              aria-label={t('ai_bpmn_close')}
              onClick={onClose}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        ) : null}
      </Stack>

      <Stack
        spacing={1.5}
        sx={{ flex: 1, overflowY: 'auto', p: 1.5 }}
        aria-live="polite"
      >
        {entries.length === 0 ? emptyState : entries.map(entryElement)}
        {loading ? (
          <Box data-testid="ai-text-loading">
            <Typography variant="body2" color="text.secondary">
              {t(`ai_text_${kind}_thinking`)}
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
          maxRows={6}
          size="small"
          label={t(`ai_text_${kind}_input_label`)}
          helperText={t('ai_bpmn_input_helper')}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          slotProps={{ htmlInput: { 'data-testid': 'ai-text-input' } }}
        />
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
          <Button
            variant="contained"
            size="small"
            endIcon={<SendIcon />}
            onClick={() => send(input)}
            disabled={loading || !input.trim()}
            data-testid="ai-text-send"
          >
            {t('ai_bpmn_send')}
          </Button>
        </Box>
      </Box>
    </Stack>
  );
}
