import { isValidElement } from "react"
import type { DependencyList, ReactNode } from "react"
import { blueprintTheme } from "@/lib/pdf-themes/blueprint"
import type { PdfcnTheme } from "@/types/pdf-themes"

export type { PdfcnTheme }

let serializedTheme: PdfcnTheme = blueprintTheme

export interface PdfcnThemeProviderProps {
  theme?: PdfcnTheme
  children: ReactNode
}

const renderForSerializer = (
  children: ReactNode,
  theme: PdfcnTheme,
): ReactNode => {
  serializedTheme = theme

  if (!isValidElement(children) || typeof children.type !== "function") {
    return children
  }

  return (children.type as (props: unknown) => ReactNode)(children.props)
}

export const PdfcnThemeProvider = ({
  theme,
  children,
}: PdfcnThemeProviderProps) =>
  renderForSerializer(children, theme ?? blueprintTheme)

export const usePdfcnTheme = (): PdfcnTheme => serializedTheme

export const useSafeMemo = <T,>(factory: () => T, _deps: DependencyList): T =>
  factory()
