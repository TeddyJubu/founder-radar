import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import { whyContent, whyFor } from "@/content/why"
import { useAudience } from "@/hooks/use-audience"

export function WhySection() {
  const { audience, isTechnical } = useAudience()
  const paragraphs = whyFor(audience)

  return (
    <SectionShell id="why" number="01" title="Why it exists" eyebrow="The problem">
      <Reveal>
        <p className="mb-6 max-w-3xl text-xl font-medium leading-snug text-slate-deep md:text-2xl">
          {whyContent.easyLead}
        </p>
      </Reveal>
      <div className="max-w-3xl space-y-4 text-base leading-relaxed text-foreground/90 md:text-lg">
        {paragraphs.map((p) => (
          <Reveal key={p.slice(0, 24)}>
            <p>{p}</p>
          </Reveal>
        ))}
        <Reveal>
          <p className="font-medium text-primary">{whyContent.successLine}</p>
        </Reveal>
      </div>
      {isTechnical ? (
        <TechnicalPanel>
          <p>
            Acceptance measure: median age of shortlisted companies under 24 months.
            Freshness gates make old companies structurally impossible to shortlist.
          </p>
          <p>
            Companies House verifies age. Signal sources (spinouts, accelerators, grants,
            curated news) carry quality discovery.
          </p>
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
