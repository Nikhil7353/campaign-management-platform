import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardPaste, FileSpreadsheet, Link2, RefreshCw, Send, Trash2, Upload } from 'lucide-react';
import './sheets.css';

const formatDate = value => {
  if (!value) return '';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
};

const pickColumn = (columns, patterns) => {
  for (const pattern of patterns) {
    const match = columns.find(col => pattern.test(col.toLowerCase()));
    if (match) return match;
  }
  return '';
};

const renderTemplatePreview = (template, row) => {
  if (!template || !row) return '';
  const lower = Object.fromEntries(Object.entries(row).map(([k, v]) => [k.trim().toLowerCase(), String(v ?? '').trim()]));
  return template.replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_, token) => {
    const clean = token.trim();
    if (clean in row && row[clean] != null) return String(row[clean]).trim();
    return lower[clean.toLowerCase()] ?? '';
  }).trim();
};

export default function SheetsWorkspace({ api, whatsapp = false, go, onQueued }) {
  const [uploads, setUploads] = useState([]);
  const [googleStatus, setGoogleStatus] = useState(null);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [offset, setOffset] = useState(0);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [importMode, setImportMode] = useState('file');
  const [googleUrl, setGoogleUrl] = useState('');
  const [googleTitle, setGoogleTitle] = useState('');
  const [pasteTitle, setPasteTitle] = useState('');
  const [pasteText, setPasteText] = useState('');
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [queuing, setQueuing] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [queueResult, setQueueResult] = useState(null);

  const [mapping, setMapping] = useState({
    recipient_column: '',
    cc_column: '',
    bcc_column: '',
    subject_column: '',
    subject_template: '',
    body_column: '',
    body_template: '',
    message_column: '',
    message_template: '',
  });

  const loadLibrary = useCallback(async ({ preferId = '' } = {}) => {
    setLoadingList(true);
    setError('');
    try {
      const [sheetsRes, gRes] = await Promise.all([
        api('/sheets'),
        api('/google/status').catch(() => null),
      ]);
      const list = sheetsRes.uploads || [];
      setUploads(list);
      if (gRes) setGoogleStatus(gRes);
      setSelectedId(current => {
        if (preferId && list.some(item => item.id === preferId)) return preferId;
        if (current && list.some(item => item.id === current)) return current;
        return list[0]?.id || '';
      });
    } catch (err) {
      setError(`Could not load sheets: ${err.message}`);
    } finally {
      setLoadingList(false);
    }
  }, [api]);

  useEffect(() => {
    loadLibrary();
  }, [loadLibrary]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    setLoadingDetail(true);
    api(`/sheets/${selectedId}?limit=25&offset=${offset}`)
      .then(res => {
        if (!active) return;
        setDetail(res);
        const cols = res.columns || [];
        if (offset === 0) {
          setMapping(prev => {
            const keepIfValid = col => (col && cols.includes(col) ? col : '');
            const defaultRecipient = whatsapp
              ? pickColumn(cols, [/phone/, /whatsapp/, /mobile/, /number/, /contact/])
              : pickColumn(cols, [/email/, /e-mail/, /mail/, /recipient/]);
            const defaultSubject = pickColumn(cols, [/subject/, /title/, /headline/]);
            const defaultBody = pickColumn(cols, [/body/, /message/, /content/, /text/]);
            return {
              ...prev,
              recipient_column: keepIfValid(prev.recipient_column) || defaultRecipient || cols[0] || '',
              cc_column: keepIfValid(prev.cc_column),
              bcc_column: keepIfValid(prev.bcc_column),
              subject_column: keepIfValid(prev.subject_column) || defaultSubject,
              body_column: keepIfValid(prev.body_column) || defaultBody,
              message_column: keepIfValid(prev.message_column) || defaultBody,
            };
          });
        }
      })
      .catch(err => {
        if (active) setError(`Could not load sheet rows: ${err.message}`);
      })
      .finally(() => {
        if (active) setLoadingDetail(false);
      });
    return () => { active = false; };
  }, [api, selectedId, offset, whatsapp]);

  async function uploadFile(file) {
    if (!file) return;
    setUploading(true);
    setError('');
    setNotice('');
    setQueueResult(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const result = await api('/sheets/upload', { method: 'POST', body: form });
      setNotice(`Uploaded ${result.filename}: ${result.rows} row(s) stored.`);
      setOffset(0);
      setMapping({
        recipient_column: '',
        cc_column: '',
        bcc_column: '',
        subject_column: '',
        subject_template: '',
        body_column: '',
        body_template: '',
        message_column: '',
        message_template: '',
      });
      await loadLibrary({ preferId: result.id });
    } catch (err) {
      setError(`Upload failed: ${err.message}`);
    } finally {
      setUploading(false);
    }
  }

  function resetMappingState() {
    setOffset(0);
    setMapping({
      recipient_column: '',
      cc_column: '',
      bcc_column: '',
      subject_column: '',
      subject_template: '',
      body_column: '',
      body_template: '',
      message_column: '',
      message_template: '',
    });
  }

  async function handleGoogleUrlImport(event) {
    event.preventDefault();
    if (!googleUrl.trim()) return;
    setUploading(true);
    setError('');
    setNotice('');
    setQueueResult(null);
    try {
      const result = await api('/sheets/import-google-url', {
        method: 'POST',
        body: JSON.stringify({ url: googleUrl.trim(), title: googleTitle.trim() }),
      });
      setNotice(`Imported Google Sheet "${result.filename}": ${result.rows} row(s) loaded.`);
      setGoogleUrl('');
      setGoogleTitle('');
      resetMappingState();
      await loadLibrary({ preferId: result.id });
    } catch (err) {
      setError(`Google Sheet import failed: ${err.message}`);
    } finally {
      setUploading(false);
    }
  }

  async function handlePasteImport(event) {
    event.preventDefault();
    if (!pasteText.trim()) return;
    setUploading(true);
    setError('');
    setNotice('');
    setQueueResult(null);
    try {
      const result = await api('/sheets/import-paste', {
        method: 'POST',
        body: JSON.stringify({ title: pasteTitle.trim(), text: pasteText }),
      });
      setNotice(`Imported pasted data "${result.filename}": ${result.rows} row(s) loaded.`);
      setPasteTitle('');
      setPasteText('');
      resetMappingState();
      await loadLibrary({ preferId: result.id });
    } catch (err) {
      setError(`Paste import failed: ${err.message}`);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(sheet) {
    setError('');
    setNotice('');
    try {
      await api(`/sheets/${sheet.id}`, { method: 'DELETE' });
      setNotice(`Removed ${sheet.title}.`);
      if (selectedId === sheet.id) {
        setSelectedId('');
        setOffset(0);
      }
      await loadLibrary();
    } catch (err) {
      setError(`Could not delete sheet: ${err.message}`);
    }
  }

  async function handleGoogleConnect() {
    setGoogleBusy(true);
    setError('');
    try {
      const res = await api('/google/connect', { method: 'POST' });
      if (res.authorization_url) {
        window.open(res.authorization_url, '_blank', 'noopener,noreferrer');
        setNotice('Google authorization opened in a new tab.');
      }
    } catch (err) {
      setError(`Google Sheets connection failed: ${err.message}`);
    } finally {
      setGoogleBusy(false);
    }
  }

  async function handleGoogleDisconnect() {
    setGoogleBusy(true);
    setError('');
    try {
      await api('/google/disconnect', { method: 'POST' });
      setNotice('Google Sheets disconnected.');
      await loadLibrary();
    } catch (err) {
      setError(`Could not disconnect Google Sheets: ${err.message}`);
    } finally {
      setGoogleBusy(false);
    }
  }

  async function handleQueueCampaign(event) {
    event.preventDefault();
    if (!selectedId) return;
    setQueuing(true);
    setError('');
    setNotice('');
    setQueueResult(null);
    try {
      const body = whatsapp
        ? {
            channel: 'whatsapp',
            recipient_column: mapping.recipient_column,
            message_column: mapping.message_column,
            message_template: mapping.message_template,
          }
        : {
            channel: 'email',
            recipient_column: mapping.recipient_column,
            cc_column: mapping.cc_column,
            bcc_column: mapping.bcc_column,
            subject_column: mapping.subject_column,
            subject_template: mapping.subject_template,
            body_column: mapping.body_column,
            body_template: mapping.body_template,
          };
      const result = await api(`/sheets/${selectedId}/queue`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setQueueResult(result);
      setNotice(
        `Queued ${result.queued} ${whatsapp ? 'WhatsApp' : 'email'} message(s)` +
          (result.already_queued ? ` · ${result.already_queued} duplicate(s) skipped` : '') +
          (result.skipped ? ` · ${result.skipped} invalid row(s) skipped` : '') +
          '.'
      );
      if (result.queued > 0) onQueued?.();
    } catch (err) {
      setError(`Could not queue campaign: ${err.message}`);
    } finally {
      setQueuing(false);
    }
  }

  const columns = detail?.columns || [];
  const sampleRow = detail?.rows?.[0] || null;
  const previewRecipient = sampleRow && mapping.recipient_column ? String(sampleRow[mapping.recipient_column] ?? '').trim() : '';
  const previewSubject = sampleRow
    ? (mapping.subject_template.trim()
        ? renderTemplatePreview(mapping.subject_template, sampleRow)
        : String(sampleRow[mapping.subject_column] ?? '').trim())
    : '';
  const previewBody = sampleRow
    ? (whatsapp
        ? (mapping.message_template.trim()
            ? renderTemplatePreview(mapping.message_template, sampleRow)
            : String(sampleRow[mapping.message_column] ?? '').trim())
        : (mapping.body_template.trim()
            ? renderTemplatePreview(mapping.body_template, sampleRow)
            : String(sampleRow[mapping.body_column] ?? '').trim()))
    : '';

  const insertToken = (field, col) => {
    setMapping(prev => ({
      ...prev,
      [field]: `${prev[field]}${prev[field] && !prev[field].endsWith(' ') ? ' ' : ''}{{${col}}}`,
    }));
  };

  const googleBadgeClass = googleStatus?.connected
    ? 'connected'
    : googleStatus?.configured
      ? 'ready'
      : 'unconfigured';
  const googleBadgeLabel = googleStatus?.connected
    ? 'Connected'
    : googleStatus?.configured
      ? 'Ready to connect'
      : 'Not configured';

  return (
    <section className="sheets-workspace">
      <div className="page-header">
        <div>
          <div className="eyebrow">{whatsapp ? 'WHATSAPP CAMPAIGNS' : 'EMAIL CAMPAIGNS'}</div>
          <h1>Sheets</h1>
          <p className="muted">
            Import CSV or XLSX contact lists, preview rows, map fields, and queue {whatsapp ? 'WhatsApp' : 'email'} campaigns.
          </p>
        </div>
        <button type="button" className="quiet" disabled={loadingList || uploading} onClick={() => loadLibrary()}>
          <RefreshCw size={15} />
          Refresh
        </button>
      </div>

      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && (
        <div className="success" role="status">
          <span>{notice}</span>
          {queueResult?.queued > 0 && go && (
            <button
              type="button"
              className="text-button"
              style={{ marginLeft: 12 }}
              onClick={() => go(whatsapp ? 'wa-sent' : 'sent')}
            >
              Open {whatsapp ? 'WhatsApp Sent' : 'Sent & Queue'} -&gt;
            </button>
          )}
          {queueResult?.errors?.length > 0 && (
            <ul className="sheets-error-list">
              {queueResult.errors.map((item, idx) => <li key={idx}>{item}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="sheets-top-grid">
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Import spreadsheet</h2>
              <p className="panel-subtitle">Upload CSV/XLSX, paste a Google Sheets link, or paste copied cells</p>
            </div>
          </div>

          <div className="settings-tabs sheets-import-tabs" role="tablist" aria-label="Import source">
            <button
              type="button"
              role="tab"
              aria-selected={importMode === 'file'}
              className={importMode === 'file' ? 'active' : ''}
              onClick={() => setImportMode('file')}
            >
              Upload File
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={importMode === 'google-url'}
              className={importMode === 'google-url' ? 'active' : ''}
              onClick={() => setImportMode('google-url')}
            >
              Google Sheets Link
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={importMode === 'paste'}
              className={importMode === 'paste' ? 'active' : ''}
              onClick={() => setImportMode('paste')}
            >
              Paste Rows
            </button>
          </div>

          {importMode === 'file' && (
            <div
              className={`sheets-dropzone ${dragging ? 'dragging' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => {
                e.preventDefault();
                setDragging(false);
                const file = e.dataTransfer.files?.[0];
                if (file) uploadFile(file);
              }}
            >
              <Upload size={28} />
              <b>{uploading ? 'Uploading spreadsheet...' : 'Drop a CSV or XLSX file here'}</b>
              <span>Column headers in row 1 are auto-detected for campaign mapping</span>
              <label className="primary">
                {uploading ? 'Uploading...' : 'Select file'}
                <input
                  type="file"
                  accept=".csv,.xlsx"
                  disabled={uploading}
                  onChange={e => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    if (file) uploadFile(file);
                  }}
                />
              </label>
            </div>
          )}

          {importMode === 'google-url' && (
            <form className="sheets-form" onSubmit={handleGoogleUrlImport}>
              <label>
                Google Sheets URL or Spreadsheet ID *
                <input
                  required
                  type="text"
                  placeholder="https://docs.google.com/spreadsheets/d/1AbC.../edit#gid=0"
                  disabled={uploading}
                  value={googleUrl}
                  onChange={e => setGoogleUrl(e.target.value)}
                />
              </label>
              <label>
                Custom sheet name (optional)
                <input
                  type="text"
                  placeholder="e.g. November Leads"
                  disabled={uploading}
                  value={googleTitle}
                  onChange={e => setGoogleTitle(e.target.value)}
                />
              </label>
              <p className="muted" style={{ margin: 0 }}>
                Works directly if the Google Sheet is shared as <strong>Anyone with the link (Viewer)</strong>, or when Google OAuth is connected. Specific tab <code>#gid=...</code> is supported.
              </p>
              <div className="sheets-queue-actions">
                <button type="submit" className="primary" disabled={uploading || !googleUrl.trim()}>
                  <Link2 size={15} />
                  {uploading ? 'Importing...' : 'Import Google Sheet'}
                </button>
              </div>
            </form>
          )}

          {importMode === 'paste' && (
            <form className="sheets-form" onSubmit={handlePasteImport}>
              <label>
                Sheet name (optional)
                <input
                  type="text"
                  placeholder="e.g. Pasted Google Sheet"
                  disabled={uploading}
                  value={pasteTitle}
                  onChange={e => setPasteTitle(e.target.value)}
                />
              </label>
              <label>
                Paste copied cells from Google Sheets or CSV *
                <textarea
                  required
                  className="sheets-paste-area"
                  placeholder={"name\temail\tsubject\nAsha\tasha@example.com\tHello"}
                  disabled={uploading}
                  value={pasteText}
                  onChange={e => setPasteText(e.target.value)}
                />
              </label>
              <p className="muted" style={{ margin: 0 }}>
                Select rows (including header row) in Google Sheets, press <code>Ctrl+C</code>, and paste here with <code>Ctrl+V</code>. Both tab-separated and comma-separated rows work automatically.
              </p>
              <div className="sheets-queue-actions">
                <button type="submit" className="primary" disabled={uploading || !pasteText.trim()}>
                  <ClipboardPaste size={15} />
                  {uploading ? 'Importing...' : 'Import Pasted Rows'}
                </button>
              </div>
            </form>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Google Sheets &amp; Workflow</h2>
              <p className="panel-subtitle">Connect Google OAuth or use direct file uploads</p>
            </div>
          </div>

          <div className="sheets-google-card">
            <div className="sheets-google-head">
              <strong>Google Sheets OAuth</strong>
              <span className={`sheets-status-pill ${googleBadgeClass}`}>{googleBadgeLabel}</span>
            </div>
            {googleStatus?.connected ? (
              <div className="sheets-queue-actions">
                <span className="muted">Google account linked for spreadsheet access.</span>
                <button type="button" className="quiet" disabled={googleBusy} onClick={handleGoogleDisconnect}>
                  Disconnect
                </button>
              </div>
            ) : googleStatus?.configured ? (
              <div className="sheets-queue-actions">
                <button type="button" className="quiet" disabled={googleBusy} onClick={handleGoogleConnect}>
                  <Link2 size={15} />
                  {googleBusy ? 'Opening...' : 'Connect Google Sheets'}
                </button>
              </div>
            ) : (
              <span className="muted">
                Optional: set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in backend <code>.env</code> to enable Google OAuth. Direct CSV/XLSX upload works immediately.
              </span>
            )}
          </div>

          <div className="workflow">
            <p><span>1</span> Upload a CSV or XLSX contact list</p>
            <p><span>2</span> Map recipient &amp; message fields with <code>{'{{column}}'}</code> tokens</p>
            <p><span>3</span> Preview row 1 and queue messages to {whatsapp ? 'WhatsApp Sent' : 'Sent & Queue'}</p>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Uploaded spreadsheets ({uploads.length})</h2>
            <p className="panel-subtitle">Select a spreadsheet to preview records and queue a campaign</p>
          </div>
        </div>
        {loadingList ? (
          <p className="empty" role="status">Loading uploaded sheets...</p>
        ) : !uploads.length ? (
          <div className="empty">
            <FileSpreadsheet size={30} />
            <span>No spreadsheets uploaded yet. Upload a CSV or XLSX file above to get started.</span>
          </div>
        ) : (
          <div className="sheets-list">
            {uploads.map(sheet => (
              <div key={sheet.id} className={`sheets-item ${selectedId === sheet.id ? 'active' : ''}`}>
                <button
                  type="button"
                  className="sheets-item-main"
                  onClick={() => { setSelectedId(sheet.id); setOffset(0); setQueueResult(null); }}
                >
                  <strong>{sheet.title}</strong>
                  <small>
                    {sheet.row_count} {sheet.row_count === 1 ? 'row' : 'rows'} · {(sheet.columns || []).length} columns · Uploaded {formatDate(sheet.created_at)}
                  </small>
                </button>
                <div className="sheets-item-actions">
                  <button
                    type="button"
                    className="quiet small"
                    onClick={() => { setSelectedId(sheet.id); setOffset(0); setQueueResult(null); }}
                  >
                    {selectedId === sheet.id ? 'Selected' : 'Open'}
                  </button>
                  <button
                    type="button"
                    className="sheets-danger-btn"
                    aria-label={`Delete ${sheet.title}`}
                    onClick={() => handleDelete(sheet)}
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {detail && (
        <>
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Map &amp; Queue Campaign — {detail.title}</h2>
                <p className="panel-subtitle">
                  {detail.total} total {detail.total === 1 ? 'record' : 'records'} · Use column mappings or <code>{'{{Column}}'}</code> templates
                </p>
              </div>
            </div>

            <div className="sheets-mapper-grid">
              <form className="sheets-form" onSubmit={handleQueueCampaign}>
                <div className="sheets-form-row">
                  <label>
                    {whatsapp ? 'Phone number column *' : 'Recipient email column *'}
                    <select
                      required
                      value={mapping.recipient_column}
                      onChange={e => setMapping(prev => ({ ...prev, recipient_column: e.target.value }))}
                    >
                      <option value="">Select column...</option>
                      {columns.map(col => <option key={col} value={col}>{col}</option>)}
                    </select>
                  </label>
                  {!whatsapp && (
                    <label>
                      Subject column (optional if using template)
                      <select
                        value={mapping.subject_column}
                        onChange={e => setMapping(prev => ({ ...prev, subject_column: e.target.value }))}
                      >
                        <option value="">Use subject template below</option>
                        {columns.map(col => <option key={col} value={col}>{col}</option>)}
                      </select>
                    </label>
                  )}
                </div>

                {!whatsapp && (
                  <>
                    <div className="sheets-form-row">
                      <label>
                        Cc column (optional)
                        <select
                          value={mapping.cc_column}
                          onChange={e => setMapping(prev => ({ ...prev, cc_column: e.target.value }))}
                        >
                          <option value="">None</option>
                          {columns.map(col => <option key={col} value={col}>{col}</option>)}
                        </select>
                      </label>
                      <label>
                        Bcc column (optional)
                        <select
                          value={mapping.bcc_column}
                          onChange={e => setMapping(prev => ({ ...prev, bcc_column: e.target.value }))}
                        >
                          <option value="">None</option>
                          {columns.map(col => <option key={col} value={col}>{col}</option>)}
                        </select>
                      </label>
                    </div>

                    <label>
                      Subject template (overrides Subject column when filled)
                      <input
                        placeholder="e.g. Quick update for {{name}}"
                        value={mapping.subject_template}
                        onChange={e => setMapping(prev => ({ ...prev, subject_template: e.target.value }))}
                      />
                      <div className="sheets-token-bar">
                        <span>Insert token:</span>
                        {columns.slice(0, 8).map(col => (
                          <button key={col} type="button" onClick={() => insertToken('subject_template', col)}>
                            {`{{${col}}}`}
                          </button>
                        ))}
                      </div>
                    </label>

                    <label>
                      Body column (optional if using template)
                      <select
                        value={mapping.body_column}
                        onChange={e => setMapping(prev => ({ ...prev, body_column: e.target.value }))}
                      >
                        <option value="">Use message template below</option>
                        {columns.map(col => <option key={col} value={col}>{col}</option>)}
                      </select>
                    </label>

                    <label>
                      Message template (overrides Body column when filled)
                      <textarea
                        placeholder="Hi {{name}}, writing to follow up on..."
                        value={mapping.body_template}
                        onChange={e => setMapping(prev => ({ ...prev, body_template: e.target.value }))}
                      />
                      <div className="sheets-token-bar">
                        <span>Insert token:</span>
                        {columns.slice(0, 8).map(col => (
                          <button key={col} type="button" onClick={() => insertToken('body_template', col)}>
                            {`{{${col}}}`}
                          </button>
                        ))}
                      </div>
                    </label>
                  </>
                )}

                {whatsapp && (
                  <>
                    <label>
                      Message column (optional if using template)
                      <select
                        value={mapping.message_column}
                        onChange={e => setMapping(prev => ({ ...prev, message_column: e.target.value }))}
                      >
                        <option value="">Use message template below</option>
                        {columns.map(col => <option key={col} value={col}>{col}</option>)}
                      </select>
                    </label>

                    <label>
                      WhatsApp message template (overrides Message column when filled)
                      <textarea
                        placeholder="Hi {{name}}, ..."
                        value={mapping.message_template}
                        onChange={e => setMapping(prev => ({ ...prev, message_template: e.target.value }))}
                      />
                      <div className="sheets-token-bar">
                        <span>Insert token:</span>
                        {columns.slice(0, 8).map(col => (
                          <button key={col} type="button" onClick={() => insertToken('message_template', col)}>
                            {`{{${col}}}`}
                          </button>
                        ))}
                      </div>
                    </label>
                  </>
                )}

                <div className="sheets-queue-actions">
                  <button type="submit" className="primary" disabled={queuing || !mapping.recipient_column}>
                    <Send size={15} />
                    {queuing ? 'Queuing campaign...' : `Queue ${detail.total} ${whatsapp ? 'WhatsApp' : 'email'} message(s)`}
                  </button>
                </div>
              </form>

              <div className="sheets-preview-card">
                <h3>Live Row 1 Preview</h3>
                {sampleRow ? (
                  <>
                    <div className="sheets-preview-row">
                      <span>{whatsapp ? 'Recipient phone' : 'To'}</span>
                      <strong>{previewRecipient || '— (select recipient column)'}</strong>
                    </div>
                    {!whatsapp && (
                      <div className="sheets-preview-row">
                        <span>Subject</span>
                        <strong>{previewSubject || '— (select subject column or enter template)'}</strong>
                      </div>
                    )}
                    <div className="sheets-preview-row">
                      <span>Message</span>
                      <pre>{previewBody || '— (select body column or enter template)'}</pre>
                    </div>
                  </>
                ) : (
                  <p className="muted">No rows available to preview.</p>
                )}
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Records preview — {detail.title}</h2>
                <p className="panel-subtitle">Inspect imported contact records before queuing</p>
              </div>
            </div>
            {loadingDetail ? (
              <p className="empty" role="status">Loading rows...</p>
            ) : (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>#</th>
                        {columns.map(col => <th key={col}>{col}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.rows || []).map((row, idx) => (
                        <tr key={offset + idx}>
                          <td>{offset + idx + 1}</td>
                          {columns.map(col => <td key={col}>{row[col] || ''}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="sheets-table-foot">
                  <span>
                    {detail.total
                      ? `${offset + 1}-${Math.min(offset + 25, detail.total)} of ${detail.total} rows`
                      : '0 rows'}
                  </span>
                  <button
                    type="button"
                    className="quiet"
                    disabled={loadingDetail || offset === 0}
                    onClick={() => setOffset(v => Math.max(0, v - 25))}
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    disabled={loadingDetail || offset + 25 >= detail.total}
                    onClick={() => setOffset(v => v + 25)}
                  >
                    Next
                  </button>
                </div>
              </>
            )}
          </section>
        </>
      )}
    </section>
  );
}
