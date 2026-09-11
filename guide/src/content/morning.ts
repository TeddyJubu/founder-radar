export interface MorningContent {
  easySteps: { title: string; body: string }[]
  technicalNotes: string[]
  verdicts: { key: "1" | "2" | "3"; label: string; effect: string }[]
}

export const morningContent: MorningContent = {
  easySteps: [
    {
      title: "Open Today",
      body: "In the morning, open the Today page (or wait for the Telegram ping). You might see about five to ten companies — or none. Zero is okay. It means a quiet day, not a broken tool.",
    },
    {
      title: "Press 1, 2, or 3",
      body: "1 means yes, worth contacting — it goes to Kept. 2 means maybe — also Kept. 3 means no — we remember it so it will not come back on Today.",
    },
    {
      title: "One notebook everywhere",
      body: "Website, sheet, and Telegram all write to the same notebook. If you only say “no” in chat and never save it for real, nothing changes.",
    },
  ],
  technicalNotes: [
    "Engine shortlist (score.tier = shortlist) is the system opinion and moves when thresholds change. Kept is the human pick list of worth contacting / unsure.",
    "After scoring, Hermes Today QA can drop already-backed, IPO / late-stage, or wrong-region cards before they appear.",
    "Sheet column Z (Verdict) updates when a web decision is saved; SQLite remains primary. Blanks only clear when the pipeline had previously rendered a value.",
  ],
  verdicts: [
    {
      key: "1",
      label: "Worth contacting",
      effect: "Goes to Kept right away",
    },
    {
      key: "2",
      label: "Unsure",
      effect: "Goes to Kept for later",
    },
    {
      key: "3",
      label: "Not for me",
      effect: "Will not show on Today again",
    },
  ],
}
