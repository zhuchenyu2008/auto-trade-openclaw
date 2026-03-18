from __future__ import annotations

import http.cookies
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .config import public_config_dict
from .runtime import Runtime


HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TG OKX Auto Trade</title>
  <style>
    :root { --bg:#f6f2ea; --ink:#1b1d1f; --accent:#d45500; --card:#fffdf8; --line:#e7d8c6; --muted:#6f665e; }
    body{font-family:Georgia,serif;background:linear-gradient(180deg,#efe6d8, #f8f5ef 30%, #f0ebe2);color:var(--ink);margin:0}
    header{padding:20px 24px;border-bottom:1px solid var(--line);background:rgba(255,253,248,.85);position:sticky;top:0;backdrop-filter: blur(8px)}
    main{padding:20px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
    .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 10px 25px rgba(78,54,28,.08)}
    h1,h2{margin:0 0 12px 0}
    .hero{display:flex;justify-content:space-between;gap:16px;align-items:end}
    .muted{color:var(--muted)}
    .pill{display:inline-block;padding:4px 10px;border-radius:999px;background:#fff1e8;color:var(--accent);font-size:12px}
    pre{white-space:pre-wrap;font-size:12px;background:#221f1c;color:#f8f4ee;padding:12px;border-radius:12px;max-height:280px;overflow:auto}
    input,button,select,textarea{font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line)}
    button{background:var(--accent);color:white;border:none;cursor:pointer}
    form{display:grid;gap:10px}
    table{width:100%;border-collapse:collapse;font-size:13px}
    td,th{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    @media(max-width:720px){main,.grid2{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <header>
    <div class="hero">
      <div>
        <div id="webBindBox" class="pill">loading</div>
        <h1>Telegram OKX Auto Trade</h1>
        <div class="muted">Contracts only. Default leverage 20x. Global TP/SL disabled by default.</div>
      </div>
      <div id="modeBox" class="pill">loading</div>
    </div>
  </header>
  <main id="app"></main>
  <script>
    async function api(path, options={}) {
      const res = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, options));
      if (res.status === 401) { location.href = '/login'; return; }
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || ('Request failed: ' + res.status));
      }
      if (res.headers.get('content-type')?.includes('application/json')) return res.json();
      return res.text();
    }
    function esc(v){ return String(v ?? '').replace(/[&<>]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[s])); }
    function render(data) {
      window.currentState = data;
      document.getElementById('modeBox').textContent = data.config.trading.mode + ' / ' + data.config.trading.execution_mode;
      document.getElementById('webBindBox').textContent = data.wiring.web_server_active ? ('Serving ' + data.wiring.web_bind) : ('Configured ' + data.wiring.web_bind);
      const openPositions = data.positions.filter(item => Number(item?.payload?.qty || 0) > 0 && ['long', 'short'].includes(String(item?.payload?.side || '')));
      const readiness = data.readiness_checks.map(item => `<tr><td>${esc(item.name)}</td><td>${esc(item.status)}</td><td>${esc(item.detail)}</td></tr>`).join('');
      const logs = data.logs.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.level)}</td><td>${esc(item.category)}</td><td>${esc(item.message)}</td></tr>`).join('');
      const auditLogs = data.audit_logs.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.category)}</td><td>${esc(item.message)}</td></tr>`).join('');
      const orders = data.orders.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.symbol)}</td><td>${esc(item.action)}</td><td>${esc(item.status)}</td><td>${esc(item.mode)}</td></tr>`).join('');
      const positions = openPositions.length
        ? openPositions.map(item => `<tr><td>${esc(item.symbol)}</td><td>${esc(item.payload.side)}</td><td>${esc(item.payload.qty)}</td><td>${esc(item.payload.leverage)}</td><td>${esc(item.payload.unrealized_pnl)}</td><td>${esc(JSON.stringify(item.payload.protection || {}))}</td><td><button type="button" data-close-symbol="${esc(item.symbol)}">Close</button></td></tr>`).join('')
        : '<tr><td colspan="7">No open positions.</td></tr>';
      const channels = data.config.telegram.channels.map(ch => `<tr><td>${esc(ch.name)}</td><td>${esc(ch.source_type)}</td><td>${esc(ch.chat_id || ch.channel_username)}</td><td>${esc(ch.enabled)}</td><td>${esc(ch.reconcile_interval_seconds)}</td><td><button type="button" data-channel-action="edit" data-channel-id="${esc(ch.id)}">Edit</button> <button type="button" data-channel-action="toggle" data-channel-id="${esc(ch.id)}">${ch.enabled ? 'Disable' : 'Enable'}</button> <button type="button" data-channel-action="remove" data-channel-id="${esc(ch.id)}">Remove</button></td></tr>`).join('');
      const messages = data.messages.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.chat_id)}</td><td>${esc(item.message_id)} v${esc(item.version)}</td><td>${esc(item.event_type)}</td><td>${esc(item.status)}</td><td>${esc(item.payload.text || item.payload.caption || '')}</td></tr>`).join('');
      const decisions = data.ai_decisions.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.payload.symbol)}</td><td>${esc(item.payload.action)}</td><td>${esc(item.payload.confidence)}</td><td>${esc(item.payload.reason)}</td></tr>`).join('');
      const health = esc(JSON.stringify(data.health, null, 2));
      const runPaths = esc(JSON.stringify(data.run_paths, null, 2));
      const setupExamples = esc(JSON.stringify(data.run_paths.setup_examples || {}, null, 2));
      const capabilities = Object.entries(data.capabilities || {}).map(([name, item]) => `<tr><td>${esc(name)}</td><td>${esc(item.status)}</td><td>${esc(item.detail)}</td><td>${esc(item.action)}</td></tr>`).join('');
      const activationSummary = Object.entries(data.activation_summary || {}).map(([name, item]) => `<tr><td>${esc(name)}</td><td>${esc(item.status)}</td><td>${esc(item.detail)}</td><td>${esc(item.action)}</td></tr>`).join('');
      const remainingGaps = (data.remaining_gaps || []).map(item => `<tr><td>${esc(item.id)}</td><td>${esc(item.scope)}</td><td>${esc(item.status)}</td><td>${esc(item.detail)}</td><td>${esc(item.action)}</td></tr>`).join('');
      const activationChecklist = (data.run_paths.activation_checklist || []).map(item => `<tr><td>${esc(item)}</td></tr>`).join('');
      const nextSteps = (data.next_steps || []).map(item => `<tr><td>${esc(item)}</td></tr>`).join('');
      const directUseProfile = data.capabilities?.current_operating_profile || {status:'unknown', detail:'n/a', action:'n/a'};
      document.getElementById('app').innerHTML = `
        <section class="card"><h2>Dashboard</h2>
          <div class="grid2">
            <div><strong>Current Profile</strong><div>${esc(directUseProfile.status)}</div></div>
            <div><strong>Immediate Next Step</strong><div>${esc(directUseProfile.action)}</div></div>
            <div><strong>Verification</strong><div>${esc(data.verification_status || 'unknown')}</div></div>
            <div><strong>Remaining Gaps</strong><div>${esc((data.remaining_gaps || []).length)}</div></div>
            <div><strong>AI Provider</strong><div>${esc(data.config.ai.provider)}</div></div>
            <div><strong>AI Model</strong><div>${esc(data.config.ai.model)} / ${esc(data.config.ai.thinking)}</div></div>
            <div><strong>Default Leverage</strong><div>${esc(data.config.trading.default_leverage)}x</div></div>
            <div><strong>Global TP/SL</strong><div>${esc(data.config.trading.global_tp_sl_enabled)}</div></div>
            <div><strong>PnL</strong><div>${esc(data.dashboard.total_unrealized_pnl)} unrealized / ${esc(data.dashboard.total_realized_pnl)} realized</div></div>
            <div><strong>Positions</strong><div>${esc(data.dashboard.positions_count)} open / ${esc(data.dashboard.tracked_symbols_count || data.positions.length)} tracked</div></div>
            <div><strong>Runtime</strong><div>${esc(data.operator_state.paused ? 'paused' : data.health.trading_runtime.status)}</div></div>
            <div><strong>Last Reconcile</strong><div>${esc(data.operator_state.last_reconcile.detail)}</div></div>
            <div><strong>OKX Path</strong><div>${esc(data.wiring.okx_execution_path)}</div></div>
            <div><strong>Configured OKX Actions</strong><div>${esc((data.wiring.configured_okx_supported_actions || []).join(', ') || 'n/a')}</div></div>
            <div><strong>Manual Inject</strong><div>${esc(data.wiring.manual_signal_default_path)} default / ${esc(data.wiring.manual_signal_configured_path)} configured</div></div>
            <div><strong>Topic Target</strong><div>${esc(data.wiring.topic_target || 'not configured')}</div></div>
            <div><strong>Topic Link</strong><div>${esc(data.run_paths.topic_target_link || 'n/a')}</div></div>
            <div><strong>Topic Source</strong><div>${esc(data.wiring.topic_target_source || 'n/a')}</div></div>
            <div><strong>Topic Delivery</strong><div>${esc(data.wiring.topic_delivery_state)}${data.wiring.topic_delivery_verified ? ' / verified' : ''}</div></div>
            <div><strong>Topic Detail</strong><div>${esc(data.wiring.topic_delivery_detail)}</div></div>
            <div><strong>Topic Ingress</strong><div>${esc(data.wiring.operator_command_ingress)}</div></div>
            <div><strong>Telegram Watcher</strong><div>${esc(data.wiring.telegram_watch_mode)}</div></div>
            <div><strong>Web Bind</strong><div>${esc(data.wiring.web_bind)}</div></div>
            <div><strong>Enabled Channels</strong><div>${esc(data.wiring.enabled_channel_ids.join(', ') || 'none')}</div></div>
          </div>
          <div class="muted">${esc(directUseProfile.detail)}</div>
          <div class="muted">${esc(data.wiring.web_restart_required ? ('Web host/port changed in config. Restart required to apply ' + data.run_paths.configured_web_login + '.') : (data.operator_state.pause_reason || data.health.trading_runtime.detail))}</div>
        </section>
        <section class="card"><h2>Activation Summary</h2><table><tr><th>Path</th><th>Status</th><th>Detail</th><th>Action</th></tr>${activationSummary}</table></section>
        <section class="card"><h2>Capabilities</h2><table><tr><th>Capability</th><th>Status</th><th>Detail</th><th>Action</th></tr>${capabilities}</table></section>
        <section class="card"><h2>Remaining Gaps</h2><table><tr><th>Gap</th><th>Scope</th><th>Status</th><th>Detail</th><th>Action</th></tr>${remainingGaps || '<tr><td colspan="5">No explicit gaps reported.</td></tr>'}</table></section>
        <section class="card"><h2>Readiness</h2><table><tr><th>Check</th><th>Status</th><th>Detail</th></tr>${readiness}</table></section>
        <section class="card"><h2>Next Steps</h2><table><tr><th>Action</th></tr>${nextSteps || '<tr><td>No next steps generated.</td></tr>'}</table></section>
        <section class="card"><h2>Activation</h2>
          <table><tr><th>Checklist</th></tr>${activationChecklist || '<tr><td>No activation checklist available.</td></tr>'}</table>
          <div class="muted">Redacted config snippets for the next local wiring step.</div>
          <pre>${setupExamples}</pre>
        </section>
        <section class="card"><h2>Run Paths</h2>
          <div class="muted">Config: ${esc(data.runtime.config_path)}</div>
          <pre>${runPaths}</pre>
        </section>
        <section class="card"><h2>Direct Use Summary</h2>
          <div class="muted">Same demo-only summary written to the runtime artifact text file.</div>
          <pre>${esc(data.direct_use_text || '')}</pre>
        </section>
        <section class="card"><h2>Trading Mode</h2>
          <form id="modeForm">
            <div class="grid2">
              <select name="mode">
                <option ${data.config.trading.mode==='observe'?'selected':''}>observe</option>
                <option ${data.config.trading.mode==='demo'?'selected':''}>demo</option>
              </select>
              <select name="execution_mode">
                <option ${data.config.trading.execution_mode==='automatic'?'selected':''}>automatic</option>
                <option ${data.config.trading.execution_mode==='observe'?'selected':''}>observe</option>
              </select>
            </div>
            <div class="grid2">
              <input name="default_leverage" type="number" min="1" max="125" value="${esc(data.config.trading.default_leverage)}">
              <select name="paused">
                <option value="false" ${!data.config.trading.paused?'selected':''}>running</option>
                <option value="true" ${data.config.trading.paused?'selected':''}>paused</option>
              </select>
            </div>
            <button>Save Trading Config</button>
          </form>
          <div class="grid2">
            <button type="button" id="pauseButton">Pause</button>
            <button type="button" id="resumeButton">Resume</button>
            <button type="button" id="reconcileButton">Reconcile Now</button>
            <button type="button" id="topicTestButton">Topic Smoke</button>
            <button type="button" id="resetLocalStateButton">Reset Local State</button>
          </div>
        </section>
        <section class="card"><h2>AI Settings</h2>
          <form id="aiForm">
            <div class="grid2">
              <input name="provider" value="${esc(data.config.ai.provider)}" placeholder="openclaw">
              <input name="model" value="${esc(data.config.ai.model)}" placeholder="default">
            </div>
            <div class="grid2">
              <select name="thinking">
                <option ${data.config.ai.thinking==='off'?'selected':''}>off</option>
                <option ${data.config.ai.thinking==='minimal'?'selected':''}>minimal</option>
                <option ${data.config.ai.thinking==='low'?'selected':''}>low</option>
                <option ${data.config.ai.thinking==='medium'?'selected':''}>medium</option>
                <option ${data.config.ai.thinking==='high'?'selected':''}>high</option>
                <option ${data.config.ai.thinking==='custom'?'selected':''}>custom</option>
              </select>
              <input name="timeout_seconds" type="number" min="1" value="${esc(data.config.ai.timeout_seconds)}">
            </div>
            <textarea name="system_prompt" rows="4" placeholder="strict JSON only">${esc(data.config.ai.system_prompt)}</textarea>
            <button>Save AI Config</button>
          </form>
        </section>
        <section class="card"><h2>Risk Controls</h2>
          <form id="riskForm">
            <div class="grid2">
              <select name="global_tp_sl_enabled">
                <option value="false" ${!data.config.trading.global_tp_sl_enabled?'selected':''}>disabled</option>
                <option value="true" ${data.config.trading.global_tp_sl_enabled?'selected':''}>enabled</option>
              </select>
              <input name="global_take_profit_ratio" type="number" step="0.1" value="${esc(data.config.trading.global_take_profit_ratio)}">
            </div>
            <div class="grid2">
              <input name="global_stop_loss_ratio" type="number" step="0.1" value="${esc(data.config.trading.global_stop_loss_ratio)}">
              <select name="readonly_close_only">
                <option value="false" ${!data.config.trading.readonly_close_only?'selected':''}>normal</option>
                <option value="true" ${data.config.trading.readonly_close_only?'selected':''}>close only</option>
              </select>
            </div>
            <button>Save Risk Config</button>
          </form>
        </section>
        <section class="card"><h2>Telegram Wiring</h2>
          <form id="telegramForm">
            <div class="muted">Bot token configured: ${esc(data.secret_status.telegram_bot_token_configured)}. Source: ${esc(data.secret_sources.telegram_bot_token)}. Leave blank to keep the existing token.</div>
            <input name="bot_token" value="" placeholder="set or rotate bot token">
            <label class="muted"><input name="clear_bot_token" type="checkbox" value="true"> clear the stored bot token on save</label>
            <div class="muted">Topic target accepts either `-100...:topic:...` or a `https://t.me/c/.../...` topic link.</div>
            <div class="grid2">
              <input name="report_topic" value="${esc(data.config.telegram.report_topic)}" placeholder="report topic or https://t.me/c/.../...">
              <input name="operator_target" value="${esc(data.config.telegram.operator_target)}" placeholder="operator target or https://t.me/c/.../...">
            </div>
            <div class="grid2">
              <input name="operator_thread_id" type="number" min="0" value="${esc(data.config.telegram.operator_thread_id)}" placeholder="thread id">
              <input name="poll_interval_seconds" type="number" min="1" value="${esc(data.config.telegram.poll_interval_seconds)}">
            </div>
            <button>Save Telegram Config</button>
          </form>
        </section>
        <section class="card"><h2>Operator Commands</h2>
          <div class="muted">Use these commands from the operator topic after the bot can receive topic messages, or dry-run them here first.</div>
          <div class="muted">${esc((data.run_paths.operator_command_examples || []).join('  '))}</div>
          <form id="operatorCommandForm">
            <input name="text" value="/status" placeholder="/status">
            <button>Run Operator Command</button>
          </form>
        </section>
        <section class="card"><h2>Channels</h2>
          <table><tr><th>Name</th><th>Adapter</th><th>Target</th><th>Enabled</th><th>Reconcile</th><th>Actions</th></tr>${channels}</table>
          <form id="channelForm">
            <input name="id" placeholder="channel id (leave empty to derive)">
            <input name="name" placeholder="display name">
            <div class="muted">`chat_id` accepts raw `-100...` or `https://t.me/c/.../...`; `channel_username` accepts `@name` or `https://t.me/name`.</div>
            <div class="grid2">
              <select name="source_type">
                <option value="bot_api">bot_api</option>
                <option value="mtproto">mtproto</option>
              </select>
              <input name="chat_id" placeholder="-100... or https://t.me/c/.../...">
            </div>
            <div class="grid2">
              <input name="channel_username" placeholder="@username or https://t.me/username">
              <select name="enabled">
                <option value="true">enabled</option>
                <option value="false">disabled</option>
              </select>
            </div>
            <div class="grid2">
              <input name="reconcile_interval_seconds" type="number" min="1" value="30">
              <input name="dedup_window_seconds" type="number" min="1" value="3600">
            </div>
            <textarea name="notes" rows="2" placeholder="notes"></textarea>
            <button id="channelSubmitButton">Save Channel</button>
          </form>
        </section>
        <section class="card"><h2>Demo Signal Test</h2>
          <div class="muted">Manual signal injection defaults to the simulated engine. Choose configured path only when you explicitly want an OKX demo REST order.</div>
          <form id="injectForm">
            <textarea name="text" rows="4" placeholder="LONG BTCUSDT now"></textarea>
            <div class="grid2">
              <input name="chat_id" value="-1000000000000" placeholder="chat id">
              <input name="message_id" type="number" min="1" value="1001">
            </div>
            <div class="grid2">
              <select name="event_type">
                <option value="new">new</option>
                <option value="edit">edit</option>
              </select>
              <input name="version" type="number" min="1" placeholder="auto version">
            </div>
            <select name="execution_path">
              <option value="simulated">simulated smoke</option>
              <option value="configured">configured OKX path</option>
            </select>
            <button>Run Demo Signal</button>
          </form>
        </section>
        <section class="card"><h2>Positions</h2>
          <button type="button" id="closeAllButton">Close All Positions</button>
          <table><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Lev</th><th>uPnL</th><th>Protection</th><th>Action</th></tr>${positions}</table>
        </section>
        <section class="card"><h2>Orders</h2><table><tr><th>Time</th><th>Symbol</th><th>Action</th><th>Status</th><th>Mode</th></tr>${orders}</table></section>
        <section class="card"><h2>Recent Messages</h2><table><tr><th>Time</th><th>Chat</th><th>Message</th><th>Event</th><th>Status</th><th>Text</th></tr>${messages}</table></section>
        <section class="card"><h2>AI Decisions</h2><table><tr><th>Time</th><th>Symbol</th><th>Action</th><th>Confidence</th><th>Reason</th></tr>${decisions}</table></section>
        <section class="card"><h2>Logs</h2><table><tr><th>Time</th><th>Level</th><th>Category</th><th>Message</th></tr>${logs}</table></section>
        <section class="card"><h2>Audit Logs</h2><table><tr><th>Time</th><th>Category</th><th>Message</th></tr>${auditLogs}</table></section>
        <section class="card"><h2>Health</h2><pre>${health}</pre></section>`;
      bindForms();
    }
    async function load(){ render(await api('/api/state')); }
    function setChannelForm(channel){
      const form = document.getElementById('channelForm');
      if (!form) return;
      form.elements.id.value = channel?.id || '';
      form.elements.name.value = channel?.name || '';
      form.elements.source_type.value = channel?.source_type || 'bot_api';
      form.elements.chat_id.value = channel?.chat_id || '';
      form.elements.channel_username.value = channel?.channel_username || '';
      form.elements.enabled.value = String(channel?.enabled ?? true);
      form.elements.reconcile_interval_seconds.value = channel?.reconcile_interval_seconds || 30;
      form.elements.dedup_window_seconds.value = channel?.dedup_window_seconds || 3600;
      form.elements.notes.value = channel?.notes || '';
      const submit = document.getElementById('channelSubmitButton');
      if (submit) submit.textContent = channel ? 'Update Channel' : 'Save Channel';
    }
    function bindForms(){
      document.getElementById('modeForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          await api('/api/config', {method:'POST', body: JSON.stringify({trading:{
            mode:f.get('mode'),
            execution_mode:f.get('execution_mode'),
            default_leverage:Number(f.get('default_leverage')),
            paused:f.get('paused') === 'true'
          }})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('riskForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          await api('/api/config', {method:'POST', body: JSON.stringify({trading:{
            global_tp_sl_enabled:f.get('global_tp_sl_enabled') === 'true',
            global_take_profit_ratio:Number(f.get('global_take_profit_ratio')),
            global_stop_loss_ratio:Number(f.get('global_stop_loss_ratio')),
            readonly_close_only:f.get('readonly_close_only') === 'true'
          }})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('aiForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          await api('/api/config', {method:'POST', body: JSON.stringify({ai:{
            provider:String(f.get('provider')).trim(),
            model:String(f.get('model')).trim(),
            thinking:String(f.get('thinking')),
            timeout_seconds:Number(f.get('timeout_seconds')),
            system_prompt:String(f.get('system_prompt'))
          }})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('telegramForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        const telegramPatch = {
          report_topic:String(f.get('report_topic')),
          operator_target:String(f.get('operator_target')),
          operator_thread_id:Number(f.get('operator_thread_id') || 0),
          poll_interval_seconds:Number(f.get('poll_interval_seconds'))
        };
        const botToken = String(f.get('bot_token') || '').trim();
        const clearBotToken = String(f.get('clear_bot_token') || '') === 'true';
        if (clearBotToken) {
          telegramPatch.bot_token = '';
        } else if (botToken) {
          telegramPatch.bot_token = botToken;
        }
        try {
          await api('/api/config', {method:'POST', body: JSON.stringify({telegram:telegramPatch})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('channelForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          await api('/api/channels/upsert', {method:'POST', body: JSON.stringify({
            id:String(f.get('id')),
            name:String(f.get('name')),
            source_type:String(f.get('source_type')),
            chat_id:String(f.get('chat_id')),
            channel_username:String(f.get('channel_username')),
            enabled:String(f.get('enabled')) === 'true',
            reconcile_interval_seconds:Number(f.get('reconcile_interval_seconds')),
            dedup_window_seconds:Number(f.get('dedup_window_seconds')),
            notes:String(f.get('notes'))
          })});
          e.target.reset();
          setChannelForm(null);
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.querySelectorAll('[data-channel-action]').forEach(button => {
        button.addEventListener('click', async e => {
          const channelId = e.currentTarget.dataset.channelId;
          const action = e.currentTarget.dataset.channelAction;
          const state = window.currentState || await api('/api/state');
          const channel = state.config.telegram.channels.find(item => item.id === channelId);
          try {
            if (action === 'edit') {
              setChannelForm(channel || null);
              return;
            }
            if (action === 'toggle') {
              await api('/api/channels/toggle', {method:'POST', body: JSON.stringify({channel_id: channelId, enabled: !(channel?.enabled)})});
            } else if (action === 'remove') {
              await api('/api/channels/remove', {method:'POST', body: JSON.stringify({channel_id: channelId})});
            }
            load();
          } catch (err) {
            alert(err.message);
          }
        });
      });
      document.getElementById('injectForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          await api('/api/inject-message', {method:'POST', body: JSON.stringify({
            text:String(f.get('text')),
            chat_id:String(f.get('chat_id')),
            message_id:Number(f.get('message_id')),
            event_type:String(f.get('event_type')),
            version:f.get('version') ? Number(f.get('version')) : null,
            use_configured_okx_path:String(f.get('execution_path')) === 'configured'
          })});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('operatorCommandForm')?.addEventListener('submit', async e => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          const result = await api('/api/actions/operator-command', {method:'POST', body: JSON.stringify({text:String(f.get('text'))})});
          alert(result.reply || result.status || 'ok');
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('closeAllButton')?.addEventListener('click', async () => {
        try {
          await api('/api/positions/close', {method:'POST', body: JSON.stringify({})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('pauseButton')?.addEventListener('click', async () => {
        try {
          await api('/api/actions/pause', {method:'POST', body: JSON.stringify({reason:'Manual pause from Web UI'})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('resumeButton')?.addEventListener('click', async () => {
        try {
          await api('/api/actions/resume', {method:'POST', body: JSON.stringify({reason:'Manual resume from Web UI'})});
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('reconcileButton')?.addEventListener('click', async () => {
        try {
          const result = await api('/api/actions/reconcile', {method:'POST', body: JSON.stringify({})});
          alert(result.detail);
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('topicTestButton')?.addEventListener('click', async () => {
        try {
          const result = await api('/api/actions/topic-test', {method:'POST', body: JSON.stringify({})});
          const detail = result.reason || result.stderr || result.target_link || result.target || '';
          alert(result.sent ? ('Topic smoke succeeded: ' + detail) : ('Topic smoke ' + (result.status || 'failed') + ': ' + detail));
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.getElementById('resetLocalStateButton')?.addEventListener('click', async () => {
        if (!confirm('Reset local runtime state? This only clears local DB/log/session state and does not touch any external OKX demo position.')) return;
        try {
          const result = await api('/api/actions/reset-local-state', {method:'POST', body: JSON.stringify({})});
          alert(result.detail);
          load();
        } catch (err) {
          alert(err.message);
        }
      });
      document.querySelectorAll('[data-close-symbol]').forEach(button => {
        button.addEventListener('click', async e => {
          try {
            await api('/api/positions/close', {method:'POST', body: JSON.stringify({symbol: e.currentTarget.dataset.closeSymbol})});
            load();
          } catch (err) {
            alert(err.message);
          }
        });
      });
    }
    load();
    setInterval(load, 5000);
  </script>
</body></html>"""


LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login</title><style>body{font-family:Georgia,serif;background:#f6f2ea;display:grid;place-items:center;height:100vh;margin:0}form{background:#fffdf8;border:1px solid #e7d8c6;border-radius:18px;padding:28px;display:grid;gap:12px;min-width:280px}input,button{font:inherit;padding:12px;border-radius:10px;border:1px solid #d8c7b1}button{background:#d45500;color:#fff;border:none}</style></head>
<body><form method="post" action="/login"><h2>6-digit PIN</h2><input name="pin" pattern="[0-9]{6}" maxlength="6" minlength="6" inputmode="numeric" autofocus><button>Login</button></form></body></html>"""


class WebController:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.failed_attempts: dict[str, list[float]] = {}

    def route(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        client_ip: str = "local",
    ) -> tuple[int, dict[str, str], str | dict]:
        request_headers = headers or {}
        if method == "GET":
            return self._route_get(path, request_headers)
        if method == "POST":
            return self._route_post(path, body, request_headers, client_ip)
        return HTTPStatus.METHOD_NOT_ALLOWED, {}, {"error": "Method not allowed"}

    def _route_get(self, path: str, headers: dict[str, str]) -> tuple[int, dict[str, str], str | dict]:
        if path == "/login":
            if self._require_auth(headers):
                return HTTPStatus.SEE_OTHER, {"Location": "/"}, ""
            return HTTPStatus.OK, {"Content-Type": "text/html; charset=utf-8"}, LOGIN_HTML
        if path == "/healthz":
            snapshot = self.runtime.health_snapshot()
            overall = "ok" if all(item["status"] not in {"error", "fail"} for item in snapshot.values()) else "error"
            return HTTPStatus.OK, {}, {"status": overall, "health": snapshot}
        if path == "/readyz":
            report = self.runtime.public_verification_report()
            return HTTPStatus.OK, {}, {"status": report["status"], "checks": report["checks"]}
        if path == "/" or path == "/index.html":
            if not self._require_auth(headers):
                return HTTPStatus.SEE_OTHER, {"Location": "/login"}, ""
            return HTTPStatus.OK, {"Content-Type": "text/html; charset=utf-8"}, HTML
        if path == "/api/state":
            if not self._require_auth(headers):
                return HTTPStatus.UNAUTHORIZED, {}, {"error": "Unauthorized"}
            snapshot = self.runtime.public_snapshot()
            snapshot["run_paths"] = self.runtime.usage_paths()
            snapshot["direct_use_text"] = self.runtime.direct_use_text(snapshot=snapshot, usage_paths=snapshot["run_paths"])
            return HTTPStatus.OK, {}, snapshot
        return HTTPStatus.NOT_FOUND, {}, {"error": "Not found"}

    def _route_post(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
        client_ip: str,
    ) -> tuple[int, dict[str, str], str | dict]:
        if path == "/login":
            if self._is_rate_limited(client_ip):
                return HTTPStatus.TOO_MANY_REQUESTS, {}, {"error": "Too many login attempts"}
            form = parse_qs(body.decode("utf-8"))
            pin = form.get("pin", [""])[0]
            session_id = self.runtime.authenticate(pin)
            if not session_id:
                self._record_failed_attempt(client_ip)
                return HTTPStatus.UNAUTHORIZED, {}, {"error": "Invalid PIN"}
            cookie = http.cookies.SimpleCookie()
            cookie["session"] = session_id
            cookie["session"]["path"] = "/"
            cookie["session"]["httponly"] = True
            return HTTPStatus.SEE_OTHER, {"Set-Cookie": cookie.output(header="").strip(), "Location": "/"}, ""

        if not self._require_auth(headers):
            return HTTPStatus.UNAUTHORIZED, {}, {"error": "Unauthorized"}

        if path == "/api/config":
            try:
                patch = _decode_json_body(body)
                updated = self.runtime.update_config(patch)
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            return HTTPStatus.OK, {}, {
                "updated": True,
                "config": public_config_dict(updated),
                "secret_status": self.runtime.secret_status(updated),
                "wiring": self.runtime.wiring_summary(updated),
            }
        if path == "/api/inject-message":
            try:
                payload = _decode_json_body(body)
                self.runtime.inject_message(
                    text=str(payload.get("text", "")),
                    chat_id=str(payload.get("chat_id", "-1000000000000")),
                    message_id=int(payload.get("message_id", 1)),
                    event_type=str(payload.get("event_type", "new")),
                    version=payload.get("version"),
                    use_configured_okx_path=bool(payload.get("use_configured_okx_path", False)),
                )
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            snapshot = self.runtime.public_snapshot()
            snapshot["run_paths"] = self.runtime.usage_paths()
            return HTTPStatus.CREATED, {}, snapshot
        if path == "/api/channels/upsert":
            try:
                payload = _decode_json_body(body)
                channel = self.runtime.upsert_channel(payload)
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            return HTTPStatus.CREATED, {}, channel
        if path == "/api/channels/toggle":
            try:
                payload = _decode_json_body(body)
                channel = self.runtime.set_channel_enabled(str(payload.get("channel_id", "")), bool(payload.get("enabled", False)))
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            return HTTPStatus.OK, {}, channel
        if path == "/api/channels/remove":
            try:
                payload = _decode_json_body(body)
                self.runtime.remove_channel(str(payload.get("channel_id", "")))
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            return HTTPStatus.OK, {}, {"removed": True}
        if path == "/api/actions/pause":
            try:
                payload = _decode_json_body(body)
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            self.runtime.pause_trading(str(payload.get("reason", "Manual pause from Web UI")))
            return HTTPStatus.OK, {}, self.runtime.public_snapshot()["operator_state"]
        if path == "/api/actions/resume":
            try:
                payload = _decode_json_body(body)
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            self.runtime.resume_trading(str(payload.get("reason", "Manual resume from Web UI")))
            return HTTPStatus.OK, {}, self.runtime.public_snapshot()["operator_state"]
        if path == "/api/actions/reconcile":
            return HTTPStatus.CREATED, {}, self.runtime.reconcile_now()
        if path == "/api/actions/operator-command":
            try:
                payload = _decode_json_body(body)
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            result = self.runtime.run_operator_command(str(payload.get("text", "")), source="web")
            return HTTPStatus.CREATED if result.get("handled") else HTTPStatus.BAD_REQUEST, {}, result
        if path == "/api/actions/topic-test":
            try:
                result = self.runtime.send_topic_test()
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            return HTTPStatus.CREATED, {}, result
        if path == "/api/actions/reset-local-state":
            return HTTPStatus.CREATED, {}, self.runtime.reset_local_runtime_state()
        if path == "/api/positions/close":
            try:
                payload = _decode_json_body(body)
                result = self.runtime.close_positions(symbol=payload.get("symbol"))
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            except RuntimeError as exc:
                return HTTPStatus.BAD_GATEWAY, {}, {"error": str(exc)}
            return HTTPStatus.CREATED, {}, result
        return HTTPStatus.NOT_FOUND, {}, {"error": "Not found"}

    def _require_auth(self, headers: dict[str, str]) -> bool:
        cookie_header = headers.get("Cookie", "")
        cookies = http.cookies.SimpleCookie(cookie_header)
        session = cookies.get("session")
        return bool(session and self.runtime.check_session(session.value))

    def _is_rate_limited(self, client_ip: str) -> bool:
        now = time.time()
        attempts = [ts for ts in self.failed_attempts.get(client_ip, []) if now - ts < 300]
        self.failed_attempts[client_ip] = attempts
        return len(attempts) >= 5

    def _record_failed_attempt(self, client_ip: str) -> None:
        self.failed_attempts.setdefault(client_ip, []).append(time.time())


def create_server(runtime: Runtime) -> ThreadingHTTPServer:
    controller = WebController(runtime)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, response_headers, payload = controller.route(
                "GET",
                self.path,
                headers={key: value for key, value in self.headers.items()},
                client_ip=self._client_ip(),
            )
            self._write_response(status, response_headers, payload)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            status, response_headers, payload = controller.route(
                "POST",
                self.path,
                body=self.rfile.read(length),
                headers={key: value for key, value in self.headers.items()},
                client_ip=self._client_ip(),
            )
            self._write_response(status, response_headers, payload)

        def log_message(self, format: str, *args) -> None:
            return

        def _write_response(self, status: int, headers: dict[str, str], payload: str | dict) -> None:
            content_type = headers.get("Content-Type", "application/json")
            body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                if key == "Content-Type":
                    continue
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _client_ip(self) -> str:
            return self.client_address[0] if self.client_address else "unknown"

    config = runtime.config_manager.get()
    server = ThreadingHTTPServer((config.web.host, config.web.port), Handler)
    bound_host, bound_port = server.server_address[:2]
    runtime.register_web_server(str(bound_host), int(bound_port))
    return server


def _decode_json_body(body: bytes) -> dict:
    text = body.decode("utf-8").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload
