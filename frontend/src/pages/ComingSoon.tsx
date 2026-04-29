import { Card } from 'react-bootstrap';

interface ComingSoonProps {
  title: string;
  slice: string;
}

export default function ComingSoon({ title, slice }: ComingSoonProps) {
  return (
    <Card>
      <Card.Body>
        <Card.Title>{title}</Card.Title>
        <Card.Text className="text-muted mb-0">Coming soon — {slice}.</Card.Text>
      </Card.Body>
    </Card>
  );
}