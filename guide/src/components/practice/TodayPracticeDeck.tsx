import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useCallback, useEffect, useState } from "react"
import { practiceCompanies, practiceTeaching } from "@/content/practice"
import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type Verdict = "keep" | "unsure" | "reject"

interface HistoryItem {
  id: string
  name: string
  verdict: Verdict
}

const LABELS = {
  easy: {
    keep: "1 · Yes",
    unsure: "2 · Maybe",
    reject: "3 · No",
    keepTag: "yes",
    unsureTag: "maybe",
    keptEmpty: "Empty — press Yes or Maybe.",
    rejectEmpty: "No answers stay out of Kept and will not come back tomorrow.",
  },
  technical: {
    keep: "1 · Worth contacting",
    unsure: "2 · Unsure",
    reject: "3 · Not for me",
    keepTag: "worth contacting",
    unsureTag: "unsure",
    keptEmpty: "Empty — press 1 or 2.",
    rejectEmpty: "Rejects stay out of Kept and off tomorrow’s Today.",
  },
} as const

export function TodayPracticeDeck() {
  const { isTechnical } = useAudience()
  const copy = isTechnical ? LABELS.technical : LABELS.easy
  const [index, setIndex] = useState(0)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [flash, setFlash] = useState<Verdict | null>(null)
  const reduce = useReducedMotion()
  const company = practiceCompanies[index]
  const done = index >= practiceCompanies.length

  const decide = useCallback(
    (verdict: Verdict) => {
      if (!company) return
      setFlash(verdict)
      setHistory((prev) => [...prev, { id: company.id, name: company.name, verdict }])
      window.setTimeout(() => {
        setFlash(null)
        setIndex((v) => v + 1)
      }, reduce ? 0 : 420)
    },
    [company, reduce],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement) {
        const tag = event.target.tagName
        if (tag === "INPUT" || tag === "TEXTAREA" || event.target.isContentEditable) return
      }
      if (event.key === "1") decide("keep")
      if (event.key === "2") decide("unsure")
      if (event.key === "3") decide("reject")
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [decide])

  const reset = () => {
    setIndex(0)
    setHistory([])
    setFlash(null)
  }

  const kept = history.filter((h) => h.verdict !== "reject")
  const rejected = history.filter((h) => h.verdict === "reject")

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <div className="relative min-h-[22rem] overflow-hidden rounded-2xl border border-border bg-card/80 p-5 md:p-6">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Try it · made-up companies
        </p>
        <p className="mt-2 text-sm text-muted-foreground">{practiceTeaching.keyboard}</p>

        <div className="relative mt-6 min-h-[14rem]" aria-live="polite">
          <AnimatePresence mode="wait">
            {!done && company ? (
              <motion.div
                key={company.id}
                initial={reduce ? false : { opacity: 0, x: 40 }}
                animate={{
                  opacity: flash ? 0.35 : 1,
                  x: flash === "reject" ? 80 : flash ? -80 : 0,
                  rotate: flash === "reject" ? 4 : flash ? -3 : 0,
                }}
                exit={reduce ? undefined : { opacity: 0, y: -20 }}
                transition={{ duration: 0.35 }}
                className="rounded-xl border border-border bg-background p-5 shadow-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-2xl font-semibold text-slate-deep">
                    {company.name}
                  </h3>
                  <Badge variant="secondary">{company.region}</Badge>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {company.ageMonths} months old
                  </Badge>
                </div>
                <p className="mt-3 text-sm leading-relaxed">{company.oneLiner}</p>
                <p className="mt-4 text-sm text-primary">
                  Likely fund: <strong>{company.bestFund}</strong>
                  {isTechnical ? ` — ${company.fitNote}` : null}
                </p>
              </motion.div>
            ) : (
              <motion.div
                key="done"
                initial={reduce ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
                className="rounded-xl border border-dashed border-primary/40 bg-accent/40 p-6"
              >
                <h3 className="text-xl font-semibold text-slate-deep">Nice — you finished</h3>
                <p className="mt-2 text-sm text-muted-foreground">
                  {practiceTeaching.quietMorning}
                </p>
                <Button className="mt-4" onClick={reset}>
                  Practice again
                </Button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {!done ? (
          <div className="mt-5 flex flex-wrap gap-2" role="group" aria-label="Decide on this company">
            <Button onClick={() => decide("keep")}>{copy.keep}</Button>
            <Button variant="secondary" onClick={() => decide("unsure")}>
              {copy.unsure}
            </Button>
            <Button variant="outline" onClick={() => decide("reject")}>
              {copy.reject}
            </Button>
          </div>
        ) : null}
      </div>

      <div className="space-y-4">
        <div className="rounded-2xl border border-border bg-card/80 p-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">
            Kept
          </p>
          {kept.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">{copy.keptEmpty}</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {kept.map((item) => (
                <li
                  key={item.id}
                  className="flex items-center justify-between rounded-lg border border-border/70 px-3 py-2 text-sm"
                >
                  <span>{item.name}</span>
                  <Badge
                    className={cn(
                      "font-mono text-[10px]",
                      item.verdict === "keep" ? "bg-primary" : "",
                    )}
                    variant={item.verdict === "keep" ? "default" : "secondary"}
                  >
                    {item.verdict === "keep" ? copy.keepTag : copy.unsureTag}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-2xl border border-border bg-card/80 p-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Won&apos;t show again
          </p>
          {rejected.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">{copy.rejectEmpty}</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {rejected.map((item) => (
                <li
                  key={item.id}
                  className="rounded-lg border border-dashed border-border px-3 py-2 text-sm text-muted-foreground line-through"
                >
                  {item.name}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
