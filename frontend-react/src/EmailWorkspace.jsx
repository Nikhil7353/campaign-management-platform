import React, { useEffect, useRef, useState } from 'react';
import { Send, Minus, Maximize2, Minimize2, X, RefreshCw, ArrowLeft } from 'lucide-react';
import './email-workspace.css';

const blank = { recipient: '', cc: '', bcc: '', subject: '', body: '' };

export function FloatingComposer({ api, mode, setMode }) {
  const [form, setForm] = useState(blank);
  const [copies, setCopies] = useState({ cc: false, bcc: false });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const toRef = useRef(null);
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
      setForm(blank); setNotice(`${succeeded.length} message(s) queued. They will appear in Sent after sending.`);
    } catch (error) {
      setForm(current => ({ ...current, recipient: recipients.filter(v => !succeeded.includes(v)).join(', ') }));
      setNotice(`${succeeded.length} queued. Remaining recipients kept for retry. ${error.message}`);
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

export function SentMailbox({ api }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);
  async function load() {
    setLoading(true); setError('');
    try {
      const all = []; let offset = 0; let total = Infinity;
      while (offset < total) {
        const result = await api(`/emails?limit=100&offset=${offset}`);
        const page = result.emails || []; all.push(...page); total = result.total || 0;
        if (!page.length) break;
        offset += page.length;
      }
      setItems(all.filter(email => ['sent', 'delivered'].includes(email.status)));
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  const visible = items.filter(e => `${e.recipient} ${e.subject} ${e.body}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="sent-mailbox">
    <div className="page-header"><div><div className="eyebrow">EMAIL</div><h1>Sent</h1><p className="muted">Messages that have been sent. Pending and failed emails are not included.</p></div><button className="quiet" disabled={loading} onClick={load}><RefreshCw size={16} />Refresh</button></div>
    <div className="panel">
      {error && <p role="alert" className="error-banner">Could not load sent emails: {error}</p>}
      {selected ? <article className="sent-detail"><button className="quiet" onClick={() => setSelected(null)}><ArrowLeft size={16} />Back to Sent</button><h2>{selected.subject}</h2><p>To: {selected.recipient}</p>{selected.cc && <p>Cc: {selected.cc}</p>}{selected.bcc && <p>Bcc: {selected.bcc}</p>}<small>{selected.sent_at || selected.created_at}</small><pre>{selected.body}</pre></article> : <>
        <div className="sent-toolbar"><input aria-label="Search sent emails" placeholder="Search sent emails" value={query} onChange={e => setQuery(e.target.value)} /><span>{visible.length} messages</span></div>
        {loading ? <p role="status" className="empty">Loading sent emails...</p> : !visible.length ? <div className="empty"><Send size={30} /><h2>{query ? 'No matching emails' : 'No sent emails yet'}</h2><p>{query ? 'Try another search.' : 'Use Compose to write a message. Sent messages will appear here.'}</p></div> : <div className="sent-list">{visible.map(email => <button key={email.id} className="sent-row" onClick={() => setSelected(email)}><span>To: {email.recipient}</span><strong>{email.subject}</strong><span className="sent-snippet">{email.body}</span><time>{email.sent_at ? new Date(email.sent_at).toLocaleDateString() : ''}</time></button>)}</div>}
      </>}
    </div>
  </section>;
}
