"use client"
import { Info } from "lucide-react"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

interface InfoPopoverProps {
  title: string
  children: React.ReactNode
  className?: string
  side?: "top" | "right" | "bottom" | "left"
}

export default function InfoPopover({ title, children, className, side = "bottom" }: InfoPopoverProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className={cn(
            "inline-flex items-center justify-center rounded-full p-0.5 text-muted-foreground/50 hover:text-muted-foreground hover:bg-white/5 transition-colors",
            className
          )}
          aria-label={`Подробнее: ${title}`}
        >
          <Info className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent side={side} className="w-80 space-y-2 text-sm bg-zinc-900/95 backdrop-blur-sm border-white/10">
        <h4 className="font-semibold text-foreground text-xs uppercase tracking-wider">{title}</h4>
        <div className="text-muted-foreground text-xs leading-relaxed space-y-1.5">
          {children}
        </div>
      </PopoverContent>
    </Popover>
  )
}
