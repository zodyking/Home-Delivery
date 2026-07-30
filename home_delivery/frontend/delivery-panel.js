/**
 * Home Delivery Panel - Vanilla JS for HA custom panel / standalone Ingress
 * Theme and auto-save patterns ported from home-weather.
 */
class HomeDeliveryPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._loading = false;
    this._error = null;
    this._currentView = "packages";
    this._settingsPane = "general";
    this._packages = [];
    this._mailState = null;
    this._mediaPlayers = [];
    this._ttsEntities = [];
    this._settings = {};
    this._settings.appearance = this._readStoredAppearance();
    this._autoSaveTimer = null;
    this._saveStatus = "idle";
    this._saveStatusText = "";
    this._narrow = null;
    this._addPackageModal = false;
    this._selectedPackage = null;
    this._refreshingPackage = null;
  }

  get _isNarrow() {
    return this._narrow ?? this._mediaQuery?.matches ?? false;
  }

  connectedCallback() {
    this._mediaQuery = window.matchMedia("(max-width: 768px)");
    this._onMediaChange = () => this._render();
    this._mediaQuery.addEventListener("change", this._onMediaChange);
    this._render();
    this._loadConfig();
  }

  disconnectedCallback() {
    if (this._mediaQuery && this._onMediaChange) {
      this._mediaQuery.removeEventListener("change", this._onMediaChange);
    }
  }

  // ============================================================================
  // API Communication
  // ============================================================================

  _getApiBase() {
    // Ingress path detection for HA add-on
    const path = window.location.pathname;
    const match = path.match(/^(\/api\/hassio_ingress\/[^/]+)/);
    return match ? match[1] : "";
  }

  async _fetchApi(endpoint, options = {}) {
    const base = this._getApiBase();
    const url = `${base}${endpoint}`;
    const resp = await fetch(url, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }

  async _loadConfig() {
    try {
      this._loading = true;
      this._error = null;
      this._render();

      const [configResp, packagesResp, mailResp, entitiesResp] = await Promise.all([
        this._fetchApi("/api/config"),
        this._fetchApi("/api/packages"),
        this._fetchApi("/api/mail"),
        this._fetchApi("/api/ha-entities"),
      ]);

      this._config = configResp.config || {};
      this._settings = JSON.parse(JSON.stringify(this._config));
      this._packages = packagesResp.packages || [];
      this._mailState = mailResp;
      this._mediaPlayers = entitiesResp.media_players || [];
      this._ttsEntities = entitiesResp.tts_entities || [];

      // Restore appearance
      if (this._settings.appearance && this._settings.appearance.mode) {
        this._settings.appearance = {
          mode: this._settings.appearance.mode === "light" ? "light" : "dark",
          overrides: this._settings.appearance.overrides || {},
        };
      } else {
        this._settings.appearance = this._readStoredAppearance();
      }
      this._persistAppearanceLocal();
      this._applyTheme();

      this._loading = false;
      this._render();
    } catch (e) {
      console.error("Failed to load config:", e);
      this._loading = false;
      this._error = e.message;
      this._render();
    }
  }

  async _saveConfig(config) {
    return this._fetchApi("/api/config", {
      method: "PUT",
      body: JSON.stringify({ config }),
    });
  }

  async _addPackage(data) {
    const resp = await this._fetchApi("/api/packages", {
      method: "POST",
      body: JSON.stringify(data),
    });
    if (resp.package) {
      this._packages.push(resp.package);
    }
    return resp;
  }

  async _deletePackage(id) {
    await this._fetchApi(`/api/packages/${id}`, { method: "DELETE" });
    this._packages = this._packages.filter(p => p.id !== id);
  }

  async _refreshPackage(id) {
    this._refreshingPackage = id;
    this._render();
    try {
      const resp = await this._fetchApi(`/api/packages/${id}/refresh`, { method: "POST" });
      if (resp.package) {
        const idx = this._packages.findIndex(p => p.id === id);
        if (idx >= 0) this._packages[idx] = resp.package;
      }
    } finally {
      this._refreshingPackage = null;
      this._render();
    }
  }

  // ============================================================================
  // Theme System (ported from home-weather)
  // ============================================================================

  _readStoredAppearance() {
    try {
      const mode = window.localStorage.getItem("hd_theme_mode");
      let overrides = {};
      const rawOv = window.localStorage.getItem("hd_theme_overrides");
      if (rawOv) {
        const parsed = JSON.parse(rawOv);
        if (parsed && typeof parsed === "object") overrides = parsed;
      }
      return { mode: mode === "light" ? "light" : "dark", overrides };
    } catch (_) {
      return { mode: "dark", overrides: {} };
    }
  }

  _persistAppearanceLocal() {
    const { mode, overrides } = this._getAppearance();
    try {
      window.localStorage.setItem("hd_theme_mode", mode);
      window.localStorage.setItem("hd_theme_overrides", JSON.stringify(overrides || {}));
    } catch (_) {}
  }

  _persistAppearance() {
    this._persistAppearanceLocal();
    if (this._config) this._config.appearance = this._getAppearance();
    this._scheduleAutoSave();
  }

  _getAppearance() {
    const app = this._settings?.appearance || {};
    return {
      mode: app.mode === "light" ? "light" : "dark",
      overrides: app.overrides && typeof app.overrides === "object" ? app.overrides : {},
    };
  }

  _themeBase(mode) {
    if (mode === "light") {
      return {
        bg: "#f8fafc",
        surface: "#ffffff",
        surface2: "#f1f5f9",
        elevated: "#ffffff",
        inputBg: "#ffffff",
        text: "#0f172a",
        muted: "#64748b",
        disabled: "#94a3b8",
        accent: "#0ea5e9",
        accentHover: "#0284c7",
        accentDim: "rgba(14, 165, 233, 0.12)",
        danger: "#ef4444",
        warning: "#f59e0b",
        success: "#22c55e",
        border: "#e2e8f0",
        borderStrong: "#cbd5e1",
        hover: "#f1f5f9",
      };
    }
    return {
      bg: "#0d1117",
      surface: "#161b22",
      surface2: "#1c2128",
      elevated: "#21262d",
      inputBg: "#0d1117",
      text: "#e6edf3",
      muted: "#8b949e",
      disabled: "#6e7681",
      accent: "#58a6ff",
      accentHover: "#79b8ff",
      accentDim: "rgba(88, 166, 255, 0.15)",
      danger: "#f85149",
      warning: "#d29922",
      success: "#3fb950",
      border: "#30363d",
      borderStrong: "#484f58",
      hover: "#1c2128",
    };
  }

  _hexToRgba(hex, alpha) {
    const h = String(hex || "").replace("#", "");
    const full = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
    const int = parseInt(full, 16);
    if (Number.isNaN(int) || full.length !== 6) return `rgba(88, 166, 255, ${alpha})`;
    const r = (int >> 16) & 255, g = (int >> 8) & 255, b = int & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  _shiftColor(hex, amt) {
    const h = String(hex || "").replace("#", "");
    const full = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
    const int = parseInt(full, 16);
    if (Number.isNaN(int) || full.length !== 6) return hex;
    const clamp = v => Math.max(0, Math.min(255, v));
    const r = clamp(((int >> 16) & 255) + amt);
    const g = clamp(((int >> 8) & 255) + amt);
    const b = clamp((int & 255) + amt);
    return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
  }

  _applyTheme() {
    const host = this;
    if (!host || !host.style) return;
    const { mode, overrides: ov } = this._getAppearance();
    const base = this._themeBase(mode);
    const t = { ...base };
    if (ov.accent) {
      t.accent = ov.accent;
      t.accentHover = this._shiftColor(ov.accent, mode === "light" ? -14 : 18);
      t.accentDim = this._hexToRgba(ov.accent, 0.15);
    }
    if (ov.bg) t.bg = ov.bg;
    if (ov.surface) {
      t.surface = ov.surface;
      t.surface2 = this._shiftColor(ov.surface, mode === "light" ? -10 : 10);
      t.elevated = ov.surface;
      t.inputBg = ov.surface;
    }
    if (ov.text) t.text = ov.text;
    if (ov.muted) { t.muted = ov.muted; t.disabled = ov.muted; }
    if (ov.border) { t.border = ov.border; t.borderStrong = ov.border; }
    if (ov.danger) t.danger = ov.danger;
    if (ov.warning) t.warning = ov.warning;
    if (ov.success) t.success = ov.success;

    const set = (k, v) => host.style.setProperty(k, v);
    set("--hd-bg", t.bg);
    set("--hd-surface", t.surface);
    set("--hd-surface-2", t.surface2);
    set("--hd-elevated", t.elevated);
    set("--hd-input-bg", t.inputBg);
    set("--hd-text", t.text);
    set("--hd-muted", t.muted);
    set("--hd-disabled", t.disabled);
    set("--hd-accent", t.accent);
    set("--hd-accent-hover", t.accentHover);
    set("--hd-accent-dim", t.accentDim);
    set("--hd-danger", t.danger);
    set("--hd-warning", t.warning);
    set("--hd-success", t.success);
    set("--hd-border", t.border);
    set("--hd-border-strong", t.borderStrong);
    set("--hd-hover", t.hover);

    const shadows = mode === "light"
      ? {
          sm: "0 1px 2px rgba(15, 23, 42, 0.05)",
          base: "0 1px 2px rgba(15, 23, 42, 0.05), 0 2px 6px rgba(15, 23, 42, 0.06)",
          md: "0 2px 8px rgba(15, 23, 42, 0.08), 0 1px 3px rgba(15, 23, 42, 0.05)",
          lg: "0 12px 32px rgba(15, 23, 42, 0.12), 0 4px 8px rgba(15, 23, 42, 0.06)",
        }
      : {
          sm: "0 1px 2px rgba(0, 0, 0, 0.3)",
          base: "0 2px 8px rgba(0, 0, 0, 0.35)",
          md: "0 4px 16px rgba(0, 0, 0, 0.35)",
          lg: "0 12px 40px rgba(0, 0, 0, 0.5)",
        };
    set("--shadow-sm", shadows.sm);
    set("--shadow", shadows.base);
    set("--shadow-md", shadows.md);
    set("--shadow-lg", shadows.lg);

    host.setAttribute("data-hd-theme", mode);
  }

  // ============================================================================
  // Auto-Save (ported from home-weather)
  // ============================================================================

  _scheduleAutoSave() {
    if (this._currentView !== "settings") return;
    if (this._autoSaveTimer) clearTimeout(this._autoSaveTimer);
    this._setSaveStatus("saving", "Saving\u2026");
    this._autoSaveTimer = setTimeout(() => {
      this._autoSaveTimer = null;
      this._saveSettings({ silent: true });
    }, 800);
  }

  _isEditingField() {
    const active = this.shadowRoot?.activeElement;
    if (!active) return false;
    const tag = active.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  async _saveSettings({ silent = false } = {}) {
    if (this._autoSaveTimer) { clearTimeout(this._autoSaveTimer); this._autoSaveTimer = null; }
    this._syncSettingsFromForm();
    const snapshot = JSON.parse(JSON.stringify(this._settings));
    this._config = JSON.parse(JSON.stringify(snapshot));
    this._setSaveStatus("saving", "Saving\u2026");
    try {
      await this._saveConfig(snapshot);
      this._setSaveStatus("saved", "All changes saved");
      if (!silent) this._showToast("\u2713 Saved");
      setTimeout(() => this._setSaveStatus("idle", ""), 2500);
    } catch (e) {
      console.error("Save failed:", e);
      this._setSaveStatus("error", "Save failed");
      this._showToast("\u26a0 Save failed", { error: true });
    }
  }

  _setSaveStatus(status, text) {
    this._saveStatus = status;
    this._saveStatusText = text;
    const el = this.shadowRoot?.querySelector(".save-status");
    if (el) {
      el.textContent = text;
      el.className = `save-status ${status}`;
    }
  }

  _showToast(message, { error = false } = {}) {
    const s = this.shadowRoot;
    if (!s) return;
    let toast = s.querySelector(".toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      s.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.toggle("error", error);
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 3000);
  }

  _syncSettingsFromForm() {
    const s = this.shadowRoot;
    if (!s) return;

    // Mail settings
    const mailEnabled = s.querySelector("#mail-enabled");
    const imapHost = s.querySelector("#imap-host");
    const imapPort = s.querySelector("#imap-port");
    const imapUser = s.querySelector("#imap-user");
    const imapPassword = s.querySelector("#imap-password");
    const imapFolder = s.querySelector("#imap-folder");

    if (!this._settings.mail) this._settings.mail = {};
    if (mailEnabled) this._settings.mail.enabled = mailEnabled.checked;
    if (imapHost) this._settings.mail.imap_host = imapHost.value;
    if (imapPort) this._settings.mail.imap_port = parseInt(imapPort.value) || 993;
    if (imapUser) this._settings.mail.imap_user = imapUser.value;
    if (imapPassword && imapPassword.value !== "********") {
      this._settings.mail.imap_password = imapPassword.value;
    }
    if (imapFolder) this._settings.mail.folder = imapFolder.value;

    // TTS settings
    const ttsEnabled = s.querySelector("#tts-enabled");
    const ttsStatusChange = s.querySelector("#tts-status-change");
    const ttsOutForDelivery = s.querySelector("#tts-out-for-delivery");
    const ttsDelivered = s.querySelector("#tts-delivered");
    const ttsMailArrived = s.querySelector("#tts-mail-arrived");
    const ttsStartTime = s.querySelector("#tts-start-time");
    const ttsEndTime = s.querySelector("#tts-end-time");

    if (!this._settings.tts) this._settings.tts = {};
    if (ttsEnabled) this._settings.tts.enabled = ttsEnabled.checked;
    if (ttsStatusChange) this._settings.tts.enable_status_change = ttsStatusChange.checked;
    if (ttsOutForDelivery) this._settings.tts.enable_out_for_delivery = ttsOutForDelivery.checked;
    if (ttsDelivered) this._settings.tts.enable_delivered = ttsDelivered.checked;
    if (ttsMailArrived) this._settings.tts.enable_mail_arrived = ttsMailArrived.checked;
    if (ttsStartTime) this._settings.tts.start_time = ttsStartTime.value;
    if (ttsEndTime) this._settings.tts.end_time = ttsEndTime.value;

    // Polling settings
    const defaultInterval = s.querySelector("#polling-default");
    const ofdInterval = s.querySelector("#polling-ofd");

    if (!this._settings.polling) this._settings.polling = {};
    if (defaultInterval) this._settings.polling.default_interval_seconds = parseInt(defaultInterval.value) || 3600;
    if (ofdInterval) this._settings.polling.out_for_delivery_interval_seconds = parseInt(ofdInterval.value) || 300;
  }

  // ============================================================================
  // Carrier Badge Helper
  // ============================================================================

  _carrierBadge(carrier) {
    const colors = {
      usps: { bg: "#004B87", text: "#fff", label: "USPS" },
      ups: { bg: "#351C15", text: "#FFB500", label: "UPS" },
      fedex: { bg: "#4D148C", text: "#FF6600", label: "FedEx" },
    };
    const c = colors[carrier] || { bg: "#666", text: "#fff", label: carrier?.toUpperCase() || "?" };
    return `<span class="carrier-badge" style="background:${c.bg};color:${c.text}">${c.label}</span>`;
  }

  _statusClass(pkg) {
    if (pkg.delivered) return "delivered";
    if (pkg.out_for_delivery) return "out-for-delivery";
    if (pkg.error) return "error";
    return "in-transit";
  }

  // ============================================================================
  // Render
  // ============================================================================

  _render() {
    const s = this.shadowRoot;
    if (!s) return;
    this._applyTheme();

    s.innerHTML = `
      <style>${this._getStyles()}</style>
      <div class="app">
        <header class="header">
          <h1 class="logo">
            <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
              <path d="M20 8h-3V6c0-1.1-.9-2-2-2H9c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v10h20V10c0-1.1-.9-2-2-2zM9 6h6v2H9V6zm11 12H4v-3h16v3z"/>
            </svg>
            Home Delivery
          </h1>
          <nav class="nav">
            <button class="nav-btn ${this._currentView === "packages" ? "active" : ""}" data-view="packages">Packages</button>
            <button class="nav-btn ${this._currentView === "mail" ? "active" : ""}" data-view="mail">Mail</button>
            <button class="nav-btn ${this._currentView === "settings" ? "active" : ""}" data-view="settings">Settings</button>
          </nav>
        </header>
        <main class="main">
          ${this._loading ? this._renderLoading() : ""}
          ${this._error ? this._renderError() : ""}
          ${!this._loading && !this._error ? this._renderView() : ""}
        </main>
        ${this._addPackageModal ? this._renderAddPackageModal() : ""}
      </div>
    `;

    this._bindEvents();
  }

  _renderLoading() {
    return `
      <div class="loading">
        <div class="spinner"></div>
        <p>Loading...</p>
      </div>
    `;
  }

  _renderError() {
    return `
      <div class="error-box">
        <p>Error: ${this._error}</p>
        <button class="btn" onclick="location.reload()">Retry</button>
      </div>
    `;
  }

  _renderView() {
    switch (this._currentView) {
      case "packages": return this._renderPackagesView();
      case "mail": return this._renderMailView();
      case "settings": return this._renderSettingsView();
      default: return "";
    }
  }

  _renderPackagesView() {
    const active = this._packages.filter(p => !p.delivered);
    const delivered = this._packages.filter(p => p.delivered);

    return `
      <div class="packages-view">
        <div class="section-header">
          <h2>Active Packages (${active.length})</h2>
          <button class="btn btn-primary" data-action="add-package">+ Add Package</button>
        </div>
        ${active.length === 0 ? `
          <div class="empty-state">
            <svg viewBox="0 0 24 24" width="48" height="48" fill="currentColor" style="opacity:0.3">
              <path d="M20 8h-3V6c0-1.1-.9-2-2-2H9c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v10h20V10c0-1.1-.9-2-2-2zM9 6h6v2H9V6zm11 12H4v-3h16v3z"/>
            </svg>
            <p>No packages being tracked</p>
            <p class="muted">Click "Add Package" to start tracking</p>
          </div>
        ` : `
          <div class="package-grid">
            ${active.map(p => this._renderPackageCard(p)).join("")}
          </div>
        `}
        ${delivered.length > 0 ? `
          <div class="section-header" style="margin-top:32px">
            <h2>Delivered (${delivered.length})</h2>
          </div>
          <div class="package-grid delivered">
            ${delivered.map(p => this._renderPackageCard(p)).join("")}
          </div>
        ` : ""}
      </div>
      ${this._selectedPackage ? this._renderPackageDetail() : ""}
    `;
  }

  _renderPackageCard(pkg) {
    const isRefreshing = this._refreshingPackage === pkg.id;
    return `
      <div class="package-card ${this._statusClass(pkg)}" data-package-id="${pkg.id}">
        <div class="package-header">
          ${this._carrierBadge(pkg.carrier)}
          <span class="package-status">${pkg.status || "Pending"}</span>
        </div>
        <div class="package-tracking">${pkg.tracking_number}</div>
        ${pkg.recipient ? `<div class="package-meta">For: ${pkg.recipient}</div>` : ""}
        ${pkg.destination ? `<div class="package-meta">To: ${pkg.destination}</div>` : ""}
        ${pkg.status_detail ? `<div class="package-detail">${pkg.status_detail}</div>` : ""}
        ${pkg.events?.length > 0 ? `
          <div class="package-latest">
            <span class="event-date">${pkg.events[0].date || ""}</span>
            <span class="event-location">${pkg.events[0].location || ""}</span>
          </div>
        ` : ""}
        ${pkg.error ? `<div class="package-error">${pkg.error}</div>` : ""}
        <div class="package-actions">
          <button class="btn btn-sm" data-action="refresh" data-id="${pkg.id}" ${isRefreshing ? "disabled" : ""}>
            ${isRefreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button class="btn btn-sm" data-action="view" data-id="${pkg.id}">Details</button>
          <button class="btn btn-sm btn-danger" data-action="delete" data-id="${pkg.id}">Remove</button>
        </div>
      </div>
    `;
  }

  _renderPackageDetail() {
    const pkg = this._selectedPackage;
    if (!pkg) return "";
    return `
      <div class="modal-backdrop" data-action="close-detail">
        <div class="modal" onclick="event.stopPropagation()">
          <div class="modal-header">
            <h2>${this._carrierBadge(pkg.carrier)} ${pkg.tracking_number}</h2>
            <button class="close-btn" data-action="close-detail">&times;</button>
          </div>
          <div class="modal-body">
            <div class="detail-row">
              <span class="detail-label">Status</span>
              <span class="detail-value status-${this._statusClass(pkg)}">${pkg.status || "Pending"}</span>
            </div>
            ${pkg.recipient ? `
              <div class="detail-row">
                <span class="detail-label">Recipient</span>
                <span class="detail-value">${pkg.recipient}</span>
              </div>
            ` : ""}
            ${pkg.destination ? `
              <div class="detail-row">
                <span class="detail-label">Destination</span>
                <span class="detail-value">${pkg.destination}</span>
              </div>
            ` : ""}
            <h3 style="margin-top:20px">Tracking History</h3>
            ${pkg.events?.length > 0 ? `
              <div class="timeline">
                ${pkg.events.map((e, i) => `
                  <div class="timeline-item ${i === 0 ? "current" : ""}">
                    <div class="timeline-marker"></div>
                    <div class="timeline-content">
                      <div class="timeline-date">${e.date || ""} ${e.time || ""}</div>
                      <div class="timeline-desc">${e.description || ""}</div>
                      <div class="timeline-location">${e.location || ""}</div>
                    </div>
                  </div>
                `).join("")}
              </div>
            ` : `<p class="muted">No tracking events yet</p>`}
          </div>
        </div>
      </div>
    `;
  }

  _renderMailView() {
    const mail = this._mailState || {};
    return `
      <div class="mail-view">
        <h2>USPS Informed Delivery</h2>
        ${!mail.configured ? `
          <div class="info-box">
            <p>Mail tracking is not configured. Go to Settings &gt; Mail to set up IMAP.</p>
          </div>
        ` : !mail.enabled ? `
          <div class="info-box">
            <p>Mail tracking is disabled. Enable it in Settings &gt; Mail.</p>
          </div>
        ` : `
          <div class="mail-summary">
            <div class="mail-count">
              <span class="count">${mail.piece_count || 0}</span>
              <span class="label">Mail Pieces Today</span>
            </div>
            ${mail.gif_url ? `
              <div class="mail-preview">
                <img src="${this._getApiBase()}${mail.gif_url}" alt="Mail Preview" />
              </div>
            ` : ""}
            <div class="mail-meta">
              ${mail.last_check ? `<p>Last checked: ${new Date(mail.last_check).toLocaleString()}</p>` : ""}
              <button class="btn" data-action="refresh-mail">Check Now</button>
            </div>
          </div>
        `}
      </div>
    `;
  }

  _renderSettingsView() {
    const mail = this._settings.mail || {};
    const tts = this._settings.tts || {};
    const polling = this._settings.polling || {};
    const { mode } = this._getAppearance();

    return `
      <div class="settings-view">
        <div class="settings-header">
          <h2>Settings</h2>
          <span class="save-status ${this._saveStatus}">${this._saveStatusText}</span>
        </div>
        <div class="settings-tabs">
          <button class="tab-btn ${this._settingsPane === "general" ? "active" : ""}" data-pane="general">General</button>
          <button class="tab-btn ${this._settingsPane === "mail" ? "active" : ""}" data-pane="mail">Mail</button>
          <button class="tab-btn ${this._settingsPane === "tts" ? "active" : ""}" data-pane="tts">TTS</button>
          <button class="tab-btn ${this._settingsPane === "appearance" ? "active" : ""}" data-pane="appearance">Appearance</button>
        </div>
        <form class="settings-form" onsubmit="return false">
          ${this._settingsPane === "general" ? `
            <section class="settings-section">
              <h3>Polling</h3>
              <div class="form-group">
                <label for="polling-default">Default Interval (seconds)</label>
                <input type="number" id="polling-default" value="${polling.default_interval_seconds || 3600}" min="300" />
                <p class="hint">How often to check package status (minimum 5 minutes)</p>
              </div>
              <div class="form-group">
                <label for="polling-ofd">Out for Delivery Interval (seconds)</label>
                <input type="number" id="polling-ofd" value="${polling.out_for_delivery_interval_seconds || 300}" min="60" />
                <p class="hint">Faster polling when package is out for delivery</p>
              </div>
            </section>
          ` : ""}
          ${this._settingsPane === "mail" ? `
            <section class="settings-section">
              <h3>USPS Informed Delivery</h3>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" id="mail-enabled" ${mail.enabled ? "checked" : ""} />
                  Enable Mail Tracking
                </label>
              </div>
              <div class="form-group">
                <label for="imap-host">IMAP Server</label>
                <input type="text" id="imap-host" value="${mail.imap_host || ""}" placeholder="imap.gmail.com" />
              </div>
              <div class="form-group">
                <label for="imap-port">IMAP Port</label>
                <input type="number" id="imap-port" value="${mail.imap_port || 993}" />
              </div>
              <div class="form-group">
                <label for="imap-user">Email Address</label>
                <input type="email" id="imap-user" value="${mail.imap_user || ""}" placeholder="you@example.com" />
              </div>
              <div class="form-group">
                <label for="imap-password">Password / App Password</label>
                <input type="password" id="imap-password" value="${mail.imap_password ? "********" : ""}" placeholder="App password for Gmail" />
              </div>
              <div class="form-group">
                <label for="imap-folder">Folder</label>
                <input type="text" id="imap-folder" value="${mail.folder || "INBOX"}" />
              </div>
            </section>
          ` : ""}
          ${this._settingsPane === "tts" ? `
            <section class="settings-section">
              <h3>Text-to-Speech Announcements</h3>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" id="tts-enabled" ${tts.enabled ? "checked" : ""} />
                  Enable TTS Announcements
                </label>
              </div>
              <h4>Announcement Triggers</h4>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" id="tts-status-change" ${tts.enable_status_change !== false ? "checked" : ""} />
                  Status Changes
                </label>
              </div>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" id="tts-out-for-delivery" ${tts.enable_out_for_delivery !== false ? "checked" : ""} />
                  Out for Delivery
                </label>
              </div>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" id="tts-delivered" ${tts.enable_delivered !== false ? "checked" : ""} />
                  Delivered
                </label>
              </div>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" id="tts-mail-arrived" ${tts.enable_mail_arrived !== false ? "checked" : ""} />
                  Mail Arrived
                </label>
              </div>
              <h4>Quiet Hours</h4>
              <div class="form-row">
                <div class="form-group">
                  <label for="tts-start-time">Start</label>
                  <input type="time" id="tts-start-time" value="${tts.start_time || "08:00"}" />
                </div>
                <div class="form-group">
                  <label for="tts-end-time">End</label>
                  <input type="time" id="tts-end-time" value="${tts.end_time || "21:00"}" />
                </div>
              </div>
              <p class="hint">Announcements only play between these hours</p>
            </section>
          ` : ""}
          ${this._settingsPane === "appearance" ? `
            <section class="settings-section">
              <h3>Theme</h3>
              <div class="theme-toggle">
                <button type="button" class="theme-btn ${mode === "dark" ? "active" : ""}" data-theme="dark">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 3a9 9 0 109 9c0-.46-.04-.92-.1-1.36a5.39 5.39 0 01-4.4 2.26 5.4 5.4 0 01-5.4-5.4c0-1.81.9-3.42 2.26-4.4A9.1 9.1 0 0012 3z"/></svg>
                  Dark
                </button>
                <button type="button" class="theme-btn ${mode === "light" ? "active" : ""}" data-theme="light">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zM2 13h2c.55 0 1-.45 1-1s-.45-1-1-1H2c-.55 0-1 .45-1 1s.45 1 1 1zm18 0h2c.55 0 1-.45 1-1s-.45-1-1-1h-2c-.55 0-1 .45-1 1s.45 1 1 1zM11 2v2c0 .55.45 1 1 1s1-.45 1-1V2c0-.55-.45-1-1-1s-1 .45-1 1zm0 18v2c0 .55.45 1 1 1s1-.45 1-1v-2c0-.55-.45-1-1-1s-1 .45-1 1zM5.99 4.58a.996.996 0 00-1.41 0 .996.996 0 000 1.41l1.06 1.06c.39.39 1.03.39 1.41 0s.39-1.03 0-1.41L5.99 4.58zm12.37 12.37a.996.996 0 00-1.41 0 .996.996 0 000 1.41l1.06 1.06c.39.39 1.03.39 1.41 0a.996.996 0 000-1.41l-1.06-1.06zm1.06-10.96a.996.996 0 000-1.41.996.996 0 00-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06zM7.05 18.36a.996.996 0 000-1.41.996.996 0 00-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06z"/></svg>
                  Light
                </button>
              </div>
            </section>
          ` : ""}
          <div class="form-actions">
            <button type="button" class="btn btn-primary" data-action="save-settings">Save Settings</button>
          </div>
        </form>
      </div>
    `;
  }

  _renderAddPackageModal() {
    return `
      <div class="modal-backdrop" data-action="close-add">
        <div class="modal" onclick="event.stopPropagation()">
          <div class="modal-header">
            <h2>Add Package</h2>
            <button class="close-btn" data-action="close-add">&times;</button>
          </div>
          <form class="modal-body" id="add-package-form">
            <div class="form-group">
              <label for="tracking-number">Tracking Number *</label>
              <input type="text" id="tracking-number" required placeholder="e.g., 9400111899560438600329" />
              <p class="hint">Carrier will be auto-detected</p>
            </div>
            <div class="form-group">
              <label for="recipient">Who it's for</label>
              <input type="text" id="recipient" placeholder="e.g., Mom, John" />
            </div>
            <div class="form-group">
              <label for="destination">Destination</label>
              <input type="text" id="destination" placeholder="e.g., Home, Work, Grandma's" />
            </div>
            <div class="form-actions">
              <button type="button" class="btn" data-action="close-add">Cancel</button>
              <button type="submit" class="btn btn-primary">Add Package</button>
            </div>
          </form>
        </div>
      </div>
    `;
  }

  // ============================================================================
  // Event Binding
  // ============================================================================

  _bindEvents() {
    const s = this.shadowRoot;
    if (!s) return;

    // Navigation
    s.querySelectorAll(".nav-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        this._currentView = btn.dataset.view;
        this._render();
      });
    });

    // Settings tabs
    s.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        this._settingsPane = btn.dataset.pane;
        this._render();
      });
    });

    // Theme buttons
    s.querySelectorAll(".theme-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        if (!this._settings.appearance) this._settings.appearance = {};
        this._settings.appearance.mode = btn.dataset.theme;
        this._applyTheme();
        this._persistAppearance();
        this._render();
      });
    });

    // Action buttons
    s.querySelectorAll("[data-action]").forEach(el => {
      el.addEventListener("click", (e) => this._handleAction(e, el.dataset.action, el.dataset));
    });

    // Form auto-save
    const form = s.querySelector(".settings-form");
    if (form) {
      const onChange = () => this._scheduleAutoSave();
      form.addEventListener("input", onChange);
      form.addEventListener("change", onChange);
    }

    // Add package form
    const addForm = s.querySelector("#add-package-form");
    if (addForm) {
      addForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const tracking = s.querySelector("#tracking-number")?.value?.trim();
        const recipient = s.querySelector("#recipient")?.value?.trim();
        const destination = s.querySelector("#destination")?.value?.trim();
        if (!tracking) return;
        try {
          await this._addPackage({ tracking_number: tracking, recipient, destination });
          this._addPackageModal = false;
          this._render();
          this._showToast("Package added");
        } catch (err) {
          this._showToast(err.message, { error: true });
        }
      });
    }
  }

  async _handleAction(e, action, data) {
    switch (action) {
      case "add-package":
        this._addPackageModal = true;
        this._render();
        break;
      case "close-add":
        this._addPackageModal = false;
        this._render();
        break;
      case "refresh":
        await this._refreshPackage(data.id);
        break;
      case "view":
        this._selectedPackage = this._packages.find(p => p.id === data.id);
        this._render();
        break;
      case "close-detail":
        this._selectedPackage = null;
        this._render();
        break;
      case "delete":
        if (confirm("Remove this package from tracking?")) {
          await this._deletePackage(data.id);
          this._render();
          this._showToast("Package removed");
        }
        break;
      case "refresh-mail":
        try {
          await this._fetchApi("/api/mail/refresh", { method: "POST" });
          const mailResp = await this._fetchApi("/api/mail");
          this._mailState = mailResp;
          this._render();
          this._showToast("Mail checked");
        } catch (err) {
          this._showToast(err.message, { error: true });
        }
        break;
      case "save-settings":
        await this._saveSettings();
        break;
    }
  }

  // ============================================================================
  // Styles
  // ============================================================================

  _getStyles() {
    return `
      :host {
        display: block;
        min-height: 100vh;
        background: var(--hd-bg);
        color: var(--hd-text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }

      * { box-sizing: border-box; }

      .app {
        max-width: 1200px;
        margin: 0 auto;
        padding: 0 16px;
      }

      /* Header */
      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 0;
        border-bottom: 1px solid var(--hd-border);
        flex-wrap: wrap;
        gap: 12px;
      }

      .logo {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 20px;
        font-weight: 600;
        margin: 0;
        color: var(--hd-accent);
      }

      .nav {
        display: flex;
        gap: 8px;
      }

      .nav-btn {
        background: transparent;
        border: 1px solid var(--hd-border);
        color: var(--hd-text);
        padding: 8px 16px;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
        transition: all 0.15s;
      }

      .nav-btn:hover {
        background: var(--hd-hover);
      }

      .nav-btn.active {
        background: var(--hd-accent);
        border-color: var(--hd-accent);
        color: #fff;
      }

      /* Main */
      .main {
        padding: 24px 0;
      }

      /* Loading / Error */
      .loading, .error-box {
        text-align: center;
        padding: 48px;
      }

      .spinner {
        width: 40px;
        height: 40px;
        border: 3px solid var(--hd-border);
        border-top-color: var(--hd-accent);
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin: 0 auto 16px;
      }

      @keyframes spin {
        to { transform: rotate(360deg); }
      }

      .error-box {
        background: var(--hd-surface);
        border: 1px solid var(--hd-danger);
        border-radius: 8px;
        color: var(--hd-danger);
      }

      /* Buttons */
      .btn {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        color: var(--hd-text);
        padding: 10px 16px;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
        transition: all 0.15s;
      }

      .btn:hover {
        background: var(--hd-hover);
        border-color: var(--hd-border-strong);
      }

      .btn:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .btn-primary {
        background: var(--hd-accent);
        border-color: var(--hd-accent);
        color: #fff;
      }

      .btn-primary:hover {
        background: var(--hd-accent-hover);
      }

      .btn-danger {
        color: var(--hd-danger);
        border-color: var(--hd-danger);
      }

      .btn-danger:hover {
        background: var(--hd-danger);
        color: #fff;
      }

      .btn-sm {
        padding: 6px 12px;
        font-size: 12px;
      }

      /* Section Header */
      .section-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      }

      .section-header h2 {
        font-size: 18px;
        font-weight: 600;
        margin: 0;
      }

      /* Empty State */
      .empty-state {
        text-align: center;
        padding: 48px;
        background: var(--hd-surface);
        border-radius: 12px;
        border: 1px dashed var(--hd-border);
      }

      .empty-state .muted {
        color: var(--hd-muted);
        margin-top: 8px;
      }

      /* Package Grid */
      .package-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 16px;
      }

      .package-grid.delivered {
        opacity: 0.7;
      }

      /* Package Card */
      .package-card {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: 12px;
        padding: 16px;
        transition: all 0.15s;
      }

      .package-card:hover {
        border-color: var(--hd-border-strong);
        box-shadow: var(--shadow-md);
      }

      .package-card.out-for-delivery {
        border-color: var(--hd-warning);
      }

      .package-card.delivered {
        border-color: var(--hd-success);
      }

      .package-card.error {
        border-color: var(--hd-danger);
      }

      .package-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
      }

      .carrier-badge {
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
      }

      .package-status {
        font-size: 14px;
        font-weight: 600;
      }

      .package-tracking {
        font-family: monospace;
        font-size: 13px;
        color: var(--hd-muted);
        margin-bottom: 8px;
      }

      .package-meta {
        font-size: 13px;
        color: var(--hd-muted);
      }

      .package-detail {
        font-size: 13px;
        margin-top: 8px;
      }

      .package-latest {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid var(--hd-border);
      }

      .package-error {
        font-size: 12px;
        color: var(--hd-danger);
        margin-top: 8px;
      }

      .package-actions {
        display: flex;
        gap: 8px;
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid var(--hd-border);
      }

      /* Modal */
      .modal-backdrop {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.6);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1000;
        padding: 16px;
      }

      .modal {
        background: var(--hd-surface);
        border-radius: 12px;
        width: 100%;
        max-width: 500px;
        max-height: 90vh;
        overflow: auto;
        box-shadow: var(--shadow-lg);
      }

      .modal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 20px;
        border-bottom: 1px solid var(--hd-border);
      }

      .modal-header h2 {
        font-size: 18px;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .close-btn {
        background: none;
        border: none;
        font-size: 24px;
        color: var(--hd-muted);
        cursor: pointer;
        padding: 0;
        line-height: 1;
      }

      .close-btn:hover {
        color: var(--hd-text);
      }

      .modal-body {
        padding: 20px;
      }

      /* Timeline */
      .timeline {
        margin-top: 12px;
      }

      .timeline-item {
        display: flex;
        gap: 12px;
        padding-bottom: 16px;
        position: relative;
      }

      .timeline-item:not(:last-child)::before {
        content: "";
        position: absolute;
        left: 5px;
        top: 16px;
        bottom: 0;
        width: 2px;
        background: var(--hd-border);
      }

      .timeline-marker {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: var(--hd-border);
        flex-shrink: 0;
        margin-top: 4px;
      }

      .timeline-item.current .timeline-marker {
        background: var(--hd-accent);
      }

      .timeline-content {
        flex: 1;
      }

      .timeline-date {
        font-size: 12px;
        color: var(--hd-muted);
      }

      .timeline-desc {
        font-size: 14px;
        font-weight: 500;
        margin: 2px 0;
      }

      .timeline-location {
        font-size: 12px;
        color: var(--hd-muted);
      }

      /* Detail Row */
      .detail-row {
        display: flex;
        justify-content: space-between;
        padding: 8px 0;
        border-bottom: 1px solid var(--hd-border);
      }

      .detail-label {
        color: var(--hd-muted);
      }

      .detail-value.status-out-for-delivery {
        color: var(--hd-warning);
      }

      .detail-value.status-delivered {
        color: var(--hd-success);
      }

      /* Mail View */
      .mail-view {
        max-width: 600px;
      }

      .info-box {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: 8px;
        padding: 16px;
        color: var(--hd-muted);
      }

      .mail-summary {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: 12px;
        padding: 24px;
        text-align: center;
      }

      .mail-count .count {
        font-size: 48px;
        font-weight: 700;
        color: var(--hd-accent);
        display: block;
      }

      .mail-count .label {
        color: var(--hd-muted);
      }

      .mail-preview {
        margin: 20px 0;
      }

      .mail-preview img {
        max-width: 100%;
        border-radius: 8px;
        border: 1px solid var(--hd-border);
      }

      /* Settings View */
      .settings-view {
        max-width: 700px;
      }

      .settings-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      }

      .settings-header h2 {
        margin: 0;
      }

      .save-status {
        font-size: 13px;
        color: var(--hd-muted);
      }

      .save-status.saving {
        color: var(--hd-warning);
      }

      .save-status.saved {
        color: var(--hd-success);
      }

      .save-status.error {
        color: var(--hd-danger);
      }

      .settings-tabs {
        display: flex;
        gap: 8px;
        margin-bottom: 24px;
        border-bottom: 1px solid var(--hd-border);
        padding-bottom: 12px;
      }

      .tab-btn {
        background: transparent;
        border: none;
        color: var(--hd-muted);
        padding: 8px 12px;
        cursor: pointer;
        font-size: 14px;
        border-radius: 6px;
      }

      .tab-btn:hover {
        color: var(--hd-text);
        background: var(--hd-hover);
      }

      .tab-btn.active {
        color: var(--hd-accent);
        font-weight: 600;
      }

      .settings-section {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
      }

      .settings-section h3 {
        margin: 0 0 16px 0;
        font-size: 16px;
      }

      .settings-section h4 {
        margin: 20px 0 12px 0;
        font-size: 14px;
        color: var(--hd-muted);
      }

      /* Forms */
      .form-group {
        margin-bottom: 16px;
      }

      .form-group label {
        display: block;
        margin-bottom: 6px;
        font-size: 14px;
        font-weight: 500;
      }

      .form-group input[type="text"],
      .form-group input[type="email"],
      .form-group input[type="password"],
      .form-group input[type="number"],
      .form-group input[type="time"],
      .form-group select {
        width: 100%;
        padding: 10px 12px;
        background: var(--hd-input-bg);
        border: 1px solid var(--hd-border);
        border-radius: 6px;
        color: var(--hd-text);
        font-size: 14px;
      }

      .form-group input:focus,
      .form-group select:focus {
        outline: none;
        border-color: var(--hd-accent);
        box-shadow: 0 0 0 2px var(--hd-accent-dim);
      }

      .form-row {
        display: flex;
        gap: 16px;
      }

      .form-row .form-group {
        flex: 1;
      }

      .hint {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: 4px;
      }

      .muted {
        color: var(--hd-muted);
      }

      .checkbox-label {
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
      }

      .checkbox-label input[type="checkbox"] {
        width: 18px;
        height: 18px;
        accent-color: var(--hd-accent);
      }

      .form-actions {
        display: flex;
        gap: 12px;
        justify-content: flex-end;
        margin-top: 20px;
        padding-top: 16px;
        border-top: 1px solid var(--hd-border);
      }

      /* Theme Toggle */
      .theme-toggle {
        display: flex;
        gap: 12px;
      }

      .theme-btn {
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        padding: 12px;
        background: var(--hd-hover);
        border: 2px solid var(--hd-border);
        border-radius: 8px;
        color: var(--hd-text);
        cursor: pointer;
        font-size: 14px;
        transition: all 0.15s;
      }

      .theme-btn:hover {
        border-color: var(--hd-border-strong);
      }

      .theme-btn.active {
        border-color: var(--hd-accent);
        background: var(--hd-accent-dim);
      }

      /* Toast */
      .toast {
        position: fixed;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%) translateY(100px);
        background: var(--hd-elevated);
        color: var(--hd-text);
        padding: 12px 24px;
        border-radius: 8px;
        box-shadow: var(--shadow-lg);
        z-index: 2000;
        opacity: 0;
        transition: all 0.3s;
      }

      .toast.show {
        transform: translateX(-50%) translateY(0);
        opacity: 1;
      }

      .toast.error {
        background: var(--hd-danger);
        color: #fff;
      }
    `;
  }
}

customElements.define("home-delivery-panel", HomeDeliveryPanel);
