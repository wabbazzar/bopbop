import { marked } from 'marked';
import DOMPurify from 'dompurify';

// Replies are untrusted: sanitize after Markdown parsing. No embeds or styles.
export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false, breaks: true }) as string;
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'ul', 'ol',
      'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
      'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a'],
    ALLOWED_ATTR: ['href', 'title'],
    ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(https?:\/\/|#)/i
  });
}
