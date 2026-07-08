export function shouldReduceMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function finishAnimation(element: HTMLElement, finalStyles: Partial<CSSStyleDeclaration>) {
  Object.assign(element.style, finalStyles);
}
