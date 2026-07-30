/**
 * Home Delivery Panel - Vanilla JS for HA custom panel / standalone Ingress
 * Design aligned with home-weather: topbar + gear settings, dashboard layout.
 */
const PANEL_VERSION = "0.0.7";

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
    this._addPackageWizard = null;
    this._selectedPackage = null;
    this._refreshingPackage = null;
    this._refreshingAll = false;
    this._mailAccountModal = null;
    this._mailSyncing = false;
    this._mailSyncAccountId = null;
    this._carouselTimers = [];
    this._dashboardSettled = false;
  }

  get _isNarrow() {
    return this._narrow ?? this._mediaQuery?.matches ?? false;
  }

  connectedCallback() {
    this._mediaQuery = window.matchMedia("(max-width: 768px)");
    this._onMediaChange = () => {
      this._syncHaLayoutVars();
      this._render();
    };
    this._mediaQuery.addEventListener("change", this._onMediaChange);
    this._syncHaLayoutVars();
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
    const path = window.location.pathname || "";
    const match = path.match(/^(\/api\/(?:hassio_ingress|ingress)\/[^/]+)/);
    return match ? match[1] : "";
  }

  _apiUrl(path) {
    const normalized = path.startsWith("/") ? path : `/${path}`;
    const base = this._getApiBase();
    return base ? `${base}${normalized}` : normalized;
  }

  _isHomeMailLabel(label) {
    const normalized = String(label || "").trim().toLowerCase();
    return normalized === "home"
      || normalized.startsWith("home,")
      || normalized.startsWith("home ");
  }

  _getHomeMailAccount(accounts = []) {
    const list = Array.isArray(accounts) ? accounts : [];
    const enabled = list.filter((account) => account.enabled !== false);
    const candidates = enabled.length ? enabled : list;
    return candidates.find((account) => this._isHomeMailLabel(account.label))
      || candidates[0]
      || null;
  }

  _getOtherMailAccounts(accounts = []) {
    const list = Array.isArray(accounts) ? accounts : [];
    const home = this._getHomeMailAccount(list);
    return list.filter((account) => account.enabled !== false && account.id !== home?.id);
  }

  _mailPreviewHtml(account, label = "Mail preview") {
    const previewImages = account?.preview_images || [];
    if (previewImages.length > 0) {
      return this._mailCarouselHtml(previewImages, label);
    }

    const gifUrl = account?.gif_url;
    if (!gifUrl) {
      return `
        <div class="mail-preview mail-preview--placeholder" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="56" height="56" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
        </div>
      `;
    }

    const src = this._apiUrl(gifUrl);
    return `
      <div class="mail-preview">
        <img src="${this._esc(src)}" alt="${this._esc(label)}" loading="lazy"
          onerror="this.closest('.mail-preview')?.classList.add('mail-preview--broken'); this.remove();" />
      </div>
    `;
  }

  _mailCarouselHtml(images, label) {
    const slides = images.map((image, index) => `
      <figure class="mail-carousel-slide ${index === 0 ? "active" : ""}" data-index="${index}">
        <img src="${this._esc(this._apiUrl(image.url))}" alt="${this._esc(label)} ${index + 1} of ${images.length}"
          loading="${index === 0 ? "eager" : "lazy"}"
          onerror="this.closest('.mail-carousel')?.classList.add('mail-carousel--broken');" />
      </figure>
    `).join("");

    return `
      <div class="mail-carousel" data-slide-count="${images.length}" aria-label="${this._esc(label)}">
        <div class="mail-carousel-viewport">${slides}</div>
        ${images.length > 1 ? `
          <div class="mail-carousel-dots" aria-hidden="true">
            ${images.map((_, index) => `<span class="mail-carousel-dot ${index === 0 ? "active" : ""}"></span>`).join("")}
          </div>
        ` : ""}
      </div>
    `;
  }

  _initMailCarousels() {
    const root = this.shadowRoot;
    if (!root) return;

    this._carouselTimers.forEach((timer) => clearInterval(timer));
    this._carouselTimers = [];

    root.querySelectorAll(".mail-carousel[data-slide-count]").forEach((carousel) => {
      const count = Number(carousel.dataset.slideCount || "0");
      if (count <= 1) return;

      const slides = carousel.querySelectorAll(".mail-carousel-slide");
      const dots = carousel.querySelectorAll(".mail-carousel-dot");
      let active = 0;

      const show = (index) => {
        active = index;
        slides.forEach((slide, slideIndex) => {
          slide.classList.toggle("active", slideIndex === index);
        });
        dots.forEach((dot, dotIndex) => {
          dot.classList.toggle("active", dotIndex === index);
        });
      };

      const timer = setInterval(() => {
        show((active + 1) % count);
      }, 3500);
      this._carouselTimers.push(timer);
    });
  }

  /** True when running inside Home Assistant ingress iframe. */
  _isHaEmbedded() {
    const path = window.location.pathname || "";
    return /\/api\/hassio_ingress\//.test(path) || /\/api\/ingress\//.test(path);
  }

  _syncHaLayoutVars() {
    const host = this;
    if (!host?.style) return;
    try {
      const haRoot = window.parent?.document?.documentElement;
      if (!haRoot) return;
      const haStyle = getComputedStyle(haRoot);
      const headerHeight = haStyle.getPropertyValue("--header-height").trim();
      if (headerHeight) {
        host.style.setProperty("--header-height", headerHeight);
      }
      const fontFamily = haStyle.getPropertyValue("--paper-font-body1_-_font-family").trim();
      if (fontFamily) {
        host.style.setProperty("--hd-font-family", fontFamily);
      }
    } catch (_) {
      // Standalone or cross-origin — fall back to CSS defaults.
    }
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
      let detail = text;
      try {
        const parsed = JSON.parse(text);
        if (parsed.detail) {
          detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
        }
      } catch (_) {
        // Plain-text error body
      }
      throw new Error(detail || `API error ${resp.status}`);
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
      this._normalizeMailSettings(this._settings);
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
      this._dashboardSettled = true;
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
        this._fetchApi("/api/mail/sync", { method: "POST", body: "{}" }).then(() => this._fetchApi("/api/mail")).catch(() => this._mailState),
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

    set("--primary-text-color", t.text);
    set("--secondary-text-color", t.muted);
    set("--panel-header-background", t.surface);
    host.setAttribute("data-hd-theme", mode);
    this._syncHaLayoutVars();
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

    if (mailEnabled || imapHost) {
      // Legacy mail form — accounts are managed directly in _settings.mail.accounts
    }

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

  _carrierClass(carrier) {
    const slug = String(carrier || "unknown").toLowerCase().replace(/[^a-z0-9]/g, "");
    return slug || "unknown";
  }

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

  _generateId() {
    return (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  _normalizeMailSettings(settings) {
    if (!settings.mail) settings.mail = { accounts: [] };
    if (Array.isArray(settings.mail.accounts)) return;

    const legacy = settings.mail;
    if (legacy.imap_host || legacy.imap_user) {
      settings.mail = {
        accounts: [{
          id: this._generateId(),
          label: legacy.label || "Home",
          enabled: legacy.enabled !== false,
          imap_host: legacy.imap_host || "",
          imap_port: legacy.imap_port || 993,
          imap_user: legacy.imap_user || "",
          imap_password: legacy.imap_password || "",
          folder: legacy.folder || "INBOX",
          piece_count: legacy.piece_count || 0,
          last_check: legacy.last_check || null,
          gif_filename: legacy.gif_filename || null,
          last_error: null,
        }],
      };
    } else {
      settings.mail = { accounts: [] };
    }
  }

  _getMailAccounts() {
    this._normalizeMailSettings(this._settings);
    return this._settings.mail?.accounts || [];
  }

  _getMailAccount(id) {
    return this._getMailAccounts().find(a => a.id === id) || null;
  }

  async _persistMailAccounts() {
    if (!this._settings.mail) this._settings.mail = { accounts: [] };
    await this._saveSettings({ silent: true });
  }

  async _syncMail(accountId = null) {
    this._mailSyncing = true;
    this._mailSyncAccountId = accountId;
    this._render();
    try {
      await this._saveSettings({ silent: true });
      const body = accountId ? { account_id: accountId } : {};
      const resp = await this._fetchApi("/api/mail/sync", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const mailResp = await this._fetchApi("/api/mail");
      this._mailState = mailResp;
      const configResp = await this._fetchApi("/api/config");
      this._config = configResp.config || {};
      this._settings = JSON.parse(JSON.stringify(this._config));
      this._normalizeMailSettings(this._settings);

      const failed = (resp.results || []).filter(r => !r.success);
      if (failed.length > 0) {
        this._showToast(failed[0].error || "Sync failed", { error: true });
      } else {
        this._showToast(`Synced — ${resp.piece_count || 0} pieces today`);
      }
    } catch (err) {
      this._showToast(err.message, { error: true });
    } finally {
      this._mailSyncing = false;
      this._mailSyncAccountId = null;
      this._render();
    }
  }

  // ============================================================================
  // Add Package Wizard Helpers
  // ============================================================================

  async _handleWizardNext() {
    const wiz = this._addPackageWizard;
    if (!wiz) return;

    const s = this.shadowRoot;

    if (wiz.step === 1) {
      const trackingInput = s.querySelector("#wizard-tracking");
      const tracking = trackingInput?.value?.trim();

      if (!tracking) {
        this._showToast("Please enter a tracking number", { error: true });
        return;
      }

      wiz.tracking = tracking;

      // If we already have a carrier, move to next step
      if (wiz.carrier) {
        wiz.step = 2;
        this._render();
        return;
      }

      // Probe for carrier
      wiz.probing = true;
      wiz.probeError = null;
      this._render();

      try {
        const resp = await this._fetchApi("/api/packages/probe-carrier", {
          method: "POST",
          body: JSON.stringify({ tracking_number: tracking }),
        });

        wiz.carrier = resp.carrier;
        wiz.probing = false;
        wiz.step = 2;
        this._render();
      } catch (err) {
        wiz.probing = false;
        wiz.probeError = err.message || "Could not detect carrier";
        this._render();
      }
    } else if (wiz.step === 2) {
      const recipientInput = s.querySelector("#wizard-recipient");
      wiz.recipient = recipientInput?.value?.trim() || "";
      wiz.step = 3;
      this._render();
    }
  }

  _handleWizardBack() {
    const wiz = this._addPackageWizard;
    if (!wiz || wiz.step <= 1) return;

    const s = this.shadowRoot;

    // Capture current step's input before going back
    if (wiz.step === 2) {
      const recipientInput = s.querySelector("#wizard-recipient");
      wiz.recipient = recipientInput?.value?.trim() || "";
    } else if (wiz.step === 3) {
      const otherInput = s.querySelector("#wizard-destination-other");
      if (otherInput) {
        wiz.destinationOther = otherInput.value?.trim() || "";
      }
    }

    wiz.step--;
    this._render();
  }

  async _handleWizardSubmit() {
    const wiz = this._addPackageWizard;
    if (!wiz) return;

    const s = this.shadowRoot;

    // Get destination
    let destination = "";
    let destinationAccountId = null;

    const accounts = this._getMailAccounts();
    const hasAccounts = accounts.length > 0;

    if (hasAccounts && wiz.destinationMode === "account" && wiz.destinationAccountId) {
      const account = this._getMailAccount(wiz.destinationAccountId);
      destination = account?.label || "";
      destinationAccountId = wiz.destinationAccountId;
    } else {
      const otherInput = s.querySelector("#wizard-destination-other");
      destination = otherInput?.value?.trim() || wiz.destinationOther || "";
    }

    if (!wiz.tracking || !wiz.carrier) {
      this._showToast("Missing tracking number or carrier", { error: true });
      return;
    }

    wiz.submitting = true;
    this._render();

    try {
      await this._addPackage({
        tracking_number: wiz.tracking,
        carrier: wiz.carrier,
        recipient: wiz.recipient || "",
        destination: destination,
        destination_account_id: destinationAccountId,
      });

      this._addPackageWizard = null;
      this._render();
      this._showToast("Package added");
    } catch (err) {
      wiz.submitting = false;
      this._render();
      this._showToast(err.message, { error: true });
    }
  }

  // ============================================================================
  // Mail Account Wizard Helpers
  // ============================================================================

  _syncMailWizardFromForm(step) {
    const s = this.shadowRoot;
    const modal = this._mailAccountModal;
    if (!s || !modal) return;

    if (step === 1) {
      modal.imapHost = s.querySelector("#mail-imap-host")?.value?.trim() || modal.imapHost;
      modal.imapPort = parseInt(s.querySelector("#mail-imap-port")?.value) || modal.imapPort || 993;
      modal.imapUser = s.querySelector("#mail-imap-user")?.value?.trim() || modal.imapUser;
      const password = s.querySelector("#mail-imap-password")?.value;
      if (password !== undefined && password !== null) {
        modal.imapPassword = password;
      }
    } else if (step === 2) {
      modal.folder = s.querySelector("#mail-imap-folder")?.value?.trim() || modal.folder || "INBOX";
    } else if (step === 3) {
      modal.label = s.querySelector("#mail-label")?.value?.trim() || modal.label;
    }
  }

  async _handleMailWizardNext() {
    const modal = this._mailAccountModal;
    if (!modal) return;

    if (modal.step === 1) {
      this._syncMailWizardFromForm(1);

      if (!modal.imapHost || !modal.imapUser) {
        this._showToast("IMAP server and email are required", { error: true });
        return;
      }

      const isEdit = modal.mode === "edit";
      const hasPassword = modal.imapPassword && modal.imapPassword !== "********";
      if (!isEdit && !hasPassword) {
        this._showToast("Password is required", { error: true });
        return;
      }
      if (isEdit && !hasPassword && !modal.id) {
        this._showToast("Password is required", { error: true });
        return;
      }

      modal.testing = true;
      modal.testError = null;
      this._render();

      try {
        const body = {
          imap_host: modal.imapHost,
          imap_port: modal.imapPort || 993,
          imap_user: modal.imapUser,
          imap_password: modal.imapPassword || null,
        };
        if (modal.mode === "edit" && modal.id) {
          body.account_id = modal.id;
        }

        const resp = await this._fetchApi("/api/mail/test-imap", {
          method: "POST",
          body: JSON.stringify(body),
        });

        modal.folders = resp.folders || [];
        const preferred = modal.folder && modal.folders.includes(modal.folder)
          ? modal.folder
          : resp.default_folder;
        modal.folder = preferred || "INBOX";
        modal.testing = false;
        modal.step = 2;
        this._render();
      } catch (err) {
        modal.testing = false;
        modal.testError = err.message || "Could not connect to IMAP";
        this._render();
      }
    } else if (modal.step === 2) {
      this._syncMailWizardFromForm(2);
      modal.step = 3;
      this._render();
    }
  }

  _handleMailWizardBack() {
    const modal = this._mailAccountModal;
    if (!modal || modal.step <= 1) return;

    this._syncMailWizardFromForm(modal.step);
    modal.step--;
    modal.testError = null;
    this._render();
  }

  async _handleMailWizardSubmit() {
    const modal = this._mailAccountModal;
    if (!modal) return;

    this._syncMailWizardFromForm(3);

    if (!modal.label) {
      this._showToast("Please enter who this mail is for", { error: true });
      return;
    }

    modal.submitting = true;
    this._render();

    const accounts = this._getMailAccounts();
    const isEdit = modal.mode === "edit";

    try {
      if (isEdit) {
        const idx = accounts.findIndex(a => a.id === modal.id);
        if (idx >= 0) {
          const updated = {
            ...accounts[idx],
            label: modal.label,
            imap_host: modal.imapHost,
            imap_port: modal.imapPort || 993,
            folder: modal.folder || "INBOX",
            imap_user: modal.imapUser,
          };
          if (modal.imapPassword && modal.imapPassword !== "********") {
            updated.imap_password = modal.imapPassword;
          }
          accounts[idx] = updated;
        }
      } else {
        if (!modal.imapPassword || modal.imapPassword === "********") {
          throw new Error("Password is required");
        }
        accounts.push({
          id: this._generateId(),
          label: modal.label,
          enabled: true,
          imap_host: modal.imapHost,
          imap_port: modal.imapPort || 993,
          imap_user: modal.imapUser,
          imap_password: modal.imapPassword,
          folder: modal.folder || "INBOX",
          piece_count: 0,
          last_check: null,
          gif_filename: null,
          last_error: null,
        });
      }

      this._settings.mail = { accounts };
      await this._persistMailAccounts();
      this._mailAccountModal = null;
      const mailResp = await this._fetchApi("/api/mail");
      this._mailState = mailResp;
      this._render();
      this._showToast(isEdit ? "Address updated" : "Address added");
    } catch (err) {
      modal.submitting = false;
      this._render();
      this._showToast(err.message, { error: true });
    }
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
        <div class="settings-view ${this._isNarrow ? "narrow" : ""} ${this._isHaEmbedded() ? "ha-embedded" : ""}">
          ${this._renderSettingsMenubar()}
          <div class="settings-body">
            ${this._renderSettingsContent()}
          </div>
        </div>
        ${this._mailAccountModal ? this._renderMailAccountModal() : ""}
        ${this._addPackageWizard ? this._renderAddPackageWizard() : ""}
      `;
      this._bindEvents();
      this._attachSettingsHandlers();
      return;
    }

    s.innerHTML = `
      <style>${this._getStyles()}</style>
      <div class="hud-wrapper ${this._isHaEmbedded() ? "ha-embedded" : ""} ${this._isNarrow ? "narrow" : ""}">
        <div class="delivery-app">
          ${this._renderDashboardTopbar()}
          <div class="content-area">
            ${this._loading ? this._renderLoading() : ""}
            ${this._error && !this._loading ? this._renderError() : ""}
            ${!this._loading && !this._error ? this._renderDashboard() : ""}
          </div>
        </div>
      </div>
      ${this._addPackageWizard ? this._renderAddPackageWizard() : ""}
      ${this._selectedPackage ? this._renderPackageDetail() : ""}
    `;

    this._bindEvents();
  }

  _renderTopbarActions() {
    return `
      <div class="status-card">
        <button class="icon-btn" id="refresh-btn" aria-label="Refresh" ${this._refreshingAll ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" class="${this._refreshingAll ? "spinning" : ""}"><path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
        </button>
        <button class="icon-btn" id="gear-btn" aria-label="Settings">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.488.488 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
        </button>
      </div>
    `;
  }

  _renderDashboardTopbar() {
    const embeddedNarrow = this._isHaEmbedded() && this._isNarrow;

    if (embeddedNarrow) {
      return `
        <header class="topbar topbar--embedded-overlay" aria-label="Panel actions">
          <div class="topbar-spacer" aria-hidden="true"></div>
          ${this._renderTopbarActions()}
        </header>
      `;
    }

    return `
      <header class="topbar">
        ${this._isNarrow ? `<button class="hamburger icon-btn" id="hamburger-btn" aria-label="Open sidebar">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
        </button>` : ""}
        <section class="title-card">
          <div class="title-wrap">
            <div class="title">Home Delivery</div>
          </div>
        </section>
        ${this._renderTopbarActions()}
      </header>
    `;
  }

  _renderSettingsMenubar() {
    const backLabel = "Back";
    const embeddedNarrow = this._isHaEmbedded() && this._isNarrow;

    if (embeddedNarrow) {
      return `
        <header class="topbar topbar--embedded-overlay topbar--settings-overlay">
          <button type="button" class="hd-menubar-back hd-menubar-back--compact" data-action="nav-back" aria-label="${backLabel}">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
          </button>
          <div class="topbar-spacer" aria-hidden="true"></div>
          <span class="settings-save-status" id="settings-save-status" role="status" aria-live="polite" data-state="${this._saveStatus}">${this._saveStatusText}</span>
        </header>
      `;
    }

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
    const settled = this._dashboardSettled ? " dashboard--settled" : "";
    return `
      <section class="dashboard${settled}">
        ${this._renderMailHero()}
        ${this._renderStatsStrip()}
        ${this._renderOtherMailSection()}
        <div class="dashboard-bento">
          ${this._renderPackagesSection()}
          ${this._renderDeliveredSection()}
        </div>
      </section>
    `;
  }

  _renderStatsStrip() {
    const mail = this._mailState || {};
    const accounts = mail.accounts || [];
    const home = this._getHomeMailAccount(accounts);
    const active = this._packages.filter(p => !p.delivered);
    const delivered = this._packages.filter(p => p.delivered);
    const mailCount = home ? (home.piece_count ?? 0) : (mail.configured ? 0 : "—");

    return `
      <div class="stats-strip" role="list" aria-label="Delivery overview">
        <article class="stat-glass" role="listitem">
          <div class="stat-glass-value">${mailCount}</div>
          <div class="stat-glass-label">Mail today</div>
        </article>
        <article class="stat-glass" role="listitem">
          <div class="stat-glass-value">${active.length}</div>
          <div class="stat-glass-label">Active packages</div>
        </article>
        <article class="stat-glass" role="listitem">
          <div class="stat-glass-value">${delivered.length}</div>
          <div class="stat-glass-label">Delivered</div>
        </article>
      </div>
    `;
  }

  _renderOtherMailSection() {
    const mail = this._mailState || {};
    if (!mail.configured) return "";

    const others = this._getOtherMailAccounts(mail.accounts || []);
    if (others.length === 0) return "";

    return `
      <section class="mail-other-section" aria-label="Other mail addresses">
        <div class="mail-other-head">
          <div class="mail-other-title">Other Addresses</div>
          <div class="mail-other-sub">${others.length} address${others.length === 1 ? "" : "es"}</div>
        </div>
        <div class="mail-other-grid">
          ${others.map((account) => this._renderOtherMailCard(account)).join("")}
        </div>
      </section>
    `;
  }

  _renderOtherMailCard(account) {
    const lastCheckStr = account.last_check
      ? new Date(account.last_check).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
      : "";
    const label = account.label || account.imap_user || "Address";
    const syncing = this._mailSyncing && this._mailSyncAccountId === account.id;

    return `
      <article class="glass card mail-other-card ${account.last_error ? "has-error" : ""}">
        <div class="mail-other-card-head">
          <div>
            <div class="mail-other-label">${this._esc(label)}</div>
            ${lastCheckStr ? `<div class="mail-other-meta">Last checked: ${lastCheckStr}</div>` : ""}
          </div>
          <button class="btn btn-sm btn-ghost" data-action="refresh-mail" data-id="${account.id}" ${syncing ? "disabled" : ""}>
            ${syncing ? "..." : "Check"}
          </button>
        </div>
        <div class="mail-other-body">
          <div class="mail-other-count">${account.piece_count || 0}</div>
          <div class="mail-other-count-label">pieces arriving</div>
        </div>
        ${this._mailPreviewHtml(account, `${label} mail preview`)}
        ${account.last_error ? `<div class="mail-other-error">${this._esc(account.last_error)}</div>` : ""}
      </article>
    `;
  }

  _renderMailHero() {
    const mail = this._mailState || {};
    const configured = mail.configured;
    const enabled = mail.enabled;
    const accounts = mail.accounts || [];
    const home = this._getHomeMailAccount(accounts);

    if (!configured) {
      return `
        <article class="glass card mail-hero-card mail-hero-card--empty">
          <div class="mail-hero-bg" aria-hidden="true"></div>
          <div class="mail-hero-inner">
            <div class="mail-hero-message">
              <svg viewBox="0 0 24 24" width="48" height="48" fill="currentColor" style="opacity:0.3"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
              <p>No mail addresses configured</p>
              <button class="btn btn-primary" data-action="configure-mail">Add Mail Address</button>
            </div>
          </div>
        </article>
      `;
    }

    const lastCheckStr = home?.last_check
      ? new Date(home.last_check).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
      : "";
    const subLabel = home?.label || "Home";
    const pieceCount = home?.piece_count || 0;
    const homeSyncing = this._mailSyncing && (!this._mailSyncAccountId || this._mailSyncAccountId === home?.id);

    return `
      <article class="glass card mail-hero-card">
        <div class="mail-hero-bg" aria-hidden="true"></div>
        <div class="mail-hero-inner">
          <div class="card-head mail-hero-head">
            <div>
              <div class="mail-hero-eyebrow">Informed Delivery</div>
              <div class="card-title">Mail Today</div>
              <div class="card-sub">${this._esc(subLabel)}</div>
            </div>
            <button class="btn btn-sm btn-ghost" data-action="refresh-mail" ${home?.id ? `data-id="${home.id}"` : ""} ${homeSyncing ? "disabled" : ""}>
              ${homeSyncing ? "Checking..." : "Check Now"}
            </button>
          </div>
          <div class="mail-hero-stage">
            <div class="mail-hero-main">
              <div class="mail-count-large">${pieceCount}</div>
              <div class="mail-count-label">pieces arriving</div>
            </div>
            ${this._mailPreviewHtml(home, `${subLabel} mail preview`)}
          </div>
          ${lastCheckStr ? `<div class="mail-meta">Last checked: ${lastCheckStr}</div>` : ""}
          ${home?.last_error ? `<div class="mail-meta mail-meta-warn">${this._esc(home.last_error)}</div>` : ""}
          ${!enabled ? `<div class="mail-meta mail-meta-warn">All addresses are disabled — enable one in Settings</div>` : ""}
        </div>
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
      <div class="package-card ${this._statusClass(pkg)} carrier-${this._carrierClass(pkg.carrier)}" data-package-id="${pkg.id}">
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

  _renderSettingsContent() {
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

            ${this._renderMailSettingsPane(activePane)}

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

  _renderMailSettingsPane(activePane) {
    const accounts = this._getMailAccounts();

    return `
      <section class="settings-pane ${activePane === "mail" ? "active" : ""}" data-settings-pane="mail">
        <div class="settings-pane-head">
          <div class="settings-pane-title">Mail</div>
          <div class="settings-pane-sub">USPS Informed Delivery via IMAP</div>
        </div>
        <div class="settings-card mail-accounts-card">
          <div class="settings-card-body">
            ${accounts.length === 0 ? `
              <div class="mail-accounts-empty">
                <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor" style="opacity:0.3"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
                <p>No mail addresses yet</p>
                <p class="hint">Add an address to track USPS Informed Delivery</p>
              </div>
            ` : `
              <div class="mail-accounts-list">
                ${accounts.map(a => this._renderMailAccountCard(a)).join("")}
              </div>
            `}
            <button type="button" class="btn mail-add-btn" data-action="add-mail-account">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
              Add Address
            </button>
          </div>
          <div class="mail-sync-footer">
            <button type="button" class="btn btn-primary btn-full" data-action="sync-mail" ${this._mailSyncing ? "disabled" : ""}>
              ${this._mailSyncing ? "Syncing..." : "Sync"}
            </button>
            <p class="hint">Fetch inbox now and verify IMAP credentials</p>
          </div>
        </div>
      </section>
    `;
  }

  _renderMailAccountCard(account) {
    const lastCheck = account.last_check
      ? new Date(account.last_check).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
      : "Never synced";
    const statusClass = account.last_error ? "error" : account.enabled !== false ? "active" : "disabled";

    return `
      <div class="mail-account-card ${statusClass}" data-account-id="${account.id}">
        <div class="mail-account-main">
          <div class="mail-account-label">${this._esc(account.label || "Address")}</div>
          <div class="mail-account-email">${this._esc(account.imap_user || "")}</div>
          <div class="mail-account-meta">
            <span>${account.piece_count || 0} pieces today</span>
            <span class="mail-account-dot">·</span>
            <span>${this._esc(account.folder || "INBOX")}</span>
          </div>
          <div class="mail-account-meta muted">${lastCheck}</div>
          ${account.last_error ? `<div class="mail-account-error">${this._esc(account.last_error)}</div>` : ""}
        </div>
        <div class="mail-account-actions">
          <label class="toggle-switch" title="${account.enabled !== false ? "Disable" : "Enable"}">
            <input type="checkbox" data-action="toggle-mail-account" data-id="${account.id}" ${account.enabled !== false ? "checked" : ""} />
            <span class="toggle-slider"></span>
          </label>
          <button type="button" class="btn btn-sm" data-action="edit-mail-account" data-id="${account.id}">Edit</button>
          <button type="button" class="btn btn-sm btn-danger" data-action="delete-mail-account" data-id="${account.id}">Remove</button>
        </div>
      </div>
    `;
  }

  _renderMailAccountModal() {
    const modal = this._mailAccountModal || {};
    const isEdit = modal.mode === "edit";
    const step = modal.step || 1;

    return `
      <div class="modal-backdrop" data-action="close-mail-account">
        <div class="modal wizard-modal" onclick="event.stopPropagation()">
          <div class="modal-header">
            <h2>${isEdit ? "Edit Mail Address" : "Add Mail Address"}</h2>
            <button class="close-btn" data-action="close-mail-account">&times;</button>
          </div>
          <div class="wizard-progress">
            <div class="wizard-step-indicator ${step >= 1 ? "active" : ""} ${step > 1 ? "completed" : ""}">
              <span class="wizard-step-num">1</span>
              <span class="wizard-step-label">IMAP</span>
            </div>
            <div class="wizard-step-connector ${step > 1 ? "completed" : ""}"></div>
            <div class="wizard-step-indicator ${step >= 2 ? "active" : ""} ${step > 2 ? "completed" : ""}">
              <span class="wizard-step-num">2</span>
              <span class="wizard-step-label">Folder</span>
            </div>
            <div class="wizard-step-connector ${step > 2 ? "completed" : ""}"></div>
            <div class="wizard-step-indicator ${step >= 3 ? "active" : ""}">
              <span class="wizard-step-num">3</span>
              <span class="wizard-step-label">Details</span>
            </div>
          </div>
          <div class="modal-body wizard-body">
            ${step === 1 ? this._renderMailWizardStep1(modal) : ""}
            ${step === 2 ? this._renderMailWizardStep2(modal) : ""}
            ${step === 3 ? this._renderMailWizardStep3(modal) : ""}
          </div>
        </div>
      </div>
    `;
  }

  _renderMailWizardStep1(modal) {
    const isEdit = modal.mode === "edit";
    const testing = modal.testing;

    return `
      <div class="wizard-step-content">
        <div class="form-group">
          <label for="mail-imap-host">IMAP Server</label>
          <input type="text" id="mail-imap-host" required
            value="${this._esc(modal.imapHost || "imap.gmail.com")}"
            placeholder="imap.gmail.com"
            ${testing ? "disabled" : ""} />
        </div>
        <div class="form-group">
          <label for="mail-imap-port">Port</label>
          <input type="number" id="mail-imap-port"
            value="${modal.imapPort || 993}"
            ${testing ? "disabled" : ""} />
        </div>
        <div class="form-group">
          <label for="mail-imap-user">Email Address</label>
          <input type="email" id="mail-imap-user" required
            value="${this._esc(modal.imapUser || "")}"
            placeholder="you@example.com"
            ${testing ? "disabled" : ""} />
        </div>
        <div class="form-group">
          <label for="mail-imap-password">Password / App Password</label>
          <input type="password" id="mail-imap-password" ${isEdit ? "" : "required"}
            value="${modal.imapPassword === "********" ? "********" : this._esc(modal.imapPassword || "")}"
            placeholder="App password for Gmail"
            ${testing ? "disabled" : ""} />
          ${isEdit ? `<p class="hint">Leave as ******** to keep your existing password</p>` : ""}
        </div>
        ${modal.testError ? `
          <div class="wizard-error">${this._esc(modal.testError)}</div>
        ` : ""}
        ${testing ? `
          <div class="wizard-probing">
            <div class="spinner small"></div>
            <span>Connecting and listing folders...</span>
          </div>
        ` : ""}
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn" data-action="close-mail-account">Cancel</button>
        <button type="button" class="btn btn-primary" data-action="mail-wizard-next" ${testing ? "disabled" : ""}>
          Connect
        </button>
      </div>
    `;
  }

  _renderMailWizardStep2(modal) {
    const folders = modal.folders || [];
    const selectedFolder = modal.folder || "INBOX";

    return `
      <div class="wizard-step-content">
        <div class="wizard-carrier-result">
          <span class="wizard-carrier-text">Connected to ${this._esc(modal.imapUser || "")}</span>
        </div>
        <div class="form-group">
          <label for="mail-imap-folder">Mailbox Folder</label>
          <select id="mail-imap-folder">
            ${folders.map(folder => `
              <option value="${this._esc(folder)}" ${folder === selectedFolder ? "selected" : ""}>
                ${this._esc(folder)}
              </option>
            `).join("")}
          </select>
          <p class="hint">Choose where USPS Informed Delivery emails arrive (usually INBOX)</p>
        </div>
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn btn-ghost" data-action="mail-wizard-back">Back</button>
        <button type="button" class="btn btn-primary" data-action="mail-wizard-next">Next</button>
      </div>
    `;
  }

  _renderMailWizardStep3(modal) {
    const submitting = modal.submitting;

    return `
      <div class="wizard-step-content">
        <div class="form-group">
          <label for="mail-label">Mail for</label>
          <input type="text" id="mail-label" required
            value="${this._esc(modal.label || "")}"
            placeholder="e.g., Home, Mom, Office"
            ${submitting ? "disabled" : ""} />
          <p class="hint">Who this mail is for — used in announcements and package destinations</p>
        </div>
        <div class="wizard-summary">
          <div class="wizard-summary-row">
            <span class="wizard-summary-label">Email</span>
            <span>${this._esc(modal.imapUser || "")}</span>
          </div>
          <div class="wizard-summary-row">
            <span class="wizard-summary-label">Folder</span>
            <span>${this._esc(modal.folder || "INBOX")}</span>
          </div>
          <div class="wizard-summary-row">
            <span class="wizard-summary-label">Server</span>
            <span>${this._esc(modal.imapHost || "")}:${modal.imapPort || 993}</span>
          </div>
        </div>
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn btn-ghost" data-action="mail-wizard-back" ${submitting ? "disabled" : ""}>Back</button>
        <button type="button" class="btn btn-primary" data-action="mail-wizard-submit" ${submitting ? "disabled" : ""}>
          ${submitting ? "Saving..." : modal.mode === "edit" ? "Save Address" : "Add Address"}
        </button>
      </div>
    `;
  }

  _initMailAccountWizard(mode, account = null) {
    if (mode === "edit" && account) {
      return {
        mode: "edit",
        id: account.id,
        step: 1,
        imapHost: account.imap_host || "imap.gmail.com",
        imapPort: account.imap_port || 993,
        imapUser: account.imap_user || "",
        imapPassword: account.imap_password ? "********" : "",
        folders: [],
        folder: account.folder || "INBOX",
        label: account.label || "",
        testing: false,
        testError: null,
        submitting: false,
      };
    }

    return {
      mode: "add",
      id: null,
      step: 1,
      imapHost: "imap.gmail.com",
      imapPort: 993,
      imapUser: "",
      imapPassword: "",
      folders: [],
      folder: "INBOX",
      label: "",
      testing: false,
      testError: null,
      submitting: false,
    };
  }

  _renderAddPackageWizard() {
    const wiz = this._addPackageWizard || { step: 1 };
    const step = wiz.step || 1;
    const accounts = this._getMailAccounts();

    return `
      <div class="modal-backdrop" data-action="close-wizard">
        <div class="modal wizard-modal" onclick="event.stopPropagation()">
          <div class="modal-header">
            <h2>Add Package</h2>
            <button class="close-btn" data-action="close-wizard">&times;</button>
          </div>
          <div class="wizard-progress">
            <div class="wizard-step-indicator ${step >= 1 ? "active" : ""} ${step > 1 ? "completed" : ""}">
              <span class="wizard-step-num">1</span>
              <span class="wizard-step-label">Tracking</span>
            </div>
            <div class="wizard-step-connector ${step > 1 ? "completed" : ""}"></div>
            <div class="wizard-step-indicator ${step >= 2 ? "active" : ""} ${step > 2 ? "completed" : ""}">
              <span class="wizard-step-num">2</span>
              <span class="wizard-step-label">Recipient</span>
            </div>
            <div class="wizard-step-connector ${step > 2 ? "completed" : ""}"></div>
            <div class="wizard-step-indicator ${step >= 3 ? "active" : ""}">
              <span class="wizard-step-num">3</span>
              <span class="wizard-step-label">Destination</span>
            </div>
          </div>
          <div class="modal-body wizard-body">
            ${step === 1 ? this._renderWizardStep1(wiz) : ""}
            ${step === 2 ? this._renderWizardStep2(wiz) : ""}
            ${step === 3 ? this._renderWizardStep3(wiz, accounts) : ""}
          </div>
        </div>
      </div>
    `;
  }

  _renderWizardStep1(wiz) {
    const probing = wiz.probing;
    const probeError = wiz.probeError;
    const carrier = wiz.carrier;

    return `
      <div class="wizard-step-content">
        <div class="form-group">
          <label for="wizard-tracking">Tracking Number *</label>
          <input type="text" id="wizard-tracking" required
            placeholder="e.g., 9400111899560438600329"
            value="${this._esc(wiz.tracking || "")}"
            ${probing ? "disabled" : ""} />
          <p class="hint">We'll check USPS, UPS, and FedEx automatically</p>
        </div>
        ${carrier ? `
          <div class="wizard-carrier-result">
            ${this._carrierBadge(carrier)}
            <span class="wizard-carrier-text">Carrier detected</span>
          </div>
        ` : ""}
        ${probeError ? `
          <div class="wizard-error">${this._esc(probeError)}</div>
        ` : ""}
        ${probing ? `
          <div class="wizard-probing">
            <div class="spinner small"></div>
            <span>Detecting carrier...</span>
          </div>
        ` : ""}
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn" data-action="close-wizard">Cancel</button>
        <button type="button" class="btn btn-primary" data-action="wizard-next" ${probing ? "disabled" : ""}>
          ${carrier ? "Next" : "Detect Carrier"}
        </button>
      </div>
    `;
  }

  _renderWizardStep2(wiz) {
    return `
      <div class="wizard-step-content">
        <div class="form-group">
          <label for="wizard-recipient">Who is this package for?</label>
          <input type="text" id="wizard-recipient"
            placeholder="e.g., Mom, John, Office"
            value="${this._esc(wiz.recipient || "")}" />
          <p class="hint">Optional — used for announcements</p>
        </div>
        <div class="wizard-carrier-summary">
          ${this._carrierBadge(wiz.carrier)}
          <span class="wizard-tracking-preview">${this._esc(wiz.tracking || "")}</span>
        </div>
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn btn-ghost" data-action="wizard-back">Back</button>
        <button type="button" class="btn btn-primary" data-action="wizard-next">Next</button>
      </div>
    `;
  }

  _renderWizardStep3(wiz, accounts) {
    const showOtherInput = wiz.destinationMode === "other";
    const hasAccounts = accounts.length > 0;

    return `
      <div class="wizard-step-content">
        <div class="form-group">
          <label for="wizard-destination-select">Destination</label>
          ${hasAccounts ? `
            <select id="wizard-destination-select">
              <option value="">Select destination...</option>
              ${accounts.map(a => `
                <option value="${this._esc(a.id)}" ${wiz.destinationAccountId === a.id ? "selected" : ""}>
                  ${this._esc(a.label || a.imap_user || "Address")}
                </option>
              `).join("")}
              <option value="other" ${wiz.destinationMode === "other" ? "selected" : ""}>Other (enter manually)</option>
            </select>
          ` : `
            <p class="hint">No mail addresses configured — enter destination manually</p>
          `}
        </div>
        ${showOtherInput || !hasAccounts ? `
          <div class="form-group">
            <label for="wizard-destination-other">${hasAccounts ? "Enter destination" : "Destination"}</label>
            <input type="text" id="wizard-destination-other"
              placeholder="e.g., Home, Work, Grandma's"
              value="${this._esc(wiz.destinationOther || "")}" />
          </div>
        ` : ""}
        ${!hasAccounts ? `
          <p class="hint">
            <a href="#" data-action="configure-mail" class="link">Add mail addresses</a> in Settings to use as destinations
          </p>
        ` : ""}
        <div class="wizard-summary">
          <div class="wizard-summary-row">
            ${this._carrierBadge(wiz.carrier)}
            <span>${this._esc(wiz.tracking || "")}</span>
          </div>
          ${wiz.recipient ? `
            <div class="wizard-summary-row">
              <span class="wizard-summary-label">For:</span>
              <span>${this._esc(wiz.recipient)}</span>
            </div>
          ` : ""}
        </div>
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn btn-ghost" data-action="wizard-back">Back</button>
        <button type="button" class="btn btn-primary" data-action="wizard-submit" ${wiz.submitting ? "disabled" : ""}>
          ${wiz.submitting ? "Adding..." : "Add Package"}
        </button>
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

    // Wizard destination select handler
    const destSelect = s.querySelector("#wizard-destination-select");
    if (destSelect) {
      destSelect.addEventListener("change", () => {
        const value = destSelect.value;
        if (this._addPackageWizard) {
          if (value === "other") {
            this._addPackageWizard.destinationMode = "other";
            this._addPackageWizard.destinationAccountId = null;
          } else if (value) {
            this._addPackageWizard.destinationMode = "account";
            this._addPackageWizard.destinationAccountId = value;
            const account = this._getMailAccount(value);
            if (account) {
              this._addPackageWizard.destinationOther = account.label || "";
            }
          } else {
            this._addPackageWizard.destinationMode = null;
            this._addPackageWizard.destinationAccountId = null;
          }
          this._render();
        }
      });
    }

    s.querySelectorAll('[data-action="toggle-mail-account"]').forEach(el => {
      el.addEventListener("change", (e) => {
        e.stopPropagation();
        this._handleAction(e, "toggle-mail-account", el.dataset);
      });
    });

    this._initMailCarousels();
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
        this._addPackageWizard = { step: 1, tracking: "", carrier: null, recipient: "", destinationAccountId: null, destinationOther: "", destinationMode: null, probing: false, probeError: null, submitting: false };
        this._render();
        break;
      case "close-wizard":
        this._addPackageWizard = null;
        this._render();
        break;
      case "wizard-next":
        await this._handleWizardNext();
        break;
      case "wizard-back":
        this._handleWizardBack();
        break;
      case "wizard-submit":
        await this._handleWizardSubmit();
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
        await this._syncMail(data.id || null);
        break;
      case "sync-mail":
        await this._syncMail();
        break;
      case "configure-mail":
        this._openSettingsPane("mail");
        break;
      case "add-mail-account":
        this._mailAccountModal = this._initMailAccountWizard("add");
        this._render();
        break;
      case "close-mail-account":
        this._mailAccountModal = null;
        this._render();
        break;
      case "mail-wizard-next":
        await this._handleMailWizardNext();
        break;
      case "mail-wizard-back":
        this._handleMailWizardBack();
        break;
      case "mail-wizard-submit":
        await this._handleMailWizardSubmit();
        break;
      case "edit-mail-account":
        this._mailAccountModal = this._initMailAccountWizard("edit", this._getMailAccount(data.id));
        this._render();
        break;
      case "delete-mail-account":
        if (confirm("Remove this mail address?")) {
          this._settings.mail.accounts = this._getMailAccounts().filter(a => a.id !== data.id);
          await this._persistMailAccounts();
          const mailResp = await this._fetchApi("/api/mail");
          this._mailState = mailResp;
          this._render();
          this._showToast("Address removed");
        }
        break;
      case "toggle-mail-account": {
        const account = this._getMailAccount(data.id);
        const checkbox = this.shadowRoot?.querySelector(`[data-action="toggle-mail-account"][data-id="${data.id}"]`);
        if (account && checkbox) {
          account.enabled = checkbox.checked;
          await this._persistMailAccounts();
          const mailResp = await this._fetchApi("/api/mail");
          this._mailState = mailResp;
          this._render();
        }
        break;
      }
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
        /* Inherit --header-height from Home Assistant when embedded; do not override. */
        --hd-menubar-height: var(--header-height, 64px);
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
        --safe-top: env(safe-area-inset-top, 0px);
        --safe-bottom: env(safe-area-inset-bottom, 0px);
        --safe-left: env(safe-area-inset-left, 0px);
        --safe-right: env(safe-area-inset-right, 0px);

        display: block;
        width: 100%;
        min-height: 100%;
        padding: 0;
        max-width: none;
        margin: 0;
        background: var(--hd-bg);
        color: var(--hd-text);
        font-family: var(--hd-font-family, var(--paper-font-body1_-_font-family, "Roboto", "Segoe UI", sans-serif));
        line-height: 1.5;
      }

      :host button,
      :host article,
      :host section {
        color: inherit;
      }

      :host article,
      :host section {
        background: transparent;
      }

      :host button {
        font: inherit;
      }

      * { box-sizing: border-box; }

      /* ======================== HUD Wrapper + App Shell ======================== */

      .hud-wrapper {
        position: relative;
        min-height: 100%;
        overflow: auto;
      }

      .hud-wrapper.ha-embedded.narrow {
        margin-top: calc(-1 * var(--header-height, 64px));
        min-height: calc(100% + var(--header-height, 64px));
      }

      .hud-wrapper.ha-embedded.narrow .content-area {
        padding-top: var(--space-2);
      }

      .settings-view.ha-embedded.narrow {
        margin-top: calc(-1 * var(--header-height, 64px));
        min-height: calc(100% + var(--header-height, 64px));
      }

      .hud-wrapper::before,
      .hud-wrapper::after {
        content: none;
      }

      .delivery-app {
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 0;
        height: 100%;
        min-height: 0;
        min-width: 0;
        position: relative;
      }

      /* ======================== Topbar ======================== */

      .topbar {
        position: sticky;
        top: 0;
        z-index: 100;
        display: flex;
        flex-wrap: nowrap;
        align-items: center;
        gap: var(--space-2) var(--space-3);
        min-width: 0;
        box-sizing: border-box;
        height: var(--header-height, 64px);
        min-height: var(--header-height, 64px);
        max-height: var(--header-height, 64px);
        padding: 0 calc(var(--space-3) + var(--safe-right)) 0 calc(var(--space-3) + var(--safe-left));
        background: var(--hd-surface);
        border-bottom: 1px solid var(--hd-border-strong);
      }

      .topbar .icon-btn {
        flex-shrink: 0;
        width: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
        min-width: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
        height: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
        min-height: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
      }

      .topbar .icon-btn svg {
        width: clamp(18px, 4.5vw, 20px);
        height: clamp(18px, 4.5vw, 20px);
      }

      .status-card {
        display: flex;
        align-items: center;
        gap: var(--space-2);
        justify-content: flex-end;
        padding: 0;
        flex-shrink: 0;
        flex-wrap: nowrap;
        min-width: 0;
        margin-left: auto;
        background: transparent;
        border: none;
        box-shadow: none;
        border-radius: 0;
      }

      .topbar-spacer {
        flex: 1;
        min-width: 0;
      }

      .topbar--embedded-overlay {
        background: transparent;
        border-bottom: none;
        z-index: 110;
      }

      .topbar--settings-overlay {
        gap: var(--space-2);
      }

      .topbar--settings-overlay .settings-save-status {
        flex-shrink: 1;
        min-width: 0;
        max-width: 42vw;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .hd-menubar-back--compact {
        flex-shrink: 0;
        width: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
        height: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
        min-width: clamp(34px, calc(var(--header-height, 64px) - 22px), 40px);
        padding: 0;
        justify-content: center;
        border: 1px solid var(--hd-border-strong);
        background: var(--hd-input-bg);
        border-radius: var(--radius-sm);
      }

      .hd-menubar-back--compact span {
        display: none;
      }

      .title-card {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        padding: 0 var(--space-2) 0 0;
        background: transparent;
        border: none;
        box-shadow: none;
        border-radius: 0;
      }

      .title-wrap {
        min-width: 0;
        flex: 1;
        overflow: hidden;
      }

      .title {
        font-size: clamp(15px, 1.8vw, 18px);
        line-height: 1.2;
        font-weight: 600;
        letter-spacing: -0.02em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        color: var(--hd-text);
        font-family: inherit;
      }

      .embedded-toolbar,
      .embedded-settings-bar {
        display: none;
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

      .topbar .hamburger.icon-btn {
        display: none;
      }

      @media (max-width: 768px) {
        .topbar .hamburger.icon-btn { display: inline-flex; }
      }

      .narrow .topbar .hamburger.icon-btn {
        display: inline-flex;
      }

      /* ======================== Content Area ======================== */

      .content-area {
        flex: 1;
        min-height: 0;
        min-width: 0;
        width: 100%;
        max-width: 100%;
        margin: 0;
        padding: clamp(var(--space-3), 3vw, var(--space-5));
        padding-bottom: calc(clamp(var(--space-3), 3vw, var(--space-5)) + var(--safe-bottom));
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        overflow-x: hidden;
      }

      @media (min-width: 1200px) {
        .content-area {
          max-width: 1600px;
          margin: 0 auto;
        }
      }

      .dashboard {
        display: flex;
        flex-direction: column;
        gap: var(--space-3);
        min-width: 0;
        min-height: 0;
        flex: 1;
      }

      .dashboard-bento {
        display: grid;
        gap: var(--space-3);
        min-width: 0;
      }

      @media (min-width: 960px) {
        .dashboard-bento {
          grid-template-columns: minmax(0, 1.35fr) minmax(0, 0.65fr);
          align-items: start;
        }

        .dashboard-bento .packages-card {
          grid-column: 1;
        }

        .dashboard-bento .delivered-card {
          grid-column: 2;
        }

        .dashboard-bento .packages-card:only-child {
          grid-column: 1 / -1;
        }
      }

      @media (prefers-reduced-motion: no-preference) {
        .dashboard:not(.dashboard--settled) > * {
          animation: cardIn 0.45s var(--ease) both;
        }

        .dashboard:not(.dashboard--settled) > *:nth-child(1) { animation-delay: 0s; }
        .dashboard:not(.dashboard--settled) > *:nth-child(2) { animation-delay: 0.06s; }
        .dashboard:not(.dashboard--settled) > *:nth-child(3) { animation-delay: 0.12s; }
      }

      @keyframes cardIn {
        from { opacity: 0; transform: translateY(12px); }
        to { opacity: 1; transform: translateY(0); }
      }

      .stats-strip {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: var(--space-3);
        min-width: 0;
      }

      .stat-glass {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border-strong);
        border-radius: var(--radius-lg);
        padding: var(--space-3) var(--space-4);
        box-shadow: var(--shadow-sm);
        min-width: 0;
        transition: border-color var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
      }

      .stat-glass:hover {
        border-color: var(--hd-accent);
        transform: translateY(-1px);
      }

      .stat-glass-value {
        font-size: clamp(22px, 5vw, 28px);
        font-weight: 700;
        letter-spacing: -0.04em;
        font-variant-numeric: tabular-nums;
        color: var(--hd-text);
        line-height: 1.1;
      }

      .stat-glass-label {
        margin-top: var(--space-1);
        font-size: 11px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--hd-muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      @media (max-width: 420px) {
        .stats-strip {
          gap: var(--space-2);
        }

        .stat-glass {
          padding: var(--space-2) var(--space-3);
        }

        .stat-glass-label {
          font-size: 10px;
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .dashboard:not(.dashboard--settled) > * {
          animation: none;
        }

        .stat-glass:hover,
        .package-card:hover {
          transform: none;
        }
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
        position: relative;
        overflow: hidden;
        padding: 0;
        min-height: clamp(200px, 42vw, 280px);
      }

      .mail-hero-card--empty .mail-hero-inner {
        min-height: clamp(180px, 36vw, 220px);
      }

      .mail-hero-bg {
        position: absolute;
        inset: 0;
        pointer-events: none;
        background:
          radial-gradient(ellipse 85% 70% at 12% 100%, var(--hd-accent-dim) 0%, transparent 58%),
          radial-gradient(ellipse 55% 45% at 92% 8%, rgba(255, 255, 255, 0.035) 0%, transparent 52%),
          linear-gradient(165deg, var(--hd-surface) 0%, var(--hd-surface-2) 100%);
      }

      .mail-hero-inner {
        position: relative;
        z-index: 1;
        padding: var(--space-4);
        display: flex;
        flex-direction: column;
        gap: var(--space-3);
        min-height: inherit;
      }

      .mail-hero-head {
        margin-bottom: 0;
      }

      .mail-hero-eyebrow {
        font-size: 10px;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--hd-muted);
        margin-bottom: 2px;
      }

      .mail-hero-stage {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: var(--space-4);
        min-width: 0;
      }

      @media (max-width: 560px) {
        .mail-hero-stage {
          grid-template-columns: 1fr;
        }

        .mail-preview {
          max-width: none;
        }
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

      .mail-hero-main {
        min-width: 0;
      }

      .mail-count-large {
        font-size: clamp(56px, 16vw, 88px);
        font-weight: 700;
        color: var(--hd-accent);
        line-height: 0.95;
        letter-spacing: -0.06em;
        font-variant-numeric: tabular-nums;
      }

      .mail-count-label {
        font-size: 14px;
        color: var(--hd-muted);
        margin-top: var(--space-1);
      }

      .mail-preview {
        min-width: 0;
        max-width: min(100%, 320px);
        justify-self: end;
      }

      .mail-preview img {
        width: 100%;
        border-radius: var(--radius-md);
        border: 1px solid var(--hd-border);
        box-shadow: var(--shadow-md);
      }

      .mail-preview--placeholder {
        display: flex;
        align-items: center;
        justify-content: center;
        width: clamp(120px, 28vw, 180px);
        aspect-ratio: 724 / 320;
        border-radius: var(--radius-md);
        border: 1px dashed var(--hd-border-strong);
        background: var(--hd-elevated);
        color: var(--hd-muted);
        opacity: 0.45;
      }

      .mail-meta {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: var(--space-3);
      }

      .mail-meta-warn {
        color: var(--hd-warning);
      }

      .mail-preview--broken {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 120px;
        border-radius: var(--radius-md);
        border: 1px dashed var(--hd-border-strong);
        background: var(--hd-elevated);
        color: var(--hd-muted);
        font-size: 12px;
      }

      .mail-preview--broken::after {
        content: "Preview unavailable";
      }

      .mail-carousel {
        position: relative;
        width: 100%;
        min-width: 0;
        max-width: min(100%, 320px);
        justify-self: end;
        aspect-ratio: 724 / 320;
        border-radius: var(--radius-md);
        overflow: hidden;
        border: 1px solid var(--hd-border);
        background: var(--hd-bg);
        box-shadow: var(--shadow-md);
      }

      .mail-carousel-viewport {
        position: relative;
        width: 100%;
        height: 100%;
      }

      .mail-carousel-slide {
        position: absolute;
        inset: 0;
        margin: 0;
        opacity: 0;
        transition: opacity 0.6s ease;
        pointer-events: none;
      }

      .mail-carousel-slide.active {
        opacity: 1;
        pointer-events: auto;
      }

      .mail-carousel-slide img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        background: #fff;
      }

      .mail-carousel-dots {
        position: absolute;
        left: 0;
        right: 0;
        bottom: 8px;
        display: flex;
        justify-content: center;
        gap: 6px;
        pointer-events: none;
      }

      .mail-carousel-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.45);
        box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
      }

      .mail-carousel-dot.active {
        background: var(--hd-accent);
      }

      .mail-carousel--broken::after {
        content: "Preview unavailable";
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        color: var(--hd-muted);
        background: var(--hd-elevated);
      }

      .mail-other-card .mail-carousel {
        max-width: none;
        justify-self: stretch;
      }

      .mail-other-section {
        display: flex;
        flex-direction: column;
        gap: var(--space-3);
        min-width: 0;
      }

      .mail-other-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: var(--space-2);
        padding: 0 var(--space-1);
      }

      .mail-other-title {
        font-size: 15px;
        font-weight: 600;
        color: var(--hd-text);
      }

      .mail-other-sub {
        font-size: 12px;
        color: var(--hd-muted);
      }

      .mail-other-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 240px), 1fr));
        gap: var(--space-3);
      }

      .mail-other-card {
        padding: var(--space-3);
        display: flex;
        flex-direction: column;
        gap: var(--space-3);
      }

      .mail-other-card.has-error {
        border-color: var(--hd-danger);
      }

      .mail-other-card-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: var(--space-2);
      }

      .mail-other-label {
        font-size: 14px;
        font-weight: 600;
        color: var(--hd-text);
      }

      .mail-other-meta {
        font-size: 11px;
        color: var(--hd-muted);
        margin-top: 2px;
      }

      .mail-other-body {
        display: flex;
        align-items: baseline;
        gap: var(--space-2);
      }

      .mail-other-count {
        font-size: clamp(28px, 7vw, 36px);
        font-weight: 700;
        color: var(--hd-accent);
        line-height: 1;
        font-variant-numeric: tabular-nums;
      }

      .mail-other-count-label {
        font-size: 12px;
        color: var(--hd-muted);
      }

      .mail-other-card .mail-preview {
        max-width: none;
        justify-self: stretch;
      }

      .mail-other-card .mail-preview img {
        max-height: 140px;
        object-fit: contain;
        background: var(--hd-bg);
      }

      .mail-other-error {
        font-size: 11px;
        color: var(--hd-danger);
      }

      /* ======================== Mail Accounts Settings ======================== */

      .mail-accounts-card {
        display: flex;
        flex-direction: column;
      }

      .mail-accounts-empty {
        text-align: center;
        padding: var(--space-5) var(--space-3);
        color: var(--hd-muted);
      }

      .mail-accounts-empty p {
        margin: var(--space-2) 0 0;
      }

      .mail-accounts-list {
        display: flex;
        flex-direction: column;
        gap: var(--space-3);
        margin-bottom: var(--space-4);
      }

      .mail-account-card {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: var(--space-3);
        padding: var(--space-3);
        background: var(--hd-elevated);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-md);
      }

      .mail-account-card.active {
        border-color: var(--hd-accent);
      }

      .mail-account-card.disabled {
        opacity: 0.65;
      }

      .mail-account-card.error {
        border-color: var(--hd-danger);
      }

      .mail-account-main {
        flex: 1;
        min-width: 0;
      }

      .mail-account-label {
        font-size: 15px;
        font-weight: 600;
        margin-bottom: 2px;
      }

      .mail-account-email {
        font-size: 13px;
        color: var(--hd-muted);
        word-break: break-all;
      }

      .mail-account-meta {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: var(--space-2);
        display: flex;
        flex-wrap: wrap;
        gap: var(--space-1);
      }

      .mail-account-dot {
        opacity: 0.5;
      }

      .mail-account-error {
        font-size: 12px;
        color: var(--hd-danger);
        margin-top: var(--space-2);
      }

      .mail-account-actions {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: var(--space-2);
        flex-shrink: 0;
      }

      .mail-add-btn {
        width: 100%;
        justify-content: center;
        border-style: dashed;
      }

      .mail-sync-footer {
        padding: var(--space-4);
        border-top: 1px solid var(--hd-border);
        background: var(--hd-surface-2);
      }

      .mail-sync-footer .hint {
        text-align: center;
        margin-top: var(--space-2);
        margin-bottom: 0;
      }

      .btn-full {
        width: 100%;
        justify-content: center;
      }

      .toggle-switch {
        position: relative;
        display: inline-block;
        width: 44px;
        height: 24px;
        flex-shrink: 0;
      }

      .toggle-switch input {
        opacity: 0;
        width: 0;
        height: 0;
      }

      .toggle-slider {
        position: absolute;
        cursor: pointer;
        inset: 0;
        background: var(--hd-border-strong);
        border-radius: 24px;
        transition: background var(--dur-fast) var(--ease);
      }

      .toggle-slider:before {
        position: absolute;
        content: "";
        height: 18px;
        width: 18px;
        left: 3px;
        bottom: 3px;
        background: #fff;
        border-radius: 50%;
        transition: transform var(--dur-fast) var(--ease);
      }

      .toggle-switch input:checked + .toggle-slider {
        background: var(--hd-accent);
      }

      .toggle-switch input:checked + .toggle-slider:before {
        transform: translateX(20px);
      }

      /* ======================== Packages ======================== */

      .packages-body {
        min-height: 80px;
      }

      .package-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
        gap: var(--space-3);
      }

      .package-grid.delivered {
        opacity: 0.85;
      }

      .package-card {
        background: var(--hd-elevated);
        border: 1px solid var(--hd-border);
        border-top-width: 3px;
        border-top-color: var(--hd-border-strong);
        border-radius: var(--radius-lg);
        padding: var(--space-3);
        transition: border-color var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
      }

      .package-card.carrier-usps { border-top-color: #004B87; }
      .package-card.carrier-ups { border-top-color: #FFB500; }
      .package-card.carrier-fedex { border-top-color: #4D148C; }

      .package-card:hover {
        border-left-color: var(--hd-border-strong);
        border-right-color: var(--hd-border-strong);
        border-bottom-color: var(--hd-border-strong);
        box-shadow: var(--shadow-md);
        transform: translateY(-2px);
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
        box-sizing: border-box;
        height: var(--header-height, 64px);
        min-height: var(--header-height, 64px);
        max-height: var(--header-height, 64px);
        padding: 0 calc(var(--space-4) + var(--safe-right)) 0 calc(var(--space-4) + var(--safe-left));
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
        font-size: clamp(15px, 1.8vw, 18px);
        line-height: 1.2;
        font-weight: 600;
        letter-spacing: -0.02em;
        color: var(--hd-text);
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

      /* ======================== Wizard ======================== */

      .wizard-modal {
        max-width: 480px;
      }

      .wizard-progress {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: var(--space-3) var(--space-4);
        background: var(--hd-surface-2);
        border-bottom: 1px solid var(--hd-border);
        gap: var(--space-2);
      }

      .wizard-step-indicator {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        min-width: 60px;
      }

      .wizard-step-num {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: var(--hd-border);
        color: var(--hd-muted);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: 600;
        transition: all var(--dur-fast) var(--ease);
      }

      .wizard-step-indicator.active .wizard-step-num {
        background: var(--hd-accent);
        color: #fff;
      }

      .wizard-step-indicator.completed .wizard-step-num {
        background: var(--hd-success);
        color: #fff;
      }

      .wizard-step-label {
        font-size: 11px;
        color: var(--hd-muted);
        font-weight: 500;
      }

      .wizard-step-indicator.active .wizard-step-label {
        color: var(--hd-text);
      }

      .wizard-step-connector {
        width: 40px;
        height: 2px;
        background: var(--hd-border);
        transition: background var(--dur-fast) var(--ease);
      }

      .wizard-step-connector.completed {
        background: var(--hd-success);
      }

      .wizard-body {
        display: flex;
        flex-direction: column;
        min-height: 200px;
      }

      .wizard-step-content {
        flex: 1;
        padding: var(--space-4);
      }

      .wizard-footer {
        display: flex;
        justify-content: space-between;
        gap: var(--space-3);
        padding: var(--space-3) var(--space-4);
        border-top: 1px solid var(--hd-border);
        background: var(--hd-surface-2);
      }

      .wizard-footer .btn:first-child {
        margin-right: auto;
      }

      .btn-ghost {
        background: transparent;
        border-color: transparent;
      }

      .btn-ghost:hover {
        background: var(--hd-hover);
        border-color: var(--hd-border);
      }

      .wizard-carrier-result {
        display: flex;
        align-items: center;
        gap: var(--space-2);
        padding: var(--space-3);
        background: var(--hd-accent-dim);
        border-radius: var(--radius-md);
        margin-top: var(--space-3);
      }

      .wizard-carrier-text {
        font-size: 14px;
        color: var(--hd-accent);
        font-weight: 500;
      }

      .wizard-error {
        padding: var(--space-3);
        background: rgba(244, 67, 54, 0.1);
        border: 1px solid var(--hd-danger);
        border-radius: var(--radius-md);
        color: var(--hd-danger);
        font-size: 13px;
        margin-top: var(--space-3);
      }

      .wizard-probing {
        display: flex;
        align-items: center;
        gap: var(--space-3);
        padding: var(--space-3);
        color: var(--hd-muted);
        font-size: 14px;
        margin-top: var(--space-3);
      }

      .spinner.small {
        width: 20px;
        height: 20px;
        border-width: 2px;
      }

      .wizard-carrier-summary {
        display: flex;
        align-items: center;
        gap: var(--space-2);
        padding: var(--space-3);
        background: var(--hd-surface-2);
        border-radius: var(--radius-md);
        margin-top: var(--space-4);
      }

      .wizard-tracking-preview {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
        font-size: 12px;
        color: var(--hd-muted);
        word-break: break-all;
      }

      .wizard-summary {
        margin-top: var(--space-4);
        padding: var(--space-3);
        background: var(--hd-surface-2);
        border-radius: var(--radius-md);
      }

      .wizard-summary-row {
        display: flex;
        align-items: center;
        gap: var(--space-2);
        font-size: 13px;
      }

      .wizard-summary-row + .wizard-summary-row {
        margin-top: var(--space-2);
        padding-top: var(--space-2);
        border-top: 1px solid var(--hd-border);
      }

      .wizard-summary-label {
        color: var(--hd-muted);
        min-width: 40px;
      }

      .link {
        color: var(--hd-accent);
        text-decoration: none;
      }

      .link:hover {
        text-decoration: underline;
      }
    `;
  }
}

customElements.define("home-delivery-panel", HomeDeliveryPanel);
