import type { ExplainMode, Language, Level } from '@/api/generated'

// The API's enums in display order.
export const LEVELS: Level[] = ['beginner', 'intermediate', 'advanced']
export const TOPIC_LANGUAGES: Language[] = ['zh', 'en', 'mixed']
export const EXPLAIN_MODES: ExplainMode[] = ['prefetch', 'all', 'manual']
