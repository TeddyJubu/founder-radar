import { Reveal, SectionShell, TechnicalPanel } from "@/components/SectionShell"
import { cliCheatSheet, howToUseEasy, surfacesTable } from "@/content/cli"
import { useAudience } from "@/hooks/use-audience"
import { Badge } from "@/components/ui/badge"

export function UseSection() {
  const { isTechnical } = useAudience()
  const commands = cliCheatSheet.filter(
    (c) => isTechnical || c.audience === "both",
  )

  return (
    <SectionShell id="use" number="06" title="Day to day" eyebrow="What you actually do">
      <div className="mb-10 grid gap-4 md:grid-cols-3">
        {howToUseEasy.map((item) => (
          <Reveal key={item.title}>
            <div className="h-full rounded-xl border border-border bg-card/70 p-4">
              <h3 className="text-lg font-semibold text-slate-deep">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{item.body}</p>
            </div>
          </Reveal>
        ))}
      </div>

      {isTechnical ? (
        <Reveal>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-[32rem] text-left text-sm">
              <thead className="bg-muted/60 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Surface</th>
                  <th className="px-3 py-2">Job</th>
                  <th className="px-3 py-2">Owns</th>
                </tr>
              </thead>
              <tbody>
                {surfacesTable.map((row) => (
                  <tr key={row.surface} className="border-t border-border">
                    <td className="px-3 py-2 font-medium">{row.surface}</td>
                    <td className="px-3 py-2 text-muted-foreground">{row.role}</td>
                    <td className="px-3 py-2 text-muted-foreground">{row.owns}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Reveal>
      ) : null}

      <Reveal className="mt-8">
        <div className="rounded-2xl border border-border bg-card/80 p-5">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold text-slate-deep">
              {isTechnical ? "CLI cheat sheet" : "The two saves that matter"}
            </h3>
            <Badge variant="secondary" className="font-mono text-[10px]">
              {isTechnical ? "technical" : "keep / reject"}
            </Badge>
          </div>
          <ul className="space-y-3">
            {commands.map((c) => (
              <li key={c.command} className="text-sm">
                {isTechnical ? (
                  <code className="block break-all rounded-md bg-muted px-2 py-1.5 font-mono text-xs">
                    {c.command}
                  </code>
                ) : null}
                <p className={isTechnical ? "mt-1 text-muted-foreground" : "text-muted-foreground"}>
                  {isTechnical
                    ? c.when
                    : c.command.includes("worth contacting")
                      ? "Save a Yes so it appears in Kept everywhere."
                      : "Save a No so it never comes back on Today."}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </Reveal>

      {isTechnical ? (
        <TechnicalPanel>
          <p>
            First aid: <code className="font-mono text-xs">doctor</code>,{" "}
            <code className="font-mono text-xs">why-today</code>,{" "}
            <code className="font-mono text-xs">today-qa</code>,{" "}
            <code className="font-mono text-xs">rescore</code>.
          </p>
          <p>
            Telegram keep / reject must run{" "}
            <code className="font-mono text-xs">founder-radar decide</code> — chat-only
            replies do not update Today, Kept, or the sheet.
          </p>
        </TechnicalPanel>
      ) : null}
    </SectionShell>
  )
}
