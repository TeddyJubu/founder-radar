import { Loader2 } from "lucide-react"
import { useState, type ReactNode } from "react"
import { GuideDocument } from "@/components/pdf/GuideDocument"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

async function downloadBytes(bytes: Uint8Array, filename: string) {
  const copy = new Uint8Array(bytes.byteLength)
  copy.set(bytes)
  const blob = new Blob([copy.buffer], { type: "application/pdf" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.rel = "noopener"
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Delay revoke so Safari finishes the download handoff.
  window.setTimeout(() => URL.revokeObjectURL(url), 1500)
}

export function DownloadPdfButton({
  className,
  icon,
}: {
  className?: string
  icon?: ReactNode
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onClick = async () => {
    setBusy(true)
    setError(null)
    try {
      const [{ render }, { googleFonts }] = await Promise.all([
        import("takumi-pdf"),
        import("@takumi-rs/helpers"),
      ])
      // Blueprint uses JetBrains Mono + Source Code Pro; Noto Sans covers
      // glyphs those mono fonts miss (arrows, circled numerals) if any slip in.
      const fonts = await googleFonts([
        "JetBrains Mono",
        "Source Code Pro",
        "Noto Sans",
      ])
      const bytes = await render(<GuideDocument />, {
        size: "a4",
        fonts,
        margin: { top: 36, right: 36, bottom: 48, left: 36 },
      })
      await downloadBytes(bytes, "uk-founder-radar-teaching-guide.pdf")
    } catch (err) {
      console.error(err)
      const message =
        err instanceof Error ? err.message : "PDF failed — try again"
      setError(
        message.includes("font")
          ? "PDF fonts failed to load. Check your network and try again."
          : message,
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cn("inline-flex flex-col items-stretch gap-1", className)}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => void onClick()}
        className={cn(className?.includes("w-full") ? "w-full" : undefined)}
      >
        {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
        {busy ? "Building PDF…" : "Download PDF"}
      </Button>
      {error ? (
        <span className="max-w-[16rem] text-[10px] leading-snug text-destructive">
          {error}
        </span>
      ) : null}
    </div>
  )
}
