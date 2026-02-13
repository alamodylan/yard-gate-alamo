/* ==========================================
   Yard Map v2 (Bloques → Estibas → Filas)
   - Sin modificar endpoints existentes
   - UI por paneles: stacksPanel + rowsPanel
   - Mejora: Rack real en Estibas (N1–N4 y F01–F10)
   - Mantiene drag & drop funcional (PC) + tap (touch)
   ========================================== */

const svg = document.getElementById("yardSvg");

// Left panel
const containersList = document.getElementById("containersList");
const containerSearch = document.getElementById("containerSearch");
const refreshContainersBtn = document.getElementById("refreshContainersBtn");
const touchHint = document.getElementById("touchHint");
const selectedContainerBar = document.getElementById("selectedContainerBar");
const selectedContainerText = document.getElementById("selectedContainerText");
const clearSelectedBtn = document.getElementById("clearSelectedBtn");

// New UI (map.html rediseñado)
const yardBackBtn = document.getElementById("yardBackBtn");
const yardCrumbLevel = document.getElementById("yardCrumbLevel");
const yardCrumbDetail = document.getElementById("yardCrumbDetail");
const yardActiveContainer = document.getElementById("yardActiveContainer");

const dragChip = document.getElementById("dragChip");
const dragChipCode = document.getElementById("dragChipCode");

const stacksPanel = document.getElementById("stacksPanel");
const stacksGrid = document.getElementById("stacksGrid");
const stacksBlockCode = document.getElementById("stacksBlockCode");
const stacksCloseBtn = document.getElementById("stacksCloseBtn");

const rowsPanel = document.getElementById("rowsPanel");
const rowsGrid = document.getElementById("rowsGrid");
const rowsBlockCode = document.getElementById("rowsBlockCode");
const rowsStackCode = document.getElementById("rowsStackCode");
const rowsCloseBtn = document.getElementById("rowsCloseBtn");

const confirmBar = document.getElementById("confirmBar");
const suggestedSlot = document.getElementById("suggestedSlot");
const confirmPlacementBtn = document.getElementById("confirmPlacementBtn");
const cancelPlacementBtn = document.getElementById("cancelPlacementBtn");

// Touch detection
const IS_TOUCH = ("ontouchstart" in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints > 0);

// In-memory cache
let allContainers = [];

// Remember last bays rendered (for re-render on select)
let currentBaysList = [];

// UX state machine
const VIEW = {
  BLOCKS: "BLOCKS",
  STACKS: "STACKS",
  ROWS: "ROWS",
};

let state = {
  view: VIEW.BLOCKS,
  containerId: null,
  containerCode: null,

  // Selection path
  blockCode: null,
  bayCode: null,    // estiba
  rowNumber: null,  // fila
  tier: null,       // nivel exacto

  // suggestion payload (for POST)
  suggested: null,  // { bay_code, depth_row, tier }
};

// ------------------------
// Theme helpers (dark-friendly)
// ------------------------
const THEME = {
  text: "rgba(229,231,235,.95)",
  muted: "rgba(148,163,184,.92)",
  stroke: "rgba(148,163,184,.20)",
  primaryFill: "rgba(37,99,235,0.16)",
  primaryStroke: "rgba(37,99,235,0.40)",
};

// ========================
// CONFIG Rack (v2)
// ========================
const RACK_ROWS_VISIBLE = 10;  // F01–F10 por ahora
const RACK_TIERS = 4;          // N1–N4
const ROW_ENTRY_IS_F01 = true; // F01 = entrada (visual)

// ------------------------
// Helpers
// ------------------------
function clearSvg() {
  while (svg && svg.firstChild) svg.removeChild(svg.firstChild);
}

function setView(view) {
  state.view = view;

  // Breadcrumb
  if (yardCrumbLevel) {
    yardCrumbLevel.textContent =
      view === VIEW.BLOCKS ? "Bloques" :
      view === VIEW.STACKS ? "Estibas" :
      "Filas";
  }

  if (yardCrumbDetail) {
    if (view === VIEW.BLOCKS) yardCrumbDetail.textContent = "";
    if (view === VIEW.STACKS) yardCrumbDetail.textContent = state.blockCode ? `· Bloque ${state.blockCode}` : "";
    if (view === VIEW.ROWS) {
      const b = state.blockCode ? `Bloque ${state.blockCode}` : "";
      const y = state.bayCode ? `Estiba ${state.bayCode}` : "";
      yardCrumbDetail.textContent = (b && y) ? `· ${b} · ${y}` : (b || y || "");
    }
  }

  // Back button
  if (yardBackBtn) yardBackBtn.classList.toggle("hidden", view === VIEW.BLOCKS);

  // Panels
  if (stacksPanel) stacksPanel.classList.toggle("hidden", view !== VIEW.STACKS);
  if (rowsPanel) rowsPanel.classList.toggle("hidden", view !== VIEW.ROWS);

  // Confirm bar
  if (confirmBar) confirmBar.classList.add("hidden");
  setSuggestionText("—");
}

function setSelectedBar(open, text) {
  if (!selectedContainerBar) return;
  selectedContainerBar.classList.toggle("hidden", !open);
  if (selectedContainerText) selectedContainerText.textContent = text || "—";
}

function highlightSelectedContainerInList() {
  if (!containersList) return;
  containersList.querySelectorAll(".container-item").forEach(el => {
    const id = parseInt(el.getAttribute("data-container-id"), 10);
    el.classList.toggle("is-selected", state.containerId === id);
  });
}

function setSelectedContainer(containerId, containerCode) {
  state.containerId = containerId;
  state.containerCode = containerCode;

  if (yardActiveContainer) yardActiveContainer.textContent = containerCode || `#${containerId}`;

  // Drag chip
  if (dragChip && dragChipCode) {
    dragChipCode.textContent = containerCode || `#${containerId}`;
    dragChip.classList.remove("hidden");
  }

  if (IS_TOUCH) setSelectedBar(true, `${containerCode} (#${containerId})`);

  highlightSelectedContainerInList();
}

function clearSelectedContainer() {
  state.containerId = null;
  state.containerCode = null;

  if (yardActiveContainer) yardActiveContainer.textContent = "—";
  if (dragChip) dragChip.classList.add("hidden");

  setSelectedBar(false, "");
  highlightSelectedContainerInList();

  // Clear any destination selection
  clearDestinationSelection();
}

function hasActiveContainer() {
  return !!state.containerId;
}

function fmtRow(row) {
  return `F${String(row).padStart(2, "0")}`;
}

function fmtTier(tier) {
  return `N${tier}`;
}

function setSuggestionText(text) {
  if (suggestedSlot) suggestedSlot.textContent = text || "—";
}

function shortCode(code) {
  if (!code) return "";
  if (code.length <= 8) return code;
  return `${code.slice(0, 4)}…${code.slice(-3)}`;
}

function getRowOrderVisible() {
  const rows = [];
  for (let r = 1; r <= RACK_ROWS_VISIBLE; r++) rows.push(r);
  return ROW_ENTRY_IS_F01 ? rows : rows.reverse();
}

function getTierOrderVisual() {
  // Visual rack: N4 arriba -> N1 abajo
  return [4, 3, 2, 1];
}

function clearDestinationSelection() {
  // Remove blue selection
  if (stacksGrid) stacksGrid.querySelectorAll(".rack-slot.is-selected").forEach(el => el.classList.remove("is-selected"));
  if (confirmBar) confirmBar.classList.add("hidden");
  setSuggestionText("—");

  state.bayCode = null;
  state.rowNumber = null;
  state.tier = null;
  state.suggested = null;
}

// ------------------------
// Occupancy helpers (from containers-in-yard)
// ------------------------
function buildOccupancyIndexForBlock(blockCode) {
  // Map: bay_code -> Map "row-tier" -> {id, code}
  const idx = new Map();
  const rows = Array.isArray(allContainers) ? allContainers : [];

  for (const c of rows) {
    if (!c || !c.position) continue;

    const bay = c.position.bay_code;
    const row = c.position.depth_row;
    const tier = c.position.tier;

    if (!bay || !row || !tier) continue;
    if (String(bay)[0] !== String(blockCode)) continue;

    if (!idx.has(bay)) idx.set(bay, new Map());
    idx.get(bay).set(`${row}-${tier}`, { id: c.id, code: c.code });
  }
  return idx;
}

// ------------------------
// SVG: Blocks
// ------------------------
function drawBlocks() {
  clearSvg();

  const blocks = [
    { code: "A", x: 20,  y: 20,  w: 520, h: 220 },
    { code: "B", x: 560, y: 20,  w: 520, h: 220 },
    { code: "C", x: 20,  y: 270, w: 520, h: 220 },
    { code: "D", x: 560, y: 270, w: 520, h: 220 },
  ];

  blocks.forEach(b => {
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("x", b.x);
    r.setAttribute("y", b.y);
    r.setAttribute("width", b.w);
    r.setAttribute("height", b.h);
    r.setAttribute("rx", 18);
    r.setAttribute("data-block", b.code);

    r.setAttribute("fill", THEME.primaryFill);
    r.setAttribute("stroke", THEME.primaryStroke);
    r.setAttribute("stroke-width", "2");
    r.classList.add("yard-block-dropzone");

    // Hover hint when dragging
    r.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      if (!hasActiveContainer()) return;
      r.classList.add("yard-block-highlight");
    });
    r.addEventListener("dragleave", () => r.classList.remove("yard-block-highlight"));

    // Drop on block (PC)
    r.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      r.classList.remove("yard-block-highlight");
      if (!hasActiveContainer()) return;
      await openBlock(b.code);
    });

    // Touch click on block
    r.addEventListener("click", async () => {
      if (!IS_TOUCH) return;
      await openBlock(b.code);
    });

    svg.appendChild(r);

    // Label
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", b.x + 18);
    t.setAttribute("y", b.y + 32);
    t.setAttribute("font-size", "16");
    t.setAttribute("font-weight", "800");
    t.setAttribute("fill", THEME.text);
    t.textContent = `Bloque ${b.code}`;
    svg.appendChild(t);

    // Sub label
    const t2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t2.setAttribute("x", b.x + 18);
    t2.setAttribute("y", b.y + 55);
    t2.setAttribute("font-size", "12");
    t2.setAttribute("fill", THEME.muted);
    t2.textContent = hasActiveContainer()
      ? "Suelta / toca para ver estibas"
      : "Toca para ver estibas (modo lectura)";
    svg.appendChild(t2);
  });
}

// ------------------------
// Stacks panel (Estibas) — now vertical list with mini-rack
// ------------------------
async function openBlock(blockCode) {
  state.blockCode = blockCode;
  clearDestinationSelection();

  if (stacksBlockCode) stacksBlockCode.textContent = blockCode;
  if (stacksGrid) stacksGrid.innerHTML = `<div class="hint">Cargando estibas…</div>`;

  try {
    const r = await fetch(`/api/yard/map?block=${encodeURIComponent(blockCode)}`);
    if (!r.ok) {
      if (stacksGrid) stacksGrid.innerHTML = `<div class="hint">Error cargando estibas (HTTP ${r.status}).</div>`;
      setView(VIEW.STACKS);
      return;
    }

    const data = await r.json();
    const bays = (data && data.bays) ? data.bays : [];
    bays.sort((a, b) => (a.bay_number || 0) - (b.bay_number || 0));

    currentBaysList = bays;
    renderStacksGrid(bays);
    setView(VIEW.STACKS);
  } catch (e) {
    if (stacksGrid) stacksGrid.innerHTML = `<div class="hint">Error de red cargando estibas.</div>`;
    setView(VIEW.STACKS);
  }
}

function renderStacksGrid(bays) {
  if (!stacksGrid) return;

  if (!bays || bays.length === 0) {
    stacksGrid.innerHTML = `<div class="hint">No hay estibas configuradas en este bloque.</div>`;
    return;
  }

  const occ = buildOccupancyIndexForBlock(state.blockCode);
  const rowOrder = getRowOrderVisible();
  const tierOrder = getTierOrderVisual();

  const html = bays.map(b => {
    const used = b.used || 0;
    const cap = b.capacity || 0;
    const available = cap > 0 ? (used < cap) : true;

    const badge = available ? "Disponible" : "Lleno";
    const badgeCls = available ? "badge-ok" : "badge-bad";

    const bayOcc = occ.get(b.code) || new Map();

    const header = `
      <div class="rack-header">
        <div class="rack-corner"></div>
        ${rowOrder.map(r => `<div class="rack-colhdr">${fmtRow(r)}</div>`).join("")}
      </div>
    `;

    const body = tierOrder.map(t => {
      return `
        <div class="rack-row">
          <div class="rack-tierlbl">${fmtTier(t)}</div>
          ${rowOrder.map(r => {
            const key = `${r}-${t}`;
            const item = bayOcc.get(key);

            if (item) {
              return `
                <div class="rack-slot is-occupied"
                     draggable="${IS_TOUCH ? "false" : "true"}"
                     data-action="pick-container"
                     data-container-id="${item.id}"
                     data-container-code="${item.code}"
                     data-bay="${b.code}"
                     data-row="${r}"
                     data-tier="${t}"
                     title="${item.code} · ${b.code} · ${fmtRow(r)} · ${fmtTier(t)}">
                  <span class="rack-code">${shortCode(item.code)}</span>
                </div>
              `;
            }

            const canPickDest = hasActiveContainer();
            return `
              <div class="rack-slot ${canPickDest ? "is-available" : "is-empty"}"
                   data-action="pick-destination"
                   data-bay="${b.code}"
                   data-row="${r}"
                   data-tier="${t}"
                   title="${b.code} · ${fmtRow(r)} · ${fmtTier(t)}">
              </div>
            `;
          }).join("")}
        </div>
      `;
    }).join("");

    return `
      <div class="stack-card" data-baycard="${b.code}">
        <div class="stack-card-head">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
            <div>
              <div style="font-weight:950; font-size:16px;">${b.code}</div>
              <div class="hint" style="margin-top:6px;">
                ${cap ? `${used}/${cap}` : `${used} usados`}
              </div>
            </div>
            <div class="${badgeCls}" style="font-size:11px; font-weight:900;">${badge}</div>
          </div>
          <div class="hint" style="margin-top:8px;">
            Toca un contenedor para seleccionarlo · Toca un espacio <b>verde</b> para destino
          </div>
        </div>

        <div class="rack">
          ${header}
          ${body}
        </div>
      </div>
    `;
  }).join("");

  stacksGrid.innerHTML = html;

  // Attach per-slot drag/drop behavior (drop on destination slots)
  stacksGrid.querySelectorAll(".rack-slot").forEach(slot => {
    // Make occupied slots draggable (PC) so they can be "picked" by dragstart
    if (!IS_TOUCH && slot.classList.contains("is-occupied")) {
      slot.addEventListener("dragstart", (ev) => {
        const id = parseInt(slot.getAttribute("data-container-id"), 10);
        const code = slot.getAttribute("data-container-code");
        try { ev.dataTransfer.setData("text/plain", code || String(id)); } catch (_) {}
        setSelectedContainer(id, code);
        clearDestinationSelection();
        renderStacksGrid(currentBaysList);
      });
    }

    // Allow drop on empty destination
    slot.addEventListener("dragover", (ev) => {
      if (!hasActiveContainer()) return;
      if (slot.getAttribute("data-action") !== "pick-destination") return;
      ev.preventDefault();
      slot.classList.add("yard-block-highlight");
    });

    slot.addEventListener("dragleave", () => slot.classList.remove("yard-block-highlight"));

    slot.addEventListener("drop", (ev) => {
      slot.classList.remove("yard-block-highlight");
      if (!hasActiveContainer()) return;
      if (slot.getAttribute("data-action") !== "pick-destination") return;
      ev.preventDefault();
      // emulate click on destination
      pickDestinationFromSlot(slot);
    });
  });

  // Hide confirm if no destination yet
  if (confirmBar) confirmBar.classList.add("hidden");
  setSuggestionText("—");
}

// Single click handler (delegation) for stacksGrid
function attachStacksGridClickHandler() {
  if (!stacksGrid) return;

  stacksGrid.addEventListener("click", (ev) => {
    const slot = ev.target.closest(".rack-slot");
    if (!slot) return;

    const action = slot.getAttribute("data-action");
    if (action === "pick-container") {
      const id = parseInt(slot.getAttribute("data-container-id"), 10);
      const code = slot.getAttribute("data-container-code");
      setSelectedContainer(id, code);

      // Enter "modo mover": re-render to paint empty slots as green
      clearDestinationSelection();
      renderStacksGrid(currentBaysList);
      return;
    }

    if (action === "pick-destination") {
      if (!hasActiveContainer()) return;
      pickDestinationFromSlot(slot);
      return;
    }
  });
}

function pickDestinationFromSlot(slot) {
  if (!slot || slot.classList.contains("is-occupied")) return;

  // Clear old selection
  stacksGrid.querySelectorAll(".rack-slot.is-selected").forEach(el => el.classList.remove("is-selected"));

  // Mark new selection
  slot.classList.add("is-selected");

  const bay = slot.getAttribute("data-bay");
  const row = parseInt(slot.getAttribute("data-row"), 10);
  const tier = parseInt(slot.getAttribute("data-tier"), 10);

  state.bayCode = bay;
  state.rowNumber = row;
  state.tier = tier;
  state.suggested = { bay_code: bay, depth_row: row, tier: tier };

  setSuggestionText(`${bay} · ${fmtRow(row)} · ${fmtTier(tier)}`);
  if (confirmBar) confirmBar.classList.remove("hidden");
}

// ------------------------
// Rows panel (Filas) — kept for compatibility/fallback
// ------------------------
async function openBay(bayCode) {
  state.bayCode = bayCode;
  state.rowNumber = null;
  state.tier = null;
  state.suggested = null;

  if (rowsBlockCode) rowsBlockCode.textContent = state.blockCode || "—";
  if (rowsStackCode) rowsStackCode.textContent = bayCode;

  if (rowsGrid) rowsGrid.innerHTML = `<div class="hint">Cargando filas…</div>`;
  if (confirmBar) confirmBar.classList.add("hidden");
  setSuggestionText("—");

  let rowsData = null;
  try {
    const rr = await fetch(`/api/yard/bays/${encodeURIComponent(bayCode)}/rows-availability`);
    if (rr.ok) rowsData = await rr.json();
  } catch (e) {}

  if (rowsData && rowsData.rows) {
    renderRowsGrid(rowsData.rows);
    setView(VIEW.ROWS);
    return;
  }

  renderRowsGridFallback(bayCode);
  setView(VIEW.ROWS);
  await suggestLastAvailable(bayCode);
}

function renderRowsGrid(rows) {
  if (!rowsGrid) return;

  if (!rows || rows.length === 0) {
    rowsGrid.innerHTML = `<div class="hint">No hay filas configuradas para esta estiba.</div>`;
    return;
  }

  const html = rows.map(r => {
    const maxLv = r.max_levels ?? 4;
    const usedLv = r.levels_used ?? 0;
    const isFull = r.is_full ?? (usedLv >= maxLv);
    const cls = isFull ? "yard-cell unavailable" : "yard-cell available";
    const badge = isFull ? "4/4" : `${usedLv}/${maxLv}`;

    return `
      <button type="button"
              class="${cls}"
              data-row="${r.row}"
              ${isFull ? "disabled" : ""}>
        <div style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
          <div style="font-weight:950;">${fmtRow(r.row)}</div>
          <div class="hint" style="margin:0;">${badge}</div>
        </div>
        <div class="hint" style="margin-top:6px;">
          ${isFull ? "Fila llena" : (hasActiveContainer() ? "Suelta aquí para sugerir nivel" : "Disponible")}
        </div>
      </button>
    `;
  }).join("");

  rowsGrid.innerHTML = html;

  rowsGrid.querySelectorAll("[data-row]").forEach(btn => {
    const row = parseInt(btn.getAttribute("data-row"), 10);

    btn.addEventListener("click", async () => {
      await chooseRow(row);
    });

    btn.addEventListener("dragover", (ev) => {
      if (!hasActiveContainer()) return;
      ev.preventDefault();
      btn.classList.add("yard-block-highlight");
    });

    btn.addEventListener("dragleave", () => btn.classList.remove("yard-block-highlight"));

    btn.addEventListener("drop", async (ev) => {
      btn.classList.remove("yard-block-highlight");
      if (!hasActiveContainer()) return;
      ev.preventDefault();
      await chooseRow(row);
    });
  });
}

function renderRowsGridFallback(bayCode) {
  if (!rowsGrid) return;

  rowsGrid.innerHTML = `
    <div class="hint">
      Este predio aún no expone disponibilidad por filas.
      <br>Voy a sugerirte automáticamente la última posición disponible en <b>${bayCode}</b>.
      <br><small>(Cuando agreguemos el endpoint de filas, aquí verás verde/rojo por fila)</small>
    </div>
  `;
}

async function suggestLastAvailable(bayCode) {
  state.suggested = null;
  if (confirmBar) confirmBar.classList.add("hidden");
  setSuggestionText("—");

  try {
    const r = await fetch(`/api/yard/bays/${encodeURIComponent(bayCode)}/last-available`);
    const data = await r.json();

    if (!r.ok || !data.ok) {
      setSuggestionText("No hay espacio (estiba llena)");
      return;
    }

    state.suggested = { bay_code: data.bay_code, depth_row: data.depth_row, tier: data.tier };
    state.rowNumber = data.depth_row;
    state.tier = data.tier;

    setSuggestionText(`${data.bay_code} · ${fmtRow(data.depth_row)} · ${fmtTier(data.tier)}`);

    if (confirmBar) confirmBar.classList.remove("hidden");
  } catch (e) {
    setSuggestionText("Error de red al sugerir posición");
  }
}

async function chooseRow(rowNumber) {
  state.rowNumber = rowNumber;
  state.tier = null;
  state.suggested = null;

  try {
    const r = await fetch(`/api/yard/bays/${encodeURIComponent(state.bayCode)}/row/${encodeURIComponent(rowNumber)}/suggest-tier`);
    if (r.ok) {
      const data = await r.json();
      if (data && data.ok) {
        state.suggested = { bay_code: data.bay_code, depth_row: data.depth_row, tier: data.tier };
        state.tier = data.tier;

        setSuggestionText(`${data.bay_code} · ${fmtRow(data.depth_row)} · ${fmtTier(data.tier)}`);
        if (confirmBar) confirmBar.classList.remove("hidden");
        return;
      }
    }
  } catch (e) {}

  await suggestLastAvailable(state.bayCode);
}

// ------------------------
// Placement
// ------------------------
async function confirmPlacement() {
  if (!hasActiveContainer()) {
    alert("Primero selecciona un contenedor.");
    return;
  }
  if (!state.bayCode || !state.suggested) {
    alert("Selecciona un destino (espacio verde) antes de confirmar.");
    return;
  }

  const payload = {
    container_id: state.containerId,
    to_bay_code: state.bayCode,
    to_depth_row: state.suggested.depth_row,
    to_tier: state.suggested.tier
  };

  confirmPlacementBtn.disabled = true;

  try {
    const r = await fetch(`/api/yard/place`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await r.json();
    if (!r.ok) {
      alert(data.error || "Error al colocar contenedor");
      return;
    }

    const bay = data.bay_code || state.bayCode;
    const row = data.depth_row || state.suggested.depth_row;
    const tier = data.tier || state.suggested.tier;

    alert(`Colocado en ${bay} ${fmtRow(row)} ${fmtTier(tier)}`);

    // Reset destination selection
    clearDestinationSelection();

    // Refresh data + re-render stacks (so the moved container appears)
    await loadContainersInYard();

    // If still in stacks view, repaint
    if (state.view === VIEW.STACKS && currentBaysList.length) {
      renderStacksGrid(currentBaysList);
    }

    // Touch: optionally clear selection after move
    if (IS_TOUCH) clearSelectedContainer();

  } catch (e) {
    alert("Error de red al colocar contenedor");
  } finally {
    confirmPlacementBtn.disabled = false;
  }
}

function cancelPlacement() {
  clearDestinationSelection();
}

// ------------------------
// Containers list (Bandeja)
// ------------------------
function renderContainersList(list) {
  if (!containersList) return;

  if (!list || list.length === 0) {
    containersList.innerHTML = `<div class="hint">No hay contenedores en patio.</div>`;
    return;
  }

  const html = list.map(item => {
    const pos = item.position
      ? `${item.position.bay_code} ${fmtRow(item.position.depth_row)} ${fmtTier(item.position.tier)}`
      : "Sin posición";

    return `
      <div class="container-item"
           ${IS_TOUCH ? "" : `draggable="true"`}
           data-container-id="${item.id}"
           data-container-code="${item.code}">
        <div style="display:flex; justify-content:space-between; gap:10px;">
          <div>
            <div style="font-weight:950;">${item.code}</div>
            <div class="hint" style="margin-top:6px;">${item.size}${item.year ? " · " + item.year : ""}</div>
            <div style="font-size:12px; margin-top:6px; color:rgba(229,231,235,.92); font-weight:800;">
              ${pos}
            </div>
          </div>
          <div class="hint" style="text-align:right;">
            ${item.status_notes ? "📝" : ""}
          </div>
        </div>
      </div>
    `;
  }).join("");

  containersList.innerHTML = html;

  const nodes = containersList.querySelectorAll(".container-item");
  nodes.forEach(el => {
    const id = parseInt(el.getAttribute("data-container-id"), 10);
    const code = el.getAttribute("data-container-code");

    // Touch: tap-to-select
    el.addEventListener("click", () => {
      if (!IS_TOUCH) return;
      setSelectedContainer(id, code);
      clearDestinationSelection();
      // If on stacks view, repaint to show greens
      if (state.view === VIEW.STACKS && currentBaysList.length) {
        renderStacksGrid(currentBaysList);
      }
    });

    // PC: dragstart sets selection
    el.addEventListener("dragstart", (ev) => {
      if (IS_TOUCH) return;
      try { ev.dataTransfer.setData("text/plain", code || String(id)); } catch (_) {}
      setSelectedContainer(id, code);
      clearDestinationSelection();
      if (state.view === VIEW.STACKS && currentBaysList.length) {
        renderStacksGrid(currentBaysList);
      }
    });
  });

  highlightSelectedContainerInList();
}

function filterContainers(query) {
  const q = (query || "").trim().toUpperCase();
  if (!q) return allContainers;
  return allContainers.filter(c => (c.code || "").toUpperCase().includes(q));
}

async function loadContainersInYard() {
  if (!containersList) return;

  containersList.innerHTML = `<div class="hint">Cargando contenedores…</div>`;

  try {
    const r = await fetch(`/api/yard/containers-in-yard`);
    if (!r.ok) {
      containersList.innerHTML = `<div class="hint">Error cargando contenedores (HTTP ${r.status}).</div>`;
      return;
    }

    const data = await r.json();
    allContainers = (data && data.rows) ? data.rows : [];
    renderContainersList(filterContainers(containerSearch ? containerSearch.value : ""));

  } catch (e) {
    containersList.innerHTML = `<div class="hint">Error de red cargando contenedores.</div>`;
  }
}

// ------------------------
// Hooks / UI events
// ------------------------
if (touchHint) touchHint.classList.toggle("hidden", !IS_TOUCH);

if (clearSelectedBtn) clearSelectedBtn.addEventListener("click", clearSelectedContainer);

if (containerSearch) {
  containerSearch.addEventListener("input", () => {
    renderContainersList(filterContainers(containerSearch.value));
  });
}

if (refreshContainersBtn) {
  refreshContainersBtn.addEventListener("click", async () => {
    await loadContainersInYard();
    if (state.view === VIEW.STACKS && currentBaysList.length) {
      renderStacksGrid(currentBaysList);
    }
  });
}

// Drag chip
if (dragChip) {
  dragChip.addEventListener("dragstart", (ev) => {
    if (!hasActiveContainer()) {
      ev.preventDefault();
      return;
    }
    try { ev.dataTransfer.setData("text/plain", state.containerCode || String(state.containerId)); } catch (_) {}
  });
}

// Close panel buttons
if (stacksCloseBtn) stacksCloseBtn.addEventListener("click", () => {
  setView(VIEW.BLOCKS);
  drawBlocks();
});

if (rowsCloseBtn) rowsCloseBtn.addEventListener("click", () => {
  setView(VIEW.STACKS);
});

// Back button
if (yardBackBtn) {
  yardBackBtn.addEventListener("click", () => {
    if (state.view === VIEW.ROWS) {
      setView(VIEW.STACKS);
      return;
    }
    if (state.view === VIEW.STACKS) {
      setView(VIEW.BLOCKS);
      drawBlocks();
      return;
    }
  });
}

// Confirm/Cancel placement
if (confirmPlacementBtn) confirmPlacementBtn.addEventListener("click", confirmPlacement);
if (cancelPlacementBtn) cancelPlacementBtn.addEventListener("click", cancelPlacement);

// ------------------------
// Init
// ------------------------
attachStacksGridClickHandler();
setView(VIEW.BLOCKS);
drawBlocks();
loadContainersInYard();


