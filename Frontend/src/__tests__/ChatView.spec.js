import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ChatView from '../pages/ChatView.vue'

describe('ChatView', () => {
  beforeEach(() => {
    const stored = new Map()
    vi.stubGlobal('localStorage', {
      getItem: key => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
      removeItem: key => stored.delete(key),
      clear: () => stored.clear(),
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders streamed markdown and metadata safely', async () => {
    const events = [
      {
        type: 'metadata',
        session_id: 'session-1',
        sources: ['docs/Example.txt', 'docs/Example.txt'],
        search_query: 'Question?',
      },
      { type: 'token', text: '**Answer** <img src=x onerror=alert(1)>' },
      { type: 'done' },
    ]
    const stream = new TextEncoder().encode(`${events.map(event => JSON.stringify(event)).join('\n')}\n`)
    const chunks = [stream.slice(0, 24), stream.slice(24)]
    const fetchMock = vi.fn(async url => {
      if (url.endsWith('/status')) {
        return {
          ok: true,
          json: async () => ({
            status: 'ok',
            document_count: 7,
            documents: ['Example'],
            model: 'gemini-test',
          }),
        }
      }

      return {
        ok: true,
        body: {
          getReader: () => ({
            read: async () => chunks.length
              ? { done: false, value: chunks.shift() }
              : { done: true, value: undefined },
          }),
        },
      }
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(ChatView)
    await flushPromises()
    expect(wrapper.text()).toContain('gemini-test')
    expect(wrapper.text()).toContain('Example')
    await wrapper.find('.chat-input').setValue('Question?')
    await wrapper.find('#send-btn').trigger('click')

    await vi.waitFor(() => {
      expect(wrapper.find('.markdown strong').text()).toBe('Answer')
    })

    expect(wrapper.find('.markdown img').attributes('onerror')).toBeUndefined()
    expect(wrapper.findAll('.source-chip')).toHaveLength(1)
    expect(wrapper.find('.session-id code').text()).toContain('session-1')
    wrapper.unmount()
  })
})
