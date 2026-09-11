import { motion, useReducedMotion } from "motion/react"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

export function Reveal({
  children,
  className,
  delay = 0,
}: {
  children: ReactNode
  className?: string
  delay?: number
}) {
  const reduce = useReducedMotion()
  if (reduce) {
    return <div className={className}>{children}</div>
  }
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-10% 0px" }}
      transition={{ duration: 0.45, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  )
}

export function SectionShell({
  id,
  number,
  title,
  eyebrow,
  children,
  className,
}: {
  id: string
  number: string
  title: string
  eyebrow?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section
      id={id}
      className={cn("scroll-mt-24 border-b border-border/70 py-16 md:py-24", className)}
    >
      <div className="mx-auto max-w-5xl px-4 sm:px-6">
        <Reveal>
          <div className="mb-8 flex flex-wrap items-end gap-4">
            <span className="font-mono text-sm tracking-[0.2em] text-primary/80">
              {number}
            </span>
            <div>
              {eyebrow ? (
                <p className="mb-1 font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">
                  {eyebrow}
                </p>
              ) : null}
              <h2 className="text-3xl font-semibold tracking-tight text-slate-deep md:text-4xl">
                {title}
              </h2>
            </div>
          </div>
        </Reveal>
        {children}
      </div>
    </section>
  )
}

export function TechnicalPanel({ children }: { children: ReactNode }) {
  return (
    <div className="mt-6 rounded-xl border border-cyan-signal/30 bg-accent/40 p-4 md:p-5">
      <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-primary">
        Technical depth
      </p>
      <div className="space-y-3 text-sm leading-relaxed text-foreground/90">
        {children}
      </div>
    </div>
  )
}
