import { useEffect, useState } from 'react';
import { Library, Save, Trash2, UploadCloud } from 'lucide-react';
import { createRagDocument, deleteRagDocument, fetchRagDocuments, uploadRagDocument } from '../lib/api';
import type { RagDocument } from '../types';

interface NotesPageProps {
  t: (key: string) => string;
}

export function NotesPage({ t }: NotesPageProps) {
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchRagDocuments().then(setDocuments).catch(() => setStatus(t('legacy.materialSaveError')));
  }, [t]);

  async function saveText() {
    if (!content.trim()) return;
    setBusy(true);
    setStatus('');
    try {
      const saved = await createRagDocument({ title: title.trim() || t('legacy.materialTitle'), content: content.trim(), sourceType: 'paste' });
      setDocuments((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setTitle('');
      setContent('');
      setStatus(t('legacy.materialSaved'));
    } catch {
      setStatus(t('legacy.materialSaveError'));
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    setStatus('');
    try {
      const saved = await uploadRagDocument(file, title.trim() || undefined);
      setDocuments((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setFile(null);
      setTitle('');
      setStatus(t('legacy.materialUploaded'));
    } catch {
      setStatus(t('legacy.materialUploadError'));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    try {
      await deleteRagDocument(id);
      setDocuments((current) => current.filter((item) => item.id !== id));
    } catch {
      setStatus(t('legacy.materialSaveError'));
    }
  }

  return (
    <section className="surface ai-panel">
      <div className="section-head">
        <div><h2>{t('legacy.notesTitle')}</h2><p className="section-hint">{t('legacy.notesHint')}</p></div>
      </div>
      <div className="material-library">
        <div className="settings-title"><span><Library size={15} />{t('legacy.materialLibrary')}</span><strong>{documents.length}</strong></div>
        <div className="settings-grid">
          <label><span>{t('legacy.materialTitle')}</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          <label className="settings-wide"><span>{t('legacy.materialContent')}</span><textarea value={content} onChange={(event) => setContent(event.target.value)} /></label>
          <label className="settings-wide"><span>{t('legacy.uploadMaterial')}</span><input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
        </div>
        <div className="settings-actions">
          <button onClick={saveText} disabled={busy || !content.trim()}><Save size={16} />{t('legacy.saveMaterial')}</button>
          <button onClick={upload} disabled={busy || !file}><UploadCloud size={16} />{t('legacy.uploadMaterial')}</button>
          {status && <span>{status}</span>}
        </div>
        <div className="material-list">
          {documents.map((document) => (
            <article key={document.id}>
              <div><strong>{document.title}</strong><small>{document.sourceType} · {document.chunks}</small></div>
              <button type="button" onClick={() => remove(document.id)} aria-label={t('legacy.deleteMaterial')}><Trash2 size={14} /></button>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
