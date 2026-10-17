import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { MarkdownTextContent } from './markdown-text'

// Model output reaches the transcript DOM through two sanitizers: Streamdown's
// rehype pipeline for raw HTML inside markdown, and DOMPurify for ```svg
// fences (svg-embed.tsx). A leaked script-capable element, inline event
// handler, or javascript: URL is script execution in the renderer.
const EXECUTABLE_TAGS = new Set(['embed', 'foreignobject', 'iframe', 'object', 'script'])

function executableLeaks(root: Element): string[] {
  const leaks: string[] = []

  for (const element of root.querySelectorAll('*')) {
    const tag = element.localName.toLowerCase()

    if (EXECUTABLE_TAGS.has(tag)) {
      leaks.push(`<${tag}>`)
    }

    for (const attribute of Array.from(element.attributes)) {
      if (/^on/i.test(attribute.name)) {
        leaks.push(`${tag}[${attribute.name}]`)
      }

      if (/(^|:)(href|src|action|formaction)$/i.test(attribute.name) && /^\s*javascript:/i.test(attribute.value)) {
        leaks.push(`${tag}[${attribute.name}="${attribute.value}"]`)
      }
    }
  }

  return leaks
}

describe('MarkdownTextContent sanitizing', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders raw HTML in markdown without executable elements, handlers, or javascript: URLs', async () => {
    const text = [
      'Intro paragraph stays.',
      '',
      '<img src="x" onerror="window.__pwned = 1"> <b onclick="window.__pwned = 1">bold stays</b>',
      '',
      '<script>window.__pwned = 1</script>',
      '',
      '<a href="javascript:window.__pwned = 1">click</a> <iframe src="https://example.com"></iframe>',
      '',
      '<object data="x.swf"></object><embed src="x.swf">'
    ].join('\n')

    const { container } = render(<MarkdownTextContent isRunning={false} text={text} />)

    await waitFor(() => expect(container.textContent).toContain('bold stays'))

    expect(container.textContent).toContain('Intro paragraph stays.')
    expect(container.querySelector('b')?.textContent).toBe('bold stays')
    expect(executableLeaks(container)).toEqual([])
  })

  it('renders an svg fence as inline svg with scripts, handlers, foreignObject, and javascript: links removed', async () => {
    const svg = [
      '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">',
      '<script>window.__pwned = 1</script>',
      '<circle cx="5" cy="5" r="4" fill="red" onload="window.__pwned = 1"/>',
      '<a href="javascript:window.__pwned = 1"><rect width="2" height="2"/></a>',
      '<a xlink:href="javascript:window.__pwned = 1"><rect x="3" width="2" height="2"/></a>',
      '<foreignObject width="10" height="10"><img src="x" onerror="window.__pwned = 1"/></foreignObject>',
      '<animate attributeName="href" to="javascript:window.__pwned = 1"/>',
      '</svg>'
    ].join('')

    const { container } = render(
      <MarkdownTextContent isRunning={false} text={`Diagram:\n\n\`\`\`svg\n${svg}\n\`\`\`\n`} />
    )

    const embedded = await waitFor(() => {
      const node = container.querySelector('svg[viewBox="0 0 10 10"]')

      expect(node).not.toBeNull()

      return node!
    })

    expect(embedded.querySelector('circle')?.getAttribute('fill')).toBe('red')
    expect(embedded.querySelectorAll('rect')).toHaveLength(2)
    expect(embedded.querySelector('animate[to^="javascript:"]')).toBeNull()
    expect(executableLeaks(container)).toEqual([])
  })
})
