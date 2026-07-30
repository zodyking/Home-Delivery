/**
 * Home Delivery Panel - Vanilla JS for HA custom panel / standalone Ingress
 * Design aligned with home-weather: topbar + gear settings, dashboard layout.
 */
const PANEL_VERSION = "0.0.15";

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
    this._mailHistory = [];
    this._historyLoading = false;
    this._historySelectedDate = null;
    this._historyMonth = null; // Date at first of visible month
    this._historyAccountId = null;
    this._expandedSections = new Set(["general", "media-players"]);
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

  _getMailCounts(account) {
    if (!account) {
      return { mailpieces: 0, packages: 0, total: 0 };
    }

    const hasSplit = account.mailpiece_count != null || account.package_count != null;
    if (hasSplit) {
      const mailpieces = Number(account.mailpiece_count) || 0;
      const packages = Number(account.package_count) || 0;
      return { mailpieces, packages, total: mailpieces + packages };
    }

    const total = Number(account.piece_count) || 0;
    return { mailpieces: total, packages: 0, total };
  }

  _formatMailCountSummary(counts) {
    const parts = [];
    if (counts.mailpieces > 0) {
      parts.push(`${counts.mailpieces} mailpiece${counts.mailpieces === 1 ? "" : "s"}`);
    }
    if (counts.packages > 0) {
      parts.push(`${counts.packages} inbound package${counts.packages === 1 ? "" : "s"}`);
    }
    return parts.length ? parts.join(", ") : "nothing arriving today";
  }

  _renderMailCountBlocks(counts) {
    return `
      <div class="mail-hero-counts" aria-label="Today's mail and inbound packages">
        <div class="mail-count-block">
          <div class="mail-count-large">${counts.mailpieces}</div>
          <div class="mail-count-label">mailpiece${counts.mailpieces === 1 ? "" : "s"}</div>
        </div>
        <div class="mail-count-block">
          <div class="mail-count-large mail-count-large--package">${counts.packages}</div>
          <div class="mail-count-label">inbound package${counts.packages === 1 ? "" : "s"}</div>
        </div>
      </div>
    `;
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
        <span class="mail-carousel-badge">Letter #${index + 1}</span>
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
      if (!Array.isArray(this._settings.media_players)) this._settings.media_players = [];
      if (!this._settings.announcement_players || typeof this._settings.announcement_players !== "object") {
        this._settings.announcement_players = {};
      }
      if (typeof this._settings.message_prefix !== "string") {
        this._settings.message_prefix = "Message from Home Delivery";
      }
      this._packages = packagesResp.packages || [];
      this._mailState = mailResp;
      this._mediaPlayers = this._normalizeHaEntityList(entitiesResp.media_players);
      this._ttsEntities = this._normalizeHaEntityList(entitiesResp.tts_entities);

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
      let mailResp = this._mailState;
      await Promise.all([
        this._fetchApi("/api/mail/sync", { method: "POST", body: "{}" })
          .then(async () => {
            mailResp = await this._fetchApi("/api/mail");
          })
          .catch(() => {}),
        ...this._packages.filter(p => !p.delivered).map(p =>
          this._fetchApi(`/api/packages/${p.id}/refresh`, { method: "POST" })
            .then(resp => { if (resp.package) { const idx = this._packages.findIndex(x => x.id === p.id); if (idx >= 0) this._packages[idx] = resp.package; } })
            .catch(() => {})
        ),
      ]);
      if (mailResp) this._mailState = mailResp;
      const home = this._getHomeMailAccount(mailResp?.accounts || []);
      const summary = home ? this._formatMailCountSummary(this._getMailCounts(home)) : "Refreshed";
      this._showToast(summary === "Refreshed" ? summary : `Synced — ${summary}`);
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

    if (!this._settings.tts) this._settings.tts = {};
    const ttsEnabled = s.querySelector("#tts-enabled");
    const ttsStatusChange = s.querySelector("#tts-status-change");
    const ttsOutForDelivery = s.querySelector("#tts-out-for-delivery");
    const ttsDelivered = s.querySelector("#tts-delivered");
    const ttsMailArrived = s.querySelector("#tts-mail-arrived");
    const ttsStartTime = s.querySelector("#tts-start-time");
    const ttsEndTime = s.querySelector("#tts-end-time");

    if (ttsEnabled) this._settings.tts.enabled = ttsEnabled.checked;
    if (ttsStatusChange) this._settings.tts.enable_status_change = ttsStatusChange.checked;
    if (ttsOutForDelivery) this._settings.tts.enable_out_for_delivery = ttsOutForDelivery.checked;
    if (ttsDelivered) this._settings.tts.enable_delivered = ttsDelivered.checked;
    if (ttsMailArrived) this._settings.tts.enable_mail_arrived = ttsMailArrived.checked;
    if (ttsStartTime) this._settings.tts.start_time = ttsStartTime.value;
    if (ttsEndTime) this._settings.tts.end_time = ttsEndTime.value;

    const digestHours = s.querySelector("#daily-digest-repeat-hours");
    const digestMinutes = s.querySelector("#daily-digest-repeat-minutes");
    const digestOffset = s.querySelector("#daily-digest-minute-offset");
    if (digestHours) {
      this._settings.tts.daily_digest_repeat_hours = Math.max(0, Math.min(12, parseInt(digestHours.value, 10) || 0));
    }
    if (digestMinutes) {
      this._settings.tts.daily_digest_repeat_minutes = Math.max(0, Math.min(59, parseInt(digestMinutes.value, 10) || 0));
    }
    if (digestOffset) {
      this._settings.tts.daily_digest_minute_offset = Math.max(0, Math.min(59, parseInt(digestOffset.value, 10) || 0));
    }

    const messagePrefix = s.querySelector("#message-prefix");
    if (messagePrefix) {
      this._settings.message_prefix = messagePrefix.value || "Message from Home Delivery";
    }

    const cards = s.querySelectorAll("#media-player-list .media-player-card");
    if (cards.length || s.querySelector("#media-player-list")) {
      this._settings.media_players = Array.from(cards).map((card) => {
        const entitySel = card.querySelector(".media-player-select");
        const ttsSel = card.querySelector(".media-player-tts-entity");
        const volumeSlider = card.querySelector(".media-player-volume");
        const prerollInput = card.querySelector(".media-player-preroll");
        const cacheChk = card.querySelector(".media-player-cache");
        const langInput = card.querySelector(".media-player-language");
        const optionsInput = card.querySelector(".media-player-options");
        let options = {};
        if (optionsInput?.value) {
          try { options = JSON.parse(optionsInput.value); } catch (_) {}
        }
        return {
          entity_id: entitySel?.value || "",
          tts_entity_id: ttsSel?.value || "",
          volume: parseFloat(volumeSlider?.value || 0.6),
          preroll_ms: parseInt(prerollInput?.value || 150, 10),
          cache: !!cacheChk?.checked,
          language: (langInput?.value || "").trim(),
          options,
        };
      }).filter((m) => m.entity_id);
    }

    this._settings.announcement_players = this._collectAnnouncementPlayers();

    const defaultInterval = s.querySelector("#polling-default");
    const ofdInterval = s.querySelector("#polling-ofd");

    if (!this._settings.polling) this._settings.polling = {};
    if (defaultInterval) this._settings.polling.default_interval_seconds = parseInt(defaultInterval.value) || 3600;
    if (ofdInterval) this._settings.polling.out_for_delivery_interval_seconds = parseInt(ofdInterval.value) || 300;
  }

  _collectAnnouncementPlayers() {
    const s = this.shadowRoot;
    if (!s) return this._settings.announcement_players || {};

    const result = { ...(this._settings.announcement_players || {}) };
    s.querySelectorAll(".per-speaker-list").forEach((list) => {
      const typeId = list.dataset.typeId;
      if (!typeId) return;
      if (!result[typeId]) result[typeId] = {};
      list.querySelectorAll(".per-speaker-row").forEach((row) => {
        const entityId = row.dataset.entityId;
        if (!entityId) return;
        const volumeInput = row.querySelector(".per-speaker-volume");
        const bypassInput = row.querySelector(".per-speaker-bypass-input");
        result[typeId][entityId] = {
          volume: parseFloat(volumeInput?.value ?? 0.6),
          bypass: bypassInput?.checked ?? false,
        };
      });
    });
    return result;
  }

  // ============================================================================
  // Helpers
  // ============================================================================

  _carrierClass(carrier) {
    const slug = String(carrier || "unknown").toLowerCase().replace(/[^a-z0-9]/g, "");
    return slug || "unknown";
  }

  _wizardCarrierOptions() {
    return [
      { id: "estes", label: "Estes Express" },
      { id: "ups", label: "UPS" },
      { id: "usps", label: "USPS" },
    ];
  }

  _carrierMeta(carrier) {
    const key = this._carrierClass(carrier);
    const catalog = {
      usps: {
        label: "USPS",
        shipperName: "United States Postal Service",
        service: "Ground Advantage",
        subtitle: "Package Tracking",
        logoLocal: "usps.svg",
      },
      ups: {
        label: "UPS",
        shipperName: "United Parcel Service",
        service: "Ground",
        subtitle: "Package Tracking",
        logoLocal: "ups.svg",
      },
      fedex: {
        label: "FedEx",
        shipperName: "FedEx",
        service: "Ground",
        subtitle: "Package Tracking",
        logoLocal: "fedex.svg",
      },
      estes: {
        label: "Estes",
        shipperName: "Estes Express Lines",
        service: "LTL Freight",
        subtitle: "Shipment Tracking",
        logoLocal: "estes.svg",
      },
    };
    return catalog[key] || {
      label: (carrier || "?").toString().toUpperCase(),
      shipperName: (carrier || "Carrier").toString(),
      service: "Tracking",
      subtitle: "Package Tracking",
      logoLocal: null,
    };
  }

  _carrierLogoHtml(carrier) {
    const meta = this._carrierMeta(carrier);
    const local = meta.logoLocal
      ? this._apiUrl(`/assets/carriers/${meta.logoLocal}`)
      : "";
    if (!local) {
      return `<span class="carrier-logo-text">${this._esc(meta.label)}</span>`;
    }
    return `
      <img
        class="carrier-logo-img carrier-logo-${this._carrierClass(carrier)}"
        src="${this._esc(local)}"
        alt="${this._esc(meta.label)}"
        loading="lazy"
        decoding="async"
        onerror="this.style.display='none';const t=this.nextElementSibling;if(t)t.hidden=false;"
      />
      <span class="carrier-logo-text" hidden>${this._esc(meta.label)}</span>
    `;
  }

  _carrierBadge(carrier) {
    const colors = {
      usps: { bg: "#004B87", text: "#fff", label: "USPS" },
      ups: { bg: "#351C15", text: "#FFB500", label: "UPS" },
      fedex: { bg: "#4D148C", text: "#FF6600", label: "FedEx" },
      estes: { bg: "#111111", text: "#FFD200", label: "Estes" },
    };
    const key = this._carrierClass(carrier);
    const c = colors[key] || { bg: "#666", text: "#fff", label: carrier?.toUpperCase() || "?" };
    return `<span class="carrier-badge" style="background:${c.bg};color:${c.text}">${c.label}</span>`;
  }

  _packageAddressLines(destination) {
    const raw = String(destination || "").trim();
    if (!raw) return ["Address pending"];
    const parts = raw
      .split(/\n+|,\s*(?=[A-Za-z0-9])/)
      .map((p) => p.trim())
      .filter(Boolean);
    return parts.length ? parts : [raw];
  }

  _eventRegionCode(location) {
    const loc = String(location || "").toUpperCase();
    const state = loc.match(/\b([A-Z]{2})\b(?=\s*\d{5}|\s*$|,)/);
    if (state?.[1] && !["US", "OF", "AM", "PM"].includes(state[1])) return state[1];
    const city = loc.split(",")[0]?.trim();
    if (city) return city.slice(0, 3);
    return "—";
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

  async _syncMail(accountId = null, { includeHistory = false } = {}) {
    this._mailSyncing = true;
    this._mailSyncAccountId = accountId;
    this._render();
    try {
      await this._saveSettings({ silent: true });
      const body = {};
      if (accountId) body.account_id = accountId;
      if (includeHistory) body.include_history = true;
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

      const packagesResp = await this._fetchApi("/api/packages");
      this._packages = packagesResp.packages || [];
      if (includeHistory || resp.history_days) {
        await this._loadMailHistory({ silent: true });
      }

      const discovered = (resp.results || []).reduce((n, r) => n + (r.discovered_packages || 0), 0);
      const failed = (resp.results || []).filter(r => !r.success);
      if (failed.length > 0) {
        this._showToast(failed[0].error || "Sync failed", { error: true });
      } else {
        const summary = this._formatMailCountSummary(this._getMailCounts(
          (mailResp.accounts || []).find((a) => a.id === accountId)
            || this._getHomeMailAccount(mailResp.accounts || [])
        ));
        const histNote = resp.history_days
          ? `; ${resp.history_days} history day${resp.history_days === 1 ? "" : "s"} loaded`
          : "";
        this._showToast(
          discovered > 0
            ? `Synced — ${summary}; ${discovered} package${discovered === 1 ? "" : "s"} auto-discovered${histNote}`
            : `Synced — ${summary}${histNote}`
        );
      }
    } catch (err) {
      this._showToast(err.message, { error: true });
    } finally {
      this._mailSyncing = false;
      this._mailSyncAccountId = null;
      this._render();
    }
  }

  async _loadMailHistory({ silent = false } = {}) {
    this._historyLoading = true;
    if (!silent) this._render();
    try {
      const resp = await this._fetchApi("/api/mail/history");
      this._mailHistory = resp.days || [];
      if (!this._historyMonth) {
        const now = new Date();
        this._historyMonth = new Date(now.getFullYear(), now.getMonth(), 1);
      }
    } catch (err) {
      if (!silent) this._showToast(err.message || "Failed to load mail history", { error: true });
    } finally {
      this._historyLoading = false;
      if (!silent) this._render();
    }
  }

  async _openMailHistory(accountId = null) {
    this._historyAccountId = accountId || null;
    this._historySelectedDate = null;
    this._navigateTo("history");
    await this._loadMailHistory();
  }

  _getHistoryAccountLabel() {
    if (!this._historyAccountId) return "";
    const account = this._getMailAccount(this._historyAccountId);
    return account?.label || account?.imap_user || "Address";
  }

  _getHistoryTitle() {
    const accountLabel = this._getHistoryAccountLabel();
    if (this._historySelectedDate) {
      const dayTitle = this._historyDayTitle(this._historySelectedDate);
      return accountLabel ? `${dayTitle} · ${accountLabel}` : dayTitle;
    }
    return accountLabel ? `Mail History · ${accountLabel}` : "Mail History";
  }

  _sliceHistoryDayForAccount(day, accountId) {
    if (!day || !accountId) return day;
    const byAccount = day.by_account || {};
    const counts = byAccount[accountId] || {};
    const letters = (day.letters || []).filter(
      (letter) => !letter.account_id || letter.account_id === accountId,
    );
    const mailpiece_count = counts.mailpiece_count != null
      ? Number(counts.mailpiece_count) || 0
      : letters.length;
    const package_count = counts.package_count != null
      ? Number(counts.package_count) || 0
      : 0;

    return {
      ...day,
      mailpiece_count,
      package_count,
      piece_count: mailpiece_count + package_count,
      letters,
      preview_images: letters.map((letter) => letter.image).filter(Boolean),
    };
  }

  _getFilteredMailHistory() {
    const days = this._mailHistory || [];
    const accountId = this._historyAccountId;
    if (!accountId) return days;

    return days
      .map((day) => this._sliceHistoryDayForAccount(day, accountId))
      .filter((day) =>
        (day.mailpiece_count || 0) > 0
        || (day.package_count || 0) > 0
        || (day.letters || []).length > 0,
      );
  }

  _historyDayMap() {
    const map = {};
    for (const day of this._getFilteredMailHistory()) {
      if (day?.date) map[day.date] = day;
    }
    return map;
  }

  // ============================================================================
  // Add Package Wizard Helpers
  // ============================================================================

  _syncWizardFromForm() {
    const wiz = this._addPackageWizard;
    const s = this.shadowRoot;
    if (!wiz || !s) return;

    const trackingInput = s.querySelector("#wizard-tracking");
    if (trackingInput) {
      wiz.tracking = trackingInput.value?.trim() || wiz.tracking || "";
    }

    const carrierSelect = s.querySelector("#wizard-carrier");
    if (carrierSelect) {
      wiz.carrier = carrierSelect.value || wiz.carrier || "";
    }

    const recipientInput = s.querySelector("#wizard-recipient");
    if (recipientInput) {
      wiz.recipient = recipientInput.value?.trim() || "";
    }

    const destSelect = s.querySelector("#wizard-destination-select");
    if (destSelect) {
      const value = destSelect.value;
      if (value === "other") {
        wiz.destinationMode = "other";
        wiz.destinationAccountId = null;
      } else if (value) {
        wiz.destinationMode = "account";
        wiz.destinationAccountId = value;
        const account = this._getMailAccount(value);
        if (account) {
          wiz.destinationOther = account.label || wiz.destinationOther || "";
        }
      }
    }

    const otherInput = s.querySelector("#wizard-destination-other");
    if (otherInput) {
      wiz.destinationOther = otherInput.value?.trim() || wiz.destinationOther || "";
    }
  }

  _wizardRecipientInputValue(wiz) {
    const raw = (wiz.recipient || "").trim();
    if (wiz.completingDetails && this._isPlaceholderRecipient(raw)) {
      return "";
    }
    return raw;
  }

  _wizardHasKnownDestination(wiz) {
    return !!(wiz.destinationAccountId || (wiz.destinationOther || "").trim());
  }

  _resolveWizardDestination(wiz) {
    let destination = (wiz.destinationOther || "").trim();
    let destinationAccountId = wiz.destinationAccountId || null;

    if (wiz.destinationAccountId) {
      const account = this._getMailAccount(wiz.destinationAccountId);
      destination = (account?.label || destination || "").trim();
      destinationAccountId = wiz.destinationAccountId;
    }

    return { destination, destinationAccountId };
  }

  _isPlaceholderRecipient(name) {
    const value = (name || "").trim().toLowerCase();
    return !value || value === "someone";
  }

  _needsPackageDetails(pkg) {
    if (!pkg) return false;
    if (pkg.needs_details) return true;
    if (!pkg.auto_discovered) return false;
    return this._isPlaceholderRecipient(pkg.recipient) || !(pkg.destination || "").trim();
  }

  async _handleWizardNext() {
    const wiz = this._addPackageWizard;
    if (!wiz) return;

    const s = this.shadowRoot;

    if (wiz.step === 1) {
      const trackingInput = s.querySelector("#wizard-tracking");
      const carrierSelect = s.querySelector("#wizard-carrier");
      const tracking = trackingInput?.value?.trim();
      const carrier = carrierSelect?.value?.trim();

      if (!tracking) {
        this._showToast("Please enter a tracking number", { error: true });
        return;
      }
      if (!carrier) {
        this._showToast("Please select a carrier", { error: true });
        return;
      }

      wiz.tracking = tracking;
      wiz.carrier = carrier;
      wiz.step = 2;
      this._render();
    } else if (wiz.step === 2) {
      this._syncWizardFromForm();

      if (wiz.completingDetails && this._wizardHasKnownDestination(wiz)) {
        if (this._isPlaceholderRecipient(wiz.recipient)) {
          this._showToast("Enter a real name — Someone is only a placeholder on the card", { error: true });
          return;
        }
        await this._handleWizardSubmit();
        return;
      }

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

    this._syncWizardFromForm();

    const s = this.shadowRoot;

    let destination = "";
    let destinationAccountId = null;

    if (wiz.completingDetails && this._wizardHasKnownDestination(wiz)) {
      ({ destination, destinationAccountId } = this._resolveWizardDestination(wiz));
    } else {
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
    }

    if (!wiz.tracking || !wiz.carrier) {
      this._showToast("Missing tracking number or carrier", { error: true });
      return;
    }

    if (wiz.completingDetails && this._isPlaceholderRecipient(wiz.recipient)) {
      this._showToast("Enter a real name — Someone is only a placeholder on the card", { error: true });
      return;
    }

    if (!destination && !wiz.completingDetails) {
      this._showToast("Please choose a destination", { error: true });
      return;
    }

    wiz.submitting = true;
    this._render();

    try {
      if (wiz.existingPackageId) {
        const resp = await this._fetchApi(`/api/packages/${wiz.existingPackageId}`, {
          method: "PATCH",
          body: JSON.stringify({
            recipient: wiz.recipient || "",
            destination: destination,
            destination_account_id: destinationAccountId,
            auto_discovered: true,
          }),
        });
        const idx = this._packages.findIndex((p) => p.id === wiz.existingPackageId);
        if (idx >= 0 && resp.package) {
          this._packages[idx] = resp.package;
        }
        this._addPackageWizard = null;
        this._render();
        this._showToast("Package details saved");
      } else {
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
      }
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
      const ignoreEl = s.querySelector("#mail-ignore-sender");
      if (ignoreEl) {
        modal.ignoreSender = ignoreEl.checked;
      } else if (this._isInboxFolder(modal.folder)) {
        modal.ignoreSender = false;
      }
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
        modal.ignoreSender = !this._isInboxFolder(modal.folder);
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
      if (this._isInboxFolder(modal.folder)) {
        modal.ignoreSender = false;
      } else if (modal.ignoreSender === undefined) {
        modal.ignoreSender = true;
      }
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
            ignore_sender: !!modal.ignoreSender,
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
          ignore_sender: !!modal.ignoreSender,
          piece_count: 0,
          last_check: null,
          gif_filename: null,
          last_error: null,
        });
      }

      const wasEmpty = !isEdit && accounts.length === 1;
      this._settings.mail = { accounts };
      await this._persistMailAccounts();
      this._mailAccountModal = null;
      const mailResp = await this._fetchApi("/api/mail");
      this._mailState = mailResp;
      this._render();
      this._showToast(isEdit ? "Address updated" : "Address added");
      // First address setup: sync today + pull last 30 days of digests.
      if (wasEmpty) {
        const newId = accounts[accounts.length - 1]?.id;
        await this._syncMail(newId, { includeHistory: true });
      }
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
    if (this._addPackageWizard) {
      this._syncWizardFromForm();
    }
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

    if (this._currentView === "history") {
      s.innerHTML = `
        <style>${this._getStyles()}</style>
        <div class="settings-view ${this._isNarrow ? "narrow" : ""} ${this._isHaEmbedded() ? "ha-embedded" : ""}">
          ${this._renderHistoryMenubar()}
          <div class="settings-body">
            ${this._renderHistoryContent()}
          </div>
        </div>
      `;
      this._bindEvents();
      this._initMailCarousels();
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
        <button class="icon-btn" id="refresh-btn" aria-label="Refresh" ${this._refreshingAll || this._mailSyncing ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" class="${this._refreshingAll || this._mailSyncing ? "spinning" : ""}"><path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
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
        ${this._renderMailSections()}
        <div class="dashboard-bento">
          ${this._renderPackagesSection()}
        </div>
      </section>
    `;
  }

  _renderMailSections() {
    const mail = this._mailState || {};
    if (!mail.configured) {
      return this._renderMailAddressCard(null, { isEmpty: true });
    }

    const accounts = mail.accounts || [];
    const home = this._getHomeMailAccount(accounts);
    const others = this._getOtherMailAccounts(accounts);
    const cards = [];

    if (home) {
      cards.push(this._renderMailAddressCard(home, {
        showDisabledWarning: mail.enabled === false,
      }));
    }
    for (const account of others) {
      cards.push(this._renderMailAddressCard(account));
    }

    if (cards.length === 0) {
      return this._renderMailAddressCard(null, { isEmpty: true });
    }

    return `<div class="mail-address-stack">${cards.join("")}</div>`;
  }

  _renderMailAddressCard(account, options = {}) {
    if (options.isEmpty || !account) {
      return `
        <article class="glass card mail-hero-card mail-hero-card--empty">
          <div class="mail-hero-bg" aria-hidden="true"></div>
          <div class="mail-hero-inner">
            <div class="mail-hero-message">
              <svg viewBox="0 0 24 24" width="48" height="48" fill="currentColor" style="opacity:0.3"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
              <p>No mail addresses configured</p>
              <button class="btn btn-ghost btn-sm" data-action="configure-mail">Add Mail Address</button>
            </div>
          </div>
        </article>
      `;
    }

    const label = account.label || account.imap_user || "Address";
    const lastCheckStr = account.last_check
      ? new Date(account.last_check).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
      : "";
    const counts = this._getMailCounts(account);
    const accountIdAttr = account.id ? ` data-account-id="${this._escapeAttr(account.id)}"` : "";

    return `
      <article class="glass card mail-hero-card${account.last_error ? " mail-hero-card--error" : ""}">
        <div class="mail-hero-bg" aria-hidden="true"></div>
        <div class="mail-hero-inner">
          <div class="card-head mail-hero-head">
            <div>
              <div class="mail-hero-eyebrow">Informed Delivery</div>
              <div class="card-title">Mail Today</div>
              <div class="card-sub">${this._esc(label)}</div>
            </div>
            <button type="button" class="btn-link history-btn" data-action="open-history"${accountIdAttr} aria-label="Mail history for ${this._escapeAttr(label)}">
              History
            </button>
          </div>
          <div class="mail-hero-stage">
            <div class="mail-hero-preview">
              ${this._mailPreviewHtml(account, `${label} mail preview`)}
            </div>
            ${this._renderMailCountBlocks(counts)}
          </div>
          ${lastCheckStr ? `<div class="mail-meta">Last checked: ${lastCheckStr}</div>` : ""}
          ${account.last_error ? `<div class="mail-meta mail-meta-warn">${this._esc(account.last_error)}</div>` : ""}
          ${options.showDisabledWarning ? `<div class="mail-meta mail-meta-warn">All addresses are disabled — enable one in Settings</div>` : ""}
        </div>
      </article>
    `;
  }

  _historyDayTitle(dateKey) {
    if (!dateKey) return "Mail History";
    return new Date(`${dateKey}T12:00:00`).toLocaleDateString(undefined, {
      weekday: "short", month: "short", day: "numeric", year: "numeric",
    });
  }

  _renderHistoryMenubar() {
    const viewingDay = !!this._historySelectedDate;
    const backLabel = "Back";
    const title = viewingDay ? this._getHistoryTitle() : this._getHistoryTitle();
    const backAction = viewingDay ? "history-back-calendar" : "nav-back";
    const embeddedNarrow = this._isHaEmbedded() && this._isNarrow;
    if (embeddedNarrow) {
      return `
        <header class="topbar topbar--embedded-overlay topbar--settings-overlay">
          <button type="button" class="hd-menubar-back hd-menubar-back--compact" data-action="${backAction}" aria-label="${backLabel}">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
          </button>
          <div class="topbar-spacer" aria-hidden="true"></div>
          ${viewingDay ? "" : `
            <button type="button" class="btn-link" data-action="sync-history" ${this._historyLoading || this._mailSyncing ? "disabled" : ""}>
              ${this._historyLoading || this._mailSyncing ? "Loading…" : "Refresh"}
            </button>
          `}
        </header>
      `;
    }
    return `
      <header class="hd-menubar">
        <button type="button" class="hd-menubar-back" data-action="${backAction}" aria-label="${backLabel}">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
          <span>${backLabel}</span>
        </button>
        <div class="hd-menubar-title">${this._esc(title)}</div>
        ${viewingDay ? `<div class="hd-menubar-spacer" aria-hidden="true"></div>` : `
          <button type="button" class="btn-link" data-action="sync-history" ${this._historyLoading || this._mailSyncing ? "disabled" : ""}>
            ${this._historyLoading || this._mailSyncing ? "Loading…" : "Refresh 30 days"}
          </button>
        `}
      </header>
    `;
  }

  _renderHistoryContent() {
    if (this._historySelectedDate) {
      const day = this._historyDayMap()[this._historySelectedDate]
        || { date: this._historySelectedDate, mailpiece_count: 0, package_count: 0, letters: [] };
      return `
        <div class="history-view history-view--day">
          <div class="history-day-full card glass">
            ${this._renderHistoryDayDetail(day)}
          </div>
        </div>
      `;
    }

    const month = this._historyMonth || new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    const year = month.getFullYear();
    const monthIndex = month.getMonth();
    const monthLabel = month.toLocaleString(undefined, { month: "long", year: "numeric" });
    const firstDow = new Date(year, monthIndex, 1).getDay();
    const daysInMonth = new Date(year, monthIndex + 1, 0).getDate();
    const dayMap = this._historyDayMap();

    const cells = [];
    for (let i = 0; i < firstDow; i++) cells.push(`<div class="hist-cell hist-cell--empty"></div>`);
    for (let day = 1; day <= daysInMonth; day++) {
      const dateKey = `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      const entry = dayMap[dateKey];
      const mailN = entry?.mailpiece_count || 0;
      const pkgN = entry?.package_count || 0;
      const hasData = !!(entry && (mailN || pkgN || (entry.letters || []).length));
      cells.push(`
        <button type="button" class="hist-cell ${hasData ? "has-data" : ""}"
          data-action="select-history-day" data-date="${dateKey}" ${hasData ? "" : "disabled"}>
          <span class="hist-daynum">${day}</span>
          ${hasData ? `
            <span class="hist-counts">
              <span class="hist-mail">${mailN}m</span>
              <span class="hist-pkg">${pkgN}p</span>
            </span>
          ` : ""}
        </button>
      `);
    }

    return `
      <div class="history-view history-view--calendar">
        <div class="history-calendar card glass">
          <div class="history-cal-head">
            <button type="button" class="btn-link" data-action="history-prev-month" aria-label="Previous month">‹</button>
            <div class="history-cal-title">${this._esc(monthLabel)}</div>
            <button type="button" class="btn-link" data-action="history-next-month" aria-label="Next month">›</button>
          </div>
          <div class="history-dow">
            <span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span>
          </div>
          <div class="history-grid">
            ${this._historyLoading && !(this._mailHistory || []).length
              ? `<div class="history-loading">Loading history…</div>`
              : cells.join("")}
          </div>
          <p class="hint history-legend">m = mailpieces · p = inbound packages · tap a day for letters</p>
        </div>
      </div>
    `;
  }

  _renderHistoryDayDetail(day) {
    const letters = day.letters || [];
    const dateLabel = new Date(`${day.date}T12:00:00`).toLocaleDateString(undefined, {
      weekday: "long", month: "short", day: "numeric", year: "numeric",
    });
    return `
      <div class="history-detail-head">
        <div class="card-title">${this._esc(dateLabel)}</div>
        <div class="card-sub">${day.mailpiece_count || 0} mailpiece${(day.mailpiece_count || 0) === 1 ? "" : "s"} · ${day.package_count || 0} package${(day.package_count || 0) === 1 ? "" : "s"}</div>
      </div>
      ${letters.length === 0 ? `<p class="muted">No letter images for this day</p>` : `
        <div class="history-letters">
          ${letters.map((letter, index) => `
            <article class="history-letter">
              <div class="history-letter-preview">
                ${letter.url || letter.image ? `
                  <img src="${this._esc(this._apiUrl(letter.url || `/api/mail/image/${letter.image}`))}"
                    alt="Letter ${index + 1}" loading="lazy" />
                  <span class="mail-carousel-badge">Letter #${index + 1}</span>
                ` : `<div class="mail-preview--placeholder">No image</div>`}
              </div>
              <div class="history-letter-meta">
                <div class="detail-row">
                  <span class="detail-label">From</span>
                  <span class="detail-value">${this._esc(letter.from_name || letter.from_address?.split(",")[0] || "—")}</span>
                </div>
                ${letter.from_address ? `
                  <div class="detail-row">
                    <span class="detail-label">Sender addr</span>
                    <span class="detail-value muted">${this._esc(letter.from_address)}</span>
                  </div>
                ` : ""}
                <div class="detail-row">
                  <span class="detail-label">For</span>
                  <span class="detail-value">${this._esc(letter.to_name || letter.to_address?.split(",")[0] || "—")}</span>
                </div>
                ${letter.to_address ? `
                  <div class="detail-row">
                    <span class="detail-label">Deliver to</span>
                    <span class="detail-value muted">${this._esc(letter.to_address)}</span>
                  </div>
                ` : ""}
              </div>
            </article>
          `).join("")}
        </div>
      `}
    `;
  }

  _renderPackagesSection() {
    const packages = this._packages.slice().sort((a, b) => {
      const aDelivered = a.delivered ? 1 : 0;
      const bDelivered = b.delivered ? 1 : 0;
      if (aDelivered !== bDelivered) return aDelivered - bDelivered;
      const aNeeds = this._needsPackageDetails(a) ? 1 : 0;
      const bNeeds = this._needsPackageDetails(b) ? 1 : 0;
      return bNeeds - aNeeds;
    });
    const activeCount = packages.filter((p) => !p.delivered).length;
    const deliveredCount = packages.length - activeCount;

    return `
      <article class="glass card packages-card">
        <div class="card-head">
          <div>
            <div class="card-title">Packages</div>
            <div class="card-sub">${activeCount} active${deliveredCount ? ` · ${deliveredCount} delivered` : ""}</div>
          </div>
          <button class="btn btn-ghost btn-sm" data-action="add-package">+ Add Package</button>
        </div>
        <div class="packages-body">
          ${packages.length === 0 ? `
            <div class="empty-state">
              <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor" style="opacity:0.3"><path d="M20 8h-3V6c0-1.1-.9-2-2-2H9c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v10h20V10c0-1.1-.9-2-2-2zM9 6h6v2H9V6zm11 12H4v-3h16v3z"/></svg>
              <p>No packages being tracked</p>
              <p class="muted">Click "+ Add Package" to start</p>
            </div>
          ` : `
            <div class="package-grid">
              ${packages.map(p => this._renderPackageCard(p)).join("")}
            </div>
          `}
        </div>
      </article>
    `;
  }

  _packageStatusLabel(pkg) {
    if (pkg.delivered) return "Delivered";
    if (pkg.out_for_delivery) return "Out for delivery";
    const status = (pkg.status || "").trim();
    if (!status || /^pending$/i.test(status) || /^unknown$/i.test(status)) {
      return pkg.error ? "Needs refresh" : "On the way";
    }
    return status;
  }

  _renderPackageCard(pkg) {
    const isRefreshing = this._refreshingPackage === pkg.id;
    const needsDetails = this._needsPackageDetails(pkg);
    const forName = (pkg.recipient || "").trim();
    const showPlaceholderRecipient = this._isPlaceholderRecipient(forName);
    const statusLabel = this._packageStatusLabel(pkg);
    const statusClass = this._statusClass(pkg);
    const destination = (pkg.destination || "").trim();
    const detail = (pkg.status_detail || "").trim();
    const latest = pkg.events?.[0];
    const carrierKey = this._carrierClass(pkg.carrier);
    const meta = this._carrierMeta(pkg.carrier);
    const tracking = String(pkg.tracking_number || "");
    const addressLines = this._packageAddressLines(destination);
    const recipientDisplay = showPlaceholderRecipient
      ? (needsDetails ? "Add recipient" : "Someone")
      : forName;
    const eventTitle = detail || statusLabel || "Status update";
    const eventCode = this._eventRegionCode(latest?.location);
    const eventCodeLabel = latest?.location ? "Origin Facility" : "Awaiting Scan";
    const eventDate = latest?.date || "";
    const eventTime = latest?.time || "";
    const eventWhen = [eventDate, eventTime].filter(Boolean).join(eventDate && eventTime ? " at " : "");
    const eventLocation = (latest?.location || "").trim();
    const sourceLine = pkg.auto_discovered
      ? "Source: Informed Delivery"
      : (pkg.error ? `Error: ${pkg.error}` : "");

    return `
      <article class="package-card ${statusClass} carrier-${carrierKey}${needsDetails ? " needs-details" : ""}${pkg.delivered ? " is-delivered" : ""}" data-package-id="${pkg.id}">
        <section class="pkg-label">
          <header class="label-header">
            <div class="service-block service-block--full">
              <strong>${this._esc(meta.label || meta.service)}</strong>
              <span>${this._esc(meta.subtitle)}</span>
            </div>
          </header>

          <section class="label-meta">
            <div class="tracking-summary">
              <div class="small-label">Tracking number</div>
              <div class="tracking-number" title="${this._esc(tracking)}">${this._esc(tracking)}</div>
            </div>
          </section>

          <section class="address-section">
            <div class="ship-to">Ship To</div>
            <div class="recipient">
              <h3 class="recipient-name ${showPlaceholderRecipient ? "is-empty" : ""}">${this._esc(recipientDisplay)}</h3>
              <p class="recipient-address">
                ${addressLines.map((line) => this._esc(line)).join("<br />")}
              </p>
              <div class="delivery-status ${statusClass}">
                <span class="status-dot" aria-hidden="true"></span>
                <span>${this._esc(statusLabel)}</span>
              </div>
              ${needsDetails ? `
                <div class="discover-badge compact" title="Found in USPS Informed Delivery — add who it's for">
                  <span class="discover-ping" aria-hidden="true"></span>
                  Auto discovered
                </div>
              ` : ""}
            </div>
          </section>

          <section class="tracking-event">
            <div class="event-code">
              <div>
                <strong>${this._esc(eventCode)}</strong>
                <span>${this._esc(eventCodeLabel)}</span>
              </div>
            </div>
            <div class="event-details">
              <h4 class="event-title">${this._esc(eventTitle)}</h4>
              <div class="event-meta">
                ${eventWhen ? `${this._esc(eventWhen)}<br />` : ""}
                ${eventLocation ? `<span class="event-location">${this._esc(eventLocation)}</span><br />` : ""}
                ${sourceLine ? this._esc(sourceLine) : (pkg.delivered ? "Delivered" : "Live tracking")}
              </div>
            </div>
          </section>

          <footer class="label-footer">
            ${needsDetails ? `
              <button class="action-button primary" type="button" data-action="complete-details" data-id="${pkg.id}">
                Complete details
              </button>
            ` : `
              <button class="action-button" type="button" data-action="refresh" data-id="${pkg.id}" ${isRefreshing ? "disabled" : ""}>
                ${isRefreshing ? "..." : "Refresh"}
              </button>
            `}
            <button class="action-button ${needsDetails ? "" : "primary"}" type="button" data-action="view" data-id="${pkg.id}">
              View Details
            </button>
            <button
              class="action-button remove"
              type="button"
              data-action="delete"
              data-id="${pkg.id}"
              aria-label="Remove package"
              title="Remove package"
            >×</button>
          </footer>
        </section>
      </article>
    `;
  }

  _renderPackageDetail() {
    const pkg = this._selectedPackage;
    if (!pkg) return "";

    const lastPolled = pkg.last_polled ? this._formatRelativeTime(pkg.last_polled) : "Never";
    const eventCount = pkg.events?.length || 0;

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
            ${pkg.status_detail ? `
              <div class="detail-row">
                <span class="detail-label">Detail</span>
                <span class="detail-value">${this._esc(pkg.status_detail)}</span>
              </div>
            ` : ""}
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
            <div class="detail-row">
              <span class="detail-label">Last Polled</span>
              <span class="detail-value muted">${lastPolled}</span>
            </div>
            ${pkg.error ? `
              <div class="detail-row detail-error">
                <span class="detail-label">Error</span>
                <span class="detail-value error-text">${this._esc(pkg.error)}</span>
              </div>
            ` : ""}
            <h3 style="margin-top:20px">Tracking History <span class="muted">(${eventCount} events)</span></h3>
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
            ${pkg.tracking_url ? `
              <div class="modal-footer">
                <a href="${this._esc(pkg.tracking_url)}" target="_blank" rel="noopener" class="btn btn-sm">View on ${this._esc((pkg.carrier || "").toUpperCase())}</a>
              </div>
            ` : ""}
          </div>
        </div>
      </div>
    `;
  }

  _formatRelativeTime(isoString) {
    if (!isoString) return "Unknown";
    try {
      const date = new Date(isoString);
      const now = new Date();
      const diffMs = now - date;
      const diffSec = Math.floor(diffMs / 1000);
      const diffMin = Math.floor(diffSec / 60);
      const diffHr = Math.floor(diffMin / 60);
      const diffDays = Math.floor(diffHr / 24);

      if (diffSec < 60) return "Just now";
      if (diffMin < 60) return `${diffMin}m ago`;
      if (diffHr < 24) return `${diffHr}h ago`;
      if (diffDays < 7) return `${diffDays}d ago`;
      return date.toLocaleDateString();
    } catch {
      return isoString;
    }
  }

  _escapeAttr(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  _entityFriendlyName(entityId) {
    if (!entityId) return "Media player";
    return String(entityId).split(".").pop() || entityId;
  }

  _normalizeHaEntityList(items) {
    return (items || []).map((item) => {
      if (typeof item === "string") {
        return { entity_id: item, friendly_name: this._entityFriendlyName(item) };
      }
      const entity_id = item?.entity_id || "";
      const friendly_name = item?.friendly_name || this._entityFriendlyName(entity_id);
      return { entity_id, friendly_name };
    }).filter((item) => item.entity_id);
  }

  _haEntityId(item) {
    return typeof item === "string" ? item : (item?.entity_id || "");
  }

  _haEntityLabel(item) {
    if (typeof item === "string") return this._entityFriendlyName(item);
    return item?.friendly_name || this._entityFriendlyName(item?.entity_id);
  }

  _haEntityOptionText(item) {
    const id = this._haEntityId(item);
    const label = this._haEntityLabel(item);
    if (label && label !== id && !id.endsWith(label)) {
      return `${label} (${id})`;
    }
    return id;
  }

  _renderCollapsibleSection(id, title, subtitle, content, {
    hasToggle = false,
    toggleId = "",
    toggleChecked = false,
  } = {}) {
    const open = this._expandedSections.has(id);
    return `
      <div class="collapsible-section ${open ? "open" : ""}" data-section-id="${id}">
        <div class="collapsible-header">
          <div class="collapsible-header-left">
            ${hasToggle ? `
              <label class="toggle-switch" style="margin-right: 8px;" onclick="event.stopPropagation()">
                <input type="checkbox" id="${toggleId}" ${toggleChecked ? "checked" : ""} />
                <span class="toggle-slider"></span>
              </label>
            ` : ""}
            <div>
              <div class="collapsible-title">${title}</div>
              ${subtitle ? `<div class="collapsible-subtitle">${subtitle}</div>` : ""}
            </div>
          </div>
          <button type="button" class="collapsible-toggle" data-toggle-section="${id}" aria-label="Toggle ${title}">
            <svg class="collapsible-chevron" viewBox="0 0 24 24" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>
          </button>
        </div>
        <div class="collapsible-content">${content}</div>
      </div>
    `;
  }

  _renderPerSpeakerSection(typeId, defaultVolume = 0.6) {
    const mediaPlayers = this._settings.media_players || [];
    const announcementPlayers = this._settings.announcement_players || {};
    const typeOverrides = announcementPlayers[typeId] || {};

    if (mediaPlayers.length === 0) {
      return `
        <div class="per-speaker-empty">
          <span class="muted">No media players configured.</span>
          <button type="button" class="per-speaker-link" data-nav-section="media-players">Configure in Media Players</button>
        </div>
      `;
    }

    return `
      <div class="per-speaker-list" data-type-id="${typeId}">
        ${mediaPlayers.map((mp) => {
          const entityId = mp.entity_id || "";
          const override = typeOverrides[entityId] || {};
          const volume = override.volume !== undefined ? override.volume : (mp.volume ?? defaultVolume);
          const bypass = !!override.bypass;
          const displayName = this._entityFriendlyName(entityId);
          return `
            <div class="per-speaker-row" data-entity-id="${this._escapeAttr(entityId)}">
              <span class="per-speaker-name" title="${this._escapeAttr(entityId)}">${this._escapeAttr(displayName)}</span>
              <div class="per-speaker-controls">
                <input type="range" class="per-speaker-volume" min="0" max="1" step="0.05"
                       value="${volume}" data-type-id="${typeId}" data-entity-id="${this._escapeAttr(entityId)}"
                       ${bypass ? "disabled" : ""} />
                <span class="per-speaker-volume-val">${Math.round(volume * 100)}%</span>
                <label class="toggle-switch per-speaker-bypass">
                  <input type="checkbox" class="per-speaker-bypass-input"
                         data-type-id="${typeId}" data-entity-id="${this._escapeAttr(entityId)}"
                         ${bypass ? "checked" : ""} />
                  <span class="toggle-slider"></span>
                </label>
                <span class="per-speaker-bypass-label">Skip</span>
              </div>
            </div>
          `;
        }).join("")}
      </div>
    `;
  }

  _renderMediaPlayerCard(m, i) {
    const cardId = `media-player-${i}`;
    const title = this._entityFriendlyName(m.entity_id);
    const configured = m.tts_entity_id ? "Configured" : "Not configured";
    const subtitle = `${configured} · ${m.tts_entity_id ? m.tts_entity_id.replace(/^tts\./, "") : "No TTS"}`;
    const haPlayers = this._normalizeHaEntityList(this._mediaPlayers);
    const ttsEntities = this._normalizeHaEntityList(this._ttsEntities);
    const entityIds = [...new Set([m.entity_id, ...haPlayers.map((p) => p.entity_id)].filter(Boolean))];
    const ttsIds = [...new Set([m.tts_entity_id, ...ttsEntities.map((t) => t.entity_id)].filter(Boolean))];
    const vol = m.volume ?? 0.6;
    const optionsJson = this._escapeAttr(JSON.stringify(m.options || {}));

    const content = `
      <div class="media-player-card" data-index="${i}">
        <div class="form-group">
          <label>Media Player *</label>
          <div class="media-player-controls">
            <select class="media-player-select" data-field="entity_id">
              ${entityIds.map((id) => {
                const item = haPlayers.find((p) => p.entity_id === id) || { entity_id: id, friendly_name: this._entityFriendlyName(id) };
                return `<option value="${this._escapeAttr(id)}" ${id === m.entity_id ? "selected" : ""}>${this._esc(this._haEntityOptionText(item))}</option>`;
              }).join("")}
            </select>
            <button type="button" class="btn btn-secondary btn-icon" data-remove-media="${i}" aria-label="Remove player">Remove</button>
          </div>
        </div>
        <div class="form-group">
          <label>TTS Entity *</label>
          <select class="media-player-tts-entity" data-field="tts_entity_id">
            <option value="">-- Select TTS Entity --</option>
            ${ttsIds.map((id) => {
              const item = ttsEntities.find((t) => t.entity_id === id) || { entity_id: id, friendly_name: this._entityFriendlyName(id) };
              return `<option value="${this._escapeAttr(id)}" ${id === m.tts_entity_id ? "selected" : ""}>${this._esc(this._haEntityOptionText(item))}</option>`;
            }).join("")}
          </select>
        </div>
        <div class="form-group">
          <label for="${cardId}-volume">Announcement volume</label>
          <div class="range-slider">
            <input type="range" id="${cardId}-volume" class="media-player-volume" data-field="volume"
                   min="0" max="1" step="0.05" value="${vol}" />
            <span class="range-value">${Math.round(vol * 100)}%</span>
          </div>
          <p class="hint">Speaker is set to this level for announcements, then restored.</p>
        </div>
        <div class="playback-options-row">
          <div class="form-group">
            <label>Preroll (ms)</label>
            <input type="number" class="media-player-preroll" data-field="preroll_ms" min="0" max="2000" step="50" value="${m.preroll_ms ?? 150}" />
          </div>
          <div class="settings-toggle-row">
            <span class="inline-toggle-label">Cache TTS</span>
            <label class="toggle-switch">
              <input type="checkbox" class="media-player-cache" data-field="cache" ${m.cache ? "checked" : ""} />
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Language</label>
            <input type="text" class="media-player-language" data-field="language" placeholder="e.g. en, en-US" value="${this._escapeAttr(m.language || "")}" />
          </div>
          <div class="form-group">
            <label>Options (JSON)</label>
            <input type="text" class="media-player-options" data-field="options" placeholder='{"key": "value"}' value="${optionsJson}" />
          </div>
        </div>
        <div class="media-player-actions">
          <button type="button" class="test-tts-btn" data-test-media="${i}">Test announcement</button>
        </div>
      </div>
    `;
    return `
      <div class="settings-panel settings-panel--player">
        ${this._renderCollapsibleSection(cardId, title, subtitle, content)}
      </div>
    `;
  }

  _renderMessageTypeSection(typeId, title, subtitle, toggleId, enabled) {
    return this._renderCollapsibleSection(typeId.replace(/_/g, "-"), title, subtitle, `
      <div class="subsection-title">Per-speaker playback</div>
      <div class="per-speaker-header">
        <span>Speaker</span>
        <span>Volume</span>
        <span>Skip</span>
      </div>
      ${this._renderPerSpeakerSection(typeId, 0.6)}
      <div class="form-actions-row">
        <button type="button" class="test-tts-btn" data-test-type="${typeId}">Test announcement</button>
      </div>
    `, { hasToggle: true, toggleId, toggleChecked: enabled });
  }

  _renderDailyDigestSection(tts) {
    const repeatHours = tts.daily_digest_repeat_hours ?? 1;
    const repeatMinutes = tts.daily_digest_repeat_minutes ?? 30;
    const minuteOffset = tts.daily_digest_minute_offset ?? 0;

    return this._renderCollapsibleSection(
      "daily-digest",
      "Daily Digest",
      "Repeating mail and package summary during active hours",
      `
        <div class="form-row daily-digest-repeat-row">
          <div class="form-group">
            <label for="daily-digest-repeat-hours">Repeat every (hours)</label>
            <input type="number" id="daily-digest-repeat-hours" min="0" max="12" step="1" value="${repeatHours}" />
          </div>
          <div class="form-group">
            <label for="daily-digest-repeat-minutes">Repeat every (minutes)</label>
            <input type="number" id="daily-digest-repeat-minutes" min="0" max="59" step="5" value="${repeatMinutes}" />
          </div>
          <div class="form-group">
            <label for="daily-digest-minute-offset">Minute offset</label>
            <input type="number" id="daily-digest-minute-offset" min="0" max="59" step="1" value="${minuteOffset}" />
          </div>
        </div>
        <p class="hint">Uses the active hours above. Example: 9:00–21:00, offset 45, every 1h 30m → 9:45, 11:15, 12:45…</p>
        <div class="subsection-title">Per-speaker playback</div>
        <div class="per-speaker-header">
          <span>Speaker</span>
          <span>Volume</span>
          <span>Skip</span>
        </div>
        ${this._renderPerSpeakerSection("mail_arrived", 0.6)}
        <div class="form-actions-row">
          <button type="button" class="test-tts-btn" data-test-type="mail_arrived">Test announcement</button>
        </div>
      `,
      {
        hasToggle: true,
        toggleId: "tts-mail-arrived",
        toggleChecked: tts.enable_mail_arrived !== false,
      },
    );
  }

  _renderAnnouncementsPane(activePane) {
    const tts = this._settings.tts || {};
    const mediaPlayers = this._settings.media_players || [];
    const messagePrefix = this._settings.message_prefix || "Message from Home Delivery";
    const configuredIds = new Set(mediaPlayers.map((m) => m.entity_id).filter(Boolean));
    const availableMediaPlayers = (this._mediaPlayers || []).filter(
      (e) => !configuredIds.has(this._haEntityId(e)),
    );

    return `
      <section class="settings-pane ${activePane === "announcements" ? "active" : ""}" data-settings-pane="announcements">
        <div class="settings-pane-head">
          <div class="settings-pane-title">Announcements</div>
          <div class="settings-pane-sub">Spoken package and mail updates. Configure media players, then enable message types below.</div>
        </div>

        <div class="settings-card">
          <div class="settings-card-body">
            <div class="settings-toggle-row">
              <span class="inline-toggle-label">Enable TTS announcements</span>
              <label class="toggle-switch">
                <input type="checkbox" id="tts-enabled" ${tts.enabled ? "checked" : ""} />
                <span class="toggle-slider"></span>
              </label>
            </div>
            <div class="form-row" style="margin-top: var(--space-4);">
              <div class="form-group">
                <label for="tts-start-time">Active from</label>
                <input type="time" id="tts-start-time" value="${tts.start_time || "08:00"}" />
              </div>
              <div class="form-group">
                <label for="tts-end-time">Active until</label>
                <input type="time" id="tts-end-time" value="${tts.end_time || "21:00"}" />
              </div>
            </div>
            <p class="hint">Announcements only play between these hours</p>
          </div>
        </div>

        ${this._renderCollapsibleSection("general", "Message Intro", "Opening phrase spoken before updates", `
          <div class="form-group">
            <label for="message-prefix">Intro Message</label>
            <input type="text" id="message-prefix" placeholder="Message from Home Delivery" value="${this._escapeAttr(messagePrefix)}" />
            <p class="hint">Spoken before each announcement, followed by a comma.</p>
          </div>
        `)}

        ${this._renderCollapsibleSection("media-players", "Media Players", `${mediaPlayers.length} configured`, `
          <p class="hint">Each media player has its own TTS entity, volume, preroll, and options.</p>
          <div class="media-player-list" id="media-player-list">
            ${mediaPlayers.map((m, i) => this._renderMediaPlayerCard(m, i)).join("")}
          </div>
          <div class="form-row media-player-add-row">
            <select id="media-player-add">
              <option value="">Add media player...</option>
              ${availableMediaPlayers.map((e) => `<option value="${this._escapeAttr(this._haEntityId(e))}">${this._esc(this._haEntityOptionText(e))}</option>`).join("")}
            </select>
            <button type="button" class="btn btn-secondary" id="add-media-btn">Add</button>
          </div>
        `)}

        ${this._renderMessageTypeSection(
          "status_change",
          "Status Changes",
          "Speak when package status updates",
          "tts-status-change",
          tts.enable_status_change !== false,
        )}
        ${this._renderMessageTypeSection(
          "out_for_delivery",
          "Out for Delivery",
          "Speak when a package is out for delivery",
          "tts-out-for-delivery",
          tts.enable_out_for_delivery !== false,
        )}
        ${this._renderMessageTypeSection(
          "delivered",
          "Delivered",
          "Speak when a package is delivered",
          "tts-delivered",
          tts.enable_delivered !== false,
        )}
        ${this._renderDailyDigestSection(tts)}
      </section>
    `;
  }

  _renderSettingsContent() {
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

            ${this._renderAnnouncementsPane(activePane)}

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
            <div class="settings-secondary-menu" role="toolbar" aria-label="Mail actions">
              <button type="button" class="btn-link" data-action="add-mail-account">+ Add Address</button>
              <span class="settings-secondary-sep" aria-hidden="true">·</span>
              <button type="button" class="btn-link" data-action="sync-mail" ${this._mailSyncing ? "disabled" : ""}>
                ${this._mailSyncing ? "Syncing…" : "Sync inbox"}
              </button>
            </div>
            <p class="hint settings-secondary-hint">Fetch inbox now and verify IMAP credentials</p>
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
            <span>${this._formatMailCountSummary(this._getMailCounts(account))}</span>
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
          <div class="secondary-actions">
            <button type="button" class="btn-link" data-action="edit-mail-account" data-id="${account.id}">Edit</button>
            <span class="settings-secondary-sep" aria-hidden="true">·</span>
            <button type="button" class="btn-link btn-link-danger" data-action="delete-mail-account" data-id="${account.id}">Remove</button>
          </div>
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
    const isInbox = this._isInboxFolder(selectedFolder);
    const ignoreSender = modal.ignoreSender ?? !isInbox;

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
        ${isInbox ? "" : `
        <div class="form-group mail-forward-option">
          <label class="checkbox-label" for="mail-ignore-sender">
            <input type="checkbox" id="mail-ignore-sender" ${ignoreSender ? "checked" : ""} />
            <span>Include forwarded digests</span>
          </label>
          <p class="hint">Forwarded emails come from someone else, not USPS. Enable this to find digests by subject only.</p>
        </div>
        `}
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
          <div class="address-autocomplete-wrapper">
            <input type="text" id="mail-label" class="address-autocomplete-input" required
              value="${this._esc(modal.label || "")}"
              placeholder="Start typing a street address…"
              autocomplete="off"
              spellcheck="false"
              ${submitting ? "disabled" : ""} />
            <div class="address-autocomplete-dropdown" hidden></div>
          </div>
          <p class="hint">Street address only — suggestions appear as you type. City, state, and ZIP are not saved.</p>
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
          ${modal.ignoreSender ? `
          <div class="wizard-summary-row">
            <span class="wizard-summary-label">Forwarded digests</span>
            <span>Included</span>
          </div>
          ` : ""}
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
      const folder = account.folder || "INBOX";
      return {
        mode: "edit",
        id: account.id,
        step: 1,
        imapHost: account.imap_host || "imap.gmail.com",
        imapPort: account.imap_port || 993,
        imapUser: account.imap_user || "",
        imapPassword: account.imap_password ? "********" : "",
        folders: [],
        folder,
        ignoreSender: account.ignore_sender ?? !this._isInboxFolder(folder),
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
      ignoreSender: false,
      label: "",
      testing: false,
      testError: null,
      submitting: false,
    };
  }

  _isInboxFolder(folder) {
    return String(folder || "INBOX").trim().toUpperCase() === "INBOX";
  }

  _renderAddPackageWizard() {
    const wiz = this._addPackageWizard || { step: 1 };
    const step = wiz.step || 1;
    const accounts = this._getMailAccounts();

    return `
      <div class="modal-backdrop" data-action="close-wizard">
        <div class="modal wizard-modal" onclick="event.stopPropagation()">
          <div class="modal-header">
            <h2>${wiz.completingDetails ? "Complete Package Details" : "Add Package"}</h2>
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
    const selectedCarrier = wiz.carrier || "";

    return `
      <div class="wizard-step-content">
        <div class="form-group">
          <label for="wizard-carrier">Carrier *</label>
          <select id="wizard-carrier" required>
            <option value="">Select carrier…</option>
            ${this._wizardCarrierOptions().map((c) => `
              <option value="${this._esc(c.id)}" ${selectedCarrier === c.id ? "selected" : ""}>
                ${this._esc(c.label)}
              </option>
            `).join("")}
          </select>
        </div>
        <div class="form-group">
          <label for="wizard-tracking">Tracking Number *</label>
          <input type="text" id="wizard-tracking" required
            placeholder="e.g., 1ZA92T380303849608 or 103-8067337"
            value="${this._esc(wiz.tracking || "")}" />
          <p class="hint">Pick the carrier first — tracking starts immediately when you add the package.</p>
        </div>
        ${selectedCarrier ? `
          <div class="wizard-carrier-result">
            ${this._carrierBadge(selectedCarrier)}
            <span class="wizard-carrier-text">${this._esc(this._carrierMeta(selectedCarrier).label)}</span>
          </div>
        ` : ""}
      </div>
      <div class="wizard-footer">
        <button type="button" class="btn" data-action="close-wizard">Cancel</button>
        <button type="button" class="btn btn-primary" data-action="wizard-next">Next</button>
      </div>
    `;
  }

  _renderWizardStep2(wiz) {
    const knownDestination = wiz.completingDetails && this._wizardHasKnownDestination(wiz);
    const { destination: resolvedDestination } = knownDestination
      ? this._resolveWizardDestination(wiz)
      : { destination: "" };

    return `
      <div class="wizard-step-content">
        ${wiz.completingDetails ? `
          <p class="hint">Auto-discovered from USPS Informed Delivery — tell us who it's for.</p>
        ` : ""}
        ${knownDestination && resolvedDestination ? `
          <div class="wizard-summary wizard-summary--destination">
            <div class="small-label">Delivering to</div>
            <div>${this._esc(resolvedDestination)}</div>
          </div>
        ` : ""}
        <div class="form-group">
          <label for="wizard-recipient">Who is this package for?</label>
          <input type="text" id="wizard-recipient"
            placeholder="${wiz.completingDetails ? "e.g., Devon, Mom, Office" : "e.g., Mom, John, Office"}"
            value="${this._esc(this._wizardRecipientInputValue(wiz))}" />
          <p class="hint">${wiz.completingDetails ? "Required — replace the Someone placeholder with a real name" : "Optional — used for announcements"}</p>
        </div>
        <div class="wizard-carrier-summary">
          ${this._carrierBadge(wiz.carrier)}
          <span class="wizard-tracking-preview">${this._esc(wiz.tracking || "")}</span>
          ${wiz.completingDetails ? `<span class="discover-badge compact"><span class="discover-ping" aria-hidden="true"></span>Auto discovered</span>` : ""}
        </div>
      </div>
      <div class="wizard-footer">
        ${wiz.completingDetails
          ? `<button type="button" class="btn" data-action="close-wizard">Cancel</button>`
          : `<button type="button" class="btn btn-ghost" data-action="wizard-back">Back</button>`}
        <button type="button" class="btn btn-primary" data-action="${knownDestination ? "wizard-submit" : "wizard-next"}" ${wiz.submitting ? "disabled" : ""}>
          ${wiz.submitting ? "Saving..." : (knownDestination ? "Save Details" : "Next")}
        </button>
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
          ${wiz.submitting
            ? (wiz.completingDetails ? "Saving..." : "Adding...")
            : (wiz.completingDetails ? "Save Details" : "Add Package")}
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

    // Wizard carrier select — preview badge while choosing
    const carrierSelect = s.querySelector("#wizard-carrier");
    if (carrierSelect) {
      carrierSelect.addEventListener("change", () => {
        if (this._addPackageWizard) {
          const trackingInput = s.querySelector("#wizard-tracking");
          if (trackingInput) {
            this._addPackageWizard.tracking = trackingInput.value?.trim() || "";
          }
          this._addPackageWizard.carrier = carrierSelect.value || "";
          this._render();
        }
      });
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
    this._attachMailAddressAutocomplete();
    this._attachMailWizardFolderListener();
  }

  _attachMailWizardFolderListener() {
    const s = this.shadowRoot;
    const modal = this._mailAccountModal;
    if (!s || !modal || modal.step !== 2) return;

    const folderSelect = s.querySelector("#mail-imap-folder");
    if (!folderSelect || folderSelect.dataset.bound === "1") return;
    folderSelect.dataset.bound = "1";

    folderSelect.addEventListener("change", () => {
      const previousFolder = modal.folder;
      const folder = folderSelect.value?.trim() || "INBOX";
      modal.folder = folder;
      if (this._isInboxFolder(folder)) {
        modal.ignoreSender = false;
      } else if (this._isInboxFolder(previousFolder)) {
        modal.ignoreSender = true;
      }
      this._render();
    });
  }

  _attachMailAddressAutocomplete() {
    const s = this.shadowRoot;
    if (!s) return;

    const input = s.querySelector("#mail-label");
    if (!input || input.dataset.autocompleteBound === "1") return;
    input.dataset.autocompleteBound = "1";

    const wrapper = input.closest(".address-autocomplete-wrapper");
    const dropdown = wrapper?.querySelector(".address-autocomplete-dropdown");
    if (!wrapper || !dropdown) return;

    let activeIndex = -1;
    let suggestions = [];
    let debounceTimer = null;
    let abortController = null;

    const closeDropdown = () => {
      dropdown.hidden = true;
      activeIndex = -1;
      suggestions = [];
      dropdown.innerHTML = "";
    };

    const selectSuggestion = (street) => {
      input.value = street;
      if (this._mailAccountModal) {
        this._mailAccountModal.label = street;
      }
      closeDropdown();
    };

    const renderDropdown = () => {
      if (!suggestions.length) {
        closeDropdown();
        return;
      }
      dropdown.innerHTML = suggestions.map((item, index) => `
        <button type="button"
                class="address-autocomplete-option ${index === activeIndex ? "active" : ""}"
                data-index="${index}">
          ${this._esc(item.street_address)}
        </button>
      `).join("");
      dropdown.hidden = false;

      dropdown.querySelectorAll(".address-autocomplete-option").forEach((btn) => {
        btn.addEventListener("mousedown", (e) => {
          e.preventDefault();
          const index = parseInt(btn.dataset.index, 10);
          if (Number.isFinite(index) && suggestions[index]) {
            selectSuggestion(suggestions[index].street_address);
          }
        });
      });
    };

    const fetchSuggestions = async (query) => {
      if (abortController) abortController.abort();
      abortController = new AbortController();

      try {
        const base = this._getApiBase();
        const url = `${base}/api/address/autocomplete?q=${encodeURIComponent(query)}&limit=8`;
        const resp = await fetch(url, { signal: abortController.signal });
        if (!resp.ok) {
          closeDropdown();
          return;
        }
        const data = await resp.json();
        suggestions = data.suggestions || [];
        activeIndex = -1;
        renderDropdown();
      } catch (err) {
        if (err?.name !== "AbortError") {
          closeDropdown();
        }
      }
    };

    input.addEventListener("input", () => {
      const query = input.value.trim();
      if (this._mailAccountModal) {
        this._mailAccountModal.label = query;
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      if (query.length < 3) {
        closeDropdown();
        return;
      }
      debounceTimer = setTimeout(() => fetchSuggestions(query), 300);
    });

    input.addEventListener("keydown", (e) => {
      if (dropdown.hidden || !suggestions.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, suggestions.length - 1);
        renderDropdown();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        renderDropdown();
      } else if (e.key === "Enter" && activeIndex >= 0) {
        e.preventDefault();
        selectSuggestion(suggestions[activeIndex].street_address);
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    input.addEventListener("blur", () => {
      setTimeout(closeDropdown, 150);
    });
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

    s.querySelectorAll("[data-toggle-section]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const id = btn.dataset.toggleSection;
        const section = s.querySelector(`.collapsible-section[data-section-id="${id}"]`);
        if (!section) return;
        const open = section.classList.toggle("open");
        if (open) this._expandedSections.add(id);
        else this._expandedSections.delete(id);
      });
    });

    s.querySelectorAll(".collapsible-header").forEach((header) => {
      header.addEventListener("click", (e) => {
        if (e.target.closest(".toggle-switch, button, input, select, textarea, label")) return;
        const section = header.closest(".collapsible-section");
        const id = section?.dataset.sectionId;
        if (!id) return;
        const open = section.classList.toggle("open");
        if (open) this._expandedSections.add(id);
        else this._expandedSections.delete(id);
      });
    });

    s.querySelectorAll("[data-nav-section]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const id = btn.dataset.navSection;
        this._expandedSections.add(id);
        const section = s.querySelector(`.collapsible-section[data-section-id="${id}"]`);
        if (section) {
          section.classList.add("open");
          section.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      });
    });

    const addMediaBtn = s.querySelector("#add-media-btn");
    const addMediaSelect = s.querySelector("#media-player-add");
    if (addMediaBtn && addMediaSelect) {
      addMediaBtn.addEventListener("click", () => {
        const entityId = addMediaSelect.value;
        if (!entityId) return;
        this._syncSettingsFromForm();
        if (!Array.isArray(this._settings.media_players)) this._settings.media_players = [];
        if (this._settings.media_players.some((m) => m.entity_id === entityId)) return;
        this._settings.media_players.push({
          entity_id: entityId,
          tts_entity_id: this._haEntityId((this._ttsEntities || [])[0]) || "",
          volume: 0.6,
          preroll_ms: 150,
          cache: true,
          language: "",
          options: {},
        });
        this._expandedSections.add("media-players");
        this._scheduleAutoSave();
        this._render();
      });
    }

    s.querySelectorAll("[data-remove-media]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const index = parseInt(btn.dataset.removeMedia, 10);
        this._syncSettingsFromForm();
        if (!Array.isArray(this._settings.media_players)) return;
        this._settings.media_players.splice(index, 1);
        this._scheduleAutoSave();
        this._render();
      });
    });

    s.querySelectorAll(".media-player-volume, .per-speaker-volume").forEach((slider) => {
      slider.addEventListener("input", () => {
        const valEl = slider.parentElement?.querySelector(".range-value, .per-speaker-volume-val");
        if (valEl) valEl.textContent = `${Math.round(parseFloat(slider.value) * 100)}%`;
      });
    });

    s.querySelectorAll(".per-speaker-bypass-input").forEach((chk) => {
      chk.addEventListener("change", () => {
        const row = chk.closest(".per-speaker-row");
        const volume = row?.querySelector(".per-speaker-volume");
        if (volume) volume.disabled = chk.checked;
      });
    });

    s.querySelectorAll("[data-test-media]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        this._syncSettingsFromForm();
        await this._saveSettings({ silent: true });
        const index = parseInt(btn.dataset.testMedia, 10);
        const mp = (this._settings.media_players || [])[index];
        if (!mp?.entity_id) {
          this._showToast("Configure a media player first", { error: true });
          return;
        }
        try {
          btn.disabled = true;
          await this._fetchApi("/api/test-tts", {
            method: "POST",
            body: JSON.stringify({
              message: "the UPS package for Mom is out for delivery.",
              media_player: mp.entity_id,
            }),
          });
          this._showToast("Test announcement sent");
        } catch (err) {
          this._showToast(err?.message || "Test failed", { error: true });
        } finally {
          btn.disabled = false;
        }
      });
    });

    s.querySelectorAll("[data-test-type]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        this._syncSettingsFromForm();
        await this._saveSettings({ silent: true });
        try {
          btn.disabled = true;
          await this._fetchApi("/api/test-tts", {
            method: "POST",
            body: JSON.stringify({
              type_id: btn.dataset.testType,
            }),
          });
          this._showToast("Test announcement sent");
        } catch (err) {
          this._showToast(err?.message || "Test failed", { error: true });
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  async _handleAction(e, action, data) {
    switch (action) {
      case "add-package":
        this._addPackageWizard = {
          step: 1,
          tracking: "",
          carrier: "",
          recipient: "",
          destinationAccountId: null,
          destinationOther: "",
          destinationMode: null,
          submitting: false,
        };
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
      case "complete-details": {
        const pkg = this._packages.find(p => p.id === data.id);
        if (!pkg) break;
        this._addPackageWizard = {
          step: 2,
          tracking: pkg.tracking_number || "",
          carrier: pkg.carrier || null,
          recipient: this._isPlaceholderRecipient(pkg.recipient) ? "" : (pkg.recipient || ""),
          destinationAccountId: pkg.destination_account_id || pkg.source_account_id || null,
          destinationOther: pkg.destination || "",
          destinationMode: (pkg.destination_account_id || pkg.source_account_id) ? "account" : (pkg.destination ? "other" : null),
          probing: false,
          probeError: null,
          submitting: false,
          existingPackageId: pkg.id,
          completingDetails: true,
        };
        this._selectedPackage = null;
        this._render();
        break;
      }
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
        await this._syncMail(null, { includeHistory: true });
        break;
      case "open-history":
        await this._openMailHistory(data.accountId || null);
        break;
      case "sync-history":
        this._historyLoading = true;
        this._render();
        try {
          const resp = await this._fetchApi("/api/mail/history/sync", {
            method: "POST",
            body: JSON.stringify({ days: 30 }),
          });
          this._mailHistory = resp.days || [];
          this._showToast(`Loaded ${resp.count || 0} history day${(resp.count || 0) === 1 ? "" : "s"}`);
        } catch (err) {
          this._showToast(err.message || "History sync failed", { error: true });
        } finally {
          this._historyLoading = false;
          this._render();
        }
        break;
      case "select-history-day":
        this._historySelectedDate = data.date || null;
        this._render();
        break;
      case "history-back-calendar":
        this._historySelectedDate = null;
        this._render();
        break;
      case "history-prev-month": {
        const m = this._historyMonth || new Date(new Date().getFullYear(), new Date().getMonth(), 1);
        this._historyMonth = new Date(m.getFullYear(), m.getMonth() - 1, 1);
        this._render();
        break;
      }
      case "history-next-month": {
        const m = this._historyMonth || new Date(new Date().getFullYear(), new Date().getMonth(), 1);
        this._historyMonth = new Date(m.getFullYear(), m.getMonth() + 1, 1);
        this._render();
        break;
      }
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
        if (this._currentView === "history") {
          this._historyAccountId = null;
          this._navigateTo("dashboard");
        } else {
          this._navigateTo(this._settingsReturnView || "dashboard");
        }
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
        background: transparent;
        border: none;
        box-shadow: none;
        border-radius: 0;
        color: var(--hd-text);
      }

      .topbar .icon-btn:hover {
        background: transparent;
        color: var(--hd-text);
        opacity: 0.75;
      }

      .topbar .icon-btn:disabled {
        opacity: 0.4;
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
        font-size: clamp(16px, 1.9vw, 20px);
        line-height: 1.2;
        font-weight: 700;
        letter-spacing: -0.01em;
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

      .dashboard-bento .packages-card {
        min-width: 0;
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

      @media (prefers-reduced-motion: reduce) {
        .dashboard:not(.dashboard--settled) > * {
          animation: none;
        }

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
        min-height: auto;
      }

      .mail-hero-card--empty .mail-hero-inner {
        min-height: clamp(100px, 22vw, 140px);
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
        padding: var(--space-3);
        display: flex;
        flex-direction: column;
        gap: var(--space-2);
        min-height: inherit;
      }

      .mail-hero-head {
        margin-bottom: 0;
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: var(--space-2);
      }

      .mail-address-stack {
        display: flex;
        flex-direction: column;
        gap: var(--space-3);
        min-width: 0;
      }

      .mail-hero-card--error {
        border-color: color-mix(in srgb, var(--hd-danger) 55%, var(--hd-border));
      }

      .history-btn {
        font-size: 12px;
        color: var(--hd-accent);
        padding: 4px 0;
        flex-shrink: 0;
      }

      .history-btn:hover {
        color: var(--hd-accent-hover);
      }

      .history-view {
        display: block;
        width: 100%;
        min-width: 0;
      }

      .history-calendar,
      .history-day-full {
        width: 100%;
        padding: var(--space-4);
      }

      .history-cal-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--space-2);
        margin-bottom: var(--space-4);
      }

      .history-cal-title {
        font-weight: 700;
        font-size: clamp(18px, 3vw, 22px);
        letter-spacing: -0.02em;
      }

      .history-dow {
        display: grid;
        grid-template-columns: repeat(7, minmax(0, 1fr));
        gap: 6px;
        margin-bottom: 6px;
        color: var(--hd-muted);
        font-size: 12px;
        text-align: center;
        font-weight: 600;
      }

      .history-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(0, 1fr));
        gap: 6px;
      }

      .hist-cell {
        appearance: none;
        border: 1px solid var(--hd-border);
        background: transparent;
        color: var(--hd-text);
        border-radius: var(--radius-md);
        min-height: clamp(64px, 12vw, 88px);
        padding: 8px;
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        justify-content: space-between;
        cursor: pointer;
        text-align: left;
        transition: border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
      }

      .hist-cell--empty {
        border-color: transparent;
        pointer-events: none;
      }

      .hist-cell:disabled {
        opacity: 0.32;
        cursor: default;
      }

      .hist-cell.has-data {
        border-color: var(--hd-border-strong);
        background: rgba(255, 255, 255, 0.03);
      }

      .hist-cell.has-data:hover {
        border-color: var(--hd-accent);
        background: color-mix(in srgb, var(--hd-accent) 10%, transparent);
        transform: translateY(-1px);
      }

      .hist-daynum {
        font-size: 14px;
        font-weight: 700;
      }

      .hist-counts {
        display: flex;
        gap: 6px;
        font-size: 11px;
        color: var(--hd-muted);
        font-weight: 600;
      }

      .hist-mail { color: var(--hd-accent); }
      .hist-pkg { color: #7eb8ff; }

      .history-legend,
      .history-loading {
        margin-top: var(--space-3);
        color: var(--hd-muted);
        font-size: 12px;
        text-align: center;
      }

      .history-detail-head {
        margin-bottom: var(--space-4);
      }

      .history-letters {
        display: flex;
        flex-direction: column;
        gap: var(--space-4);
      }

      .history-letter {
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
        gap: var(--space-4);
        align-items: start;
        padding-top: var(--space-4);
        border-top: 1px solid var(--hd-border);
      }

      .history-letter:first-of-type {
        border-top: none;
        padding-top: 0;
      }

      @media (max-width: 700px) {
        .history-letter {
          grid-template-columns: 1fr;
        }
      }

      .hd-menubar-spacer {
        width: 1px;
        min-width: 64px;
      }

      .history-letter-preview {
        position: relative;
        border-radius: var(--radius-md);
        overflow: hidden;
        border: 1px solid var(--hd-border);
        background: #fff;
        aspect-ratio: 960 / 432;
      }

      .history-letter-preview img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        display: block;
      }

      .history-letter-meta .detail-row {
        border-bottom: none;
        padding: 3px 0;
      }

      .mail-hero-eyebrow {
        font-size: 10px;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--hd-muted);
        margin-bottom: 2px;
      }

      .mail-hero-stage {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: var(--space-2);
        min-width: 0;
        margin-left: calc(-1 * var(--space-3));
        margin-right: calc(-1 * var(--space-3));
        width: calc(100% + (2 * var(--space-3)));
      }

      .mail-hero-preview {
        width: 100%;
        display: block;
        min-height: 0;
      }

      .mail-hero-preview .mail-carousel,
      .mail-hero-preview .mail-preview {
        width: 100%;
        border-radius: 0;
      }

      .mail-hero-counts {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: var(--space-2);
        padding: 0 var(--space-3);
      }

      .mail-count-block {
        text-align: center;
        padding: var(--space-2);
        border-radius: var(--radius-sm);
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--hd-border);
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
        font-size: clamp(22px, 5.5vw, 32px);
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

      .mail-count-large--package {
        color: #7eb8ff;
      }

      .mail-preview {
        width: 100%;
        min-width: 0;
        max-width: 100%;
      }

      .mail-preview img {
        width: 100%;
        height: clamp(200px, 38vw, 300px);
        max-height: clamp(200px, 38vw, 300px);
        object-fit: contain;
        object-position: center;
        border-radius: 0;
        border: none;
        box-shadow: none;
        background: #0d0d0f;
      }

      .mail-preview--placeholder {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        height: clamp(200px, 38vw, 300px);
        max-height: clamp(200px, 38vw, 300px);
        border-radius: 0;
        border: none;
        background: var(--hd-elevated);
        color: var(--hd-muted);
        opacity: 0.45;
      }

      .mail-meta {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: var(--space-1);
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
        max-width: 100%;
        height: clamp(200px, 38vw, 300px);
        max-height: clamp(200px, 38vw, 300px);
        border-radius: 0;
        overflow: hidden;
        border: none;
        background: #0d0d0f;
        box-shadow: none;
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
        object-position: center;
        background: #0d0d0f;
      }

      .mail-carousel-badge {
        position: absolute;
        top: 8px;
        left: 8px;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: #fff;
        background: rgba(0, 0, 0, 0.65);
        border-radius: 4px;
        pointer-events: none;
        z-index: 2;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
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
        gap: var(--space-2);
        margin-bottom: 0;
      }

      .mail-account-card {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--space-3);
        padding: var(--space-2) var(--space-3);
        background: transparent;
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-sm);
      }

      .mail-account-card.active {
        border-color: var(--hd-border-strong);
      }

      .mail-account-card.disabled {
        opacity: 0.55;
      }

      .mail-account-card.error {
        border-color: var(--hd-danger);
      }

      .mail-account-main {
        flex: 1;
        min-width: 0;
      }

      .mail-account-label {
        font-size: 14px;
        font-weight: 600;
        margin-bottom: 1px;
        line-height: 1.25;
      }

      .mail-account-email {
        font-size: 12px;
        color: var(--hd-muted);
        word-break: break-all;
        line-height: 1.3;
      }

      .mail-account-meta {
        font-size: 11px;
        color: var(--hd-muted);
        margin-top: 4px;
        display: flex;
        flex-wrap: wrap;
        gap: var(--space-1);
        line-height: 1.3;
      }

      .mail-account-dot {
        opacity: 0.5;
      }

      .mail-account-error {
        font-size: 11px;
        color: var(--hd-danger);
        margin-top: 4px;
      }

      .mail-account-actions {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 6px;
        flex-shrink: 0;
      }

      .secondary-actions {
        display: flex;
        align-items: center;
        gap: 6px;
      }

      .settings-secondary-menu {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: var(--space-3);
        padding-top: var(--space-3);
        border-top: 1px solid var(--hd-border);
      }

      .settings-secondary-sep {
        color: var(--hd-muted);
        opacity: 0.55;
        font-size: 12px;
        user-select: none;
      }

      .settings-secondary-hint {
        margin: 6px 0 0;
        font-size: 11px;
      }

      .btn-link {
        appearance: none;
        background: none;
        border: none;
        padding: 0;
        margin: 0;
        font: inherit;
        font-size: 12px;
        font-weight: 500;
        color: var(--hd-muted);
        cursor: pointer;
        text-decoration: none;
        line-height: 1.2;
      }

      .btn-link:hover {
        color: var(--hd-text);
      }

      .btn-link:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }

      .btn-link-danger {
        color: var(--hd-danger);
      }

      .btn-link-danger:hover {
        color: var(--hd-danger);
        opacity: 0.85;
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
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr));
        gap: var(--space-4);
      }

      /* Package cards — dark theme, full-width header */
      .package-card {
        --label-ink: var(--hd-text);
        --label-muted: var(--hd-muted);
        position: relative;
        overflow: hidden;
        color: var(--hd-text);
        background: var(--hd-surface-2);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-md);
        padding: 0;
        box-shadow: var(--shadow-md);
        transition: transform var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease);
      }

      .package-card::before {
        display: none;
      }

      .package-card:hover {
        transform: translateY(-2px);
        box-shadow: var(--shadow-lg);
      }

      .package-card.out-for-delivery {
        box-shadow: var(--shadow-md), 0 0 0 1px color-mix(in srgb, #c98500 55%, transparent);
      }

      .package-card.delivered,
      .package-card.is-delivered {
        opacity: 0.92;
        box-shadow: var(--shadow-md), 0 0 0 1px color-mix(in srgb, #2f8f57 50%, transparent);
      }

      .package-card.error {
        box-shadow: var(--shadow-md), 0 0 0 1px color-mix(in srgb, #9e2b2b 55%, transparent);
      }

      .package-card.needs-details {
        box-shadow: var(--shadow-md), 0 0 0 1px color-mix(in srgb, #3f9a5f 55%, transparent);
      }

      .pkg-label {
        position: relative;
        z-index: 1;
        margin: 0;
        overflow: hidden;
        border: none;
        background: transparent;
        box-shadow: none;
      }

      .label-header {
        display: block;
        border-bottom: 1px solid var(--hd-border);
      }

      .service-block--full {
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: var(--space-3) var(--space-4);
        min-width: 0;
        width: 100%;
      }

      .service-block strong {
        font-size: clamp(15px, 2.4vw, 18px);
        line-height: 1.2;
        font-weight: 700;
        letter-spacing: -0.01em;
        text-transform: none;
        color: var(--hd-text);
      }

      .service-block span {
        margin-top: 4px;
        color: var(--hd-muted);
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }

      .label-meta {
        border-bottom: 1px solid var(--hd-border);
      }

      .tracking-summary {
        padding: var(--space-3) var(--space-4);
        min-width: 0;
      }

      .small-label {
        color: var(--hd-muted);
        font-size: 8px;
        font-weight: 700;
        letter-spacing: 0.13em;
        text-transform: uppercase;
      }

      .tracking-number {
        margin-top: 4px;
        font-family: "Courier New", ui-monospace, monospace;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.02em;
        word-break: break-all;
        color: var(--hd-text);
      }

      .address-section {
        display: grid;
        grid-template-columns: 48px 1fr;
        min-height: 118px;
        border-bottom: 1px solid var(--hd-border);
      }

      .ship-to {
        display: flex;
        align-items: flex-start;
        justify-content: center;
        padding-top: 14px;
        border-right: 1px solid var(--hd-border);
        color: var(--hd-muted);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        writing-mode: vertical-rl;
        transform: rotate(180deg);
      }

      .recipient {
        padding: 14px var(--space-4) 12px;
        min-width: 0;
      }

      .recipient-name {
        margin: 0;
        font-size: clamp(20px, 4vw, 24px);
        font-weight: 800;
        letter-spacing: -0.03em;
        text-transform: uppercase;
        line-height: 1.05;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: var(--hd-text);
      }

      .recipient-name.is-empty {
        color: #5dbe7a;
        font-style: italic;
        text-transform: none;
      }

      .recipient-address {
        margin: 8px 0 0;
        font-family: "Courier New", ui-monospace, monospace;
        font-size: 13px;
        font-weight: 700;
        line-height: 1.4;
        text-transform: uppercase;
        word-break: break-word;
        color: color-mix(in srgb, var(--hd-text) 88%, var(--hd-muted));
      }

      .delivery-status {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 10px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--hd-text);
      }

      .status-dot {
        width: 8px;
        height: 8px;
        flex: 0 0 auto;
        border-radius: 50%;
        background: #2f83d2;
        box-shadow: 0 0 0 3px rgba(47, 131, 210, 0.16);
      }

      .delivery-status.out-for-delivery .status-dot {
        background: #c98500;
        box-shadow: 0 0 0 3px rgba(201, 133, 0, 0.18);
      }

      .delivery-status.delivered .status-dot {
        background: #2f8f57;
        box-shadow: 0 0 0 3px rgba(47, 143, 87, 0.18);
      }

      .delivery-status.error .status-dot {
        background: #9e2b2b;
        box-shadow: 0 0 0 3px rgba(158, 43, 43, 0.18);
      }

      .tracking-event {
        display: grid;
        grid-template-columns: 92px 1fr;
        min-height: 84px;
        border-bottom: 1px solid var(--hd-border);
      }

      .event-code {
        display: grid;
        place-items: center;
        padding: 10px;
        border-right: 1px solid var(--hd-border);
        background: rgba(255, 255, 255, 0.03);
        text-align: center;
      }

      .event-code strong {
        display: block;
        font-size: 22px;
        line-height: 1;
        color: var(--hd-text);
      }

      .event-code span {
        display: block;
        margin-top: 5px;
        color: var(--hd-muted);
        font-size: 8px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }

      .event-details {
        padding: 11px var(--space-4);
        min-width: 0;
      }

      .event-title {
        margin: 0;
        font-size: 14px;
        font-weight: 700;
        text-transform: uppercase;
        line-height: 1.2;
        color: var(--hd-text);
      }

      .event-meta {
        margin-top: 6px;
        color: var(--hd-muted);
        font-family: "Courier New", ui-monospace, monospace;
        font-size: 11px;
        font-weight: 600;
        line-height: 1.45;
        word-break: break-word;
      }

      .event-location {
        color: var(--hd-text);
        font-weight: 700;
        text-transform: uppercase;
      }

      .label-footer {
        display: grid;
        grid-template-columns: 1fr 1fr 44px;
        gap: 7px;
        padding: var(--space-3) var(--space-4);
        background: transparent;
        border-top: none;
      }

      .action-button {
        min-height: 40px;
        padding: 0 10px;
        border: 1px solid var(--hd-border-strong);
        border-radius: var(--radius-sm);
        background: transparent;
        color: var(--hd-text);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        cursor: pointer;
        transition: transform 130ms ease, background 130ms ease, opacity 130ms ease;
      }

      .action-button:hover:not(:disabled) {
        transform: translateY(-1px);
        background: rgba(255, 255, 255, 0.06);
      }

      .action-button:disabled {
        opacity: 0.55;
        cursor: not-allowed;
      }

      .action-button.primary {
        background: var(--hd-accent);
        border-color: var(--hd-accent);
        color: #fff;
      }

      .action-button.primary:hover:not(:disabled) {
        background: var(--hd-accent-hover);
        border-color: var(--hd-accent-hover);
      }

      .action-button.remove {
        padding: 0;
        border-color: color-mix(in srgb, var(--hd-danger) 70%, transparent);
        color: var(--hd-danger);
        font-size: 20px;
        line-height: 1;
        background: transparent;
      }

      .action-button.remove:hover:not(:disabled) {
        background: color-mix(in srgb, var(--hd-danger) 18%, transparent);
        color: var(--hd-danger);
      }

      .package-card .discover-badge {
        margin-top: 10px;
        margin-left: 0;
        width: fit-content;
        color: #5dbe7a;
        background: color-mix(in srgb, #5dbe7a 14%, transparent);
        border-color: color-mix(in srgb, #5dbe7a 45%, transparent);
      }

      .discover-badge {
        position: relative;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-left: auto;
        padding: 3px 8px 3px 7px;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 650;
        letter-spacing: 0.02em;
        color: #3f9a5f;
        background: color-mix(in srgb, #5dbe7a 18%, var(--hd-elevated));
        border: 1px solid color-mix(in srgb, #5dbe7a 65%, transparent);
        white-space: nowrap;
      }

      .discover-badge.compact {
        margin-left: 0;
      }

      .discover-ping {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #5dbe7a;
        box-shadow: 0 0 0 0 color-mix(in srgb, #5dbe7a 55%, transparent);
        animation: discover-ping 1.8s ease-out infinite;
        flex-shrink: 0;
      }

      @keyframes discover-ping {
        0% { box-shadow: 0 0 0 0 color-mix(in srgb, #5dbe7a 55%, transparent); }
        70% { box-shadow: 0 0 0 7px transparent; }
        100% { box-shadow: 0 0 0 0 transparent; }
      }

      .btn-discover {
        color: #3f9a5f;
        border-color: color-mix(in srgb, #5dbe7a 65%, var(--hd-border));
        background: color-mix(in srgb, #5dbe7a 16%, var(--hd-elevated));
      }

      .btn-discover:hover {
        border-color: #5dbe7a;
      }

      .carrier-badge {
        padding: 3px 6px;
        border-radius: 4px;
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.02em;
      }

      @media (max-width: 420px) {
        .pkg-label {
          margin: 8px;
        }

        .label-header {
          grid-template-columns: 88px 1fr 52px;
        }

        .service-block strong {
          font-size: 12px;
        }

        .recipient-name {
          font-size: 20px;
        }

        .recipient-address {
          font-size: 12px;
        }

        .tracking-event {
          grid-template-columns: 78px 1fr;
        }

        .label-footer {
          grid-template-columns: 1fr 1fr 40px;
        }
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

      .address-autocomplete-wrapper {
        position: relative;
        width: 100%;
      }

      .address-autocomplete-input {
        width: 100%;
      }

      .address-autocomplete-dropdown {
        position: absolute;
        top: calc(100% + 4px);
        left: 0;
        right: 0;
        z-index: 40;
        max-height: 240px;
        overflow-y: auto;
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-sm);
        box-shadow: var(--shadow-md);
      }

      .address-autocomplete-option {
        display: block;
        width: 100%;
        padding: 10px 12px;
        border: none;
        background: transparent;
        color: var(--hd-text);
        font-size: 14px;
        text-align: left;
        cursor: pointer;
      }

      .address-autocomplete-option:hover,
      .address-autocomplete-option.active {
        background: color-mix(in srgb, var(--hd-accent) 12%, transparent);
      }

      .address-autocomplete-option + .address-autocomplete-option {
        border-top: 1px solid var(--hd-border);
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
      .detail-value.status-error { color: var(--hd-danger); }

      .detail-error {
        background: rgba(239, 68, 68, 0.1);
        border-radius: var(--radius-1);
        padding: var(--space-2);
        margin: var(--space-2) 0;
      }

      .error-text {
        color: var(--hd-danger);
        font-size: 12px;
      }

      .modal-footer {
        margin-top: var(--space-4);
        padding-top: var(--space-3);
        border-top: 1px solid var(--hd-border);
        text-align: center;
      }

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
        font-size: clamp(16px, 1.9vw, 20px);
        line-height: 1.2;
        font-weight: 700;
        letter-spacing: -0.01em;
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

      .daily-digest-repeat-row {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: var(--space-3);
      }

      @media (max-width: 640px) {
        .daily-digest-repeat-row {
          grid-template-columns: 1fr;
        }
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

      /* ======================== Announcements (home-weather parity) ======================== */

      .settings-pane[data-settings-pane="announcements"] {
        gap: var(--space-3);
      }

      .collapsible-section {
        background: var(--hd-surface);
        border: 1px solid var(--hd-border);
        border-radius: var(--radius-lg);
        overflow: hidden;
      }

      .collapsible-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 16px;
        cursor: pointer;
        gap: 12px;
        user-select: none;
      }

      .collapsible-header:hover {
        background: color-mix(in srgb, var(--hd-text) 4%, transparent);
      }

      .collapsible-header-left {
        display: flex;
        align-items: center;
        gap: 12px;
        flex: 1;
        min-width: 0;
      }

      .collapsible-title {
        font-size: 15px;
        font-weight: 600;
      }

      .collapsible-subtitle {
        font-size: 12px;
        color: var(--hd-muted);
        margin-top: 2px;
      }

      .collapsible-toggle {
        flex-shrink: 0;
        width: 36px;
        height: 36px;
        border: 1px solid var(--hd-border);
        background: var(--hd-input-bg);
        border-radius: var(--radius-sm);
        color: var(--hd-muted);
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0;
      }

      .collapsible-chevron {
        width: 20px;
        height: 20px;
        transition: transform 0.2s ease;
      }

      .collapsible-section.open > .collapsible-header .collapsible-chevron {
        transform: rotate(180deg);
      }

      .collapsible-content {
        display: none;
        flex-direction: column;
        gap: var(--space-3);
        padding: 0 16px 16px;
        border-top: 1px solid var(--hd-border);
        padding-top: 14px;
      }

      .collapsible-section.open > .collapsible-content {
        display: flex;
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
        background: var(--hd-input-bg);
        border-radius: 24px;
        transition: 0.2s;
        border: 1px solid var(--hd-border);
      }

      .toggle-slider:before {
        position: absolute;
        content: "";
        height: 18px;
        width: 18px;
        left: 2px;
        bottom: 2px;
        background: var(--hd-muted);
        border-radius: 50%;
        transition: 0.2s;
      }

      .toggle-switch input:checked + .toggle-slider {
        background: var(--hd-accent);
        border-color: var(--hd-accent);
      }

      .toggle-switch input:checked + .toggle-slider:before {
        transform: translateX(20px);
        background: #fff;
      }

      .settings-toggle-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 8px 0;
      }

      .inline-toggle-label {
        font-size: 14px;
        font-weight: 500;
      }

      .media-player-list {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }

      .settings-panel--player .collapsible-section {
        background: color-mix(in srgb, var(--hd-text) 3%, var(--hd-surface));
      }

      .media-player-controls {
        display: flex;
        gap: 8px;
        align-items: center;
      }

      .media-player-controls select {
        flex: 1;
        min-width: 0;
      }

      .media-player-add-row {
        align-items: center;
        margin-top: 4px;
      }

      .media-player-add-row select {
        flex: 1;
        min-width: 0;
      }

      .playback-options-row {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 12px 16px;
      }

      .playback-options-row .form-group {
        margin-bottom: 0;
        flex: 0 1 148px;
      }

      .range-slider {
        display: flex;
        align-items: center;
        gap: 10px;
      }

      .range-slider input[type="range"] {
        flex: 1;
        accent-color: var(--hd-accent);
      }

      .range-value,
      .per-speaker-volume-val {
        font-size: 12px;
        min-width: 36px;
        text-align: right;
        color: var(--hd-muted);
      }

      .media-player-actions,
      .form-actions-row {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        justify-content: flex-end;
        padding-top: 8px;
      }

      .btn-secondary {
        background: transparent;
      }

      .btn-icon {
        white-space: nowrap;
        flex-shrink: 0;
      }

      .test-tts-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 8px 14px;
        border-radius: var(--radius-sm);
        background: var(--hd-accent);
        border: 1px solid var(--hd-accent);
        color: #fff;
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
      }

      .test-tts-btn:hover {
        filter: brightness(1.05);
      }

      .test-tts-btn:disabled {
        opacity: 0.55;
        cursor: not-allowed;
      }

      .subsection-title {
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--hd-muted);
        margin-top: 4px;
      }

      .per-speaker-header {
        display: grid;
        grid-template-columns: 1fr 140px 60px;
        gap: 8px;
        padding: 6px 12px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--hd-muted);
        border-bottom: 1px solid var(--hd-border);
      }

      .per-speaker-header span:last-child {
        text-align: center;
      }

      .per-speaker-list {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }

      .per-speaker-row {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 8px;
        align-items: center;
        padding: 8px 12px;
        background: color-mix(in srgb, var(--hd-text) 4%, transparent);
        border-radius: 8px;
      }

      .per-speaker-name {
        font-size: 13px;
        font-weight: 500;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .per-speaker-controls {
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .per-speaker-volume {
        width: 80px;
        height: 6px;
        accent-color: var(--hd-accent);
        cursor: pointer;
      }

      .per-speaker-volume:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }

      .per-speaker-bypass-label {
        font-size: 11px;
        color: var(--hd-muted);
      }

      .per-speaker-empty {
        padding: 16px;
        text-align: center;
        background: color-mix(in srgb, var(--hd-text) 4%, transparent);
        border-radius: 8px;
      }

      .per-speaker-link {
        display: block;
        margin: 8px auto 0;
        background: none;
        border: none;
        color: var(--hd-accent);
        cursor: pointer;
        font-size: 13px;
        text-decoration: underline;
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
