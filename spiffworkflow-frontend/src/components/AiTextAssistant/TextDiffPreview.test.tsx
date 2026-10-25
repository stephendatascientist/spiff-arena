import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import TextDiffPreview from './TextDiffPreview';

describe('TextDiffPreview', () => {
  it('renders the suggestion as an inline diff', () => {
    const { container } = render(
      <TextDiffPreview
        original={'x = 1\n'}
        modified={'x = 2\n'}
        language="python"
        ariaLabel="Suggested changes"
      />,
    );
    expect(screen.getByTestId('ai-text-diff')).toBeInTheDocument();
    expect(container.querySelector('.cm-editor')).not.toBeNull();
    expect(container.textContent).toContain('x = 2');
  });
});
