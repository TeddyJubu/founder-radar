export type Audience = "easy" | "technical"

export type SectionId =
  | "why"
  | "architecture"
  | "morning"
  | "scores"
  | "pipeline"
  | "use"
  | "built"

export interface SectionMeta {
  id: SectionId
  number: string
  nav: string
  title: string
}

export const SECTIONS: SectionMeta[] = [
  { id: "why", number: "01", nav: "Why", title: "Why it exists" },
  { id: "morning", number: "02", nav: "Practice", title: "Morning loop" },
  { id: "architecture", number: "03", nav: "Parts", title: "The parts" },
  { id: "scores", number: "04", nav: "Funds", title: "Scores & funds" },
  { id: "pipeline", number: "05", nav: "Steps", title: "Morning steps" },
  { id: "use", number: "06", nav: "Daily", title: "Day to day" },
  { id: "built", number: "07", nav: "Built", title: "How it was built" },
]
