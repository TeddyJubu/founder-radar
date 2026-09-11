import { TodayPracticeDeck } from "@/components/practice/TodayPracticeDeck"
import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import { morningContent } from "@/content/morning"
import { useAudience } from "@/hooks/use-audience"

export function MorningSection() {
  const { isTechnical } = useAudience()

  return (
    <SectionShell id="morning" number="02" title="Your morning job" eyebrow="Practice first">
      <div className="mb-10 grid gap-6 md:grid-cols-3">
        {morningContent.easySteps.map((step) => (
          <Reveal key={step.title}>
            <div className="h-full rounded-xl border border-border bg-card/70 p-4">
              <h3 className="text-lg font-semibold text-slate-deep">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
            </div>
          </Reveal>
        ))}
      </div>
      <Reveal>
        <TodayPracticeDeck />
      </Reveal>
      {isTechnical ? (
        <TechnicalPanel>
          {morningContent.technicalNotes.map((note) => (
            <p key={note.slice(0, 32)}>{note}</p>
          ))}
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
