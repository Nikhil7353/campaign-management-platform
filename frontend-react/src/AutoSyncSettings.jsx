import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import './auto-sync.css';

const PRESETS = [
  { label: '1 min', seconds: 60 },
  { label: '5 min', seconds: 300 },
  { label: '15 min', seconds: 900 },
  { label: '30 min', seconds: 1800 },
  { label: '1 hour', seconds: 3600 },
];

function formatTimestamp(value) {
  if (!value) return 'Never';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function formatInterval(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (value % 3600 === 0) {
    const hours = value / 3600;
    return `${hours} ${hours === 1 ? 'hour' : 'hours'}`;
  }
  if (value % 60 === 0) {
    const minutes = value / 60;
    return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
  }
  return `${value} seconds`;
}

export default function AutoSyncSettings({ api }) {
  const [status, setStatus] = useState(null);
  const [enabled, setEnabled] = useState(false);
  const [intervalSeconds, setIntervalSeconds] = useState('300');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const dirtyRef = useRef(false);

  const loadStatus = useCallback(async ({ syncForm = false, silent = false } = {}) => {
    if (!silent) {
      setLoading(true);
      setError('');
    }
    try {
      const data = await api('/settings/sync');
      setStatus(data);
      if (syncForm || !dirtyRef.current) {
        setEnabled(Boolean(data.enabled));
        setIntervalSeconds(String(data.interval_seconds ?? 300));
        dirtyRef.current = false;
      }
    } catch (err) {
      if (!silent) setError(`Could not load auto-sync settings: ${err.message}`);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadStatus({ syncForm: true });
    const timer = window.setInterval(() => loadStatus({ silent: true }), 15000);
    return () => window.clearInterval(timer);
  }, [loadStatus]);

  async function handleSave(event) {
    event.preventDefault();
    const parsedInterval = Number(intervalSeconds);
    const min = status?.min_interval_seconds ?? 30;
    const max = status?.max_interval_seconds ?? 86400;
    if (!Number.isInteger(parsedInterval) || parsedInterval < min || parsedInterval > max) {
      setError(`Interval must be a whole number between ${min} and ${max} seconds.`);
      return;
    }
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const updated = await api('/settings/sync', {
        method: 'POST',
        body: JSON.stringify({ enabled, interval_seconds: parsedInterval }),
      });
      setStatus(updated);
      setEnabled(Boolean(updated.enabled));
      setIntervalSeconds(String(updated.interval_seconds));
      dirtyRef.current = false;
      setNotice(
        updated.enabled
          ? `Auto-sync enabled (every ${formatInterval(updated.interval_seconds)}).`
          : 'Auto-sync disabled.'
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleManualSync() {
    setSyncing(true);
    setError('');
    setNotice('');
    try {
      const result = await api('/settings/sync/run', { method: 'POST' });
      if (result.sync_settings) {
        setStatus(result.sync_settings);
      } else {
        await loadStatus({ silent: true });
      }
      setNotice(`Manual sync completed: ${result.new_count} new message(s) (${result.total_inbox} total in inbox).`);
    } catch (err) {
      setError(`Manual sync failed: ${err.message}`);
      await loadStatus({ silent: true });
    } finally {
      setSyncing(false);
    }
  }

  const parsedInput = Number(intervalSeconds);
  const currentStatus = status?.is_running || syncing ? 'running' : (status?.last_status || 'idle');
  const hasUnsavedChanges = Boolean(
    status && (enabled !== Boolean(status.enabled) || parsedInput !== Number(status.interval_seconds))
  );

  return (
    <div className="sync-settings-grid">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Inbox Auto-Sync</h2>
            <p className="panel-subtitle">Schedule automatic IMAP inbox checks or trigger a manual sync anytime.</p>
          </div>
          <button
            type="button"
            className="quiet"
            disabled={loading || syncing}
            onClick={() => loadStatus({ syncForm: !dirtyRef.current })}
          >
            <RefreshCw size={15} className={loading ? 'sync-spin' : ''} />
            Refresh
          </button>
        </div>

        <form className="settings-form" onSubmit={handleSave}>
          <div className="sync-toggle-row">
            <div>
              <strong>Automatic inbox sync</strong>
              <small>Runs in the FastAPI background scheduler when enabled.</small>
            </div>
            <label className="sync-switch">
              <input
                type="checkbox"
                role="switch"
                checked={enabled}
                disabled={loading || saving}
                onChange={e => {
                  dirtyRef.current = true;
                  setEnabled(e.target.checked);
                  setNotice('');
                }}
              />
              <span className="sync-switch-track" aria-hidden="true" />
              <span>{enabled ? 'On' : 'Off'}</span>
            </label>
          </div>

          <label>
            Sync interval (seconds)
            <input
              type="number"
              min={status?.min_interval_seconds ?? 30}
              max={status?.max_interval_seconds ?? 86400}
              step={1}
              required
              disabled={loading || saving}
              value={intervalSeconds}
              onChange={e => {
                dirtyRef.current = true;
                setIntervalSeconds(e.target.value);
                setNotice('');
              }}
            />
            <span className="muted">
              Current selection: {formatInterval(parsedInput)} (allowed: {status?.min_interval_seconds ?? 30}s – {status?.max_interval_seconds ?? 86400}s)
            </span>
          </label>

          <div className="sync-presets" role="group" aria-label="Interval presets">
            {PRESETS.map(preset => (
              <button
                key={preset.seconds}
                type="button"
                disabled={loading || saving}
                className={parsedInput === preset.seconds ? 'active' : ''}
                onClick={() => {
                  dirtyRef.current = true;
                  setIntervalSeconds(String(preset.seconds));
                  setNotice('');
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {error && <div className="sync-error-box" role="alert">{error}</div>}
          {notice && <p className="success" role="status">{notice}</p>}

          <div className="sync-actions">
            <button type="submit" className="primary" disabled={loading || saving || !hasUnsavedChanges}>
              {saving ? 'Saving...' : 'Save sync settings'}
            </button>
            <button
              type="button"
              className="quiet"
              disabled={loading || syncing || status?.is_running}
              onClick={handleManualSync}
            >
              <RefreshCw size={15} className={syncing || status?.is_running ? 'sync-spin' : ''} />
              {syncing || status?.is_running ? 'Syncing...' : 'Sync now'}
            </button>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Last Sync Status</h2>
            <p className="panel-subtitle">Live scheduler state and most recent mailbox sync outcome.</p>
          </div>
          <span className={`sync-badge ${currentStatus}`}>{currentStatus}</span>
        </div>

        <div className="sync-status-list">
          <div className="sync-status-item">
            <span>Scheduler</span>
            <strong>
              {status?.enabled ? `Active · every ${formatInterval(status.interval_seconds)}` : 'Paused (Off)'}
            </strong>
          </div>
          <div className="sync-status-item">
            <span>IMAP configuration</span>
            <strong>{status ? (status.imap_configured ? 'Configured' : 'Not configured') : 'Checking...'}</strong>
          </div>
          <div className="sync-status-item">
            <span>Last sync time</span>
            <strong>{formatTimestamp(status?.last_sync_at)}</strong>
          </div>
          <div className="sync-status-item">
            <span>Last trigger</span>
            <strong>{status?.last_trigger ? status.last_trigger.toUpperCase() : '—'}</strong>
          </div>
          <div className="sync-status-item">
            <span>Last sync result</span>
            <strong>
              {status?.last_sync_at
                ? `${status.last_new_count} new · ${status.last_total_inbox} in inbox`
                : 'No sync recorded yet'}
            </strong>
          </div>
          <div className="sync-status-item">
            <span>Next scheduled sync</span>
            <strong>{status?.enabled ? formatTimestamp(status?.next_sync_at) : 'Off'}</strong>
          </div>
        </div>

        {status && !status.imap_configured && (
          <p className="sync-hint">
            Set <code>IMAP_HOST</code>, <code>IMAP_USERNAME</code>, and <code>IMAP_PASSWORD</code> in your backend <code>.env</code> to enable mailbox synchronization.
          </p>
        )}

        {status?.last_error && (
          <div className="sync-error-box">
            <strong>Last error:</strong> {status.last_error}
          </div>
        )}
      </section>
    </div>
  );
}
