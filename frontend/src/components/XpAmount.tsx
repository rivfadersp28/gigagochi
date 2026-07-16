/* eslint-disable @next/next/no-img-element */

type XpAmountProps = {
  text: string;
  ariaLabel: string;
  className?: string;
};

export function XpAmount({ text, ariaLabel, className = "" }: XpAmountProps) {
  return (
    <span className={`xp-amount ${className}`.trim()} aria-label={ariaLabel}>
      <span>{text}</span>
      <img src="/figma/xp-coin.svg?v=2" alt="" aria-hidden="true" />
    </span>
  );
}
