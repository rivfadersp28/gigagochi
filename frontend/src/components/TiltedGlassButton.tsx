import type { ButtonHTMLAttributes, CSSProperties } from "react";

import { cn } from "@/lib/utils";

import styles from "./TiltedGlassButton.module.css";

type TiltedGlassButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  animate?: boolean;
  delayMs?: number;
  index?: number;
};

type TiltedGlassButtonStyle = CSSProperties & {
  "--tilted-glass-rotation": string;
  WebkitBackdropFilter: string;
};

export function TiltedGlassButton({
  animate = true,
  className,
  delayMs = 0,
  index = 0,
  style,
  type = "button",
  ...props
}: TiltedGlassButtonProps) {
  const rotation = index % 2 === 0 ? "-2deg" : "2deg";

  return (
    <button
      {...props}
      type={type}
      className={cn(
        styles.button,
        animate && styles.animated,
        className,
      )}
      style={{
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        ...style,
        "--tilted-glass-rotation": rotation,
        animationDelay: animate ? `${delayMs}ms` : undefined,
      } as TiltedGlassButtonStyle}
    />
  );
}
