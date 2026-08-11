import { AIWorkspace } from '../components/AIWorkspace';

interface SettingsPageProps {
  t: (key: string) => string;
}

export function SettingsPage({ t }: SettingsPageProps) {
  return <section className="page-stack"><AIWorkspace t={t} /></section>;
}
