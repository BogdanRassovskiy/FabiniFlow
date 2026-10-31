// Průvodce rozhraním: při první návštěvě ukáže, co je na které záložce.
// Kroky se berou z prvků s atributem data-tour (text) a data-tour-title (nadpis);
// prvky s data-tour-global patří k úvodní prohlídce horní lišty, ostatní k aktuální stránce.
// Co už uživatel viděl, se pamatuje v localStorage; tlačítko „?“ průvodce spustí znovu.
(function () {
  const STORE = "fabiniflow.tour";
  const page = document.body.dataset.tourPage || "page";
  const WELCOME = {
    title: "Vítejte ve FabiniFlow",
    text: "Krátce ukážeme, co najdete na jednotlivých záložkách. Průvodce můžete kdykoli spustit znovu tlačítkem „?“ vpravo nahoře.",
  };

  const load = () => { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch (e) { return {}; } };
  const save = (s) => { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* soukromý režim */ } };
  const visible = (el) => el.getClientRects().length > 0;

  function collect(global) {
    return [...document.querySelectorAll("[data-tour]")]
      .filter((el) => el.hasAttribute("data-tour-global") === global && visible(el))
      .map((el) => ({el, title: el.dataset.tourTitle || "", text: el.dataset.tour}));
  }

  let steps = [], index = 0, spot, pop;

  function build() {
    spot = document.createElement("div");
    spot.className = "tour-spot";
    pop = document.createElement("div");
    pop.className = "tour-pop";
    pop.setAttribute("role", "dialog");
    pop.innerHTML = `<div class="tour-step muted"></div><strong class="tour-title"></strong><p class="tour-text"></p>
      <div class="tour-actions"><button type="button" class="ghost small" data-act="skip">Přeskočit</button>
      <span></span><button type="button" class="ghost small" data-act="prev">Zpět</button>
      <button type="button" class="small" data-act="next">Další</button></div>`;
    pop.addEventListener("click", (e) => {
      const act = e.target.dataset.act;
      if (act === "next") go(index + 1);
      if (act === "prev") go(index - 1);
      if (act === "skip") finish();
    });
    document.body.append(spot, pop);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("keydown", onKey);
  }

  function onKey(e) {
    if (!pop) return;
    if (e.key === "Escape") finish();
    if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); go(index + 1); }
    if (e.key === "ArrowLeft") go(index - 1);
  }

  function place() {
    const step = steps[index];
    if (!step) return;
    const gap = 12, pad = 6, w = pop.offsetWidth, h = pop.offsetHeight;
    if (!step.el) {
      spot.classList.add("center");
      spot.removeAttribute("style");
      pop.style.left = `${(innerWidth - w) / 2}px`;
      pop.style.top = `${Math.max(gap, (innerHeight - h) / 3)}px`;
      return;
    }
    spot.classList.remove("center");
    const r = step.el.getBoundingClientRect();
    Object.assign(spot.style, {
      left: `${r.left - pad}px`, top: `${r.top - pad}px`,
      width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px`,
    });
    // Pod prvek, když se tam vejde; jinak nad něj; u vysokých prvků dovnitř k hornímu okraji.
    let top = r.bottom + pad + gap;
    if (top + h > innerHeight - gap) top = r.top - pad - gap - h;
    if (top < gap) top = Math.min(Math.max(r.top + gap, gap), innerHeight - h - gap);
    const left = Math.min(Math.max(r.left, gap), innerWidth - w - gap);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  function go(i) {
    if (i < 0) return;
    if (i >= steps.length) return finish();
    index = i;
    const step = steps[i];
    pop.querySelector(".tour-step").textContent = `${i + 1} / ${steps.length}`;
    pop.querySelector(".tour-title").textContent = step.title;
    pop.querySelector(".tour-text").textContent = step.text;
    pop.querySelector("[data-act=prev]").hidden = i === 0;
    pop.querySelector("[data-act=next]").textContent = i === steps.length - 1 ? "Hotovo" : "Další";
    if (step.el) {
      const r = step.el.getBoundingClientRect();
      if (r.top < 70 || r.bottom > innerHeight - 20) step.el.scrollIntoView({block: "center", behavior: "instant"});
    }
    place();
    pop.querySelector("[data-act=next]").focus({preventScroll: true});
  }

  function finish() {
    const seen = load();
    seen.global = true;
    seen.pages = {...seen.pages, [page]: true};
    save(seen);
    spot.remove(); pop.remove(); spot = pop = null;
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    document.removeEventListener("keydown", onKey);
  }

  function start(withGlobal) {
    if (pop) return;
    steps = withGlobal ? [WELCOME, ...collect(true)] : [];
    steps.push(...collect(false));
    if (!steps.length) return;
    build();
    go(0);
  }

  document.getElementById("tour-help")?.addEventListener("click", () => start(true));
  const seen = load();
  if (!seen.global) start(true);
  else if (!(seen.pages || {})[page]) start(false);
})();
