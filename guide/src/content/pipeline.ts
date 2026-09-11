export type AiKind = "no" | "yes" | "veto"

export interface PipelineStage {
  id: string
  /** Display label for UI (can use circled numerals). */
  numeral: string
  /** ASCII-safe label for PDF fonts. */
  numeralPlain: string
  name: string
  easy: string
  summary: string
  ai: AiKind
  network: boolean
  deterministic: boolean
  failure: string
  technicalNote?: string
  highlight?: boolean
}

export const pipelineStages: PipelineStage[] = [
  {
    id: "config",
    numeral: "①",
    numeralPlain: "1",
    name: "Config",
    easy: "Read the rules from the Google Sheet so today’s run knows what “good” means.",
    summary: "Read and validate the Google Sheet (Fund Criteria, Weights, Settings, Sources).",
    ai: "no",
    network: true,
    deterministic: true,
    failure: "Typo → last-known-good config; Status column reports the error. Run continues.",
  },
  {
    id: "fetch",
    numeral: "②",
    numeralPlain: "2",
    name: "Fetch",
    easy: "Collect clues from many places: news, grants, spinouts, and the company register.",
    summary: "~14 source adapters, isolated and polite — Track A signals + Track B Companies House.",
    ai: "no",
    network: true,
    deterministic: false,
    failure: "One broken source is recorded and skipped; the other sources still run.",
  },
  {
    id: "extract",
    numeral: "③",
    numeralPlain: "3",
    name: "Extract",
    easy: "Turn long articles into short, clear facts the computer can check.",
    summary: "Article prose → structured record (schema-enforced, evidence-quoted, cached).",
    ai: "yes",
    network: true,
    deterministic: false,
    failure: "Model down → heuristic extractor; records marked needs_review. Pipeline never stops.",
    technicalNote: "Pre-filter (URL, roundup title, length, signal keywords) runs before any AI call.",
  },
  {
    id: "resolve",
    numeral: "④",
    numeralPlain: "4",
    name: "Resolve",
    easy: "Make sure the same company is not listed twice under different names.",
    summary: "Normalise, match ladder, merge, provenance — de-duplication without fuzzy traps.",
    ai: "no",
    network: false,
    deterministic: true,
    failure: "Ambiguous matches go to the review queue; merges are reversible.",
  },
  {
    id: "enrich",
    numeral: "⑤",
    numeralPlain: "5",
    name: "Enrich",
    easy: "Add helpful extras — like where the company is — without guessing missing facts.",
    summary: "Officers, filings, postcode → region. Personal data dropped at ingest.",
    ai: "no",
    network: true,
    deterministic: true,
    failure: "Enrichment respects budget; missing enrichment does not invent attributes.",
  },
  {
    id: "gate-score",
    numeral: "⑥",
    numeralPlain: "6",
    name: "Gate + score",
    easy: "Apply hard rules, then give Match and Fresh scores. Same inputs always give the same numbers. No AI here.",
    summary: "Hard gates → Fund Fit per vehicle → Discovery Edge. Pure function of DB + config.",
    ai: "no",
    network: false,
    deterministic: true,
    failure: "Never invents scores. Reproducible from config_hash; rescoring is milliseconds.",
    technicalNote:
      "Product core. Built before crawlers. No AI, no network. When the client asks why something dropped, the answer is a number he can check.",
    highlight: true,
  },
  {
    id: "today-qa",
    numeral: "⑥½",
    numeralPlain: "6.5",
    name: "Today QA",
    easy: "One last human-like check: drop cards that clearly should not be on the morning list.",
    summary: "Hermes subagent veto on the morning list — wrong company off Today.",
    ai: "veto",
    network: true,
    deterministic: false,
    failure: "Fail-open if Hermes is down. Cannot add, score, or merge — reject reason is stored.",
  },
  {
    id: "render",
    numeral: "⑦",
    numeralPlain: "7",
    name: "Render",
    easy: "Write the results into the sheet and send the morning message when it is safe to publish.",
    summary: "SQLite → Google Sheet (minimal diff) → Telegram digest when publish allows.",
    ai: "no",
    network: true,
    deterministic: true,
    failure: "Publish gate can BLOCK send; sheet still updates. Quiet days render empty digest.",
  },
]
