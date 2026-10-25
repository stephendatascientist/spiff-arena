import React, { ReactNode, SyntheticEvent } from 'react';
import { Box, Button, Stack, Tab, Tabs } from '@mui/material';
import DialogShell from './DialogShell';
import { AssistLayout, AssistToggleButton } from './AssistLayout';

type ScriptEditorDialogProps = {
    open: boolean;
    title: string;
    tabValue: number;
    onTabChange: (event: SyntheticEvent, value: number) => void;
    scriptTabLabel: string;
    unitTestsTabLabel: string;
    renderScript: () => ReactNode;
    renderUnitTests?: () => ReactNode;
    closeLabel: string;
    onClose: () => void;
    /** Side panel shown next to the editor tabs, e.g. an AI assistant. */
    renderAssistPanel?: () => ReactNode;
    assistPanelOpen?: boolean;
    onToggleAssist?: () => void;
    assistToggleLabel?: string;
    assistToggleTooltip?: string;
    assistToggleIcon?: ReactNode;
    /** @deprecated legacy assist tab, ignored when renderAssistPanel is given */
    assistTabLabel?: string;
    /** @deprecated legacy assist tab, ignored when renderAssistPanel is given */
    assistEnabled?: boolean;
    /** @deprecated legacy assist tab, ignored when renderAssistPanel is given */
    renderAssist?: () => ReactNode;
};

function TabPanel(props: {
    children?: ReactNode;
    index: number;
    value: number;
}) {
    const { children, value, index, ...other } = props;

    return (
        <div
            role="tabpanel"
            hidden={value !== index}
            id={`simple-tabpanel-${index}`}
            aria-labelledby={`simple-tab-${index}`}
            {...other}
        >
            {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
        </div>
    );
}

export default function ScriptEditorDialog({
    open,
    title,
    tabValue,
    onTabChange,
    scriptTabLabel,
    unitTestsTabLabel,
    renderScript,
    renderUnitTests,
    closeLabel,
    onClose,
    renderAssistPanel,
    assistPanelOpen = false,
    onToggleAssist,
    assistToggleLabel,
    assistToggleTooltip,
    assistToggleIcon,
    assistTabLabel,
    assistEnabled = false,
    renderAssist,
}: ScriptEditorDialogProps) {
    const showAssistTab =
        !renderAssistPanel && assistEnabled && !!assistTabLabel && !!renderAssist;
    return (
        <DialogShell
            className="bpmn-editor-wide-dialog"
            open={open}
            onClose={onClose}
            title={title}
        >
            <Stack direction="row" alignItems="center" spacing={1}>
                <Tabs value={tabValue} onChange={onTabChange} sx={{ flex: 1 }}>
                    <Tab label={scriptTabLabel} />
                    {showAssistTab ? <Tab label={assistTabLabel} /> : null}
                    {renderUnitTests ? <Tab label={unitTestsTabLabel} /> : null}
                </Tabs>
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
                <TabPanel value={tabValue} index={0}>
                    {renderScript()}
                </TabPanel>
                {showAssistTab && renderAssist ? (
                    <TabPanel value={tabValue} index={1}>
                        {renderAssist()}
                    </TabPanel>
                ) : null}
                {renderUnitTests ? (
                    <TabPanel value={tabValue} index={showAssistTab ? 2 : 1}>
                        {renderUnitTests()}
                    </TabPanel>
                ) : null}
            </AssistLayout>
            <Button onClick={onClose}>{closeLabel}</Button>
        </DialogShell>
    );
}
