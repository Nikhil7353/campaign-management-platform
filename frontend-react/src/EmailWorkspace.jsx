import React, { useEffect, useState } from 'react';
import { Send, Minus, Maximize2, Minimize2, X, RefreshCw, ArrowLeft, Play, RotateCw } from 'lucide-react';
import './email-workspace.css';

const blank = { recipient: '', cc: '', bcc: '', subject: '', body: '' };

const formatDate = value => {
  if (!value) return '';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
};

const statusLabel = status => {
  if (status === 'dry_run') return 'Dry run';
  if (status === 'pending') return 'Queued';
  if (!status) return 'Unknown';
  return status.charAt(0).toUpperCase() + status.slice(1);
};

const statusTone = status => {
  if (status === 'sent' || status === 'delivered') return 'sent';
  if (status === 'failed' || status === 'bounced') return 'failed';
  if (status === 'pending' || status === 'processing') return 'pending';
  return 'dry-run';
};

export function FloatingComposer({ api, mode, setMode, onQueued }) {
  const [form, setForm] = useState(blank);
  const [copies, setCopies] = useState({ cc: false, bcc: false });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const toRef = React.useRef(null);
  useEffect(() => { if (mode === 'normal' || mode === 'expanded') toRef.current?.focus(); }, [mode]);
  const update = key => event => setForm(current => ({ ...current, [key]: event.target.value }));
  async function queue(event) {
    event.preventDefault();
    const split = value => [...new Set(value.split(/[,;\n]/).map(v => v.trim()).filter(Boolean))];
    const recipients = split(form.recipient);
    const invalid = [...recipients, ...split(form.cc), ...split(form.bcc)].filter(v => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v));
    if (!recipients.length || invalid.length) { setNotice('Please enter valid recipient, Cc and Bcc email addresses.'); return; }
    if (!form.subject.trim() || !form.body.trim()) { setNotice('Add a subject and message before queuing.'); return; }
    setBusy(true); setNotice('');
    const succeeded = [];
    try {
      for (const recipient of recipients) {
        await api('/emails', { method: 'POST', body: JSON.stringify({ ...form, recipient, cc: split(form.cc).join(', '), bcc: split(form.bcc).join(', ') }) });
        succeeded.push(recipient);
      }
      setForm(blank);
      setNotice(`${succeeded.length} message(s) queued. Open Sent to view or send queued messages.`);
      onQueued?.();
    } catch (error) {
      setForm(current => ({ ...current, recipient: recipients.filter(v => !succeeded.includes(v)).join(', ') }));
      setNotice(`${succeeded.length} queued. Remaining recipients kept for retry. ${error.message}`);
      if (succeeded.length) onQueued?.();
    } finally { setBusy(false); }
  }
  return <section hidden={mode === 'closed'} className={`floating-compose ${mode}`} role="dialog" aria-label="Compose email" aria-modal="false">
    <header className="floating-title">
      <button type="button" className="compose-title" onClick={() => mode === 'minimized' && setMode('normal')}>{form.subject || 'New message'}</button>
      <div>
        <button type="button" aria-label={mode === 'minimized' ? 'Restore compose' : 'Minimize compose'} onClick={() => setMode(mode === 'minimized' ? 'normal' : 'minimized')}><Minus size={16} /></button>
        <button type="button" aria-label={mode === 'expanded' ? 'Restore size' : 'Expand compose'} onClick={() => setMode(mode === 'expanded' ? 'normal' : 'expanded')}>{mode === 'expanded' ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
        <button type="button" aria-label="Close compose" title="Close; keep draft until page reload" onClick={() => setMode('closed')}><X size={18} /></button>
      </div>
    </header>
    <form hidden={mode === 'minimized'} onSubmit={queue} className="floating-form">
      <fieldset disabled={busy}>
        <div className="compose-address"><label>To<input ref={toRef} required value={form.recipient} onChange={update('recipient')} placeholder="Recipients (comma separated)" /></label><button type="button" aria-pressed={copies.cc} onClick={() => setCopies(c => ({ ...c, cc: !c.cc }))}>Cc</button><button type="button" aria-pressed={copies.bcc} onClick={() => setCopies(c => ({ ...c, bcc: !c.bcc }))}>Bcc</button></div>
        {copies.cc && <label>Cc<input value={form.cc} onChange={update('cc')} placeholder="Copy recipients" /></label>}
        {copies.bcc && <label>Bcc<input value={form.bcc} onChange={update('bcc')} placeholder="Hidden recipients" /></label>}
        <label>Subject<input required maxLength={255} value={form.subject} onChange={update('subject')} /></label>
        <textarea aria-label="Message" required value={form.body} onChange={update('body')} placeholder="Write your message..." />
      </fieldset>
      {notice && <p className="compose-notice" role="status">{notice}</p>}
      <footer><button disabled={busy} className="primary" type="submit"><Send size={16} />{busy ? 'Queuing...' : 'Queue email'}</button><small>Plain text · Draft kept while this page stays open</small></footer>
    </form>
  </section>;
}

const SENT_FILTERS = [
  ['', 'All', 'all'],
  ['sent', 'Sent', 'sent'],
  ['pending', 'Queued', 'pending'],
  ['failed', 'Failed', 'failed'],
  ['dry_run', 'Dry run', 'dry_run'],
];

export function SentMailbox({ api, onCompose, externalRevision = 0 }) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState({ emails: [], total: 0, counts: { all: 0, sent: 0, pending: 0, failed: 0, dry_run: 0 } });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);

  const refresh = () => setRevision(v => v + 1);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    const params = new URLSearchParams({ status: filter, search: query, limit: '25', offset: String(offset) });
    api(`/emails?${params}`)
      .then(result => {
        if (!active) return;
        const total = Number(result.total || 0);
        if (offset && offset >= total) {
          setOffset(Math.max(0, Math.floor((total - 1) / 25) * 25));
        } else {
          setData({
            emails: result.emails || [],
            total,
            counts: result.counts || { all: total, sent: 0, pending: 0, failed: 0, dry_run: 0 },
          });
        }
      })
      .catch(e => { if (active) setError(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api, filter, query, offset, revision, externalRevision]);

  async function processQueue() {
    setProcessing(true);
    setError('');
    setNotice('');
    try {
      const result = await api('/workers/process-email', { method: 'POST' });
      const results = result.results || [];
      const failedCount = results.filter(r => r.status === 'failed').length;
      const dryRunCount = results.filter(r => r.status === 'dry_run').length;
      const sentCount = results.filter(r => r.status === 'sent').length;
      setNotice(
        result.processed
          ? `Processed ${result.processed} queued email(s): ${sentCount} sent, ${dryRunCount} dry run, ${failedCount} failed.`
          : 'No queued emails to process.'
      );
      refresh();
    } catch (e) {
      setError(`Could not process email queue: ${e.message}`);
    } finally {
      setProcessing(false);
    }
  }

  async function sendSingle(email) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api(`/emails/${email.id}/send`, { method: 'POST' });
      setNotice(`Email #${email.id} updated: ${statusLabel(result.status)}.`);
      const detail = await api(`/emails/${email.id}`);
      setSelected(detail);
      refresh();
    } catch (e) {
      setError(`Send failed: ${e.message}`);
      try {
        const detail = await api(`/emails/${email.id}`);
        setSelected(detail);
      } catch {
        // Ignore follow-up detail fetch error
      }
      refresh();
    } finally {
      setBusy(false);
    }
  }

  const counts = data.counts || {};
  const pendingCount = Number(counts.pending || 0);
  const canSendSelected = selected && ['pending', 'failed', 'bounced'].includes(selected.status);

  return (
    <section className="sent-mailbox">
      <div className="page-header">
        <div>
          <div className="eyebrow">EMAIL</div>
          <h1>Sent</h1>
          <p className="muted">Track sent, queued, dry-run, and failed outgoing messages.</p>
        </div>
        <div className="sent-header-actions">
          {pendingCount > 0 && (
            <button type="button" className="primary" disabled={processing || busy} onClick={processQueue}>
              <Play size={15} />
              {processing ? 'Processing...' : `Send queued (${pendingCount})`}
            </button>
          )}
          <button type="button" className="quiet" disabled={loading || processing} onClick={refresh}>
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </div>
      <div className="panel">
        {error && (
          <div className="error-banner sent-error-banner" role="alert">
            <span>{error}</span>
            <button type="button" className="quiet" onClick={refresh}>Retry</button>
          </div>
        )}
        {notice && <p className="success sent-notice" role="status">{notice}</p>}
        {selected ? (
          <article className="sent-detail">
            <div className="sent-detail-bar">
              <button type="button" className="quiet" disabled={busy} onClick={() => setSelected(null)}>
                <ArrowLeft size={16} />
                Back to Sent
              </button>
              <div className="sent-detail-actions">
                <span className={`sent-status-badge ${statusTone(selected.status)}`}>{statusLabel(selected.status)}</span>
                {canSendSelected && (
                  <button type="button" className="primary" disabled={busy} onClick={() => sendSingle(selected)}>
                    <RotateCw size={15} />
                    {busy ? 'Sending...' : selected.status === 'pending' ? 'Send now' : 'Retry send'}
                  </button>
                )}
              </div>
            </div>
            <h2>{selected.subject || '(No subject)'}</h2>
            <div className="sent-meta-grid">
              <div><span>To</span><strong>{selected.recipient}</strong></div>
              {selected.cc && <div><span>Cc</span><strong>{selected.cc}</strong></div>}
              {selected.bcc && <div><span>Bcc</span><strong>{selected.bcc}</strong></div>}
              <div><span>{selected.sent_at ? 'Sent at' : 'Queued at'}</span><strong>{formatDate(selected.sent_at || selected.created_at)}</strong></div>
              <div><span>Attempts</span><strong>{selected.attempts ?? 0}</strong></div>
              {selected.message_id && <div><span>Message ID</span><strong>{selected.message_id}</strong></div>}
            </div>
            {selected.error && (
              <div className="sent-error-detail" role="alert">
                <strong>Delivery error:</strong> {selected.error}
              </div>
            )}
            <pre>{selected.body}</pre>
          </article>
        ) : (
          <>
            <form className="sent-search" onSubmit={e => { e.preventDefault(); setOffset(0); setQuery(search.trim()); }}>
              <input
                aria-label="Search sent emails"
                placeholder="Search recipient, subject or message"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              <button className="quiet" type="submit">Search</button>
              {(search || query) && (
                <button className="quiet" type="button" onClick={() => { setSearch(''); setQuery(''); setOffset(0); }}>
                  Clear
                </button>
              )}
            </form>
            <div className="sent-filter-bar">
              <div className="settings-tabs" aria-label="Email status filters">
                {SENT_FILTERS.map(([value, label, countKey]) => (
                  <button
                    key={value}
                    type="button"
                    className={filter === value ? 'active' : ''}
                    aria-pressed={filter === value}
                    onClick={() => { setFilter(value); setOffset(0); }}
                  >
                    {label} ({Number(counts[countKey] || 0)})
                  </button>
                ))}
              </div>
              <span>{data.total} {data.total === 1 ? 'message' : 'messages'}</span>
            </div>
            {loading ? (
              <p role="status" className="empty">Loading outgoing emails...</p>
            ) : error ? null : !data.emails.length ? (
              <div className="empty sent-empty">
                <Send size={30} />
                <h2>{query || filter ? 'No matching emails' : 'No outgoing emails yet'}</h2>
                <p>{query || filter ? 'Try another search term or status filter.' : 'Use Compose to queue a message, then send it right from here.'}</p>
                {!query && !filter && onCompose && (
                  <button type="button" className="primary" onClick={onCompose}>
                    <Send size={15} />
                    Compose email
                  </button>
                )}
              </div>
            ) : (
              <div className="sent-list">
                {data.emails.map(email => (
                  <button
                    key={email.id}
                    type="button"
                    className="sent-row"
                    onClick={() => setSelected(email)}
                  >
                    <span className="sent-recipient">To: {email.recipient}</span>
                    <span className="sent-subject">
                      <strong>{email.subject || '(No subject)'}</strong>
                      <small>{email.error ? `Error: ${email.error}` : email.body}</small>
                    </span>
                    <span className={`sent-status-badge ${statusTone(email.status)}`}>{statusLabel(email.status)}</span>
                    <time>{formatDate(email.sent_at || email.created_at)}</time>
                  </button>
                ))}
              </div>
            )}
            <div className="sent-pagination">
              <span>{data.total ? `${offset + 1}-${Math.min(offset + 25, data.total)} of ${data.total}` : '0 messages'}</span>
              <button type="button" className="quiet" disabled={loading || offset === 0} onClick={() => setOffset(v => Math.max(0, v - 25))}>Previous</button>
              <button type="button" className="quiet" disabled={loading || offset + 25 >= data.total} onClick={() => setOffset(v => v + 25)}>Next</button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
