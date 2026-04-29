import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Card } from 'react-bootstrap';
export default function ComingSoon({ title, slice }) {
    return (_jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Title, { children: title }), _jsxs(Card.Text, { className: "text-muted mb-0", children: ["Coming soon \u2014 ", slice, "."] })] }) }));
}
