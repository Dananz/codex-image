import { useEffect, useState, type RefObject } from 'react'
import { Slider } from '@base-ui/react/slider'
import NumberFlow from '@number-flow/react'
import { Cpu } from '@phosphor-icons/react'
import { thumb, type Strength as StrengthKey } from './api'
import { DrawerShell, GenerateButton, useComposing, Label, RefEditor, type Added } from './drawer'
import type { Gallery } from './state'

const STRENGTH = ['Subtle', 'Balanced', 'Strong', 'New take'] as const
type StrengthName = (typeof STRENGTH)[number]
const STRENGTH_KEY: Record<StrengthName, StrengthKey> = { Subtle: 'subtle', Balanced: 'balanced', Strong: 'strong', 'New take': 'new' }
const STRENGTH_NOTE: Record<StrengthName, string> = {
  Subtle: 'Stays very close: same composition, small changes only.',
  Balanced: 'Keeps the subject and layout, freer on details.',
  Strong: 'Keeps the idea, reinvents the look.',
  'New take': 'Uses only the prompt; the original is not a reference.',
}

const SHAPES = [
  { size: '941x1672', name: 'Tall' },
  { size: '1024x1536', name: 'Portrait' },
  { size: '1024x1024', name: 'Square' },
  { size: '1536x1024', name: 'Landscape' },
  { size: '1672x941', name: 'Wide' },
]

function snapSize(w: number, h: number) {
  const r = Math.log(w / h)
  let best = SHAPES[2].size, d = Infinity
  for (const s of SHAPES) {
    const [sw, sh] = s.size.split('x').map(Number)
    const e = Math.abs(Math.log(sw / sh) - r)
    if (e < d) { d = e; best = s.size }
  }
  return best
}

export function Regenerate({ g, container, wide }: { g: Gallery; container: RefObject<HTMLElement | null>; wide: boolean }) {
  const img = useComposing(g)
  const [text, setText] = useState('')
  const [strength, setStrength] = useState<StrengthName>('Balanced')
  const [count, setCount] = useState(2)
  const [size, setSize] = useState('')
  const [scale, setScale] = useState(1)
  const [refs, setRefs] = useState<string[]>([])
  const [added, setAdded] = useState<Added[]>([])
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const i = g.composer
    if (i) { setText(i.prompt); setSize(snapSize(i.w, i.h)); setRefs(i.refs.filter(r => !r.missing).map(r => r.id)); setAdded([]); setBusy(false) }
  }, [g.composer])
  if (!img) return null
  const si = STRENGTH.indexOf(strength)
  const uploading = strength !== 'New take' && added.some(a => !a.id && !a.failed)
  const run = async () => {
    if (busy || uploading || !text.trim()) return
    setBusy(true)
    const isNew = strength === 'New take'
    await g.regenerate({
      from: img.id,
      prompt: text,
      strength: STRENGTH_KEY[strength],
      count,
      size,
      scale,
      refs: isNew ? [] : [...refs, ...added.flatMap(a => (a.id ? [a.id] : []))],
    })
    setBusy(false)
  }
  return (
    <DrawerShell g={g} container={container} wide={wide}
      footer={<GenerateButton label={uploading ? 'Uploading references' : busy ? 'Starting' : `Generate ${count}`} disabled={busy || uploading || !text.trim()} onClick={() => void run()} />}>
      <div className="space-y-6">
        <label className="block">
          <Label>Prompt</Label>
          <textarea value={text} onChange={e => setText(e.target.value)} rows={wide ? 6 : 4} data-base-ui-swipe-ignore
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run() } }}
            className="w-full resize-none rounded-2xl bg-white/[0.05] p-3.5 text-[15px] leading-[1.55] text-white outline-none focus:bg-white/[0.08]" />
        </label>
        <div>
          <div className="mb-2 flex items-baseline justify-between"><Label>How far from the original</Label><span className="text-[13px] font-medium">{strength}</span></div>
          <div className="relative flex rounded-2xl bg-white/[0.05] p-1" role="radiogroup" aria-label="How far from the original" data-base-ui-swipe-ignore>
            <span className="absolute inset-y-1 rounded-xl bg-white transition-[left] duration-300 ease-[cubic-bezier(.2,.8,.2,1)]" style={{ width: `calc((100% - 8px) / 4)`, left: `calc(4px + ${si} * (100% - 8px) / 4)` }} />
            {STRENGTH.map(s => (
              <button key={s} role="radio" aria-checked={s === strength} onClick={() => setStrength(s)}
                className={`relative z-10 h-10 flex-1 rounded-xl text-[13px] transition-colors ${s === strength ? 'text-black' : 'text-white/65 hover:text-white'}`}>{s}</button>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-3">
            {img.pending
              ? <span className="size-12 shrink-0 animate-pulse rounded-lg bg-white/[0.05]" />
              : <img src={thumb(img.id, 480)} className="size-12 shrink-0 rounded-lg object-cover transition" alt="" style={{ opacity: 1 - si * 0.25, filter: `blur(${si * 1.5}px)` }} />}
            <p className="text-[12px] leading-[1.5] text-white/50">{STRENGTH_NOTE[strength]}</p>
          </div>
        </div>
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <Label>How many</Label>
            <span className="text-[14px] font-medium tabular-nums"><NumberFlow value={count} /> {count === 1 ? 'image' : 'images'}</span>
          </div>
          <Steps value={count} min={1} max={10} onChange={setCount} label="How many" shoot="count-slider" />
        </div>
        {strength !== 'New take' && <div><Label>References</Label><RefEditor img={img} keep={refs} setKeep={setRefs} added={added} setAdded={setAdded} /></div>}
        <SizeSlider value={size} onChange={setSize} />
        <Resolution size={size} scale={scale} onChange={setScale} />
      </div>
    </DrawerShell>
  )
}

function SizeSlider({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const i = Math.max(0, SHAPES.findIndex(s => s.size === value))
  const [w, h] = SHAPES[i].size.split('x').map(Number)
  const box = 44
  const pw = w >= h ? box : (box * w) / h
  const ph = h >= w ? box : (box * h) / w
  return (
    <div>
      <Label>Shape</Label>
      <div className="flex items-center gap-4 rounded-2xl bg-white/[0.04] p-3.5">
        <span className="grid size-12 shrink-0 place-items-center">
          <span className="rounded-[6px] border-2 border-white/80 bg-white/[0.08] transition-[width,height] duration-300 ease-[cubic-bezier(.2,.9,.25,1.1)]" style={{ width: pw, height: ph }} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="mb-2.5 flex items-baseline justify-between">
            <span className="text-[14px] font-medium">{SHAPES[i].name}</span>
            <span className="text-[12px] tabular-nums text-white/40">{SHAPES[i].size.replace('x', ' × ')}</span>
          </div>
          <Steps value={i} min={0} max={SHAPES.length - 1} onChange={v => onChange(SHAPES[v].size)} label="Shape" shoot="size-slider" />
          <div className="mt-1.5 flex justify-between text-[11px] text-white/35"><span>Tall</span><span>Wide</span></div>
        </div>
      </div>
    </div>
  )
}

function Steps({ value, min, max, onChange, label, shoot }: { value: number; min: number; max: number; onChange: (v: number) => void; label: string; shoot?: string }) {
  const n = max - min
  return (
    <Slider.Root value={value} min={min} max={max} step={1} onValueChange={v => onChange(v as number)} data-base-ui-swipe-ignore data-shoot={shoot} aria-label={label}>
      <Slider.Control className="flex h-7 w-full touch-none items-center">
        <Slider.Track className="relative h-1.5 w-full rounded-full bg-white/10">
          {Array.from({ length: n + 1 }, (_, k) => (
            <span key={k} className={`absolute top-1/2 size-1 -translate-x-1/2 -translate-y-1/2 rounded-full ${k + min <= value ? 'bg-black/30' : 'bg-white/25'}`} style={{ left: `${(k / n) * 100}%` }} />
          ))}
          <Slider.Indicator className="rounded-full bg-white" />
          <Slider.Thumb className="size-5 rounded-full bg-white shadow-[0_2px_8px_rgba(0,0,0,.5)] outline-none focus-visible:ring-2 focus-visible:ring-sky-400" />
        </Slider.Track>
      </Slider.Control>
    </Slider.Root>
  )
}

function Resolution({ size, scale, onChange }: { size: string; scale: number; onChange: (v: number) => void }) {
  const [w, h] = (size || '1024x1024').split('x').map(Number)
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <Label>Resolution</Label>
        <span className="text-[12px] tabular-nums text-white/45"><NumberFlow value={w * scale} /> × <NumberFlow value={h * scale} /></span>
      </div>
      <div className="flex gap-1.5" role="radiogroup" aria-label="Resolution">
        {([[1, 'Standard'], [2, '2× upscale'], [4, '4× upscale']] as const).map(([v, name]) => (
          <button key={v} role="radio" aria-checked={scale === v} onClick={() => onChange(v)}
            className={`h-10 flex-1 rounded-xl text-[13px] transition ${scale === v ? 'bg-white text-black' : 'bg-white/[0.06] text-white/70 hover:bg-white/[0.1]'}`}>{name}</button>
        ))}
      </div>
      {scale === 1
        ? <p className="mt-2 text-[12px] leading-[1.5] text-white/40">The size Codex paints at for this shape. Codex cannot paint larger.</p>
        : (
          <div className="mt-2.5 flex gap-2.5 rounded-xl bg-white/[0.04] p-3 text-[12px] leading-[1.5] text-white/55" data-shoot="upscale-note">
            <Cpu size={16} className="mt-px shrink-0 text-white/45" />
            <p>
              After Codex delivers the image, <span className="text-white/85">Real-ESRGAN</span> (an open-source AI upscaler, model x4plus) enlarges it on this Mac, offline, in about 10 seconds.
              It keeps the picture exactly as painted; Codex is not asked again. Fine textures like water can come out a little smooth.
            </p>
          </div>
        )}
    </div>
  )
}
