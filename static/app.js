(() => {
  "use strict";
  const toggle = document.getElementById("notification-toggle");
  const dropdown = document.getElementById("notification-dropdown");
  const content = document.getElementById("notification-dropdown-content");
  const toastStack = document.getElementById("live-notification-stack");
  if (!toggle || !dropdown || !content) return;
  let initialized = false;
  let highestSeenId = 0;
  let fetching = false;
  let audioContext = null;
  let audioEnabled = false;
  let soundEnabled = true;
  try { soundEnabled = localStorage.getItem("previna.notification.sound") !== "off"; }
  catch (_error) { /* O armazenamento pode estar bloqueado pelo navegador. */ }

  function updateSoundControl() {
    if (!soundButton) return;
    const label = !soundEnabled ? "Som desativado" : (audioEnabled ? "Som ativado" : "Ativar som");
    soundButton.textContent = label;
    soundButton.setAttribute("aria-label", label);
    soundButton.title = !soundEnabled ? "Clique para ativar alertas sonoros" :
      (audioEnabled ? "Clique para desativar alertas sonoros" : "Clique uma vez para liberar o som no navegador");
    soundButton.classList.toggle("sound-muted", !soundEnabled);
    soundButton.classList.toggle("sound-ready", soundEnabled && audioEnabled);
  }

  function unlockAudio(playTestTone = false) {
    if (!soundEnabled) { updateSoundControl(); return; }
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) { audioEnabled = false; updateSoundControl(); return; }
      audioContext = audioContext || new Audio();
      const ready = audioContext.state === "suspended" ? audioContext.resume() : Promise.resolve();
      Promise.resolve(ready).then(() => {
        audioEnabled = Boolean(audioContext && audioContext.state === "running");
        updateSoundControl();
        if (playTestTone && audioEnabled) playNoticeSound();
      }).catch(() => { audioEnabled = false; updateSoundControl(); });
    } catch (_error) { audioEnabled = false; updateSoundControl(); }
  }
  document.addEventListener("pointerdown", () => unlockAudio(), { once: true });
  document.addEventListener("keydown", () => unlockAudio(), { once: true });

  const soundButton = make("button", "notification-sound-toggle", "");
  soundButton.type = "button";
  soundButton.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    try { localStorage.setItem("previna.notification.sound", soundEnabled ? "on" : "off"); }
    catch (_error) { /* A configuração permanece ativa apenas durante esta sessão. */ }
    if (soundEnabled) unlockAudio(true);
    else updateSoundControl();
  });
  const dropdownHeader = dropdown.querySelector(".notification-dropdown-header");
  if (dropdownHeader) dropdownHeader.insertBefore(soundButton, dropdownHeader.querySelector("a"));
  updateSoundControl();

  function playNoticeSound() {
    if (!soundEnabled || !audioEnabled || !audioContext) return;
    try {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(740, audioContext.currentTime);
      oscillator.frequency.setValueAtTime(560, audioContext.currentTime + 0.12);
      gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.07, audioContext.currentTime + 0.025);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.28);
      oscillator.connect(gain); gain.connect(audioContext.destination);
      oscillator.start(); oscillator.stop(audioContext.currentTime + 0.3);
    } catch (_error) { /* Falha de áudio não bloqueia a interface. */ }
  }

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className || "";
    node.textContent = text || "";
    return node;
  };
  const safeCount = (n) => n > 99 ? "99+" : String(n);

  function updateBadges(count) {
    let top = toggle.querySelector(".topbar-notification-count");
    if (count > 0) {
      if (!top) { top = make("span", "topbar-notification-count"); toggle.appendChild(top); }
      top.textContent = safeCount(count);
    } else if (top) top.remove();
    const side = document.querySelector(".side-nav-item[href*='/notificacoes']");
    if (side) {
      let badge = side.querySelector(".side-nav-count");
      if (count > 0) {
        if (!badge) { badge = make("span", "side-nav-count"); badge.id = "sidebar-notification-count"; side.appendChild(badge); }
        badge.textContent = safeCount(count);
      } else if (badge) badge.remove();
    }
    toggle.setAttribute("aria-label", count > 0 ? "Notificações, " + count + " não lidas" : "Notificações");
  }
  function dateText(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? (value || "") :
      new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
  }
  function renderList(items) {
    content.replaceChildren();
    if (!items.length) { content.appendChild(make("p", "notification-dropdown-empty", "Nenhuma notificação recente.")); return; }
    items.forEach((item) => {
      const link = document.createElement("a");
      link.className = "notification-preview-item" + (item.is_read ? "" : " notification-preview-unread");
      link.href = item.open_url;
      const heading = make("div", "notification-preview-heading");
      heading.appendChild(make("strong", "notification-preview-title", item.title));
      if (!item.is_read) heading.appendChild(make("span", "notification-preview-dot", "Nova"));
      link.appendChild(heading);
      link.appendChild(make("p", "notification-preview-message", item.message));
      link.appendChild(make("time", "notification-preview-date", dateText(item.created_at)));
      content.appendChild(link);
    });
  }
  function toast(item) {
    if (!toastStack) return;
    const node = make("section", "live-toast");
    node.setAttribute("role", "status");
    node.appendChild(make("strong", "", item.title));
    node.appendChild(make("p", "", item.message));
    const open = document.createElement("a"); open.href = item.open_url; open.textContent = "Abrir atualização"; node.appendChild(open);
    const close = make("button", "live-toast-close", "×");
    close.type = "button"; close.setAttribute("aria-label", "Fechar notificação");
    close.addEventListener("click", () => node.remove()); node.appendChild(close);
    toastStack.prepend(node);
    while (toastStack.children.length > 3) toastStack.lastElementChild.remove();
    window.setTimeout(() => node.remove(), 9000);
  }

  async function refresh() {
    if (fetching) return;
    fetching = true;
    try {
      const response = await fetch("/api/notificacoes/resumo", {
        credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }
      });
      if (!response.ok) throw new Error("Falha ao buscar notificações.");
      const data = await response.json();
      updateBadges(Number(data.unread_count || 0));
      const items = Array.isArray(data.latest) ? data.latest : [];
      renderList(items);
      if (!initialized) {
        highestSeenId = items.reduce((max, item) => Math.max(max, Number(item.id || 0)), 0);
        initialized = true;
      } else {
        const fresh = items.filter((item) => Number(item.id || 0) > highestSeenId);
        if (fresh.length) {
          fresh.slice(0, 3).reverse().forEach(toast);
          playNoticeSound();
          highestSeenId = fresh.reduce((max, item) => Math.max(max, Number(item.id || 0)), highestSeenId);
        }
      }
    } catch (_error) {
      if (!content.children.length || content.querySelector(".notification-skeleton")) {
        content.replaceChildren(make("p", "notification-dropdown-error", "Não foi possível carregar as notificações. Tente novamente."));
      }
    } finally { fetching = false; }
  }

  toggle.addEventListener("click", async () => {
    unlockAudio();
    const open = dropdown.hidden;
    dropdown.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) await refresh();
  });
  document.addEventListener("click", (event) => {
    if (!dropdown.hidden && !dropdown.contains(event.target) && !toggle.contains(event.target)) {
      dropdown.hidden = true; toggle.setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dropdown.hidden) {
      dropdown.hidden = true; toggle.setAttribute("aria-expanded", "false"); toggle.focus();
    }
  });
  refresh();
  window.setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 30000);
})();
