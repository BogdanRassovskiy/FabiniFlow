# FabiniFlow – AI Operations Agent

Case study pro FABINI: AI agent, který řeší provozní požadavky e-shopu.
Požadavek dostane v běžné řeči, sám si zjistí potřebné informace přes nástroje,
vyhodnotí je, rizikové kroky nechá schválit člověkem a vrátí strukturovaný výsledek.

Vše běží v jedné Django aplikaci. Externí systémy (e-shop, platby, sklad, integrace, helpdesk)
jsou simulované jako HTTP API, ve kterém si lze zapnout výpadky a zpoždění.

## Živé demo

**http://139.59.136.141:4445/**

| Záložka | Adresa |
|---|---|
| Zákazník (chat) | http://139.59.136.141:4445/zakaznik/ |
| Administrace obchodů (konzole agenta, schvalování) | http://139.59.136.141:4445/administrace/ |
| Simulace výpadků | http://139.59.136.141:4445/administrace/simulace/ |
| Trace log | http://139.59.136.141:4445/trace/ |

Tlačítko **↺ Reset dema** vpravo nahoře vrátí všechna data do výchozího stavu
(objednávky, chaty, schválení, incidenty, nastavení simulace i trace). Doporučuji ho použít před každou ukázkou.

## Spuštění u sebe

Potřeba: Python 3.12+ a klíč k [OpenRouter](https://openrouter.ai/keys).

```bash
git clone https://github.com/BogdanRassovskiy/FabiniFlow.git
cd FabiniFlow
cp .env.example .env        # doplňte OPENROUTER_API_KEY
python3 start.py --reset
```

Aplikace pak běží na `http://<adresa-serveru>:4445/` (port 4445 musí být povolený ve firewallu).
Lokálně stačí v `.env` nastavit `FABINI_HOST=127.0.0.1` a otevřít `http://127.0.0.1:4445/`.

`start.py` při prvním spuštění vytvoří `.venv` a nainstaluje závislosti.
Volba `--reset` vrátí data do výchozího stavu, stejně jako tlačítko Reset dema v aplikaci.

## Co v aplikaci najdete

| Záložka | K čemu slouží |
|---|---|
| **Zákazník** | Chat zákazníka s obchodem fabini.cz nebo fabini.sk. Agent během rozhovoru vyplní a založí požadavek. |
| **Administrace obchodů** | Konzole agenta pro interní tým, žádosti o schválení (červený odznak), objednávky, sklad, požadavky a incidenty. |
| **Administrace → Simulace** | Zapínání výpadků externích systémů: timeout, chyba 500, nedostupnost 503, občasné chyby, zpoždění. |
| **Trace** | Historie všech rozhodnutí a volání. U každého záznamu je vidět, kdo rozhodl: **LLM**, **SCRIPT** (kód), **HUMAN** (člověk) nebo **SYSTEM** (odpověď externího systému). |

Model se vybírá vpravo nahoře, vedle je tlačítko **↺ Reset dema**. Výchozí je `anthropic/claude-haiku-5.5`, levný a s podporou obrázků.

## Splnění zadání

| Požadavek | Jak je to řešené |
|---|---|
| Přijmout požadavek v přirozeném jazyce | Konzole v administraci (interní tým) a zákaznický chat (zákazník). |
| Rozhodnout, jaké informace potřebuje | Rozhoduje model. Nezávislá čtení volá najednou. |
| Samostatně zvolit a zavolat nástroje | Skutečné tool calling přes OpenRouter (OpenAI-kompatibilní API). Nástroje jsou v `agent/tools.py`. |
| Vyhodnotit výsledky a pokračovat | Smyčka agenta v `agent/runner.py` se opakuje, dokud model volá nástroje. Po úspěšném `retry_sync` si například znovu načte stav. |
| Provést povolenou akci nebo požádat o schválení | Čtení proběhne hned. `retry_sync` a `create_ticket` jsou rizikové: běh se pozastaví, v administraci se objeví žádost a po schválení nebo zamítnutí agent pokračuje. |
| Vrátit strukturovaný výsledek s kroky | Agent končí voláním `submit_result` (stav, shrnutí, příčina, doporučení). Provedené akce a kroky doplní kód z historie, model je nemůže vymyslet. |
| Nástroje get_order … create_ticket | Všech šest je implementováno jako HTTP volání na simulované systémy (`simulator/`).  Navíc `search_orders`: když v požadavku chybí číslo objednávky, agent ji najde podle jména, e-mailu nebo města. |
| Ošetření chyb | Timeout 5 s, přechodné chyby (502/503/504, síť) se opakují s backoffem. Trvalé chyby dostane agent jako výsledek a musí s nimi pracovat. Chyby modelu se také opakují a nakonec běh korektně ukončí. |
| Žádné nekonečné smyčky | Max. 12 kroků modelu a 30 volání nástrojů na běh. Každý nástroj má vlastní limit (`retry_sync` a `create_ticket` jen 1×). Stejné čtení se neprovede dvakrát, vrátí se z cache. |
| Schvalování rizikových operací | Fronta žádostí v administraci. O každé se rozhoduje jen jednou a rozhodnutí se zapíše do trace jako HUMAN. |
| Historie rozhodnutí a volání | `data/runtime/trace.json`, záložka Trace a časová osa přímo v konzoli agenta. |

## Ukázkové scénáře

Všechny jsou připravené jako tlačítka v konzoli agenta (Administrace obchodů).
Před ukázkou klikněte na **↺ Reset dema** vpravo nahoře.

**1. Úspěch: přechodná chyba synchronizace (100245)**
> Zjisti, proč objednávka 100245 nebyla expedována, a pokud je problém v synchronizaci, navrhni další postup.

Agent načte objednávku, platbu, expedici a logy a najde 3× TIMEOUT při předání do skladu.
Navrhne `retry_sync`, ten čeká na schválení. Po schválení se objednávka předá do skladu,
agent ověří nový stav a vrátí `resolved`.

**2. Úspěch: zákaznický chat**
Na záložce Zákazník (fabini.cz) napište *„Objednávku 100245 jsem zaplatil, ale nebyla odeslána“*
nebo přiložte snímek potvrzení platby. Agent zjistí typ požadavku, vyžádá si e-mail
(ověří ho proti objednávce), prověří objednávku a založí požadavek `REQ-…`.
Vpravo je vidět, jak se požadavek postupně vyplňuje.

**3. Chyba v datech: retry nepomůže (100247)**
> Proč objednávka 100247 stále není ve skladu? Vyřeš to.

V logu je chyba validace: produkt nemá ve skladovém systému mapování pro fabini.sk.
Agent pozná, že opakování nemá smysl, a navrhne `create_ticket` s diagnózou.
Po schválení vznikne incident `INC-…` a výsledek je `escalated`.

**4. Chyba: neexistující objednávka a výpadek systému**
> Zkontroluj objednávku 100999.

Systémy vrátí 404 a agent to korektně vyhodnotí, nic si nevymýšlí.
Pro výpadek zapněte v Simulaci např. sklad (WMS) na „nedostupný 503“ a spusťte scénář 1.
V trace je vidět opakování s backoffem a výsledek, který s výpadkem počítá.

## Architektura

```
Prohlížeč ── Django (web) ──┬── agent: smyčka, nástroje, limity, schvalování ── OpenRouter (LLM)
                            │        └── HTTP ──► simulator (/mock/…) ◄── nastavení výpadků
                            └── core: JSON úložiště + trace
```

- **web/** – stránky a JSON API pro frontend (bez frameworku, čisté JS s pollingem).
- **agent/** – `runner.py` (smyčka a ochrany), `tools.py` (registr nástrojů s metadaty: riziko, limit, kanál),
  `llm.py` (OpenRouter), `approvals.py` (schvalování), `conversations.py` + `handoff.py` (zákaznický chat),
  `context/fabini.md` (kdo je agent a kde pracuje).
- **simulator/** – simulované externí systémy včetně obchodních pravidel (retry jen u zaplacené objednávky ap.).
- **core/** – ukládání do JSON souborů (atomický zápis, zámek) a trace.

**Technologie:** Python 3.12+, Django 6, OpenRouter (výchozí Claude Haiku 5.5), requests.
Data se ukládají do JSON (`data/seed` → `data/runtime`), databáze pro case study není potřeba.

**Hlavní myšlenka:** model rozhoduje, *co* udělat, ale *co smí*, hlídá kód.
Limity, oprávnění, schvalování a ověřování zákazníka nejsou v promptu, ale v `runner.py` a `tools.py`.
Proto jsou v trace odlišené rozhodnutí LLM a zásahy SCRIPT.

## Nad rámec zadání

- **Zákaznický chat s rozpracovaným požadavkem.** Agent se doptává po jednom údaji, dokud není požadavek
  kompletní (typy: stav objednávky, platba, reklamace, vrácení, jiné).
- **Ověření zákazníka v kódu.** Agent vidí jen objednávku, ke které zákazník zná správný e-mail.
  Objednávka z jiného obchodu se odmítne. Po 3 špatných pokusech se požadavek zamkne a administrace dostane upozornění.
- **Předání člověku.** Když je zákazník podrážděný nebo chce mluvit s člověkem, přijde administraci žádost.
  Po přijetí operátor píše přímo do chatu, po zamítnutí pokračuje agent. Zákazník žádost nevidí,
  takže se nenaučí, jak agenta obejít.
- **Snímky obrazovky.** Zákazník přiloží připravený snímek (potvrzení platby, sledování zásilky, e-mail).
  Model ho přečte, ale údaje z něj bere jen jako tvrzení zákazníka a ověřuje je nástroji.
- **Vyhledání objednávky podle zákazníka** (`search_orders`), např. *„Zákazník Tomáš Procházka si stěžuje,
  že mu nepřišla objednávka“*. Hledá bez ohledu na diakritiku. Jen v administraci: v zákaznickém chatu
  by šlo o únik cizích objednávek, tam se zákazník ověřuje číslem objednávky a e-mailem.
- **Dva obchody** (fabini.cz v češtině a Kč, fabini.sk ve slovenštině a €) s vlastními objednávkami a skladem.
- **Simulace výpadků** nastavitelná z administrace, i jen pro vybrané objednávky.
- **Výběr modelu** z OpenRouter přímo v UI (jen modely s podporou nástrojů, s cenou).

## Omezení

Jde o demo, ne o produkční řešení. Administrace nemá přihlášení, CSRF ochrana je vypnutá,
aplikace běží na vývojovém serveru Django v jednom procesu (agent běží ve vláknech)
a data jsou sdílená všemi návštěvníky.
