/**
 * 极简内联 SVG 折线（零依赖）。仓库无图表库（MonitorPage 用 Statistic 替代），
 * 评测趋势用它画 Recall@10/MRR/NDCG@10 随历史变化。null 值跳过（FAILED 运行无 aggregate）。
 */
interface SparklineProps {
  values: (number | null)[];
  width?: number;
  height?: number;
  color?: string;
}

export default function Sparkline({ values, width = 120, height = 32, color = "#1677ff" }: SparklineProps) {
  const pts = values.filter((v): v is number => v != null);
  if (pts.length === 0) return <span style={{ color: "#999" }}>—</span>;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const stepX = pts.length > 1 ? width / (pts.length - 1) : 0;
  const coords = pts
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline points={coords} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
