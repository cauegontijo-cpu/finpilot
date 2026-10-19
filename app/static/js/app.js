/* FinPilot — interatividade progressiva. Sem eval, sem handlers inline (CSP estrita).
 * Comportamentos são declarados via atributos data-* nos templates. */
(function () {
  "use strict";

  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";

  /* ---------------------------------------------------------------- HTMX */
  document.addEventListener("htmx:configRequest", (evt) => {
    const verb = (evt.detail.verb || "get").toUpperCase();
    if (verb !== "GET") evt.detail.headers["X-CSRF-Token"] = csrf();
  });

  // Desabilita botões de envio durante a requisição (evita cliques duplos; a garantia real é a idempotência no servidor).
  document.addEventListener("htmx:beforeRequest", (evt) => {
    const form = evt.detail.elt.closest("form");
    form?.querySelectorAll('button[type="submit"]').forEach((b) => b.setAttribute("disabled", ""));
  });
  document.addEventListener("htmx:afterRequest", (evt) => {
    const form = evt.detail.elt.closest("form");
    form?.querySelectorAll('button[type="submit"]').forEach((b) => b.removeAttribute("disabled"));
  });
  document.addEventListener("submit", (evt) => {
    const form = evt.target;
    if (form instanceof HTMLFormElement && !form.hasAttribute("hx-post") && !form.hasAttribute("hx-get")) {
      form.querySelectorAll('button[type="submit"]').forEach((b) => {
        window.setTimeout(() => b.setAttribute("disabled", ""), 0);
      });
    }
  });

  document.addEventListener("htmx:responseError", (evt) => {
    if (!evt.detail.xhr.getResponseHeader("HX-Trigger")) {
      toast("Não foi possível concluir a operação. Tente novamente.", "error");
    }
  });
  document.addEventListener("htmx:sendError", () => toast("Falha de conexão com o servidor.", "error"));

  /* ---------------------------------------------------------------- Toasts */
  const ICONS = { success: "✓", error: "!", warning: "!", info: "i" };
  function toast(message, kind) {
    const region = document.getElementById("toasts");
    if (!region || !message) return;
    const el = document.createElement("div");
    el.className = "toast-enter pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm shadow-lift";
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    const badge = document.createElement("span");
    const tone = { success: "bg-positive-soft text-positive", error: "bg-negative-soft text-negative", warning: "bg-warning-soft text-warning", info: "bg-info-soft text-info" }[kind] || "bg-info-soft text-info";
    badge.className = "mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-bold " + tone;
    badge.textContent = ICONS[kind] || "i";
    const text = document.createElement("p");
    text.className = "text-fg";
    text.textContent = message; // textContent: nunca interpreta HTML
    const close = document.createElement("button");
    close.type = "button";
    close.className = "ml-2 text-muted hover:text-fg";
    close.setAttribute("aria-label", "Fechar notificação");
    close.textContent = "×";
    close.addEventListener("click", () => el.remove());
    el.append(badge, text, close);
    region.appendChild(el);
    window.setTimeout(() => el.remove(), kind === "error" ? 8000 : 5000);
  }
  window.FinPilot = { toast };
  document.body.addEventListener("toast", (evt) => toast(evt.detail.message, evt.detail.kind));

  /* ---------------------------------------------------------------- Diálogos (modal / drawer) */
  document.addEventListener("click", (evt) => {
    const opener = evt.target.closest("[data-open-dialog]");
    if (opener) {
      const dlg = document.getElementById(opener.dataset.openDialog);
      if (dlg && typeof dlg.showModal === "function") { evt.preventDefault(); dlg.showModal(); }
      return;
    }
    const closer = evt.target.closest("[data-close-dialog]");
    if (closer) { closer.closest("dialog")?.close(); return; }
    const dlg = evt.target instanceof HTMLDialogElement ? evt.target : null;
    if (dlg && dlg.open) {
      // Clique no backdrop fecha o diálogo.
      const r = dlg.getBoundingClientRect();
      if (evt.clientX < r.left || evt.clientX > r.right || evt.clientY < r.top || evt.clientY > r.bottom) dlg.close();
    }
  });
  // Conteúdo carregado via HTMX dentro de um <dialog data-autoshow> é exibido automaticamente.
  document.addEventListener("htmx:afterSwap", (evt) => {
    const target = evt.detail.target;
    const dlg = target.closest?.("dialog[data-autoshow]") || target.querySelector?.("dialog[data-autoshow]");
    if (dlg && !dlg.open) dlg.showModal();
    initAll(target);
  });
  document.body.addEventListener("closeDialog", () => document.querySelectorAll("dialog[open]").forEach((d) => d.close()));

  /* ---------------------------------------------------------------- Confirmação de ações destrutivas */
  document.addEventListener("click", (evt) => {
    const btn = evt.target.closest("[data-confirm]");
    if (!btn || btn.dataset.confirmed === "1") return;
    evt.preventDefault();
    evt.stopImmediatePropagation();
    const dlg = document.getElementById("confirm-dialog");
    if (!dlg) return;
    dlg.querySelector("[data-confirm-message]").textContent = btn.dataset.confirm;
    const expected = btn.dataset.confirmType || "";
    const typeBox = dlg.querySelector("[data-confirm-type-box]");
    const typeInput = dlg.querySelector("[data-confirm-type-input]");
    const ok = dlg.querySelector("[data-confirm-ok]");
    typeBox.hidden = !expected;
    typeInput.value = "";
    dlg.querySelector("[data-confirm-expected]").textContent = expected;
    ok.disabled = !!expected;
    typeInput.oninput = () => { ok.disabled = typeInput.value.trim() !== expected; };
    ok.onclick = () => {
      dlg.close();
      btn.dataset.confirmed = "1";
      btn.click();
      delete btn.dataset.confirmed;
    };
    dlg.showModal();
  }, true);

  /* ---------------------------------------------------------------- Sidebar móvel */
  document.addEventListener("click", (evt) => {
    const toggle = evt.target.closest("[data-sidebar-toggle]");
    const sidebar = document.getElementById("sidebar");
    if (!sidebar) return;
    if (toggle) {
      const open = sidebar.dataset.open !== "true";
      sidebar.dataset.open = String(open);
      document.getElementById("sidebar-backdrop").hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      if (open) sidebar.querySelector("a,button")?.focus();
    } else if (evt.target.closest("#sidebar-backdrop")) {
      closeSidebar();
    }
  });
  function closeSidebar() {
    const sidebar = document.getElementById("sidebar");
    if (!sidebar || sidebar.dataset.open !== "true") return;
    sidebar.dataset.open = "false";
    document.getElementById("sidebar-backdrop").hidden = true;
    document.querySelector("[data-sidebar-toggle]")?.setAttribute("aria-expanded", "false");
  }
  document.addEventListener("keydown", (evt) => { if (evt.key === "Escape") closeSidebar(); });

  /* ---------------------------------------------------------------- Menus (disclosure) */
  document.addEventListener("click", (evt) => {
    const trigger = evt.target.closest("[data-menu-trigger]");
    document.querySelectorAll("[data-menu]").forEach((menu) => {
      if (trigger && menu.id === trigger.getAttribute("aria-controls")) return;
      menu.hidden = true;
      document.querySelector(`[aria-controls="${menu.id}"]`)?.setAttribute("aria-expanded", "false");
    });
    if (trigger) {
      const menu = document.getElementById(trigger.getAttribute("aria-controls"));
      const open = menu.hidden;
      menu.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
    }
  });

  /* ---------------------------------------------------------------- Mostrar/ocultar senha */
  document.addEventListener("click", (evt) => {
    const btn = evt.target.closest("[data-toggle-password]");
    if (!btn) return;
    const input = document.getElementById(btn.dataset.togglePassword);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-pressed", String(show));
    btn.setAttribute("aria-label", show ? "Ocultar senha" : "Mostrar senha");
  });

  /* ---------------------------------------------------------------- Campos dependentes (mostrar/ocultar) */
  function syncToggles(root) {
    root.querySelectorAll("[data-show-when]").forEach((el) => {
      const [name, values] = el.dataset.showWhen.split("=");
      const form = el.closest("form") || document;
      const field = form.querySelector(`[name="${name}"]:checked`) || form.querySelector(`select[name="${name}"], input[name="${name}"]:not([type=radio])`);
      const current = field ? (field.type === "checkbox" ? String(field.checked) : field.value) : "";
      const visible = values.split("|").includes(current);
      el.hidden = !visible;
      el.querySelectorAll("input,select,textarea").forEach((i) => { i.disabled = !visible; });
    });
  }
  document.addEventListener("change", (evt) => { if (evt.target.closest("form")) syncToggles(evt.target.closest("form")); });

  /* ---------------------------------------------------------------- Tabela de dados dos gráficos */
  document.addEventListener("click", (evt) => {
    const btn = evt.target.closest("[data-toggle-table]");
    if (!btn) return;
    const table = document.getElementById(btn.dataset.toggleTable);
    table.hidden = !table.hidden;
    btn.setAttribute("aria-expanded", String(!table.hidden));
    btn.textContent = table.hidden ? "Ver tabela de dados" : "Ocultar tabela de dados";
  });

  /* ---------------------------------------------------------------- Gráficos (Chart.js) */
  const charts = new Map();
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const brlCompact = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });

  function palette() {
    return {
      positive: cssVar("--positive"), negative: cssVar("--negative"), primary: cssVar("--primary"),
      accent: cssVar("--accent"), warning: cssVar("--warning"), muted: cssVar("--muted"),
      grid: cssVar("--chart-grid"), text: cssVar("--text-2"), surface: cssVar("--surface"),
    };
  }
  function resolveColor(c, p) { return typeof c === "string" && c.startsWith("--") ? cssVar(c) : (p[c] || c); }

  function buildChart(canvas) {
    if (!window.Chart) return;
    const source = document.getElementById(canvas.dataset.chart);
    if (!source) return;
    const spec = JSON.parse(source.textContent);
    const p = palette();
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    Chart.defaults.color = p.text;
    const money = (v) => brl.format(v / 100);
    const datasets = spec.datasets.map((d) => {
      const color = Array.isArray(d.color) ? d.color.map((c) => resolveColor(c, p)) : resolveColor(d.color, p);
      const base = { label: d.label, data: d.data, borderColor: color, backgroundColor: color, borderWidth: 2 };
      if (spec.type === "line") Object.assign(base, { backgroundColor: (Array.isArray(color) ? color[0] : color) + "22", fill: d.fill ?? true, tension: 0.35, pointRadius: d.pointRadius ?? 2, borderDash: d.dashed ? [6, 4] : [] });
      if (spec.type === "bar") Object.assign(base, { borderRadius: 6, borderWidth: 0, maxBarThickness: 28, stack: d.stack });
      if (spec.type === "doughnut") Object.assign(base, { borderColor: p.surface, borderWidth: 2, hoverOffset: 6 });
      return base;
    });
    const isPercent = spec.unit === "percent";
    const config = {
      type: spec.type,
      data: { labels: spec.labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
        indexAxis: spec.horizontal ? "y" : "x",
        interaction: { mode: spec.type === "doughnut" ? "nearest" : "index", intersect: false },
        plugins: {
          legend: { display: spec.legend !== false, position: spec.type === "doughnut" ? "right" : "bottom", labels: { usePointStyle: true, boxWidth: 8, padding: 14 } },
          tooltip: { callbacks: { label: (ctx) => {
            const raw = ctx.parsed !== null && typeof ctx.parsed === "object" ? (spec.horizontal ? ctx.parsed.x : ctx.parsed.y) : ctx.parsed;
            return `${ctx.dataset.label || ctx.label}: ${isPercent ? raw.toLocaleString("pt-BR") + "%" : money(raw)}`;
          } } },
        },
        cutout: spec.type === "doughnut" ? "68%" : undefined,
        scales: spec.type === "doughnut" ? {} : {
          x: { stacked: !!spec.stacked, grid: { display: !!spec.horizontal, color: p.grid }, border: { display: false },
               ticks: spec.horizontal ? { callback: (v) => isPercent ? v + "%" : brlCompact.format(v / 100) } : {} },
          y: { stacked: !!spec.stacked, grid: { color: p.grid, display: !spec.horizontal }, border: { display: false },
               ticks: spec.horizontal ? {} : { callback: (v) => isPercent ? v + "%" : brlCompact.format(v / 100) } },
        },
      },
    };
    charts.get(canvas)?.destroy();
    charts.set(canvas, new Chart(canvas, config));
  }

  function initCharts(root) {
    for (const [canvas, chart] of charts) if (!document.body.contains(canvas)) { chart.destroy(); charts.delete(canvas); }
    root.querySelectorAll?.("canvas[data-chart]").forEach(buildChart);
  }
  document.addEventListener("htmx:beforeSwap", () => {
    for (const [canvas, chart] of charts) if (!document.body.contains(canvas)) { chart.destroy(); charts.delete(canvas); }
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => initCharts(document));

  /* ---------------------------------------------------------------- Auto-submit de filtros */
  document.addEventListener("change", (evt) => {
    const form = evt.target.closest("form[data-autosubmit]");
    if (form && window.htmx) htmx.trigger(form, "submit");
  });

  function initAll(root) { syncToggles(root); initCharts(root); }
  document.addEventListener("DOMContentLoaded", () => {
    initAll(document);
    const flash = document.getElementById("flash");
    if (flash) toast(flash.content.textContent.trim(), flash.dataset.kind);
  });
})();
