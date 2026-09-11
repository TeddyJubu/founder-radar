import { motion, useReducedMotion } from "motion/react"
import { useState } from "react"
import { pipelineStages, type AiKind } from "@/content/pipeline"
import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function aiLabel(kind: AiKind) {
  if (kind === "yes") return "AI"
  if (kind === "veto") return "AI veto"
  return "No AI"
}

export function PipelineStepper() {
  const { isTechnical } = useAudience()
  const [index, setIndex] = useState(
    pipelineStages.findIndex((s) => s.highlight) || 0,
  )
  const reduce = useReducedMotion()
  const stage = pipelineStages[index]!

  return (
    <div className="space-y-5">
      <div className="flex gap-2 overflow-x-auto pb-1">
        {pipelineStages.map((s, i) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setIndex(i)}
            className={cn(
              "min-w-[4.5rem] shrink-0 rounded-lg border px-2 py-2 text-left transition",
              i === index
                ? s.highlight
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-primary/60 bg-accent"
                : "border-border bg-card/70 hover:border-primary/30",
            )}
          >
            <p className="font-mono text-xs">{s.numeral}</p>
            <p className="text-[11px] font-medium leading-tight">{s.name}</p>
          </button>
        ))}
      </div>

      <motion.div
        key={stage.id}
        initial={reduce ? false : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className={cn(
          "rounded-2xl border p-5 md:p-6",
          stage.highlight
            ? "border-primary/50 bg-primary/5"
            : stage.ai !== "no"
              ? "border-cyan-signal/40 bg-accent/50"
              : "border-border bg-card/80",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold text-slate-deep">
            {stage.numeral} {stage.name}
          </h3>
          <Badge
            variant={stage.ai === "no" ? "secondary" : "default"}
            className="font-mono text-[10px]"
          >
            {aiLabel(stage.ai)}
          </Badge>
          <Badge variant="outline" className="font-mono text-[10px]">
            {stage.network ? "Network" : "Offline"}
          </Badge>
          {stage.deterministic ? (
            <Badge variant="outline" className="font-mono text-[10px]">
              Deterministic
            </Badge>
          ) : null}
          {stage.highlight ? (
            <Badge className="bg-cyan-signal text-slate-deep font-mono text-[10px]">
              Product core
            </Badge>
          ) : null}
        </div>
        <p className="mt-3 text-sm leading-relaxed">
          {isTechnical ? stage.summary : stage.easy}
        </p>
        {isTechnical ? (
          <p className="mt-4 rounded-lg border border-border/70 bg-background/70 px-3 py-2 text-sm text-muted-foreground">
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-foreground">
              On failure ·{" "}
            </span>
            {stage.failure}
          </p>
        ) : null}
        {isTechnical && stage.technicalNote ? (
          <p className="mt-3 text-sm text-primary">{stage.technicalNote}</p>
        ) : null}
        <div className="mt-5 flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={index === 0}
            onClick={() => setIndex((v) => Math.max(0, v - 1))}
          >
            Previous
          </Button>
          <Button
            size="sm"
            disabled={index === pipelineStages.length - 1}
            onClick={() =>
              setIndex((v) => Math.min(pipelineStages.length - 1, v + 1))
            }
          >
            Next stage
          </Button>
        </div>
      </motion.div>
    </div>
  )
}
