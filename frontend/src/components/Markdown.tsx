import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import CopyButton from '@/components/CopyButton'

/**
 * Renders the agent's answer (headings, lists, tables, code, links).
 *
 * The text can contain words from any web page the agent visited, so it is treated as untrusted:
 *  - raw HTML is not rendered (react-markdown's default);
 *  - links only use safe protocols (react-markdown's default) and open in a new tab without
 *    giving the page a handle back to this app;
 *  - IMAGES are not loaded. A markdown image from a hostile page would make this browser request
 *    an attacker's URL the moment the answer appears (a classic way to leak data); it is shown as a
 *    plain link instead.
 */



function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (node && typeof node === 'object' && 'props' in node) return textOf((node as { props: { children?: ReactNode } }).props.children)
  return ''
}

/** A JSON answer reads better as a code block than as one long line. */
function prepare(text: string): string {
  const t = text.trim()
  if ((t.startsWith('{') || t.startsWith('[')) && t.length > 1 && t.length < 200_000) {
    try {
      return '```json\n' + JSON.stringify(JSON.parse(t), null, 2) + '\n```'
    } catch {
      /* not JSON: show as written */
    }
  }
  return text
}

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="md text-[14.5px] leading-relaxed text-gray-800 break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-brand underline underline-offset-2">
              {children}
            </a>
          ),
          img: ({ src, alt }) => (
            <span className="text-gray-500">
              [image{alt ? `: ${alt}` : ''}
              {src ? (
                <>
                  {' '}
                  <a href={src} target="_blank" rel="noopener noreferrer nofollow" className="underline">
                    open
                  </a>
                </>
              ) : null}
              ]
            </span>
          ),
          h1: ({ children }) => <h3 className="mt-3 mb-1.5 text-base font-semibold text-gray-900">{children}</h3>,
          h2: ({ children }) => <h3 className="mt-3 mb-1.5 text-[15px] font-semibold text-gray-900">{children}</h3>,
          h3: ({ children }) => <h4 className="mt-2.5 mb-1 text-[14.5px] font-semibold text-gray-900">{children}</h4>,
          h4: ({ children }) => <h4 className="mt-2 mb-1 text-[14.5px] font-semibold text-gray-900">{children}</h4>,
          p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="my-1.5 ml-5 list-disc space-y-0.5">{children}</ul>,
          ol: ({ children }) => <ol className="my-1.5 ml-5 list-decimal space-y-0.5">{children}</ol>,
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-gray-200 pl-3 text-gray-600">{children}</blockquote>
          ),
          hr: () => <hr className="my-3 border-gray-100" />,
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto rounded-lg border border-gray-100">
              <table className="min-w-full text-left text-[13px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-gray-50 text-gray-600">{children}</thead>,
          th: ({ children }) => <th className="px-3 py-1.5 font-semibold whitespace-nowrap">{children}</th>,
          td: ({ children }) => <td className="border-t border-gray-100 px-3 py-1.5 align-top">{children}</td>,
          pre: ({ children }) => {
            const code = textOf(children).replace(/\n$/, '')
            return (
              <div className="my-2 overflow-hidden rounded-lg border border-gray-100 bg-gray-50">
                <div className="flex justify-end border-b border-gray-100 px-2.5 py-1">
                  <CopyButton text={code} label="Copy code" />
                </div>
                <pre className="max-h-72 overflow-auto px-3 py-2 text-[12px] leading-relaxed">{children}</pre>
              </div>
            )
          },
          code: ({ children, className }) =>
            className ? (
              <code className={`font-mono ${className}`}>{children}</code>
            ) : (
              <code className="rounded bg-gray-100 px-1 py-px font-mono text-[12.5px]">{children}</code>
            ),
        }}
      >
        {prepare(text)}
      </ReactMarkdown>
    </div>
  )
}
