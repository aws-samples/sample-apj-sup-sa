import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import styles from "@/components/ui/ui.module.css";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <section className={cn(styles.card, className)} {...props} />;
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn(styles.cardHeader, className)} {...props} />;
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn(styles.cardTitle, className)} {...props} />;
}

export function CardDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn(styles.cardDescription, className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn(styles.cardContent, className)} {...props} />;
}
