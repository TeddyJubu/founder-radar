import { PdfCard } from "@/components/pdf/card/card"
import { Heading } from "@/components/pdf/heading/heading"
import { KeyValue } from "@/components/pdf/key-value/key-value"
import { PdfList } from "@/components/pdf/list/list"
import { PageBreak } from "@/components/pdf/page-break/page-break"
import { PageFooter } from "@/components/pdf/page-footer/page-footer"
import { PageHeader } from "@/components/pdf/page-header/page-header"
import { PageNumber } from "@/components/pdf/page-number/page-number"
import { Section } from "@/components/pdf/section/section"
import { Text } from "@/components/pdf/text/text"
import { PdfcnThemeProvider } from "@/components/pdf/theme-provider"
import { aiBoundary, architectureNodes } from "@/content/architecture"
import { cliCheatSheet } from "@/content/cli"
import { fundCards } from "@/content/funds"
import { morningContent } from "@/content/morning"
import { pipelineStages } from "@/content/pipeline"
import { whyContent } from "@/content/why"
import { Document, Page, View } from "@/lib/pdf-primitives"
import { blueprintTheme } from "@/lib/pdf-themes/blueprint"

/** Strip glyphs that Blueprint mono fonts may not cover. */
function asciiSafe(input: string): string {
  return input
    .replaceAll("→", "->")
    .replaceAll("·", "-")
    .replaceAll("—", "-")
    .replaceAll("–", "-")
    .replaceAll("“", '"')
    .replaceAll("”", '"')
    .replaceAll("‘", "'")
    .replaceAll("’", "'")
    .replaceAll("①", "1")
    .replaceAll("②", "2")
    .replaceAll("③", "3")
    .replaceAll("④", "4")
    .replaceAll("⑤", "5")
    .replaceAll("⑥", "6")
    .replaceAll("⑦", "7")
    .replaceAll("½", ".5")
}

function GuideBody() {
  return (
    <Document title="UK Founder Radar - Teaching Handout">
      <Page size="A4">
        <View
          style={{
            backgroundColor: blueprintTheme.colors.background,
            padding: 48,
            minHeight: 841,
            position: "relative",
          }}
        >
          <PageHeader
            variant="minimal"
            title="UK Founder Radar"
            subtitle="Teaching handout - architecture, morning loop, funds, CLI"
          />
          <Heading level={1} noMargin>
            How the system works
          </Heading>
          <Text variant="sm" color="mutedForeground">
            {asciiSafe(`${whyContent.easyLead} ${whyContent.successLine}`)}
          </Text>

          <Section spacing="sm">
            <Heading level={2}>Architecture (one truth)</Heading>
            <Text variant="sm">
              {asciiSafe(
                "SQLite is the real notebook. The founder-radar CLI is the engine. Sheet, Today/Kept, and Telegram are windows. Hermes maps chat to CLI only.",
              )}
            </Text>
            <KeyValue
              size="sm"
              items={architectureNodes.map((n) => ({
                key: n.label,
                value: asciiSafe(n.role),
              }))}
            />
          </Section>

          <Section spacing="sm" variant="callout">
            <Heading level={3} noMargin>
              AI may / may not
            </Heading>
            <Text variant="xs" weight="semibold" noMargin>
              May
            </Text>
            <PdfList
              variant="bullet"
              gap="xs"
              items={aiBoundary.may.map((item) => ({ text: asciiSafe(item) }))}
            />
            <Text variant="xs" weight="semibold" noMargin>
              May not
            </Text>
            <PdfList
              variant="bullet"
              gap="xs"
              items={aiBoundary.mayNot.map((item) => ({ text: asciiSafe(item) }))}
            />
          </Section>

          <PageFooter
            leftText="Canonical rules from product defaults - not marketing copy"
            rightText={<PageNumber format="p.{page}" />}
            sticky
            pagePadding={36}
          />
        </View>
      </Page>

      <PageBreak />

      <Page size="A4">
        <View
          style={{
            backgroundColor: blueprintTheme.colors.background,
            padding: 48,
            minHeight: 841,
            position: "relative",
          }}
        >
          <Heading level={2}>Morning loop</Heading>
          <PdfList
            variant="numbered"
            items={morningContent.easySteps.map((s) => ({
              text: asciiSafe(`${s.title}: ${s.body}`),
            }))}
          />
          <Text variant="sm">
            {asciiSafe(
              "Keys: 1 yes -> Kept. 2 maybe -> Kept. 3 no -> never on Kept, never back on Today. Quiet mornings with zero cards are OK.",
            )}
          </Text>

          <Heading level={2}>Pipeline stages</Heading>
          <PdfList
            variant="bullet"
            gap="xs"
            items={pipelineStages.map((s) => ({
              text: asciiSafe(
                `${s.numeralPlain} ${s.name} - ${s.easy} [${
                  s.ai === "no" ? "no AI" : s.ai === "veto" ? "AI veto" : "AI"
                }]`,
              ),
            }))}
          />
          <PdfCard title="Stage 6 is the product core" variant="bordered" padding="sm">
            <Text variant="sm" noMargin>
              {asciiSafe(
                "Gate + score: no AI, no network, deterministic. Built before crawlers. Scores stamped with config_hash for reproducible answers.",
              )}
            </Text>
          </PdfCard>

          <PageFooter
            leftText="UK Founder Radar teaching guide"
            rightText={<PageNumber format="p.{page}" />}
            sticky
            pagePadding={36}
          />
        </View>
      </Page>

      <PageBreak />

      <Page size="A4">
        <View
          style={{
            backgroundColor: blueprintTheme.colors.background,
            padding: 48,
            minHeight: 841,
            position: "relative",
          }}
        >
          <Heading level={2}>Fund rules (canonical)</Heading>
          <Text variant="xs" color="mutedForeground">
            From radar/config/defaults.py - unpublished cheque sizes left blank, not guessed.
            Do not invent descriptors.
          </Text>
          {fundCards.map((fund) => (
            <Section key={fund.key} spacing="sm" border padding="sm">
              <Heading level={3} noMargin>
                {fund.name}
              </Heading>
              <Text variant="xs" noMargin>
                {asciiSafe(fund.mandate)}
              </Text>
              <PdfList
                variant="bullet"
                gap="xs"
                items={fund.vehicles
                  .filter((v) => v.active)
                  .map((v) => ({
                    text: asciiSafe(
                      `${v.name}: ${v.geo}; ${v.stage}; ${v.oneLiner}${
                        v.hardRules.length ? ` - hard: ${v.hardRules.join(", ")}` : ""
                      }`,
                    ),
                  }))}
              />
            </Section>
          ))}

          <PageFooter
            leftText="Fund criteria must match real rules"
            rightText={<PageNumber format="p.{page}" />}
            sticky
            pagePadding={36}
          />
        </View>
      </Page>

      <PageBreak />

      <Page size="A4">
        <View
          style={{
            backgroundColor: blueprintTheme.colors.background,
            padding: 48,
            minHeight: 841,
            position: "relative",
          }}
        >
          <Heading level={2}>CLI cheat sheet</Heading>
          <Text variant="sm">
            {asciiSafe(
              "Telegram keep/reject must run decide - chat-only replies do not update Today, Kept, or the sheet.",
            )}
          </Text>
          <KeyValue
            size="sm"
            divided
            items={cliCheatSheet.map((c) => ({
              key: c.command,
              value: asciiSafe(c.when),
            }))}
          />
          <Section spacing="md" variant="highlight" padding="sm">
            <Heading level={3} noMargin>
              Ops first-aid
            </Heading>
            <PdfList
              variant="bullet"
              items={[
                { text: "doctor - keys, quotas, disk, sheet" },
                { text: "why-today - empty or surprising Today / config_hash drift" },
                { text: "today-qa - re-run Hermes veto" },
                { text: "rescore - after Fund Criteria / Weights change" },
              ]}
            />
          </Section>

          <PageFooter
            leftText="Local guide only - not live production data"
            rightText={<PageNumber format="p.{page}" />}
            sticky
            pagePadding={36}
          />
        </View>
      </Page>
    </Document>
  )
}

export function GuideDocument() {
  return (
    <PdfcnThemeProvider theme={blueprintTheme}>
      <GuideBody />
    </PdfcnThemeProvider>
  )
}
