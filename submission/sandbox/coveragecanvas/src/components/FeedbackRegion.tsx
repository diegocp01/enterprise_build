import type { Feedback } from '../domain/types';

export function FeedbackRegion({ feedback }: { feedback: Feedback | null }) {
  if (!feedback) return null;
  return (
    <div className={`feedback feedback-${feedback.kind}`} role="status" aria-live="polite" data-testid="feedback">
      <span aria-hidden="true">{feedback.kind === 'success' ? '✓' : '!'}</span>
      <div><strong>{feedback.kind === 'success' ? 'Plan updated' : 'Assignment blocked'}</strong><p>{feedback.message}</p></div>
    </div>
  );
}
