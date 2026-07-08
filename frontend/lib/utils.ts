import { formatDistanceToNow } from 'date-fns';

export function relTime(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return '';
  }
}

export const CREDIBILITY_COLORS: Record<string, string> = {
  high: '#16a34a',
  medium: '#eab308',
  low: '#f97316',
  unverified: '#9ca3af',
};

export const CREDIBILITY_LABEL: Record<string, string> = {
  high: 'Verified',
  medium: 'Medium trust',
  low: 'Low trust',
  unverified: 'Unverified',
};

export function classNames(...arr: Array<string | false | null | undefined>): string {
  return arr.filter(Boolean).join(' ');
}