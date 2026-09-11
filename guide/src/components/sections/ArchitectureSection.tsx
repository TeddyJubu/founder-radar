import { ArchitectureExplorer } from "@/components/diagrams/ArchitectureExplorer"
import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import { aiBoundary } from "@/content/architecture"
import { useAudience } from "@/hooks/use-audience"

const EASY_STORY = [
  { label: "Alarm", detail: "Wakes up early" },
  { label: "Engine", detail: "Does the scan" },
  { label: "Notebook", detail: "Saves the truth" },
  { label: "Three windows", detail: "Sheet · Website · Phone" },
]

export function ArchitectureSection() {
  const { isTechnical } = useAudience()

  return (
    <SectionShell
      id="architecture"
      number="03"
      title="The parts"
      eyebrow="One notebook, many windows"
    >
      <Reveal>
        <p className="mb-6 max-w-3xl text-base leading-relaxed text-muted-foreground md:text-lg">
          Everything important lives in one notebook. The other screens are just different
          ways to look at it — or talk to it.
        </p>
      </Reveal>

      {!isTechnical ? (
        <Reveal>
          <ol className="mb-8 grid gap-2 sm:grid-cols-4" aria-label="Simple system story">
            {EASY_STORY.map((step, i) => (
              <li
                key={step.label}
                className="relative rounded-xl border border-border bg-card/80 p-4"
              >
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-primary">
                  {i + 1}
                </p>
                <p className="mt-1 font-semibold text-slate-deep">{step.label}</p>
                <p className="mt-1 text-sm text-muted-foreground">{step.detail}</p>
              </li>
            ))}
          </ol>
        </Reveal>
      ) : null}

      <Reveal>
        <ArchitectureExplorer />
      </Reveal>

      <Reveal className="mt-8 grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-border bg-card/70 p-4">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-primary">
            A helper may
          </p>
          <ul className="mt-3 space-y-2 text-sm">
            {aiBoundary.may.map((item) => (
              <li key={item}>· {item}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl border border-border bg-card/70 p-4">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-destructive">
            A helper may not
          </p>
          <ul className="mt-3 space-y-2 text-sm">
            {aiBoundary.mayNot.map((item) => (
              <li key={item}>· {item}</li>
            ))}
          </ul>
        </div>
      </Reveal>
      {isTechnical ? (
        <TechnicalPanel>
          <p>
            Three layers: Engine (deterministic Python), Reader (two boxed AI calls), Front
            desk (Hermes). Scoring never uses the network or invents numbers.
          </p>
          <p>
            If Hermes is down, the morning pipeline still runs. Chat-only replies without{" "}
            <code className="font-mono text-xs">decide</code> do not update Today, Kept, or
            the sheet.
          </p>
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
