# HyperFrames prompt — UK Founder Radar architecture

Paste this whole file as the first message to HyperFrames (workflow: **faceless-explainer**).

**Locked choices (do not re-interview these):**

- `workflow:` faceless-explainer
- `angle:` concept-explainer with process (one idea; morning steps inside it)
- `length:` ~98 seconds
- `destination:` YouTube / embed
- `aspect:` 1920×1080 (16:9)
- `language:` en-GB
- `audience:` anyone — a smart twelve-year-old, a fund associate, a new engineer. Same words.
- `message:` Everything important lives in one notebook, and the scores are numbers you can check — never a model's mood.
- `style_preset:` cartesian
- `VO_MODE:` **verbatim** — use the locked script below, word for word. Do not rewrite, reorder, or “improve” the narration.
- `flow:` automation
- `storyboard:` yes
- `voice:` warm British teaching voice (HeyGen: a calm UK female if available; otherwise Marcia). Not American broadcast. Not a trailer VO.
- `music:` quiet analog paper-and-clock underscore, low under the voice. Not epic. Not lo-fi beats.

Also attach `SCRIPT.md` from this folder if the run accepts files. If it does not, the script in this prompt is the source of truth.

---

## Intent

Make a faceless explainer of **UK Founder Radar** architecture so a person who has never seen the code still understands how the machine is put together.

This is **not** a product launch, not a website tour, not a feature list.

Thesis the viewer should walk away with:

> One notebook holds the truth. Windows (sheet, website, phone) look at it. A helper may read and translate. It may never invent a score.

Tone: kid-clear plain language. Short sentences. Contractions. Like explaining a clever kitchen to a curious person. Never salesy. Never “unlock”, “seamless”, “powerful platform”, “AI-powered dealflow”.

---

## What this system actually is (facts — do not invent)

UK Founder Radar is a daily scan of **young UK startups**, matched to four funds: **Northstar**, **DSW Ventures**, **Outward**, and **Anticus**.

A win is: you introduce a fund to a company they have not already seen.

**Do not invent fund descriptors.** Do not call Outward “government-backed”. Do not add funds. If a fund appears on screen, use only the four names above.

### The picture

A small computer (a Linux VPS) wakes at **half past six, London time**, and runs `founder-radar`.

Three layers:

1. **Engine** — Python. Fetch, match, hard rules, scores, write-out. Boring on purpose. No AI in scoring. No internet in scoring.
2. **Reader** — two boxed helper jobs only: turn article prose into structured facts; after scoring, veto a Today card that is clearly the wrong company (with a stored reason).
3. **Front desk** — Hermes on Telegram. Turns chat into CLI commands. Holds no scores, no thresholds, no company database. If Hermes is down, the morning scan still runs.

Three windows onto one notebook (SQLite):

- Google Sheet — rules you can edit (criteria, weights, sources). Not the morning review surface.
- Today / Kept website — morning pass. 1 yes / 2 maybe / 3 no.
- Telegram — the ping. Keep/reject only counts if it is saved for real (same notebook), not chat-only.

Morning steps (on-screen labels may use the real names; voice stays easy):

1. Config — read the rules from the sheet
2. Fetch — collect clues (news, grants, spinouts, company register)
3. Extract — helper reads long articles into short facts
4. Resolve — same company is not listed twice
5. Enrich — extras (place, filings). Never guess a missing fact
6. Gate + score — **the heart**. Hard rules, then Match / Fresh-style scores. Same inputs → same numbers. No AI. No internet.
6.5 Today QA — helper may drop a wrong card. Cannot add, score, or merge. If Hermes is down, this check fails open (pipeline continues).
7. Render — write the notebook into the sheet and send the ping when it is safe

Hard AI rule (must appear as two columns, exact meaning):

**A helper may**

- Read a news story and pull out the facts
- Turn a chat message into the right command
- Say “this card should not be on Today” after scoring (with a reason)

**A helper may not**

- Decide if a company passes a hard rule
- Make up or change a score
- Decide if two records are the same company
- Add a company to the sheet by itself

Morning buttons:

- 1 Worth contacting → Kept
- 2 Unsure → Kept
- 3 Not for me → remembered, never on Today again

Quiet days with zero companies are fine.

Geography: **UK companies only** on the morning list.

---

## Visual metaphor (the whole video is one world)

Treat the architecture as a **paper notebook on a drafting table**.

- The notebook is the hero object (this is SQLite). It never leaves the film.
- Sheet, website, and phone are **windows** that open around the notebook — not three separate apps in a tech collage.
- The engine is a precise drawing machine, not a glowing brain.
- The helper is a thin reading lamp / highlighter — it lights text, it does not steer the scores.
- Hermes is a small front-desk bell or nameplate, clearly removable.
- The morning run is **one growing diagram on the same page**, not eight PowerPoint slides.
- Stage 6 is a stamp or lock that clicks. That click is the emotional peak.

On-screen labels may be technical (`SQLite`, `founder-radar`, `Hermes`, `①–⑦`). Spoken words stay easy (`notebook`, `engine`, `helper`, `front desk`). That dual register is the point.

---

## Design

Adopt HyperFrames preset **`cartesian`**.

Warm stone paper (`#EDE8E0`), near-black ink (`#1A1A1A`), taupe hairlines (`#B8B0A4`), Playfair Display + Inter. Compass rings. **Zero shadow, zero fill, no neon.**

One accent only: the taupe line, plus a single ink stamp for “the heart” / “no A I”.

Lazy defaults to refuse: cyan-on-dark HUD, purple-blue gradients, glassmorphism, stock photos of founders, laptop mockups, network-node explosions, robot mascots, gradient text.

---

## Story (10 frames — keep this order)

Rhythm: `hook-PUNCH — breathe — name — world — parts — process-BUILD — HEART — rule — buttons — land`

| Frame | Job | On screen (invented, faceless) | Blueprint candidate |
| --- | --- | --- | --- |
| 01 Hook | Curiosity | Huge type: “Every morning.” A short list of company names drafts itself on paper. | kinetic-type-beats |
| 02 Too late | Pain | Old fund-site pages stack up, grey, already-funded. The pile looks tired. | kinetic-type-beats |
| 03 Birthdays | Name the idea | A company “birthday” stamp. Four fund names appear as quiet labels — Northstar, DSW Ventures, Outward, Anticus. No extra adjectives. | titlecard-reveal |
| 04 Notebook | The world | One open notebook centre. Three windows hinge open: Sheet · Today · Phone. | constellation-hub |
| 05 Three parts | Mechanism | Three stations on one page: Engine / Helper / Front desk. Front desk can lift off; engine stays. | spatial-pan-stations |
| 06 Wake + gather | Process ①–⑤ | Horizontal morning strip. Stations light in order: rules → clues → facts → merge → extras. Same page as frame 04’s notebook. | spatial-pan-stations |
| 07 The heart | Process ⑥–⑦ | Stage 6 STAMPS. “Same ingredients → same numbers.” Then a thin check (⑥½) and a phone ping (⑦). | dataviz-countup **or** kinetic-type-beats |
| 08 Helper rules | The law | Two columns. Left: MAY (check). Right: MAY NOT (bar). | comparison-split |
| 09 Three buttons | How you use it | Giant 1 / 2 / 3. 1 and 2 flow into a tray labelled Kept. 3 goes to a locked drawer labelled never again. | grid-card-assemble |
| 10 Land | Thesis | Return to the notebook. Closing line on paper. | titlecard-reveal |

Transitions: Frame 1 `cut`. Frames 2–5 `crossfade`. Frames 6–7 `push-slide RIGHT` (one continuous morning). Frame 8 `cut`. Frames 9–10 `crossfade`.

Captions: on, matching the verbatim script.

---

## Locked narration (VO_MODE = verbatim)

Do not change words. Split across frames at the line breaks below. Phrase-segmented on em dashes and short sentences so type reveals when the voice names them.

**Voice direction:** Warm, unhurried, teaching. Smile in the voice without performing. Pause after “No A I.” Pause after “same numbers.”

### Line 1 — Hook (Frame 1)

Every morning, a short list of young U K companies is waiting.

### Line 2 — Too late (Frame 2)

The old lists were too late. Fund websites only show companies that already got money.

### Line 3 — Birthdays (Frame 3)

This tool starts from birthdays instead. It finds young U K startups — then says which of four funds might care.

### Line 4 — Notebook (Frame 4)

Everything important lives in one notebook. The sheet, the website, and your phone are just windows onto it.

### Line 5 — Three parts (Frame 5)

Three parts. An engine that scans and scores. A helper that reads stories into facts. A front desk that turns chat into the right button. If the desk takes a day off — the engine still runs.

### Line 6 — Wake (Frame 6)

At half past six, London time, the computer wakes up. It reads the rules. Gathers clues. Turns long articles into short facts.

### Line 7 — Heart (Frame 7)

Then the heart: hard rules, then scores. Same ingredients — same numbers. No A I. No internet. One last check drops the wrong cards. Then a ping goes to your phone.

### Line 8 — The law (Frame 8)

A helper may read. It may turn chat into a command. It may never invent a score — or add a company by itself.

### Line 9 — Buttons (Frame 9)

Open Today. Press one for yes. Two for maybe. Three for no. Yes and maybe go to Kept. No never comes back tomorrow.

### Line 10 — Land (Frame 10)

If a company drops off, the answer is a number you can check — not "the computer felt differently." One notebook. Checkable scores. A list that is actually young.

---

## Negative prompt

- Do not film a real website or capture founder-radar UI.
- Do not explain Python packages, systemd, Docker, or the repo tree.
- Do not say scoring is AI, ML, embeddings, or “smart matching”.
- Do not let Hermes appear as the brain, scheduler, or database.
- Do not put rejected companies back on the morning list in any graphic.
- Do not show non-UK geography as in-scope.
- Do not add a CTA to sign up, visit a URL, or “book a demo”.
- Do not rewrite the script into marketing English.

Build `SCRIPT.md` and `STORYBOARD.md` from the locked lines above, then produce the video.
