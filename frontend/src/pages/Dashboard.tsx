import { useEffect, useState } from 'react';
import { Row, Col, Card, Spinner, Alert, ProgressBar } from 'react-bootstrap';
import { Link } from 'react-router-dom';
import { useApi } from '../api/client';
import { StatusBadge } from './Submissions';

interface Metric {
  today?: number;
  week?: number;
  pending?: number;
  daily: number | null;
  weekly: number | null;
}

interface RecentSubmission {
  id: number;
  role_title: string | null;
  company_name: string | null;
  status: string;
  submitted_on: string | null;
  updated_at: string | null;
}

interface DashboardData {
  today: string;
  week_start: string;
  metrics: {
    submissions: Metric;
    personal_outreach: Metric;
    recruiter_outreach: Metric;
    follow_ups: Metric;
  };
  recent_submissions: RecentSubmission[];
}

const TODAY_TILES: { key: keyof DashboardData['metrics']; label: string }[] = [
  { key: 'submissions', label: 'Submissions' },
  { key: 'personal_outreach', label: 'Personal Outreach' },
  { key: 'recruiter_outreach', label: 'Recruiter Outreach' },
];

function ProgressTile({
  label,
  count,
  goal,
}: {
  label: string;
  count: number;
  goal: number | null;
}) {
  const target = goal ?? 0;
  const pct = target > 0 ? Math.min(100, (count / target) * 100) : 0;
  return (
    <Card className="h-100">
      <Card.Body>
        <Card.Subtitle className="text-muted mb-2">{label}</Card.Subtitle>
        <div className="fs-3 fw-semibold">
          {count}
          <span className="text-muted fs-5"> of {goal ?? '—'}</span>
        </div>
        <ProgressBar now={pct} className="mt-2" style={{ height: 6 }} />
      </Card.Body>
    </Card>
  );
}

export default function Dashboard() {
  const apiFetch = useApi();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch('/dashboard/today')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [apiFetch]);

  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Spinner animation="border" size="sm" />;

  const m = data.metrics;

  return (
    <>
      <h3 className="mb-4">Dashboard</h3>

      <h5 className="text-muted">Today ({data.today})</h5>
      <Row className="g-3 mb-4">
        {TODAY_TILES.map(({ key, label }) => (
          <Col md={4} key={`today-${key}`}>
            <ProgressTile
              label={label}
              count={m[key].today ?? 0}
              goal={m[key].daily}
            />
          </Col>
        ))}
      </Row>

      <h5 className="text-muted">This week (since {data.week_start})</h5>
      <Row className="g-3 mb-4">
        {TODAY_TILES.map(({ key, label }) => (
          <Col md={4} key={`week-${key}`}>
            <ProgressTile
              label={label}
              count={m[key].week ?? 0}
              goal={m[key].weekly}
            />
          </Col>
        ))}
      </Row>

      <Row className="g-3">
        <Col md={8}>
          <Card>
            <Card.Body>
              <Card.Title>Recent activity</Card.Title>
              {data.recent_submissions.length === 0 ? (
                <Card.Text className="text-muted mb-0">
                  No activity yet — submissions and responses will appear here.
                </Card.Text>
              ) : (
                <ul className="list-unstyled mb-0">
                  {data.recent_submissions.map((s) => (
                    <li
                      key={s.id}
                      className="py-2 border-bottom d-flex align-items-center"
                    >
                      <span className="me-2">
                        <StatusBadge status={s.status} />
                      </span>
                      <Link to={`/submissions/${s.id}`} className="me-2">
                        {s.role_title ?? '(untitled)'}
                      </Link>
                      <span className="text-muted me-2">
                        {s.company_name ? `at ${s.company_name}` : ''}
                      </span>
                      <span className="ms-auto text-muted small">
                        {s.submitted_on ?? ''}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card.Body>
          </Card>
        </Col>
        <Col md={4}>
          <Card>
            <Card.Body>
              <Card.Title>Pending follow-ups</Card.Title>
              <div className="fs-3 fw-semibold">
                {m.follow_ups.pending ?? 0}
              </div>
              <Card.Text className="text-muted mb-0">
                Submissions awaiting a follow-up.
              </Card.Text>
            </Card.Body>
          </Card>
        </Col>
      </Row>
    </>
  );
}