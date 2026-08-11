import type { LearningTranscriptFormat } from '../types';

export const LEARNING_TRANSCRIPT_HARD_MAX_BYTES = 2 * 1024 * 1024;

export type LearningTranscriptFileError =
  | 'empty_file'
  | 'unsupported_file'
  | 'file_too_large'
  | 'invalid_encoding';

export function canonicalizeBilibiliVideoUrl(value: string): string | null {
  try {
    const parsed = new URL(value.trim());
    const host = parsed.hostname.toLowerCase().replace(/\.$/, '');
    if (!['http:', 'https:'].includes(parsed.protocol)) return null;
    if (host !== 'bilibili.com' && !host.endsWith('.bilibili.com')) return null;
    const identifier = parsed.pathname.match(/BV[0-9A-Za-z]{10}/i)?.[0];
    return identifier ? `https://www.bilibili.com/video/BV${identifier.slice(2)}` : null;
  } catch {
    return null;
  }
}

export function validateLearningTranscriptFile(file: Pick<File, 'name' | 'size'>): LearningTranscriptFormat {
  if (file.size === 0) throw new Error('empty_file');
  if (file.size > LEARNING_TRANSCRIPT_HARD_MAX_BYTES) throw new Error('file_too_large');
  const extension = file.name.toLowerCase().split('.').at(-1);
  if (extension !== 'srt' && extension !== 'vtt') throw new Error('unsupported_file');
  return extension;
}

export function readLearningTranscriptFile(file: File): Promise<string> {
  validateLearningTranscriptFile(file);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('invalid_encoding'));
    reader.onload = () => {
      try {
        const bytes = reader.result;
        if (!(bytes instanceof ArrayBuffer)) throw new Error('invalid_encoding');
        const decoded = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        if (!decoded.trim()) throw new Error('empty_file');
        resolve(decoded);
      } catch (error) {
        reject(error instanceof Error ? error : new Error('invalid_encoding'));
      }
    };
    reader.readAsArrayBuffer(file);
  });
}
