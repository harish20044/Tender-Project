interface PagePlaceholderProps {
  title: string;
  purpose: string;
  /** What this screen must do before it is considered complete. */
  requirements: string[];
}

/**
 * Stands in for a screen that is specified but not yet built.
 *
 * It carries the screen's requirements rather than the word "TODO", so the
 * scaffold is readable as a specification while the routes are wired up.
 */
export function PagePlaceholder({ title, purpose, requirements }: PagePlaceholderProps) {
  return (
    <section className="mx-auto max-w-3xl">
      <p className="label">Not yet built</p>
      <h1 className="mt-2 text-2xl">{title}</h1>
      <p className="mt-3 text-ink-muted">{purpose}</p>

      <div className="mt-8 border-t border-rule pt-6">
        <p className="label">Requirements</p>
        <ul className="mt-3 flex flex-col gap-2">
          {requirements.map((requirement) => (
            <li key={requirement} className="flex gap-3">
              <span aria-hidden="true" className="mt-2 h-px w-4 shrink-0 bg-rule-strong" />
              <span>{requirement}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
