import { LEVEL_COLOR, levelRu } from "@/lib/format";
import type { Level } from "@/lib/types";

export default function LevelChip({ level }: { level: Level }) {
  return <span className="text-xs" style={{ color: LEVEL_COLOR[level] }}>{levelRu(level)}</span>;
}
