import { useState } from 'react'
import { ArrowRight, Terminal } from '@phosphor-icons/react'
import type { Img } from './api'
import { dayLabel, timeLabel, useWidth } from './parts'
import { glass } from './feed'
import { useCurrent, useSwipe, Picture, BackChip, Actions, RegenButton, CopyPrompt, metaRows, RefThumbs, Thumb, Arrows } from './kit'
import { Regenerate } from './regen'
import type { Gallery } from './state'

export function Editorial({ g, back }: { g: Gallery; back: (img: Img) => void }) {
  const { img, idx, prev, next, parent, children } = useCurrent(g, back)
  const { dx, handlers } = useSwipe(() => prev && g.setOpenId(prev.id), () => next && g.setOpenId(next.id))
  const [rootRef, width] = useWidth<HTMLDivElement>()
  return (
    <div ref={rootRef} className="absolute inset-0 z-30 overflow-y-auto bg-[#0b0b0d] md:flex md:overflow-hidden" data-shoot="detail">
      {img && (
        <>
          <div className="relative h-[56vh] shrink-0 md:h-auto md:flex-[1.5]">
            <div className="absolute inset-0 flex touch-pan-y items-center justify-center p-4 pt-[68px] md:p-12" {...handlers}
              style={{ transform: `translateX(${dx * 0.6}px)`, transition: dx ? 'none' : 'transform .25s' }}>
              <Picture img={img} className="rounded-lg" />
            </div>
            <BackChip img={img} back={back} className="absolute left-4 top-4 z-20" />
            <Arrows prev={prev} next={next} g={g} />
          </div>

          <section className="relative flex flex-col md:w-[440px] md:shrink-0 md:border-l md:border-white/[0.06]">
            <div className="flex-1 px-6 pb-28 pt-6 md:overflow-y-auto md:px-9 md:pt-10">
              <p className="text-[12px] text-white/45">{dayLabel(img.date)} at {timeLabel(img.date)}{idx >= 0 && <> <span className="text-white/25">·</span> {idx + 1} of {g.list.length.toLocaleString()}</>}</p>
              {img.prompt
                ? <PromptHeading key={img.id} text={img.prompt} />
                : <h1 className="mt-4 text-[24px] font-medium leading-[1.3] tracking-[-0.02em] text-white/35 md:text-[26px]">No prompt recorded</h1>}
              <div className="mt-3 flex items-center justify-between gap-3">
                {img.context && !img.prompt.startsWith(img.context.slice(0, 60)) ? <p className="flex min-w-0 gap-1.5 text-[13px] text-white/45"><Terminal size={14} className="mt-[3px] shrink-0" /><span>{img.context}</span></p> : <span />}
                <CopyPrompt img={img} />
              </div>

              <dl className="mt-9 border-t border-white/[0.07] text-[13px]">
                {metaRows(img).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between border-b border-white/[0.07] py-2.5"><dt className="text-white/45">{k}</dt><dd className="text-white/85">{v}</dd></div>
                ))}
              </dl>

              <h3 className="mt-9 text-[13px] font-medium text-white/80">References</h3>
              <div className="mt-3"><RefThumbs img={img} size={72} /></div>

              <h3 className="mt-9 text-[13px] font-medium text-white/80">Lineage</h3>
              <div className="mt-3 flex flex-wrap items-center gap-2.5" data-shoot="lineage">
                {parent ? <Thumb img={parent} size={56} label="Made from" onClick={() => g.setOpenId(parent.id)} /> : <span className="grid size-14 place-items-center rounded-lg border border-dashed border-white/10 text-[10px] text-white/30">original</span>}
                <ArrowRight size={14} className="text-white/30" />
                <Thumb img={img} size={56} current />
                <ArrowRight size={14} className="text-white/30" />
                {children.length ? children.map(c => <Thumb key={c.id} img={c} size={56} label="Regenerated as" onClick={() => g.setOpenId(c.id)} />)
                  : <button onClick={() => g.setComposer(img)} disabled={img.pending} className="grid size-14 place-items-center rounded-lg border border-dashed border-white/15 text-[18px] text-white/40 hover:border-white/30 hover:text-white/70 disabled:opacity-40" aria-label="Regenerate">+</button>}
              </div>
            </div>
            <div className="fixed inset-x-0 bottom-0 z-20 flex justify-center bg-gradient-to-t from-[#0b0b0d] via-[#0b0b0d]/90 to-transparent px-4 pb-4 pt-10 md:absolute">
              <div data-shoot="island" className={`flex items-center gap-0.5 rounded-full p-1 ${glass}`}>
                <Actions g={g} img={img} />
                <span className="mx-1 h-6 w-px bg-white/10" />
                <RegenButton g={g} img={img} compact />
              </div>
            </div>
          </section>
        </>
      )}
      {!img && <div className="grid h-full w-full place-items-center text-[13px] text-white/45">Loading image</div>}
      <Regenerate g={g} container={rootRef} wide={width >= 768} />
    </div>
  )
}

// Skill prompts can run to thousands of characters: short ones stay a headline, long ones step down and fold.
function PromptHeading({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const n = text.length
  const size = n <= 140 ? 'text-[24px] md:text-[26px] leading-[1.3] tracking-[-0.02em]' : n <= 400 ? 'text-[18px] leading-[1.45] tracking-[-0.01em]' : 'text-[15px] leading-[1.55]'
  const folds = n > 400
  return (
    <div className="mt-4">
      <h1 className={`whitespace-pre-line font-medium text-white ${size} ${folds && !open ? 'line-clamp-[9]' : ''}`}>{text}</h1>
      {folds && (
        <button onClick={() => setOpen(o => !o)} className="mt-2 text-[13px] font-medium text-sky-300 hover:text-sky-200" data-shoot="prompt-more">
          {open ? 'Show less' : `Show all (${n.toLocaleString()} characters)`}
        </button>
      )}
    </div>
  )
}
