import { PipelineStepper } from "@/components/diagrams/PipelineStepper"
import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import { useAudience } from "@/hooks/use-audience"

export function PipelineSection() {
  const { isTechnical } = useAudience()

  return (
    <SectionShell id="pipeline" number="05" title="Morning steps" eyebrow="What the computer does">
      <Reveal>
        <p className="mb-8 max-w-3xl text-base leading-relaxed text-muted-foreground md:text-lg">
          Every morning the tool walks through seven steps (plus one last check). Click each
          step. Step 6 is the heart: hard rules and scores, with no AI and no internet.
        </p>
      </Reveal>
      <Reveal>
        <PipelineStepper />
      </Reveal>
      {isTechnical ? (
        <TechnicalPanel>
          <p>
            Stage 6 is a pure function of the database plus config. Same inputs always yield
            the same scores. Built before crawlers so the product value is testable arithmetic.
          </p>
          <p>
            Scores are stamped with <code className="font-mono text-xs">config_hash</code>.
            After Fund Criteria or Weights change, run{" "}
            <code className="font-mono text-xs">rescore</code>.
          </p>
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
