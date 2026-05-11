/**
 * OutreachHistory — week-bucketed feed of outreach events across all contacts.
 *
 * Reads ``?week=YYYY-MM-DD`` from the URL (defaulted to the current UTC
 * Monday) and lists events from ``/outreach/history?week_start=...``. Each
 * row renders the direction arrow, contact name (linked to detail), method,
 * subject-or-notes headline (expand-collapse on email bodies), the
 * role/company badge when the event came from an imported email thread,
 * and the timestamp.
 *
 * The expand state is local to this page and resets on week navigation so
 * stale expansions don't carry over.
 */
import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Card, Collapse, Spinner } from 'react-bootstrap';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../api/client';
import WeekNav, { currentMonday } from '../components/WeekNav';
import { METHODS } from './ContactForm';

interface HistoryEvent {
  id: number;
  outreach_at: string | null;
  direction: string;
  method: string | null;
  subject: string | null;
  body_text: string | null;
  notes: string | null;
  gmail_message_id: string | null;
  contact_id: number;
  contact_name: string;
  contact_kind: string;
  submission_id: number | null;
  role_title: string | null;
  company_name: string | null;
}

interface HistoryData {
  week_start: string;
  events: HistoryEvent[];
}

function methodLabel(m: string | null): string {
  if (!m) return '';
  return METHODS.find((x) => x.short_name === m)?.label ?? m;
}

export default function OutreachHistory() {
  const apiFetch = useApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const weekStart = searchParams.get('week') || currentMonday();
  const [data, setData] = useState<HistoryData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    apiFetch(
      `/outreach/history?week_start=${encodeURIComponent(weekStart)}`,
      { signal: controller.signal },
    )
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => {
        if (e.name === 'AbortError') return;
        setError(String(e));
      });
    return () => controller.abort();
  }, [apiFetch, weekStart]);

  function handleWeekChange(next: string) {
    setSearchParams({ week: next });
    setExpandedIds(new Set());
  }

  function toggleExpanded(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Spinner animation="border" size="sm" />;

  return (
    <>
      <h3 className="mb-4">Outreach history</h3>

      <WeekNav weekStart={weekStart} onChange={handleWeekChange} />

      {data.events.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            No outreach in this week. Use ◀ to look earlier.
          </Card.Body>
        </Card>
      ) : (
        <Card>
          <Card.Body className="p-0">
            <ul className="list-unstyled mb-0">
              {data.events.map((evt) => {
                const isEmail = Boolean(evt.gmail_message_id);
                const hasBody = Boolean(
                  evt.body_text && evt.body_text.length > 0,
                );
                const isExpanded = expandedIds.has(evt.id);
                const arrow = evt.direction === 'inbound' ? '↓' : '↑';
                const arrowVariant =
                  evt.direction === 'inbound' ? 'success' : 'primary';
                const headline = isEmail
                  ? evt.subject || '(no subject)'
                  : evt.notes || '(no notes)';
                return (
                  <li key={evt.id} className="px-3 py-2 border-bottom">
                    <div className="d-flex align-items-center gap-2 flex-wrap">
                      <Badge bg={arrowVariant} pill title={evt.direction}>
                        {arrow}
                      </Badge>
                      <Link
                        to={`/contacts/${evt.contact_id}`}
                        className="text-decoration-none"
                      >
                        {evt.contact_name}
                      </Link>
                      {evt.method && (
                        <Badge bg="secondary">{methodLabel(evt.method)}</Badge>
                      )}
                      {isEmail && (
                        <Badge bg="info" text="dark">
                          via Gmail
                        </Badge>
                      )}
                      <span className="flex-grow-1">
                        {hasBody ? (
                          <Button
                            variant="link"
                            size="sm"
                            className="p-0 align-baseline text-start text-decoration-none"
                            onClick={() => toggleExpanded(evt.id)}
                          >
                            <span className="me-1">
                              {isExpanded ? '▾' : '▸'}
                            </span>
                            {headline}
                          </Button>
                        ) : (
                          <span>{headline}</span>
                        )}
                      </span>
                      {evt.submission_id !== null && (
                        <Link
                          to={`/submissions/${evt.submission_id}`}
                          className="text-decoration-none"
                        >
                          <Badge bg="light" text="dark" className="border">
                            {evt.role_title ?? '(role)'}
                            {evt.company_name ? ` @ ${evt.company_name}` : ''}
                          </Badge>
                        </Link>
                      )}
                      {evt.outreach_at && (
                        <span className="text-muted small">
                          {evt.outreach_at}
                        </span>
                      )}
                    </div>
                    <Collapse in={isExpanded && hasBody}>
                      <div>
                        <pre
                          className="small text-muted mt-2 mb-0 ms-4 p-2 bg-light rounded"
                          style={{
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                            maxHeight: 400,
                            overflowY: 'auto',
                          }}
                        >
                          {evt.body_text}
                        </pre>
                      </div>
                    </Collapse>
                  </li>
                );
              })}
            </ul>
          </Card.Body>
        </Card>
      )}
    </>
  );
}