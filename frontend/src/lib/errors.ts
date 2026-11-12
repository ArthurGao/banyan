// One way to report a failure the page cannot show inline (PRODUCT.md principle 5: never masked).
import { toast } from 'sonner'
import { ApiError } from '@/api/http'
import i18n from '@/i18n'

export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export function toastError(e: unknown): void {
  toast.error(i18n.t('app.error', { message: errorMessage(e) }))
}

export const isStatus = (e: unknown, status: number): boolean => e instanceof ApiError && e.status === status
