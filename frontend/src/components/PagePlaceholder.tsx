import type { ReactNode } from "react";

interface Props {
  title: string;
  stage: string;
  children?: ReactNode;
}

// Generic "coming in a later stage" page used by Stage 0 stubs.
export default function PagePlaceholder({ title, stage, children }: Readonly<Props>) {
  return (
    <div className="page">
      <h1 className="page__title">{title}</h1>
      <div className="card">
        <p className="muted">Planned for {stage}.</p>
        {children}
      </div>
    </div>
  );
}
