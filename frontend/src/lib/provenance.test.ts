import { expect, test } from 'vitest'
import { answeredBy } from './provenance'

test('shows the step that wrote the answer and its model, not the whole chain', () => {
  expect(answeredBy('judge.freshness:gateway:claude-sub,judge.sufficiency:gateway:claude-sub,answer.from_kb:gateway:claude-sub')).toBe(
    'answer.from_kb · claude-sub',
  )
  expect(answeredBy('node.explain:ollama:qwen3:8b')).toBe('node.explain · qwen3:8b')
})
