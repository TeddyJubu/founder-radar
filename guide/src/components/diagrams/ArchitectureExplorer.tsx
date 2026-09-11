import { motion, useReducedMotion } from "motion/react"
import { useMemo, useState } from "react"
import {
  architectureLayers,
  architectureNodes,
  type ArchNodeId,
} from "@/content/architecture"
import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const layout: { id: ArchNodeId; col: string; row: string }[] = [
  { id: "vps", col: "1", row: "1" },
  { id: "cli", col: "2", row: "1" },
  { id: "sqlite", col: "3", row: "1" },
  { id: "sheet", col: "1", row: "2" },
  { id: "web", col: "2", row: "2" },
  { id: "telegram", col: "3", row: "2" },
  { id: "hermes", col: "2", row: "3" },
]

export function ArchitectureExplorer() {
  const { isTechnical } = useAudience()
  const [selected, setSelected] = useState<ArchNodeId>("sqlite")
  const reduce = useReducedMotion()
  const node = useMemo(
    () => architectureNodes.find((n) => n.id === selected)!,
    [selected],
  )

  return (
    <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
      <div className="relative overflow-hidden rounded-2xl border border-border bg-card/70 p-4 md:p-6">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,oklch(0.85_0.06_210/0.35),transparent_45%),radial-gradient(circle_at_80%_0%,oklch(0.88_0.05_230/0.3),transparent_40%)]" />
        <div className="relative grid grid-cols-3 gap-3 md:gap-4">
          {layout.map((slot) => {
            const item = architectureNodes.find((n) => n.id === slot.id)!
            const active = selected === item.id
            return (
              <motion.button
                key={item.id}
                type="button"
                onClick={() => setSelected(item.id)}
                className={cn(
                  "rounded-xl border px-3 py-3 text-left transition md:px-4 md:py-4",
                  active
                    ? "border-primary bg-primary text-primary-foreground shadow-lg shadow-primary/20"
                    : "border-border/80 bg-background/80 hover:border-primary/40",
                )}
                animate={
                  reduce
                    ? undefined
                    : active
                      ? { scale: 1.02 }
                      : { scale: 1 }
                }
                whileTap={reduce ? undefined : { scale: 0.98 }}
              >
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] opacity-70">
                  {item.label}
                </p>
                <p className="mt-1 text-sm font-semibold md:text-base">{item.role}</p>
                {active && !reduce ? (
                  <motion.span
                    className="mt-2 block h-1 w-8 rounded-full bg-cyan-signal"
                    layoutId="arch-pulse"
                    transition={{ type: "spring", stiffness: 320, damping: 28 }}
                  />
                ) : (
                  <span className="mt-2 block h-1 w-8 rounded-full bg-transparent" />
                )}
              </motion.button>
            )
          })}
        </div>
        {!reduce ? (
          <motion.div
            key={selected}
            className="relative mt-4 h-1 overflow-hidden rounded-full bg-muted"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <motion.div
              className="h-full w-1/3 rounded-full bg-cyan-signal"
              initial={{ x: "-10%" }}
              animate={{ x: "220%" }}
              transition={{ duration: 1.1, ease: "easeInOut" }}
            />
          </motion.div>
        ) : null}
        <p className="relative mt-3 font-mono text-[11px] text-muted-foreground">
          {isTechnical
            ? "Select a node · pulse shows data moving through the CLI"
            : "Tap a box to learn what it does"}
        </p>
      </div>

      <div className="rounded-2xl border border-border bg-card/80 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold text-slate-deep">{node.role}</h3>
          <Badge variant="secondary" className="font-mono text-[10px]">
            {node.label}
          </Badge>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-foreground/90">
          {isTechnical ? node.technical : node.easy}
        </p>
        <div className="mt-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
            Connects to
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {node.flowsTo.map((id) => {
              const target = architectureNodes.find((n) => n.id === id)!
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSelected(id)}
                  className="rounded-full border border-border px-2.5 py-1 text-xs hover:border-primary/50"
                >
                  {target.label}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {isTechnical ? (
        <div className="lg:col-span-2 grid gap-3 md:grid-cols-3">
          {architectureLayers.map((layer) => (
            <div
              key={layer.name}
              className="rounded-xl border border-border bg-background/70 p-4"
            >
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">
                {layer.name}
              </p>
              <p className="mt-1 text-sm font-medium">{layer.subtitle}</p>
              <ul className="mt-3 space-y-1.5 text-xs text-muted-foreground">
                {layer.bullets.map((b) => (
                  <li key={b}>· {b}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
