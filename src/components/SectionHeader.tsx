import InfoPopover from "./InfoPopover"

interface SectionHeaderProps {
  icon?: string
  title: string
  description?: string
  infoTitle?: string
  infoContent?: React.ReactNode
  children?: React.ReactNode
}

export default function SectionHeader({ icon, title, description, infoTitle, infoContent, children }: SectionHeaderProps) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        {icon && <span>{icon}</span>}
        <h2 className="text-lg font-semibold">{title}</h2>
        {infoTitle && infoContent && (
          <InfoPopover title={infoTitle}>
            {infoContent}
          </InfoPopover>
        )}
        {children}
      </div>
      {description && (
        <p className="text-xs text-muted-foreground/70">{description}</p>
      )}
    </div>
  )
}
