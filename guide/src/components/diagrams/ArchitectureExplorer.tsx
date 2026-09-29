import { motion, useReducedMotion } from "motion/react"
import { useMemo, useState } from "react"
import {
  architectureLayers,
  architectureNodes,
  engineSteps,
  sheetJobs,
  trackCompare,
  type ArchNodeId,
} from "@/content/architecture"
import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const flow: ArchNodeId[][] = [
  ["trackA", "trackB"],
  ["engine"],
  ["notebook"],
  ["sheet", "today", "telegram"],
  ["hermes", "alarm"],
]

const cues = [
  "both become company records",
  "saves the truth in",
  "shown through three windows",
  "front desk and the alarm",
]

function TrackDetail({ selected }: { selected: "trackA" | "trackB" }) {
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-border">
      <p className="border-b border-border px-3 py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
        {trackCompare.title}
      </p>
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            <th className="px-3 py-2 font-medium"> </th>
            <th
              className={cn(
                "px-3 py-2 font-medium",
                selected === "trackA" && "text-primary",
              )}
            >
              Track A
            </th>
            <th
              className={cn(
                "px-3 py-2 font-medium",
                selected === "trackB" && "text-primary",
              )}
            >
              Track B
            </th>
          </tr>
        </thead>
        <tbody>
          {trackCompare.rows.map((row) => (
            <tr key={row.label} className="border-b border-border/70 last:border-0">
              <td className="px-3 py-2 text-muted-foreground">{row.label}</td>
              <td className={cn("px-3 py-2", selected === "trackA" && "font-medium")}>
                {row.a}
              </td>
              <td className={cn("px-3 py-2", selected === "trackB" && "font-medium")}>
                {row.b}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
        {trackCompare.footer}
      </p>
    </div>
  )
}

function SheetDetail() {
  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-border p-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-primary">
          You type
        </p>
        <ul className="mt-2 space-y-1.5 text-xs">
          {sheetJobs.youType.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
      <div className="rounded-xl border border-border p-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-primary">
          The engine writes
        </p>
        <ul className="mt-2 space-y-1.5 text-xs">
          {sheetJobs.engineWrites.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-muted-foreground sm:col-span-2">{sheetJobs.footer}</p>
    </div>
  )
}

function EngineDetail() {
  return (
    <ol className="mt-4 space-y-2">
      {engineSteps.rows.map((row) => (
        <li
          key={row.step}
          className="grid grid-cols-[1fr_auto] gap-2 rounded-lg border border-border px-3 py-2 text-xs"
        >
          <div>
            <p className="font-medium">{row.step}</p>
            <p className="mt-0.5 text-muted-foreground">{row.what}</p>
          </div>
          <span className="self-start font-mono text-[10px] text-muted-foreground">
            {row.ai}
          </span>
        </li>
      ))}
    </ol>
  )
}

export function ArchitectureExplorer() {
  const { isTechnical } = useAudience()
  const [selected, setSelected] = useState<ArchNodeId>("trackA")
  const reduce = useReducedMotion()
  const node = useMemo(
    () => architectureNodes.find((n) => n.id === selected)!,
    [selected],
  )

  return (
    <div className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
      <div className="relative overflow-hidden rounded-2xl border border-border bg-card/70 p-4 md:p-6">
        <p className="mb-4 text-sm text-muted-foreground">
          {isTechnical
            ? "Select a node. The card opens that part, plus a second diagram for tracks, the sheet, or the pipeline."
            : "Tap a box. A card opens and tells you what that part is for."}
        </p>
        <div className="relative space-y-2">
          {flow.map((row, rowIndex) => (
            <div key={row.join("-")}>
              <div
                className={cn(
                  "grid gap-2",
                  row.length === 1 && "grid-cols-1",
                  row.length === 2 && "grid-cols-2",
                  row.length === 3 && "grid-cols-3",
                )}
              >
                {row.map((id) => {
                  const item = architectureNodes.find((n) => n.id === id)!
                  const active = selected === item.id
                  return (
                    <motion.button
                      key={item.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setSelected(item.id)}
                      className={cn(
                        "rounded-xl border px-3 py-3 text-left transition",
                        active
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border/80 bg-background/80 hover:border-primary/40",
                      )}
                      animate={
                        reduce ? undefined : active ? { scale: 1.02 } : { scale: 1 }
                      }
                      whileTap={reduce ? undefined : { scale: 0.98 }}
                    >
                      <p className="font-mono text-[10px] uppercase tracking-[0.16em] opacity-70">
                        {item.label}
                      </p>
                      <p className="mt-1 text-sm font-semibold md:text-base">
                        {item.role}
                      </p>
                    </motion.button>
                  )
                })}
              </div>
              {cues[rowIndex] ? (
                <p className="py-1.5 text-center font-mono text-[10px] text-muted-foreground">
                  {cues[rowIndex]}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card/80 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold text-slate-deep">{node.role}</h3>
          <Badge variant="secondary" className="font-mono text-[10px]">
            {node.label}
          </Badge>
        </div>
        <p className="mt-3 text-sm font-medium">{node.question}</p>
        <p className="mt-2 text-sm leading-relaxed text-foreground/90">
          {isTechnical ? node.technical : node.easy}
        </p>
        {node.detail === "tracks" ? (
          <TrackDetail selected={selected === "trackB" ? "trackB" : "trackA"} />
        ) : null}
        {node.detail === "sheet" ? <SheetDetail /> : null}
        {node.detail === "engine" ? <EngineDetail /> : null}
        <div className="mt-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
            Open a connected box
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {node.related.map((id) => {
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
        <div className="grid gap-3 md:grid-cols-3 lg:col-span-2">
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
