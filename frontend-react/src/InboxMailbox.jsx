import React, { useEffect, useRef, useState } from 'react';
import { Inbox, ArrowLeft, RefreshCw } from 'lucide-react';
import './inbox.css';

const date = value => { const d = new Date(value); return Number.isNaN(d.getTime()) ? value : d.toLocaleString(); };
export default function InboxMailbox({ api }) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState({ emails: [], total: 0, unread_count: 0 });
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const refresh = () => setRevision(v => v + 1);
  useEffect(() => {
    let active = true; setLoading(true); setError('');
    api(`/inbox?${new URLSearchParams({ search: query, unread: filter, limit: '25', offset: String(offset) })}`)
      .then(result => { if (active) { if (offset && offset >= result.total) setOffset(Math.max(0, Math.floor((result.total - 1) / 25) * 25)); else setData(result); } })
      .catch(e => { if (active) setError(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [query, filter, offset, revision]);
  useEffect(() => () => { sequence.current++; }, []);
  async function open(email) {
    const request = ++sequence.current; setBusy(true); setError('');
    try {
      const detail = await api(`/inbox/${email.id}`);
      if (request !== sequence.current) return;
      setSelected(detail);
      if (!Number(detail.is_read)) {
        await api(`/inbox/${email.id}/read`, { method: 'POST' });
        if (request !== sequence.current) return;
        setSelected({ ...detail, is_read: 1 }); refresh();
      }
    } catch (e) { if (request === sequence.current) setError(`Could not open or mark message as read: ${e.message}`); }
    finally { if (request === sequence.current) setBusy(false); }
  }
  async function toggle() {
    setBusy(true); setError('');
    try { await api(`/inbox/${selected.id}/toggle-read`, { method: 'POST' }); setSelected(current => ({ ...current, is_read: Number(current.is_read) ? 0 : 1 })); refresh(); }
    catch (e) { setError(`Could not update read status: ${e.message}`); }
    finally { setBusy(false); }
  }
  return <section className="inbox-mailbox">
    <div className="page-header"><div><div className="eyebrow">EMAIL</div><h1>Inbox</h1><p className="muted">Received messages and campaign replies</p></div><button className="quiet" disabled={loading || busy} onClick={refresh}><RefreshCw size={16} />Refresh list</button></div>
    <div className="panel">
      {error && <div className="error-banner" role="alert">{error}<button className="quiet" onClick={refresh}>Retry list</button></div>}
      {selected ? <article className="inbox-detail">
        <div className="inbox-actions"><button className="quiet" disabled={busy} onClick={() => setSelected(null)}><ArrowLeft size={16} />Back to inbox</button><button className="quiet" disabled={busy} onClick={toggle}>{Number(selected.is_read) ? 'Mark unread' : 'Mark read'}</button></div>
        <h2>{selected.subject || '(No subject)'}</h2><p><strong>{selected.from_name || selected.from_email}</strong> &lt;{selected.from_email}&gt;</p><p>To: {selected.to_email || 'Not available'}</p><p className="muted">{date(selected.received_at)} · {Number(selected.is_read) ? 'Read' : 'Unread'}</p>
        {selected.body ? <div className="inbox-message">{selected.body}</div> : <p className="muted">{selected.snippet || 'No plain-text message is available. HTML rendering is not enabled.'}</p>}
      </article> : <>
        <form className="inbox-search" onSubmit={e => { e.preventDefault(); setOffset(0); setQuery(search.trim()); }}><input aria-label="Search inbox" placeholder="Search sender email, subject or message" value={search} onChange={e => setSearch(e.target.value)} /><button className="quiet" type="submit">Search</button>{(search || query) && <button className="quiet" type="button" onClick={() => { setSearch(''); setQuery(''); setOffset(0); }}>Clear</button>}</form>
        <div className="inbox-actions"><div className="settings-tabs">{[['', 'All'], ['unread', 'Unread'], ['read', 'Read']].map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} aria-pressed={filter === value} onClick={() => { setFilter(value); setOffset(0); }}>{label}</button>)}</div><span>{data.unread_count} unread</span></div>
        {loading || busy ? <p className="empty" role="status">Loading messages...</p> : error ? null : !data.emails.length ? <div className="empty"><Inbox size={30} /><h2>{query || filter ? 'No matching messages' : 'Your inbox is empty'}</h2><p>{query || filter ? 'Try another search or filter.' : 'Messages will appear after your mailbox is connected and synchronized.'}</p></div> : <div className="inbox-list">{data.emails.map(email => <button key={email.id} className={`inbox-row ${Number(email.is_read) ? '' : 'is-unread'}`} onClick={() => open(email)}><span className="inbox-sender">{!Number(email.is_read) && <span className="unread-dot" aria-label="Unread" />}{email.from_name || email.from_email}</span><span className="inbox-subject">{email.subject || '(No subject)'}<small>{email.snippet || email.body}</small></span><time>{date(email.received_at)}</time></button>)}</div>}
        <div className="inbox-pagination"><span>{data.total ? `${offset + 1}-${Math.min(offset + 25, data.total)} of ${data.total}` : '0 messages'}</span><button className="quiet" disabled={loading || busy || offset === 0} onClick={() => setOffset(v => Math.max(0, v - 25))}>Previous</button><button className="quiet" disabled={loading || busy || offset + 25 >= data.total} onClick={() => setOffset(v => v + 25)}>Next</button></div>
      </>}
      <p className="muted">Refresh reloads stored messages; it does not synchronize your connected mailbox.</p>
    </div>
  </section>;
}
