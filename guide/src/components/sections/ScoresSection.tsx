import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import { breakdownFundOrder, fundCards, scoringExplainer } from "@/content/funds"
import { useAudience } from "@/hooks/use-audience"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"

const EASY_FUND_BLURBS: Record<string, string> = {
  dsw: "Cares about UK tech companies that can get special early-stage tax deals.",
  northstar: "Cares about companies linked to the North East of England.",
  outward: "Cares when money or payments are a big part of the product.",
  anticus: "Cares about companies in Yorkshire (or moving there).",
}

export function ScoresSection() {
  const { isTechnical } = useAudience()
  const ordered = breakdownFundOrder.map((key) => fundCards.find((f) => f.key === key)!)

  return (
    <SectionShell id="scores" number="04" title="Scores & funds" eyebrow="Two scores, four funds">
      <Reveal>
        <p className="mb-6 max-w-3xl text-base leading-relaxed text-muted-foreground md:text-lg">
          Every company gets two simple scores. Then we check which fund&apos;s rules it fits.
        </p>
      </Reveal>
      <div className="mb-8 grid gap-4 md:grid-cols-2">
        {scoringExplainer.easy.matchVsFresh.map((item) => (
          <Reveal key={item.title}>
            <div className="h-full rounded-xl border border-border bg-card/70 p-5">
              <h3 className="text-lg font-semibold text-slate-deep">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{item.body}</p>
            </div>
          </Reveal>
        ))}
      </div>
      <Reveal>
        <p className="mb-8 max-w-3xl text-sm leading-relaxed text-muted-foreground md:text-base">
          {scoringExplainer.easy.coverage}
        </p>
      </Reveal>

      {!isTechnical ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {ordered.map((fund) => (
            <Reveal key={fund.key}>
              <div className="h-full rounded-xl border border-border bg-card/80 p-5">
                <h3 className="text-lg font-semibold text-slate-deep">{fund.name}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {EASY_FUND_BLURBS[fund.key]}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          {ordered.map((fund) => (
            <Reveal key={fund.key}>
              <div className="rounded-2xl border border-border bg-card/80 p-5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-xl font-semibold text-slate-deep">{fund.name}</h3>
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {fund.key}
                  </Badge>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{fund.mandate}</p>
                <Accordion type="multiple" className="mt-4">
                  {fund.vehicles.map((v) => (
                    <AccordionItem key={v.key} value={v.key}>
                      <AccordionTrigger className="text-left text-sm">
                        <span className="flex flex-wrap items-center gap-2">
                          {v.name}
                          {!v.active ? (
                            <Badge variant="outline" className="font-mono text-[10px]">
                              inactive
                            </Badge>
                          ) : null}
                        </span>
                      </AccordionTrigger>
                      <AccordionContent className="space-y-2 text-sm text-muted-foreground">
                        <p>{v.oneLiner}</p>
                        <p>
                          <span className="font-mono text-[10px] uppercase tracking-wide text-foreground">
                            Stage ·{" "}
                          </span>
                          {v.stage}
                        </p>
                        <p>
                          <span className="font-mono text-[10px] uppercase tracking-wide text-foreground">
                            Geo ·{" "}
                          </span>
                          {v.geo}
                        </p>
                        <p>
                          <span className="font-mono text-[10px] uppercase tracking-wide text-foreground">
                            Cheque ·{" "}
                          </span>
                          {v.cheque}
                        </p>
                        {v.hardRules.length ? (
                          <ul className="list-disc space-y-1 pl-5">
                            {v.hardRules.map((r) => (
                              <li key={r}>{r}</li>
                            ))}
                          </ul>
                        ) : null}
                      </AccordionContent>
                    </AccordionItem>
                  ))}
                </Accordion>
              </div>
            </Reveal>
          ))}
        </div>
      )}

      {isTechnical ? (
        <TechnicalPanel>
          <p>
            Universal freshness gates: {scoringExplainer.technical.gates.join("; ")}.
          </p>
          <p>{scoringExplainer.technical.nullPolicy}</p>
          <p>{scoringExplainer.technical.derivation}</p>
          <p>{scoringExplainer.technical.configHash}</p>
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
