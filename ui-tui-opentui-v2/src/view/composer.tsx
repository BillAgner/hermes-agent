/**
 * Composer — the input row (spec v4 §2). A native <textarea> captured by ref;
 * Enter submits, the input clears imperatively, and a live slash-completion
 * dropdown renders ABOVE it as you type `/…` (spec §1 completions).
 *
 * Gotchas (§8 #3): `flexShrink:0` so it never collapses onto its rule; clear via
 * `.clear()` (NOT key-remount); a `submitting` re-entrancy guard.
 *
 * Completions: `onContentChange` reports the text → `onType` (entry boundary)
 * queries `complete.slash` and fills `completions()`. The textarea owns key input
 * (so live-refine-by-typing works), so we use Tab to accept the top match and Esc
 * to dismiss (arrow-nav would fight the textarea's cursor; a polish item).
 * `onSubmit`/`onType` are plain callbacks wired by the entry — no Effect here.
 *
 * Always-active input (item 2): the textarea focuses on mount, on click
 * (onMouseDown), and reclaims focus on the next PRINTABLE keystroke if focus ever
 * drifted off (e.g. the transcript scrollbox grabbed it on a mouse-scroll). Nav
 * keys are left alone so keyboard transcript-scroll still works (opencode keeps
 * the prompt focused via a reactive effect; here a keystroke net is enough since
 * the composer remounts+refocuses whenever an overlay closes).
 */
import { type TextareaRenderable } from '@opentui/core'
import { useKeyboard } from '@opentui/solid'
import { For, onMount, Show } from 'solid-js'

import type { CompletionItem } from '../logic/store.ts'
import { useTheme } from './theme.tsx'

/** Keys that must NOT steal focus back to the composer (scroll/edit/nav). */
const NAV_KEYS = new Set([
  'return',
  'linefeed',
  'tab',
  'escape',
  'backspace',
  'delete',
  'insert',
  'up',
  'down',
  'left',
  'right',
  'home',
  'end',
  'pageup',
  'pagedown',
  'clear',
  'menu'
])

/** A printable, unmodified key press (recoverable into the textarea). */
function isPrintableKey(k: {
  name: string
  ctrl: boolean
  meta: boolean
  option: boolean
  super?: boolean
  sequence: string
  eventType?: string
}): boolean {
  return (
    k.eventType !== 'release' &&
    !k.ctrl &&
    !k.meta &&
    !k.option &&
    !k.super &&
    !NAV_KEYS.has(k.name) &&
    typeof k.sequence === 'string' &&
    k.sequence.length >= 1 &&
    (k.sequence.codePointAt(0) ?? 0) >= 0x20
  )
}

export function Composer(props: {
  onSubmit: (text: string) => void
  onType?: ((text: string) => void) | undefined
  completions?: (() => CompletionItem[]) | undefined
  onDismiss?: (() => void) | undefined
}) {
  const theme = useTheme()
  let ta: TextareaRenderable | undefined
  let submitting = false
  const completions = () => props.completions?.() ?? []

  const submit = () => {
    if (submitting || !ta) return
    const text = ta.plainText.trim()
    if (!text) return
    submitting = true
    props.onSubmit(text)
    ta.clear()
    props.onDismiss?.()
    submitting = false
  }

  useKeyboard(key => {
    // 1) completion accept (Tab) / dismiss (Esc) while the dropdown is open
    if (completions().length > 0) {
      if (key.name === 'tab') {
        const top = completions()[0]
        if (top && ta) {
          ta.clear()
          ta.insertText(top.text + ' ')
          props.onDismiss?.()
        }
        return
      }
      if (key.name === 'escape') {
        props.onDismiss?.()
        return
      }
    }
    // 2) always-active input (item 2): a printable key while the textarea lost
    // focus reclaims it AND recovers the char (the in-flight event went to this
    // global handler, not the unfocused textarea). Nav/scroll keys are untouched.
    if (ta && !ta.focused && isPrintableKey(key)) {
      ta.focus()
      ta.insertText(key.sequence)
    }
  })

  onMount(() => ta?.focus())

  return (
    <box style={{ flexDirection: 'column', flexShrink: 0, marginTop: 1 }}>
      <Show when={completions().length > 0}>
        <box
          style={{
            backgroundColor: theme().color.completionBg,
            flexDirection: 'column',
            paddingLeft: 1,
            paddingRight: 1
          }}
        >
          <For each={completions().slice(0, 8)}>
            {(c, i) => (
              <text fg={i() === 0 ? theme().color.accent : theme().color.text}>
                {c.display || c.text}
                {c.meta ? `  ${c.meta}` : ''}
              </text>
            )}
          </For>
          <text fg={theme().color.muted}>Tab complete · Esc dismiss</text>
        </box>
      </Show>
      <textarea
        ref={el => (ta = el)}
        style={{ height: 3, width: '100%' }}
        placeholder={theme().brand.welcome}
        placeholderColor={theme().color.muted}
        textColor={theme().color.text}
        cursorColor={theme().color.accent}
        focusedBackgroundColor={theme().color.statusBg}
        keyBindings={[{ action: 'submit', name: 'return' }]}
        onMouseDown={() => ta?.focus()}
        onSubmit={submit}
        onContentChange={() => props.onType?.(ta?.plainText ?? '')}
      />
    </box>
  )
}
