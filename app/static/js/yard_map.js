/* ==========================================
   Yard Map v2 (Bloques → Estibas → Filas)
   - Sin blockSelect
   - Sin availabilityOverlay
   - UI por paneles: stacksPanel + rowsPanel
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
  bayCode: null, // estiba
  rowNumber: null, // fila
  tier: null, // nivel sugerido o calculado

  // suggestion payload
  suggested: null, // { bay_code, depth_row, tier } o similar
};

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
  if (yardBackBtn) {
    yardBackBtn.classList.toggle("hidden", view === VIEW.BLOCKS);
  }

  // Panels
  if (stacksPanel) stacksPanel.classList.toggle("hidden", view !== VIEW.STACKS);
  if (rowsPanel) rowsPanel.classList.toggle("hidden", view !== VIEW.ROWS);

  // Confirm bar
  if (confirmBar) confirmBar.classList.add("hidden");
}

function setSelectedBar(open, text) {
  if (!selectedContainerBar) return;
  selectedContainerBar.classList.toggle("hidden", !open);
  if (selectedContainerText) selectedContainerText.textContent = text || "—";
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
}

function clearSelectedContainer() {
  state.containerId = null;
  state.containerCode = null;

  if (yardActiveContainer) yardActiveContainer.textContent = "—";
  if (dragChip) dragChip.classList.add("hidden");

  setSelectedBar(false, "");
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

// ------------------------
// SVG: Blocks
// ------------------------
function drawBlocks() {
  clearSvg();

  // 4 bloques fijos (los tuyos)
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

    // Visual base
    r.setAttribute("fill", "rgba(37,99,235,0.06)");
    r.setAttribute("stroke", "rgba(37,99,235,0.35)");
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

      // Sin contenedor seleccionado: igual dejemos abrir estibas (solo lectura)
      await openBlock(b.code);
    });

    svg.appendChild(r);

    // Label
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", b.x + 18);
    t.setAttribute("y", b.y + 32);
    t.setAttribute("font-size", "16");
    t.setAttribute("font-weight", "800");
    t.setAttribute("fill", "#111");
    t.textContent = `Bloque ${b.code}`;
    svg.appendChild(t);

    // Sub label
    const t2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t2.setAttribute("x", b.x + 18);
    t2.setAttribute("y", b.y + 55);
    t2.setAttribute("font-size", "12");
    t2.setAttribute("fill", "#666");
    t2.textContent = hasActiveContainer()
      ? "Suelta / toca para ver estibas"
      : "Toca para ver estibas (modo lectura)";
    svg.appendChild(t2);
  });
}

// ------------------------
// Stacks panel (Estibas)
// ------------------------
async function openBlock(blockCode) {
  state.blockCode = blockCode;
  state.bayCode = null;
  state.rowNumber = null;
  state.tier = null;
  state.suggested = null;

  if (stacksBlockCode) stacksBlockCode.textContent = blockCode;

  // Cargar bays (estibas) desde endpoint existente
  // Nota: ya lo usabas en loadMap(block)
  const r = await fetch(`/api/yard/map?block=${encodeURIComponent(blockCode)}`);
  const data = await r.json();

  const bays = (data && data.bays) ? data.bays : [];

  // Orden consistente
  bays.sort((a, b) => (a.bay_number || 0) - (b.bay_number || 0));

  renderStacksGrid(bays);

  setView(VIEW.STACKS);
}

function renderStacksGrid(bays) {
  if (!stacksGrid) return;

  if (!bays || bays.length === 0) {
    stacksGrid.innerHTML = `<div class="hint">No hay estibas configuradas en este bloque.</div>`;
    return;
  }

  const html = bays.map(b => {
    const used = b.used || 0;
    const cap = b.capacity || 0;
    const available = cap > 0 ? (used < cap) : true; // si no hay cap, asumimos disponible
    const cls = available ? "yard-cell available" : "yard-cell unavailable";
    const badge = available ? "Disponible" : "Lleno";

    return `
      <button type="button"
              class="${cls}"
              data-bay="${b.code}"
              ${available ? "" : "disabled"}
              style="text-align:left;">
        <div style="display:flex; justify-content:space-between; gap:10px; align-items:center;">
          <div style="font-weight:800;">${b.code}</div>
          <div style="font-size:11px; color:${available ? "#137333" : "#b3261e"}; font-weight:700;">${badge}</div>
        </div>
        <div style="font-size:12px; color:#555; margin-top:4px;">
          ${cap ? `${used}/${cap}` : `${used} usados`}
        </div>
      </button>
    `;
  }).join("");

  stacksGrid.innerHTML = html;

  // Bind
  stacksGrid.querySelectorAll("[data-bay]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const bayCode = btn.getAttribute("data-bay");
      await openBay(bayCode);
    });
  });
}

// ------------------------
// Rows panel (Filas)
// ------------------------
async function openBay(bayCode) {
  state.bayCode = bayCode;
  state.rowNumber = null;
  state.tier = null;
  state.suggested = null;

  if (rowsBlockCode) rowsBlockCode.textContent = state.blockCode || "—";
  if (rowsStackCode) rowsStackCode.textContent = bayCode;

  // Intento 1: endpoint de filas (ideal)
  // Esperado: { ok:true, bay_code:"X", rows:[ { row:1, levels_used:2, max_levels:4, is_full:false }, ... ] }
  let rowsData = null;
  try {
    const rr = await fetch(`/api/yard/bays/${encodeURIComponent(bayCode)}/rows-availability`);
    if (rr.ok) rowsData = await rr.json();
  } catch (e) {
    // ignore
  }

  if (rowsData && rowsData.rows) {
    renderRowsGrid(rowsData.rows);
    setView(VIEW.ROWS);
    return;
  }

  // Fallback: si no hay endpoint de filas, mostramos “modo simple”
  // y usamos last-available para sugerir una fila/nivel.
  renderRowsGridFallback(bayCode);
  setView(VIEW.ROWS);

  // Llamamos last-available para sugerir slot
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
          <div style="font-weight:800;">${fmtRow(r.row)}</div>
          <div style="font-size:12px; color:#555;">${badge}</div>
        </div>
        <div style="font-size:11px; color:#666; margin-top:4px;">
          ${isFull ? "Fila llena" : "Disponible"}
        </div>
      </button>
    `;
  }).join("");

  rowsGrid.innerHTML = html;

  // Bind: elegir fila
  rowsGrid.querySelectorAll("[data-row]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const row = parseInt(btn.getAttribute("data-row"), 10);
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

  // Intento 1 (ideal): endpoint que devuelve nivel sugerido para una fila específica
  // Esperado: { ok:true, bay_code, depth_row, tier }
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
  } catch (e) {
    // ignore and fallback
  }

  // Fallback: si no existe suggest-tier, usamos last-available (puede no caer en esa fila exacta)
  // Para no mentirle al usuario, mostramos que es “auto”.
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

  // Si tenemos suggested, lo ideal es colocar exactamente ahí.
  // Si tu backend hoy solo acepta to_bay_code, igual funciona, pero se perderá el control por fila/nivel.
  const payload = {
    container_id: state.containerId,
    to_bay_code: state.bayCode
  };

  // Si backend lo soporta, mandamos el destino completo
  if (state.suggested) {
    payload.to_depth_row = state.suggested.depth_row;
    payload.to_tier = state.suggested.tier;
  }

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

    // Mensaje
    const bay = data.bay_code || state.bayCode;
    const row = data.depth_row || (state.suggested ? state.suggested.depth_row : null);
    const tier = data.tier || (state.suggested ? state.suggested.tier : null);

    if (row && tier) {
      alert(`Colocado en ${bay} ${fmtRow(row)} ${fmtTier(tier)}`);
    } else {
      alert(`Colocado en ${bay}`);
    }

    // Reset navegación (volvemos a bloques)
    state.blockCode = null;
    state.bayCode = null;
    state.rowNumber = null;
    state.tier = null;
    state.suggested = null;

    setView(VIEW.BLOCKS);
    drawBlocks();

    // refrescar bandeja (para ver nueva posición)
    await loadContainersInYard();

    // touch: limpio selección para evitar meter el mismo contenedor por accidente
    if (IS_TOUCH) clearSelectedContainer();

  } catch (e) {
    alert("Error de red al colocar contenedor");
  } finally {
    confirmPlacementBtn.disabled = false;
  }
}

function cancelPlacement() {
  // Solo ocultamos confirm
  if (confirmBar) confirmBar.classList.add("hidden");
  state.suggested = null;
  state.tier = null;
  setSuggestionText("—");
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
            <div style="font-weight:800;">${item.code}</div>
            <div style="font-size:12px; color:#555;">${item.size}${item.year ? " · " + item.year : ""}</div>
            <div style="font-size:12px; color:#111; margin-top:4px;">${pos}</div>
          </div>
          <div style="font-size:12px; color:#666; text-align:right;">
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
    });

    // PC: dragstart sets selection
    el.addEventListener("dragstart", () => {
      if (IS_TOUCH) return;
      setSelectedContainer(id, code);
    });
  });
}

function filterContainers(query) {
  const q = (query || "").trim().toUpperCase();
  if (!q) return allContainers;
  return allContainers.filter(c => (c.code || "").toUpperCase().includes(q));
}

async function loadContainersInYard() {
  if (!containersList) return;

  containersList.innerHTML = `<div class="hint">Cargando contenedores…</div>`;

  const r = await fetch(`/api/yard/containers-in-yard`);
  const data = await r.json();

  allContainers = (data && data.rows) ? data.rows : [];
  renderContainersList(filterContainers(containerSearch ? containerSearch.value : ""));
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

if (refreshContainersBtn) refreshContainersBtn.addEventListener("click", loadContainersInYard);

// Drag chip (no es obligatorio, pero queda “pro”)
if (dragChip) {
  dragChip.addEventListener("dragstart", (ev) => {
    if (!hasActiveContainer()) {
      ev.preventDefault();
      return;
    }
    // nada: el “active container” ya está en state
  });
}

// Close panel buttons
if (stacksCloseBtn) stacksCloseBtn.addEventListener("click", () => {
  // volver a bloques
  setView(VIEW.BLOCKS);
  drawBlocks();
});

if (rowsCloseBtn) rowsCloseBtn.addEventListener("click", () => {
  // volver a estibas del bloque actual
  setView(VIEW.STACKS);
});

// Back button (breadcrumbs)
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
setView(VIEW.BLOCKS);
drawBlocks();
loadContainersInYard();

