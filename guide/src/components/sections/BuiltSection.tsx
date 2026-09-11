import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import {
  buildOrderOneLiner,
  buildOrderOneLinerEasy,
  buildPhases,
} from "@/content/build-story"
import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export function BuiltSection() {
  const { isTechnical } = useAudience()

  return (
    <SectionShell id="built" number="07" title="How it was built" eyebrow="Build story">
      <Reveal>
        <p className="mb-10 max-w-3xl text-base leading-relaxed text-muted-foreground md:text-lg">
          {isTechnical ? buildOrderOneLiner : buildOrderOneLinerEasy}
        </p>
      </Reveal>
      <div className="relative space-y-4 before:absolute before:top-2 before:bottom-2 before:left-[1.15rem] before:w-px before:bg-border md:before:left-[1.35rem]">
        {buildPhases.map((phase, i) => (
          <Reveal key={phase.id} delay={Math.min(i * 0.03, 0.2)}>
            <div className="relative flex gap-4 pl-1">
              <div
                className={cn(
                  "relative z-10 mt-1 flex size-8 shrink-0 items-center justify-center rounded-full border text-xs font-mono md:size-9",
                  phase.highlight
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-card",
                )}
              >
                {phase.phase}
              </div>
              <div className="flex-1 rounded-xl border border-border bg-card/70 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-lg font-semibold text-slate-deep">{phase.title}</h3>
                  {phase.highlight ? (
                    <Badge className="bg-cyan-signal text-slate-deep font-mono text-[10px]">
                      key
                    </Badge>
                  ) : null}
                </div>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {isTechnical ? phase.technical : phase.easy}
                </p>
              </div>
            </div>
          </Reveal>
        ))}
      </div>
      {isTechnical ? (
        <TechnicalPanel>
          <p>
            Offline pytest is the contract. Scoring and gates must pass with zero network.
            Layout-change detectors and last-good config keep daily runs from failing closed.
          </p>
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
