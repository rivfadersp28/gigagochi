/* eslint-disable @next/next/no-img-element */

export function MainPetBackdrop({ src }: { src: string }) {
  return (
    <div className="main-pet-backdrop" aria-hidden="true">
      <img src={src} alt="" draggable={false} />
    </div>
  );
}
