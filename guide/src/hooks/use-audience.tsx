import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import type { Audience } from "@/content/types"

interface AudienceContextValue {
  audience: Audience
  setAudience: (audience: Audience) => void
  /** True when Technical depth is selected. */
  isTechnical: boolean
}

const AudienceContext = createContext<AudienceContextValue | null>(null)

export function AudienceProvider({ children }: { children: ReactNode }) {
  const [audience, setAudience] = useState<Audience>("easy")
  const value = useMemo(
    () => ({
      audience,
      setAudience,
      isTechnical: audience === "technical",
    }),
    [audience],
  )
  return (
    <AudienceContext.Provider value={value}>{children}</AudienceContext.Provider>
  )
}

export function useAudience() {
  const ctx = useContext(AudienceContext)
  if (!ctx) throw new Error("useAudience must be used within AudienceProvider")
  return ctx
}
