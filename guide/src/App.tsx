import { motion, useReducedMotion } from "motion/react"
import { ArchitectureSection } from "@/components/sections/ArchitectureSection"
import { BuiltSection } from "@/components/sections/BuiltSection"
import { MorningSection } from "@/components/sections/MorningSection"
import { PipelineSection } from "@/components/sections/PipelineSection"
import { ScoresSection } from "@/components/sections/ScoresSection"
import { UseSection } from "@/components/sections/UseSection"
import { WhySection } from "@/components/sections/WhySection"
import { SiteHeader } from "@/components/SiteHeader"
import { AudienceProvider } from "@/hooks/use-audience"
import { TooltipProvider } from "@/components/ui/tooltip"

const BIG_PICTURE = [
  {
    n: "1",
    title: "Find",
    body: "Look for young UK companies — using real birthdays, not old fund websites.",
  },
  {
    n: "2",
    title: "Score",
    body: "Ask: does a fund fit? Is the company still fresh enough to be new to them?",
  },
  {
    n: "3",
    title: "Decide",
    body: "Each morning, press yes / maybe / no. Your choices are saved in one notebook.",
  },
]

function Hero() {
  const reduce = useReducedMotion()
  return (
    <section id="top" className="relative overflow-hidden border-b border-border/70">
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute -top-24 left-1/4 size-[28rem] rounded-full bg-cyan-signal/20 blur-3xl" />
        <div className="absolute top-10 right-0 size-[22rem] rounded-full bg-primary/15 blur-3xl" />
        <div
          className="absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage:
              "linear-gradient(to right, oklch(0.55 0.08 220 / 0.08) 1px, transparent 1px), linear-gradient(to bottom, oklch(0.55 0.08 220 / 0.08) 1px, transparent 1px)",
            backgroundSize: "48px 48px",
          }}
        />
      </div>
      <div className="relative mx-auto flex max-w-5xl flex-col gap-8 px-4 py-16 sm:px-6 md:py-24">
        <div className="flex flex-col gap-5">
          <motion.p
            className="font-mono text-xs uppercase tracking-[0.22em] text-primary"
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
          >
            Teaching site · not the live app
          </motion.p>
          <motion.h1
            className="max-w-3xl text-4xl font-semibold tracking-tight text-slate-deep sm:text-5xl md:text-6xl"
            initial={reduce ? false : { opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.05, duration: 0.45 }}
          >
            UK Founder Radar
          </motion.h1>
          <motion.p
            className="max-w-2xl text-lg leading-relaxed text-muted-foreground md:text-xl"
            initial={reduce ? false : { opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1, duration: 0.45 }}
          >
            A daily helper that finds young UK startups and suggests which of four funds
            might care — then you decide with three buttons.
          </motion.p>
        </div>

        <motion.ol
          className="grid gap-3 sm:grid-cols-3"
          initial={reduce ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.16, duration: 0.4 }}
          aria-label="How the system works in three steps"
        >
          {BIG_PICTURE.map((step) => (
            <li
              key={step.n}
              className="rounded-xl border border-border/80 bg-card/90 p-4 shadow-sm"
            >
              <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-primary">
                Step {step.n}
              </p>
              <p className="mt-1 text-lg font-semibold text-slate-deep">{step.title}</p>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
            </li>
          ))}
        </motion.ol>

        <motion.div
          className="flex flex-wrap gap-3"
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.22 }}
        >
          <a
            href="#why"
            className="inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Start from the beginning
          </a>
          <a
            href="#morning"
            className="inline-flex h-10 items-center rounded-lg border border-border bg-card/80 px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Skip to morning practice
          </a>
        </motion.div>
      </div>
    </section>
  )
}

export default function App() {
  return (
    <AudienceProvider>
      <TooltipProvider>
        <div className="min-h-svh">
          <a
            href="#main"
            className="absolute left-4 top-4 z-50 -translate-y-[200%] rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow focus:translate-y-0 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Skip to content
          </a>
          <SiteHeader />
          <main id="main">
            <Hero />
            {/* Learn the job (why + practice) before the plumbing. */}
            <WhySection />
            <MorningSection />
            <ArchitectureSection />
            <ScoresSection />
            <PipelineSection />
            <UseSection />
            <BuiltSection />
          </main>
          <footer className="border-t border-border/70 py-10">
            <div className="mx-auto max-w-5xl px-4 text-sm text-muted-foreground sm:px-6">
              <p className="font-medium text-slate-deep">UK Founder Radar · teaching guide</p>
              <p className="mt-2 max-w-2xl">
                Use Easy for plain language. Switch Technical when you want the full ops depth.
              </p>
            </div>
          </footer>
        </div>
      </TooltipProvider>
    </AudienceProvider>
  )
}
