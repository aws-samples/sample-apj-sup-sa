import { cn } from "@/lib/cn";

function initials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((n) => n[0]?.toUpperCase())
    .join("");
}

function colorFor(name: string): string {
  const palette = [
    "#635bff", "#06b6d4", "#22c55e", "#f59e0b",
    "#ec4899", "#a855f7", "#ef4444", "#14b8a6",
  ];
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + hash * 31;
  return palette[Math.abs(hash) % palette.length];
}

export function Avatar({
  name,
  size = 36,
  color,
  className,
}: {
  name: string;
  size?: number;
  color?: string;
  className?: string;
}) {
  const bg = color ?? colorFor(name);
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white",
        className
      )}
      style={{
        width: size,
        height: size,
        backgroundColor: bg,
        fontSize: size * 0.36,
      }}
    >
      {initials(name)}
    </span>
  );
}
