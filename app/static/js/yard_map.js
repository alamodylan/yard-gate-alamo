/* ==========================================================
   Yard Gate Álamo — Yard Map (Blocks -> Racks Excel-like)
   - NO modifica endpoints
   - Solo frontend usando APIs actuales
   - Bloques (SVG) -> Panel estibas con rack tipo Excel:
       columnas F01..F10, filas N4..N1
   - Selección de contenedor:
       desde lista izquierda o tocando un contenedor dentro del rack
   - Destino:
       tocar slot verde => confirm inmediato (sin bajar)
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

// Confirm bar (lo dejamos por compat, pero ya no es obligatorio)
const confirmBar = document.getElementById("confirmBar");
const suggestedSlot = document.getElementById("suggestedSlot");
const confirmPlacementBtn = document.getElementById("confirmPlacementBtn");
const cancelPlacementBtn = document.getElementById("cancelPlacementBtn");

// Touch detection
const IS_TOUCH = ("ontouchstart" in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints > 0);

// Data cache
let allContainers = [];
let mountedContainers = [];
let currentBaysList = [];
let occupancyIndex = null; // Map<bay_code, Map<"row-tier", {id, code}>>
let validDestinationsIndex = new Set();

// Views
const VIEW = {
  BLOCKS: "BLOCKS",
  STACKS: "STACKS",
  ROWS: "ROWS",
};
const mountContainerBtn = document.getElementById("mountContainerBtn");
const mountContainerToolbarBtn = document.getElementById("mountContainerToolbarBtn");

const MOUNTABLE_STATUSES = new Set([
  "PARA_DESPACHO",
  "EVACUAR_SOLICITADO",
]);

function getSelectedContainerData() {
  return allContainers.find(c => Number(c.id) === Number(state.containerId)) || null;
}

function getDispatchStatusClass(containerOrStatus) {
  let status = "NORMAL";
  let isPrelistVisible = false;

  if (typeof containerOrStatus === "string") {
    status = (containerOrStatus || "NORMAL").toUpperCase();
    isPrelistVisible = true;
  } else if (containerOrStatus) {

    const hasPhysicalLocation =
      containerOrStatus.bay_code &&
      containerOrStatus.depth_row &&
      containerOrStatus.tier;

    if (hasPhysicalLocation) {
      return "";
    }

    status = (containerOrStatus.dispatch_status || "NORMAL").toUpperCase();
    isPrelistVisible = containerOrStatus.is_prelist_visible === true;
  }

  if (!isPrelistVisible) {
    return "";
  }

  if (status === "PARA_DESPACHO") return "is-dispatch";
  if (status === "PARA_EVACUAR") return "is-evac";

  return "";
}

function getDispatchStatusLabel(status) {
  const s = (status || "NORMAL").toUpperCase();

  if (s === "PARA_DESPACHO") return "Asignado";
  if (s === "PARA_EVACUAR") return "Evacuar";
  if (s === "EVACUAR_SOLICITADO") return "Solicitado evacuar";
  if (s === "DESPACHO_MONTADO") return "Despacho montado";
  if (s === "EVACUACION_MONTADA") return "Evacuación montada";

  return "Disponible";
}

function updateMountButtons() {
  const c = getSelectedContainerData();
  const status = (c?.dispatch_status || "NORMAL").toUpperCase();
  const canMount = MOUNTABLE_STATUSES.has(status);

  if (mountContainerBtn) {
    mountContainerBtn.classList.toggle("hidden", !canMount);
  }

  if (mountContainerToolbarBtn) {
    mountContainerToolbarBtn.classList.toggle("hidden", !canMount);
  }
}

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
const ROW_DIRECTION = "ASC";

function getRowOrderForBay(bay) {
  const maxRows = parseInt(bay?.max_depth_rows || 1, 10);
  const safeRows = Number.isFinite(maxRows) && maxRows > 0 ? maxRows : 1;

  const rows = [];
  for (let i = 1; i <= safeRows; i++) {
    rows.push(i);
  }

  return ROW_DIRECTION === "DESC" ? rows.reverse() : rows;
}

function getTierOrderForBay(bay) {
  const maxTiers = parseInt(bay?.max_tiers || 1, 10);
  const safeTiers = Number.isFinite(maxTiers) && maxTiers > 0 ? maxTiers : 1;

  const tiers = [];
  for (let i = safeTiers; i >= 1; i--) {
    tiers.push(i);
  }

  return tiers;
}

function fmtRow(row) {
  return `F${String(row).padStart(2, "0")}`;
}

function fmtTier(tier) {
  return `N${tier}`;
}

function getContainerSizeClass(size) {
  const s = String(size || "").toUpperCase();

  if (s.startsWith("20")) return "is-size-20";

  if (
    s.startsWith("40") ||
    s.startsWith("45")
  ) {
    return "is-size-40";
  }

  return "";
}

function getBayRackVisualVars(bayType) {
  const t = String(bayType || "40").toUpperCase();

  if (t === "20") {
    return {
      colMin: "95px",
      slotMinHeight: "52px",
    };
  }

  return {
    colMin: "180px",
    slotMinHeight: "72px",
  };
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

  const c = getSelectedContainerData();
  const mountedC = mountedContainers.find(x => Number(x.id) === Number(containerId)) || null;

  const statusLabel = getDispatchStatusLabel(
    c?.dispatch_status || mountedC?.dispatch_status || "NORMAL"
  );

  if (yardActiveContainer) {
    yardActiveContainer.textContent = containerCode
      ? `${containerCode} · ${statusLabel}`
      : `#${containerId}`;
  }

  if (dragChip && dragChipCode) {
    dragChipCode.textContent = containerCode || `#${containerId}`;
    dragChip.classList.remove("hidden");
  }

  if (IS_TOUCH) {
    setSelectedBar(true, `${containerCode} (#${containerId}) · ${statusLabel}`);
  }

  highlightSelectedContainerInList();
  updateMountButtons();

  if (state.view === VIEW.STACKS && state.blockCode && state.blockCode !== "__MOUNTED__" && currentBaysList.length) {
    loadValidDestinationsForBlock(state.blockCode).then(() => {
      renderStacksGrid(currentBaysList);
    });
  }

  if (state.view === VIEW.STACKS && state.blockCode === "__MOUNTED__") {
    renderMountedContainersBlock();
  }
}

function clearSelectedContainer() {
  state.containerId = null;
  state.containerCode = null;

  if (yardActiveContainer) yardActiveContainer.textContent = "—";
  if (dragChip) dragChip.classList.add("hidden");

  setSelectedBar(false, "");
  highlightSelectedContainerInList();
  validDestinationsIndex = new Set();
  clearDestinationSelection();
  updateMountButtons();

  if (state.view === VIEW.STACKS && currentBaysList.length) {
    renderStacksGrid(currentBaysList);
  }
}

// ------------------------
// SVG blocks view
// ------------------------
function getDynamicBlocksLayout() {
  const rawBlocks = Array.isArray(window.YARD_INIT?.blocks)
    ? window.YARD_INIT.blocks
    : [];

  const blockCodes = rawBlocks
    .map(b => String(b.code || "").trim().toUpperCase())
    .filter(Boolean);

  const uniqueCodes = [...new Set(blockCodes)];

  // Bloque virtual al final
  uniqueCodes.push("__MOUNTED__");

  const canvasW = 1100;
  const canvasH = 520;
  const gap = 20;
  const margin = 20;

  const count = uniqueCodes.length;

  let cols = Math.ceil(Math.sqrt(count));
  let rows = Math.ceil(count / cols);

  if (count <= 2) {
    cols = count;
    rows = 1;
  }

  const availableW = canvasW - margin * 2 - gap * (cols - 1);
  const availableH = canvasH - margin * 2 - gap * (rows - 1);

  const blockW = Math.floor(availableW / cols);
  const blockH = Math.floor(availableH / rows);

  return uniqueCodes.map((code, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);

    return {
      code,
      isMountedBlock: code === "__MOUNTED__",
      label: code === "__MOUNTED__" ? "Montados" : `Bloque ${code}`,
      subtitle: code === "__MOUNTED__"
        ? "Contenedores sobre chasis"
        : "Toca para ver estibas",
      x: margin + col * (blockW + gap),
      y: margin + row * (blockH + gap),
      w: blockW,
      h: blockH,
    };
  });
}

function drawBlocks() {
  clearSvg();

  const blocks = getDynamicBlocksLayout();

  if (!blocks.length) {
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", 30);
    t.setAttribute("y", 50);
    t.setAttribute("font-size", "16");
    t.setAttribute("font-weight", "800");
    t.setAttribute("fill", THEME.text);
    t.textContent = "No hay bloques configurados para este predio.";
    svg.appendChild(t);
    return;
  }

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

    r.addEventListener("dragleave", () => {
      r.classList.remove("yard-block-highlight");
    });

    r.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      r.classList.remove("yard-block-highlight");
      if (b.isMountedBlock) {
        await openMountedBlock();
      } else {
        await openBlock(b.code);
      }
    });

    r.addEventListener("click", async () => {
      if (b.isMountedBlock) {
        await openMountedBlock();
      } else {
        await openBlock(b.code);
      }
    });

    svg.appendChild(r);

    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", b.x + 18);
    t.setAttribute("y", b.y + 32);
    t.setAttribute("font-size", "16");
    t.setAttribute("font-weight", "800");
    t.setAttribute("fill", THEME.text);
    t.textContent = b.label;
    svg.appendChild(t);

    const t2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t2.setAttribute("x", b.x + 18);
    t2.setAttribute("y", b.y + 55);
    t2.setAttribute("font-size", "12");
    t2.setAttribute("fill", THEME.muted);
    t2.textContent = b.subtitle;
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

    idx.get(bay).set(`${row}-${tier}`, {
      id: c.id,
      code: c.code,
      size: c.size || "",
      dispatch_status: c.dispatch_status || "NORMAL",
      is_prelist_visible: c.is_prelist_visible === true,
      prelist: c.prelist || null,
    });
  }

  return idx;
}

async function loadMountedContainers() {
  try {
    const r = await fetch("/api/yard/mounted-containers");

    if (!r.ok) {
      mountedContainers = [];
      return;
    }

    const data = await r.json();
    mountedContainers = data.rows || [];
  } catch (e) {
    mountedContainers = [];
  }
}

async function openMountedBlock() {
  state.blockCode = "__MOUNTED__";
  clearDestinationSelection();

  if (stacksBlockCode) stacksBlockCode.textContent = "Montados";
  if (stacksGrid) stacksGrid.innerHTML = `<div class="hint">Cargando contenedores montados…</div>`;

  await loadMountedContainers();

  renderMountedContainersBlock();
  setView(VIEW.STACKS);
}

function renderMountedContainersBlock() {
  if (!stacksGrid) return;

  if (!mountedContainers.length) {
    stacksGrid.innerHTML = `
      <div class="stack-card">
        <div class="hint">No hay contenedores en Montados actualmente.</div>
      </div>
    `;
    return;
  }

  const cols = 4;
  const total = mountedContainers.length;
  const rows = Math.ceil(total / cols);

  const slots = [];

  for (let i = 0; i < rows * cols; i++) {
    const item = mountedContainers[i];

    if (!item) {
      slots.push(`
        <div class="rack-slot is-empty"
             title="Espacio vacío en Montados">
          <span class="rack-code"></span>
        </div>
      `);
      continue;
    }

    let statusClass = "";

    if (item.visual_type === "pending_location") {
      statusClass = "is-mounted-pending";
    } else if (item.visual_type === "mounted_evac") {
      statusClass = "is-mounted-evac";
    } else if (item.visual_type === "mounted_dispatch") {
      statusClass = "is-mounted-dispatch";
    }

    const label = item.visual_label || getDispatchStatusLabel(item.dispatch_status);

    slots.push(`
      <div class="rack-slot is-occupied ${statusClass}"
           data-action="pick-container"
           data-container-id="${item.id}"
           data-container-code="${item.code}"
           ${IS_TOUCH ? "" : `draggable="true"`}
           title="${item.code} · ${label}">
        <span class="rack-code">${item.code}</span>
        <span class="rack-status">${label}</span>
      </div>
    `);
  }

  let rowsHtml = "";

  for (let r = 0; r < rows; r++) {
    const rowSlots = slots.slice(r * cols, r * cols + cols).join("");

    rowsHtml += `
      <div class="rack-row">
        <div class="rack-tierlbl">M${r + 1}</div>
        ${rowSlots}
      </div>
    `;
  }

  const headerCols = Array.from({ length: cols }, (_, i) => {
    return `<div class="rack-colhdr">C${i + 1}</div>`;
  }).join("");

  stacksGrid.innerHTML = `
    <div class="stack-card">
      <div class="stack-card-head">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
          <div>
            <div style="font-weight:950; font-size:16px;">Estiba virtual Montados</div>
            <div class="hint" style="margin-top:6px;">
              ${mountedContainers.length} contenedor(es) sin posición física
            </div>
          </div>
          <div class="badge-ok" style="font-size:11px; font-weight:900;">
            Virtual
          </div>
        </div>

        <div class="hint" style="margin-top:8px;">
          Toca un contenedor para seleccionarlo. Luego entra a un bloque real y colócalo en un slot verde.
        </div>
      </div>

      <div class="rack" style="--rack-cols:${cols};">
        <div class="rack-header">
          <div class="rack-corner"></div>
          ${headerCols}
        </div>
        ${rowsHtml}
      </div>
    </div>
  `;

  stacksGrid.onclick = onStacksGridClick;

  stacksGrid.querySelectorAll(".rack-slot").forEach(slot => {
    const action = slot.getAttribute("data-action");

    if (!IS_TOUCH && action === "pick-container") {
      slot.addEventListener("dragstart", (ev) => {
        const id = parseInt(slot.getAttribute("data-container-id"), 10);
        const code = slot.getAttribute("data-container-code");

        try {
          ev.dataTransfer.setData("text/plain", code || String(id));
        } catch (_) {}

        setSelectedContainer(id, code);
        clearDestinationSelection();
      });
    }
  });
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

    await loadValidDestinationsForBlock(blockCode);

    renderStacksGrid(currentBaysList);
    setView(VIEW.STACKS);
  } catch (e) {
    console.error("Error cargando/renderizando estibas:", e);

    if (stacksGrid) {
      stacksGrid.innerHTML = `
        <div class="hint">
          Error cargando/renderizando estibas: ${e.message || e}
        </div>
      `;
    }

    setView(VIEW.STACKS);
  }
}

async function loadValidDestinationsForBlock(blockCode) {
  validDestinationsIndex = new Set();

  if (!hasActiveContainer() || !blockCode) return;

  try {
    const url = `/api/yard/valid-destinations?container_id=${encodeURIComponent(state.containerId)}&block=${encodeURIComponent(blockCode)}`;
    const r = await fetch(url);

    if (!r.ok) return;

    const data = await r.json();
    const destinations = data.destinations || [];

    destinations.forEach(d => {
      validDestinationsIndex.add(`${d.bay_code}-${d.depth_row}-${d.tier}`);
    });
  } catch (e) {
    validDestinationsIndex = new Set();
  }
}

// ------------------------
// Render racks
// ------------------------
function renderStacksGrid(bays) {
  if (!stacksGrid) return;

  if (!bays || bays.length === 0) {
    stacksGrid.innerHTML = `<div class="hint">No hay estibas configuradas en este bloque.</div>`;
    return;
  }

  const occ = occupancyIndex || new Map();

  const html = bays.map(b => {
    const used = b.used || 0;
    const cap = b.capacity || 0;
    const available = cap > 0 ? (used < cap) : true;

    const bayType = String(b.container_size_type || "40").toUpperCase();
    const bayTypeClass = bayType === "20" ? "is-bay-20" : "is-bay-40";
    const visual = getBayRackVisualVars(bayType);

    const rowOrder = getRowOrderForBay(b);
    const tierOrder = getTierOrderForBay(b);
    const colsCount = rowOrder.length;

    const badgeText = available ? "Disponible" : "Lleno";
    const badgeCls = available ? "badge-ok" : "badge-bad";

    const bayOcc = occ.get(b.code) || new Map();

    const headerCols = rowOrder
      .map(rn => `<div class="rack-colhdr">${fmtRow(rn)}</div>`)
      .join("");

    const rowsHtml = tierOrder.map(tn => {
      const slots = rowOrder.map(rn => {
        const key = `${rn}-${tn}`;
        const item = bayOcc.get(key);

        if (item) {
          const statusClass = getDispatchStatusClass(item);
          const statusLabel = getDispatchStatusLabel(item.dispatch_status);
          const sizeClass = getContainerSizeClass(item.size);

          return `
            <div class="rack-slot is-occupied ${statusClass} ${sizeClass}"
                data-action="pick-container"
                data-container-id="${item.id}"
                data-container-code="${item.code}"
                data-bay="${b.code}"
                data-row="${rn}"
                data-tier="${tn}"
                ${IS_TOUCH ? "" : `draggable="true"`}
                title="${item.code} · ${item.size || ""} · ${statusLabel} · ${b.code} · ${fmtRow(rn)} · ${fmtTier(tn)}">
              <span class="rack-code">${item.code}</span>
              <span class="rack-status">${item.size || ""} · ${statusLabel}</span>
            </div>
          `;
        }

        const canDrop = hasActiveContainer();
        let cls = "is-empty";

        if (canDrop) {
          const validKey = `${b.code}-${rn}-${tn}`;
          if (validDestinationsIndex.has(validKey)) {
            cls = "is-available";
          }
        }

        return `
          <div class="rack-slot ${cls}"
              data-action="pick-destination"
              data-bay="${b.code}"
              data-row="${rn}"
              data-tier="${tn}"
              title="${b.code} · ${bayType} pies · ${fmtRow(rn)} · ${fmtTier(tn)}">
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
      <div class="stack-card ${bayTypeClass}" data-baycard="${b.code}">
        <div class="stack-card-head">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
            <div>
              <div style="font-weight:950; font-size:16px;">
                Estiba ${b.code}
                <span class="yard-size-pill">${bayType} pies</span>
              </div>
              <div class="hint" style="margin-top:6px;">
                ${cap ? `${used}/${cap}` : `${used} usados`}
              </div>
            </div>
            <div class="${badgeCls}" style="font-size:11px; font-weight:900;">${badgeText}</div>
          </div>

          <div class="hint" style="margin-top:8px;">
            ${hasActiveContainer()
              ? "Toca un espacio verde para destino. Solo se habilitan estibas compatibles con el tamaño del contenedor."
              : "Toca un contenedor para seleccionarlo y moverlo."
            }
          </div>
        </div>

        <div
          class="rack"
          style="--rack-cols:${colsCount}; --rack-col-min:${visual.colMin}; --rack-slot-min-height:${visual.slotMinHeight};"
        >
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

  stacksGrid.onclick = onStacksGridClick;

  stacksGrid.querySelectorAll(".rack-slot").forEach(slot => {
    const action = slot.getAttribute("data-action");

    if (!IS_TOUCH && action === "pick-container") {
      slot.addEventListener("dragstart", (ev) => {
        const id = parseInt(slot.getAttribute("data-container-id"), 10);
        const code = slot.getAttribute("data-container-code");

        try {
          ev.dataTransfer.setData("text/plain", code || String(id));
        } catch (_) {}

        setSelectedContainer(id, code);
        clearDestinationSelection();
      });
    }

    slot.addEventListener("dragover", (ev) => {
      if (!hasActiveContainer()) return;
      if (slot.getAttribute("data-action") !== "pick-destination") return;
      if (!slot.classList.contains("is-available")) return;

      ev.preventDefault();
      slot.classList.add("yard-block-highlight");
    });

    slot.addEventListener("dragleave", () => {
      slot.classList.remove("yard-block-highlight");
    });

    slot.addEventListener("drop", async (ev) => {
      slot.classList.remove("yard-block-highlight");

      if (!hasActiveContainer()) return;
      if (slot.getAttribute("data-action") !== "pick-destination") return;
      if (!slot.classList.contains("is-available")) return;

      ev.preventDefault();
      await pickDestinationFromSlot(slot);
    });
  });

  if (state.suggested) {
    setSuggestionText(`${state.suggested.bay_code} · ${fmtRow(state.suggested.depth_row)} · ${fmtTier(state.suggested.tier)}`);

    if (confirmBar) {
      confirmBar.classList.remove("hidden");
    }
  }
}

async function onStacksGridClick(ev) {
  const slot = ev.target.closest(".rack-slot");
  if (!slot) return;

  const action = slot.getAttribute("data-action");

  if (action === "pick-container") {
    const id = parseInt(slot.getAttribute("data-container-id"), 10);
    const code = slot.getAttribute("data-container-code");

    setSelectedContainer(id, code);
    clearDestinationSelection();

    if (state.blockCode === "__MOUNTED__") {
      renderMountedContainersBlock();
    } else {
      renderStacksGrid(currentBaysList);
    }

    return;
  }

  if (action === "pick-destination") {
    if (!hasActiveContainer()) return;
    if (!slot.classList.contains("is-available")) return;

    await pickDestinationFromSlot(slot);
    return;
  }
}

async function pickDestinationFromSlot(slot) {
  stacksGrid.querySelectorAll(".rack-slot.is-selected").forEach(el => el.classList.remove("is-selected"));
  slot.classList.add("is-selected");

  const bay = slot.getAttribute("data-bay");
  const row = parseInt(slot.getAttribute("data-row"), 10);
  const tier = parseInt(slot.getAttribute("data-tier"), 10);

  state.bayCode = bay;
  state.rowNumber = row;
  state.tier = tier;
  state.suggested = { bay_code: bay, depth_row: row, tier: tier };

  const msg = `¿Deseas mover el contenedor ${state.containerCode} a:\n\n${bay} · ${fmtRow(row)} · ${fmtTier(tier)} ?`;

  const ok = window.confirm(msg);
  if (ok) {
    await confirmPlacement();
  } else {
    setSuggestionText(`${bay} · ${fmtRow(row)} · ${fmtTier(tier)}`);
    if (confirmBar) confirmBar.classList.remove("hidden");
  }
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

  if (confirmPlacementBtn) confirmPlacementBtn.disabled = true;

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

    await loadContainersInYard();
    occupancyIndex = buildOccupancyIndexForBlock(state.blockCode);

    clearDestinationSelection();
    renderStacksGrid(currentBaysList);

  } catch (e) {
    alert("Error de red al colocar contenedor");
  } finally {
    if (confirmPlacementBtn) confirmPlacementBtn.disabled = false;
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

    const statusLabel = getDispatchStatusLabel(item.dispatch_status);
    const statusClass = getDispatchStatusClass(item);

    const prelistText = item.is_prelist_visible && item.prelist
      ? `
        <div style="font-size:11px; margin-top:5px; color:rgba(253,224,71,.95); font-weight:900;">
          Prelista · ${item.prelist.load_date || "—"}${item.prelist.load_time ? " · " + item.prelist.load_time : ""}
        </div>
      `
      : "";

    return `
      <div class="container-item"
           ${IS_TOUCH ? "" : `draggable="true"`}
           data-container-id="${item.id}"
           data-container-code="${item.code}">
        <div style="display:flex; justify-content:space-between; gap:10px;">
          <div>
            <div style="font-weight:950;">${item.code}</div>

            <div class="hint" style="margin-top:6px;">
              ${item.size}${item.year ? " · " + item.year : ""}
            </div>

            <div class="yard-status-pill ${statusClass}">
              ${statusLabel}
            </div>

            ${prelistText}

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
      try {
        ev.dataTransfer.setData("text/plain", code || String(id));
      } catch (_) {}

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

async function mountSelectedContainer() {
  if (!hasActiveContainer()) {
    alert("Primero selecciona un contenedor.");
    return;
  }

  const c = getSelectedContainerData();
  const status = (c?.dispatch_status || "NORMAL").toUpperCase();

  if (!MOUNTABLE_STATUSES.has(status)) {
    alert("Este contenedor no está en estado válido para montar.");
    return;
  }

  const ok = window.confirm(
    `¿Deseas marcar como montado el contenedor ${state.containerCode}?`
  );

  if (!ok) return;

  if (mountContainerBtn) mountContainerBtn.disabled = true;
  if (mountContainerToolbarBtn) mountContainerToolbarBtn.disabled = true;

  try {
    const r = await fetch("/api/yard/mount-container", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify({
        container_id: state.containerId,
      }),
    });

    const data = await r.json();

    if (!r.ok) {
      alert(data.message || data.error || "No se pudo montar el contenedor.");
      return;
    }

    alert(`Contenedor ${data.container_code || state.containerCode} marcado como montado.`);

    await loadContainersInYard();
    await loadMountedContainers();

    if (state.blockCode === "__MOUNTED__") {
      renderMountedContainersBlock();
    } else if (state.blockCode) {
      occupancyIndex = buildOccupancyIndexForBlock(state.blockCode);
      renderStacksGrid(currentBaysList);
    }

    updateMountButtons();

  } catch (e) {
    alert("Error de red al montar el contenedor.");
  } finally {
    if (mountContainerBtn) mountContainerBtn.disabled = false;
    if (mountContainerToolbarBtn) mountContainerToolbarBtn.disabled = false;
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

if (mountContainerBtn) {
  mountContainerBtn.addEventListener("click", mountSelectedContainer);
}

if (mountContainerToolbarBtn) {
  mountContainerToolbarBtn.addEventListener("click", mountSelectedContainer);
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

// Confirm/Cancel (compat)
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
loadMountedContainers();

// Bloque preseleccionado (si existe)
if (window.YARD_INIT && window.YARD_INIT.block) {
  openBlock(window.YARD_INIT.block);
}



