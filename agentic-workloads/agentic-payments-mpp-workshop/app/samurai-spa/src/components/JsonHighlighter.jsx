export default function JsonHighlighter({ data }) {
  let text
  try { text = JSON.stringify(data, null, 2) } catch { text = String(data) }
  // Minimal highlighter: color keys, strings, numbers, booleans
  const html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/("(?:\\.|[^"\\])*")(\s*:)/g, '<span style="color:#a14b1f">$1</span>$2')
    .replace(/:\s*("(?:\\.|[^"\\])*")/g, ': <span style="color:#1a7a2c">$1</span>')
    .replace(/:\s*(-?\d+(?:\.\d+)?)/g, ': <span style="color:#175dda">$1</span>')
    .replace(/:\s*(true|false|null)/g, ': <span style="color:#7a3aa4">$1</span>')
  return (
    <pre
      className="font-mono text-[11px] whitespace-pre-wrap break-all text-[#141414] bg-black/[0.02] p-2 rounded"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
