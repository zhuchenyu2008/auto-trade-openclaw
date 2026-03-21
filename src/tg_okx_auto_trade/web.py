from __future__ import annotations

import http.cookies
import json
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import public_config_dict
from .runtime import (
    DEFAULT_DEMO_SIGNAL_CHAT_ID,
    DEFAULT_DEMO_SIGNAL_MESSAGE_ID,
    DEFAULT_DEMO_SIGNAL_TEXT,
    Runtime,
)


_VALID_WEB_VIEWS = ("overview", "actions", "settings", "channels", "runtime")


def _normalize_web_view(value: str) -> str:
    return value if value in _VALID_WEB_VIEWS else "overview"


def _render_app_html(initial_view: str = "overview") -> str:
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TG OKX Auto Trade</title>
  <style>
    :root { --bg:#f6f2ea; --ink:#1b1d1f; --accent:#d45500; --accent-soft:#fff1e8; --card:#fffdf8; --line:#e7d8c6; --muted:#6f665e; --shadow:0 14px 34px rgba(78,54,28,.08); }
    *{box-sizing:border-box}
    html{overflow-y:scroll}
    body{font-family:Georgia,serif;background:linear-gradient(180deg,#efe6d8, #f8f5ef 30%, #f0ebe2);color:var(--ink);margin:0;line-height:1.5}
    header{padding:24px 24px 18px;border-bottom:1px solid var(--line);background:rgba(255,253,248,.92);position:sticky;top:0;backdrop-filter: blur(10px);z-index:20;box-shadow:0 8px 24px rgba(78,54,28,.06)}
    h1,h2,h3,p{margin:0}
    h1{line-height:1.2}
    h2{font-size:22px;line-height:1.25}
    h3{font-size:16px;line-height:1.3}
    .hero{display:flex;justify-content:space-between;gap:18px;align-items:end;flex-wrap:wrap;margin-bottom:16px}
    .muted{color:var(--muted)}
    .pill{display:inline-block;padding:5px 11px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:12px;line-height:1.3}
    .topnav{display:flex;flex-wrap:wrap;gap:10px;padding-top:4px}
    .topnav a{padding:10px 14px;border-radius:999px;border:1px solid var(--line);background:#fff8ef;color:var(--ink);text-decoration:none;line-height:1.3;min-height:42px;display:inline-flex;align-items:center;justify-content:center}
    .topnav a.is-active{background:var(--accent);border-color:var(--accent);color:#fff}
    .shell{padding:24px;display:grid;gap:20px;max-width:1440px;margin:0 auto}
    .status-strip{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));align-items:start}
    .view-grid{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));align-items:start}
    .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:var(--shadow);min-width:0;overflow:hidden}
    .card--wide{grid-column:1/-1;min-width:0}
    .card > * + *{margin-top:16px}
    .card h2 + .muted,.card h2 + .section-lead{margin-top:6px}
    .section-lead{color:var(--muted)}
    .key-grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
    .key-item{padding:14px;border-radius:14px;background:#fff8ef;border:1px solid #f0e1d0;min-width:0}
    .key-item strong{display:block;margin-bottom:6px}
    .grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    .field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    .field-grid--triple{grid-template-columns:repeat(3,minmax(0,1fr))}
    .field{display:grid;gap:8px;min-width:0}
    .field span{font-size:13px;font-weight:600;color:var(--muted);line-height:1.35}
    .field--full{grid-column:1/-1}
    .form-section{display:grid;gap:14px;padding:16px;border:1px solid #efe2d3;border-radius:16px;background:#fffaf3}
    .button-row{display:flex;flex-wrap:wrap;gap:12px}
    .button-row button{flex:1 1 180px}
    .split-panel{display:grid;gap:16px;grid-template-columns:minmax(0,1.1fr) minmax(0,.9fr)}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font-size:12px;background:#221f1c;color:#f8f4ee;padding:14px;border-radius:14px;max-height:360px;overflow:auto;max-width:100%}
    code{overflow-wrap:anywhere}
    input,button,select,textarea{font:inherit;padding:11px 13px;border-radius:12px;border:1px solid var(--line);width:100%;max-width:100%;min-height:44px;background:#fff}
    textarea{min-height:108px;resize:vertical}
    button{background:var(--accent);color:white;border:none;cursor:pointer;font-weight:600}
    button:hover{filter:brightness(.98)}
    form{display:grid;gap:14px;min-width:0}
    details{display:grid;gap:12px;padding:14px 16px;border:1px solid #efe2d3;border-radius:14px;background:#fffaf3}
    summary{cursor:pointer}
    table{width:100%;border-collapse:collapse;font-size:13px;min-width:720px}
    td,th{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}
    th{background:#fbf4ea;font-weight:700;position:sticky;top:0}
    .table-scroll{overflow:auto;max-width:100%;border:1px solid #f0e1d0;border-radius:14px;background:#fff}
    .table-scroll table{min-width:720px}
    .channel-actions{display:flex;flex-wrap:wrap;gap:8px}
    .channel-table td:last-child,.channel-table th:last-child{min-width:220px}
    .channel-actions button{width:auto;min-width:88px;flex:0 0 auto}
    @media(max-width:1100px){.view-grid{grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}.field-grid--triple{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:900px){header{padding:20px 18px 16px}.shell{padding:18px}.split-panel{grid-template-columns:1fr}.field-grid,.grid2,.field-grid--triple{grid-template-columns:1fr}}
    @media(max-width:720px){.view-grid,.status-strip{grid-template-columns:1fr}.card{padding:16px}.topnav a{flex:1 1 140px}}
  </style>
</head>
<body>
  <header>
    <div class="hero">
      <div>
        <div id="webBindBox" class="pill">加载中</div>
        <h1>Telegram OKX Auto Trade</h1>
        <div class="muted">仅合约。默认杠杆 20x。全局止盈止损默认关闭。</div>
      </div>
      <div id="modeBox" class="pill">加载中</div>
    </div>
    <nav id="primaryNav" class="topnav" aria-label="主导航">
      <a href="/?view=overview" data-nav-view="overview">总览 / Dashboard</a>
      <a href="/?view=actions" data-nav-view="actions">控制 / Actions</a>
      <a href="/?view=settings" data-nav-view="settings">配置 / Settings</a>
      <a href="/?view=channels" data-nav-view="channels">频道 / Channels</a>
      <a href="/?view=runtime" data-nav-view="runtime">运行数据 / Runtime Data</a>
    </nav>
  </header>
  <main class="shell">
    <section id="statusStrip" class="status-strip"></section>
    <section id="viewMount" class="view-grid" data-current-view="__INITIAL_VIEW__"></section>
  </main>
  <script>
    let latestLoadRequestId = 0;
    const DEFAULT_VIEW = '__INITIAL_VIEW__';
    const VALID_VIEWS = ['overview', 'actions', 'settings', 'channels', 'runtime'];
    async function api(path, options={}) {
      const res = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, options));
      if (res.status === 401) { location.href = '/login'; return; }
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || ('请求失败: ' + res.status));
      }
      const contentType = res.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) return res.json();
      return res.text();
    }
    function esc(v){ return String(v == null ? '' : v).replace(/[&<>]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[s])); }
    function pickLabel(map, value, fallback='未提供') {
      const key = String(value == null ? '' : value);
      return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : (key || fallback);
    }
    function displayTradingMode(value) { return pickLabel({observe:'观察模式', demo:'演示模式'}, value); }
    function displayExecutionMode(value) { return pickLabel({automatic:'自动执行', observe:'仅观察'}, value); }
    function displayThinking(value) { return pickLabel({off:'关闭', minimal:'极低', low:'低', medium:'中', high:'高', custom:'自定义'}, value); }
    function displayBool(value) { return String(value) === 'true' ? '是' : '否'; }
    function displaySourceType(value) { return pickLabel({public_web:'public_web', bot_api:'bot_api（遗留）', mtproto:'mtproto（未实现）'}, value); }
    function displayPositionSide(value) { return pickLabel({long:'多', short:'空'}, value); }
    function displayEventType(value) { return pickLabel({new:'新增', edit:'编辑', delete:'删除'}, value); }
    function displayAction(value) {
      return pickLabel({
        open_long:'开多', open_short:'开空', add_long:'加多', add_short:'加空', reduce_long:'减多', reduce_short:'减空',
        reverse_to_long:'反手开多', reverse_to_short:'反手开空', close_long:'平多', close_short:'平空', close_all:'全部平仓',
        cancel_orders:'撤单', cancel_entry:'撤销入场', update_protection:'更新保护', ignore:'忽略',
      }, value);
    }
    function displayRecordStatus(value) {
      return pickLabel({
        filled:'已成交', observed:'仅观察', submitted:'已提交', canceled:'已撤销', rejected:'已拒绝', failed:'失败',
        pending:'处理中', ignored:'已忽略', open:'进行中', closed:'已关闭', EXECUTED:'已执行', OBSERVED:'仅观察',
        EXECUTION_FAILED:'执行失败', RISK_REJECTED:'风控拒绝', IGNORED:'已忽略', ERROR:'错误',
      }, value);
    }
    function getCurrentView() {
      const url = new URL(window.location.href);
      const view = url.searchParams.get('view') || document.getElementById('viewMount').dataset.currentView || DEFAULT_VIEW;
      return VALID_VIEWS.indexOf(view) >= 0 ? view : DEFAULT_VIEW;
    }
    function setCurrentView(view, replace) {
      const nextView = VALID_VIEWS.indexOf(view) >= 0 ? view : DEFAULT_VIEW;
      const url = new URL(window.location.href);
      url.searchParams.set('view', nextView);
      if (replace) {
        history.replaceState({view: nextView}, '', url.toString());
      } else {
        history.pushState({view: nextView}, '', url.toString());
      }
      document.getElementById('viewMount').dataset.currentView = nextView;
      renderNav(nextView);
      if (window.currentState) renderView(window.currentState, nextView);
    }
    function renderNav(activeView) {
      document.querySelectorAll('[data-nav-view]').forEach(link => {
        link.classList.toggle('is-active', link.dataset.navView === activeView);
      });
    }
    function renderStatusStrip(data, ui, overview, directUseProfile) {
      document.getElementById('statusStrip').innerHTML = `
        <div class="card"><strong>当前运行画像</strong><div>${esc(directUseProfile.status_label || directUseProfile.status || '未提供')}</div><div class="muted">${esc(directUseProfile.detail || '')}</div></div>
        <div class="card"><strong>运行态</strong><div>${esc(overview.runtime_status || (data.operator_state.paused ? '已暂停' : data.health.trading_runtime.status))}</div><div class="muted">${esc(overview.runtime_detail || data.operator_state.pause_reason || data.health.trading_runtime.detail || '')}</div></div>
        <div class="card"><strong>最近对账</strong><div>${esc(overview.last_reconcile_detail || data.operator_state.last_reconcile.detail || '未执行')}</div><div class="muted">核验状态 ${esc(ui.verification_status || data.verification_status || 'unknown')}</div></div>
        <div class="card"><strong>已启用频道</strong><div>${esc(data.wiring.enabled_channel_ids.join(', ') || '无')}</div><div class="muted">Web 监听 ${esc(data.wiring.web_bind)}</div></div>`;
    }
    function buildContext(data) {
      const ui = data.web_display || {};
      const overview = ui.overview || {};
      const nextSteps = (ui.next_steps || data.next_steps || []).filter(Boolean);
      const demoSignalText = __DEMO_SIGNAL_TEXT__;
      const nextDemoSignalMessageId = data.messages.reduce((maxId, item) => {
        const messageId = Number(item && item.message_id);
        return Number.isFinite(messageId) ? Math.max(maxId, messageId) : maxId;
      }, __DEMO_SIGNAL_MESSAGE_ID__ - 1) + 1;
      const openPositions = data.positions.filter(item => {
        const payload = item && item.payload ? item.payload : {};
        return Number(payload.qty || 0) > 0 && ['long', 'short'].includes(String(payload.side || ''));
      });
      return {
        data,
        ui,
        overview,
        demoSignalText,
        nextDemoSignalMessageId,
        directUseProfile: ui.direct_use_profile || {status_label:'未提供', detail:'未提供', action:'未提供'},
        logs: data.logs.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.level)}</td><td>${esc(item.category)}</td><td>${esc(item.message)}</td></tr>`).join(''),
        auditLogs: data.audit_logs.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.category)}</td><td>${esc(item.message)}</td></tr>`).join(''),
        orders: data.orders.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.symbol)}</td><td>${esc(displayAction(item.action))}</td><td>${esc(displayRecordStatus(item.status))}</td><td>${esc(displayTradingMode(item.mode))}</td></tr>`).join(''),
        positions: openPositions.length ? openPositions.map(item => `<tr><td>${esc(item.symbol)}</td><td>${esc(displayPositionSide(item.payload.side))}</td><td>${esc(item.payload.qty)}</td><td>${esc(item.payload.leverage)}</td><td>${esc(item.payload.unrealized_pnl)}</td><td>${esc(JSON.stringify(item.payload.protection || {}))}</td><td><button type="button" data-close-symbol="${esc(item.symbol)}">平仓</button></td></tr>`).join('') : '<tr><td colspan="7">当前无持仓。</td></tr>',
        channels: data.config.telegram.channels.map(ch => `<tr><td>${esc(ch.name)}</td><td>${esc(displaySourceType(ch.source_type))}</td><td>${esc(ch.chat_id || ch.channel_username)}</td><td>${esc(displayBool(ch.enabled))}</td><td>${esc(ch.reconcile_interval_seconds)}</td><td><div class="channel-actions"><button type="button" data-channel-action="edit" data-channel-id="${esc(ch.id)}">编辑</button><button type="button" data-channel-action="toggle" data-channel-id="${esc(ch.id)}">${ch.enabled ? '禁用' : '启用'}</button><button type="button" data-channel-action="remove" data-channel-id="${esc(ch.id)}">删除</button></div></td></tr>`).join(''),
        messages: data.messages.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.chat_id)}</td><td>${esc(item.message_id)} v${esc(item.version)}</td><td>${esc(displayEventType(item.event_type))}</td><td>${esc(displayRecordStatus(item.status))}</td><td>${esc(item.payload.text || item.payload.caption || '')}</td></tr>`).join(''),
        decisions: data.ai_decisions.map(item => `<tr><td>${esc(item.created_at)}</td><td>${esc(item.payload.symbol)}</td><td>${esc(displayAction(item.payload.action))}</td><td>${esc(item.payload.confidence)}</td><td>${esc(item.payload.reason)}</td></tr>`).join(''),
        health: esc(ui.health_json || JSON.stringify(data.health, null, 2)),
        nextStep: nextSteps[0] || '',
      };
    }
    function renderOverviewView(ctx) {
      const data = ctx.data;
      const nextStep = ctx.nextStep;
      return `
        <section class="card card--wide" data-view-section="overview-summary"><h2>总览 / Dashboard</h2>
          <div class="key-grid">
            <div class="key-item"><strong>交易模式</strong><div>${esc(displayTradingMode(data.config.trading.mode))}</div></div>
            <div class="key-item"><strong>执行模式</strong><div>${esc(displayExecutionMode(data.config.trading.execution_mode))}</div></div>
            <div class="key-item"><strong>盈亏</strong><div>${esc(data.dashboard.total_unrealized_pnl)} 未实现 / ${esc(data.dashboard.total_realized_pnl)} 已实现</div></div>
            <div class="key-item"><strong>持仓</strong><div>${esc(data.dashboard.positions_count)} 已开 / ${esc(data.dashboard.tracked_symbols_count || data.positions.length)} 已跟踪</div></div>
            <div class="key-item"><strong>话题目标</strong><div>${esc(data.wiring.topic_target || '未配置')}</div></div>
            <div class="key-item"><strong>话题发送</strong><div>${esc(data.wiring.topic_delivery_state)}${data.wiring.topic_delivery_verified ? ' / 已验证' : ''}</div></div>
          </div>
        </section>
        <section class="card"><h2>下一步</h2>
          <div>${esc(nextStep || ctx.directUseProfile.action || '当前无需额外操作。')}</div>
          <div class="muted">${esc(data.wiring.web_restart_required ? ('Web 地址配置已变更，重启后生效：' + data.run_paths.configured_web_login + '。') : '')}</div>
        </section>
      `;
    }
    function renderActionsView(ctx) {
      const data = ctx.data;
      return `
        <section class="card card--wide" data-view-section="actions-quick"><h2>控制 / Actions</h2>
          <div class="button-row">
            <button type="button" id="pauseButton">暂停</button>
            <button type="button" id="resumeButton">恢复</button>
            <button type="button" id="reconcileButton">立即对账</button>
            <button type="button" id="topicTestButton">话题自检</button>
            <button type="button" id="resetLocalStateButton">重置本地状态</button>
            <button type="button" id="closeAllButton">全部平仓</button>
          </div>
        </section>
        <section class="card"><h2>操作员命令</h2>
          <div class="section-lead">${esc((data.run_paths.operator_command_examples || []).join('  '))}</div>
          <form id="operatorCommandForm">
            <label class="field">
              <span>命令内容</span>
              <input name="text" value="/status" placeholder="/status">
            </label>
            <button>执行操作员命令</button>
          </form>
        </section>
        <section class="card card--wide"><h2>演示信号测试</h2>
          <div class="section-lead">默认走模拟；只有明确要打到已配置 OKX Demo 路径时才切换。</div>
          <form id="injectForm">
            <div class="form-section">
              <label class="field field--full">
                <span>信号文本</span>
                <textarea name="text" rows="4" placeholder="${esc(ctx.demoSignalText)}">${esc(ctx.demoSignalText)}</textarea>
              </label>
              <div class="field-grid">
                <label class="field">
                  <span>会话 ID</span>
                  <input name="chat_id" value="__DEMO_SIGNAL_CHAT_ID__" placeholder="会话 id">
                </label>
                <label class="field">
                  <span>消息 ID</span>
                  <input name="message_id" type="number" min="1" value="${esc(ctx.nextDemoSignalMessageId)}">
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>事件类型</span>
                  <select name="event_type"><option value="new">新增</option><option value="edit">编辑</option></select>
                </label>
                <label class="field">
                  <span>版本号</span>
                  <input name="version" type="number" min="1" placeholder="自动版本号">
                </label>
              </div>
              <label class="field">
                <span>执行路径</span>
                <select name="execution_path"><option value="simulated">模拟冒烟</option><option value="configured">已配置 OKX 路径</option></select>
              </label>
            </div>
            <button>执行演示信号</button>
          </form>
        </section>
      `;
    }
    function renderSettingsView(ctx) {
      const data = ctx.data;
      return `
        <section class="card"><h2>交易配置</h2>
          <form id="modeForm">
            <div class="form-section">
              <div class="field-grid">
                <label class="field">
                  <span>交易模式</span>
                  <select name="mode"><option value="observe" ${data.config.trading.mode==='observe'?'selected':''}>观察模式</option><option value="demo" ${data.config.trading.mode==='demo'?'selected':''}>演示模式</option></select>
                </label>
                <label class="field">
                  <span>执行模式</span>
                  <select name="execution_mode"><option value="automatic" ${data.config.trading.execution_mode==='automatic'?'selected':''}>自动执行</option><option value="observe" ${data.config.trading.execution_mode==='observe'?'selected':''}>仅观察</option></select>
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>默认杠杆</span>
                  <input name="default_leverage" type="number" min="1" max="125" value="${esc(data.config.trading.default_leverage)}">
                </label>
                <label class="field">
                  <span>运行状态</span>
                  <select name="paused"><option value="false" ${!data.config.trading.paused?'selected':''}>运行中</option><option value="true" ${data.config.trading.paused?'selected':''}>已暂停</option></select>
                </label>
              </div>
            </div>
            <button>保存交易配置</button>
          </form>
        </section>
        <section class="card"><h2>AI 配置</h2>
          <form id="aiForm">
            <div class="form-section">
              <div class="field-grid">
                <label class="field">
                  <span>Provider</span>
                  <input name="provider" value="${esc(data.config.ai.provider)}" placeholder="openclaw">
                </label>
                <label class="field">
                  <span>Model</span>
                  <input name="model" value="${esc(data.config.ai.model)}" placeholder="default">
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>Thinking 强度</span>
                  <select name="thinking"><option value="off" ${data.config.ai.thinking==='off'?'selected':''}>关闭</option><option value="minimal" ${data.config.ai.thinking==='minimal'?'selected':''}>极低</option><option value="low" ${data.config.ai.thinking==='low'?'selected':''}>低</option><option value="medium" ${data.config.ai.thinking==='medium'?'selected':''}>中</option><option value="high" ${data.config.ai.thinking==='high'?'selected':''}>高</option><option value="custom" ${data.config.ai.thinking==='custom'?'selected':''}>自定义</option></select>
                </label>
                <label class="field">
                  <span>超时秒数</span>
                  <input name="timeout_seconds" type="number" min="1" value="${esc(data.config.ai.timeout_seconds)}">
                </label>
              </div>
              <label class="field field--full">
                <span>系统提示词</span>
                <textarea name="system_prompt" rows="4" placeholder="仅输出严格 JSON">${esc(data.config.ai.system_prompt)}</textarea>
              </label>
            </div>
            <button>保存 AI 配置</button>
          </form>
        </section>
        <section class="card"><h2>风控配置</h2>
          <form id="riskForm">
            <div class="form-section">
              <div class="field-grid">
                <label class="field">
                  <span>全局止盈止损</span>
                  <select name="global_tp_sl_enabled"><option value="false" ${!data.config.trading.global_tp_sl_enabled?'selected':''}>关闭</option><option value="true" ${data.config.trading.global_tp_sl_enabled?'selected':''}>启用</option></select>
                </label>
                <label class="field">
                  <span>全局止盈比例</span>
                  <input name="global_take_profit_ratio" type="number" step="0.1" value="${esc(data.config.trading.global_take_profit_ratio)}">
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>全局止损比例</span>
                  <input name="global_stop_loss_ratio" type="number" step="0.1" value="${esc(data.config.trading.global_stop_loss_ratio)}">
                </label>
                <label class="field">
                  <span>只读平仓限制</span>
                  <select name="readonly_close_only"><option value="false" ${!data.config.trading.readonly_close_only?'selected':''}>正常</option><option value="true" ${data.config.trading.readonly_close_only?'selected':''}>仅平仓</option></select>
                </label>
              </div>
            </div>
            <button>保存风控配置</button>
          </form>
        </section>
        <section class="card card--wide"><h2>Telegram 接线</h2>
          <form id="telegramForm">
            <div class="section-lead"><code>public_web</code> 是主支持采集路径；这里主要维护话题目标和轮询参数。</div>
            <div class="form-section">
              <div class="field-grid">
                <label class="field">
                  <span>汇报话题</span>
                  <input name="report_topic" value="${esc(data.config.telegram.report_topic)}" placeholder="汇报话题或 https://t.me/c/.../...">
                </label>
                <label class="field">
                  <span>操作员目标</span>
                  <input name="operator_target" value="${esc(data.config.telegram.operator_target)}" placeholder="操作员目标或 https://t.me/c/.../...">
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>线程 ID</span>
                  <input name="operator_thread_id" type="number" min="0" value="${esc(data.config.telegram.operator_thread_id)}" placeholder="线程 id">
                </label>
                <label class="field">
                  <span>轮询秒数</span>
                  <input name="poll_interval_seconds" type="number" min="1" value="${esc(data.config.telegram.poll_interval_seconds)}">
                </label>
              </div>
            </div>
            <details>
              <summary class="muted">遗留 bot_api / bot token 兼容项</summary>
              <div class="muted">Bot token 已配置: ${esc(data.secret_status.telegram_bot_token_configured)}。来源: ${esc(data.secret_sources.telegram_bot_token)}。留空则保留现有 token。</div>
              <input name="bot_token" value="" placeholder="遗留 bot token">
              <label class="muted"><input name="clear_bot_token" type="checkbox" value="true"> 保存时清空已存储的 bot token</label>
            </details>
            <button>保存 Telegram 配置</button>
          </form>
        </section>
      `;
    }
    function renderChannelsView(ctx) {
      return `
        <section class="card card--wide" data-view-section="channels-table"><h2>频道 / Channels</h2>
          <div class="table-scroll"><table class="channel-table"><tr><th>名称</th><th>适配器</th><th>目标</th><th>已启用</th><th>对账</th><th>操作</th></tr>${ctx.channels}</table></div>
        </section>
        <section class="card card--wide"><h2>频道配置</h2>
          <form id="channelForm">
            <div class="form-section">
              <div class="field-grid">
                <label class="field">
                  <span>频道 ID</span>
                  <input name="id" placeholder="频道 id（留空自动生成）">
                </label>
                <label class="field">
                  <span>展示名称</span>
                  <input name="name" placeholder="展示名称">
                </label>
              </div>
              <div class="muted">自动采集优先用 <code>public_web</code>；legacy 适配器才需要 <code>chat_id</code>。</div>
              <div class="field-grid">
                <label class="field">
                  <span>采集适配器</span>
                  <select name="source_type"><option value="public_web">public_web</option><option value="bot_api">bot_api (legacy)</option><option value="mtproto">mtproto</option></select>
                </label>
                <label class="field">
                  <span>chat_id / 链接</span>
                  <input name="chat_id" placeholder="-100... or https://t.me/c/.../...">
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>频道用户名 / 公共页链接</span>
                  <input name="channel_username" placeholder="@username, https://t.me/username, or https://t.me/s/username">
                </label>
                <label class="field">
                  <span>启用状态</span>
                  <select name="enabled"><option value="true">启用</option><option value="false">禁用</option></select>
                </label>
              </div>
              <div class="field-grid">
                <label class="field">
                  <span>对账间隔秒数</span>
                  <input name="reconcile_interval_seconds" type="number" min="1" value="30">
                </label>
                <label class="field">
                  <span>去重窗口秒数</span>
                  <input name="dedup_window_seconds" type="number" min="1" value="3600">
                </label>
              </div>
              <label class="field field--full">
                <span>备注</span>
                <textarea name="notes" rows="2" placeholder="备注"></textarea>
              </label>
            </div>
            <button id="channelSubmitButton" type="submit">保存频道</button>
          </form>
        </section>
      `;
    }
    function renderRuntimeView(ctx) {
      return `
        <section class="card"><h2>持仓</h2><div class="table-scroll"><table><tr><th>标的</th><th>方向</th><th>数量</th><th>杠杆</th><th>未实现盈亏</th><th>保护</th><th>操作</th></tr>${ctx.positions}</table></div></section>
        <section class="card"><h2>订单</h2><div class="table-scroll"><table><tr><th>时间</th><th>标的</th><th>动作</th><th>状态</th><th>模式</th></tr>${ctx.orders}</table></div></section>
        <section class="card"><h2>最近消息</h2><div class="table-scroll"><table><tr><th>时间</th><th>会话</th><th>消息</th><th>事件</th><th>状态</th><th>文本</th></tr>${ctx.messages}</table></div></section>
        <section class="card"><h2>AI 决策</h2><div class="table-scroll"><table><tr><th>时间</th><th>标的</th><th>动作</th><th>置信度</th><th>原因</th></tr>${ctx.decisions}</table></div></section>
        <section class="card"><h2>日志</h2><div class="table-scroll"><table><tr><th>时间</th><th>级别</th><th>分类</th><th>消息</th></tr>${ctx.logs}</table></div></section>
        <section class="card"><h2>审计日志</h2><div class="table-scroll"><table><tr><th>时间</th><th>分类</th><th>消息</th></tr>${ctx.auditLogs}</table></div></section>
        <section class="card"><h2>健康状态</h2><pre>${ctx.health}</pre></section>
      `;
    }
    const VIEW_RENDERERS = {
      overview: renderOverviewView,
      actions: renderActionsView,
      settings: renderSettingsView,
      channels: renderChannelsView,
      runtime: renderRuntimeView,
    };
    function captureChannelFormState() {
      const form = document.getElementById('channelForm');
      if (!form) return null;
      return {
        mode: form.dataset.mode || 'create', dirty: form.dataset.dirty === 'true', id: form.elements.id.value, name: form.elements.name.value,
        source_type: form.elements.source_type.value, chat_id: form.elements.chat_id.value, channel_username: form.elements.channel_username.value,
        enabled: form.elements.enabled.value, reconcile_interval_seconds: form.elements.reconcile_interval_seconds.value,
        dedup_window_seconds: form.elements.dedup_window_seconds.value, notes: form.elements.notes.value
      };
    }
    function restoreChannelFormState(state) {
      if (!state || (!state.dirty && state.mode !== 'edit')) return;
      const form = document.getElementById('channelForm');
      if (!form) return;
      form.elements.id.value = state.id || '';
      form.elements.name.value = state.name || '';
      form.elements.source_type.value = state.source_type || 'public_web';
      form.elements.chat_id.value = state.chat_id || '';
      form.elements.channel_username.value = state.channel_username || '';
      form.elements.enabled.value = state.enabled || 'true';
      form.elements.reconcile_interval_seconds.value = state.reconcile_interval_seconds || 30;
      form.elements.dedup_window_seconds.value = state.dedup_window_seconds || 3600;
      form.elements.notes.value = state.notes || '';
      form.dataset.mode = state.mode || 'create';
      form.dataset.dirty = state.dirty ? 'true' : 'false';
      const submit = document.getElementById('channelSubmitButton');
      if (submit) submit.textContent = '保存频道';
    }
    function shouldDeferBackgroundLoad() {
      const form = document.getElementById('channelForm');
      if (!form) return false;
      if (getCurrentView() !== 'channels') return false;
      if (form.dataset.mode === 'edit' || form.dataset.dirty === 'true') return true;
      const active = document.activeElement;
      return !!(active && form.contains(active));
    }
    function renderView(data, view) {
      const channelFormState = captureChannelFormState();
      const ctx = buildContext(data);
      renderStatusStrip(data, ctx.ui, ctx.overview, ctx.directUseProfile);
      document.getElementById('viewMount').innerHTML = VIEW_RENDERERS[view](ctx);
      bindNav();
      bindForms();
      restoreChannelFormState(channelFormState);
    }
    function render(data) {
      window.currentState = data;
      document.getElementById('modeBox').textContent = displayTradingMode(data.config.trading.mode) + ' / ' + displayExecutionMode(data.config.trading.execution_mode);
      document.getElementById('webBindBox').textContent = data.wiring.web_server_active ? ('当前监听 ' + data.wiring.web_bind) : ('配置监听 ' + data.wiring.web_bind);
      const activeView = getCurrentView();
      renderNav(activeView);
      renderView(data, activeView);
    }
    async function load(options){
      const background = !!(options && options.background);
      if (background && shouldDeferBackgroundLoad()) return;
      const requestId = ++latestLoadRequestId;
      const data = await api('/api/state');
      if (!data || requestId !== latestLoadRequestId) return;
      if (background && shouldDeferBackgroundLoad()) return;
      render(data);
    }
    function setChannelForm(channel){
      const form = document.getElementById('channelForm');
      if (!form) return;
      form.elements.id.value = channel && channel.id ? channel.id : '';
      form.elements.name.value = channel && channel.name ? channel.name : '';
      form.elements.source_type.value = channel && channel.source_type ? channel.source_type : 'public_web';
      form.elements.chat_id.value = channel && channel.chat_id ? channel.chat_id : '';
      form.elements.channel_username.value = channel && channel.channel_username ? channel.channel_username : '';
      form.elements.enabled.value = String(channel && channel.enabled !== undefined ? channel.enabled : true);
      form.elements.reconcile_interval_seconds.value = channel && channel.reconcile_interval_seconds ? channel.reconcile_interval_seconds : 30;
      form.elements.dedup_window_seconds.value = channel && channel.dedup_window_seconds ? channel.dedup_window_seconds : 3600;
      form.elements.notes.value = channel && channel.notes ? channel.notes : '';
      form.dataset.mode = channel ? 'edit' : 'create';
      form.dataset.dirty = 'false';
      const submit = document.getElementById('channelSubmitButton');
      if (submit) submit.textContent = '保存频道';
    }
    function bindNav() {
      document.querySelectorAll('[data-nav-view]').forEach(link => {
        if (link.dataset.bound === 'true') return;
        link.dataset.bound = 'true';
        link.addEventListener('click', event => {
          event.preventDefault();
          setCurrentView(link.dataset.navView, false);
        });
      });
    }
    function bindForms(){
      const modeForm = document.getElementById('modeForm');
      if (modeForm && modeForm.dataset.bound !== 'true') {
        modeForm.dataset.bound = 'true';
        modeForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          try {
            await api('/api/config', {method:'POST', body: JSON.stringify({trading:{ mode:f.get('mode'), execution_mode:f.get('execution_mode'), default_leverage:Number(f.get('default_leverage')), paused:f.get('paused') === 'true' }})});
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const riskForm = document.getElementById('riskForm');
      if (riskForm && riskForm.dataset.bound !== 'true') {
        riskForm.dataset.bound = 'true';
        riskForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          try {
            await api('/api/config', {method:'POST', body: JSON.stringify({trading:{ global_tp_sl_enabled:f.get('global_tp_sl_enabled') === 'true', global_take_profit_ratio:Number(f.get('global_take_profit_ratio')), global_stop_loss_ratio:Number(f.get('global_stop_loss_ratio')), readonly_close_only:f.get('readonly_close_only') === 'true' }})});
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const aiForm = document.getElementById('aiForm');
      if (aiForm && aiForm.dataset.bound !== 'true') {
        aiForm.dataset.bound = 'true';
        aiForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          try {
            await api('/api/config', {method:'POST', body: JSON.stringify({ai:{ provider:String(f.get('provider')).trim(), model:String(f.get('model')).trim(), thinking:String(f.get('thinking')), timeout_seconds:Number(f.get('timeout_seconds')), system_prompt:String(f.get('system_prompt')) }})});
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const telegramForm = document.getElementById('telegramForm');
      if (telegramForm && telegramForm.dataset.bound !== 'true') {
        telegramForm.dataset.bound = 'true';
        telegramForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          const telegramPatch = { report_topic:String(f.get('report_topic')), operator_target:String(f.get('operator_target')), operator_thread_id:Number(f.get('operator_thread_id') || 0), poll_interval_seconds:Number(f.get('poll_interval_seconds')) };
          const botToken = String(f.get('bot_token') || '').trim();
          const clearBotToken = String(f.get('clear_bot_token') || '') === 'true';
          if (clearBotToken) { telegramPatch.bot_token = ''; } else if (botToken) { telegramPatch.bot_token = botToken; }
          try {
            await api('/api/config', {method:'POST', body: JSON.stringify({telegram:telegramPatch})});
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const channelForm = document.getElementById('channelForm');
      if (channelForm && channelForm.dataset.bound !== 'true') {
        channelForm.dataset.bound = 'true';
        channelForm.dataset.mode = channelForm.dataset.mode || 'create';
        channelForm.dataset.dirty = channelForm.dataset.dirty || 'false';
        const markDirty = () => { channelForm.dataset.dirty = 'true'; };
        channelForm.addEventListener('input', markDirty);
        channelForm.addEventListener('change', markDirty);
        channelForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          try {
            await api('/api/channels/upsert', {method:'POST', body: JSON.stringify({ id:String(f.get('id')), name:String(f.get('name')), source_type:String(f.get('source_type')), chat_id:String(f.get('chat_id')), channel_username:String(f.get('channel_username')), enabled:String(f.get('enabled')) === 'true', reconcile_interval_seconds:Number(f.get('reconcile_interval_seconds')), dedup_window_seconds:Number(f.get('dedup_window_seconds')), notes:String(f.get('notes')) })});
            e.target.reset();
            setChannelForm(null);
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      document.querySelectorAll('[data-channel-action]').forEach(button => {
        if (button.dataset.bound === 'true') return;
        button.dataset.bound = 'true';
        button.addEventListener('click', async e => {
          const channelId = e.currentTarget.dataset.channelId;
          const action = e.currentTarget.dataset.channelAction;
          const state = window.currentState || await api('/api/state');
          const channel = state.config.telegram.channels.find(item => item.id === channelId);
          try {
            if (action === 'edit') {
              if (getCurrentView() !== 'channels') setCurrentView('channels', false);
              setChannelForm(channel || null);
              return;
            }
            if (action === 'toggle') {
              await api('/api/channels/toggle', {method:'POST', body: JSON.stringify({channel_id: channelId, enabled: !(channel && channel.enabled)})});
            } else if (action === 'remove') {
              await api('/api/channels/remove', {method:'POST', body: JSON.stringify({channel_id: channelId})});
            }
            await load();
          } catch (err) { alert(err.message); }
        });
      });
      const injectForm = document.getElementById('injectForm');
      if (injectForm && injectForm.dataset.bound !== 'true') {
        injectForm.dataset.bound = 'true';
        injectForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          try {
            await api('/api/inject-message', {method:'POST', body: JSON.stringify({ text:String(f.get('text')), chat_id:String(f.get('chat_id')), message_id:Number(f.get('message_id')), event_type:String(f.get('event_type')), version:f.get('version') ? Number(f.get('version')) : null, use_configured_okx_path:String(f.get('execution_path')) === 'configured' })});
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const operatorCommandForm = document.getElementById('operatorCommandForm');
      if (operatorCommandForm && operatorCommandForm.dataset.bound !== 'true') {
        operatorCommandForm.dataset.bound = 'true';
        operatorCommandForm.addEventListener('submit', async e => {
          e.preventDefault();
          const f = new FormData(e.target);
          try {
            const result = await api('/api/actions/operator-command', {method:'POST', body: JSON.stringify({text:String(f.get('text'))})});
            alert(result.reply || result.status || '成功');
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const closeAllButton = document.getElementById('closeAllButton');
      if (closeAllButton && closeAllButton.dataset.bound !== 'true') {
        closeAllButton.dataset.bound = 'true';
        closeAllButton.addEventListener('click', async () => {
          try { await api('/api/positions/close', {method:'POST', body: JSON.stringify({})}); await load(); } catch (err) { alert(err.message); }
        });
      }
      const pauseButton = document.getElementById('pauseButton');
      if (pauseButton && pauseButton.dataset.bound !== 'true') {
        pauseButton.dataset.bound = 'true';
        pauseButton.addEventListener('click', async () => {
          try { await api('/api/actions/pause', {method:'POST', body: JSON.stringify({reason:'Web UI 手动暂停'})}); await load(); } catch (err) { alert(err.message); }
        });
      }
      const resumeButton = document.getElementById('resumeButton');
      if (resumeButton && resumeButton.dataset.bound !== 'true') {
        resumeButton.dataset.bound = 'true';
        resumeButton.addEventListener('click', async () => {
          try { await api('/api/actions/resume', {method:'POST', body: JSON.stringify({reason:'Web UI 手动恢复'})}); await load(); } catch (err) { alert(err.message); }
        });
      }
      const reconcileButton = document.getElementById('reconcileButton');
      if (reconcileButton && reconcileButton.dataset.bound !== 'true') {
        reconcileButton.dataset.bound = 'true';
        reconcileButton.addEventListener('click', async () => {
          try { const result = await api('/api/actions/reconcile', {method:'POST', body: JSON.stringify({})}); alert(result.detail); await load(); } catch (err) { alert(err.message); }
        });
      }
      const topicTestButton = document.getElementById('topicTestButton');
      if (topicTestButton && topicTestButton.dataset.bound !== 'true') {
        topicTestButton.dataset.bound = 'true';
        topicTestButton.addEventListener('click', async () => {
          try {
            const result = await api('/api/actions/topic-test', {method:'POST', body: JSON.stringify({})});
            const detail = result.reason || result.stderr || result.target_link || result.target || '';
            alert(result.sent ? ('话题发送自检成功: ' + detail) : ('话题发送自检' + (result.status || '失败') + ': ' + detail));
            await load();
          } catch (err) { alert(err.message); }
        });
      }
      const resetLocalStateButton = document.getElementById('resetLocalStateButton');
      if (resetLocalStateButton && resetLocalStateButton.dataset.bound !== 'true') {
        resetLocalStateButton.dataset.bound = 'true';
        resetLocalStateButton.addEventListener('click', async () => {
          if (!confirm('确认重置本地运行态吗？这只会清理本地 DB/日志/session 状态，不会触碰任何外部 OKX demo 持仓。')) return;
          try { const result = await api('/api/actions/reset-local-state', {method:'POST', body: JSON.stringify({})}); alert(result.detail); await load(); } catch (err) { alert(err.message); }
        });
      }
      document.querySelectorAll('[data-close-symbol]').forEach(button => {
        if (button.dataset.bound === 'true') return;
        button.dataset.bound = 'true';
        button.addEventListener('click', async e => {
          try { await api('/api/positions/close', {method:'POST', body: JSON.stringify({symbol: e.currentTarget.dataset.closeSymbol})}); await load(); } catch (err) { alert(err.message); }
        });
      });
    }
    window.addEventListener('popstate', () => {
      renderNav(getCurrentView());
      if (window.currentState) renderView(window.currentState, getCurrentView());
    });
    bindNav();
    renderNav(getCurrentView());
    load();
    setInterval(() => { load({background:true}); }, 5000);
  </script>
</body></html>"""
    return (
        html.replace("__DEMO_SIGNAL_TEXT__", json.dumps(DEFAULT_DEMO_SIGNAL_TEXT))
        .replace("__DEMO_SIGNAL_CHAT_ID__", DEFAULT_DEMO_SIGNAL_CHAT_ID)
        .replace("__DEMO_SIGNAL_MESSAGE_ID__", str(DEFAULT_DEMO_SIGNAL_MESSAGE_ID))
        .replace("__INITIAL_VIEW__", _normalize_web_view(initial_view))
    )


_STATUS_LABELS = {
    "unknown": "未知",
    "pass": "通过",
    "fail": "失败",
    "warn": "警告",
    "ok": "正常",
    "error": "错误",
    "ready": "就绪",
    "configured": "已配置",
    "manual_ready": "可手动直用",
    "blocked": "受阻",
    "partial": "部分就绪",
    "disabled": "已禁用",
    "locked": "已锁定",
    "legacy": "遗留路径",
    "simulated": "模拟路径",
    "attention": "需关注",
    "open": "待处理",
    "idle": "未开始",
    "reachable": "可达",
    "unreachable": "不可达",
    "invalid": "无效",
    "running": "运行中",
    "paused": "已暂停",
    "observe": "观察模式",
    "not_connected": "未连接",
    "missing_target": "缺少目标",
    "sent": "已发送",
    "failed": "失败",
    "heuristic": "启发式",
}

_VERIFICATION_STATUS_LABELS = {
    "ok": "通过",
    "warn": "警告",
    "error": "错误",
    "unknown": "未知",
}

_READINESS_LABELS = {
    "config_file": "配置文件",
    "config_persistence": "配置可写性",
    "data_dir": "数据目录",
    "sqlite": "SQLite 数据库",
    "web_auth": "Web 鉴权",
    "demo_only_guard": "仅演示防护",
    "trading_runtime": "交易运行态",
    "okx_demo": "OKX Demo",
    "telegram_watcher": "Telegram 轮询",
    "telegram_mtproto": "MTProto",
    "telegram_delete_events": "删除事件",
    "openclaw_cli": "OpenClaw CLI",
    "topic_logger": "话题发送",
    "operator_commands": "操作员命令",
    "web_bind": "Web 绑定",
    "reconciliation": "对账",
    "simulated_positions": "模拟持仓",
}

_CAPABILITY_LABELS = {
    "current_operating_profile": "当前运行画像",
    "manual_demo_pipeline": "手动演示链路",
    "okx_demo_execution": "OKX Demo 执行",
    "telegram_ingestion": "Telegram 自动采集",
    "operator_topic": "操作员话题",
    "demo_only_guard": "仅演示防护",
}

_ACTIVATION_LABELS = {
    "overall_profile": "总体画像",
    "manual_demo": "手动演示",
    "configured_okx_demo": "已配置 OKX Demo",
    "automatic_telegram": "自动 Telegram",
    "operator_topic_outbound": "话题出站",
    "operator_topic_inbound": "话题入站",
    "demo_only_guard": "仅演示防护",
}

_GAP_LABELS = {
    "telegram_source_channel": "Telegram 源频道",
    "telegram_source_legacy_bot_api": "遗留 bot_api 源频道",
    "telegram_mtproto": "MTProto 采集",
    "telegram_delete_events": "Telegram 删除事件",
    "telegram_reconcile_history": "Telegram 对账历史",
    "okx_private_ws": "OKX 私有 WebSocket",
    "okx_demo_action_coverage": "OKX Demo 动作覆盖",
    "okx_rest_connectivity": "OKX REST 连通性",
    "operator_topic": "操作员话题",
    "operator_topic_outbound": "操作员话题出站",
    "trading_paused": "交易暂停",
}

_SCOPE_LABELS = {
    "telegram": "Telegram",
    "okx": "OKX",
    "runtime": "运行时",
}

_TOPIC_SOURCE_LABELS = {
    "operator_target": "操作员目标配置",
    "report_topic": "汇报话题配置",
    "": "未配置",
}

_OPERATOR_INGRESS_LABELS = {
    "not_configured": "未配置机器人入站",
    "ready": "机器人入站可用",
    "configured_without_bot_token": "仅出站，未配置机器人令牌",
}

_SECRET_SOURCE_LABELS = {
    "missing": "未提供",
    "env": "环境变量",
    "config": "配置文件",
}

_TELEGRAM_MODE_LABELS = {
    "bot_api_and_public_web_polling": "bot_api + public_web 轮询",
    "bot_api_polling": "bot_api 轮询",
    "public_web_polling_with_bot_api_configured_without_token": "public_web 轮询（配置了 bot_api 但无 token）",
    "public_web_polling": "public_web 轮询",
    "bot_api_configured_without_token": "仅配置 bot_api，缺少 token",
    "mtproto_configured_not_implemented": "已配置 MTProto，但当前构建未实现",
    "idle": "未配置",
}

_EXECUTION_PATH_LABELS = {
    "simulated_demo": "模拟 Demo 路径",
    "real_demo_rest": "已配置 OKX Demo REST 路径",
}

_OKX_ACTION_LABELS = {
    "open_long": "开多",
    "open_short": "开空",
    "add_long": "加多",
    "add_short": "加空",
    "reduce_long": "减多",
    "reduce_short": "减空",
    "reverse_to_long": "反手开多",
    "reverse_to_short": "反手开空",
    "close_long": "平多",
    "close_short": "平空",
    "close_all": "全部平仓",
    "cancel_orders": "撤单",
    "update_protection": "更新保护",
}

_TEXT_REPLACEMENTS = [
    ("TG OKX Auto Trade Direct-Use Summary", "TG OKX Auto Trade 直接使用摘要"),
    ("generated_at:", "生成时间:"),
    ("status:", "状态:"),
    ("Current profile", "当前画像"),
    ("Paths", "路径"),
    ("Direct commands", "直接命令"),
    ("Key capability details", "关键能力说明"),
    ("Readiness warnings", "就绪告警"),
    ("Remaining gaps", "剩余缺口"),
    ("Next steps", "下一步"),
    ("This summary is redacted and demo-only. Live trading stays disabled.", "此摘要已做脱敏处理，仅用于演示路径。实盘交易保持禁用。"),
    ("profile_detail:", "画像说明:"),
    ("next_action:", "下一步操作:"),
    ("topic_delivery_state:", "话题发送状态:"),
    ("enabled public_web source channel", "已启用 public_web 源频道"),
    ("operator topic target", "操作员话题目标"),
    ("legacy/internal", "遗留/内部"),
    ("none", "无"),
]

_REGEX_TEXT_REPLACEMENTS = [
    (
        re.compile(r"^Verify local readiness with `(.+)`\.$"),
        lambda m: f"用 `{m.group(1)}` 核验本地就绪情况。",
    ),
    (
        re.compile(r"^Start the web console with `(.+)` and open (.+)\.$"),
        lambda m: f"用 `{m.group(1)}` 启动 Web 控制台，并打开 {m.group(2)}。",
    ),
    (
        re.compile(r"^Dry-run the pipeline with `(.+)`\.$"),
        lambda m: f"用 `{m.group(1)}` 对链路做一次 dry-run。",
    ),
    (
        re.compile(r"^Web: open (.+) and authenticate with the configured 6-digit PIN\.$"),
        lambda m: f"Web：打开 {m.group(1)}，并使用已配置的 6 位 PIN 登录。",
    ),
    (
        re.compile(r"^Safe smoke: run `(.+)` to validate the local simulated pipeline\.$"),
        lambda m: f"安全冒烟：运行 `{m.group(1)}` 以验证本地模拟链路。",
    ),
    (
        re.compile(r"^Automatic Telegram ingestion is supported through enabled public_web channels only on the intended path\.$"),
        lambda _m: "在主支持路径上，自动 Telegram 采集仅支持已启用的 public_web 频道。",
    ),
    (
        re.compile(r"^Prefer local secret storage in `(.+)`\. Use `(.+)` to move inline Telegram/OKX secrets into `\.env` without enabling live mode\.$"),
        lambda m: f"建议把密钥存放在本地 `{m.group(1)}`。可用 `{m.group(2)}` 将内联的 Telegram/OKX 密钥迁移到 `.env`，且不会启用实盘模式。",
    ),
    (
        re.compile(r"^Credentialed OKX demo: run `(.+)` only when you intentionally want an OKX demo REST order\.$"),
        lambda m: f"带凭证 OKX Demo：仅在你明确需要发出 OKX Demo REST 订单时才运行 `{m.group(1)}`。",
    ),
    (
        re.compile(r"^Configured OKX execution uses the OKX demo REST path only\. Manual `inject-message` stays simulated by default; add `--real-okx-demo` when you intentionally want a credentialed OKX demo order\.$"),
        lambda _m: "当前已配置的 OKX 执行仅使用 OKX Demo REST 路径。手动 `inject-message` 默认仍走模拟；只有在你明确需要带凭证的 OKX Demo 下单时，才添加 `--real-okx-demo`。",
    ),
    (
        re.compile(r"^Configured OKX demo REST coverage is still partial\. Keep (.+) on the simulated path until REST support is extended\.$"),
        lambda m: f"当前已配置的 OKX Demo REST 覆盖仍不完整。{m.group(1)} 在 REST 支持扩展完成前请继续走模拟路径。",
    ),
    (
        re.compile(r"^Inline Telegram/OKX secrets are still stored in the config file\. Prefer `(.+)` so the local `\.env` carries secrets while the checked config stays redacted\.$"),
        lambda m: f"Telegram/OKX 密钥仍以内联形式保存在配置文件中。建议使用 `{m.group(1)}`，让本地 `.env` 承载密钥，同时保持纳管配置脱敏。",
    ),
    (
        re.compile(r"^Operator topic smoke: run `(.+)` after confirming outbound Telegram delivery is allowed\.$"),
        lambda m: f"操作员话题冒烟：确认允许 Telegram 出站发送后，运行 `{m.group(1)}`。",
    ),
    (
        re.compile(r"^Optional operator topic: set `telegram\.operator_target` to a topic target such as `(.+)`\.$"),
        lambda m: f"可选操作员话题：可将 `telegram.operator_target` 设置为类似 `{m.group(1)}` 的话题目标。",
    ),
    (
        re.compile(r"^Add at least one enabled `public_web` source channel before expecting automatic source-channel ingestion\.$"),
        lambda _m: "在期待自动源频道采集前，至少添加一个启用的 `public_web` 源频道。",
    ),
    (
        re.compile(r"^Web config changed to (.+) but the running server is still bound to (.+); restart `serve` to apply the new bind address\.$"),
        lambda m: f"Web 配置已改为 {m.group(1)}，但当前运行中的服务仍绑定在 {m.group(2)}；重启 `serve` 后新地址才会生效。",
    ),
    (
        re.compile(r"^Add at least one enabled `public_web` Telegram channel entry before expecting supported live signal ingestion\. Manual Web/CLI demo injection already works without a Telegram source channel\. The Web channel form accepts `channel_username` values such as `@username`, `https://t\.me/<username>`, and `https://t\.me/s/<username>`\.$"),
        lambda _m: "在期待主支持的实时信号采集前，至少添加一个已启用的 `public_web` Telegram 频道。即使没有 Telegram 源频道，手动 Web/CLI 演示注入也已可用。Web 频道表单接受 `@username`、`https://t.me/<username>` 和 `https://t.me/s/<username>` 这类 `channel_username`。",
    ),
    (
        re.compile(r"^Enabled `bot_api` Telegram channels remain on a legacy/internal path\. Migrate the intended automatic ingestion path to enabled `public_web` channels\.$"),
        lambda _m: "已启用的 `bot_api` Telegram 频道仍处于遗留/内部路径。若要走主支持的自动采集路径，请迁移到已启用的 `public_web` 频道。",
    ),
    (
        re.compile(r"^Trading is paused\. Resume from Web or call the runtime resume action after fixing the underlying issue\.$"),
        lambda _m: "交易当前处于暂停状态。修复底层问题后，可在 Web 中恢复，或调用运行时恢复动作。",
    ),
    (
        re.compile(r"^Optional: set `telegram\.report_topic` or `telegram\.operator_target` to forward runtime logs into the operator topic\. `https://t\.me/c/<chat>/<topic>` links are accepted\.$"),
        lambda _m: "可选：设置 `telegram.report_topic` 或 `telegram.operator_target`，把运行时日志转发到操作员话题。支持 `https://t.me/c/<chat>/<topic>` 链接。",
    ),
    (
        re.compile(r"^Topic delivery is currently disabled by `TG_OKX_DISABLE_TOPIC_SEND=1`; unset it before expecting operator-topic smoke logs\.$"),
        lambda _m: "当前因 `TG_OKX_DISABLE_TOPIC_SEND=1` 禁用了话题发送；如需期待操作员话题冒烟日志，请先取消该环境变量。",
    ),
    (
        re.compile(r"^Install or expose the `openclaw` CLI before expecting operator-topic smoke logs or runtime broadcasts\.$"),
        lambda _m: "在期待操作员话题冒烟日志或运行时广播前，请先安装或暴露 `openclaw` CLI。",
    ),
    (
        re.compile(r"^Last operator-topic delivery failed; fix Telegram delivery/network access and rerun the topic smoke action\.$"),
        lambda _m: "最近一次操作员话题发送失败；请修复 Telegram 发送或网络访问问题后重新执行话题冒烟动作。",
    ),
    (
        re.compile(r"^Outbound operator-topic delivery has already been verified in this runtime; rerun the smoke after changing topic wiring\.$"),
        lambda _m: "当前运行时已验证过操作员话题出站发送；修改话题接线后请重新做一次冒烟验证。",
    ),
    (
        re.compile(r"^The intended operator flow is outbound topic logging plus Web/local operator controls; Telegram inbound bot commands are legacy/internal only\.$"),
        lambda _m: "当前主支持的操作员流程是出站话题日志配合 Web/本地操作控制；Telegram 入站 bot 命令仅属遗留/内部路径。",
    ),
    (
        re.compile(r"^Verify outbound operator-topic delivery with `(.+)` or the Web Topic Smoke action\.$"),
        lambda m: f"可用 `{m.group(1)}` 或 Web 上的话题自检动作验证操作员话题出站发送。",
    ),
    (
        re.compile(r"^Enabled mtproto channels are stored but not consumed in this dependency-light build; use public_web for the intended supported automatic ingestion path\.$"),
        lambda _m: "当前轻依赖构建会保存已启用的 mtproto 频道，但不会实际消费；若要走主支持的自动采集路径，请使用 public_web。",
    ),
    (
        re.compile(r"^Public Telegram web polling is ready for (\d+) enabled public_web channel\(s\)\. This is the intended supported automatic ingestion path\.$"),
        lambda m: f"公共 Telegram 网页轮询已就绪，当前有 {m.group(1)} 个启用的 public_web 频道。这是当前主支持的自动采集路径。",
    ),
    (
        re.compile(r"^Start the runtime and watch for live new/edit events from the configured public channel pages\.$"),
        lambda _m: "启动运行时，并观察已配置公开频道页面上的实时新增/编辑事件。",
    ),
    (
        re.compile(r"^(\d+) enabled bot_api channel\(s\) are configured in a legacy-compatible path\. The intended supported automatic ingestion path is public_web scraping\.$"),
        lambda m: f"当前配置了 {m.group(1)} 个启用的 bot_api 频道，但它们仍处于遗留兼容路径。主支持的自动采集路径是 public_web 抓取。",
    ),
    (
        re.compile(r"^Automatic Telegram ingestion is not ready because no enabled public_web source channel is configured\.$"),
        lambda _m: "自动 Telegram 采集尚未就绪，因为还没有配置启用的 public_web 源频道。",
    ),
    (
        re.compile(r"^Add at least one enabled public_web channel in Web > Channels or config\.telegram\.channels using channel_username / https://t\.me/s/<username>\.$"),
        lambda _m: "请在 Web > 频道配置 或 `config.telegram.channels` 中至少添加一个启用的 public_web 频道，使用 `channel_username` / `https://t.me/s/<username>`。",
    ),
    (
        re.compile(r"^Operator topic logging is not configured\.$"),
        lambda _m: "尚未配置操作员话题日志。",
    ),
    (
        re.compile(r"^Set telegram\.operator_target or telegram\.report_topic to enable operator-topic logs\.$"),
        lambda _m: "请设置 `telegram.operator_target` 或 `telegram.report_topic` 以启用操作员话题日志。",
    ),
    (
        re.compile(r"^Set telegram\.operator_target or telegram\.report_topic; topic links like https://t\.me/c/<chat>/<topic> are accepted\.$"),
        lambda _m: "请设置 `telegram.operator_target` 或 `telegram.report_topic`；支持 `https://t.me/c/<chat>/<topic>` 这类话题链接。",
    ),
    (
        re.compile(r"^Operator topic target (.+) is configured, but delivery is disabled by TG_OKX_DISABLE_TOPIC_SEND=1\.$"),
        lambda m: f"已配置操作员话题目标 {m.group(1)}，但发送被 `TG_OKX_DISABLE_TOPIC_SEND=1` 禁用。",
    ),
    (
        re.compile(r"^Unset TG_OKX_DISABLE_TOPIC_SEND before running a real operator-topic smoke test\.$"),
        lambda _m: "如需执行真实操作员话题冒烟测试，请先取消 `TG_OKX_DISABLE_TOPIC_SEND`。",
    ),
    (
        re.compile(r"^Operator topic target (.+) is configured, but the openclaw CLI is unavailable\.$"),
        lambda m: f"已配置操作员话题目标 {m.group(1)}，但本机不可用 `openclaw` CLI。",
    ),
    (
        re.compile(r"^Last operator topic attempt failed: (.+)$"),
        lambda m: f"最近一次操作员话题发送失败：{m.group(1)}",
    ),
    (
        re.compile(r"^Operator topic outbound delivery to (.+) has already been verified in this runtime\.$"),
        lambda m: f"当前运行时已经验证过向 {m.group(1)} 的操作员话题出站发送。",
    ),
    (
        re.compile(r"^Operator topic outbound delivery to (.+) has already been verified in this runtime\. (.+)$"),
        lambda m: f"当前运行时已经验证过向 {m.group(1)} 的操作员话题出站发送。{_localize_operator_text(m.group(2))}",
    ),
    (
        re.compile(r"^Operator topic outbound delivery is configured for (.+), but it has not been verified yet in this runtime\.$"),
        lambda m: f"已为 {m.group(1)} 配置操作员话题出站发送，但当前运行时还没有验证成功发送。",
    ),
    (
        re.compile(r"^Operator topic outbound delivery is configured for (.+), but it has not been verified yet in this runtime\. (.+)$"),
        lambda m: f"已为 {m.group(1)} 配置操作员话题出站发送，但当前运行时还没有验证成功发送。{_localize_operator_text(m.group(2))}",
    ),
    (
        re.compile(r"^Use the Web 'Topic Smoke' button, runtime\.send_topic_test\(\), or the CLI topic-test command to verify outbound delivery\.$"),
        lambda _m: "可用 Web 的话题自检按钮、`runtime.send_topic_test()` 或 CLI `topic-test` 命令验证出站发送。",
    ),
    (
        re.compile(r"^Web login, config persistence, runtime state, and manual demo injection paths are ready\. Manual signal injection defaults to the simulated engine even when OKX demo REST is configured\.$"),
        lambda _m: "Web 登录、配置持久化、运行时状态和手动演示注入链路均已就绪。即使已配置 OKX Demo REST，手动信号注入默认仍走模拟引擎。",
    ),
    (
        re.compile(r"^Open (.+) or use the inject-message CLI command for a safe demo signal\.$"),
        lambda m: f"打开 {m.group(1)}，或使用 `inject-message` CLI 命令执行安全的演示信号注入。",
    ),
    (
        re.compile(r"^Live trading is hard-disabled by config validation and runtime guards in this build\.$"),
        lambda _m: "当前构建通过配置校验和运行时保护强制禁用实盘交易。",
    ),
    (
        re.compile(r"^Keep all validation in demo or simulated mode only\.$"),
        lambda _m: "所有验证都应保持在 demo 或模拟模式内。",
    ),
    (
        re.compile(r"^The current profile is ready for direct manual/demo use: Web login, config edits, runtime artifacts, manual demo injection, and the configured operator/OKX paths that do not require inbound Telegram\. Full always-on automation is still blocked by: (.+)\.$"),
        lambda m: f"当前画像已经可直接用于手动/演示操作：Web 登录、配置修改、运行时产物、手动演示注入，以及不依赖 Telegram 入站的已配置操作员/OKX 路径都已可用。完整常驻自动化仍受以下条件阻塞：{m.group(1)}。",
    ),
    (
        re.compile(r"^Use the manual demo path now, then add an enabled public_web source channel and optional topic wiring before expecting the supported automatic signal flow\.$"),
        lambda _m: "现在可以先走手动演示路径；若要进入主支持的自动信号流，再补上启用的 public_web 源频道以及可选的话题接线。",
    ),
    (
        re.compile(r"^Web control, Telegram ingestion, operator-topic wiring, and demo-only execution are all configured for direct use in this build\.$"),
        lambda _m: "当前构建已经完成 Web 控制、Telegram 采集、操作员话题接线和仅演示执行的直用配置。",
    ),
    (
        re.compile(r"^Keep validation on demo/simulated paths only and start the runtime for live source-channel monitoring\.$"),
        lambda _m: "请继续把验证限制在 demo/模拟路径内，并启动运行时开始监控真实源频道。",
    ),
    (
        re.compile(r"^Web, config persistence, and manual demo controls are wired, but trading is currently paused\. Outstanding automatic-ingestion prerequisites: (.+)\.$"),
        lambda m: f"Web、配置持久化和手动演示控制都已接好，但当前交易处于暂停状态。自动采集仍缺少：{m.group(1)}。",
    ),
    (
        re.compile(r"^Fix the pause reason and resume trading before relying on the configured demo profile\.$"),
        lambda _m: "请先处理暂停原因并恢复交易，再依赖当前已配置的演示画像。",
    ),
    (
        re.compile(r"^Real OKX demo REST execution is configured, but the endpoint reachability check is not healthy: (.+)$"),
        lambda m: f"已配置真实 OKX Demo REST 执行，但端点连通性检查异常：{m.group(1)}",
    ),
    (
        re.compile(r"^Fix local DNS/network reachability to the OKX demo REST endpoint, then rerun the OKX demo smoke test\.$"),
        lambda _m: "请先修复本地到 OKX Demo REST 端点的 DNS/网络连通性，再重跑 OKX Demo 冒烟测试。",
    ),
    (
        re.compile(r"^Real OKX demo REST execution is configured\. Orders remain restricted to demo/simulated trading only\. Configured REST coverage: (.+)\.$"),
        lambda m: f"已配置真实 OKX Demo REST 执行。订单仍只允许在 demo/模拟交易范围内执行。当前 REST 覆盖动作：{m.group(1)}。",
    ),
    (
        re.compile(r"^Use a small demo-only signal path or the credentialed OKX demo smoke test to validate exchange execution\.$"),
        lambda _m: "可用小规模仅演示信号路径，或带凭证的 OKX Demo 冒烟测试验证交易所执行链路。",
    ),
    (
        re.compile(r"^The local simulated OKX demo engine is active\. No real OKX demo REST calls will be made\.$"),
        lambda _m: "当前启用的是本地模拟 OKX Demo 引擎，不会真正调用 OKX Demo REST。",
    ),
    (
        re.compile(r"^Set okx\.enabled=true with demo credentials when you want the real OKX demo REST path\.$"),
        lambda _m: "如果需要走真实 OKX Demo REST 路径，请在提供 demo 凭证后设置 `okx.enabled=true`。",
    ),
    (
        re.compile(r"^No enabled public_web source channel is configured yet\.$"),
        lambda _m: "当前还没有配置启用的 public_web 源频道。",
    ),
    (
        re.compile(r"^Add at least one enabled public_web channel in Web > Channels or config\.telegram\.channels\.$"),
        lambda _m: "请在 Web > 频道配置 或 `config.telegram.channels` 中至少添加一个启用的 public_web 频道。",
    ),
    (
        re.compile(r"^Enabled bot_api channels remain in a legacy/internal path\. The intended supported automatic ingestion path is public_web scraping\.$"),
        lambda _m: "已启用的 bot_api 频道仍属于遗留/内部路径。主支持的自动采集路径是 public_web 抓取。",
    ),
    (
        re.compile(r"^Switch the configured source channels to public_web before treating automatic ingestion as production-intended\.$"),
        lambda _m: "若要把自动采集视为正式主路径，请把已配置源频道切换为 public_web。",
    ),
    (
        re.compile(r"^MTProto channels can be stored in config, but active MTProto watching is not implemented in this build\.$"),
        lambda _m: "配置里可以保存 MTProto 频道，但当前构建尚未实现主动 MTProto 监听。",
    ),
    (
        re.compile(r"^Use public_web channels for the intended supported automatic ingestion path, or add a Telethon/MTProto adapter before relying on MTProto sources\.$"),
        lambda _m: "若要走主支持的自动采集路径，请使用 public_web；如果要依赖 MTProto 源，请先补上 Telethon/MTProto 适配器。",
    ),
    (
        re.compile(r"^Some enabled Telegram channels request delete/revoke handling, but Telegram delete events are not implemented in this build: (.+)\.$"),
        lambda m: f"部分已启用 Telegram 频道要求处理删除/撤回事件，但当前构建尚未实现 Telegram 删除事件：{m.group(1)}。",
    ),
    (
        re.compile(r"^Keep delete/revoke expectations off for now, or add an adapter that can surface delete events before relying on that path\.$"),
        lambda _m: "当前请不要依赖删除/撤回事件；如确有需要，请先补上能够暴露删除事件的适配器。",
    ),
    (
        re.compile(r"^Bot API reconciliation only replays the in-process recent buffer; it does not backfill true channel history after downtime\.$"),
        lambda _m: "Bot API 对账目前只会回放进程内的近期缓冲区，无法在停机后回补真实频道历史。",
    ),
    (
        re.compile(r"^Keep the runtime continuously connected for now, or add a stronger history source before treating reconciliation as authoritative\.$"),
        lambda _m: "当前请尽量保持运行时持续在线；如要把对账结果视为权威来源，请先补更强的历史数据源。",
    ),
    (
        re.compile(r"^OKX private WebSocket/account sync is not implemented in this build; real demo REST execution uses locally expected state after fills\.$"),
        lambda _m: "当前构建未实现 OKX 私有 WebSocket/账户同步；真实 Demo REST 执行在成交后依赖本地预期状态。",
    ),
    (
        re.compile(r"^Stay on demo/simulated validation, or add private WS/account polling before promoting beyond this build\.$"),
        lambda _m: "当前请继续停留在 demo/模拟验证；如要超出本构建能力范围，请先补上私有 WS/账户轮询。",
    ),
    (
        re.compile(r"^Configured OKX demo REST execution supports open/add/reduce/reverse/close/cancel flows, but still relies on the simulated engine for (.+) because this build only tracks attached protection locally and does not keep private WS/account sync\.$"),
        lambda m: f"当前已配置的 OKX Demo REST 执行已支持开仓/加仓/减仓/反手/平仓/撤单，但对 {m.group(1)} 仍依赖模拟引擎，因为此构建只在本地跟踪附加保护，且没有私有 WS/账户同步。",
    ),
    (
        re.compile(r"^Use the simulated path for those actions, or extend the OKX REST implementation before relying on credentialed demo execution for them\.$"),
        lambda _m: "这些动作当前请继续走模拟路径；若要依赖带凭证的 Demo 执行，请先扩展 OKX REST 实现。",
    ),
    (
        re.compile(r"^OKX demo REST credentials are configured, but the configured endpoint is not currently reachable: (.+)$"),
        lambda m: f"已配置 OKX Demo REST 凭证，但当前无法连通所配置的端点：{m.group(1)}",
    ),
    (
        re.compile(r"^Fix local DNS/network access to the OKX REST host, then rerun the OKX demo smoke test before relying on credentialed demo execution\.$"),
        lambda _m: "请先修复本地到 OKX REST 主机的 DNS/网络访问，再在依赖带凭证 Demo 执行前重跑 OKX Demo 冒烟测试。",
    ),
    (
        re.compile(r"^No operator topic target is configured for outbound logs\.$"),
        lambda _m: "尚未为出站日志配置操作员话题目标。",
    ),
    (
        re.compile(r"^Set telegram\.operator_target or telegram\.report_topic before expecting operator-topic delivery\.$"),
        lambda _m: "在期待操作员话题发送前，请先设置 `telegram.operator_target` 或 `telegram.report_topic`。",
    ),
    (
        re.compile(r"^Outbound operator-topic delivery to (.+) is disabled by TG_OKX_DISABLE_TOPIC_SEND=1\.$"),
        lambda m: f"向 {m.group(1)} 的操作员话题出站发送已被 `TG_OKX_DISABLE_TOPIC_SEND=1` 禁用。",
    ),
    (
        re.compile(r"^Unset TG_OKX_DISABLE_TOPIC_SEND before running a real topic smoke test\.$"),
        lambda _m: "如需执行真实话题冒烟测试，请先取消 `TG_OKX_DISABLE_TOPIC_SEND`。",
    ),
    (
        re.compile(r"^Outbound operator-topic delivery to (.+) is configured, but the openclaw CLI is unavailable\.$"),
        lambda m: f"已配置向 {m.group(1)} 的操作员话题出站发送，但本机不可用 `openclaw` CLI。",
    ),
    (
        re.compile(r"^Last outbound operator-topic attempt failed: (.+)$"),
        lambda m: f"最近一次操作员话题出站发送失败：{m.group(1)}",
    ),
    (
        re.compile(r"^Fix topic delivery/network access, then rerun the topic smoke action\.$"),
        lambda _m: "请先修复话题发送或网络访问问题，然后重新执行话题冒烟动作。",
    ),
    (
        re.compile(r"^Outbound operator-topic delivery to (.+) has been verified in this runtime\.$"),
        lambda m: f"当前运行时已经验证过向 {m.group(1)} 的操作员话题出站发送。",
    ),
    (
        re.compile(r"^Continue using topic-test or the Web Topic Smoke action after wiring changes to confirm delivery still works\.$"),
        lambda _m: "修改接线后，请继续用 `topic-test` 或 Web 的话题自检动作确认发送仍然正常。",
    ),
    (
        re.compile(r"^Outbound operator-topic delivery is configured for (.+), but this runtime has not verified a successful send yet\.$"),
        lambda m: f"已配置向 {m.group(1)} 的操作员话题出站发送，但当前运行时尚未验证成功发送。",
    ),
    (
        re.compile(r"^Use topic-test or the Web Topic Smoke action to verify outbound delivery\.$"),
        lambda _m: "可用 `topic-test` 或 Web 的话题自检动作验证出站发送。",
    ),
    (
        re.compile(r"^Install or expose the openclaw CLI before expecting topic delivery\.$"),
        lambda _m: "在期待话题发送前，请先安装或暴露 `openclaw` CLI。",
    ),
    (
        re.compile(r"^Validate Telegram delivery/network access, then rerun the topic smoke action\.$"),
        lambda _m: "请先确认 Telegram 发送与网络访问正常，然后重新执行话题冒烟动作。",
    ),
    (
        re.compile(r"^Telegram inbound operator commands are a legacy/internal bot path and are not part of the intended supported scope\.$"),
        lambda _m: "Telegram 入站操作员命令属于遗留/内部 bot 路径，不在当前主支持范围内。",
    ),
    (
        re.compile(r"^Telegram inbound bot commands remain a legacy/internal path and are not part of the supported public_web-first operator flow\.$"),
        lambda _m: "Telegram 入站 bot 命令仍属遗留/内部路径，不属于主支持的 public_web-first 操作员流程。",
    ),
    (
        re.compile(r"^No operator setup is required here for the supported public_web-first flow\.$"),
        lambda _m: "对主支持的 public_web-first 流程来说，这里不需要额外的操作员配置。",
    ),
    (
        re.compile(r"^Outbound topic logs can run without Telegram inbound bot commands\. Inbound bot control remains legacy/internal and is not a planned operator capability\.$"),
        lambda _m: "出站话题日志可以在没有 Telegram 入站 bot 命令时运行；入站 bot 控制仍是遗留/内部路径，不属于规划中的操作员能力。",
    ),
    (
        re.compile(r"^Keep using outbound topic logs plus Web/local operator commands on the intended path\.$"),
        lambda _m: "请继续在主支持路径上使用出站话题日志以及 Web/本地操作员命令。",
    ),
    (
        re.compile(r"^A legacy Telegram inbound bot path is still wired internally, but it is not part of the intended supported operator surface\.$"),
        lambda _m: "系统内部仍接着一条遗留的 Telegram 入站 bot 路径，但它不属于当前主支持的操作员界面。",
    ),
    (
        re.compile(r"^Keep the supported operator flow on outbound topic logs plus Web/local commands\.$"),
        lambda _m: "请把主支持的操作员流程保持在出站话题日志以及 Web/本地命令上。",
    ),
    (
        re.compile(r"^(.+) is writable$"),
        lambda m: f"{m.group(1)} 可写",
    ),
    (
        re.compile(r"^(.+) is not writable$"),
        lambda m: f"{m.group(1)} 不可写",
    ),
    (
        re.compile(r"^6-digit web PIN is configured$"),
        lambda _m: "已配置 6 位 Web PIN",
    ),
    (
        re.compile(r"^live trading remains disabled$"),
        lambda _m: "实盘交易保持禁用",
    ),
    (
        re.compile(r"^simulated OKX demo engine is active$"),
        lambda _m: "当前启用模拟 OKX Demo 引擎",
    ),
    (
        re.compile(r"^Only legacy bot_api channels are configured; switch to public_web channels for the intended supported automatic ingestion path\.$"),
        lambda _m: "当前只配置了遗留 bot_api 频道；若要走主支持的自动采集路径，请切换到 public_web 频道。",
    ),
    (
        re.compile(r"^No enabled public_web channels configured; add one in Web > Channels or config\.telegram\.channels for supported live ingestion\. Manual demo injection still works\.$"),
        lambda _m: "当前没有启用的 public_web 频道；若要使用主支持的实时采集，请在 Web > 频道配置 或 `config.telegram.channels` 中添加。手动演示注入仍可使用。",
    ),
    (
        re.compile(r"^Configured but not implemented in this build: (.+)$"),
        lambda m: f"已配置，但当前构建未实现：{m.group(1)}",
    ),
    (
        re.compile(r"^Delete/revoke handling is not implemented for enabled channels requesting it: (.+)$"),
        lambda m: f"这些启用频道请求了删除/撤回处理，但当前未实现：{m.group(1)}",
    ),
    (
        re.compile(r"^openclaw CLI not found; heuristic parser fallback will be used$"),
        lambda _m: "未找到 openclaw CLI；将回退到启发式解析器。",
    ),
    (
        re.compile(r"^topic delivery is disabled by TG_OKX_DISABLE_TOPIC_SEND=1$"),
        lambda _m: "话题发送已被 TG_OKX_DISABLE_TOPIC_SEND=1 禁用",
    ),
    (
        re.compile(r"^topic target is configured but openclaw CLI is unavailable$"),
        lambda _m: "已配置话题目标，但本机不可用 openclaw CLI",
    ),
    (
        re.compile(r"^topic delivery verified in this runtime: (.+)$"),
        lambda m: f"当前运行时已验证话题发送：{m.group(1)}",
    ),
    (
        re.compile(r"^Topic smoke succeeded(?:[: ]+)(.+)$"),
        lambda m: f"话题冒烟验证成功：{m.group(1)}",
    ),
    (
        re.compile(r"^topic delivery target configured: (.+)$"),
        lambda m: f"已配置话题发送目标：{m.group(1)}",
    ),
    (
        re.compile(r"^telegram\.report_topic / operator_target is not configured$"),
        lambda _m: "尚未配置 `telegram.report_topic` / `operator_target`",
    ),
    (
        re.compile(r"^Telegram inbound operator commands remain a legacy/internal bot path; use outbound topic logs plus Web/local operator controls on the supported path\.$"),
        lambda _m: "Telegram 入站操作员命令仍属遗留/内部 bot 路径；主支持路径请使用出站话题日志以及 Web/本地操作控制。",
    ),
    (
        re.compile(r"^Operator topic is optional for outbound logs\. Telegram inbound operator commands are not part of the supported public_web-first scope\.$"),
        lambda _m: "操作员话题对出站日志来说是可选项。Telegram 入站操作员命令不属于主支持的 public_web-first 范围。",
    ),
    (
        re.compile(r"^Install or expose the openclaw CLI before relying on operator-topic delivery\.$"),
        lambda _m: "在依赖操作员话题发送前，请先安装或暴露 `openclaw` CLI。",
    ),
    (
        re.compile(r"^Fix topic delivery/network access, then rerun the topic smoke action before relying on operator-topic delivery\.$"),
        lambda _m: "请先修复话题发送或网络访问问题，再重新执行话题冒烟动作，然后再依赖操作员话题发送。",
    ),
    (
        re.compile(r"^Outbound operator-topic delivery is disabled by TG_OKX_DISABLE_TOPIC_SEND=1\.$"),
        lambda _m: "操作员话题出站发送已被 `TG_OKX_DISABLE_TOPIC_SEND=1` 禁用。",
    ),
    (
        re.compile(r"^Unset TG_OKX_DISABLE_TOPIC_SEND before expecting operator-topic smoke logs or runtime broadcasts\.$"),
        lambda _m: "若要期待操作员话题冒烟日志或运行时广播，请先取消 `TG_OKX_DISABLE_TOPIC_SEND`。",
    ),
    (
        re.compile(r"^Config expects (.+), but the running HTTP server is still bound to (.+); restart serve to apply the new bind address$"),
        lambda m: f"配置期望绑定 {m.group(1)}，但当前 HTTP 服务仍运行在 {m.group(2)}；重启 serve 后才会切换到新地址。",
    ),
    (
        re.compile(r"^Reconciliation skipped because trading is paused$"),
        lambda _m: "交易已暂停，本次对账已跳过",
    ),
    (
        re.compile(r"^Reconciliation failed: (.+)$"),
        lambda m: f"对账失败：{m.group(1)}",
    ),
    (
        re.compile(r"^retried (\d+) incomplete message\(s\); replayed (\d+) buffered Telegram message\(s\)$"),
        lambda m: f"已重试 {m.group(1)} 条未完成消息；已回放 {m.group(2)} 条缓冲 Telegram 消息",
    ),
    (
        re.compile(r"^Trading is currently paused: (.+)$"),
        lambda m: f"交易当前处于暂停状态：{m.group(1)}",
    ),
    (
        re.compile(r"^Trading pipeline is active$"),
        lambda _m: "交易链路运行中",
    ),
    (
        re.compile(r"^Observe-only path is active$"),
        lambda _m: "当前处于仅观察路径",
    ),
    (
        re.compile(r"^Trading is paused$"),
        lambda _m: "交易已暂停",
    ),
    (
        re.compile(r"^OKX demo REST credentials are configured and endpoint reachability looks healthy$"),
        lambda _m: "已配置 OKX Demo REST 凭证，且端点连通性看起来正常。",
    ),
    (
        re.compile(r"^(\d+) simulated position snapshot\(s\) restored$"),
        lambda m: f"已恢复 {m.group(1)} 份模拟持仓快照",
    ),
    (
        re.compile(r"^Fix the underlying issue, then resume trading from Web or the runtime API\.$"),
        lambda _m: "请先修复底层问题，然后通过 Web 或运行时 API 恢复交易。",
    ),
    (
        re.compile(r"^topic target (.+)$"),
        lambda m: f"话题目标 {m.group(1)}",
    ),
    (
        re.compile(r"^telegram\.report_topic or operator_target not configured$"),
        lambda _m: "尚未配置 telegram.report_topic 或 operator_target",
    ),
    (
        re.compile(r"^Topic delivery disabled by TG_OKX_DISABLE_TOPIC_SEND=1$"),
        lambda _m: "话题发送已被 TG_OKX_DISABLE_TOPIC_SEND=1 禁用",
    ),
    (
        re.compile(r"^Watcher ready$"),
        lambda _m: "轮询器就绪",
    ),
    (
        re.compile(r"^(\d+) enabled public_web channel\(s\)$"),
        lambda m: f"{m.group(1)} 个已启用 public_web 频道",
    ),
    (
        re.compile(r"^No enabled public_web channels configured$"),
        lambda _m: "没有已启用的 public_web 频道",
    ),
    (
        re.compile(r"^Simulated OKX demo engine$"),
        lambda _m: "模拟 OKX Demo 引擎",
    ),
    (
        re.compile(r"^OKX demo REST path configured$"),
        lambda _m: "已配置 OKX Demo REST 路径",
    ),
    (
        re.compile(r"^Private WebSocket is not implemented in this build$"),
        lambda _m: "当前构建未实现私有 WebSocket",
    ),
    (
        re.compile(r"^SQLite storage is ready$"),
        lambda _m: "SQLite 存储已就绪",
    ),
    (
        re.compile(r"^Trading state initialized$"),
        lambda _m: "交易状态已初始化",
    ),
    (
        re.compile(r"^Reconciliation has not run yet$"),
        lambda _m: "尚未执行过对账",
    ),
    (
        re.compile(r"^Trading runtime state unavailable$"),
        lambda _m: "交易运行态信息暂不可用",
    ),
    (
        re.compile(r"^OKX gateway ready$"),
        lambda _m: "OKX 网关已就绪",
    ),
    (
        re.compile(r"^provider=(.+)$"),
        lambda m: f"提供方={m.group(1)}",
    ),
    (
        re.compile(r"^Replace with the real public Telegram webpage before enabling automatic ingestion\.$"),
        lambda _m: "启用自动采集前，请替换成真实的 Telegram 公共网页地址。",
    ),
]


def _status_label(value: str) -> str:
    return _STATUS_LABELS.get(str(value or ""), str(value or ""))


def _verification_status_label(value: str) -> str:
    return _VERIFICATION_STATUS_LABELS.get(str(value or ""), _status_label(str(value or "")))


def _localize_operator_text(value: str) -> str:
    text = str(value or "")
    localized = text
    for pattern, replacement in _REGEX_TEXT_REPLACEMENTS:
        match = pattern.match(text)
        if match:
            localized = replacement(match)
            break
    for source, target in _TEXT_REPLACEMENTS:
        localized = localized.replace(source, target)
    return localized


def _label(mapping: dict[str, str], value: str, default: str = "") -> str:
    raw = str(value or "")
    if raw in mapping:
        return mapping[raw]
    return raw or default


def _localize_okx_actions(actions: list[str]) -> str:
    if not actions:
        return "未提供"
    return ", ".join(_OKX_ACTION_LABELS.get(action, action) for action in actions)


def _localize_operator_data(value, key: str = ""):
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        if key == "status":
            return _status_label(value)
        if key in {"topic_delivery_state"}:
            return _status_label(value)
        if key in {"telegram_watch_mode"}:
            return _label(_TELEGRAM_MODE_LABELS, value, "未提供")
        if key in {"okx_execution_path", "manual_signal_default_path", "manual_signal_configured_path"}:
            return _label(_EXECUTION_PATH_LABELS, value, "未提供")
        if key == "topic_target_source":
            return _label(_TOPIC_SOURCE_LABELS, value, "未提供")
        if key == "operator_command_ingress":
            return _label(_OPERATOR_INGRESS_LABELS, value, "未提供")
        if key in {"web_pin", "telegram_bot_token", "okx_demo_credentials"}:
            return _label(_SECRET_SOURCE_LABELS, value, value)
        return _localize_operator_text(value)
    if isinstance(value, list):
        if key in {"configured_okx_supported_actions", "configured_okx_unsupported_actions"}:
            return [_OKX_ACTION_LABELS.get(str(item), str(item)) for item in value]
        return [_localize_operator_data(item) for item in value]
    if isinstance(value, dict):
        return {item_key: _localize_operator_data(item, item_key) for item_key, item in value.items()}
    return value


def _localized_direct_use_text(snapshot: dict) -> str:
    run_paths = snapshot.get("run_paths", {})
    capabilities = snapshot.get("capabilities", {})
    activation = snapshot.get("activation_summary", {})
    remaining_gaps = snapshot.get("remaining_gaps", [])
    next_steps = snapshot.get("next_steps", [])
    readiness_checks = snapshot.get("readiness_checks", [])
    lines = [
        "TG OKX Auto Trade 直接使用摘要",
        f"生成时间: {snapshot.get('generated_at', '')}",
        f"状态: {_verification_status_label(snapshot.get('verification_status', 'unknown'))}",
        "",
        "当前画像",
        f"- 总体: {_status_label(activation.get('overall_profile', {}).get('status', 'unknown'))}",
        f"- 手动演示: {_status_label(activation.get('manual_demo', {}).get('status', 'unknown'))}",
        f"- 已配置 OKX Demo: {_status_label(activation.get('configured_okx_demo', {}).get('status', 'unknown'))}",
        f"- 自动 Telegram: {_status_label(activation.get('automatic_telegram', {}).get('status', 'unknown'))}",
        f"- 话题出站: {_status_label(activation.get('operator_topic_outbound', {}).get('status', 'unknown'))}",
        f"- 话题入站: {_status_label(activation.get('operator_topic_inbound', {}).get('status', 'unknown'))}",
        f"- 仅演示防护: {_status_label(activation.get('demo_only_guard', {}).get('status', 'unknown'))}",
        f"- 画像说明: {_localize_operator_text(capabilities.get('current_operating_profile', {}).get('detail', ''))}",
        f"- 下一步操作: {_localize_operator_text(capabilities.get('current_operating_profile', {}).get('action', ''))}",
        "",
        "路径",
        f"- repo_root: {run_paths.get('repo_root', '')}",
        f"- config_path: {run_paths.get('config_path', '')}",
        f"- local_env_path: {run_paths.get('local_env_path', '')}",
        f"- runtime_state_dir: {run_paths.get('runtime_state_dir', '')}",
        f"- sqlite_path: {run_paths.get('sqlite_path', '')}",
        f"- runtime_direct_use_json: {run_paths.get('runtime_direct_use_json', '')}",
        f"- runtime_direct_use_text: {run_paths.get('runtime_direct_use_text', '')}",
        f"- runtime_public_state_json: {run_paths.get('runtime_public_state_json', '')}",
        f"- web_login: {run_paths.get('web_login', '')}",
        f"- healthz: {run_paths.get('healthz', '')}",
        f"- readyz: {run_paths.get('readyz', '')}",
        f"- topic_target: {run_paths.get('topic_target_link') or run_paths.get('topic_target', '')}",
        f"- topic_delivery: {_status_label(run_paths.get('topic_delivery_state', 'unknown'))}（{_localize_operator_text(run_paths.get('topic_delivery_detail', ''))}）",
        "",
        "直接命令",
        f"- verify: {run_paths.get('verify_command', '')}",
        f"- paths: {run_paths.get('paths_command', '')}",
        f"- serve: {run_paths.get('serve_command', '')}",
        f"- snapshot: {run_paths.get('snapshot_command', '')}",
        f"- inject_demo: {run_paths.get('inject_demo_signal_command', '')}",
        f"- inject_configured_demo: {run_paths.get('inject_configured_demo_signal_command', '')}",
        f"- externalize_secrets: {run_paths.get('externalize_secrets_command', '')}",
        f"- topic_test: {run_paths.get('topic_test_command', '')}",
        f"- operator_status: {run_paths.get('operator_command_command', '')}",
        f"- reset_local_state: {run_paths.get('reset_local_state_command', '')}",
        f"- close_positions: {run_paths.get('close_positions_command', '')}",
        "",
        "关键能力说明",
        f"- 手动演示: {_localize_operator_text(capabilities.get('manual_demo_pipeline', {}).get('detail', ''))}",
        f"- OKX Demo: {_localize_operator_text(capabilities.get('okx_demo_execution', {}).get('detail', ''))}",
        f"- Telegram 采集: {_localize_operator_text(capabilities.get('telegram_ingestion', {}).get('detail', ''))}",
        f"- 操作员话题: {_localize_operator_text(capabilities.get('operator_topic', {}).get('detail', ''))}",
        f"- 话题发送状态: {_status_label(run_paths.get('topic_delivery_state', 'unknown'))}",
        "",
        "就绪告警",
    ]
    warnings = [item for item in readiness_checks if item.get("status") != "pass"]
    if warnings:
        lines.extend(
            f"- {_label(_READINESS_LABELS, item.get('name', 'unknown'))}: {_status_label(item.get('status', 'unknown'))} {_localize_operator_text(item.get('detail', ''))}"
            for item in warnings[:8]
        )
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("剩余缺口")
    if remaining_gaps:
        lines.extend(
            f"- {_label(_GAP_LABELS, item.get('id', 'unknown'))}: {_status_label(item.get('status', 'unknown'))} {_localize_operator_text(item.get('detail', ''))}"
            for item in remaining_gaps[:10]
        )
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("下一步")
    if next_steps:
        lines.extend(f"- {_localize_operator_text(step)}" for step in next_steps[:10])
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("此摘要仅用于 Web 端操作展示，已做脱敏处理，并保持在 demo/模拟范围内。")
    return "\n".join(lines) + "\n"


def _web_display(snapshot: dict) -> dict:
    direct_use_profile = (snapshot.get("capabilities") or {}).get("current_operating_profile", {})
    runtime_detail = (
        f"配置中的 Web host/port 已变更，需重启后才会生效：{snapshot['run_paths']['configured_web_login']}。"
        if snapshot.get("wiring", {}).get("web_restart_required")
        else snapshot.get("operator_state", {}).get("pause_reason") or snapshot.get("health", {}).get("trading_runtime", {}).get("detail", "")
    )
    return {
        "verification_status": _verification_status_label(snapshot.get("verification_status", "unknown")),
        "direct_use_profile": {
            "status": direct_use_profile.get("status", ""),
            "status_label": _status_label(direct_use_profile.get("status", "unknown")),
            "detail": _localize_operator_text(direct_use_profile.get("detail", "")),
            "action": _localize_operator_text(direct_use_profile.get("action", "")),
        },
        "readiness_checks": [
            {
                "name": item.get("name", ""),
                "label": _label(_READINESS_LABELS, item.get("name", "")),
                "status": item.get("status", ""),
                "status_label": _status_label(item.get("status", "")),
                "detail": _localize_operator_text(item.get("detail", "")),
            }
            for item in snapshot.get("readiness_checks", [])
        ],
        "capabilities": [
            {
                "name": name,
                "label": _label(_CAPABILITY_LABELS, name),
                "status": item.get("status", ""),
                "status_label": _status_label(item.get("status", "")),
                "detail": _localize_operator_text(item.get("detail", "")),
                "action": _localize_operator_text(item.get("action", "")),
            }
            for name, item in (snapshot.get("capabilities") or {}).items()
        ],
        "activation_summary": [
            {
                "name": name,
                "label": _label(_ACTIVATION_LABELS, name),
                "status": item.get("status", ""),
                "status_label": _status_label(item.get("status", "")),
                "detail": _localize_operator_text(item.get("detail", "")),
                "action": _localize_operator_text(item.get("action", "")),
            }
            for name, item in (snapshot.get("activation_summary") or {}).items()
        ],
        "remaining_gaps": [
            {
                "id": item.get("id", ""),
                "label": _label(_GAP_LABELS, item.get("id", "")),
                "scope": item.get("scope", ""),
                "scope_label": _label(_SCOPE_LABELS, item.get("scope", "")),
                "status": item.get("status", ""),
                "status_label": _status_label(item.get("status", "")),
                "detail": _localize_operator_text(item.get("detail", "")),
                "action": _localize_operator_text(item.get("action", "")),
            }
            for item in snapshot.get("remaining_gaps", [])
        ],
        "activation_checklist": [_localize_operator_text(item) for item in snapshot.get("run_paths", {}).get("activation_checklist", [])],
        "next_steps": [_localize_operator_text(item) for item in snapshot.get("next_steps", [])],
        "direct_use_text": _localized_direct_use_text(snapshot),
        "health_json": json.dumps(_localize_operator_data(snapshot.get("health", {})), ensure_ascii=False, indent=2),
        "run_paths_json": json.dumps(_localize_operator_data(snapshot.get("run_paths", {})), ensure_ascii=False, indent=2),
        "setup_examples_json": json.dumps(
            _localize_operator_data(snapshot.get("run_paths", {}).get("setup_examples", {})),
            ensure_ascii=False,
            indent=2,
        ),
        "overview": {
            "runtime_status": "已暂停" if snapshot.get("operator_state", {}).get("paused") else _status_label(snapshot.get("health", {}).get("trading_runtime", {}).get("status", "")),
            "last_reconcile_detail": _localize_operator_text(snapshot.get("operator_state", {}).get("last_reconcile", {}).get("detail", "")),
            "okx_execution_path": _label(_EXECUTION_PATH_LABELS, snapshot.get("wiring", {}).get("okx_execution_path", ""), "未提供"),
            "configured_okx_supported_actions": _localize_okx_actions(snapshot.get("wiring", {}).get("configured_okx_supported_actions", [])),
            "manual_signal_paths": (
                f"{_label(_EXECUTION_PATH_LABELS, snapshot.get('wiring', {}).get('manual_signal_default_path', ''), '未提供')} 默认 / "
                f"{_label(_EXECUTION_PATH_LABELS, snapshot.get('wiring', {}).get('manual_signal_configured_path', ''), '未提供')} 已配置"
            ),
            "topic_target_source": _label(_TOPIC_SOURCE_LABELS, snapshot.get("wiring", {}).get("topic_target_source", ""), "未提供"),
            "topic_delivery_state": _status_label(snapshot.get("wiring", {}).get("topic_delivery_state", "")),
            "topic_delivery_detail": _localize_operator_text(snapshot.get("wiring", {}).get("topic_delivery_detail", "")),
            "operator_command_ingress": _label(_OPERATOR_INGRESS_LABELS, snapshot.get("wiring", {}).get("operator_command_ingress", ""), "未提供"),
            "telegram_watch_mode": _label(_TELEGRAM_MODE_LABELS, snapshot.get("wiring", {}).get("telegram_watch_mode", ""), "未提供"),
            "runtime_detail": _localize_operator_text(runtime_detail),
        },
    }


def _escape_login_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_login_html(error: str = "", pin: str = "") -> str:
    error_html = ""
    if error:
        error_html = f'<div class="error" role="alert">{_escape_login_html(error)}</div>'
    pin_value = _escape_login_html(pin)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录</title><style>body{{font-family:Georgia,serif;background:#f6f2ea;display:grid;place-items:center;height:100vh;margin:0}}form{{background:#fffdf8;border:1px solid #e7d8c6;border-radius:18px;padding:28px;display:grid;gap:12px;min-width:280px}}input,button{{font:inherit;padding:12px;border-radius:10px;border:1px solid #d8c7b1}}button{{background:#d45500;color:#fff;border:none}}.error{{background:#fff1e8;border:1px solid #f0b38a;color:#8a3b00;padding:10px 12px;border-radius:10px}}</style></head>
<body><form method="post" action="/login"><h2>6 位 PIN</h2>{error_html}<input name="pin" pattern="[0-9]{{6}}" maxlength="6" minlength="6" inputmode="numeric" autofocus value="{pin_value}"><button>登录</button></form></body></html>"""


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
        return HTTPStatus.METHOD_NOT_ALLOWED, {}, {"error": "不支持该请求方法"}

    def _route_get(self, path: str, headers: dict[str, str]) -> tuple[int, dict[str, str], str | dict]:
        parsed = urlsplit(path)
        clean_path = parsed.path or "/"
        query = parse_qs(parsed.query)
        if clean_path == "/login":
            if self._require_auth(headers):
                return HTTPStatus.SEE_OTHER, {"Location": "/"}, ""
            return HTTPStatus.OK, {"Content-Type": "text/html; charset=utf-8"}, _render_login_html()
        if clean_path == "/healthz":
            snapshot = self.runtime.health_snapshot()
            overall = "ok" if all(item["status"] not in {"error", "fail"} for item in snapshot.values()) else "error"
            return HTTPStatus.OK, {}, {"status": overall, "health": snapshot}
        if clean_path == "/readyz":
            report = self.runtime.public_verification_report()
            return HTTPStatus.OK, {}, {"status": report["status"], "checks": report["checks"]}
        if clean_path == "/" or clean_path == "/index.html":
            if not self._require_auth(headers):
                return HTTPStatus.SEE_OTHER, {"Location": "/login"}, ""
            initial_view = _normalize_web_view(query.get("view", ["overview"])[0])
            return HTTPStatus.OK, {"Content-Type": "text/html; charset=utf-8"}, _render_app_html(initial_view)
        if clean_path == "/api/state":
            if not self._require_auth(headers):
                return HTTPStatus.UNAUTHORIZED, {}, {"error": "未授权"}
            snapshot = self.runtime.public_snapshot()
            snapshot["run_paths"] = self.runtime.usage_paths()
            snapshot["direct_use_text"] = self.runtime.direct_use_text(snapshot=snapshot, usage_paths=snapshot["run_paths"])
            snapshot["web_display"] = _web_display(snapshot)
            return HTTPStatus.OK, {}, snapshot
        return HTTPStatus.NOT_FOUND, {}, {"error": "未找到"}

    def _route_post(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
        client_ip: str,
    ) -> tuple[int, dict[str, str], str | dict]:
        if path == "/login":
            if self._is_rate_limited(client_ip):
                return (
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"Content-Type": "text/html; charset=utf-8"},
                    _render_login_html("登录尝试过多，请稍后再试。"),
                )
            form = parse_qs(body.decode("utf-8"))
            pin = form.get("pin", [""])[0]
            try:
                session_id = self.runtime.authenticate(pin)
            except ValueError as exc:
                return (
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"Content-Type": "text/html; charset=utf-8"},
                    _render_login_html(str(exc), pin),
                )
            if not session_id:
                self._record_failed_attempt(client_ip)
                return (
                    HTTPStatus.UNAUTHORIZED,
                    {"Content-Type": "text/html; charset=utf-8"},
                    _render_login_html("PIN 无效", pin),
                )
            cookie = http.cookies.SimpleCookie()
            cookie["session"] = session_id
            cookie["session"]["path"] = "/"
            cookie["session"]["httponly"] = True
            cookie["session"]["samesite"] = "Lax"
            return HTTPStatus.SEE_OTHER, {"Set-Cookie": cookie.output(header="").strip(), "Location": "/"}, ""

        if not self._require_auth(headers):
            return HTTPStatus.UNAUTHORIZED, {}, {"error": "未授权"}

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
            self.runtime.pause_trading(str(payload.get("reason", "Web UI 手动暂停")))
            return HTTPStatus.OK, {}, self.runtime.public_snapshot()["operator_state"]
        if path == "/api/actions/resume":
            try:
                payload = _decode_json_body(body)
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {}, {"error": str(exc)}
            self.runtime.resume_trading(str(payload.get("reason", "Web UI 手动恢复")))
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
        return HTTPStatus.NOT_FOUND, {}, {"error": "未找到"}

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
