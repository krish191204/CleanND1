'use client';

import { useState } from 'react';
import { ThumbsUp, ThumbsDown, Check } from 'lucide-react';

interface FeedbackState {
  up: number;
  down: number;
}

export function FeedbackButtons({
  tweetId,
  initial,
  onSubmit,
}: {
  tweetId: string;
  initial?: FeedbackState;
  onSubmit?: (signal: 'up' | 'down') => Promise<void>;
}) {
  const [counts, setCounts] = useState<FeedbackState>(initial ?? { up: 0, down: 0 });
  const [picked, setPicked] = useState<'up' | 'down' | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (signal: 'up' | 'down') => {
    if (busy) return;
    setBusy(true);
    try {
      if (onSubmit) {
        await onSubmit(signal);
      } else {
        await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/feedback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tweet_id: tweetId, signal, user_id: 'dashboard' }),
        });
      }
      // optimistically bump the count and toggle the user's pick
      setPicked((prev) => {
        const otherSignal = signal === 'up' ? 'down' : 'up';
        setCounts((c) => ({
          up: c.up + (signal === 'up' ? 1 : prev === 'up' ? -1 : 0),
          down: c.down + (signal === 'down' ? 1 : prev === 'down' ? -1 : 0),
        }));
        return prev === signal ? null : signal;
      });
    } catch {
      // ignore — keep optimistic UI
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={() => submit('up')}
        disabled={busy}
        className={`btn btn-ghost text-xs flex items-center gap-1 ${
          picked === 'up' ? 'text-cred-high border border-cred-high/40' : ''
        }`}
        title={picked === 'up' ? "You liked this — tap to undo" : 'This is a good fit'}
      >
        {picked === 'up' ? <Check size={12} /> : <ThumbsUp size={12} />}
        {counts.up > 0 && <span className="text-xs">{counts.up}</span>}
      </button>
      <button
        onClick={() => submit('down')}
        disabled={busy}
        className={`btn btn-ghost text-xs flex items-center gap-1 ${
          picked === 'down' ? 'text-cred-low border border-cred-low/40' : ''
        }`}
        title={picked === 'down' ? 'You flagged this — tap to undo' : 'This does not belong'}
      >
        {picked === 'down' ? <Check size={12} /> : <ThumbsDown size={12} />}
        {counts.down > 0 && <span className="text-xs">{counts.down}</span>}
      </button>
    </div>
  );
}