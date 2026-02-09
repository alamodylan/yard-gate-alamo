const input = document.getElementById("container_code");
if (input) {
  input.addEventListener("input", () => {
    // Solo letras/números
    let raw = input.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    // 4 letras + 6 números + 1 número
    raw = raw.slice(0, 11);

    const part1 = raw.slice(0, 4);
    const part2 = raw.slice(4, 10);
    const part3 = raw.slice(10, 11);

    let out = part1;
    if (part2.length) out += "-" + part2;
    if (part3.length) out += "-" + part3;

    input.value = out;
  });
}