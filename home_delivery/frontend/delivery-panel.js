/**
 * Home Delivery Panel - Vanilla JS for HA custom panel / standalone Ingress
 * Design aligned with home-weather: topbar + gear settings, dashboard layout.
 */
class HomeDeliveryPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._loading = false;
    this._error = null;
    this._currentView = "dashboard";
    this._settingsReturnView = "dashboard";
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
    this._refreshingAll = false;
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
  // Navigation (ported from home-weather)
  // ============================================================================

  _navigateTo(view) {
    if (view === "settings") {
      this._settingsReturnView = this._currentView || "dashboard";
    }
    if (this._currentView === "settings") {
      this._syncSettingsFromForm();
    }
    this._currentView = view;
    this._render();
  }

  _openSettingsPane(pane) {
    this._settingsPane = pane;
    this._navigateTo("settings");
  }

  // ============================================================================
  // API Communication
  // ============================================================================

  _getApiBase() {
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

  async _refreshAll() {
    this._refreshingAll = true;
    this._render();
    try {
      const [mailResp] = await Promise.all([
        this._fetchApi("/api/mail/refresh", { method: "POST" }).then(() => this._fetchApi("/api/mail")).catch(() => this._mailState),
        ...this._packages.filter(p => !p.delivered).map(p =>
          this._fetchApi(`/api/packages/${p.id}/refresh`, { method: "POST" })
            .then(resp => { if (resp.package) { const idx = this._packages.findIndex(x => x.id === p.id); if (idx >= 0) this._packages[idx] = resp.package; } })
            .catch(() => {})
        ),
      ]);
      if (mailResp) this._mailState = mailResp;
      this._showToast("Refreshed");
    } catch (err) {
      this._showToast(err.message, { error: true });
    } finally {
      this._refreshingAll = false;
      this._render();
    }
  }

  // ============================================================================
  // Theme System
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
        bg: "#f5f5f5",
        surface: "#ffffff",
        surface2: "#f0f0f0",
        elevated: "#ffffff",
        inputBg: "#ffffff",
        text: "#1a1a1a",
        muted: "#666666",
        disabled: "#999999",
        accent: "#03a9f4",
        accentHover: "#29b6f6",
        accentDim: "rgba(3, 169, 244, 0.12)",
        danger: "#f44336",
        warning: "#ff9800",
        success: "#4caf50",
        border: "#e0e0e0",
        borderStrong: "#bdbdbd",
        hover: "#eeeeee",
      };
    }
    return {
      bg: "#111111",
      surface: "#1c1c1c",
      surface2: "#161616",
      elevated: "#282828",
      inputBg: "#282828",
      text: "#e1e1e1",
      muted: "#9b9b9b",
      disabled: "#6e7681",
      accent: "#03a9f4",
      accentHover: "#29b6f6",
      accentDim: "rgba(3, 169, 244, 0.15)",
      danger: "#f44336",
      warning: "#ff9800",
      success: "#4caf50",
      border: "#252525",
      borderStrong: "#333333",
      hover: "#222222",
    };
  }

  _hexToRgba(hex, alpha) {
    const h = String(hex || "").replace("#", "");
    const full = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
    const int = parseInt(full, 16);
    if (Number.isNaN(int) || full.length !== 6) return `rgba(3, 169, 244, ${alpha})`;
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
          sm: "0 1px 2px rgba(0, 0, 0, 0.05)",
          base: "0 1px 3px rgba(0, 0, 0, 0.08)",
          md: "0 4px 12px rgba(0, 0, 0, 0.1)",
          lg: "0 12px 32px rgba(0, 0, 0, 0.12)",
        }
      : {
          sm: "0 1px 2px rgba(0, 0, 0, 0.4)",
          base: "0 2px 8px rgba(0, 0, 0, 0.4)",
          md: "0 4px 16px rgba(0, 0, 0, 0.4)",
          lg: "0 12px 40px rgba(0, 0, 0, 0.5)",
        };
    set("--shadow-sm", shadows.sm);
    set("--shadow", shadows.base);
    set("--shadow-md", shadows.md);
    set("--shadow-lg", shadows.lg);

    host.setAttribute("data-hd-theme", mode);
  }

  // ============================================================================
  // Auto-Save
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

  async _saveSettings({ silent = false } = {}) {
    if (this._autoSaveTimer) { clearTimeout(this._autoSaveTimer); this._autoSaveTimer = null; }
    this._syncSettingsFromForm();
    const snapshot = JSON.parse(JSON.stringify(this._settings));
    this._config = JSON.parse(JSON.stringify(snapshot));
    this._setSaveStatus("saving", "Saving\u2026");
    try {
      await this._saveConfig(snapshot);
      this._setSaveStatus("saved", "Saved");
      if (!silent) this._showToast("Saved");
      setTimeout(() => this._setSaveStatus("idle", ""), 2500);
    } catch (e) {
      console.error("Save failed:", e);
      this._setSaveStatus("error", "Save failed");
      this._showToast("Save failed", { error: true });
    }
  }

  _setSaveStatus(status, text) {
    this._saveStatus = status;
    this._saveStatusText = text;
    const el = this.shadowRoot?.querySelector("#settings-save-status");
    if (el) {
      el.textContent = text;
      el.dataset.state = status;
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

    const defaultInterval = s.querySelector("#polling-default");
    const ofdInterval = s.querySelector("#polling-ofd");

    if (!this._settings.polling) this._settings.polling = {};
    if (defaultInterval) this._settings.polling.default_interval_seconds = parseInt(defaultInterval.value) || 3600;
    if (ofdInterval) this._settings.polling.out_for_delivery_interval_seconds = parseInt(ofdInterval.value) || 300;
  }

  // ============================================================================
  // Helpers
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

  _esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ============================================================================
  // Render
  // ============================================================================

  _render() {
    const s = this.shadowRoot;
    if (!s) return;
    this._applyTheme();

    if (this._currentView === "settings") {
      s.innerHTML = `
        <style>${this._getStyles()}</style>
        <div class="settings-view ${this._isNarrow ? "narrow" : ""}">
          ${this._renderSettingsMenubar()}
          <div class="settings-body">
            ${this._renderSettingsContent()}
          </div>
        </div>
        ${this._addPackageModal ? this._renderAddPackageModal() : ""}
      `;
      this._bindEvents();
      this._attachSettingsHandlers();
      return;
    }

    s.innerHTML = `
      <style>${this._getStyles()}</style>
      <div class="hud-wrapper">
        <div class="delivery-app">
          <header class="topbar">
            ${this._isNarrow ? `<button class="hamburger icon-btn" id="hamburger-btn" aria-label="Menu">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
            </button>` : ""}
            <section class="title-card">
              <div class="title-wrap">
                <div class="title">Home Delivery</div>
              </div>
            </section>
            <button class="icon-btn" id="refresh-btn" aria-label="Refresh" ${this._refreshingAll ? "disabled" : ""}>
              <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" class="${this._refreshingAll ? "spinning" : ""}"><path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
            </button>
            <button class="icon-btn" id="gear-btn" aria-label="Settings">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.488.488 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
            </button>
          </header>
          <div class="content-area">
            ${this._loading ? this._renderLoading() : ""}
            ${this._error && !this._loading ? this._renderError() : ""}
            ${!this._loading && !this._error ? this._renderDashboard() : ""}
          </div>
        </div>
      </div>
      ${this._addPackageModal ? this._renderAddPackageModal() : ""}
      ${this._selectedPackage ? this._renderPackageDetail() : ""}
    `;

    this._bindEvents();
  }

  _renderLoading() {
    return `
      <section class="dashboard">
        <article class="glass card dashboard-message">
          <div class="loading">
            <div class="spinner"></div>
            <p>Loading...</p>
          </div>
        </article>
      </section>
    `;
  }

  _renderError() {
    return `
      <section class="dashboard">
        <article class="glass card dashboard-message">
          <div class="error">
            <p>${this._esc(this._error)}</p>
            <button class="btn btn-primary" data-action="retry">Retry</button>
          </div>
        </article>
      </section>
    `;
  }

  _renderDashboard() {
    return `
      <section class="dashboard">
        ${this._renderMailHero()}
        ${this._renderPackagesSection()}
        ${this._renderDeliveredSection()}
      </section>
    `;
  }

  _renderMailHero() {
    const mail = this._mailState || {};
    const configured = mail.configured;
    const enabled = mail.enabled;

    if (!configured) {
      return `
        <article class="glass card mail-hero-card">
          <div class="mail-hero-message">
            <svg viewBox="0 0 24 24" width="48" height="48" fill="currentColor" style="opacity:0.3"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
            <p>Mail tracking is not configured</p>
            <button class="btn btn-primary" data-action="configure-mail">Configure IMAP</button>
          </div>
        </article>
      `;
    }

    if (!enabled) {
      return `
        <article class="glass card mail-hero-card">
          <div class="mail-hero-message">
            <svg viewBox="0 0 24 24" width="48" height="48" fill="currentColor" style="opacity:0.3"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
            <p>Mail tracking is disabled</p>
            <button class="btn btn-primary" data-action="configure-mail">Enable in Settings</button>
          </div>
        </article>
      `;
    }

    const lastCheckStr = mail.last_check ? new Date(mail.last_check).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";

    return `
      <article class="glass card mail-hero-card">
        <div class="card-head">
          <div>
            <div class="card-title">Mail Today</div>
            <div class="card-sub">USPS Informed Delivery</div>
          </div>
          <button class="btn btn-sm" data-action="refresh-mail">Check Now</button>
        </div>
        <div class="mail-hero-body">
          <div class="mail-hero-main">
            <div class="mail-count-large">${mail.piece_count || 0}</div>
            <div class="mail-count-label">pieces arriving</div>
          </div>
          ${mail.gif_url ? `
            <div class="mail-preview">
              <img src="${this._getApiBase()}${mail.gif_url}" alt="Mail Preview" />
            </div>
          ` : ""}
        </div>
        ${lastCheckStr ? `<div class="mail-meta">Last checked: ${lastCheckStr}</div>` : ""}
      </article>
    `;
  }

  _renderPackagesSection() {
    const active = this._packages.filter(p => !p.delivered);

    return `
      <article class="glass card packages-card">
        <div class="card-head">
          <div>
            <div class="card-title">Active Packages</div>
            <div class="card-sub">${active.length} tracking</div>
          </div>
          <button class="btn btn-primary btn-sm" data-action="add-package">+ Add Package</button>
        </div>
        <div class="packages-body">
          ${active.length === 0 ? `
            <div class="empty-state">
              <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor" style="opacity:0.3"><path d="M20 8h-3V6c0-1.1-.9-2-2-2H9c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v10h20V10c0-1.1-.9-2-2-2zM9 6h6v2H9V6zm11 12H4v-3h16v3z"/></svg>
              <p>No packages being tracked</p>
              <p class="muted">Click "+ Add Package" to start</p>
            </div>
          ` : `
            <div class="package-grid">
              ${active.map(p => this._renderPackageCard(p)).join("")}
            </div>
          `}
        </div>
      </article>
    `;
  }

  _renderDeliveredSection() {
    const delivered = this._packages.filter(p => p.delivered);
    if (delivered.length === 0) return "";

    return `
      <article class="glass card delivered-card">
        <div class="card-head">
          <div>
            <div class="card-title">Delivered</div>
            <div class="card-sub">${delivered.length} packages</div>
          </div>
        </div>
        <div class="packages-body">
          <div class="package-grid delivered">
            ${delivered.map(p => this._renderPackageCard(p)).join("")}
          </div>
        </div>
      </article>
    `;
  }

  _renderPackageCard(pkg) {
    const isRefreshing = this._refreshingPackage === pkg.id;
    return `
      <div class="package-card ${this._statusClass(pkg)}" data-package-id="${pkg.id}">
        <div class="package-header">
          ${this._carrierBadge(pkg.carrier)}
          <span class="package-status">${this._esc(pkg.status || "Pending")}</span>
        </div>
        <div class="package-tracking">${this._esc(pkg.tracking_number)}</div>
        ${pkg.recipient ? `<div class="package-meta">For: ${this._esc(pkg.recipient)}</div>` : ""}
        ${pkg.destination ? `<div class="package-meta">To: ${this._esc(pkg.destination)}</div>` : ""}
        ${pkg.status_detail ? `<div class="package-detail-text">${this._esc(pkg.status_detail)}</div>` : ""}
        ${pkg.events?.length > 0 ? `
          <div class="package-latest">
            <span class="event-date">${this._esc(pkg.events[0].date || "")}</span>
            <span class="event-location">${this._esc(pkg.events[0].location || "")}</span>
          </div>
        ` : ""}
        ${pkg.error ? `<div class="package-error">${this._esc(pkg.error)}</div>` : ""}
        <div class="package-actions">
          <button class="btn btn-sm" data-action="refresh" data-id="${pkg.id}" ${isRefreshing ? "disabled" : ""}>
            ${isRefreshing ? "..." : "Refresh"}
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
            <h2>${this._carrierBadge(pkg.carrier)} ${this._esc(pkg.tracking_number)}</h2>
            <button class="close-btn" data-action="close-detail">&times;</button>
          </div>
          <div class="modal-body">
            <div class="detail-row">
              <span class="detail-label">Status</span>
              <span class="detail-value status-${this._statusClass(pkg)}">${this._esc(pkg.status || "Pending")}</span>
            </div>
            ${pkg.recipient ? `
              <div class="detail-row">
                <span class="detail-label">Recipient</span>
                <span class="detail-value">${this._esc(pkg.recipient)}</span>
              </div>
            ` : ""}
            ${pkg.destination ? `
              <div class="detail-row">
                <span class="detail-label">Destination</span>
                <span class="detail-value">${this._esc(pkg.destination)}</span>
              </div>
            ` : ""}
            <h3 style="margin-top:20px">Tracking History</h3>
            ${pkg.events?.length > 0 ? `
              <div class="timeline">
                ${pkg.events.map((e, i) => `
                  <div class="timeline-item ${i === 0 ? "current" : ""}">
                    <div class="timeline-marker"></div>
                    <div class="timeline-content">
                      <div class="timeline-date">${this._esc(e.date || "")} ${this._esc(e.time || "")}</div>
                      <div class="timeline-desc">${this._esc(e.description || "")}</div>
                      <div class="timeline-location">${this._esc(e.location || "")}</div>
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

  _renderSettingsMenubar() {
    const backLabel = "Back";
    return `
      <header class="hd-menubar">
        <button type="button" class="hd-menubar-back" data-action="nav-back" aria-label="${backLabel}">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
          <span>${backLabel}</span>
        </button>
        <div class="hd-menubar-title">Settings</div>
        <span class="settings-save-status" id="settings-save-status" role="status" aria-live="polite" data-state="${this._saveStatus}">${this._saveStatusText}</span>
      </header>
    `;
  }

  _renderSettingsContent() {
    const mail = this._settings.mail || {};
    const tts = this._settings.tts || {};
    const polling = this._settings.polling || {};
    const { mode } = this._getAppearance();
    const activePane = this._settingsPane || "general";

    const paneNav = [
      { id: "general", label: "General", icon: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3l9 7h-3v9h-4v-6H10v6H6v-9H3l9-7z"/></svg>` },
      { id: "mail", label: "Mail", icon: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>` },
      { id: "announcements", label: "Announcements", icon: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 00-2.5-4.03v8.05A4.5 4.5 0 0016.5 12zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>` },
      { id: "appearance", label: "Appearance", icon: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3a9 9 0 000 18c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.39-.61-.39-.99 0-.83.67-1.5 1.5-1.5H16a5 5 0 005-5c0-4.42-4.03-8-9-8zm-5.5 9a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm3-4a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm5 0a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm3.5 4a1.5 1.5 0 110-3 1.5 1.5 0 010 3z"/></svg>` },
    ];

    return `
      <div class="settings-form">
        <div class="settings-shell">
          <nav class="settings-sidenav" aria-label="Settings sections">
            ${paneNav.map(p => `<button type="button" class="${activePane === p.id ? "active" : ""}" data-settings-pane="${p.id}">${p.icon}<span>${p.label}</span></button>`).join("")}
          </nav>
          <div class="settings-content">
            <section class="settings-pane ${activePane === "general" ? "active" : ""}" data-settings-pane="general">
              <div class="settings-pane-head">
                <div class="settings-pane-title">General</div>
                <div class="settings-pane-sub">Polling intervals for package tracking</div>
              </div>
              <div class="settings-card">
                <div class="settings-card-body">
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
                </div>
              </div>
            </section>

            <section class="settings-pane ${activePane === "mail" ? "active" : ""}" data-settings-pane="mail">
              <div class="settings-pane-head">
                <div class="settings-pane-title">Mail</div>
                <div class="settings-pane-sub">USPS Informed Delivery via IMAP</div>
              </div>
              <div class="settings-card">
                <div class="settings-card-body">
                  <div class="form-group">
                    <label class="checkbox-label">
                      <input type="checkbox" id="mail-enabled" ${mail.enabled ? "checked" : ""} />
                      Enable Mail Tracking
                    </label>
                  </div>
                  <div class="form-group">
                    <label for="imap-host">IMAP Server</label>
                    <input type="text" id="imap-host" value="${this._esc(mail.imap_host || "")}" placeholder="imap.gmail.com" />
                  </div>
                  <div class="form-group">
                    <label for="imap-port">IMAP Port</label>
                    <input type="number" id="imap-port" value="${mail.imap_port || 993}" />
                  </div>
                  <div class="form-group">
                    <label for="imap-user">Email Address</label>
                    <input type="email" id="imap-user" value="${this._esc(mail.imap_user || "")}" placeholder="you@example.com" />
                  </div>
                  <div class="form-group">
                    <label for="imap-password">Password / App Password</label>
                    <input type="password" id="imap-password" value="${mail.imap_password ? "********" : ""}" placeholder="App password for Gmail" />
                  </div>
                  <div class="form-group">
                    <label for="imap-folder">Folder</label>
                    <input type="text" id="imap-folder" value="${this._esc(mail.folder || "INBOX")}" />
                  </div>
                </div>
              </div>
            </section>

            <section class="settings-pane ${activePane === "announcements" ? "active" : ""}" data-settings-pane="announcements">
              <div class="settings-pane-head">
                <div class="settings-pane-title">Announcements</div>
                <div class="settings-pane-sub">Text-to-Speech notifications</div>
              </div>
              <div class="settings-card">
                <div class="settings-card-body">
                  <div class="form-group">
                    <label class="checkbox-label">
                      <input type="checkbox" id="tts-enabled" ${tts.enabled ? "checked" : ""} />
                      Enable TTS Announcements
                    </label>
                  </div>
                  <h4>Triggers</h4>
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
                </div>
              </div>
            </section>

            <section class="settings-pane ${activePane === "appearance" ? "active" : ""}" data-settings-pane="appearance">
              <div class="settings-pane-head">
                <div class="settings-pane-title">Appearance</div>
                <div class="settings-pane-sub">Theme customization</div>
              </div>
              <div class="settings-card">
                <div class="settings-card-body">
                  <h4>Theme</h4>
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
                </div>
              </div>
            </section>
          </div>
        </div>
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
              <input type="text" id="destination" placeholder="e.g., Home, Work" />
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

    const gearBtn = s.querySelector("#gear-btn");
    if (gearBtn) gearBtn.addEventListener("click", () => this._navigateTo("settings"));

    const refreshBtn = s.querySelector("#refresh-btn");
    if (refreshBtn) refreshBtn.addEventListener("click", () => this._refreshAll());

    const hamburgerBtn = s.querySelector("#hamburger-btn");
    if (hamburgerBtn) {
      hamburgerBtn.addEventListener("click", () => {
        this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
      });
    }

    s.querySelectorAll(".theme-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        if (!this._settings.appearance) this._settings.appearance = {};
        this._settings.appearance.mode = btn.dataset.theme;
        this._applyTheme();
        this._persistAppearance();
        s.querySelectorAll(".theme-btn").forEach(b => b.classList.toggle("active", b === btn));
      });
    });

    s.querySelectorAll("[data-action]").forEach(el => {
      el.addEventListener("click", (e) => this._handleAction(e, el.dataset.action, el.dataset));
    });

    const form = s.querySelector(".settings-form");
    if (form) {
      const onChange = () => this._scheduleAutoSave();
      form.addEventListener("input", onChange);
      form.addEventListener("change", onChange);
    }

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

  _attachSettingsHandlers() {
    const s = this.shadowRoot;
    if (!s) return;

    s.querySelectorAll(".settings-sidenav button[data-settings-pane]").forEach(btn => {
      btn.addEventListener("click", () => {
        const pane = btn.dataset.settingsPane;
        this._settingsPane = pane;
        s.querySelectorAll(".settings-sidenav button[data-settings-pane]")
          .forEach(b => b.classList.toggle("active", b === btn));
        s.querySelectorAll(".settings-pane")
          .forEach(p => p.classList.toggle("active", p.dataset.settingsPane === pane));
      });
    });
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
      case "configure-mail":
        this._openSettingsPane("mail");
        break;
      case "nav-back":
        this._navigateTo(this._settingsReturnView || "dashboard");
        break;
      case "retry":
        this._loadConfig();
        break;
    }
  }

  // ============================================================================
  // Styles
  // ============================================================================

  _getStyles() {
    return `
      :host {
        --header-height: 64px;
        --space-1: 4px;
        --space-2: 8px;
        --space-3: 12px;
        --space-4: 16px;
        --space-5: 24px;
        --space-6: 32px;
        --radius-sm: 6px;
        --radius-md: 8px;
        --radius-lg: 12px;
        --radius-xl: 16px;
        --dur-fast: 0.15s;
        --dur-normal: 0.25s;
        --ease: cubic-bezier(0.4, 0, 0.2, 1);

        display: block;
        min-height: 100vh;
        background: var(--hd-bg);
        color: var(--hd-text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.5;
      }

      * { box-sizing: border-box; }

      /* ======================== HUD Wrapper + App Shell ======================== */

      .hud-wrapper {
        min-height: 100vh;
        display: flex;
        flex-direction: column;
      }

      .delivery-app {
        flex: 1;
        display: flex;
        flex-direction: column;
        max-width: 1600px;
        width: 100%;
        margin: 0 auto;
      }

      /* ======================== Topbar ======================== */

      .topbar {
        position: sticky;
        top: 0;
        z-index: 100;
        display: flex;
        align-items: center;
        height: var(--header-height);
        padding: 0 var(--space-4);
        background: var(--hd-surface);
        border-bottom: 1px solid var(--hd-border-strong);
        gap: var(--space-2);
      }

      .title-card {
        flex: 1;
        min-width: 0;
      }

      .title-wrap {
        display: flex;
        align-items: center;
        gap: var(--space-2);
      }

      .title {
        font-size: clamp(16px, 2vw, 20px);
        font-weight: 600;
        color: var(--hd-accent);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .icon-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 40px;
        border: 1px solid var(--hd-border-strong);
        background: var(--hd-input-bg);
        border-radius: var(--radius-sm);
        color: var(--hd-text);
        cursor: pointer;
        transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
        flex-shrink: 0;
      }

      .icon-btn:hover {
        background: var(--hd-hover);
        color: var(--hd-accent-hover);
      }

      .icon-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      .icon-btn svg.spinning {
        animation: spin 1s linear infinite;
      }

      @keyframes spin {
        to { transform: rotate(360deg); }
      }

      .hamburger {
        display: none;
      }

      @media (max-width: 768px) {
        .hamburger { display: flex; }
      }

      /* ======================== Content Area ======================== */

      .content-area {
        flex: 1;
        padding: var(--space-4);
        padding-bottom: calc(var(--space-5) + env(safe-area-inset-bottom, 0px));
      }

      .dashboard {
        display: flex;
        flex-direction: column;
        gap: var(--space-4);
      }

      /* ======================== Glass Cards ======================== */

      .glass {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border-strong);
        border-radius: var(--radius-xl);
        box-shadow: var(--shadow-md);
      }

      .card {
        padding: var(--space-4);
      }

      .card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--space-3);
        margin-bottom: var(--space-4);
      }

      .card-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--hd-text);
      }

      .card-sub {
        font-size: 13px;
        color: var(--hd-muted);
        margin-top: 2px;
      }

      .dashboard-message {
        padding: var(--space-6);
        text-align: center;
      }

      /* ======================== Mail Hero ======================== */

      .mail-hero-card {
        min-height: 160px;
      }

      .mail-hero-message {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: var(--space-3);
        padding: var(--space-5) 0;
        text-align: center;
      }

      .mail-hero-message p {
        color: var(--hd-muted);
        margin: 0;
      }

      .mail-hero-body {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--space-5);
      }

      .mail-hero-main {
        flex: 1;
        min-width: 140px;
      }

      .mail-count-large {
        font-size: clamp(48px, 12vw, 72px);
        font-weight: 700;
        color: var(--hd-accent);
        line-height: 1;
      }

      .mail-count-label {
        font-size: 14px;
        color: var(--hd-muted);
        margin-top: var(--space-1);
      }

      .mail-preview {
        flex: 1;
        min-width: 200px;
        max-width: 400px;
      }

      .mail-preview img {
        width: 100%;
        border-radius: var(--radius-md);
        border: 1px solid var(--hd-border);
      }

      .mail-meta {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: var(--space-3);
      }

      /* ======================== Packages ======================== */

      .packages-body {
        min-height: 80px;
      }

      .package-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: var(--space-3);
      }

      .package-grid.delivered {
        opacity: 0.7;
      }

      .package-card {
        background: var(--hd-elevated);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-lg);
        padding: var(--space-3);
        transition: border-color var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease);
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
        gap: var(--space-2);
        margin-bottom: var(--space-2);
      }

      .carrier-badge {
        padding: 3px 6px;
        border-radius: 4px;
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.02em;
      }

      .package-status {
        font-size: 13px;
        font-weight: 600;
      }

      .package-tracking {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
        font-size: 12px;
        color: var(--hd-muted);
        margin-bottom: var(--space-2);
        word-break: break-all;
      }

      .package-meta {
        font-size: 12px;
        color: var(--hd-muted);
      }

      .package-detail-text {
        font-size: 12px;
        margin-top: var(--space-2);
      }

      .package-latest {
        font-size: 11px;
        color: var(--hd-muted);
        margin-top: var(--space-2);
        padding-top: var(--space-2);
        border-top: 1px solid var(--hd-border);
      }

      .package-error {
        font-size: 11px;
        color: var(--hd-danger);
        margin-top: var(--space-2);
      }

      .package-actions {
        display: flex;
        gap: var(--space-2);
        margin-top: var(--space-3);
        padding-top: var(--space-3);
        border-top: 1px solid var(--hd-border);
        flex-wrap: wrap;
      }

      .empty-state {
        text-align: center;
        padding: var(--space-5);
        color: var(--hd-muted);
      }

      .empty-state p {
        margin: var(--space-2) 0 0;
      }

      .empty-state .muted {
        font-size: 13px;
      }

      /* ======================== Buttons ======================== */

      .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: var(--space-2);
        padding: 10px 16px;
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-sm);
        color: var(--hd-text);
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
        transition: all var(--dur-fast) var(--ease);
      }

      .btn:hover {
        background: var(--hd-hover);
        border-color: var(--hd-border-strong);
      }

      .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      .btn-primary {
        background: var(--hd-accent);
        border-color: var(--hd-accent);
        color: #fff;
      }

      .btn-primary:hover {
        background: var(--hd-accent-hover);
        border-color: var(--hd-accent-hover);
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
        padding: 6px 10px;
        font-size: 12px;
      }

      /* ======================== Modal ======================== */

      .modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.6);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1000;
        padding: var(--space-4);
      }

      .modal {
        background: var(--hd-surface);
        border-radius: var(--radius-lg);
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
        padding: var(--space-4);
        border-bottom: 1px solid var(--hd-border);
      }

      .modal-header h2 {
        font-size: 18px;
        margin: 0;
        display: flex;
        align-items: center;
        gap: var(--space-2);
      }

      .close-btn {
        background: none;
        border: none;
        font-size: 24px;
        color: var(--hd-muted);
        cursor: pointer;
        line-height: 1;
        padding: 0;
      }

      .close-btn:hover {
        color: var(--hd-text);
      }

      .modal-body {
        padding: var(--space-4);
      }

      /* ======================== Timeline ======================== */

      .timeline {
        margin-top: var(--space-3);
      }

      .timeline-item {
        display: flex;
        gap: var(--space-3);
        padding-bottom: var(--space-4);
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

      .detail-row {
        display: flex;
        justify-content: space-between;
        padding: var(--space-2) 0;
        border-bottom: 1px solid var(--hd-border);
      }

      .detail-label {
        color: var(--hd-muted);
      }

      .detail-value.status-out-for-delivery { color: var(--hd-warning); }
      .detail-value.status-delivered { color: var(--hd-success); }

      /* ======================== Settings View ======================== */

      .settings-view {
        min-height: 100vh;
        background: var(--hd-bg);
      }

      .hd-menubar {
        position: sticky;
        top: 0;
        z-index: 100;
        display: flex;
        align-items: center;
        height: var(--header-height);
        padding: 0 var(--space-4);
        background: var(--hd-surface);
        border-bottom: 1px solid var(--hd-border-strong);
        gap: var(--space-3);
      }

      .hd-menubar-back {
        display: flex;
        align-items: center;
        gap: var(--space-2);
        padding: var(--space-2) var(--space-3);
        background: transparent;
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-sm);
        color: var(--hd-text);
        font-size: 14px;
        cursor: pointer;
        transition: all var(--dur-fast) var(--ease);
      }

      .hd-menubar-back:hover {
        background: var(--hd-hover);
        border-color: var(--hd-border-strong);
      }

      .hd-menubar-title {
        font-size: 16px;
        font-weight: 600;
        flex: 1;
      }

      .settings-save-status {
        font-size: 13px;
        color: var(--hd-muted);
        display: flex;
        align-items: center;
        gap: var(--space-2);
      }

      .settings-save-status::before {
        content: "";
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--hd-muted);
        display: none;
      }

      .settings-save-status[data-state="idle"] { display: none; }
      .settings-save-status[data-state="saving"] { color: var(--hd-warning); }
      .settings-save-status[data-state="saving"]::before { display: block; background: var(--hd-warning); animation: pulse 1s ease infinite; }
      .settings-save-status[data-state="saved"] { color: var(--hd-success); }
      .settings-save-status[data-state="saved"]::before { display: block; background: var(--hd-success); }
      .settings-save-status[data-state="error"] { color: var(--hd-danger); }
      .settings-save-status[data-state="error"]::before { display: block; background: var(--hd-danger); }

      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
      }

      .settings-body {
        padding: var(--space-4);
        max-width: 1200px;
        margin: 0 auto;
      }

      .settings-form {
        display: flex;
        flex-direction: column;
        gap: var(--space-4);
      }

      .settings-shell {
        display: grid;
        grid-template-columns: 200px minmax(0, 1fr);
        gap: var(--space-5);
      }

      @media (max-width: 900px) {
        .settings-shell {
          grid-template-columns: 1fr;
        }

        .settings-sidenav {
          flex-direction: row !important;
          overflow-x: auto;
          padding: var(--space-2) !important;
          gap: var(--space-2) !important;
        }

        .settings-sidenav button {
          flex-direction: row !important;
          white-space: nowrap;
          padding: var(--space-2) var(--space-3) !important;
        }

        .settings-sidenav button span {
          display: inline !important;
        }
      }

      .settings-sidenav {
        position: sticky;
        top: calc(var(--header-height) + var(--space-4));
        display: flex;
        flex-direction: column;
        gap: var(--space-1);
        padding: var(--space-3);
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-lg);
        align-self: start;
      }

      .settings-sidenav button {
        display: flex;
        align-items: center;
        gap: var(--space-3);
        padding: var(--space-3);
        background: transparent;
        border: none;
        border-radius: var(--radius-sm);
        color: var(--hd-muted);
        font-size: 14px;
        cursor: pointer;
        text-align: left;
        transition: all var(--dur-fast) var(--ease);
      }

      .settings-sidenav button:hover {
        background: var(--hd-hover);
        color: var(--hd-text);
      }

      .settings-sidenav button.active {
        background: var(--hd-accent-dim);
        color: var(--hd-accent-hover);
        font-weight: 600;
      }

      .settings-sidenav button svg {
        width: 20px;
        height: 20px;
        flex-shrink: 0;
      }

      .settings-content {
        min-width: 0;
      }

      .settings-pane {
        display: none;
        flex-direction: column;
        gap: var(--space-4);
      }

      .settings-pane.active {
        display: flex;
      }

      .settings-pane-head {
        margin-bottom: var(--space-3);
      }

      .settings-pane-title {
        font-size: 20px;
        font-weight: 600;
      }

      .settings-pane-sub {
        font-size: 14px;
        color: var(--hd-muted);
        margin-top: var(--space-1);
      }

      .settings-card {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-lg);
        overflow: hidden;
      }

      .settings-card-body {
        padding: var(--space-4);
      }

      .settings-card-body h4 {
        font-size: 13px;
        font-weight: 600;
        color: var(--hd-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin: var(--space-4) 0 var(--space-3);
        padding-top: var(--space-4);
        border-top: 1px solid var(--hd-border);
      }

      .settings-card-body h4:first-child {
        margin-top: 0;
        padding-top: 0;
        border-top: none;
      }

      /* ======================== Forms ======================== */

      .form-group {
        margin-bottom: var(--space-4);
      }

      .form-group:last-child {
        margin-bottom: 0;
      }

      .form-group label {
        display: block;
        margin-bottom: var(--space-2);
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
        border-radius: var(--radius-sm);
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
        gap: var(--space-4);
      }

      .form-row .form-group {
        flex: 1;
      }

      .hint {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: var(--space-1);
      }

      .muted {
        color: var(--hd-muted);
      }

      .checkbox-label {
        display: flex;
        align-items: center;
        gap: var(--space-2);
        cursor: pointer;
      }

      .checkbox-label input[type="checkbox"] {
        width: 18px;
        height: 18px;
        accent-color: var(--hd-accent);
      }

      .form-actions {
        display: flex;
        gap: var(--space-3);
        justify-content: flex-end;
        margin-top: var(--space-4);
        padding-top: var(--space-4);
        border-top: 1px solid var(--hd-border);
      }

      /* ======================== Theme Toggle ======================== */

      .theme-toggle {
        display: flex;
        gap: var(--space-3);
      }

      .theme-btn {
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: var(--space-2);
        padding: var(--space-3);
        background: var(--hd-hover);
        border: 2px solid var(--hd-border);
        border-radius: var(--radius-md);
        color: var(--hd-text);
        cursor: pointer;
        font-size: 14px;
        transition: all var(--dur-fast) var(--ease);
      }

      .theme-btn:hover {
        border-color: var(--hd-border-strong);
      }

      .theme-btn.active {
        border-color: var(--hd-accent);
        background: var(--hd-accent-dim);
      }

      /* ======================== Toast ======================== */

      .toast {
        position: fixed;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%) translateY(100px);
        background: var(--hd-elevated);
        color: var(--hd-text);
        padding: 12px 24px;
        border-radius: var(--radius-md);
        box-shadow: var(--shadow-lg);
        z-index: 2000;
        opacity: 0;
        transition: all 0.3s var(--ease);
      }

      .toast.show {
        transform: translateX(-50%) translateY(0);
        opacity: 1;
      }

      .toast.error {
        background: var(--hd-danger);
        color: #fff;
      }

      /* ======================== Loading ======================== */

      .loading {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: var(--space-3);
      }

      .spinner {
        width: 40px;
        height: 40px;
        border: 3px solid var(--hd-border);
        border-top-color: var(--hd-accent);
        border-radius: 50%;
        animation: spin 1s linear infinite;
      }

      .error {
        text-align: center;
      }

      .error p {
        color: var(--hd-danger);
        margin-bottom: var(--space-4);
      }
    `;
  }
}

customElements.define("home-delivery-panel", HomeDeliveryPanel);
