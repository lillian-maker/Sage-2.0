import '../experts/experts.css';

export interface RunEstimateCardProps {
  duration?: string;
  credits?: string;
  output?: string;
  emptyLabel?: string;
}

export function RunEstimateCard({ duration, credits, output, emptyLabel = '待填写' }: RunEstimateCardProps) {
  const rows: Array<[string, string | undefined]> = [
    ['耗时', duration],
    ['消耗', credits],
    ['产出', output],
  ];
  return (
    <aside className="sg-sidecard">
      <div className="sg-sidecard__label">每次运行预计</div>
      {rows.map(([k, v]) => (
        <div key={k} className="sg-sidecard__row">
          <span>{k}</span>
          <span>{v || emptyLabel}</span>
        </div>
      ))}
    </aside>
  );
}
