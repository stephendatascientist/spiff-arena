import React, { ReactNode } from 'react';
import { Box, Button, Stack, Tooltip } from '@mui/material';

type AssistLayoutProps = {
  assistPanelOpen?: boolean;
  renderAssistPanel?: () => ReactNode;
  children: ReactNode;
};

/** Puts the editor and, when open, an assistant panel side by side. */
export function AssistLayout({
  assistPanelOpen = false,
  renderAssistPanel,
  children,
}: AssistLayoutProps) {
  if (!assistPanelOpen || !renderAssistPanel) {
    return <>{children}</>;
  }
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: '1fr', md: '7fr 5fr' },
        gap: 2,
        alignItems: 'start',
      }}
    >
      <Box sx={{ minWidth: 0 }}>{children}</Box>
      <Box sx={{ minWidth: 0 }} data-testid="assist-panel-container">
        {renderAssistPanel()}
      </Box>
    </Box>
  );
}

type AssistToggleButtonProps = {
  label?: string;
  tooltip?: string;
  open: boolean;
  onToggle?: () => void;
  icon?: ReactNode;
};

export function AssistToggleButton({
  label,
  tooltip,
  open,
  onToggle,
  icon,
}: AssistToggleButtonProps) {
  if (!label || !onToggle) {
    return null;
  }
  const button = (
    <Button
      size="small"
      variant={open ? 'contained' : 'outlined'}
      startIcon={icon}
      onClick={onToggle}
      aria-pressed={open}
      data-testid="assist-toggle"
    >
      {label}
    </Button>
  );
  return (
    <Stack direction="row" justifyContent="flex-end">
      {tooltip ? <Tooltip title={tooltip}>{button}</Tooltip> : button}
    </Stack>
  );
}
