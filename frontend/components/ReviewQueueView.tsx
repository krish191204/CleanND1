'use client';

import { useEffect, useState } from 'react';
import { api, ReviewItem, ReviewQueueResponse } from '@/lib/api';
import { Check, X, Loader2, Tag, MessageSquare, RefreshCw, FlaskConical, AlertTriangle } from 'lucide-react';

const CATEGORIES = ['breaking', 'world', 'tech', 'finance', 'science', 'sports', 'culture', 'opinion', 'misinformation', 'other'];

export function ReviewQueueView() {
  const [data, setData] = useState<ReviewQueueResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [labeling, setLabeling] = useState<string | null>(null);
  const [retraining, setRetraining] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await api.reviewQueue(50);
      setData(r);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const label = async (id: string, lbl: 'approved' | 'rejected', cat?: string, notes?: string) => {
    setLabeling(id);
    try {
      await api.labelReview(id, { label: lbl, category: cat, notes, labeler_id: 'demo' });
      await refresh();
    } finally {
      setLabeling(null);
    }
  };

  const retrain = async () => {
    setRetraining(true);
    try {
      await api.retrain();
      setTimeout(() => setRetraining(false), 3000);
    } catch {
      setRetraining(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="card flex items-center gap-2 flex-wrap">
        <h2 className="text-base font-semibold text-ink-100 flex-1">
          Review Queue
        </h2>
        {data && (
          <span className="text-xs text-ink-400">
            {data.stats.unlabeled} unlabeled · {data.stats.labeled} labeled
          </span>
        )}
        <button onClick={refresh} className="btn btn-ghost text-xs" disabled={loading}>
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
        <button onClick={retrain} className="btn btn-primary text-xs" disabled={retraining}>
          {retraining ? <Loader2 size={12} className="animate-spin" /> : <FlaskConical size={12} />}
          {retraining ? 'Retraining…' : 'Retrain model'}
        </button>
      </div>

      {data && data.items.length === 0 && (
        <div className="card text-center py-12">
          <p className="text-ink-300 font-medium">Queue empty 🎉</p>
          <p className="text-ink-500 text-sm mt-1">All borderline items have been reviewed.</p>
        </div>
      )}

      <div className="space-y-3">
        {data?.items.map((it) => (
          <ReviewItemCard
            key={it.id}
            item={it}
            onLabel={label}
            isLabeling={labeling === it.id}
          />
        ))}
      </div>
    </div>
  );
}

function ReviewItemCard({
  item,
  onLabel,
  isLabeling,
}: {
  item: ReviewItem;
  onLabel: (id: string, lbl: 'approved' | 'rejected', cat?: string, notes?: string) => Promise<void>;
  isLabeling: boolean;
}) {
  const [cat, setCat] = useState(item.category || '');
  const [notes, setNotes] = useState(item.notes || '');

  // pull the cleaned text out of the snapshot
  const text: string = item.snapshot?.clean?.clean_text
    || item.snapshot?.raw?.text
    || '(no text)';
  const raw: any = item.snapshot?.raw || {};
  const clean: any = item.snapshot?.clean || {};
  const credReasons: string[] = (item.snapshot?.credibility_reasons) || [];
  const botReasons: string[] = clean.bot_reasons || [];
  const handle = raw.author_handle || '?';
  const verified = raw.author_verified;

  return (
    <div className="card">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h3 className="text-xs uppercase tracking-wide text-ink-500 mb-2">
            Model prediction
          </h3>
          <div className="space-y-2 text-sm">
            <Bar label="Bot probability" value={item.model_bot_score} color="bg-cred-low" />
            <Bar label="Credibility" value={item.model_credibility} color="bg-cred-high" />
            <Bar label="Relevance" value={item.model_relevance} color="bg-cred-medium" />
            <div className="text-xs text-ink-400 mt-1">
              Uncertainty margin: <span className="text-ink-200 font-mono">{(item.uncertainty_margin * 100).toFixed(0)}%</span>
            </div>
            {(credReasons.length + botReasons.length) > 0 && (
              <div className="text-xs text-ink-400 mt-2 flex flex-wrap gap-1">
                {[...credReasons, ...botReasons].slice(0, 5).map((r, i) => (
                  <span key={i} className="chip bg-ink-800 border border-ink-700 text-ink-300">
                    {r}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>

        <div>
          <h3 className="text-xs uppercase tracking-wide text-ink-500 mb-2">
            Tweet
          </h3>
          <div className="text-sm text-ink-200 mb-1">
            <span className="font-semibold">@{handle}</span>
            {verified && <span className="ml-1 text-cred-high">✓</span>}
          </div>
          <p className="text-sm text-ink-100 leading-relaxed">{text}</p>

          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-ink-500 flex items-center gap-1 mb-1">
                <Tag size={10} /> Category
              </label>
              <select
                value={cat}
                onChange={(e) => setCat(e.target.value)}
                className="w-full bg-ink-800 border border-ink-700 text-ink-100 text-sm rounded-md px-2 py-1"
              >
                <option value="">(none)</option>
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-ink-500 flex items-center gap-1 mb-1">
                <MessageSquare size={10} /> Notes
              </label>
              <input
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="optional"
                className="w-full bg-ink-800 border border-ink-700 text-ink-100 text-sm rounded-md px-2 py-1"
              />
            </div>
          </div>

          <div className="mt-3 flex gap-2">
            <button
              onClick={() => onLabel(item.id, 'approved', cat || undefined, notes || undefined)}
              disabled={isLabeling}
              className="btn btn-primary text-xs flex-1"
            >
              {isLabeling ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              Approve (human)
            </button>
            <button
              onClick={() => onLabel(item.id, 'rejected', cat || undefined, notes || undefined)}
              disabled={isLabeling}
              className="btn btn-danger text-xs flex-1"
            >
              <X size={12} /> Reject (bot/spam)
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Bar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span className="text-ink-400">{label}</span>
        <span className="text-ink-200 font-mono">{(value * 100).toFixed(0)}%</span>
      </div>
      <div className="h-1.5 bg-ink-800 rounded-full mt-1 overflow-hidden">
        <div
          className={`h-full ${color} transition-all`}
          style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
        />
      </div>
    </div>
  );
}