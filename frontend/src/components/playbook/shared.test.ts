import { describe, expect, it } from 'vitest'
import type { Playbook, PlaybookStep } from '@/types'
import { askForText, askedVariable, isGuarded, stopAskingForText, usedVariables } from './shared'

const step = (id: string, kind: PlaybookStep['kind'], extra: Partial<PlaybookStep> = {}): PlaybookStep =>
  ({ id, kind, tag: 'deterministic', note: '', ask: false, ...extra })

const pb = (steps: PlaybookStep[]): Playbook =>
  ({ id: 'p', name: 'x', description: '', version: 1, variables: [], steps } as unknown as Playbook)

describe('asking for text each run', () => {
  const base = pb([step('s1', 'open', { url: 'https://a.test/' }), step('s2', 'type', { text: 'hunter2', target: { node_name: 'input', ax_name: 'Code' } as never })])

  it('puts a question right before the step and makes a run-time variable', () => {
    const { playbook, original } = askForText(base, 1)
    expect(original).toBe('hunter2')
    expect(playbook.steps.map((s) => s.kind)).toEqual(['open', 'input', 'type'])
    expect(playbook.steps[1].variable).toBe('answer_1')
    expect(playbook.steps[2].text).toBe('{{answer_1}}')
    expect(playbook.variables).toEqual([expect.objectContaining({ name: 'answer_1', runtime: true, type: 'text' })])
    expect(askedVariable(playbook.steps[2])).toBe('answer_1')
    expect(askedVariable(base.steps[1])).toBeNull()
  })

  it('goes before the guard so the guard stays directly in front of the step it guards', () => {
    const guarded = pb([step('s1', 'open'), step('s2', 'approve', { message: 'Send?' }), step('s3', 'type', { text: 'hi' })])
    const { playbook } = askForText(guarded, 2)
    expect(playbook.steps.map((s) => s.kind)).toEqual(['open', 'input', 'approve', 'type'])
    expect(isGuarded(playbook.steps, 3)).toBe(true)
  })

  it('numbers new questions and steps after the ones already there', () => {
    const once = askForText(base, 1).playbook
    const twice = askForText({ ...once, steps: [...once.steps, step('s9', 'type', { text: 'b' })] }, 3).playbook
    expect(twice.variables.map((v) => v.name)).toEqual(['answer_1', 'answer_2'])
    expect(new Set(twice.steps.map((s) => s.id)).size).toBe(twice.steps.length)
  })

  it('can be undone, restoring the text', () => {
    const { playbook, original } = askForText(base, 1)
    const back = stopAskingForText(playbook, 'answer_1', original)
    expect(back.steps).toEqual(base.steps)
    expect(back.variables).toEqual([])
  })

  it('a variable only used by a question or a message is still in use', () => {
    const steps = [step('s1', 'input', { variable: 'code', message: 'Code for {{who}}?' })]
    expect(usedVariables(steps, [{ name: 'code' }, { name: 'who' }, { name: 'other' }]).map((v) => v.name)).toEqual(['code', 'who'])
  })
})
