// static/js/yard_bay.js
(function () {
  const ctx = window.BAY_CTX || {};
  const toBlock = document.getElementById("toBlock");
  const toBay = document.getElementById("toBay");
  const dropZone = document.getElementById("dropZone");
  const destText = document.getElementById("destText");
  const refreshBtn = document.getElementById("refreshBtn");

  const containerList = document.getElementById("containerList");

  let draggedContainerId = null;
  let draggedCode = null;

  function setDestLabel() {
    const b = (toBlock.value || "").toUpperCase();
    const bayCode = (toBay.value || "").toUpperCase();
    if (!b || !bayCode) {
      destText.textContent = "—";
      dropZone.style.borderColor = "#999";
      return;
    }
    destText.textContent = `${bayCode}`;
    dropZone.style.borderColor = "#2563eb";
  }

  async function loadBaysForBlock(blockCode) {
    const r = await fetch(`/api/yard/bays?block=${encodeURIComponent(blockCode)}`, {
      headers: { "Accept": "application/json" }
    });
    const data = await r.json();

    toBay.innerHTML = "";
    (data.bays || []).forEach(b => {
      const opt = document.createElement("option");
      opt.value = b.code; // aquí usamos CODE directamente para move/place
      opt.textContent = `${b.bay_number} (${b.code})`;
      toBay.appendChild(opt);
    });

    // Por defecto: si estamos en este mismo bloque, seleccionar la estiba actual
    const currentBay = (ctx.currentBay || "").toUpperCase();
    if (currentBay && blockCode.toUpperCase() === (ctx.currentBlock || "").toUpperCase()) {
      const match = [...toBay.options].find(o => o.value === currentBay);
      if (match) toBay.value = currentBay;
    }

    setDestLabel();
  }

  async function doMove(containerId, targetBayCode) {
    const r = await fetch("/api/yard/move", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json"
      },
      body: JSON.stringify({
        container_id: Number(containerId),
        to_bay_code: targetBayCode,
        mode: "auto"
      })
    });

    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(data.error || "No se pudo mover el contenedor");
      return false;
    }
    return true;
  }

  function attachDesktopDragEvents(chip) {
    chip.addEventListener("dragstart", (e) => {
      draggedContainerId = chip.dataset.containerId;
      draggedCode = chip.dataset.code || "";

      try {
        e.dataTransfer.setData("text/plain", draggedContainerId);
      } catch (_) {}
      chip.style.opacity = "0.6";
    });

    chip.addEventListener("dragend", () => {
      chip.style.opacity = "1";
    });
  }

  // Touch support (tablet/celular): long-press -> "modo arrastre"
  function attachTouchDrag(chip) {
    let pressTimer = null;
    let ghost = null;
    let dragging = false;

    function cleanup() {
      if (pressTimer) clearTimeout(pressTimer);
      pressTimer = null;
      dragging = false;
      if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
      ghost = null;
      chip.style.opacity = "1";
    }

    chip.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) return;

      pressTimer = setTimeout(() => {
        dragging = true;
        draggedContainerId = chip.dataset.containerId;
        draggedCode = chip.dataset.code || "";

        // ghost
        const rect = chip.getBoundingClientRect();
        ghost = chip.cloneNode(true);
        ghost.style.position = "fixed";
        ghost.style.left = rect.left + "px";
        ghost.style.top = rect.top + "px";
        ghost.style.width = rect.width + "px";
        ghost.style.opacity = "0.85";
        ghost.style.pointerEvents = "none";
        ghost.style.zIndex = "9999";
        ghost.style.transform = "scale(1.02)";
        document.body.appendChild(ghost);

        chip.style.opacity = "0.4";
        dropZone.style.borderColor = "#2563eb";
      }, 350); // long press ~350ms
    }, { passive: true });

    chip.addEventListener("touchmove", (e) => {
      if (!dragging || !ghost) return;
      const t = e.touches[0];
      ghost.style.left = (t.clientX - ghost.offsetWidth / 2) + "px";
      ghost.style.top = (t.clientY - ghost.offsetHeight / 2) + "px";
    }, { passive: true });

    chip.addEventListener("touchend", async (e) => {
      if (!pressTimer && !dragging) return;

      // Si no llegó a long press, es un tap normal
      if (!dragging) {
        cleanup();
        return;
      }

      // drop: verificar si soltó encima del dropZone
      const dz = dropZone.getBoundingClientRect();
      const touch = (e.changedTouches && e.changedTouches[0]) ? e.changedTouches[0] : null;

      if (touch) {
        const x = touch.clientX;
        const y = touch.clientY;
        const inside = x >= dz.left && x <= dz.right && y >= dz.top && y <= dz.bottom;

        if (inside) {
          const targetBayCode = (toBay.value || "").toUpperCase();
          if (!targetBayCode) {
            alert("Selecciona una estiba destino.");
            cleanup();
            return;
          }

          if (!confirm(`Mover ${draggedCode || "contenedor"} a ${targetBayCode}?`)) {
            cleanup();
            return;
          }

          const ok = await doMove(draggedContainerId, targetBayCode);
          if (ok) {
            alert("Movimiento registrado ✅");
            window.location.reload();
          }
        }
      }

      cleanup();
    }, { passive: true });

    chip.addEventListener("touchcancel", cleanup, { passive: true });
  }

  function initChips() {
    const chips = containerList.querySelectorAll(".chip");
    chips.forEach(chip => {
      attachDesktopDragEvents(chip);
      attachTouchDrag(chip);
    });
  }

  // Dropzone desktop
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.style.borderColor = "#2563eb";
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.style.borderColor = "#999";
  });

  dropZone.addEventListener("drop", async (e) => {
    e.preventDefault();
    dropZone.style.borderColor = "#999";

    let containerId = draggedContainerId;

    try {
      const t = e.dataTransfer.getData("text/plain");
      if (t) containerId = t;
    } catch (_) {}

    const targetBayCode = (toBay.value || "").toUpperCase();
    if (!containerId) {
      alert("No se detectó el contenedor.");
      return;
    }
    if (!targetBayCode) {
      alert("Selecciona una estiba destino.");
      return;
    }

    if (!confirm(`Mover contenedor a ${targetBayCode}?`)) return;

    const ok = await doMove(containerId, targetBayCode);
    if (ok) {
      alert("Movimiento registrado ✅");
      window.location.reload();
    }
  });

  // Events dropdowns
  toBlock.addEventListener("change", () => loadBaysForBlock(toBlock.value));
  toBay.addEventListener("change", setDestLabel);

  refreshBtn.addEventListener("click", () => window.location.reload());

  // Init
  (function boot() {
    // default block: current block
    if (ctx.currentBlock) toBlock.value = ctx.currentBlock;
    loadBaysForBlock(toBlock.value);
    initChips();
    setDestLabel();
  })();
})();

