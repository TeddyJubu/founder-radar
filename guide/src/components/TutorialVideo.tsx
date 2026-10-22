import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"

const VIDEO_SRC = `${import.meta.env.BASE_URL}tutorial/how-to-use.mp4`
const CAPTIONS_SRC = `${import.meta.env.BASE_URL}tutorial/how-to-use.vtt`
const POSTER_SRC = `${import.meta.env.BASE_URL}tutorial/how-to-use-poster.jpg`

export function TutorialVideo() {
  const { isTechnical } = useAudience()

  return (
    <figure id="use-video" className="scroll-mt-24">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold text-slate-deep">
          {isTechnical ? "A real morning pass" : "Watch someone use it"}
        </h3>
        <Badge variant="secondary" className="font-mono text-[10px]">
          {isTechnical ? "demo db · 2m 49s" : "2 min 49 sec · words on screen"}
        </Badge>
      </div>
      <p className="mb-4 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        {isTechnical
          ? "Recorded against the fixture-backed Today prototype: onboarding, one-card review, keep / unsure / reject, Kept, Dashboard, and Help. Captions are burned in and also shipped as WebVTT."
          : "This is the real Today page, not a drawing. There is no sound — read the words at the top. You will see someone press 1, 2, and 3, then open Kept."}
      </p>
      <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <video
          className="aspect-[8/5] w-full bg-slate-deep"
          controls
          playsInline
          preload="metadata"
          poster={POSTER_SRC}
          aria-label="How to use UK Founder Radar on Today, Kept, and Dashboard"
        >
          <source src={VIDEO_SRC} type="video/mp4" />
          <track
            kind="captions"
            src={CAPTIONS_SRC}
            srcLang="en-GB"
            label="English captions"
          />
          Your browser cannot play this video. Open the Today app and press 1, 2,
          or 3 instead.
        </video>
      </div>
      <figcaption className="mt-3 text-xs leading-relaxed text-muted-foreground">
        {isTechnical
          ? "Re-record with DISPLAY=:1 python scripts/record_usage_tutorial.py after seeding the demo database."
          : "Demo companies from the practice database — not this morning’s live list."}
      </figcaption>
    </figure>
  )
}
