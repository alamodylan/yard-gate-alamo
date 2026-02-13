/* ==========================================================
   Yard Gate Álamo — Yard Map (Blocks -> Racks Excel-like)
   - NO modifica endpoints
   - Solo frontend usando APIs actuales
   - Bloques (SVG) -> Panel estibas con rack tipo Excel:
       columnas F01..F10, filas N4..N1
   - Selección de contenedor:
       desde lista izquierda o tocando un contenedor dentro del rack
   - Destino:
       tocar slot verde => azul + confirm
   ========================================================== */

const svg = document.getElementById("yardSvg");

// Left panel
const containersList = document.getElementById("containersList");
const containerSearch = document.getElementById("containerSearch");
const refreshContainersBtn = document.getElementById("refreshContainersBtn");
const touchHint = document.getElementById("touchHint");
const selectedContainerBar = document.getElementById("selectedContainerBar");
const selectedContainerText = document.getElementById("selectedContainerText");
const clearSelectedBtn = document.getElementById("clearSelectedBtn");

// Toolbar
const yardBackBtn = document.getElementById("yardBackBtn");
const yardCrumbLevel = document.getElementById("yardCrumbLevel");
const yardCrumbDetail = document.getElementById("yardCrumbDetail");
const yardActiveContainer = document.getElementById("yardActiveContainer");

// Drag chip
const dragChip = document.getElementById("dragChip");
const dragChipCode = document.getElementById("dragChipCode");

// Stacks panel (rack)
const stacksPanel = document.getElementById("stacksPanel");
const stacksGrid = document.getElementById("stacksGrid");
const stacksBlockCode = document.getElementById("stacksBlockCode");
const stacksCloseBtn = document.getElementById("stacksCloseBtn");

// Legacy rows panel (kept, not used)
const rowsPanel = document.getElementById("rowsPanel");
const rowsCloseBtn = document.getElementById("rowsCloseBtn");
const rowsGrid = document.getElementById("rowsGrid");

// Confirm bar
const confirmBar = document.getElementById("confirmBar");
const suggestedSlot = document.getElementById("suggestedSlot");
const confirmPlacementBtn = document.getElementById("confirmPlacementBtn");
const cancelPlacementBtn = document.getElementById("cancelPlacementBtn");

// Touch detection
const IS_TOUCH = ("ontouchstart" in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints > 0);

// Data cache
let allContainers = [];
let currentBaysList = [];
let occupancyIndex = null; // Map<bay_code, Map<"row-tier", {id, code}>>

// Views
const VIEW = {
  BLOCKS: "BLOCKS",
  STACKS: "STACKS",
  ROWS: "ROWS",
};

let state = {
  view: VIEW.BLOCKS,

  containerId: null,
  containerCode: null,

  blockCode: null,
  bayCode: null,
  rowNumber: null,
  tier: null,

  suggested: null, // { bay_code, depth_row, tier }
};

// ------------------------
// Config: filas visibles y dirección
// ------------------------
// En operación típica: F01 = entrada. Si es al revés, cambia a "DESC".
const VISIBLE_ROWS = 10;          // F01..F10
const ROW_DIRECTION = "ASC";      // "ASC" => F01 entrada, "DESC" => F10 entrada

function getRowOrderVisible() {
  const base = [];
  for (let i = 1; i <= VISIBLE_ROWS; i++) base.push(i);
  return ROW_DIRECTION === "DESC" ? base.reverse() : base;
}

function fmtRow(row) {
  return `F${String(row).padStart(2, "0")}`;
}
function fmtTier(tier) {
  return `N${tier}`;
}
function hasActiveContainer() {
  return !!state.containerId;
}

// ------------------------
// Theme for SVG
// ------------------------
const THEME = {
  text: "rgba(229,231,235,.95)",
  muted: "rgba(148,163,184,.92)",
  primaryFill: "rgba(37,99,235,0.16)",
  primaryStroke: "rgba(37,99,235,0.40)",
};

// ------------------------
// Generic helpers
// ------------------------
function clearSvg() {
  while (svg && svg.firstChild) svg.removeChild(svg.firstChild);
}

function setSuggestionText(text) {
  if (suggestedSlot) suggestedSlot.textContent = text || "—";
}

function setView(view) {
  state.view = view;

  if (yardCrumbLevel) {
    yardCrumbLevel.textContent =
      view === VIEW.BLOCKS ? "Bloques" :
      view === VIEW.STACKS ? "Estibas" :
      "Filas";
  }

  if (yardCrumbDetail) {
    if (view === VIEW.BLOCKS) yardCrumbDetail.textContent = "";
    if (view === VIEW.STACKS) yardCrumbDetail.textContent = state.blockCode ? `· Bloque ${state.blockCode}` : "";
    if (view === VIEW.ROWS) yardCrumbDetail.textContent = "";
  }

  if (yardBackBtn) yardBackBtn.classList.toggle("hidden", view === VIEW.BLOCKS);

  if (stacksPanel) stacksPanel.classList.toggle("hidden", view !== VIEW.STACKS);
  if (rowsPanel) rowsPanel.classList.add("hidden"); // no usamos rows panel

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

function clearDestinationSelection() {
  state.bayCode = null;
  state.rowNumber = null;
  state.tier = null;
  state.suggested = null;

  if (confirmBar) confirmBar.classList.add("hidden");
  setSuggestionText("—");

  if (stacksGrid) {
    stacksGrid.querySelectorAll(".rack-slot.is-selected").forEach(el => el.classList.remove("is-selected"));
  }
}

function setSelectedContainer(containerId, containerCode) {
  state.containerId = containerId;
  state.containerCode = containerCode;

  if (yardActiveContainer) yardActiveContainer.textContent = containerCode || `#${containerId}`;

  if (dragChip && dragChipCode) {
    dragChipCode.textContent = containerCode || `#${containerId}`;
    dragChip.classList.remove("hidden");
  }

  if (IS_TOUCH) setSelectedBar(true, `${containerCode} (#${containerId})`);
  highlightSelectedContainerInList();

  // re-render racks para pintar verdes
  if (state.view === VIEW.STACKS && currentBaysList.length) {
    renderStacksGrid(currentBaysList);
  }
}

function clearSelectedContainer() {
  state.containerId = null;
  state.containerCode = null;

  if (yardActiveContainer) yardActiveContainer.textContent = "—";
  if (dragChip) dragChip.classList.add("hidden");

  setSelectedBar(false, "");
  highlightSelectedContainerInList();

  clearDestinationSelection();

  if (state.view === VIEW.STACKS && currentBaysList.length) {
    renderStacksGrid(currentBaysList);
  }
}

// ------------------------
// SVG blocks view
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

    r.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      if (!hasActiveContainer()) return;
      r.classList.add("yard-block-highlight");
    });
    r.addEventListener("dragleave", () => r.classList.remove("yard-block-highlight"));

    r.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      r.classList.remove("yard-block-highlight");
      await openBlock(b.code);
    });

    r.addEventListener("click", async () => {
      await openBlock(b.code);
    });

    svg.appendChild(r);

    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", b.x + 18);
    t.setAttribute("y", b.y + 32);
    t.setAttribute("font-size", "16");
    t.setAttribute("font-weight", "800");
    t.setAttribute("fill", THEME.text);
    t.textContent = `Bloque ${b.code}`;
    svg.appendChild(t);

    const t2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t2.setAttribute("x", b.x + 18);
    t2.setAttribute("y", b.y + 55);
    t2.setAttribute("font-size", "12");
    t2.setAttribute("fill", THEME.muted);
    t2.textContent = "Toca para ver estibas";
    svg.appendChild(t2);
  });
}

// ------------------------
// Occupancy index (from containers list)
// ------------------------
function buildOccupancyIndexForBlock(blockCode) {
  const idx = new Map();
  const prefix = String(blockCode || "").toUpperCase();

  for (const c of (allContainers || [])) {
    if (!c.position) continue;

    const bay = c.position.bay_code;
    const row = c.position.depth_row;
    const tier = c.position.tier;

    if (!bay || typeof row !== "number" || typeof tier !== "number") continue;
    if (!String(bay).toUpperCase().startsWith(prefix)) continue;

    if (!idx.has(bay)) idx.set(bay, new Map());
    idx.get(bay).set(`${row}-${tier}`, { id: c.id, code: c.code });
  }
  return idx;
}

// ------------------------
// Open block => racks
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

    currentBaysList = bays.slice().sort((a, b) => (a.bay_number || 0) - (b.bay_number || 0));
    occupancyIndex = buildOccupancyIndexForBlock(blockCode);

    renderStacksGrid(currentBaysList);
    setView(VIEW.STACKS);
  } catch (e) {
    if (stacksGrid) stacksGrid.innerHTML = `<div class="hint">Error de red cargando estibas.</div>`;
    setView(VIEW.STACKS);
  }
}

// ------------------------
// Render racks using YOUR CSS classes:
// .stack-card, .rack, .rack-header, .rack-row, .rack-slot, .rack-code
// ------------------------
function renderStacksGrid(bays) {
  if (!stacksGrid) return;

  if (!bays || bays.length === 0) {
    stacksGrid.innerHTML = `<div class="hint">No hay estibas configuradas en este bloque.</div>`;
    return;
  }

  const occ = occupancyIndex || new Map();
  const rowOrder = getRowOrderVisible();    // 10 filas
  const tierOrder = [4, 3, 2, 1];           // 4 niveles

  const html = bays.map(b => {
    const used = b.used || 0;
    const cap = b.capacity || 0;
    const available = cap > 0 ? (used < cap) : true;

    const badgeText = available ? "Disponible" : "Lleno";
    const badgeCls = available ? "badge-ok" : "badge-bad";

    const bayOcc = occ.get(b.code) || new Map();

    // Header row: F01..F10
    const headerCols = rowOrder.map(rn => {
      return `<div class="rack-colhdr">${fmtRow(rn)}</div>`;
    }).join("");

    // Tier rows: N4..N1 with 10 slots each
    const rowsHtml = tierOrder.map(tn => {
      const slots = rowOrder.map(rn => {
        const key = `${rn}-${tn}`;
        const item = bayOcc.get(key);

        // occupied slot = container
        if (item) {
          return `
            <div class="rack-slot is-occupied"
                 data-action="pick-container"
                 data-container-id="${item.id}"
                 data-container-code="${item.code}"
                 data-bay="${b.code}"
                 data-row="${rn}"
                 data-tier="${tn}"
                 ${IS_TOUCH ? "" : `draggable="true"`}
                 title="${item.code} · ${b.code} · ${fmtRow(rn)} · ${fmtTier(tn)}">
              <span class="rack-code">${item.code}</span>
            </div>
          `;
        }

        // empty slot: becomes green only if there is active container selected
        const canDrop = hasActiveContainer();
        const cls = canDrop ? "is-available" : "is-empty";

        return `
          <div class="rack-slot ${cls}"
               data-action="pick-destination"
               data-bay="${b.code}"
               data-row="${rn}"
               data-tier="${tn}"
               title="${b.code} · ${fmtRow(rn)} · ${fmtTier(tn)}">
            <span class="rack-code"></span>
          </div>
        `;
      }).join("");

      return `
        <div class="rack-row">
          <div class="rack-tierlbl">${fmtTier(tn)}</div>
          ${slots}
        </div>
      `;
    }).join("");

    return `
      <div class="stack-card" data-baycard="${b.code}">
        <div class="stack-card-head">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
            <div>
              <div style="font-weight:950; font-size:16px;">Estiba ${b.code}</div>
              <div class="hint" style="margin-top:6px;">${cap ? `${used}/${cap}` : `${used} usados`}</div>
            </div>
            <div class="${badgeCls}" style="font-size:11px; font-weight:900;">${badgeText}</div>
          </div>
          <div class="hint" style="margin-top:8px;">
            ${hasActiveContainer()
              ? "Toca un espacio verde para destino (o arrastra en PC)."
              : "Toca un contenedor para seleccionarlo y moverlo."
            }
          </div>
        </div>

        <div class="rack">
          <div class="rack-header">
            <div class="rack-corner"></div>
            ${headerCols}
          </div>
          ${rowsHtml}
        </div>
      </div>
    `;
  }).join("");

  stacksGrid.innerHTML = html;

  // Click delegation
  stacksGrid.addEventListener("click", onStacksGridClick, { once: true });

  // Drag/drop handlers per slot
  stacksGrid.querySelectorAll(".rack-slot").forEach(slot => {
    const action = slot.getAttribute("data-action");

    // Drag start from occupied -> selects container
    if (!IS_TOUCH && action === "pick-container") {
      slot.addEventListener("dragstart", (ev) => {
        const id = parseInt(slot.getAttribute("data-container-id"), 10);
        const code = slot.getAttribute("data-container-code");
        try { ev.dataTransfer.setData("text/plain", code || String(id)); } catch (_) {}
        setSelectedContainer(id, code);
        clearDestinationSelection();
        renderStacksGrid(currentBaysList);
      });
    }

    // Drag over for destination slots
    slot.addEventListener("dragover", (ev) => {
      if (!hasActiveContainer()) return;
      if (slot.getAttribute("data-action") !== "pick-destination") return;
      if (!slot.classList.contains("is-available")) return;
      ev.preventDefault();
      slot.classList.add("yard-block-highlight");
    });

    slot.addEventListener("dragleave", () => slot.classList.remove("yard-block-highlight"));

    slot.addEventListener("drop", (ev) => {
      slot.classList.remove("yard-block-highlight");
      if (!hasActiveContainer()) return;
      if (slot.getAttribute("data-action") !== "pick-destination") return;
      if (!slot.classList.contains("is-available")) return;
      ev.preventDefault();
      pickDestinationFromSlot(slot);
    });
  });

  // if destination already selected, keep UI
  if (state.suggested) {
    setSuggestionText(`${state.suggested.bay_code} · ${fmtRow(state.suggested.depth_row)} · ${fmtTier(state.suggested.tier)}`);
    if (confirmBar) confirmBar.classList.remove("hidden");
  }
}

function onStacksGridClick(ev) {
  const slot = ev.target.closest(".rack-slot");
  if (!slot) return;

  const action = slot.getAttribute("data-action");

  if (action === "pick-container") {
    const id = parseInt(slot.getAttribute("data-container-id"), 10);
    const code = slot.getAttribute("data-container-code");
    setSelectedContainer(id, code);
    clearDestinationSelection();
    renderStacksGrid(currentBaysList);
    return;
  }

  if (action === "pick-destination") {
    if (!hasActiveContainer()) return;
    if (!slot.classList.contains("is-available")) return;
    pickDestinationFromSlot(slot);
    return;
  }
}

function pickDestinationFromSlot(slot) {
  // Remove previous selected
  stacksGrid.querySelectorAll(".rack-slot.is-selected").forEach(el => el.classList.remove("is-selected"));

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
// Placement (NO endpoint change)
// ------------------------
async function confirmPlacement() {
  if (!hasActiveContainer()) {
    alert("Primero selecciona un contenedor.");
    return;
  }
  if (!state.suggested) {
    alert("Selecciona un destino (slot verde) para mover el contenedor.");
    return;
  }

  const payload = {
    container_id: state.containerId,
    to_bay_code: state.suggested.bay_code,
    to_depth_row: state.suggested.depth_row,
    to_tier: state.suggested.tier,
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

    const bay = data.bay_code || payload.to_bay_code;
    const row = data.depth_row || payload.to_depth_row;
    const tier = data.tier || payload.to_tier;

    alert(`Colocado en ${bay} ${fmtRow(row)} ${fmtTier(tier)}`);

    // Reload data, rebuild occupancy, re-render
    await loadContainersInYard();
    occupancyIndex = buildOccupancyIndexForBlock(state.blockCode);

    clearDestinationSelection();
    renderStacksGrid(currentBaysList);

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
// Containers list (left tray)
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

  containersList.querySelectorAll(".container-item").forEach(el => {
    const id = parseInt(el.getAttribute("data-container-id"), 10);
    const code = el.getAttribute("data-container-code");

    el.addEventListener("click", () => {
      if (!IS_TOUCH) return;
      setSelectedContainer(id, code);
    });

    el.addEventListener("dragstart", (ev) => {
      if (IS_TOUCH) return;
      try { ev.dataTransfer.setData("text/plain", code || String(id)); } catch (_) {}
      setSelectedContainer(id, code);
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

    if (state.blockCode) {
      occupancyIndex = buildOccupancyIndexForBlock(state.blockCode);
    }
  } catch (e) {
    containersList.innerHTML = `<div class="hint">Error de red cargando contenedores.</div>`;
  }
}

// ------------------------
// Hooks
// ------------------------
if (touchHint) touchHint.classList.toggle("hidden", !IS_TOUCH);

if (clearSelectedBtn) clearSelectedBtn.addEventListener("click", clearSelectedContainer);

if (containerSearch) {
  containerSearch.addEventListener("input", () => {
    renderContainersList(filterContainers(containerSearch.value));
  });
}

if (refreshContainersBtn) refreshContainersBtn.addEventListener("click", loadContainersInYard);

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

// Close stacks
if (stacksCloseBtn) stacksCloseBtn.addEventListener("click", () => {
  setView(VIEW.BLOCKS);
  drawBlocks();
});

// Back button
if (yardBackBtn) {
  yardBackBtn.addEventListener("click", () => {
    if (state.view === VIEW.STACKS) {
      setView(VIEW.BLOCKS);
      drawBlocks();
      return;
    }
  });
}

// Confirm/Cancel
if (confirmPlacementBtn) confirmPlacementBtn.addEventListener("click", confirmPlacement);
if (cancelPlacementBtn) cancelPlacementBtn.addEventListener("click", cancelPlacement);

// Legacy rows close
if (rowsCloseBtn) rowsCloseBtn.addEventListener("click", () => {
  if (rowsPanel) rowsPanel.classList.add("hidden");
});

// ------------------------
// Init
// ------------------------
setView(VIEW.BLOCKS);
drawBlocks();
loadContainersInYard();

// Bloque preseleccionado (si existe)
if (window.YARD_INIT && window.YARD_INIT.block) {
  openBlock(window.YARD_INIT.block);
}



