const svg = document.getElementById("yardSvg");
const blockSelect = document.getElementById("blockSelect");

const overlay = document.getElementById("availabilityOverlay");
const overlayCloseBtn = document.getElementById("overlayCloseBtn");
const overlayContainerCode = document.getElementById("overlayContainerCode");
const overlaySubtitle = document.getElementById("overlaySubtitle");
const overlaySelectedBay = document.getElementById("overlaySelectedBay");
const overlayConfirmWrap = document.getElementById("overlayConfirmWrap");
const overlaySuggestedSlot = document.getElementById("overlaySuggestedSlot");
const overlayConfirmBtn = document.getElementById("overlayConfirmBtn");

const containersList = document.getElementById("containersList");
const containerSearch = document.getElementById("containerSearch");
const refreshContainersBtn = document.getElementById("refreshContainersBtn");

const touchHint = document.getElementById("touchHint");
const selectedContainerBar = document.getElementById("selectedContainerBar");
const selectedContainerText = document.getElementById("selectedContainerText");
const clearSelectedBtn = document.getElementById("clearSelectedBtn");

const IS_TOUCH = ("ontouchstart" in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints > 0);

let allContainers = []; // cache en memoria

let dragCtx = {
  containerId: null,
  containerCode: null,
  droppedBlock: null,
  selectedBayCode: null,
  suggested: null,
};

function clearSvg() { while (svg.firstChild) svg.removeChild(svg.firstChild); }

function setOverlayOpen(open) {
  if (!overlay) return;
  overlay.classList.toggle("hidden", !open);
}

function resetOverlayState() {
  dragCtx.droppedBlock = null;
  dragCtx.selectedBayCode = null;
  dragCtx.suggested = null;

  if (overlayContainerCode) overlayContainerCode.textContent = "—";
  if (overlaySubtitle) overlaySubtitle.textContent = "Suelta un contenedor en un bloque para ver estibas disponibles.";
  if (overlaySelectedBay) overlaySelectedBay.textContent = "";
  if (overlaySuggestedSlot) overlaySuggestedSlot.textContent = "—";
  if (overlayConfirmWrap) overlayConfirmWrap.classList.add("hidden");

  clearHighlights();
}

function clearHighlights() {
  const bayRects = svg.querySelectorAll("rect[data-bay]");
  bayRects.forEach(el => {
    el.classList.remove("yard-bay", "available", "unavailable", "available-glow");
  });

  const blockRects = svg.querySelectorAll("rect[data-block]");
  blockRects.forEach(el => {
    el.classList.remove("yard-block-dropzone", "yard-block-highlight");
  });
}

function setSelectedBar(open, text) {
  if (!selectedContainerBar) return;
  selectedContainerBar.classList.toggle("hidden", !open);
  if (selectedContainerText) selectedContainerText.textContent = text || "—";
}

function setSelectedContainer(containerId, containerCode) {
  dragCtx.containerId = containerId;
  dragCtx.containerCode = containerCode;
  if (IS_TOUCH) setSelectedBar(true, `${containerCode} (#${containerId})`);
}

function clearSelectedContainer() {
  dragCtx.containerId = null;
  dragCtx.containerCode = null;
  setSelectedBar(false, "");
}

function drawBlockDropzones() {
  const blocks = [
    { code: "A", x: 10,  y: 10,  w: 490, h: 100 },
    { code: "B", x: 500, y: 10,  w: 490, h: 100 },
    { code: "C", x: 10,  y: 115, w: 490, h: 95 },
    { code: "D", x: 500, y: 115, w: 490, h: 95 },
  ];

  blocks.forEach(b => {
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("x", b.x); r.setAttribute("y", b.y);
    r.setAttribute("width", b.w); r.setAttribute("height", b.h);
    r.setAttribute("rx", 14);
    r.setAttribute("fill", "rgba(37,99,235,0.06)");
    r.setAttribute("stroke", "rgba(37,99,235,0.35)");
    r.setAttribute("stroke-width", "2");
    r.setAttribute("data-block", b.code);
    r.classList.add("yard-block-dropzone");

    // PC dragover/drop
    r.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      if (!dragCtx.containerId) return;
      r.classList.add("yard-block-highlight");
    });
    r.addEventListener("dragleave", () => r.classList.remove("yard-block-highlight"));
    r.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      r.classList.remove("yard-block-highlight");
      if (!dragCtx.containerId) return;
      await openAvailabilityForBlock(b.code);
    });

    // TOUCH: tap en bloque (con contenedor seleccionado)
    r.addEventListener("click", async () => {
      if (!IS_TOUCH) return;
      if (!dragCtx.containerId) {
        alert("Primero selecciona un contenedor en la bandeja.");
        return;
      }
      await openAvailabilityForBlock(b.code);
    });

    svg.appendChild(r);

    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", b.x + 14);
    t.setAttribute("y", b.y + 24);
    t.setAttribute("font-size", "14");
    t.setAttribute("font-weight", "700");
    t.setAttribute("fill", "#111");
    t.textContent = `Bloque ${b.code}`;
    svg.appendChild(t);
  });
}

function drawBay(bay, idx) {
  const padding = 10, gap = 6, totalW = 1000;
  const usableW = totalW - padding * 2 - gap * 14;
  const bayW = usableW / 15;
  const bayH = 160;
  const x = padding + idx * (bayW + gap);
  const y = 30;

  const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  r.setAttribute("x", x); r.setAttribute("y", y);
  r.setAttribute("width", bayW); r.setAttribute("height", bayH);
  r.setAttribute("rx", 10);
  r.setAttribute("fill", "#f5f5f5");
  r.setAttribute("stroke", "#666");
  r.setAttribute("stroke-width", "2");
  r.setAttribute("data-bay", bay.code);
  r.classList.add("yard-bay");

  const used = bay.used || 0;
  const cap = bay.capacity || 80;

  r.addEventListener("click", async () => {
    // Si overlay abierto: elegir estiba
    if (overlay && !overlay.classList.contains("hidden") && dragCtx.containerId) {
      await chooseBayAsPath(bay.code);
      return;
    }
    // Normal: detalle
    window.location.href = `/bay/${encodeURIComponent(bay.code)}`;
  });

  svg.appendChild(r);

  const t1 = document.createElementNS("http://www.w3.org/2000/svg", "text");
  t1.setAttribute("x", x + 10); t1.setAttribute("y", y + 22);
  t1.setAttribute("font-size", "14");
  t1.textContent = bay.code;
  svg.appendChild(t1);

  const t2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
  t2.setAttribute("x", x + 10); t2.setAttribute("y", y + 46);
  t2.setAttribute("font-size", "12");
  t2.textContent = `${used}/${cap}`;
  svg.appendChild(t2);
}

async function loadMap(blockCode) {
  const r = await fetch(`/api/yard/map?block=${encodeURIComponent(blockCode)}`);
  const data = await r.json();
  clearSvg();

  drawBlockDropzones();

  if (!data.bays) return;
  data.bays.sort((a,b) => a.bay_number - b.bay_number);
  data.bays.forEach((bay, idx) => drawBay(bay, idx));
}

/* =========================
   CONTENEDORES (BANDEJA)
========================= */

function renderContainersList(list) {
  if (!containersList) return;

  if (!list || list.length === 0) {
    containersList.innerHTML = `<div class="hint">No hay contenedores en patio.</div>`;
    return;
  }

  const html = list.map(item => {
    const pos = item.position ? `${item.position.bay_code} F${String(item.position.depth_row).padStart(2,"0")} N${item.position.tier}` : "Sin posición";
    return `
      <div class="container-item"
           ${IS_TOUCH ? "" : `draggable="true"`}
           data-container-id="${item.id}"
           data-container-code="${item.code}">
        <div style="display:flex; justify-content:space-between; gap:10px;">
          <div>
            <div style="font-weight:700;">${item.code}</div>
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

  // bind events
  const nodes = containersList.querySelectorAll(".container-item");
  nodes.forEach(el => {
    const id = parseInt(el.getAttribute("data-container-id"), 10);
    const code = el.getAttribute("data-container-code");

    // TOUCH: tap-to-select
    el.addEventListener("click", () => {
      if (!IS_TOUCH) return;
      setSelectedContainer(id, code);
    });

    // PC: dragstart/dragend
    el.addEventListener("dragstart", () => {
      if (IS_TOUCH) return;
      setSelectedContainer(id, code);
    });
    el.addEventListener("dragend", () => {
      if (IS_TOUCH) return;
      // No limpiamos automáticamente para permitir múltiples drops si el usuario quiere.
      // Si querés limpiar al soltar, se hace luego del place.
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

/* =========================
   OVERLAY (disponibilidad)
========================= */

async function openAvailabilityForBlock(blockCode) {
  dragCtx.droppedBlock = blockCode;

  if (overlayContainerCode) overlayContainerCode.textContent = dragCtx.containerCode || `#${dragCtx.containerId}`;
  if (overlaySubtitle) overlaySubtitle.textContent = `Bloque ${blockCode}: selecciona una estiba disponible.`;

  setOverlayOpen(true);
  await showAvailabilityForBlock(blockCode);
}

async function showAvailabilityForBlock(blockCode) {
  clearHighlights();

  // Nota: este endpoint lo agregamos en routes.py (te lo doy después)
  const r = await fetch(`/api/yard/block/${encodeURIComponent(blockCode)}/availability`);
  const data = await r.json();
  if (!data || !data.bays) return;

  // Forzar dropdown al bloque que se eligió
  if (blockSelect && blockSelect.value !== blockCode) {
    blockSelect.value = blockCode;
    await loadMap(blockCode);
  }

  const availability = new Map();
  data.bays.forEach(b => availability.set(b.code, b));

  const bayRects = svg.querySelectorAll("rect[data-bay]");
  bayRects.forEach(el => {
    const code = el.getAttribute("data-bay");
    const info = availability.get(code);

    if (!info || !info.available) {
      el.classList.add("unavailable");
      return;
    }

    el.classList.add("available", "available-glow");
  });
}

async function chooseBayAsPath(bayCode) {
  dragCtx.selectedBayCode = bayCode;
  if (overlaySelectedBay) overlaySelectedBay.textContent = `Estiba seleccionada: ${bayCode}`;

  // Nota: este endpoint lo agregamos en routes.py (te lo doy después)
  const r = await fetch(`/api/yard/bays/${encodeURIComponent(bayCode)}/last-available`);
  const data = await r.json();

  if (!r.ok || !data.ok) {
    dragCtx.suggested = null;
    if (overlaySuggestedSlot) overlaySuggestedSlot.textContent = "No hay espacio (estiba llena)";
    if (overlayConfirmWrap) overlayConfirmWrap.classList.add("hidden");
    return;
  }

  dragCtx.suggested = { bay_code: data.bay_code, depth_row: data.depth_row, tier: data.tier };
  if (overlaySuggestedSlot) overlaySuggestedSlot.textContent = `${data.bay_code} - F${String(data.depth_row).padStart(2,"0")} - N${data.tier}`;
  if (overlayConfirmWrap) overlayConfirmWrap.classList.remove("hidden");
}

async function confirmPlacement() {
  if (!dragCtx.containerId || !dragCtx.selectedBayCode) return;
  overlayConfirmBtn.disabled = true;

  try {
    // Nota: este endpoint lo agregamos en routes.py (te lo doy después)
    const r = await fetch(`/api/yard/place`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        container_id: dragCtx.containerId,
        to_bay_code: dragCtx.selectedBayCode
      })
    });

    const data = await r.json();
    if (!r.ok) {
      alert(data.error || "Error al colocar contenedor");
      return;
    }

    alert(`Colocado en ${data.bay_code} F${String(data.depth_row).padStart(2,"0")} N${data.tier}`);

    setOverlayOpen(false);
    resetOverlayState();

    // refrescar mapa y bandeja
    await loadMap(blockSelect.value);
    await loadContainersInYard();

    // en touch, mantener seleccionado opcional: yo lo limpio para evitar errores
    if (IS_TOUCH) clearSelectedContainer();

  } catch (e) {
    alert("Error de red al colocar contenedor");
  } finally {
    overlayConfirmBtn.disabled = false;
  }
}

/* =========================
   HOOKS
========================= */

if (overlayCloseBtn) {
  overlayCloseBtn.addEventListener("click", () => {
    setOverlayOpen(false);
    resetOverlayState();
  });
}

if (overlayConfirmBtn) {
  overlayConfirmBtn.addEventListener("click", confirmPlacement);
}

if (blockSelect) {
  blockSelect.addEventListener("change", () => {
    setOverlayOpen(false);
    resetOverlayState();
    loadMap(blockSelect.value);
  });
}

if (containerSearch) {
  containerSearch.addEventListener("input", () => {
    renderContainersList(filterContainers(containerSearch.value));
  });
}

if (refreshContainersBtn) {
  refreshContainersBtn.addEventListener("click", loadContainersInYard);
}

if (clearSelectedBtn) {
  clearSelectedBtn.addEventListener("click", clearSelectedContainer);
}

// Touch hint
if (touchHint) {
  touchHint.classList.toggle("hidden", !IS_TOUCH);
}

loadMap((window.YARD_INIT && window.YARD_INIT.block) || "A");
loadContainersInYard();
