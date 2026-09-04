import type { ReactNode } from 'react';

interface GooglePageHeaderProps {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  extra?: ReactNode;
}

/**
 * Consistent page header using Google typography tokens.
 */
export default function GooglePageHeader({ icon, title, subtitle, extra }: GooglePageHeaderProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 'var(--google-space-4)',
        marginBottom: 'var(--google-space-8)',
      }}
    >
      <div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--google-space-3)',
            color: 'var(--google-foreground)',
            fontSize: 'var(--google-text-2xl)',
            fontWeight: 'var(--google-font-semibold)',
            lineHeight: 'var(--google-leading-tight)',
          }}
        >
          {icon && <span style={{ color: 'var(--google-primary)' }}>{icon}</span>}
          {title}
        </div>
        {subtitle && (
          <div
            style={{
              marginTop: 'var(--google-space-2)',
              color: 'var(--google-muted-foreground)',
              fontSize: 'var(--google-text-sm)',
            }}
          >
            {subtitle}
          </div>
        )}
      </div>
      {extra && <div style={{ display: 'flex', alignItems: 'center' }}>{extra}</div>}
    </div>
  );
}
