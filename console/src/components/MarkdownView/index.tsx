import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Shared Google-styled markdown renderer.
 *
 * Extracted from the chat page's inline renderer so skill details and
 * other read-only markdown views share one consistent style.
 *
 * When `resolveFileLink` is provided, hrefs that map to a console URL
 * (e.g. workspace files → Files page) are intercepted: clicking opens
 * the resolved URL in a new browser tab instead of following the raw
 * href (which would 404 for workspace-relative paths).
 */
export default function MarkdownView({
  text,
  resolveFileLink,
}: {
  text: string;
  resolveFileLink?: (href: string) => string | null;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // Style headings
        h1: ({ children }) => <div style={{ fontSize: 18, fontWeight: 700, margin: '12px 0 6px', color: 'var(--google-foreground)' }}>{children}</div>,
        h2: ({ children }) => <div style={{ fontSize: 16, fontWeight: 700, margin: '8px 0 4px', color: 'var(--google-foreground)' }}>{children}</div>,
        h3: ({ children }) => <div style={{ fontSize: 14, fontWeight: 600, margin: '6px 0 3px', color: 'var(--google-foreground)' }}>{children}</div>,
        // Style paragraphs
        p: ({ children }) => <div style={{ margin: '2px 0' }}>{children}</div>,
        // Style lists
        ul: ({ children }) => <div style={{ paddingLeft: 16, margin: '2px 0' }}>{children}</div>,
        ol: ({ children }) => <div style={{ paddingLeft: 16, margin: '2px 0' }}>{children}</div>,
        li: ({ children }) => <div style={{ margin: '1px 0' }}>{'\u2022 '}{children}</div>,
        // Style inline code
        code: ({ children, className }) => {
          const isBlock = className?.includes('language-');
          if (isBlock) {
            return (
              <pre style={{ background: 'var(--google-muted)', padding: 'var(--google-space-4)', borderRadius: 'var(--google-radius-md)', overflow: 'auto', fontSize: 13, fontFamily: 'var(--google-font-mono)', margin: 'var(--google-space-3) 0' }}>
                <code>{children}</code>
              </pre>
            );
          }
          return (
            <code style={{ background: 'var(--google-muted)', padding: '1px 6px', borderRadius: 'var(--google-radius-sm)', fontSize: 13, fontFamily: 'var(--google-font-mono)' }}>
              {children}
            </code>
          );
        },
        // Style bold
        strong: ({ children }) => <strong>{children}</strong>,
        // Style links
        a: ({ href, children }) => {
          const target = href && resolveFileLink ? resolveFileLink(href) : null;
          if (target) {
            return (
              <a
                href={target}
                onClick={(e) => {
                  e.preventDefault();
                  window.open(target, '_blank', 'noopener');
                }}
                style={{ color: 'var(--google-primary)', cursor: 'pointer' }}
              >
                {children}
              </a>
            );
          }
          return <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--google-primary)' }}>{children}</a>;
        },
        // Style blockquotes
        blockquote: ({ children }) => <div style={{ borderLeft: '3px solid var(--google-border)', paddingLeft: 12, margin: '6px 0', color: 'var(--google-muted-foreground)' }}>{children}</div>,
        // Style GFM tables
        table: ({ children }) => (
          <div style={{ overflow: 'auto', margin: 'var(--google-space-3) 0' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th style={{ border: '1px solid var(--google-border)', background: 'var(--google-muted)', padding: '6px 10px', textAlign: 'left', fontWeight: 600 }}>{children}</th>
        ),
        td: ({ children }) => (
          <td style={{ border: '1px solid var(--google-border)', padding: '6px 10px', verticalAlign: 'top' }}>{children}</td>
        ),
        // Strikethrough / task lists from GFM
        del: ({ children }) => <del style={{ color: 'var(--google-muted-foreground)' }}>{children}</del>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
