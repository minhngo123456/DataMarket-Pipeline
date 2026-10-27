export function PageHeading({ eyebrow = 'DATA WORKSPACE', title, description, actions }) {
  return (
    <div className="page-heading">
      <div>
        <span className="page-eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function SampleLabel({ children = 'DỮ LIỆU MẪU' }) {
  return <span className="sample-label">{children}</span>;
}

export function LoadError({ error, onRetry }) {
  return (
    <div className="load-error" role="alert">
      <span>{error}</span>
      {onRetry && <button type="button" className="text-link" onClick={onRetry}>Thử lại</button>}
    </div>
  );
}
