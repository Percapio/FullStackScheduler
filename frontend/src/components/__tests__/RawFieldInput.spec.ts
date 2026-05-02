import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RawFieldInput from '../RawFieldInput.vue'

describe('RawFieldInput', () => {
  it('does NOT include the highlight ring class by default', () => {
    const w = mount(RawFieldInput, {
      props: { fieldKey: 'raw_qty', modelValue: '0', originalValue: '0' },
    })
    const textareaClasses = w.find('textarea').classes()
    expect(textareaClasses).not.toContain('ring-2')
    expect(textareaClasses).not.toContain('ring-amber-400')
    expect(textareaClasses).not.toContain('highlight-pulse-once')
  })

  it('includes the ring + pulse classes when highlighted=true', () => {
    const w = mount(RawFieldInput, {
      props: {
        fieldKey: 'raw_qty', modelValue: '0', originalValue: '0', highlighted: true,
      },
    })
    const textareaClasses = w.find('textarea').classes()
    expect(textareaClasses).toContain('ring-2')
    expect(textareaClasses).toContain('ring-amber-400')
    expect(textareaClasses).toContain('highlight-pulse-once')
  })

  it('marks the textarea dirty when modelValue differs from originalValue', () => {
    const w = mount(RawFieldInput, {
      props: { fieldKey: 'raw_qty', modelValue: '5', originalValue: '0' },
    })
    expect(w.find('textarea').classes()).toContain('border-amber-400')
  })
})
