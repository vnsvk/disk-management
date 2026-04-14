import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = 8765
SUBSCRIPTION_CACHE_TTL_SECONDS = 300
PAYLOAD_CACHE_TTL_SECONDS = 60
REGION_SUPPORT_CACHE_TTL_SECONDS = 3600

_cache_lock = threading.Lock()
_cache = {
    "subscriptions": None,
    "payloads": {},
    "region_support": {},
}


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Azure Disk Dashboard</title>
  <style>
    :root {
      --bg: #f3f9fd;
      --paper: #ffffff;
      --ink: #1b1a19;
      --muted: #605e5c;
      --line: #d2d0ce;
      --accent: #0078d4;
      --accent-2: #106ebe;
      --warn: #8a6d1f;
      --danger: #a4262c;
      --shadow: rgba(0, 120, 212, 0.10);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(0,120,212,0.10), transparent 28%),
        radial-gradient(circle at top right, rgba(16,110,190,0.08), transparent 34%),
        linear-gradient(180deg, #f8fbfd 0%, var(--bg) 100%);
    }
    .shell {
      max-width: 1520px;
      margin: 0 auto;
      padding: 16px;
    }
    .hero {
      background: linear-gradient(135deg, rgba(0,120,212,0.96), rgba(16,110,190,0.96));
      color: white;
      border-radius: 24px;
      padding: 20px 22px;
      box-shadow: 0 18px 40px var(--shadow);
    }
    .hero h1 {
      margin: 0 0 8px;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 2.2rem;
      font-weight: 700;
    }
    .hero p {
      margin: 0;
      max-width: 840px;
      line-height: 1.5;
      color: rgba(255,255,255,0.9);
    }
    .layout {
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      gap: 14px;
      align-items: start;
      margin-top: 14px;
    }
    .panel {
      margin-top: 14px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 20px;
      box-shadow: 0 10px 24px var(--shadow);
    }
    .layout .panel {
      margin-top: 0;
    }
    .sidebar {
      position: sticky;
      top: 16px;
      background: linear-gradient(180deg, #f8fbfd, #eef6fc);
    }
    .sidebar h2 {
      margin: 0 0 6px;
      font-size: 1.15rem;
    }
    .sidebar p {
      margin: 0 0 10px;
      color: var(--muted);
      line-height: 1.5;
      font-size: 0.92rem;
    }
    .nav {
      display: grid;
      gap: 8px;
    }
    .nav button {
      text-align: left;
      padding: 10px 12px;
      border-radius: 16px;
      background: white;
      border: 1px solid var(--line);
      color: var(--ink);
      box-shadow: none;
    }
    .nav button.active {
      background: linear-gradient(135deg, rgba(0,120,212,0.96), rgba(16,110,190,0.96));
      border-color: transparent;
      color: white;
    }
    .nav button small {
      display: block;
      margin-top: 4px;
      opacity: 0.8;
      font-size: 0.82rem;
      font-weight: 500;
    }
    .workspace {
      min-width: 0;
    }
    .controls {
      display: grid;
      gap: 10px;
    }
    .control-fields {
      display: grid;
      grid-template-columns: minmax(320px, 1.8fr) minmax(240px, 1.2fr);
      gap: 10px;
      align-items: end;
    }
    .control-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      max-width: 1080px;
    }
    .action-group {
      display: contents;
    }
    .action-group-title {
      display: none;
    }
    .field label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 700;
    }
    select, input, button {
      width: 100%;
      min-height: 42px;
      border-radius: 12px;
      border: 1px solid var(--line);
      padding: 9px 12px;
      font: inherit;
      background: white;
      color: var(--ink);
    }
    select, input {
      font-size: 0.95rem;
    }
    button {
      cursor: pointer;
      font-weight: 700;
      transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
      width: auto;
      min-width: 118px;
      white-space: nowrap;
    }
    button:hover {
      transform: translateY(-1px);
      box-shadow: 0 10px 18px var(--shadow);
    }
    .primary { background: var(--accent); color: white; border-color: var(--accent); }
    .secondary { background: #fff; }
    .export { background: #eff6fc; border-color: #9fd3ff; }
    .checkbox-card {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 42px;
      padding: 0 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: white;
      font-weight: 700;
      white-space: nowrap;
    }
    .checkbox-card input {
      width: 18px;
      height: 18px;
      min-height: 18px;
      margin: 0;
      padding: 0;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }
    .card {
      background: white;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }
    .card .label {
      color: var(--muted);
      font-size: 0.9rem;
      margin-bottom: 8px;
    }
    .card .value {
      font-size: 1.9rem;
      font-weight: 800;
    }
    .card.accent .value { color: var(--accent); }
    .card.warn .value { color: var(--warn); }
    .card.danger .value { color: var(--danger); }
    .meta {
      margin-top: 14px;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .view-stack {
      display: grid;
      gap: 20px;
      margin-top: 20px;
    }
    .view {
      display: none;
    }
    .view.active {
      display: grid;
      gap: 20px;
    }
    .footer-note {
      margin-top: 18px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.72);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
    }
    .footer-copy {
      line-height: 1.45;
    }
    .footer-note strong {
      color: var(--ink);
    }
    .linkedin-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-width: 130px;
      min-height: 40px;
      padding: 8px 14px;
      border-radius: 999px;
      background: #0078d4;
      color: white;
      text-decoration: none;
      font-weight: 700;
    }
    h2 {
      margin: 0 0 12px;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 1.35rem;
    }
    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: white;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 920px;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid #edebe9;
      text-align: left;
      vertical-align: top;
      font-size: 0.95rem;
    }
    th {
      position: sticky;
      top: 0;
      background: #f3f2f1;
      z-index: 1;
    }
    .pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 700;
      white-space: nowrap;
    }
    .pill.v1 { background: #fff4ce; color: #8a6d1f; }
    .pill.v2 { background: #dff6dd; color: #0b6a0b; }
    .pill.other { background: #edebe9; color: #605e5c; }
    .pill.ok { background: #dff6dd; color: #0b6a0b; }
    .pill.no { background: #fde7e9; color: #a4262c; }
    .notice {
      margin-top: 16px;
      padding: 14px 16px;
      border-radius: 14px;
      background: #eff6fc;
      border: 1px solid #9fd3ff;
      color: #004578;
      display: none;
    }
    .error {
      background: #fde7e9;
      border-color: #f1b7bb;
      color: var(--danger);
    }
    @media (max-width: 1100px) {
      .layout { grid-template-columns: 1fr; }
      .sidebar { position: static; }
      .control-fields { grid-template-columns: 1fr; }
      .control-actions { flex-wrap: wrap; }
      .summary { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 700px) {
      .shell { padding: 16px; }
      .summary { grid-template-columns: 1fr; }
      .hero h1 { font-size: 1.8rem; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>Azure Disk Dashboard</h1>
      <p>Review Azure managed disks by subscription, identify Premium SSD v1 disks for Premium SSD v2 migration, back up selected disks with snapshots, and clean up unattached disks from one dashboard.</p>
    </section>

    <div class="layout">
      <aside class="panel sidebar">
        <h2>Disk Views</h2>
        <p>Move between full inventory, migration-ready disks, and unattached cleanup. Use the action bar to export CSV, create backups, migrate selected disks, or delete selected unattached disks.</p>
        <div class="nav">
          <button id="navAll" class="active">1. All Disks<small>Full managed disk inventory</small></button>
          <button id="navEligible">2. Eligible Disks<small>V1 disks ready for migration</small></button>
          <button id="navUnattached">3. Unattached Disks<small>Selection and cleanup workflow</small></button>
        </div>
      </aside>

      <section class="workspace">
        <section class="panel">
          <div class="controls">
            <div class="control-fields">
              <div class="field">
                <label for="subscription">Subscription</label>
                <select id="subscription"></select>
              </div>
              <div class="field">
                <label for="resourceGroup">Resource Group</label>
                <input id="resourceGroup" type="text" placeholder="Optional filter">
              </div>
            </div>
            <div class="control-actions">
              <div class="action-group">
                <button id="loadBtn" class="primary">Load Disks</button>
                <button id="csvBtn" class="export">Export CSV</button>
              </div>
              <div class="action-group">
                <button id="selectAllMigrationBtn" class="secondary">Select All V1</button>
                <label class="checkbox-card"><input id="backupBeforeMigration" type="checkbox"> Backup Before Migration</label>
                <button id="backupSelectedBtn" class="secondary">Backup Selected</button>
                <button id="migrateSelectedBtn" class="secondary">Migrate Selected</button>
              </div>
              <div class="action-group">
                <button id="selectAllUnattachedBtn" class="secondary">Select All</button>
                <button id="clearUnattachedBtn" class="secondary">Clear</button>
                <button id="openSelectedBtn" class="secondary">Open Selected</button>
                <button id="deleteSelectedBtn" class="secondary">Delete Selected</button>
              </div>
            </div>
          </div>
          <div id="notice" class="notice"></div>
          <div class="summary" id="summary" hidden>
            <div class="card"><div class="label">Total Disks</div><div class="value" id="totalDisks">0</div></div>
            <div class="card warn"><div class="label">V1 Disks</div><div class="value" id="v1Disks">0</div></div>
            <div class="card accent"><div class="label">V2 Disks</div><div class="value" id="v2Disks">0</div></div>
            <div class="card"><div class="label">Eligible To Migrate</div><div class="value" id="eligibleDisks">0</div></div>
            <div class="card danger"><div class="label">Unattached Disks</div><div class="value" id="unattachedDisks">0</div></div>
          </div>
          <div id="meta" class="meta"></div>
        </section>

        <section class="view-stack" id="content" hidden>
          <div id="viewAll" class="view active">
            <div class="panel">
              <h2>All Disks</h2>
              <div class="table-wrap"><table id="inventoryTable"></table></div>
            </div>
          </div>

          <div id="viewEligible" class="view">
            <div class="panel">
              <h2>Eligible For Migration</h2>
              <div id="migrationMeta" class="meta"></div>
              <div class="table-wrap"><table id="eligibleTable"></table></div>
            </div>
            <div class="panel">
              <h2>Skipped For Migration</h2>
              <div class="table-wrap"><table id="skippedTable"></table></div>
            </div>
          </div>

          <div id="viewUnattached" class="view">
            <div class="panel">
              <h2>Unattached Disks</h2>
              <div id="unattachedMeta" class="meta"></div>
              <div class="table-wrap"><table id="unattachedTable"></table></div>
            </div>
          </div>
        </section>

        <div class="footer-note">
          <div class="footer-copy">
            Author: <strong>Vikram Vunduru</strong><br>
            For support, reach out on LinkedIn.
          </div>
          <a class="linkedin-btn" href="https://www.linkedin.com/in/vikram-vunduru/" target="_blank" rel="noopener noreferrer">
            <span aria-hidden="true">in</span>
            <span>LinkedIn</span>
          </a>
        </div>
      </section>
    </div>
  </div>

  <script>
    const state = {
      data: null,
      subscriptions: [],
      selectedUnattachedDiskIds: new Set(),
      selectedMigrationDiskIds: new Set()
    };

    const noticeEl = document.getElementById("notice");
    const summaryEl = document.getElementById("summary");
    const contentEl = document.getElementById("content");
    const metaEl = document.getElementById("meta");
    const unattachedMetaEl = document.getElementById("unattachedMeta");
    const migrationMetaEl = document.getElementById("migrationMeta");
    const navButtons = {
      all: document.getElementById("navAll"),
      eligible: document.getElementById("navEligible"),
      unattached: document.getElementById("navUnattached")
    };
    const viewPanels = {
      all: document.getElementById("viewAll"),
      eligible: document.getElementById("viewEligible"),
      unattached: document.getElementById("viewUnattached")
    };

    function showNotice(message, isError = false) {
      noticeEl.textContent = message;
      noticeEl.className = isError ? "notice error" : "notice";
      noticeEl.style.display = "block";
    }

    function clearNotice() {
      noticeEl.style.display = "none";
      noticeEl.textContent = "";
      noticeEl.className = "notice";
    }

    async function fetchJson(url) {
      const response = await fetch(url);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Request failed");
      }
      return payload;
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Request failed");
      }
      return data;
    }

    function subscriptionLabel(subscription) {
      return `${subscription.name} (${subscription.id})`;
    }

    async function loadSubscriptions() {
      try {
        const payload = await fetchJson("/api/subscriptions");
        state.subscriptions = payload.subscriptions || [];
        const select = document.getElementById("subscription");
        select.innerHTML = "";
        for (const subscription of state.subscriptions) {
          const option = document.createElement("option");
          option.value = subscription.id;
          option.textContent = subscriptionLabel(subscription);
          select.appendChild(option);
        }
        clearNotice();
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    function pill(value, kind) {
      return `<span class="pill ${kind}">${value}</span>`;
    }

    function portalLink(url, label = "Open") {
      if (!url) {
        return "";
      }
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }

    function setActiveView(viewName) {
      for (const [key, button] of Object.entries(navButtons)) {
        button.classList.toggle("active", key === viewName);
      }
      for (const [key, panel] of Object.entries(viewPanels)) {
        panel.classList.toggle("active", key === viewName);
      }
    }

    function renderTable(elementId, columns, rows, formatter = null) {
      const table = document.getElementById(elementId);
      const head = `<thead><tr>${columns.map(column => `<th>${column.label}</th>`).join("")}</tr></thead>`;
      const bodyRows = rows.length === 0
        ? `<tr><td colspan="${columns.length}">No rows</td></tr>`
        : rows.map(row => {
            const cells = columns.map(column => {
              const value = formatter ? formatter(column.key, row[column.key], row) : row[column.key];
              return `<td>${value ?? ""}</td>`;
            }).join("");
            return `<tr>${cells}</tr>`;
          }).join("");
      table.innerHTML = `${head}<tbody>${bodyRows}</tbody>`;
    }

    function renderData(data) {
      state.data = data;
      const availableUnattachedIds = new Set((data.unattached || []).map(item => item.id));
      const availableMigrationIds = new Set((data.migrationPlan || []).filter(item => item.eligible).map(item => item.id));
      state.selectedUnattachedDiskIds = new Set(
        [...state.selectedUnattachedDiskIds].filter(id => availableUnattachedIds.has(id))
      );
      state.selectedMigrationDiskIds = new Set(
        [...state.selectedMigrationDiskIds].filter(id => availableMigrationIds.has(id))
      );
      summaryEl.hidden = false;
      contentEl.hidden = false;

      document.getElementById("totalDisks").textContent = data.summary.totalDisks;
      document.getElementById("v1Disks").textContent = data.summary.v1Disks;
      document.getElementById("v2Disks").textContent = data.summary.v2Disks;
      document.getElementById("eligibleDisks").textContent = data.summary.eligibleDisks;
      document.getElementById("unattachedDisks").textContent = data.summary.unattachedDisks;

      const selectedSubscription = state.subscriptions.find(item => item.id === data.subscriptionId);
      const scope = data.resourceGroupName ? `Resource Group: ${data.resourceGroupName}` : "All resource groups";
      const cacheState = data.cacheHit ? "cache" : "live";
      const regionCheck = data.regionSupportChecked ? "region-check on" : "region-check skipped";
      metaEl.textContent = `Loaded ${selectedSubscription ? selectedSubscription.name : data.subscriptionId} | ${scope} | Generated ${data.generatedAt} | ${cacheState} | ${regionCheck} | ${data.durationMs} ms`;

      renderTable(
        "inventoryTable",
        [
          { key: "resourceGroup", label: "Resource Group" },
          { key: "diskName", label: "Disk" },
          { key: "diskVersion", label: "Version" },
          { key: "sku", label: "SKU" },
          { key: "attached", label: "Attached" },
          { key: "vmName", label: "VM" },
          { key: "isOsDisk", label: "OS Disk" },
          { key: "caching", label: "Caching" },
          { key: "location", label: "Region" }
        ],
        data.inventory,
        (key, value) => {
          if (key === "diskVersion") {
            const kind = value === "V1" ? "v1" : value === "V2" ? "v2" : "other";
            return pill(value, kind);
          }
          if (key === "attached" || key === "isOsDisk") {
            return pill(value ? "Yes" : "No", value ? "ok" : "no");
          }
          return value ?? "";
        }
      );

      renderTable(
        "eligibleTable",
        [
          { key: "select", label: "Select" },
          { key: "resourceGroup", label: "Resource Group" },
          { key: "vmName", label: "VM" },
          { key: "diskName", label: "Disk" },
          { key: "plannedSku", label: "Planned SKU" },
          { key: "reasons", label: "Notes" },
          { key: "portalUrl", label: "Portal" }
        ],
        data.migrationPlan.filter(item => item.eligible),
        (key, value, row) => {
          if (key === "select") {
            const checked = state.selectedMigrationDiskIds.has(row.id) ? "checked" : "";
            return `<input type="checkbox" class="migration-select" data-disk-id="${row.id}" ${checked}>`;
          }
          if (key === "portalUrl") {
            return portalLink(value);
          }
          return value ?? "";
        }
      );
      migrationMetaEl.textContent = `${state.selectedMigrationDiskIds.size} of ${data.migrationPlan.filter(item => item.eligible).length} migration disks selected`;
      bindMigrationSelectionHandlers();

      renderTable(
        "skippedTable",
        [
          { key: "resourceGroup", label: "Resource Group" },
          { key: "vmName", label: "VM" },
          { key: "diskName", label: "Disk" },
          { key: "reasons", label: "Reason" },
          { key: "portalUrl", label: "Portal" }
        ],
        data.migrationPlan.filter(item => !item.eligible),
        (key, value) => key === "portalUrl" ? portalLink(value) : (value ?? "")
      );

      renderTable(
        "unattachedTable",
        [
          { key: "select", label: "Select" },
          { key: "resourceGroup", label: "Resource Group" },
          { key: "diskName", label: "Disk" },
          { key: "sku", label: "SKU" },
          { key: "diskState", label: "Disk State" },
          { key: "location", label: "Region" },
          { key: "portalUrl", label: "Portal" }
        ],
        data.unattached,
        (key, value, row) => {
          if (key === "select") {
            const checked = state.selectedUnattachedDiskIds.has(row.id) ? "checked" : "";
            return `<input type="checkbox" class="unattached-select" data-disk-id="${row.id}" ${checked}>`;
          }
          if (key === "portalUrl") {
            return portalLink(value);
          }
          return value ?? "";
        }
      );
      unattachedMetaEl.textContent = `${state.selectedUnattachedDiskIds.size} of ${data.unattached.length} unattached disks selected`;
      bindUnattachedSelectionHandlers();
    }

    function downloadBlob(content, filename, mimeType) {
      const blob = new Blob([content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    }

    function openPortalUrls(rows) {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }
      const urls = [...new Set(rows.map(row => row.portalUrl).filter(Boolean))];
      if (urls.length === 0) {
        showNotice("No portal links available for this selection.", true);
        return;
      }
      for (const url of urls) {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    }

    function escapeCsv(value) {
      const text = value == null ? "" : String(value);
      if (/[",\\n]/.test(text)) {
        return `"${text.replace(/"/g, '""')}"`;
      }
      return text;
    }

    function getSelectedUnattachedRows() {
      if (!state.data) {
        return [];
      }
      return state.data.unattached.filter(row => state.selectedUnattachedDiskIds.has(row.id));
    }

    function getSelectedMigrationRows() {
      if (!state.data) {
        return [];
      }
      return state.data.migrationPlan.filter(row => row.eligible && state.selectedMigrationDiskIds.has(row.id));
    }

    function exportInventoryCsv() {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }
      const selectedRows = getSelectedUnattachedRows();
      const rows = selectedRows.length > 0 ? selectedRows : state.data.inventory;
      const headers = ["resourceGroup","diskName","location","sku","diskVersion","diskState","attached","vmName","isOsDisk","lun","caching","logicalSectorSize","burstingEnabled","unattached","id","portalUrl"];
      const lines = [headers.join(",")];
      for (const row of rows) {
        lines.push(headers.map(header => escapeCsv(row[header])).join(","));
      }
      const fileName = selectedRows.length > 0 ? "selected-unattached-disks.csv" : "disk-inventory.csv";
      downloadBlob(lines.join("\\n"), fileName, "text/csv;charset=utf-8");
    }

    function bindUnattachedSelectionHandlers() {
      for (const checkbox of document.querySelectorAll(".unattached-select")) {
        checkbox.addEventListener("change", (event) => {
          const diskId = event.target.dataset.diskId;
          if (event.target.checked) {
            state.selectedUnattachedDiskIds.add(diskId);
          } else {
            state.selectedUnattachedDiskIds.delete(diskId);
          }
          unattachedMetaEl.textContent = `${state.selectedUnattachedDiskIds.size} of ${state.data.unattached.length} unattached disks selected`;
        });
      }
    }

    function bindMigrationSelectionHandlers() {
      for (const checkbox of document.querySelectorAll(".migration-select")) {
        checkbox.addEventListener("change", (event) => {
          const diskId = event.target.dataset.diskId;
          if (event.target.checked) {
            state.selectedMigrationDiskIds.add(diskId);
          } else {
            state.selectedMigrationDiskIds.delete(diskId);
          }
          migrationMetaEl.textContent = `${state.selectedMigrationDiskIds.size} of ${state.data.migrationPlan.filter(item => item.eligible).length} migration disks selected`;
        });
      }
    }

    function selectAllMigration() {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }
      state.selectedMigrationDiskIds = new Set(state.data.migrationPlan.filter(row => row.eligible).map(row => row.id));
      renderData(state.data);
    }

    function selectAllUnattached() {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }
      state.selectedUnattachedDiskIds = new Set(state.data.unattached.map(row => row.id));
      renderData(state.data);
    }

    function clearUnattachedSelection() {
      state.selectedUnattachedDiskIds = new Set();
      if (state.data) {
        renderData(state.data);
      }
    }

    function openSelectedUnattached() {
      openPortalUrls(getSelectedUnattachedRows());
    }

    async function deleteSelectedUnattached() {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }

      const rows = getSelectedUnattachedRows();
      if (rows.length === 0) {
        showNotice("Select at least one unattached disk first.", true);
        return;
      }

      const confirmed = window.confirm(`Delete ${rows.length} selected unattached disk(s)?`);
      if (!confirmed) {
        return;
      }

      try {
        showNotice("Deleting selected unattached disks...");
        const result = await postJson("/api/delete-unattached", {
          subscriptionId: state.data.subscriptionId,
          disks: rows.map(row => ({
            resourceGroup: row.resourceGroup,
            diskName: row.diskName,
            id: row.id
          }))
        });
        state.selectedUnattachedDiskIds = new Set();
        await loadInventory();
        showNotice(`Deleted ${result.deletedCount} unattached disk(s).`);
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function backupSelectedMigration() {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }
      const rows = getSelectedMigrationRows();
      if (rows.length === 0) {
        showNotice("Select at least one migration disk first.", true);
        return;
      }
      try {
        showNotice("Creating snapshots for selected disks...");
        const result = await postJson("/api/backup-disks", {
          subscriptionId: state.data.subscriptionId,
          disks: rows.map(row => ({ id: row.id, resourceGroup: row.resourceGroup, diskName: row.diskName }))
        });
        showNotice(`Created ${result.snapshotCount} snapshot(s).`);
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function migrateSelectedDisks() {
      if (!state.data) {
        showNotice("Load a subscription first.", true);
        return;
      }
      const rows = getSelectedMigrationRows();
      if (rows.length === 0) {
        showNotice("Select at least one migration disk first.", true);
        return;
      }
      const createBackupBefore = document.getElementById("backupBeforeMigration").checked;
      const confirmed = window.confirm(`Migrate ${rows.length} selected disk(s) to PremiumV2_LRS?${createBackupBefore ? "\\n\\nSnapshots will be created first." : ""}`);
      if (!confirmed) {
        return;
      }
      try {
        showNotice("Migrating selected disks...");
        const result = await postJson("/api/migrate-disks", {
          subscriptionId: state.data.subscriptionId,
          createBackupBefore,
          disks: rows.map(row => ({ id: row.id, resourceGroup: row.resourceGroup, diskName: row.diskName }))
        });
        state.selectedMigrationDiskIds = new Set();
        await loadInventory();
        showNotice(`Migrated ${result.migratedCount} disk(s) to PremiumV2_LRS.${result.snapshotCount ? ` Created ${result.snapshotCount} snapshot(s).` : ""}`);
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function loadInventory() {
      const subscriptionId = document.getElementById("subscription").value;
      const resourceGroupName = document.getElementById("resourceGroup").value.trim();

      if (!subscriptionId) {
        showNotice("Select a subscription first.", true);
        return;
      }

      const params = new URLSearchParams({ subscriptionId });
      if (resourceGroupName) {
        params.set("resourceGroupName", resourceGroupName);
      }

      try {
        clearNotice();
        showNotice("Loading Azure disks...");
        const data = await fetchJson(`/api/inventory?${params.toString()}`);
        renderData(data);
        clearNotice();
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    document.getElementById("loadBtn").addEventListener("click", loadInventory);
    document.getElementById("csvBtn").addEventListener("click", exportInventoryCsv);
    document.getElementById("selectAllMigrationBtn").addEventListener("click", selectAllMigration);
    document.getElementById("backupSelectedBtn").addEventListener("click", backupSelectedMigration);
    document.getElementById("migrateSelectedBtn").addEventListener("click", migrateSelectedDisks);
    document.getElementById("selectAllUnattachedBtn").addEventListener("click", selectAllUnattached);
    document.getElementById("clearUnattachedBtn").addEventListener("click", clearUnattachedSelection);
    document.getElementById("openSelectedBtn").addEventListener("click", openSelectedUnattached);
    document.getElementById("deleteSelectedBtn").addEventListener("click", deleteSelectedUnattached);
    navButtons.all.addEventListener("click", () => setActiveView("all"));
    navButtons.eligible.addEventListener("click", () => setActiveView("eligible"));
    navButtons.unattached.addEventListener("click", () => setActiveView("unattached"));

    loadSubscriptions();
  </script>
</body>
</html>
"""


def run_az_json(arguments, subscription_id=None, timeout_seconds=90):
    az_executable = shutil.which("az") or shutil.which("az.cmd")
    if not az_executable:
        raise RuntimeError("Azure CLI executable was not found. Install Azure CLI or restart the terminal after installation.")

    env = os.environ.copy()

    command = [az_executable, *arguments, "--only-show-errors", "-o", "json"]
    if subscription_id:
        command.extend(["--subscription", subscription_id])

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
        env=env,
        timeout=timeout_seconds,
    )

    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "Azure CLI command failed"
        raise RuntimeError(error)

    raw_output = completed.stdout.strip()
    if not raw_output:
        return None
    return json.loads(raw_output)


def get_cached_entry(bucket, key):
    with _cache_lock:
        entry = _cache[bucket].get(key)
        if not entry:
            return None
        if entry["expires_at"] < time.monotonic():
            del _cache[bucket][key]
            return None
        return entry["value"]


def set_cached_entry(bucket, key, value, ttl_seconds):
    with _cache_lock:
        _cache[bucket][key] = {
            "value": value,
            "expires_at": time.monotonic() + ttl_seconds,
        }


def get_subscriptions():
    with _cache_lock:
        entry = _cache["subscriptions"]
        if entry and entry["expires_at"] >= time.monotonic():
            return entry["value"]

    accounts = run_az_json(["account", "list"])
    subscriptions = []
    for account in accounts or []:
        subscriptions.append(
            {
                "id": account.get("id"),
                "name": account.get("name"),
                "isDefault": account.get("isDefault", False),
                "tenantId": account.get("tenantId"),
                "state": account.get("state"),
            }
        )

    subscriptions.sort(key=lambda item: (not item["isDefault"], item["name"] or ""))
    with _cache_lock:
        _cache["subscriptions"] = {
            "value": subscriptions,
            "expires_at": time.monotonic() + SUBSCRIPTION_CACHE_TTL_SECONDS,
        }
    return subscriptions


def get_disk_version(sku_name):
    if sku_name == "Premium_LRS":
        return "V1"
    if sku_name == "PremiumV2_LRS":
        return "V2"
    return "Other"


def build_portal_disk_url(disk_id):
    if not disk_id:
        return None
    return f"https://portal.azure.com/#@/resource{disk_id}/overview"


def get_resource_name_from_id(resource_id):
    if not resource_id:
        return None
    parts = [part for part in str(resource_id).split("/") if part]
    if not parts:
        return None
    return parts[-1]


def invalidate_payload_cache(subscription_id):
    with _cache_lock:
        keys_to_delete = [key for key in _cache["payloads"] if key[0] == subscription_id]
        for key in keys_to_delete:
            del _cache["payloads"][key]


def get_region_support(subscription_id):
    cached = get_cached_entry("region_support", subscription_id)
    if cached is not None:
        return cached

    try:
        skus = run_az_json(["vm", "list-skus", "--resource-type", "disks"], subscription_id)
    except RuntimeError:
        return None

    regions = set()
    for sku in skus or []:
        if sku.get("name") != "PremiumV2_LRS":
            continue
        for location_info in sku.get("locationInfo") or []:
            location = location_info.get("location")
            if location:
                regions.add(location)
    set_cached_entry("region_support", subscription_id, regions, REGION_SUPPORT_CACHE_TTL_SECONDS)
    return regions


def build_vm_disk_map(virtual_machines):
    mapping = {}
    for vm in virtual_machines or []:
        storage_profile = vm.get("storageProfile") or {}
        os_disk = storage_profile.get("osDisk") or {}
        os_managed_disk = os_disk.get("managedDisk") or {}
        os_disk_id = os_managed_disk.get("id")
        if os_disk_id:
            mapping[os_disk_id.lower()] = {
                "vmName": vm.get("name"),
                "resourceGroup": vm.get("resourceGroup"),
                "isOsDisk": True,
                "lun": None,
                "caching": os_disk.get("caching"),
            }

        for data_disk in storage_profile.get("dataDisks") or []:
            managed_disk = data_disk.get("managedDisk") or {}
            disk_id = managed_disk.get("id")
            if not disk_id:
                continue
            mapping[disk_id.lower()] = {
                "vmName": vm.get("name"),
                "resourceGroup": vm.get("resourceGroup"),
                "isOsDisk": False,
                "lun": data_disk.get("lun"),
                "caching": data_disk.get("caching"),
            }
    return mapping


def get_inventory(subscription_id, resource_group_name=None, include_region_support=False):
    vm_args = ["vm", "list"]
    if resource_group_name:
        vm_args.extend(["--resource-group", resource_group_name])

    virtual_machines = run_az_json(vm_args, subscription_id) or []
    vm_disk_map = build_vm_disk_map(virtual_machines)

    if resource_group_name:
        resource_groups = [resource_group_name]
    else:
        groups = run_az_json(["group", "list"], subscription_id) or []
        resource_groups = [group.get("name") for group in groups if group.get("name")]

    disks = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(
                run_az_json,
                ["disk", "list", "--resource-group", group_name],
                subscription_id,
            )
            for group_name in resource_groups
        ]
        for future in futures:
            result = future.result() or []
            disks.extend(result)

    requires_region_check = include_region_support and any(((disk.get("sku") or {}).get("name")) == "Premium_LRS" for disk in disks)
    supported_regions = get_region_support(subscription_id) if requires_region_check else None

    inventory = []
    for disk in disks:
        disk_id = (disk.get("id") or "").lower()
        attachment = vm_disk_map.get(disk_id)
        managed_by = disk.get("managedBy")
        disk_state = disk.get("diskState")
        attached = bool(managed_by) or attachment is not None or disk_state == "Attached"
        sku_name = ((disk.get("sku") or {}).get("name")) or ""
        location = disk.get("location")
        derived_vm_name = attachment.get("vmName") if attachment else get_resource_name_from_id(managed_by)

        inventory.append(
            {
                "subscriptionId": subscription_id,
                "resourceGroup": disk.get("resourceGroup"),
                "diskName": disk.get("name"),
                "location": location,
                "sku": sku_name,
                "diskVersion": get_disk_version(sku_name),
                "diskState": disk_state,
                "managedBy": managed_by,
                "attached": attached,
                "vmName": derived_vm_name,
                "isOsDisk": attachment.get("isOsDisk") if attachment else False,
                "lun": attachment.get("lun") if attachment else None,
                "caching": attachment.get("caching") if attachment else None,
                "logicalSectorSize": disk.get("logicalSectorSize"),
                "burstingEnabled": disk.get("burstingEnabled"),
                "unattached": not attached,
                "premiumV2RegionSupported": None if supported_regions is None else location in supported_regions,
                "id": disk.get("id"),
                "portalUrl": build_portal_disk_url(disk.get("id")),
            }
        )

    return inventory, requires_region_check


def get_migration_plan(inventory):
    plan = []
    for disk in inventory:
        reasons = []
        eligible = True

        if disk["diskVersion"] == "V2":
            eligible = False
            reasons.append("Disk is already Premium SSD v2")
        elif disk["diskVersion"] != "V1":
            eligible = False
            reasons.append(f"SKU '{disk['sku']}' is not Premium_LRS")
        else:
            if disk["premiumV2RegionSupported"] is False:
                eligible = False
                reasons.append(f"Region '{disk['location']}' does not report Premium SSD v2 availability")
            if disk["isOsDisk"]:
                eligible = False
                reasons.append("OS disks cannot be converted to Premium SSD v2")
            if disk["logicalSectorSize"] != 512:
                eligible = False
                reasons.append(f"Logical sector size '{disk['logicalSectorSize']}' is not supported for direct conversion")
            if disk["burstingEnabled"] is True:
                eligible = False
                reasons.append("Bursting is enabled")
            if disk["caching"] and disk["caching"] != "None":
                eligible = False
                reasons.append(f"Host caching '{disk['caching']}' must be disabled first")
            if disk["premiumV2RegionSupported"] is None:
                reasons.append("Region support could not be verified from Azure CLI")

        plan.append(
            {
                "resourceGroup": disk["resourceGroup"],
                "vmName": disk["vmName"],
                "diskName": disk["diskName"],
                "eligible": eligible,
                "plannedSku": "PremiumV2_LRS" if eligible else None,
                "reasons": "; ".join(reasons) if reasons else "Ready for conversion",
                "id": disk["id"],
                "portalUrl": disk["portalUrl"],
            }
        )
    return plan


def build_payload(subscription_id, resource_group_name=None):
    cache_key = (subscription_id, resource_group_name or "")
    cached_payload = get_cached_entry("payloads", cache_key)
    if cached_payload is not None:
        payload = dict(cached_payload)
        payload["cacheHit"] = True
        return payload

    started_at = time.perf_counter()
    inventory, region_support_checked = get_inventory(subscription_id, resource_group_name, include_region_support=False)
    migration_plan = get_migration_plan(inventory)
    unattached = [item for item in inventory if item["unattached"]]
    summary = {
        "totalDisks": len(inventory),
        "v1Disks": sum(1 for item in inventory if item["diskVersion"] == "V1"),
        "v2Disks": sum(1 for item in inventory if item["diskVersion"] == "V2"),
        "otherDisks": sum(1 for item in inventory if item["diskVersion"] == "Other"),
        "eligibleDisks": sum(1 for item in migration_plan if item["eligible"]),
        "unattachedDisks": len(unattached),
    }

    payload = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "subscriptionId": subscription_id,
        "resourceGroupName": resource_group_name,
        "summary": summary,
        "inventory": inventory,
        "migrationPlan": migration_plan,
        "unattached": unattached,
        "durationMs": int((time.perf_counter() - started_at) * 1000),
        "cacheHit": False,
        "regionSupportChecked": region_support_checked,
    }
    set_cached_entry("payloads", cache_key, payload, PAYLOAD_CACHE_TTL_SECONDS)
    return payload


def delete_unattached_disks(subscription_id, disks):
    deleted_count = 0

    for disk in disks:
        resource_group = disk.get("resourceGroup")
        disk_name = disk.get("diskName")
        if not resource_group or not disk_name:
            raise RuntimeError("Each disk must include 'resourceGroup' and 'diskName'.")

        current_disk = run_az_json(
            ["disk", "show", "--resource-group", resource_group, "--name", disk_name],
            subscription_id,
        )

        if current_disk.get("managedBy") or current_disk.get("diskState") == "Attached":
            raise RuntimeError(f"Disk '{disk_name}' is attached and cannot be deleted from this action.")

        run_az_json(
            ["disk", "delete", "--resource-group", resource_group, "--name", disk_name, "--yes"],
            subscription_id,
            timeout_seconds=600,
        )
        deleted_count += 1

    invalidate_payload_cache(subscription_id)
    return {"deletedCount": deleted_count}


def create_disk_snapshot(subscription_id, resource_group, disk_name, source_disk_id, location, sku_name):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    max_disk_name_length = 60
    snapshot_disk_segment = disk_name[:max_disk_name_length]
    snapshot_name = f"snapshot-{snapshot_disk_segment}-{timestamp}"
    run_az_json(
        [
            "snapshot",
            "create",
            "--resource-group",
            resource_group,
            "--name",
            snapshot_name,
            "--location",
            location,
            "--source",
            source_disk_id,
            "--sku",
            sku_name,
        ],
        subscription_id,
        timeout_seconds=600,
    )
    return snapshot_name


def migrate_disks(subscription_id, disks, create_backup_before=False):
    migrated_count = 0
    snapshot_count = 0
    vm_cache = run_az_json(["vm", "list"], subscription_id) or []
    vm_disk_map = build_vm_disk_map(vm_cache)
    deallocated_vm_keys = set()
    deallocated_vms = []

    def ensure_vm_deallocated(resource_group, vm_name):
        vm_key = (resource_group.lower(), vm_name.lower())
        if vm_key in deallocated_vm_keys:
            return
        run_az_json(
            ["vm", "deallocate", "--resource-group", resource_group, "--name", vm_name],
            subscription_id,
            timeout_seconds=900,
        )
        deallocated_vm_keys.add(vm_key)
        deallocated_vms.append({"resourceGroup": resource_group, "vmName": vm_name})

    try:
        for disk in disks:
            resource_group = disk.get("resourceGroup")
            disk_name = disk.get("diskName")
            if not resource_group or not disk_name:
                raise RuntimeError("Each disk must include 'resourceGroup' and 'diskName'.")

            current_disk = run_az_json(
                ["disk", "show", "--resource-group", resource_group, "--name", disk_name],
                subscription_id,
            )

            sku_name = ((current_disk.get("sku") or {}).get("name")) or ""
            if sku_name != "Premium_LRS":
                raise RuntimeError(f"Disk '{disk_name}' is not Premium_LRS.")
            if current_disk.get("osType"):
                raise RuntimeError(f"Disk '{disk_name}' appears to be an OS disk and cannot be migrated to Premium SSD v2.")
            if current_disk.get("logicalSectorSize") != 512:
                raise RuntimeError(f"Disk '{disk_name}' does not have a supported logical sector size for direct conversion.")
            if current_disk.get("burstingEnabled") is True:
                raise RuntimeError(f"Disk '{disk_name}' has bursting enabled.")

            attachment = vm_disk_map.get((current_disk.get("id") or "").lower())
            if attachment and attachment.get("isOsDisk"):
                raise RuntimeError(f"Disk '{disk_name}' is attached as an OS disk and cannot be migrated.")
            if attachment and attachment.get("caching") and attachment.get("caching") != "None":
                raise RuntimeError(f"Disk '{disk_name}' must have host caching set to None before migration.")

            if create_backup_before:
                create_disk_snapshot(
                    subscription_id,
                    resource_group,
                    disk_name,
                    current_disk["id"],
                    current_disk["location"],
                    sku_name,
                )
                snapshot_count += 1

            if attachment and attachment.get("vmName"):
                ensure_vm_deallocated(attachment["resourceGroup"], attachment["vmName"])

            run_az_json(
                ["disk", "update", "--resource-group", resource_group, "--name", disk_name, "--sku", "PremiumV2_LRS"],
                subscription_id,
                timeout_seconds=600,
            )
            migrated_count += 1
    finally:
        for vm_target in deallocated_vms:
            run_az_json(
                ["vm", "start", "--resource-group", vm_target["resourceGroup"], "--name", vm_target["vmName"]],
                subscription_id,
                timeout_seconds=900,
            )

    invalidate_payload_cache(subscription_id)
    return {"migratedCount": migrated_count, "snapshotCount": snapshot_count}


def backup_disks(subscription_id, disks):
    snapshot_count = 0
    for disk in disks:
        resource_group = disk.get("resourceGroup")
        disk_name = disk.get("diskName")
        if not resource_group or not disk_name:
            raise RuntimeError("Each disk must include 'resourceGroup' and 'diskName'.")

        current_disk = run_az_json(
            ["disk", "show", "--resource-group", resource_group, "--name", disk_name],
            subscription_id,
        )
        create_disk_snapshot(
            subscription_id,
            resource_group,
            disk_name,
            current_disk["id"],
            current_disk["location"],
            ((current_disk.get("sku") or {}).get("name")) or "Standard_LRS",
        )
        snapshot_count += 1

    return {"snapshotCount": snapshot_count}


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML.encode("utf-8"))
                return

            if parsed.path == "/api/subscriptions":
                return self.write_json({"subscriptions": get_subscriptions()})

            if parsed.path == "/api/inventory":
                query = parse_qs(parsed.query)
                subscription_id = (query.get("subscriptionId") or [None])[0]
                resource_group_name = (query.get("resourceGroupName") or [None])[0]
                if not subscription_id:
                    return self.write_json({"error": "Missing required query parameter 'subscriptionId'."}, HTTPStatus.BAD_REQUEST)
                return self.write_json(build_payload(subscription_id, resource_group_name))

            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
        except Exception as error:
            if not self._is_client_disconnect(error):
                self.write_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))

            if parsed.path == "/api/delete-unattached":
                subscription_id = payload.get("subscriptionId")
                disks = payload.get("disks") or []
                if not subscription_id:
                    return self.write_json({"error": "Missing 'subscriptionId'."}, HTTPStatus.BAD_REQUEST)
                if not disks:
                    return self.write_json({"error": "No disks were provided for deletion."}, HTTPStatus.BAD_REQUEST)
                return self.write_json(delete_unattached_disks(subscription_id, disks))

            if parsed.path == "/api/backup-disks":
                subscription_id = payload.get("subscriptionId")
                disks = payload.get("disks") or []
                if not subscription_id:
                    return self.write_json({"error": "Missing 'subscriptionId'."}, HTTPStatus.BAD_REQUEST)
                if not disks:
                    return self.write_json({"error": "No disks were provided for backup."}, HTTPStatus.BAD_REQUEST)
                return self.write_json(backup_disks(subscription_id, disks))

            if parsed.path == "/api/migrate-disks":
                subscription_id = payload.get("subscriptionId")
                disks = payload.get("disks") or []
                create_backup_before = bool(payload.get("createBackupBefore"))
                if not subscription_id:
                    return self.write_json({"error": "Missing 'subscriptionId'."}, HTTPStatus.BAD_REQUEST)
                if not disks:
                    return self.write_json({"error": "No disks were provided for migration."}, HTTPStatus.BAD_REQUEST)
                return self.write_json(migrate_disks(subscription_id, disks, create_backup_before))

            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
        except Exception as error:
            if not self._is_client_disconnect(error):
                self.write_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format, *args):
        return

    def write_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            if not self._is_client_disconnect(error):
                raise

    @staticmethod
    def _is_client_disconnect(error):
        return isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, socket.error))


def main():
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Azure Disk Dashboard running at http://{HOST}:{PORT}")
    print("Use Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
