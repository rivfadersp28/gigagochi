import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import styles from "./ScreenAppBar.module.css";

type ScreenAppBarProps = {
  className?: string;
  onBack: () => void;
  overlay?: boolean;
  title?: ReactNode;
};

export function ScreenAppBar({
  className,
  onBack,
  overlay = false,
  title,
}: ScreenAppBarProps) {
  return (
    <header className={cn(styles.appBar, overlay && styles.overlay, className)}>
      <button type="button" className={styles.backButton} onClick={onBack} aria-label="Назад">
        <ArrowLeft aria-hidden="true" />
      </button>
      {title ? <div className={styles.title}>{title}</div> : null}
    </header>
  );
}
