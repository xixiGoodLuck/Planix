import { ApiHttpError, ApiNetworkError } from './api';

export function refineTaskErrorText(error: unknown, t: (key: string) => string): string {
  if (error instanceof ApiNetworkError) return t('legacy.refineTaskBackendOffline');
  if (error instanceof ApiHttpError) {
    if (error.status === 404) return t('legacy.refineTaskApiMissing');
    if (error.status === 422) return t('legacy.refineTaskInvalid');
  }
  return t('legacy.refineTaskFailed');
}
