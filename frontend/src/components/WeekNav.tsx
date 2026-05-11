/**
 * WeekNav — shared previous/next/date-picker chrome for week-scoped pages.
 *
 * Shared by the Dashboard and the OutreachHistory page. The parent owns the
 * URL state (?week=YYYY-MM-DD) and passes the current weekStart in; the
 * component emits the next weekStart through `onChange`.
 *
 * Contract: every value emitted via `onChange` is guaranteed to be a Monday
 * (in UTC). If the parent passes a non-Monday weekStart (e.g. a hand-edited
 * URL), the date input still displays it as-is, but the first interaction
 * snaps to the prior Monday.
 *
 * The ▶ button is disabled when the next week's Monday would be strictly
 * after the current week's Monday — looking into the future would just
 * show empty data.
 */
import { Button, Form } from 'react-bootstrap';

interface Props {
  /** YYYY-MM-DD; expected to be a Monday but not enforced. */
  weekStart: string;
  /** Called with the next week's Monday (YYYY-MM-DD) when the user navigates. */
  onChange: (next: string) => void;
}

/**
 * Round a YYYY-MM-DD date string back to its Monday (in UTC).
 * Returns the input unchanged if it can't be parsed.
 */
export function mondayOf(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return dateStr;
  // getUTCDay: Sunday=0, Monday=1, ..., Saturday=6. Re-base so Monday=0.
  const dayFromMonday = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dayFromMonday);
  return d.toISOString().slice(0, 10);
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Today's UTC Monday as YYYY-MM-DD. */
export function currentMonday(): string {
  const now = new Date();
  const yyyy = now.getUTCFullYear();
  const mm = String(now.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(now.getUTCDate()).padStart(2, '0');
  return mondayOf(`${yyyy}-${mm}-${dd}`);
}

export default function WeekNav({ weekStart, onChange }: Props) {
  const emit = (next: string) => onChange(mondayOf(next));
  const nextDisabled = addDays(mondayOf(weekStart), 7) > currentMonday();

  return (
    <div className="d-flex align-items-center gap-2 mb-3">
      <Button
        variant="outline-secondary"
        size="sm"
        onClick={() => emit(addDays(weekStart, -7))}
        aria-label="Previous week"
      >
        ◀
      </Button>
      <Form.Control
        type="date"
        value={weekStart}
        size="sm"
        style={{ maxWidth: '12rem' }}
        aria-label="Week start"
        onChange={(e) => {
          const picked = e.target.value;
          if (picked) emit(picked);
        }}
      />
      <Button
        variant="outline-secondary"
        size="sm"
        onClick={() => emit(addDays(weekStart, 7))}
        disabled={nextDisabled}
        aria-label="Next week"
      >
        ▶
      </Button>
    </div>
  );
}