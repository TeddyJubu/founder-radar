import { Download, Menu } from "lucide-react"
import { useState } from "react"
import { SECTIONS } from "@/content"
import { useAudience } from "@/hooks/use-audience"
import { DownloadPdfButton } from "@/components/pdf/DownloadPdfButton"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { cn } from "@/lib/utils"
import type { Audience } from "@/content/types"

const AUDIENCE_OPTIONS: { id: Audience; label: string; hint: string }[] = [
  { id: "easy", label: "Easy", hint: "Plain words" },
  { id: "technical", label: "Technical", hint: "Full detail" },
]

export function SiteHeader() {
  const { audience, setAudience } = useAudience()
  const [open, setOpen] = useState(false)

  const nav = (
    <nav
      className="flex flex-col gap-1 md:flex-row md:items-center md:gap-0.5"
      aria-label="Guide sections"
    >
      {SECTIONS.map((section) => (
        <a
          key={section.id}
          href={`#${section.id}`}
          onClick={() => setOpen(false)}
          className="rounded-md px-2 py-1.5 text-sm text-muted-foreground transition hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {section.nav}
        </a>
      ))}
    </nav>
  )

  return (
    <header className="sticky top-0 z-40 border-b border-border/80 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3 sm:px-6">
        <a
          href="#top"
          className="min-w-0 flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-md"
        >
          <p className="truncate font-semibold tracking-tight text-slate-deep">
            UK Founder Radar
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Teaching guide
          </p>
        </a>

        <div className="hidden lg:block">{nav}</div>

        <div className="flex shrink-0 items-center gap-2">
          <div
            className="inline-flex rounded-lg border border-border bg-card p-0.5"
            role="group"
            aria-label="Reading level"
          >
            {AUDIENCE_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => setAudience(option.id)}
                aria-pressed={audience === option.id}
                title={option.hint}
                className={cn(
                  "rounded-md px-2 py-1.5 font-mono text-[10px] uppercase tracking-wide transition sm:px-2.5 sm:text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  audience === option.id
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>

          <div className="hidden sm:block">
            <DownloadPdfButton icon={<Download className="size-4" />} />
          </div>

          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon-sm" className="lg:hidden">
                <Menu className="size-4" />
                <span className="sr-only">Open sections</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-[min(100%,20rem)]">
              <SheetHeader>
                <SheetTitle>Sections</SheetTitle>
              </SheetHeader>
              <div className="mt-4 space-y-4 px-1">
                {nav}
                <DownloadPdfButton className="w-full" icon={<Download className="size-4" />} />
                <Badge variant="secondary" className="font-mono text-[10px]">
                  Mode: {audience}
                </Badge>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  )
}
