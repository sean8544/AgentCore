import { Card, type CardProps } from 'antd';
import type { ReactNode } from 'react';

interface GoogleCardProps extends Omit<CardProps, 'title' | 'variant'> {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  surface?: 'default' | 'muted';
}

/**
 * Google-styled card wrapper.
 * Uses CSS variables for background, border and radius so it adapts to light/dark mode.
 */
export default function GoogleCard({
  title,
  extra,
  children,
  surface = 'default',
  bodyStyle,
  headStyle,
  ...rest
}: GoogleCardProps) {
  const background = surface === 'muted' ? 'var(--google-muted)' : 'var(--google-card)';
  return (
    <Card
      {...rest}
      title={title}
      extra={extra}
      bodyStyle={{
        background,
        ...bodyStyle,
      }}
      headStyle={{
        background,
        borderBottom: '1px solid var(--google-border)',
        ...headStyle,
      }}
      style={{
        border: '1px solid var(--google-border)',
        borderRadius: 'var(--google-radius-lg)',
        background,
        ...rest.style,
      }}
    >
      {children}
    </Card>
  );
}
