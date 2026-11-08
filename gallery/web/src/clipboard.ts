import { fileSrc } from './api'

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    if (!ok) throw new Error('Clipboard not available')
  }
}

export const canCopyImage = () => typeof ClipboardItem !== 'undefined' && !!navigator.clipboard?.write

export async function copyImage(id: string) {
  const blob = fetch(fileSrc(id)).then(r => {
    if (!r.ok) throw new Error(`${r.status}`)
    return r.blob()
  }).then(b => (b.type === 'image/png' ? b : new Blob([b], { type: 'image/png' })))
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
}

export function download(id: string) {
  const a = document.createElement('a')
  a.href = fileSrc(id, true)
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}
