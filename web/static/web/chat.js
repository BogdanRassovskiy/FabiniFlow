// Společné vykreslování chatu a rozpracovaného požadavku
// (zákaznický chat i chat operátora v administraci).
window.FabiniChat = (function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

  // perspective: "customer" – zákazník vpravo; "operator" – operátor a agent vpravo.
  const AUTHORS = {agent: "Virtuální asistent", operator: "Kolega z podpory", customer: "Zákazník"};

  function renderMessages(log, messages, {greeting = "", perspective = "customer"} = {}) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.innerHTML = greeting + messages.map((m) => {
      if (m.role === "system") return `<div class="msg system">${esc(m.text)}</div>`;
      const mine = perspective === "customer" ? m.role === "customer" : m.role !== "customer";
      const showAuthor = m.role === "operator" || (perspective === "operator" && m.role !== "customer");
      return `<div class="msg ${mine ? "user" : "bot"} role-${m.role}">
        ${showAuthor ? `<small class="author">${AUTHORS[m.role]}</small>` : ""}${m.attachment_url
          ? `<a class="msg-attachment" href="${esc(m.attachment_url)}" target="_blank" rel="noopener"><img src="${esc(m.attachment_url)}" alt="Příloha"></a>`
          : ""}${esc(m.text)}</div>`;
    }).join("");
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function fieldState(f) {
    if (!f.value) return `<span class="field-state empty">${f.required ? "chybí" : "nepovinné"}</span>`;
    if (f.verified === true) return `<span class="field-state ok">${f.verifiable ? "✓ ověřeno systémem" : "✓ od zákazníka"}</span>`;
    if (f.verified === false) return `<span class="field-state bad">✗ ${esc(f.error)}</span>`;
    return `<span class="field-state wait">⏳ ${esc(f.error || "ověřuje se")}</span>`;
  }

  function renderDraft(body, pill, d, previous, emptyHtml) {
    if (d.locked) { pill.className = "pill bad"; pill.textContent = "Zamčeno – ověření se nezdařilo"; }
    else if (d.request_id) { pill.className = "pill ok"; pill.textContent = "Založeno " + d.request_id; }
    else if (d.ready) { pill.className = "pill ok"; pill.textContent = "Připraveno k založení"; }
    else if (d.type) { pill.className = "pill warn"; pill.textContent = "Doplňuje se"; }
    else { pill.className = "pill neutral"; pill.textContent = "Čeká na zákazníka"; }

    if (!d.type) { body.innerHTML = emptyHtml; return {}; }
    const pct = d.total ? Math.round(100 * d.filled / d.total) : 0;
    body.innerHTML = `
      <div class="ticket-type"><span class="muted">Typ</span><strong>${esc(d.type_label)}</strong></div>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <small class="muted">${d.filled} / ${d.total} povinných údajů</small>
      <dl class="ticket-fields">
        ${d.fields.map((f) => `
          <div class="ticket-field ${f.value ? "filled" : ""} ${previous[f.key] !== f.value && f.value ? "flash-in" : ""}">
            <dt>${esc(f.label)}${f.required ? ' <span class="req">*</span>' : ""}</dt>
            <dd>${f.value ? esc(f.value) : `<span class="placeholder">${esc(f.hint)}${f.choices.length ? " (" + f.choices.join(" / ") + ")" : ""}</span>`}</dd>
            ${fieldState(f)}
          </div>`).join("")}
      </dl>`;
    return Object.fromEntries(d.fields.map((f) => [f.key, f.value]));
  }

  const csrf = () => document.cookie.match(/csrftoken=([^;]+)/)?.[1];

  async function post(url, data) {
    return fetch(url, {method: "POST", headers: {"X-CSRFToken": csrf()}, body: new URLSearchParams(data)});
  }

  return {esc, renderMessages, renderDraft, post};
})();
